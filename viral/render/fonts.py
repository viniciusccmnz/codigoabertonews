"""Resolucao de fontes com fallback para as do Windows."""
from __future__ import annotations

import functools
from pathlib import Path

from PIL import ImageFont

from ..config import FONTS

WIN = Path("C:/Windows/Fonts")
CANDIDATES = {
    "heavy": [FONTS / "Montserrat-ExtraBold.ttf", WIN / "impact.ttf", WIN / "arialbd.ttf", WIN / "segoeuib.ttf"],
    "bold": [FONTS / "Montserrat-Bold.ttf", WIN / "arialbd.ttf", WIN / "segoeuib.ttf"],
    "mono": [FONTS / "JetBrainsMono-Bold.ttf", WIN / "consolab.ttf", WIN / "cour.ttf"],
}


@functools.lru_cache(maxsize=128)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(8, int(size))
    for p in CANDIDATES.get(kind, CANDIDATES["heavy"]):
        if Path(p).exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def ease_out_cubic(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def ease_in_out(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p * p * (3 - 2 * p)


def ease_out_back(p: float, k: float = 1.7) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 + (k + 1) * (p - 1) ** 3 + k * (p - 1) ** 2


def ease_in_cubic(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return p ** 3
