"""Wikipedia e Wikimedia Commons: 'neste dia', resumos e busca de imagem livre."""
from __future__ import annotations

import hashlib
import re
from datetime import date

import requests

from ..config import USER_AGENT
from ..topic import Topic
from ..util.text import clean_html, entities, split_sentences, strip_accents

HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

TECH_KEYWORDS = [
    "computador", "computação", "internet", "software", "programa", "sistema operacional", "apple", "microsoft",
    "google", "ibm", "intel", "satélite", "foguete", "nasa", "espacial", "telefone", "celular", "rádio", "televis",
    "eletr", "tecnolog", "processador", "robô", "algoritmo", "rede", "web", "site", "navegador", "videogame",
    "console", "nintendo", "sony", "playstation", "xbox", "linux", "windows", "unix", "chip", "transistor",
    "digital", "e-mail", "email", "hacker", "vírus", "wikipedia", "facebook", "twitter", "youtube", "amazon",
    "tesla", "spacex", "smartphone", "iphone", "android", "bitcoin", "inteligência artificial", "engenh",
    "invent", "patente", "lançad", "primeiro voo", "avião", "elétric", "energia", "usina", "telégrafo",
    "fotografia", "cinema", "gravação", "disco", "compact disc", "cd", "dvd", "laser", "fibra", "cabo",
]


def _get(url: str, params: dict | None = None, timeout: int = 12) -> dict | None:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except (requests.RequestException, ValueError):
        return None
    return None


def _is_tech(text: str) -> int:
    low = strip_accents(text.lower())
    return sum(1 for k in TECH_KEYWORDS if strip_accents(k) in low)


def on_this_day(lang: str = "pt", day: date | None = None, log=None) -> list[Topic]:
    d = day or date.today()
    data = _get(f"https://{lang}.wikipedia.org/api/rest_v1/feed/onthisday/events/{d.month:02d}/{d.day:02d}")
    if not data:
        if log:
            log("  wikipedia 'neste dia' indisponivel")
        return []
    topics = []
    for ev in data.get("events", []):
        text = clean_html(ev.get("text", ""))
        year = ev.get("year")
        if not text or not year or year < 1800:
            continue
        hits = _is_tech(text)
        pages = ev.get("pages", [])
        if hits == 0:
            continue
        # a pagina mais especifica do evento (mais palavras do titulo dentro do texto) manda no resumo e na imagem
        scored = []
        ev_low = strip_accents(text.lower())
        for p in pages[:4]:
            t = p.get("titles", {}).get("normalized") or p.get("title") or ""
            tt = [strip_accents(w.lower()) for w in t.replace("_", " ").split() if len(w) > 3]
            overlap = sum(1 for w in tt if w in ev_low)
            generic = 1 if t.upper() in {"NASA", "APPLE", "MICROSOFT", "GOOGLE", "IBM", "ESTADOS UNIDOS", "BRASIL"} else 0
            scored.append((overlap + len(tt) * 0.1 - generic * 2, p, t))
        scored.sort(key=lambda x: -x[0])
        imgs, queries, extract = [], [], ""
        for _, p, t in scored:
            if t:
                queries.append(t)
            th = p.get("originalimage") or p.get("thumbnail")
            if th and th.get("source") and not th["source"].lower().endswith(".svg"):
                imgs.append(th["source"])
            if not extract and p.get("extract"):
                extract = clean_html(p["extract"])
        sents = split_sentences(extract)[:6]
        tid = "otd-" + hashlib.sha1(f"{year}-{text}".encode()).hexdigest()[:10]
        ents = entities(text)
        topics.append(Topic(
            id=tid, theme="history", title=text, sentences=sents, subject=queries[0] if queries else (ents[0] if ents else text),
            entities=ents[:5], image_urls=imgs[:3], image_queries=queries[:3] + ents[:2],
            source_name="Wikipedia", source_url=f"https://{lang}.wikipedia.org/wiki/{d.day}_de_{_month_pt(d.month)}",
            year=int(year), extra={"tech_hits": hits, "event": text, "years_ago": d.year - int(year), "day": d.isoformat()},
        ))
    topics.sort(key=lambda t: -t.extra["tech_hits"])
    if log:
        log(f"  wikipedia neste dia: {len(topics)} eventos de tecnologia")
    return topics


def _month_pt(m: int) -> str:
    return ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"][m - 1]


EVENT_WORDS = ("incêndio", "incendio", "fire", "ataque", "attack", "atentado", "desastre", "disaster",
               "acidente", "accident", "explosão", "explosion", "massacre", "assassinato", "enchente", "flood")


def page_image(query: str, lang: str = "pt", width: int = 1400) -> dict | None:
    """Imagem principal da pagina mais relevante para a consulta."""
    data = _get(f"https://{lang}.wikipedia.org/w/api.php", dict(
        action="query", generator="search", gsrsearch=query, gsrlimit=3, prop="pageimages|info", inprop="url",
        piprop="thumbnail|name", pithumbsize=width, format="json"))
    if not data:
        return None
    pages = list((data.get("query") or {}).get("pages", {}).values())
    pages.sort(key=lambda p: p.get("index", 99))
    for p in pages:
        th = p.get("thumbnail")
        name = (p.get("pageimage") or "").lower()
        title = (p.get("title") or "").lower()
        if any(w in title for w in EVENT_WORDS) and not any(w in query.lower() for w in EVENT_WORDS):
            continue
        if th and th.get("source") and not name.endswith(".svg") and th.get("width", 0) >= 500:
            return {"url": th["source"], "credit": f"Wikipedia: {p.get('title', '')}", "page": p.get("fullurl", "")}
    return None


def commons_search(query: str, width: int = 1400, limit: int = 8) -> list[dict]:
    data = _get("https://commons.wikimedia.org/w/api.php", dict(
        action="query", generator="search", gsrsearch=query, gsrnamespace=6, gsrlimit=limit,
        prop="imageinfo", iiprop="url|extmetadata|size|mime", iiurlwidth=width, format="json"))
    if not data:
        return []
    out = []
    for p in (data.get("query") or {}).get("pages", {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        mime = info.get("mime", "")
        if mime not in ("image/jpeg", "image/png"):
            continue
        if info.get("width", 0) < 700 or info.get("height", 0) < 500:
            continue
        meta = info.get("extmetadata", {})
        artist = clean_html(meta.get("Artist", {}).get("value", ""))[:60]
        lic = meta.get("LicenseShortName", {}).get("value", "")
        ym = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", clean_html(meta.get("DateTimeOriginal", {}).get("value", "")))
        out.append({"url": info.get("thumburl") or info.get("url"), "credit": f"{artist} ({lic})".strip(), "page": info.get("descriptionurl", ""),
                    "index": p.get("index", 99), "title": str(p.get("title", "")), "year": int(ym.group(1)) if ym else None})
    out.sort(key=lambda x: x["index"])
    return out
