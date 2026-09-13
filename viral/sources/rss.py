"""Noticias por RSS/Atom, sem chave e sem biblioteca extra (ElementTree).

Feeds em portugues viram roteiro. Feeds em ingles so medem "calor": a mesma
historia aparecendo em varias redacoes do mundo e o melhor sinal de viralidade
que existe sem acesso a metricas das plataformas.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from ..config import CACHE, DATA, USER_AGENT
from ..topic import Topic
from ..util.text import clean_html, entities, short_title, split_sentences, strip_accents

NS = {
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
}
RSS_CACHE = CACHE / "rss"
TTL = 30 * 60
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _get(url: str, timeout: int = 12, ttl: int = TTL, browser: bool = False) -> bytes | None:
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    p = RSS_CACHE / f"{key}.bin"
    if p.exists() and time.time() - p.stat().st_mtime < ttl:
        return p.read_bytes()
    try:
        ua = BROWSER_UA if browser else USER_AGENT
        r = requests.get(url, headers={"User-Agent": ua, "Accept": "*/*"}, timeout=timeout)
        if r.status_code == 200 and r.content:
            p.write_bytes(r.content)
            return r.content
    except requests.RequestException:
        pass
    return p.read_bytes() if p.exists() else None


def _text(el, path: str) -> str:
    if el is None:
        return ""
    node = el.find(path, NS)
    return (node.text or "").strip() if node is not None else ""


def _date(s: str) -> datetime | None:
    if not s:
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _first_img(html: str) -> str:
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html or "", re.I)
    return m.group(1) if m else ""


def parse_feed(xml: bytes, source: dict) -> list[dict]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    items = []
    if root.tag.endswith("rss") or root.find("channel") is not None:
        for it in root.findall("./channel/item"):
            title = _text(it, "title")
            link = _text(it, "link") or _text(it, "guid")
            desc = _text(it, "description")
            body = _text(it, "content:encoded") or desc
            img = ""
            mc = it.find("media:content", NS)
            if mc is not None and mc.get("url") and ("image" in (mc.get("type") or "image") or mc.get("medium") == "image"):
                img = mc.get("url")
            if not img:
                mt = it.find("media:thumbnail", NS)
                img = mt.get("url") if mt is not None else ""
            if not img:
                enc = it.find("enclosure")
                if enc is not None and "image" in (enc.get("type") or ""):
                    img = enc.get("url") or ""
            if not img:
                img = _first_img(body)
            date = _date(_text(it, "pubDate") or _text(it, "dc:date"))
            points = 0
            m = re.search(r"Points:\s*(\d+)", desc or "")
            if m:
                points = int(m.group(1))
            items.append(dict(title=title, link=link, summary=clean_html(desc), body=clean_html(body), image=img, published=date, points=points))
    else:  # Atom (The Register, Reddit)
        for it in root.findall("atom:entry", NS):
            title = _text(it, "atom:title")
            link_el = it.find("atom:link", NS)
            link = link_el.get("href") if link_el is not None else ""
            summ = _text(it, "atom:summary")
            body = _text(it, "atom:content") or summ
            date = _date(_text(it, "atom:published") or _text(it, "atom:updated"))
            items.append(dict(title=title, link=link, summary=clean_html(summ), body=clean_html(body), image=_first_img(body), published=date, points=0))
    for i in items:
        i["source"] = source
    return [i for i in items if i["title"] and len(i["title"].split()) >= 4]


# ------------------------------------------------------------- materia inteira
_BAD_IMG = re.compile(r"logo|avatar|icon|sprite|banner|ads?[/_.-]|pixel|tracking|badge|button|emoji|placeholder|\.svg|\.gif|share|social|author|profile", re.I)


def fetch_article(url: str) -> dict:
    """og:image, imagens do corpo e paragrafos da materia. Cache de 12 h."""
    raw = _get(url, timeout=12, ttl=12 * 3600, browser=True)
    if not raw:
        return {"images": [], "paragraphs": [], "og": ""}
    html = raw.decode("utf-8", "ignore")[:600000]
    og = ""
    for pat in (r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)', r'content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']',
                r'name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)'):
        m = re.search(pat, html, re.I)
        if m:
            og = m.group(1)
            break
    # corpo: prioriza <article>, senao o html todo; fora blocos de navegacao, listas de links e "leia tambem"
    m = re.search(r"<article[^>]*>(.*?)</article>", html, re.S | re.I)
    body = m.group(1) if m else html
    for tag in ("aside", "nav", "footer", "header", "ul", "ol", "figure", "figcaption", "form", "iframe", "script", "style", "noscript"):
        body = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", body, flags=re.S | re.I)
    body = re.sub(r'<(div|section|p|span)[^>]*(class|id)=["\'][^"\']*(related|relacionad|leia|mais-lidas|veja-tambem|veja_tambem|widget|sidebar|newsletter|tags|share|social|comment|promo|banner|ads?)[^"\']*["\'][^>]*>.*?</\1>',
                  " ", body, flags=re.S | re.I)
    imgs = []
    for tag in re.findall(r"<img[^>]+>", body, re.I):
        src = ""
        ms = re.search(r'srcset=["\']([^"\']+)', tag, re.I)
        if ms:
            cands = [c.strip().split(" ") for c in ms.group(1).split(",")]
            cands = [(c[0], int(re.sub(r"\D", "", c[1]) or 0)) for c in cands if c and c[0].startswith("http")]
            if cands:
                src = max(cands, key=lambda c: c[1])[0]
        if not src:
            m2 = re.search(r'(?:data-src|data-lazy-src|src)=["\'](https?://[^"\']+)', tag, re.I)
            src = m2.group(1) if m2 else ""
        if not src or _BAD_IMG.search(src):
            continue
        w = re.search(r'width=["\']?(\d+)', tag)
        if w and int(w.group(1)) < 400:
            continue
        if src not in imgs:
            imgs.append(src)
    paras = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S | re.I):
        t = clean_html(p)
        if len(t) >= 60 and not re.search(r"leia (também|mais)|assine|newsletter|clique aqui|siga o|cookies|publicidade|©", t, re.I):
            paras.append(t)
    return {"images": ([og] if og else []) + imgs[:6], "paragraphs": paras[:14], "og": og}


def _overlap(a: str, b: str) -> float:
    from ..util.text import is_stop, words
    wa = {strip_accents(w.lower()) for w in words(a) if len(w) > 3 and not is_stop(w)}
    wb = {strip_accents(w.lower()) for w in words(b) if len(w) > 3 and not is_stop(w)}
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def enrich(topic: Topic, log=None) -> Topic:
    """Completa um tema de noticia com o texto e as imagens da propria materia."""
    if not topic.source_url or topic.extra.get("enriched"):
        return topic
    art = fetch_article(topic.source_url)
    sents = []
    for p in art["paragraphs"]:
        sents += [s for s in split_sentences(p) if 30 <= len(s) <= 240]
    # resumo do feed primeiro (sempre no assunto); materia completa em seguida
    merged = list(topic.sentences)
    for s in sents:
        if all(_overlap(s, m) < 0.7 for m in merged):
            merged.append(s)
    topic.extra["summary_count"] = len(topic.sentences)
    topic.sentences = merged[:20]
    for u in art["images"]:
        if u not in topic.image_urls:
            topic.image_urls.append(u)
    topic.extra["enriched"] = True
    if log:
        log(f"  materia: {len(topic.sentences)} frases, {len(topic.image_urls)} imagens")
    return topic


# ------------------------------------------------------------------- temas
def _theme_for(item: dict, story_keywords: list[str]) -> str:
    low = strip_accents((item["title"] + " " + item["summary"]).lower())
    hits = sum(1 for k in story_keywords if strip_accents(k) in low)
    return "story" if hits >= 2 else item["source"].get("category", "tech_news")


def fetch_all(max_age_hours: float = 72.0, log=None) -> tuple[list[Topic], list[dict]]:
    """(temas em portugues, itens em ingles para calor)."""
    cfg = json.loads((DATA / "feeds.json").read_text("utf-8"))
    topics: list[Topic] = []
    heat_items: list[dict] = []
    now = datetime.now(timezone.utc)
    for src in cfg["feeds"]:
        xml = _get(src["url"], browser=("reddit" in src["url"]))
        if not xml:
            if log:
                log(f"  feed indisponivel: {src['name']}")
            continue
        items = parse_feed(xml, src)
        n = 0
        for it in items:
            age_h = (now - it["published"]).total_seconds() / 3600 if it["published"] else 999
            if age_h > max_age_hours:
                continue
            n += 1
            if src.get("lang") != "pt":
                heat_items.append({"title": it["title"], "age_hours": age_h, "source": src["name"], "points": it["points"], "weight": src.get("weight", 1.0), "link": it["link"]})
                continue
            text = it["body"] if len(it["body"]) > len(it["summary"]) else it["summary"]
            sents = [s for s in split_sentences(text) if len(s) < 260][:12]
            title = short_title(it["title"], 16)
            tid = "rss-" + hashlib.sha1((it["link"] or it["title"]).encode()).hexdigest()[:10]
            ents = entities(it["title"]) + [e for s in sents[:3] for e in entities(s)]
            seen, uniq = set(), []
            for e in ents:
                if e.lower() not in seen and len(e) > 2:
                    seen.add(e.lower()); uniq.append(e)
            topics.append(Topic(
                id=tid, theme=_theme_for(it, cfg["story_keywords"]), title=title, sentences=sents,
                subject=uniq[0] if uniq else title, entities=uniq[:6],
                image_urls=[it["image"]] if it["image"] else [], image_queries=uniq[:3],
                source_name=src["name"], source_url=it["link"],
                published=it["published"].isoformat() if it["published"] else None,
                extra={"age_hours": round(age_h, 1), "weight": src.get("weight", 1.0), "lang": "pt", "summary": it["summary"][:300]},
            ))
        if log:
            log(f"  {src['name']}: {n} itens recentes")
    return topics, heat_items


def fetch_topics(max_age_hours: float = 72.0, log=None) -> list[Topic]:
    return fetch_all(max_age_hours, log)[0]
