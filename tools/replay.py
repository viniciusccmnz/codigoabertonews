"""Refaz storyboard e QA de um video ja gerado, a partir do .json dele, sem rede, voz nem render.

Uso:
  python tools/replay.py output/demo/01-curiosity-o-primeiro-bug-de-computador-registrado.json
  python tools/replay.py <video.json> --no-images             sem imagem nenhuma (cartoes e fundo gerado)
  python tools/replay.py <video.json> --placeholders 0,4,5,7  PNG cinza temporario nos trechos 0, 4, 5 e 7
  python tools/replay.py <video.json> --placeholders 4x2      ... com 2 imagens no trecho 4
  python tools/replay.py <video.json> --placeholders all      em todo trecho de fato (hook, context, fact, claim)

O .json nao guarda as imagens usadas: sem --placeholders, os trechos com imagem saem de image_credits
(um PNG por credito, na ordem dos trechos de fato com image_query), o que e so uma aproximacao.
Os PNGs vivem numa pasta temporaria. O demo 01-curiosity reproduz nota, avisos e todas as metricas com
  --placeholders 0x2,4,5x2,7
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image  # noqa: E402

from viral import qa  # noqa: E402
from viral.director import Director  # noqa: E402
from viral.script.writer import Beat, Script  # noqa: E402

FACT_KINDS = ("fact", "hook", "context", "claim")


def load(path: Path) -> tuple[dict, Script, list[dict]]:
    meta = json.loads(Path(path).read_text("utf-8"))
    s = dict(meta["script"])
    beats = [Beat(**b) for b in s.pop("beats")]
    s = {k: v for k, v in s.items() if k in Script.__dataclass_fields__}
    return meta, Script(**s, beats=beats), meta["timeline"]


def parse_spec(spec: str | None, script: Script, meta: dict) -> dict[int, int]:
    """Trecho -> quantas imagens de mentira. None: um por credito, nos primeiros trechos de fato com busca de imagem."""
    fact_idx = [i for i, b in enumerate(script.beats) if b.kind in FACT_KINDS]
    if spec is None:
        n = len(meta.get("image_credits") or [])
        with_query = [i for i in fact_idx if script.beats[i].image_query] or fact_idx
        return {i: 1 for i in with_query[:n]}
    if spec == "all":
        return {i: 1 for i in fact_idx}
    out: dict[int, int] = {}
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        bi, _, cnt = part.partition("x")
        out[int(bi)] = int(cnt or 1)
    return out


def placeholder_visuals(counts: dict[int, int], folder: Path, W: int = 540, H: int = 960) -> dict[int, list[dict]]:
    """PNG cinza (um tom por imagem) no formato de viral/visuals/images.py."""
    visuals: dict[int, list[dict]] = {}
    n = 0
    for bi in sorted(counts):
        for k in range(counts[bi]):
            tone = 70 + (n * 37) % 120
            p = folder / f"placeholder_{bi:02d}_{k}.png"
            Image.new("RGB", (W, H), (tone, tone, tone)).save(p)
            visuals.setdefault(bi, []).append({"path": str(p), "focal": (0.5, 0.5), "focal_conf": 0.0, "credit": "placeholder", "query": ""})
            n += 1
    return visuals


def build(script: Script, timeline: list[dict], visuals: dict[int, list[dict]], seed: int, with_mascot: bool = True):
    sb = Director(script, timeline, visuals, seed, with_mascot).build()
    return sb, qa.evaluate(script, timeline, sb, visuals)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", type=Path)
    ap.add_argument("--no-images", action="store_true", help="nenhum trecho com imagem")
    ap.add_argument("--placeholders", help="trechos com PNG cinza: 0,4,5,7 | 4x2 | all (padrao: um por credito do .json)")
    ap.add_argument("--no-mascot", action="store_true")
    a = ap.parse_args()

    meta, script, timeline = load(a.json)
    with tempfile.TemporaryDirectory(prefix="replay_") as tmp:
        counts = {} if a.no_images else parse_spec(a.placeholders, script, meta)
        visuals = placeholder_visuals(counts, Path(tmp))
        sb, report = build(script, timeline, visuals, meta.get("seed", script.seed), not a.no_mascot)

        print(f"{a.json.name}  tema {script.theme}  semente {meta.get('seed')}  duracao {sb.duration:.1f}s")
        print("imagens de mentira: " + (", ".join(f"trecho {bi} x{c}" for bi, c in sorted(counts.items())) or "nenhuma"))
        print("\ncenas (inicio, fim, tipo, imagem, trecho, transicao):")
        for sc in sb.scenes:
            path = sc.visual.get("path")
            img = Path(path).name if path else "-"
            kind = script.beats[sc.beat].kind if 0 <= sc.beat < len(script.beats) else "?"
            print(f"  {sc.start:6.2f} {sc.end:6.2f}  {sc.visual.get('type', '?'):<11} {img:<22} {sc.beat:>2} {kind:<9} {sc.transition}")
        marks = sorted({0.0} | {o.start for sc in sb.scenes for o in sc.overlays if o.kind == "punch"} | {m.start for m in sb.mascots} | set(sb.flashes))
        print("\nquebras de padrao (s): " + " ".join(f"{m:.1f}" for m in marks))
        print("personagem:")
        for m in sb.mascots:
            segs = ", ".join(f"{s:.1f} {e}/{p}" for s, e, p in m.params.get("segments", []))
            print(f"  {m.start:5.1f}-{m.end:5.1f} lado {m.params.get('side', 'left')}: {segs}")
        print(f"\nnota {report.score}  (no .json: {meta.get('qa', {}).get('score')})")
        print("avisos:")
        for w in report.warnings:
            print(f"  - {w}")
        print("metricas:")
        for k, v in report.metrics.items():
            old = meta.get("qa", {}).get("metrics", {}).get(k)
            print(f"  {k}: {v}" + ("" if old is None or str(old) == str(v) else f"   (no .json: {old})"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
