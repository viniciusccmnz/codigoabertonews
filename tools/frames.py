"""Tira alguns quadros de um video ja gerado, a partir do .json dele, sem rede, voz, ffmpeg nem render.

Uso:
  python tools/frames.py output/demo/01-curiosity-o-primeiro-bug-de-computador-registrado.json --at 0.5,37.2,50
  python tools/frames.py <video.json> --n 8                  8 instantes espalhados pelo video
  python tools/frames.py <video.json> --n 8 --no-images      sem imagem nenhuma
  python tools/frames.py <video.json> --placeholders 0x2,4,5x2,7 --out C:/pasta

Monta o storyboard como tools/replay.py (mesmas imagens de mentira), cria o Engine em qualidade poc e chama
Engine.frame_at em no maximo 30 instantes. A boca/brilho do personagem segue um envelope sintetico feito das
palavras do timeline (nao ha audio). Salva um PNG por instante e imprime ms por quadro, o pico do tracemalloc
(so o que o Python e o numpy alocam) e, no Windows, o pico de memoria do processo (inclui o Pillow).
"""
from __future__ import annotations

import argparse
import math
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402

import replay  # noqa: E402
from viral.config import QUALITY  # noqa: E402
from viral.render.engine import Engine  # noqa: E402

MAX_FRAMES = 30


def peak_rss_mb() -> float | None:
    """Pico do working set do processo (Windows). None fora do Windows ou se a chamada falhar."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return None
        return pmc.PeakWorkingSetSize / 2**20
    except Exception:
        return None


def voice_envelope(timeline: list[dict], duration: float, fps: int) -> np.ndarray:
    """Abertura por quadro: sobe durante cada palavra (com um vaivem), zero nas pausas."""
    env = np.zeros(int(duration * fps) + 2, np.float32)
    for seg in timeline:
        for _, a, b in seg.get("words", []):
            f0, f1 = int(a * fps), max(int(b * fps), int(a * fps) + 1)
            for f in range(f0, min(f1, len(env))):
                env[f] = 0.45 + 0.55 * abs(math.sin(f * 0.9))
    return env


def instants(at: str | None, n: int, duration: float) -> list[float]:
    if at:
        ts = [float(x) for x in at.split(",") if x.strip()]
    else:
        n = max(1, n)
        ts = [duration * (k + 0.5) / n for k in range(n)]
    return [min(max(t, 0.0), max(duration - 1e-3, 0.0)) for t in ts][:MAX_FRAMES]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", type=Path)
    ap.add_argument("--at", help="instantes em segundos, separados por virgula (no maximo 30)")
    ap.add_argument("--n", type=int, default=6, help="instantes espalhados pelo video quando nao ha --at (padrao 6, maximo 30)")
    ap.add_argument("--no-images", action="store_true", help="nenhum trecho com imagem")
    ap.add_argument("--placeholders", help="trechos com PNG cinza, como em tools/replay.py")
    ap.add_argument("--no-mascot", action="store_true")
    ap.add_argument("--out", type=Path, help="pasta dos PNGs (padrao: output/frames/<nome do json>)")
    a = ap.parse_args()

    meta, script, timeline = replay.load(a.json)
    q = QUALITY["poc"]
    W, H, fps = q["width"], q["height"], q["fps"]
    out = a.out or ROOT / "output" / "frames" / a.json.stem
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="frames_") as tmp:
        counts = {} if a.no_images else replay.parse_spec(a.placeholders, script, meta)
        visuals = replay.placeholder_visuals(counts, Path(tmp))
        sb, _ = replay.build(script, timeline, visuals, meta.get("seed", script.seed), not a.no_mascot)
        ts = instants(a.at, min(a.n, MAX_FRAMES), sb.duration)
        if not ts:
            print("nenhum instante valido em --at")
            return 2

        tracemalloc.start()
        eng = Engine(sb, W, H, fps)
        eng.voice_env = voice_envelope(timeline, sb.duration, fps)
        print(f"{a.json.name}  poc {W}x{H}  duracao {sb.duration:.1f}s  {len(ts)} quadros -> {out}")
        times = []
        for t in ts:
            f = int(round(t * fps))
            t0 = time.perf_counter()
            frame = eng.frame_at(t, f)
            ms = (time.perf_counter() - t0) * 1000
            times.append(ms)
            name = f"t{t:06.2f}.png"
            frame.save(out / name)
            print(f"  {t:6.2f}s  cena {eng.scene_index(t):>2}  {ms:7.1f} ms  {name}")
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    rest = times[1:] or times
    print(f"\nms por quadro: primeiro {times[0]:.1f}, demais media {sum(rest) / len(rest):.1f}, "
          f"mediana {float(np.median(rest)):.1f}, maior {max(rest):.1f}")
    print(f"pico tracemalloc: {peak / 2**20:.1f} MB")
    rss = peak_rss_mb()
    if rss is not None:
        print(f"pico do processo (working set): {rss:.1f} MB")
    print(f"cache do personagem: {len(eng.mascot._cache)} entradas, {eng.mascot.cache_bytes / 2**20:.1f} MB")
    eng.mascot.clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
