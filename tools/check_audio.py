"""Confere a mixagem (viral/audio/mix.py) so com numpy e sinais sinteticos de 85 s, sem ffmpeg nem rede.

Uso:
  python tools/check_audio.py

Criterios:
  1. voz x trilha: trilha 18 a 22 dB abaixo da fala e 10 a 13 dB abaixo nas pausas de 0,6 s ou mais
  2. nenhuma amostra acima de -1 dBFS no render
  3. add_music em menos de 1,5 s
  4. pico de memoria de add_music abaixo de 300 MB
  5. emenda do loop sem clique: o maior salto fica abaixo de 3 vezes a mediana
Os itens marcados como extra conferem o resto do que a mixagem promete. Sai com codigo 1 se algo reprovar.
"""
from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402

from viral.audio import mix  # noqa: E402
from viral.config import MUSIC_DUCK, MUSIC_GAIN, SR  # noqa: E402

DUR = 85.0
N = int(DUR * SR)
# pausas em ciclo: as de 0,6 s ou mais devolvem a trilha, as curtas nao
PAUSES = [0.3, 0.8, 0.25, 1.2, 0.45, 2.0, 0.3, 0.9, 0.2, 1.5]
EDGE_LO, EDGE_HI = 0.5, DUR - 2.1  # fora do fade-in (0,4 s) e do fade-out (2 s)
RESULTS: list[tuple[str, bool]] = []


def db(x: float) -> float:
    return 20.0 * float(np.log10(max(x, 1e-12)))


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def check(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, bool(ok)))
    print(f"[{'OK' if ok else 'FALHA'}] {name}: {detail}")


