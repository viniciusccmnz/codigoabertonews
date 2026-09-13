"""Mixagem final: voz, trilha com ducking e efeitos posicionados no tempo."""
from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from ..config import MUSIC_DUCK, MUSIC_GAIN, SR
from . import synth

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

# Niveis da saida, em dBFS. A voz e a referencia: a fala fica perto de -16 dBFS RMS, o limitador
# so age nos picos acima de -3 dBFS e nenhuma amostra passa de -1 dBFS.
VOICE_DBFS = -16.0
LIMIT_DBFS = -3.0
CEIL_DBFS = -1.0
# Nivel de fala em que a cadeia de voz (compressao e saturacao leve) trabalha. A narracao da OpenAI
# chega por volta de -22 dBFS RMS; edge-tts e narracao gravada (normalizada a pico 0,9) chegam bem
# mais quentes e passavam a saturar. Normalizar a entrada deixa o timbre igual para qualquer origem.
VOICE_IN_DBFS = -22.0
# Ducking da trilha: analise em blocos de 10 ms, a trilha termina de abaixar quando a voz entra
# (comeca 60 ms antes, a voz inteira ja existe), volta em 350 ms, e pausa mais curta que 0,6 s
# nao devolve a trilha.
# Estereo: a voz fica mono no centro; a trilha abre para os lados. O meio (M) da trilha perde 5 dB na
# faixa em que a voz e inteligivel (1,2 a 5 kHz) e 10% no geral; os lados (S) sobem 40%. A musica soa
# mais cheia e mais alta sem cobrir a fala, e a soma em mono continua sem cancelamento.
MID_GAIN = 0.9
MID_DIP_DB = -5.0
SIDE_GAIN = 1.4
BLOCK = 0.01
ATTACK = 0.06
RELEASE = 0.35
MIN_PAUSE = 0.6


