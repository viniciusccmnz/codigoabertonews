"""B-roll: clipes de video e fotos de banco livre, por consulta em ingles.

Fontes:
- Pexels (videos e fotos), chave gratuita em pexels.com/api, PEXELS_API_KEY no .env.
  Limite gratuito: 200 pedidos/hora, 20 mil/mes; 5 videos/dia usam ~60.
- Wikimedia Commons (videos), sem chave, como reserva.
Clipes viram sequencia de quadros JPEG no tamanho do video (cache por clipe e
tamanho), para o motor ler quadro a quadro sem estourar memoria.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import requests

from ..config import CACHE, ROOT, USER_AGENT
from ..sources import wikipedia

CLIPS = CACHE / "clips"
CLIPS.mkdir(parents=True, exist_ok=True)
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
MAX_CLIP_BYTES = 22 * 1024 * 1024
CLIP_SECONDS = 6.0


def pexels_key() -> str | None:
    k = os.environ.get("PEXELS_API_KEY")
    if k:
        return k.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text("utf-8").splitlines():
            if line.strip().startswith("PEXELS_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                return v or None
    return None


def _download(url: str, suffix: str, referer: str = "") -> Path | None:
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    out = CLIPS / f"{key}{suffix}"
    bad = CLIPS / f"{key}.bad"
    if out.exists():
        return out
    if bad.exists():
        return None
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Referer": referer or "https://www.pexels.com/"}, timeout=60, stream=True)
        if r.status_code != 200:
            bad.write_bytes(b"")
            return None
        buf = b""
        for chunk in r.iter_content(1 << 16):
            buf += chunk
            if len(buf) > MAX_CLIP_BYTES:
                bad.write_bytes(b"")
                return None
        out.write_bytes(buf)
        return out
    except requests.RequestException:
        try:
            bad.write_bytes(b"")
        except Exception:
            pass
        return None


# ------------------------------------------------------------------ Pexels
def pexels_videos(query: str, per_page: int = 6, log=None) -> list[dict]:
    key = pexels_key()
    if not key or not query:
        return []
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                         params={"query": query, "per_page": per_page, "orientation": "portrait", "size": "medium"}, timeout=20)
        if r.status_code != 200:
            if log:
                log(f"  pexels videos: HTTP {r.status_code}")
            return []
        out = []
        for v in r.json().get("videos", []):
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and (f.get("height") or 0) >= 720]
            if not files:
                continue
            files.sort(key=lambda f: abs((f.get("height") or 0) - 1280))
            f = files[0]
            # a API de video nao tem "alt": a descricao do video vem no slug da pagina
            slug = re.sub(r"[-/]+", " ", re.sub(r"^https?://[^/]+/video/|\d+/?$", "", v.get("url", "")))
            out.append({"url": f["link"], "alt": (v.get("alt") or slug).strip(), "credit": f"Pexels: {v.get('user', {}).get('name', '')}", "duration": v.get("duration", 0),
                        "w": f.get("width", 0), "h": f.get("height", 0), "id": v.get("id")})
        return out
    except (requests.RequestException, ValueError):
        return []


def pexels_photos(query: str, per_page: int = 5, log=None) -> list[dict]:
    key = pexels_key()
    if not key or not query:
        return []
    try:
        r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
                         params={"query": query, "per_page": per_page, "orientation": "portrait"}, timeout=20)
        if r.status_code != 200:
            return []
        return [{"url": p["src"]["large2x"], "credit": f"Pexels: {p.get('photographer', '')}", "alt": p.get("alt", "")} for p in r.json().get("photos", [])]
    except (requests.RequestException, ValueError):
        return []


CONCRETE = {
    "smartphone", "phone", "iphone", "android", "laptop", "notebook", "computer", "keyboard", "server", "datacenter", "data", "robot",
    "satellite", "rocket", "factory", "assembly", "chip", "processor", "circuit", "screen", "display", "camera", "drone", "car", "tesla",
    "battery", "cable", "fiber", "headphones", "console", "gaming", "code", "coding", "typing", "city", "office", "street", "money", "cash",
    "wallet", "bank", "hospital", "school", "classroom", "network", "antenna", "tower", "microphone", "television", "tablet", "watch",
    "printer", "warehouse", "delivery", "truck", "airport", "airplane", "traffic", "crowd", "protest", "courthouse", "police", "hacker",
    "lock", "password", "fingerprint", "face", "eye", "brain", "hand", "hands", "desk", "store", "shopping", "mall", "supermarket",
    "electricity", "solar", "wind", "nuclear", "power", "plant", "space", "moon", "mars", "earth", "globe", "map", "graph", "chart",
    "foldable", "samsung", "apple", "google", "microsoft", "amazon", "nvidia", "intel",
}


FILLER = {"with", "from", "team", "people", "person", "history", "historical", "legend", "origin", "word",
          "concept", "idea", "moment", "timeline", "daily", "stories", "story", "image", "photo", "video",
          "close", "view", "background", "modern", "vintage", "retro", "real", "first", "that", "this"}


def relevant(query: str, description: str) -> bool:
    """A descricao do video precisa citar o assunto: metade das palavras da consulta (min. 1)
    e pelo menos um substantivo concreto. Descricao vazia nao passa."""
    desc = description.lower()
    if not desc:
        return False
    q = [w for w in re.findall(r"[a-z]{3,}", query.lower()) if w not in FILLER]
    if not q:
        return False
    hit = [w for w in q if re.search(r"\b" + w, desc)]
    return len(hit) >= max(1, (len(q) + 1) // 2) and any(w in CONCRETE for w in hit)


# ------------------------------------------------------------ Commons video
def commons_videos(query: str, limit: int = 6) -> list[dict]:
    data = wikipedia._get("https://commons.wikimedia.org/w/api.php", dict(
        action="query", generator="search", gsrsearch=f"{query} filetype:video", gsrnamespace=6, gsrlimit=limit,
        prop="videoinfo", viprop="url|mime|size|extmetadata|derivatives", format="json"))
    if not data:
        return []
    out = []
    q_tokens = {t.lower() for t in re.findall(r"[a-zA-Z]{4,}", query)} & CONCRETE
    if not q_tokens:
        return []  # sem substantivo concreto, a busca de video no Commons devolve qualquer coisa
    for p in (data.get("query") or {}).get("pages", {}).values():
        info = (p.get("videoinfo") or [{}])[0]
        mime = info.get("mime", "")
        if not (mime.startswith("video/") or mime == "application/ogg"):
            continue
        title_low = str(p.get("title", "")).lower()
        if q_tokens and not any(t in title_low for t in q_tokens):
            continue  # o titulo do arquivo precisa citar o assunto: evita caca de game no lugar de fabrica
        # transcodificacoes menores (480p/720p) evitam baixar o arquivo original gigante
        best = None
        for d in info.get("derivatives", []):
            h = int(d.get("height") or 0)
            if 400 <= h <= 1100 and d.get("src") and "vp9" in (d.get("transcodekey") or d.get("src")):
                if best is None or abs(h - 720) < abs(int(best.get("height") or 0) - 720):
                    best = d
        url = best["src"] if best else (info.get("url") if (info.get("size") or 0) <= MAX_CLIP_BYTES else None)
        if not url:
            continue
        meta = info.get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", ""))[:40]
        lic = meta.get("LicenseShortName", {}).get("value", "")
        out.append({"url": url, "credit": f"Commons: {artist} ({lic})".strip(), "w": info.get("width", 0), "h": info.get("height", 0)})
    return out


# ------------------------------------------------------------------ quadros
def extract_frames(clip: Path, W: int, H: int, seconds: float = CLIP_SECONDS, fps: int = 30) -> dict | None:
    """Clipe -> pasta de JPEGs no tamanho do video (cobrindo e cortando ao centro)."""
    out_dir = CLIPS / f"{clip.stem}_{W}x{H}"
    marker = out_dir / "ok"
    if marker.exists():
        n = len(list(out_dir.glob("f*.jpg")))
        return {"type": "clip", "frames_dir": str(out_dir), "n": n, "fps": fps} if n > 10 else None
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps}"
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-ss", "0.5", "-t", str(seconds), "-i", str(clip), "-vf", vf, "-q:v", "4", str(out_dir / "f%04d.jpg")]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except Exception:
        return None
    n = len(list(out_dir.glob("f*.jpg")))
    if n < 10:
        return None
    marker.write_bytes(b"")
    return {"type": "clip", "frames_dir": str(out_dir), "n": n, "fps": fps}


def find_clips(query: str, W: int, H: int, need: int = 1, log=None) -> list[dict]:
    """Ate `need` clipes prontos (quadros extraidos) para a consulta."""
    out = []
    cands = [c for c in pexels_videos(query, log=log) if relevant(query, c.get("alt", ""))]
    # sem video que descreva a consulta, nada: cartao ou foto e melhor que trem no lugar de equipe
    source = "pexels"
    if not cands:
        cands = commons_videos(query)
        source = "commons"
    for c in cands:
        if len(out) >= need:
            break
        suffix = ".mp4" if source == "pexels" else Path(c["url"]).suffix or ".webm"
        p = _download(c["url"], suffix)
        if not p:
            continue
        fr = extract_frames(p, W, H)
        if fr:
            fr.update({"credit": c.get("credit", ""), "query": query, "focal": (0.5, 0.5), "focal_conf": 0.0, "path": str(p)})
            out.append(fr)
            if log:
                log(f"  clipe: {query} <- {c.get('credit', '')[:40]}")
    return out


def find_broll_clip(query: str, W: int, H: int, log=None, accept=None) -> list[dict]:
    """Um clipe curto do Pexels para b-roll: a descricao do video precisa citar uma palavra da busca."""
    keys = [w for w in re.findall(r"[a-z]{3,}", query.lower()) if w not in FILLER]
    if not keys:
        return []
    cands = pexels_videos(query, per_page=8 if accept else 6, log=log)
    if accept:   # juiz le as descricoes e recusa sentido errado ("relay" de corrida)
        ok = set(accept([c.get("alt", "") for c in cands]))
        cands = [c for k, c in enumerate(cands) if k in ok]
    for c in cands:
        alt = (c.get("alt") or "").lower()
        if not accept and not any(re.search(r"\b" + w[:5], alt) for w in keys):
            continue
        p = _download(c["url"], ".mp4")
        if not p:
            continue
        fr = extract_frames(p, W, H, seconds=4.0)
        if fr:
            fr.update({"credit": c.get("credit", ""), "query": query, "focal": (0.5, 0.5), "focal_conf": 0.0, "path": str(p)})
            if log:
                log(f"  clipe: {query} <- {c.get('credit', '')[:40]}")
            return [fr]
    return []


def find_photos(query: str, need: int = 1, log=None, accept=None) -> list[dict]:
    """Fotos do Pexels (se houver chave) ja baixadas e validadas."""
    from . import images
    out = []
    cands = pexels_photos(query, per_page=8 if accept else 5, log=log)
    if accept:
        ok = set(accept([c.get("alt", "") for c in cands]))
        cands = [c for k, c in enumerate(cands) if k in ok]
    for c in cands:
        if not accept and c.get("alt") and not any(w in c["alt"].lower() for w in re.findall(r"[a-z]{3,}", query.lower()) if w not in FILLER):
            continue   # foto que nao cita o assunto na descricao
        if len(out) >= need:
            break
        p = images.download(c["url"], referer="https://www.pexels.com/")
        if p:
            out.append(images.make_visual(p, c["credit"], query))
            if log:
                log(f"  foto: {query} <- {c['credit'][:40]}")
    return out
