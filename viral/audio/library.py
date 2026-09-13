"""Jazz livre de Kevin MacLeod (incompetech.com, CC BY 4.0): 10 faixas por tipo de video, em rodizio.
Baixa sob demanda so do incompetech, guarda em assets/music_cache (no maximo 30 arquivos) e anota
o que tocou em cache/music_history.json para nao repetir: fica fora quem tocou nas ultimas 7 da categoria."""
from __future__ import annotations

import json
import zlib
from urllib.parse import urlparse

import requests

from ..config import ASSETS, CACHE, DATA

CATALOG = DATA / "music_catalog.json"
HISTORY = CACHE / "music_history.json"
FOLDER = ASSETS / "music_cache"
ALIAS = {"tense": "suspense"}
NEIGHBOR = {"lofi": "synthwave", "synthwave": "lofi", "suspense": "arp", "arp": "suspense", "urgent": "synthwave", "cinematic": "lofi"}
MAX_BYTES = 20 * 2**20
RECENT = 7
MAX_FILES = 30


def _read(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def category(style: str) -> str:
    return ALIAS.get(style, style)


def ranked(style: str, seed: int = 0) -> list[dict]:
    """Ordem de preferencia: fora das ultimas 7 da categoria, tier 1 antes do 2, a menos tocada, desempate estavel.
    A ultima que tocou nunca repete em seguida."""
    key = category(style)
    tracks = next((c["tracks"] for c in _read(CATALOG, {"categories": []})["categories"] if c["mood_key"] == key), [])
    plays = [p["title"] for p in _read(HISTORY, {"plays": []})["plays"] if p.get("category") == key]
    recent = set(plays[-RECENT:])
    last = plays[-1] if plays else None
    return sorted((t for t in tracks if t["title"] != last),
                  key=lambda t: (t["title"] in recent, t.get("tier", 1), plays.count(t["title"]),
                                 zlib.crc32(f"{t['title']}|{seed}".encode())))


def fetch(track: dict):
    """Caminho local do mp3, baixando uma vez. So aceita incompetech.com; o servidor responde
    application/octet-stream, entao o tipo nao e filtrado, so o tamanho."""
    url = track.get("mp3_url") or ""
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not (host == "incompetech.com" or host.endswith(".incompetech.com")):
        return None
    FOLDER.mkdir(parents=True, exist_ok=True)
    path = FOLDER / ("".join(ch if ch.isalnum() else "_" for ch in track["title"]) + ".mp3")
    if path.exists() and path.stat().st_size > 100_000:
        return path
    part = path.with_suffix(".part")
    try:
        with requests.get(url, stream=True, timeout=(10, 60), headers={"User-Agent": "CodigoAbertoNews/1.0"}) as r:
            if r.status_code != 200:
                return None
            got = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    got += len(chunk)
                    if got > MAX_BYTES:
                        raise ValueError("faixa acima do teto")
                    f.write(chunk)
        if got < 100_000:
            raise ValueError("faixa incompleta")
        part.replace(path)
    except Exception:
        part.unlink(missing_ok=True)
        return None
    for old in sorted(FOLDER.glob("*.mp3"), key=lambda q: q.stat().st_mtime)[:-MAX_FILES]:
        old.unlink(missing_ok=True)
    return path


def record(style: str, track: dict) -> None:
    h = _read(HISTORY, {"plays": []})
    h["plays"] = (h.get("plays", []) + [{"category": category(style), "title": track["title"]}])[-300:]
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(h, ensure_ascii=False, indent=1), "utf-8")


def credit(track: dict) -> str:
    return (f"\"{track['title']}\", {track.get('artist') or 'Kevin MacLeod'} (incompetech.com). "
            f"Licenca CC BY 4.0: https://creativecommons.org/licenses/by/4.0/")