def decode_audio(path: str | Path, start: float | None = None, dur: float | None = None, channels: int = 1) -> np.ndarray:
    """Qualquer formato -> float32 em SR, via ffmpeg: mono (n,) ou estereo (n, 2) com `channels=2`. `start` e `dur` (segundos) decodificam so o
    trecho usado: `-ss` antes do `-i` pula direto para o ponto, sem decodificar o comeco da faixa."""
    cmd = [FFMPEG, "-v", "error"]
    if start:
        cmd += ["-ss", f"{max(0.0, float(start)):.3f}"]
    cmd += ["-i", str(path)]
    if dur is not None:
        cmd += ["-t", f"{max(0.0, float(dur)):.3f}"]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(channels), "-ar", str(SR), "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).copy()
    return x[: len(x) // 2 * 2].reshape(-1, 2) if channels == 2 else x


def write_wav(path: str | Path, x: np.ndarray, sr: int = SR) -> None:
    x = np.clip(x, -1.0, 1.0)
    pcm = (x * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1] if x.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def fade(x: np.ndarray, fade_in: float = 0.0, fade_out: float = 0.0) -> np.ndarray:
    y = x.copy()
    a, b = int(fade_in * SR), int(fade_out * SR)
    if a > 0:
        a = min(a, len(y))
        y[:a] *= np.linspace(0, 1, a, dtype=np.float32)
    if b > 0:
        b = min(b, len(y))
        y[-b:] *= np.linspace(1, 0, b, dtype=np.float32)
    return y


def envelope(x: np.ndarray, window: float = 0.05, smooth: float = 0.12) -> np.ndarray:
    """Envelope de amplitude (0..1): pico por bloco de `window` s, media movel de `smooth` s na taxa de
    bloco (cumsum, O(n)) e interpolacao linear de volta para amostras. Antes era np.convolve na taxa
    cheia, O(n*k) com nucleo de dezenas de milhares de amostras."""
    n = len(x)
    w = max(1, int(window * SR))
    m = n // w
    if m == 0:
        return np.zeros(n, np.float32)
    blocks = np.abs(x[: m * w]).reshape(m, w).max(axis=1).astype(np.float64)
    k = max(1, int(round(smooth / window)))
    if k > 1:
        c = np.concatenate(([0.0], np.cumsum(blocks)))
        lo = np.clip(np.arange(m) - k // 2, 0, m)
        hi = np.clip(np.arange(m) - k // 2 + k, 0, m)
        blocks = (c[hi] - c[lo]) / np.maximum(hi - lo, 1)
    peak = float(blocks.max())
    if peak > 1e-6:
        blocks = blocks / peak
    return _block_to_samples(blocks, w, n)


def _block_to_samples(v: np.ndarray, blk: int, n: int, chunk: int = 1 << 20) -> np.ndarray:
    """Valores por bloco -> uma curva por amostra, interpolando entre os centros dos blocos (em fatias,
    para nao alocar um vetor float64 do tamanho do video inteiro)."""
    centers = (np.arange(len(v), dtype=np.float64) + 0.5) * blk
    out = np.empty(n, np.float32)
    for s in range(0, n, chunk):
        e = min(n, s + chunk)
        out[s:e] = np.interp(np.arange(s, e, dtype=np.float64), centers, v)
    return out


def _block_rms(x: np.ndarray, blk: int) -> np.ndarray:
    """RMS por bloco de `blk` amostras; o ultimo bloco incompleto conta com o tamanho que tem."""
    n = len(x)
    full = n // blk
    out = np.zeros(-(-n // blk), np.float64)
    if full:
        out[:full] = np.sqrt(np.mean(np.square(x[: full * blk].reshape(full, blk)), axis=1, dtype=np.float64))
    if len(out) > full:
        out[full] = np.sqrt(np.mean(np.square(x[full * blk:], dtype=np.float64)))
    return out


def _voice_activity(x: np.ndarray, blk: int) -> tuple[np.ndarray, float]:
    """Blocos com fala e o RMS da fala. Fala e bloco a menos de 30 dB do nivel alto (p90 dos blocos nao
    silenciosos); o RMS so conta esses blocos, entao pausa e cauda de reverb nao puxam a referencia."""
    r = _block_rms(np.asarray(x, dtype=np.float32), blk)
    live = r[r > 1e-4]
    if live.size == 0:
        return np.zeros(len(r), bool), 0.0
    active = r > float(np.percentile(live, 90)) * 10 ** (-30.0 / 20)
    return active, float(np.sqrt(np.mean(r[active] ** 2)))


def speech_rms(x: np.ndarray) -> float:
    """RMS da fala (linear), sem contar silencio: a referencia de nivel da mixagem."""
    return _voice_activity(x, max(1, int(BLOCK * SR)))[1]


def _gated_rms(x: np.ndarray, win: float = 0.4, rel_db: float = -20.0) -> float:
    """Volume medio da trilha: RMS em blocos de 0,4 s, ignorando bloco silencioso (abaixo de -70 dBFS) ou
    20 dB abaixo da media. Um pico isolado (prato, batida) nao muda o resultado."""
    r = _block_rms(x, max(1, int(win * SR)))
    p = r[r > 10 ** (-70.0 / 20)] ** 2
    if p.size == 0:
        return 0.0
    p = p[p >= p.mean() * 10 ** (rel_db / 10)]
    return float(np.sqrt(p.mean()))


def _fill_gaps(active: np.ndarray, max_len: int) -> np.ndarray:
    """Trecho sem fala mais curto que `max_len` blocos vira fala (a trilha continua abaixada)."""
    a = np.concatenate(([1], active.astype(np.int8), [1]))
    d = np.diff(a)
    out = active.copy()
    for s, e in zip(np.flatnonzero(d == -1), np.flatnonzero(d == 1)):
        if e - s < max_len and not (s == 0 and e == len(active)):
            out[s:e] = True
    return out


def _slew_db(tgt: np.ndarray, down: float, up: float) -> np.ndarray:
    """Curva em dB que segue `tgt` descendo no maximo `down` dB e subindo no maximo `up` dB por bloco. A
    descida e antecipada (termina onde o alvo cai), a subida comeca onde o alvo sobe. Duas passadas de
    minimo acumulado, O(n) e sem laco em Python."""
    i = np.arange(len(tgt), dtype=np.float64)
    g = np.minimum.accumulate((tgt + down * i)[::-1])[::-1] - down * i
    return np.minimum.accumulate(g - up * i) + up * i


def _fast_len(n: int) -> int:
    """Menor tamanho >= n so com fatores 2, 3 e 5: a FFT fica rapida para qualquer duracao."""
    best = 1 << max(0, (n - 1).bit_length())
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            p = p35 << max(0, (-(-n // p35) - 1).bit_length())
            best = min(best, p)
            p35 *= 3
        p5 *= 5
    return best


def music_eq(x: np.ndarray, hp: float = 120.0, dip_db: float = -3.0, lo: float = 2000.0, hi: float = 4000.0) -> np.ndarray:
    """Equalizacao da trilha num passo so de FFT (float32): passa-altas de 2a ordem em `hp` Hz e corte
    leve e largo centrado entre `lo` e `hi` Hz (metade do corte nas bordas), onde a voz e inteligivel."""
    x = np.asarray(x, dtype=np.float32)
    n = len(x)
    if n < 64:
        return x.copy()
    size = _fast_len(n)
    X = np.fft.rfft(x, size)
    f = np.fft.rfftfreq(size, 1.0 / SR).astype(np.float32)
    mask = 1.0 / np.sqrt(1.0 + (np.float32(hp) / np.maximum(f, np.float32(1e-3))) ** 4)
    octv = np.log2(np.maximum(f, np.float32(1.0)) / np.float32(np.sqrt(lo * hi)))
    sigma2 = 0.25 * np.log2(hi / lo) ** 2 / (2.0 * np.log(2.0))
    mask *= np.float32(10.0) ** (np.float32(dip_db / 20.0) * np.exp(-octv ** 2 / np.float32(2.0 * sigma2)))
    del f, octv
    X *= mask
    del mask
    return np.fft.irfft(X, size)[:n].astype(np.float32)


def loop_to_length(x: np.ndarray, n: int, bar_seconds: float = 0.0) -> np.ndarray:
    """Trilha no tamanho do video. Se for mais curta, repete com crossfade de potencia constante de 2
    compassos (1,5 s quando o bpm e 0), emendando num multiplo de compasso a partir do inicio da faixa.
    O np.tile de antes emendava sem crossfade e dava clique."""
    x = np.asarray(x, dtype=np.float32)
    L = len(x)
    if L == 0:
        return np.zeros(n, np.float32)
    if L >= n:
        return x[:n].copy()
    if L < 2:  # periodo seria 0 e o laco abaixo nao terminaria
        return np.resize(x, n)
    xf = int(round((2.0 * bar_seconds if bar_seconds > 0 else 1.5) * SR))
    xf = max(1, min(xf, L // 2))
    period = L - xf
    if bar_seconds > 0:
        bar = bar_seconds * SR
        whole = int(round(int((L - xf) // bar) * bar))
        if xf <= whole <= L - xf:
            period = whole
    ramp = np.linspace(0.0, np.pi / 2, xf, dtype=np.float32)
    unit = x[: period + xf].copy()
    unit[period:] *= np.cos(ramp)
    later = unit.copy()
    later[:xf] *= np.sin(ramp)
    out = np.zeros(n, np.float32)
    pos = 0
    while pos < n:
        u = unit if pos == 0 else later
        k = min(len(u), n - pos)
        out[pos:pos + k] += u[:k]
        pos += period
    return out


def limiter(x: np.ndarray, thresh_db: float = LIMIT_DBFS, ceil_db: float = CEIL_DBFS,
            attack_db_s: float = 2000.0, release_db_s: float = 40.0) -> np.ndarray:
    """Limitador por ganho, sem tanh. So age em bloco (32 amostras) com pico acima de `thresh_db`; o
    pico sai por um joelho suave que tende a `ceil_db` sem chegar nele. O ganho desce antes do pico
    (look-ahead) a `attack_db_s` e volta a `release_db_s`. Cada bloco recebe no maximo o ganho que ele
    e os vizinhos pedem, entao a interpolacao entre blocos nunca deixa amostra passar do teto."""
    y = np.asarray(x, dtype=np.float32)
    n = len(y)
    thr, ceil = 10 ** (thresh_db / 20), 10 ** (ceil_db / 20)
    if n == 0:
        return y.copy()
    blk = 32
    m = -(-n // blk)
    a = np.zeros(m * blk, np.float32)
    np.abs(y, out=a[:n])
    pk = a.reshape(m, blk).max(axis=1).astype(np.float64)
    del a
    if float(pk.max()) <= thr:
        return y.copy()
    knee = ceil - thr
    over = pk > thr
    po = pk[over]
    req = np.zeros(m, np.float64)
    req[over] = 20 * np.log10((thr + knee * (1 - np.exp(-(po - thr) / knee))) / po)
    req = np.minimum(req, np.minimum(np.concatenate((req[1:], [0.0])), np.concatenate(([0.0], req[:-1]))))
    g = np.minimum(_slew_db(req, attack_db_s * blk / SR, release_db_s * blk / SR), 0.0)
    out = y * _block_to_samples(10 ** (g / 20), blk, n)
    np.clip(out, -ceil, ceil, out=out)
    return out


def bleep(x: np.ndarray, spans: list[tuple[float, float]], freq: float = 1000.0) -> np.ndarray:
    """Censura de TV: a voz some e entra um tom de 1 kHz no nivel da fala, com rampa de 5 ms nas pontas."""
    if not spans:
        return x
    y = x.copy()
    level = min(0.9, 0.7 * speech_rms(x) * np.sqrt(2.0))
    ramp = max(1, int(0.005 * SR))
    for t0, t1 in spans:
        i0, i1 = max(0, int(t0 * SR)), min(len(y), int(t1 * SR))
        if i1 - i0 < 2 * ramp:
            continue
        env = np.ones(i1 - i0, np.float32)
        env[:ramp] = np.linspace(0, 1, ramp, dtype=np.float32)
        env[-ramp:] = np.linspace(1, 0, ramp, dtype=np.float32)
        tone = np.sin(2 * np.pi * freq * np.arange(i1 - i0) / SR).astype(np.float32) * np.float32(level)
        y[i0:i1] = y[i0:i1] * (1 - env) + tone * env
    return y


def limiter_stereo(x: np.ndarray, **kw) -> np.ndarray:
    """Limitador ligado nos dois canais: o ganho sai do maior pico entre L e R e vale igual para os dois,
    entao a imagem estereo (voz no centro) nao anda quando o limitador age."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        return limiter(x, **kw)
    pk = np.maximum(np.abs(x[:, 0]), np.abs(x[:, 1]))
    lim = limiter(pk, **kw)
    g = np.ones_like(pk)
    nz = pk > 1e-9
    g[nz] = lim[nz] / pk[nz]
    ceil = 10 ** (kw.get("ceil_db", CEIL_DBFS) / 20)
    return np.clip(x * g[:, None], -ceil, ceil)


def process_voice(x: np.ndarray) -> np.ndarray:
    """Cadeia de voz de estudio: corte de graves, presenca, compressao, leve
    saturacao e um pouco de sala. Tira o timbre 'seco' de TTS e deixa a voz
    sentada na mixagem. A entrada e levada a VOICE_IN_DBFS antes da cadeia, para a compressao e a
    saturacao fazerem o mesmo com qualquer origem de voz."""
    x = x.astype(np.float32)
    ref = speech_rms(x)
    if ref > 1e-6:
        x = x * np.float32(10 ** (VOICE_IN_DBFS / 20) / ref)
    y = synth.highpass(x, 85.0)
    # presenca: reforca 2-5 kHz por FFT
    n = len(y)
    if n > 64:
        Y = np.fft.rfft(y)
        f = np.fft.rfftfreq(n, 1.0 / SR)
        boost = 1.0 + 0.35 * np.exp(-((np.log(np.maximum(f, 1.0)) - np.log(3200.0)) ** 2) / (2 * 0.45 ** 2))
        y = np.fft.irfft(Y * boost, n).astype(np.float32)
    y = synth.glue(y, thresh=0.22, ratio=3.0, block=0.02, smooth=0.06)
    # saturacao leve: com a fala em -22 dBFS so os picos (cerca de 0,2% das amostras passam de 0,3)
    # saem do trecho quase linear. Sem a normalizacao acima, voz mais quente saturava de verdade.
    y = np.tanh(y * 1.25) / np.tanh(1.25)
    y = synth.reverb(y, decay=0.35, mix=0.07, tone=5000.0, seed=11)
    peak = float(np.max(np.abs(y))) if y.size else 1.0
    return (y / peak * 0.9).astype(np.float32) if peak > 1e-6 else y


class Mixer:
    def __init__(self, seconds: float):
        self.n = int(seconds * SR)
        self.buf = np.zeros(self.n, np.float32)
        self.seconds = seconds
        self.voice_rms = 0.0  # RMS da fala medido em add_music; referencia de nivel do render
        self.music: np.ndarray | None = None   # trilha em estereo (n, 2); voz e efeitos ficam em buf (centro)

    def add(self, x: np.ndarray, at: float, gain: float = 1.0) -> None:
        i = int(max(0.0, at) * SR)
        if i >= self.n:
            return
        L = min(len(x), self.n - i)
        if L > 0:
            self.buf[i:i + L] += x[:L] * gain

    def add_music(self, music: np.ndarray, voice: np.ndarray, base_gain: float | None = None, duck_depth: float | None = None,
                  *, bar_seconds: float = 0.0, pause_db: float | None = None, speech_db: float | None = None) -> None:
        """Trilha sob a voz (somada com ganho 1,0), com nivel medido e nao chutado.

        - Ganho pelo volume medio da trilha (RMS com portao), nunca pelo pico: `speech_db` abaixo da fala
          enquanto a voz fala (config.MUSIC_DUCK) e `pause_db` abaixo em pausa de 0,6 s ou mais
          (config.MUSIC_GAIN). Pausa mais curta mantem a trilha abaixada.
        - Abaixa em 60 ms, terminando quando a voz entra, e volta em 350 ms: rampas lineares em dB em
          blocos de 10 ms, O(n), sem convolucao.
        - Passa-altas de 120 Hz e corte leve de 2 a 4 kHz na trilha.
        - Mais curta que o video: repete com crossfade de 2 compassos (`bar_seconds`; 1,5 s sem bpm).
        - Fade-in de 0,4 s e fade-out de 2 s.
        `base_gain` e `duck_depth` sao do ganho linear antigo e nao tem mais efeito (a chamada em
        pipeline.py troca no WP8)."""
        n = self.n
        if n == 0 or len(music) == 0:
            return
        blk = max(1, int(BLOCK * SR))
        pause_db = MUSIC_GAIN if pause_db is None else float(pause_db)
        speech_db = MUSIC_DUCK if speech_db is None else float(speech_db)
        music = np.asarray(music, dtype=np.float32)
        if music.ndim == 2 and music.shape[1] >= 2:
            mid, side = (music[:, 0] + music[:, 1]) * 0.5, (music[:, 0] - music[:, 1]) * 0.5
        else:
            mid, side = music.reshape(-1), None
        mid = music_eq(loop_to_length(mid, n, bar_seconds), dip_db=MID_DIP_DB, lo=1200.0, hi=5000.0) * np.float32(MID_GAIN)
        side = music_eq(loop_to_length(side, n, bar_seconds), dip_db=0.0) * np.float32(SIDE_GAIN) if side is not None else None
        # volume medio da trilha em estereo: (L^2 + R^2) / 2 = M^2 + S^2
        m_rms = _gated_rms(np.sqrt(mid * mid + side * side) if side is not None else mid)
        if m_rms <= 1e-7:
            return
        m = mid
        active, v_rms = _voice_activity(voice[:n], blk)
        nb = -(-n // blk)
        act = np.zeros(nb, bool)
        act[: min(nb, len(active))] = active[:nb]
        if v_rms > 1e-6:
            self.voice_rms = v_rms
            ref = v_rms
        else:
            ref = 10 ** (VOICE_DBFS / 20)
        depth = min(0.0, speech_db - pause_db)
        tgt = np.where(_fill_gaps(act, int(round(MIN_PAUSE / BLOCK))), depth, 0.0)
        if depth < 0:
            tgt = _slew_db(tgt, -depth / max(1.0, ATTACK / BLOCK), -depth / max(1.0, RELEASE / BLOCK))
        gain = _block_to_samples((ref * 10 ** (pause_db / 20) / m_rms) * 10 ** (tgt / 20), blk, n)
        a, b = min(n, int(0.4 * SR)), min(n, int(2.0 * SR))
        if a > 0:
            gain[:a] *= np.linspace(0, 1, a, dtype=np.float32)
        if b > 0:
            gain[-b:] *= np.linspace(1, 0, b, dtype=np.float32)
        m = m * gain
        sd = side * gain if side is not None else np.zeros_like(m)
        self.music = np.stack([m + sd, m - sd], axis=1).astype(np.float32)

    def add_sfx(self, name: str, at: float, gain: float = 1.0, seed: int = 0) -> None:
        self.add(synth.sfx(name, seed), at, gain)

    def render(self) -> np.ndarray:
        """Fala em VOICE_DBFS (RMS) e limitador so nos picos acima de LIMIT_DBFS, com teto em CEIL_DBFS.
        Sem o tanh no mix inteiro, que saturava tudo o tempo todo. Sem add_music, a referencia e o RMS
        de fala medido no proprio mix."""
        ref = self.voice_rms or speech_rms(self.buf)
        k = np.float32(10 ** (VOICE_DBFS / 20) / ref) if ref > 1e-6 else np.float32(1.0)
        if self.music is None:
            return limiter(self.buf * k)
        # voz e efeitos iguais nos dois lados (centro); trilha com a abertura estereo dela
        out = self.music * k
        out += (self.buf * k)[:, None]
        return limiter_stereo(out)
