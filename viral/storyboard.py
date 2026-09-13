"""Modelo do storyboard: o contrato entre o diretor (decide) e o motor (desenha)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Overlay:
    kind: str            # punch | stat | arrow | circle | box | chart | label
    start: float
    end: float
    params: dict = field(default_factory=dict)


@dataclass
class Scene:
    start: float
    end: float
    visual: dict                                  # {"type": "image", "path", "focal", "credit"} | {"type": "procedural"}
    zoom: tuple = (1.0, 1.1)
    pan: tuple = ((0.5, 0.5), (0.5, 0.5))
    transition: str = "cut"
    transition_dur: float = 0.0
    overlays: list[Overlay] = field(default_factory=list)
    beat: int = -1


@dataclass
class CaptionLine:
    start: float
    end: float
    words: list[tuple[str, float, float]]         # (texto, inicio, fim)
    emphasis: tuple[str, ...] = ()


@dataclass
class Storyboard:
    theme: str
    duration: float
    scenes: list[Scene]
    captions: list[CaptionLine]
    punch_zooms: list[tuple[float, float, tuple]] = field(default_factory=list)   # (t, forca, centro)
    flashes: list[float] = field(default_factory=list)
    sfx: list[tuple[str, float, float]] = field(default_factory=list)              # (nome, t, ganho)
    fade_in: float = 0.4
    fade_out: float = 0.7
    badge: str = ""
    seed: int = 0
    shakes: list[tuple[float, float]] = field(default_factory=list)   # (t, forca) tremor de camera
    dips: list[float] = field(default_factory=list)                    # mergulho a preto centrado em t
    mascots: list[Overlay] = field(default_factory=list)               # personagem: start, end, params {expr, pose}

    def to_dict(self) -> dict:
        return asdict(self)
