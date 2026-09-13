"""Tema candidato a video, seja de onde vier (RSS, Wikipedia, banco local, serie)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Topic:
    id: str
    theme: str                      # tech_news | story | curiosity | history | prediction
    title: str
    sentences: list[str] = field(default_factory=list)
    subject: str = ""
    entities: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    image_queries: list[str] = field(default_factory=list)
    source_name: str = ""
    source_url: str = ""
    published: str | None = None    # ISO 8601
    year: int | None = None
    extra: dict = field(default_factory=dict)
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Topic":
        return Topic(**{k: v for k, v in d.items() if k in Topic.__dataclass_fields__})