def mem_mb() -> tuple[float, float]:
    """(memoria privada atual, pico da memoria privada) do processo em MB, pela API do Windows. Conta tambem
    o que o numpy aloca fora do tracemalloc (area de trabalho da FFT). (0, 0) fora do Windows."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return 0.0, 0.0
        return pmc.PagefileUsage / 2 ** 20, pmc.PeakPagefileUsage / 2 ** 20
    except Exception:
        return 0.0, 0.0


def synth_voice(rng: np.random.Generator) -> tuple[np.ndarray, list[tuple[float, float]], list[tuple[float, float]]]:
    """Fala sintetica: vogal com 8 harmonicos (f0 perto de 140 Hz), silabas a 4 Hz sem vale fundo, trechos de
    2,5 a 5 s separados por PAUSES, pico 0,9 como sai de process_voice, e uma rajada quente a cada 3 trechos
    (pico ~3,9, cerca de +4 dBFS depois que o render leva a fala a -16 dBFS) para o limitador ter trabalho.
    A ultima pausa nao entra na lista: depois dela nao volta voz."""
    v = np.zeros(N, np.float32)
    segs, pauses = [], []
    t0, k = 0.15, 0
    while True:
        t1 = t0 + float(rng.uniform(2.5, 5.0))
        if t1 > DUR - 3.0:
            break
        segs.append((t0, t1))
        p = PAUSES[k % len(PAUSES)]
        k += 1
        pauses.append((t1, t1 + p))
        t0 = t1 + p
    pauses = pauses[:-1]
    for a, b in segs:
        i, j = int(a * SR), int(b * SR)
        t = np.arange(j - i, dtype=np.float64) / SR
        ph = 2 * np.pi * np.cumsum(140.0 + 8.0 * np.sin(2 * np.pi * 0.7 * t)) / SR
        x = sum(np.sin(h * ph) / h for h in range(1, 9))
        syl = 0.35 + 0.65 * np.abs(np.sin(np.pi * 4.0 * t + rng.uniform(0, np.pi)))
        edge = np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.01)
        v[i:j] = (x * syl * edge).astype(np.float32)
    v *= np.float32(0.9 / float(np.max(np.abs(v))))
    L = int(0.03 * SR)
    burst = (3.0 * np.sin(2 * np.pi * 900.0 * np.arange(L) / SR) * np.hanning(L)).astype(np.float32)
    for a, b in segs[::3]:
        c = int((a + b) / 2 * SR)
        v[c:c + L] += burst
    return v, segs, pauses


def synth_music(seconds: float, rng: np.random.Generator) -> np.ndarray:
    """Trilha sintetica estavel, gerada em blocos de 10 s: acorde com grave em 82 Hz (parte some no passa-altas),
    um componente em 2,8 kHz (parte some no corte de presenca), tremolo leve e um estalo de pico 1,0 aos 30 s.
    Se o ganho fosse pelo pico, o estalo derrubaria o nivel da trilha inteira."""
    n = int(seconds * SR)
    parts = [(82.4, 0.5), (164.8, 0.35), (246.9, 0.3), (329.6, 0.25), (440.0, 0.2), (2800.0, 0.08)]
    phases = rng.uniform(0, 2 * np.pi, len(parts))
    y = np.zeros(n, np.float32)
    step = 10 * SR
    for s in range(0, n, step):
        t = np.arange(s, min(n, s + step), dtype=np.float64) / SR
        c = sum(a * np.sin(2 * np.pi * f * t + p) for (f, a), p in zip(parts, phases))
        y[s:s + len(t)] = (c * (1.0 + 0.08 * np.sin(2 * np.pi * 5.0 * t))).astype(np.float32)
    y *= np.float32(0.2 / rms(y))
    i = int(30.0 * SR)
    y[i:i + 220] = rng.uniform(-1.0, 1.0, 220).astype(np.float32)
    return y


def seg_rms(x: np.ndarray, a: float, b: float) -> float:
    return rms(x[int(a * SR):int(b * SR)])


def main() -> int:
    import_mb = mem_mb()[0]
    rng = np.random.default_rng(7)
    voice, segs, pauses = synth_voice(rng)
    music = synth_music(DUR + 2.0, rng)
    v_ref = rms(np.concatenate([voice[int(a * SR):int(b * SR)] for a, b in segs]))
    print(f"sinais: voz {DUR:.0f} s com {len(segs)} trechos e {len(pauses)} pausas, fala a {db(v_ref):.1f} dBFS RMS; "
          f"trilha {len(music) / SR:.0f} s a {db(rms(music)):.1f} dBFS RMS, pico {float(np.max(np.abs(music))):.2f}")
    print(f"alvos em config: MUSIC_DUCK={MUSIC_DUCK} dB (fala), MUSIC_GAIN={MUSIC_GAIN} dB (pausa)\n")

    # 3 e 4. tempo e memoria de add_music: primeira chamada do processo (fria), com tracemalloc e memoria do SO
    mx = mix.Mixer(DUR)
    priv0, peak0 = mem_mb()
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    t = time.perf_counter()
    mx.add_music(music, voice)
    cold = time.perf_counter() - t
    traced = (tracemalloc.get_traced_memory()[1] - base) / 2 ** 20
    tracemalloc.stop()
    priv1, peak1 = mem_mb()
    # limite de cima: pico do processo ate aqui menos a memoria no inicio de add_music
    os_delta = max(0.0, peak1 - priv0) if peak1 else 0.0
    # sem tracemalloc, segunda chamada
    mx2 = mix.Mixer(DUR)
    t = time.perf_counter()
    mx2.add_music(music, voice)
    warm = time.perf_counter() - t
    same = bool(np.array_equal(mx.buf, mx2.buf))
    del mx2
    check("3 add_music < 1,5 s", max(cold, warm) < 1.5, f"primeira chamada {cold:.2f} s, segunda {warm:.2f} s")
    # tracemalloc ve o que o numpy registra; a memoria privada do SO ve tambem a area de trabalho da FFT
    mem = max(traced, os_delta)
    check("4 pico de memoria < 300 MB", mem < 300.0,
          f"add_music usa no pico {mem:.0f} MB (tracemalloc {traced:.0f} MB; memoria privada {priv0:.0f} MB no inicio, "
          f"pico {peak1:.0f} MB). Processo inteiro: {import_mb:.0f} MB so com Python e numpy carregados, pico geral "
          f"{peak1:.0f} MB, ou {peak1 - import_mb:.0f} MB acima disso com os sinais de teste")
    check("extra: add_music deterministico", same, "duas chamadas com a mesma entrada dao a mesma trilha" if same else "trilhas diferentes")

    # 1. nivel da trilha contra a fala (mx.buf so tem a trilha)
    m = mx.buf
    sp = []
    for a, b in segs:
        a2, b2 = max(a, EDGE_LO), min(b, EDGE_HI)
        if b2 - a2 >= 0.5:
            sp.append(db(v_ref) - db(seg_rms(m, a2, b2)))
    long_p, short_p = [], []
    for a, b in pauses:
        if b - a >= mix.MIN_PAUSE:
            a2, b2 = max(a + mix.RELEASE + 0.05, EDGE_LO), min(b - mix.ATTACK - 0.05, EDGE_HI)
            if b2 - a2 >= 0.1:
                long_p.append(db(v_ref) - db(seg_rms(m, a2, b2)))
        else:
            a2, b2 = max(a, EDGE_LO), min(b, EDGE_HI)
            if b2 - a2 >= 0.1:
                short_p.append(db(v_ref) - db(seg_rms(m, a2, b2)))
    check("1a trilha 18 a 22 dB abaixo na fala", bool(sp) and min(sp) >= 18.0 and max(sp) <= 22.0,
          f"{len(sp)} trechos, de {min(sp):.2f} a {max(sp):.2f} dB (media {np.mean(sp):.2f})")
    check("1b trilha 10 a 13 dB abaixo nas pausas >= 0,6 s", bool(long_p) and min(long_p) >= 10.0 and max(long_p) <= 13.0,
          f"{len(long_p)} pausas, de {min(long_p):.2f} a {max(long_p):.2f} dB (depois do retorno de 350 ms)")
    check("extra: pausa curta mantem a trilha abaixada", bool(short_p) and min(short_p) >= 18.0 and max(short_p) <= 22.0,
          f"{len(short_p)} pausas < 0,6 s, de {min(short_p):.2f} a {max(short_p):.2f} dB")
    # ataque: a trilha ja esta abaixada quando a voz entra depois de uma pausa longa
    onset = [db(v_ref) - db(seg_rms(m, b, b + 0.05)) for a, b in pauses if b - a >= mix.MIN_PAUSE and b + 0.05 < EDGE_HI]
    check("extra: trilha ja abaixada quando a voz volta", bool(onset) and min(onset) >= 18.0,
          f"primeiros 50 ms de fala depois de pausa longa: de {min(onset):.2f} a {max(onset):.2f} dB")

    # 2. render com voz, trilha e efeitos
    mx.add(voice, 0.0, 1.0)
    hot = [(a + b) / 2 for a, b in segs[::3]]
    for i, at in enumerate(hot[:4]):
        mx.add_sfx("impact" if i % 2 == 0 else "whoosh", at - 0.01, 0.5, seed=i)
    pre = mx.buf * np.float32(10 ** (mix.VOICE_DBFS / 20) / mx.voice_rms)
    out = mx.render()
    ceil = 10 ** (mix.CEIL_DBFS / 20)
    peak = float(np.max(np.abs(out)))
    at_ceil = int(np.count_nonzero(np.abs(out) >= ceil * (1 - 1e-6)))
    pre_peak = float(np.max(np.abs(pre)))
    check("2 nenhuma amostra acima de -1 dBFS", peak <= ceil and pre_peak > 1.0,
          f"pico {db(peak):.2f} dBFS; antes do limitador {db(pre_peak):.2f} dBFS (tem de passar de 0 dBFS para o "
          f"teste valer); amostras no teto (trava de seguranca) {at_ceil}")
    speech_out = db(v_ref * 10 ** (mix.VOICE_DBFS / 20) / mx.voice_rms)
    check("extra: fala perto de -16 dBFS no render", abs(speech_out - mix.VOICE_DBFS) <= 1.0,
          f"fala a {speech_out:.2f} dBFS RMS (referencia medida pelo mixer {db(mx.voice_rms):.2f}, real {db(v_ref):.2f})")
    thr = 10 ** (mix.LIMIT_DBFS / 20)
    changed = np.abs(out - pre) > 1e-6
    nb = N // 441  # blocos de 10 ms
    hot_b = np.abs(pre[:nb * 441]).reshape(nb, 441).max(axis=1) > thr
    near = np.convolve(hot_b.astype(np.float32), np.ones(61, np.float32), mode="same") > 0  # 0,3 s em volta
    far = np.ones(N, bool)
    far[:nb * 441] = np.repeat(~near, 441)
    stray = int(np.count_nonzero(changed & far))
    check("extra: limitador so perto de pico acima de -3 dBFS", stray == 0,
          f"{int(np.count_nonzero(changed))} amostras alteradas ({100 * np.mean(changed):.2f}%), "
          f"{stray} delas a mais de 0,3 s de um pico acima de -3 dBFS")
    del pre, out, changed, hot_b, far, mx, m

    # 5. emenda do loop: tom puro de 30,3 s (fase nao fecha no fim) sob voz muda, ganho constante
    lo, hi = int(0.6 * SR), N - int(2.2 * SR)
    tt = np.arange(int(30.3 * SR), dtype=np.float64) / SR
    tone = (0.5 * np.sin(2 * np.pi * 197.37 * tt)).astype(np.float32)
    del tt
    silent = np.zeros(10, np.float32)
    for bpm in (96, 0):
        bar = 4 * 60.0 / bpm if bpm else 0.0
        ml = mix.Mixer(DUR)
        ml.add_music(tone, silent, bar_seconds=bar)
        d = np.abs(np.diff(ml.buf[lo:hi]))
        ratio = float(d.max() / np.median(d))
        xf = f"crossfade de 2 compassos ({2 * bar:.1f} s)" if bpm else "crossfade de 1,5 s"
        check(f"5 emenda sem clique, bpm {bpm}", ratio < 3.0, f"{xf}: maior salto = {ratio:.2f} x mediana")
        del ml, d
    reps = int(np.ceil(N / len(tone)))
    d = np.abs(np.diff(np.tile(tone, reps)[:N][lo:hi]))
    ratio = float(d.max() / np.median(d))
    check("extra: o teste pega clique (np.tile antigo)", ratio >= 3.0, f"np.tile sem crossfade: maior salto = {ratio:.1f} x mediana")
    del d
    long_track = np.arange(N + 5, dtype=np.float32)
    check("extra: faixa mais longa que o video nao faz loop", bool(np.array_equal(mix.loop_to_length(long_track, N, 2.5), long_track[:N])),
          "devolve so o comeco da faixa")
    del long_track

    # extra: decode_audio pede so o trecho (comando montado sem rodar ffmpeg)
    seen = {}
    real_run = mix.subprocess.run

    class _Fake:
        stdout = np.zeros(4, np.float32).tobytes()

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _Fake()

    mix.subprocess.run = fake_run
    try:
        x = mix.decode_audio("faixa.mp3", start=12.5, dur=87.0)
    finally:
        mix.subprocess.run = real_run
    c = seen.get("cmd", [])
    ok = ("-ss" in c and "-t" in c and c.index("-ss") < c.index("-i") < c.index("-t")
          and c[c.index("-ss") + 1] == "12.500" and c[c.index("-t") + 1] == "87.000" and x.flags.writeable)
    check("extra: decode_audio com -ss antes do -i e -t", ok, " ".join(c[1:9]))

    # extra: cadeia de voz igual para voz baixa e voz quente (10 s)
    rng2 = np.random.default_rng(3)
    n10 = 10 * SR
    t10 = np.arange(n10, dtype=np.float64) / SR
    raw = (sum(np.sin(2 * np.pi * 140 * h * t10) / h for h in range(1, 9)) * (0.35 + 0.65 * np.abs(np.sin(np.pi * 4 * t10)))
           * (np.sin(np.pi * t10 / 2.5) > -0.3)).astype(np.float32)
    raw += rng2.standard_normal(n10).astype(np.float32) * 1e-4
    t = time.perf_counter()
    quiet = mix.process_voice(raw * np.float32(0.08))
    loud = mix.process_voice(raw * np.float32(0.9 / float(np.max(np.abs(raw)))))
    el = time.perf_counter() - t
    diff = float(np.max(np.abs(quiet - loud)))
    check("extra: process_voice independe do nivel de entrada", diff < 1e-3,
          f"voz a {db(rms(raw * 0.08)):.1f} e a {db(rms(raw * 0.9 / float(np.max(np.abs(raw))))):.1f} dBFS: "
          f"maior diferenca {diff:.1e} ({el:.2f} s para as duas)")

    bad = [n for n, ok in RESULTS if not ok]
    print("\nAPROVADO" if not bad else "\nREPROVADO: " + "; ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
