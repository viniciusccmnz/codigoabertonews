"""Imagens do que esta sendo dito.

Ordem: fotos da propria materia (og:image e corpo) > Wikipedia/Commons apenas
para nome proprio especifico, com conferencia de que a pagina encontrada bate
com a busca > nada (o diretor usa fundo do tema com tipografia grande, que e
melhor do que foto fora de contexto).

Ponto focal por energia de bordas, com grau de confianca: seta e circulo so
aparecem quando a energia esta concentrada num alvo claro.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from ..config import CACHE, USER_AGENT
from ..sources import wikipedia
from ..util.text import BIG_ENTITIES, is_stop, strip_accents, words

IMG_DIR = CACHE / "images"
MAX_BYTES = 14 * 1024 * 1024
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def download(url: str, timeout: int = 20, referer: str = "") -> Path | None:
    if not url or not url.startswith("http"):
        return None
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    out = IMG_DIR / f"{key}.jpg"
    bad = IMG_DIR / f"{key}.bad"
    if out.exists():
        return out
    if bad.exists():
        return None
    try:
        ua = USER_AGENT if "wikimedia.org" in url or "wikipedia.org" in url else BROWSER_UA
        r = requests.get(url, headers={"User-Agent": ua, "Referer": referer or "https://www.google.com/"}, timeout=timeout, stream=True)
        if r.status_code == 429 or r.status_code >= 500:
            return None          # bloqueio ou queda temporaria: tenta de novo na proxima vez
        if r.status_code != 200:
            bad.write_bytes(b"")
            return None
        buf = b""
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > MAX_BYTES:
                bad.write_bytes(b"")
                return None
        tmp = IMG_DIR / f"{key}.tmp"
        tmp.write_bytes(buf)
        img = Image.open(tmp)
        img.load()
        w, h = img.size
        if w < 500 or h < 300 or w / h > 3.2 or h / w > 2.6:
            tmp.unlink(missing_ok=True)
            bad.write_bytes(b"")
            return None
        img = img.convert("RGB")
        g = np.asarray(img.convert("L").resize((64, 64)), dtype=np.float32)
        gy, gx = np.gradient(g)
        detail = float(np.mean(np.sqrt(gx * gx + gy * gy)))
        if g.mean() < 38 or g.std() < 16 or g.mean() > 235 or detail < 4.0:
            tmp.unlink(missing_ok=True)  # escura, chapada, fundo branco ou sem detalhe (tela vazia): fica feio no video
            bad.write_bytes(b"")
            return None
        if max(img.size) > 2000:
            s = 2000 / max(img.size)
            img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
        img.save(out, "JPEG", quality=88)
        tmp.unlink(missing_ok=True)
        return out
    except requests.RequestException:
        return None              # rede: nao condena a URL
    except Exception:
        try:
            bad.write_bytes(b"")
        except Exception:
            pass
        return None


def focal_point(path: Path) -> tuple[float, float, float]:
    """(fx, fy, confianca 0..1). Confianca alta = energia concentrada num alvo."""
    try:
        img = Image.open(path).convert("L")
    except Exception:
        return (0.5, 0.45, 0.0)
    small = img.resize((96, max(1, int(96 * img.height / img.width))), Image.BILINEAR)
    a = np.asarray(small, dtype=np.float32)
    gy, gx = np.gradient(a)
    energy = np.sqrt(gx * gx + gy * gy)
    h, w = energy.shape
    yy, xx = np.mgrid[0:h, 0:w]
    center_w = 1.0 - 0.5 * (((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) ** 0.5)
    energy = energy * center_w
    energy[: max(1, h // 12), :] = 0
    energy[-max(1, h // 12):, :] = 0
    total = float(energy.sum())
    if total <= 1e-6:
        return (0.5, 0.45, 0.0)
    thresh = np.percentile(energy, 88)
    mask = energy >= thresh
    if mask.sum() < 4:
        return (0.5, 0.45, 0.0)
    e = energy[mask]
    fy = float((yy[mask] * e).sum() / e.sum()) / h
    fx = float((xx[mask] * e).sum() / e.sum()) / w
    # compacidade: quanto da energia forte esta perto do centroide
    d = np.sqrt(((xx[mask] / w) - fx) ** 2 + ((yy[mask] / h) - fy) ** 2)
    near = float(e[d < 0.16].sum() / e.sum())
    spread = float(np.sqrt(((d ** 2) * e).sum() / e.sum()))
    conf = max(0.0, min(1.0, near * (1.0 - min(spread / 0.35, 1.0))))
    return (min(max(fx, 0.15), 0.85), min(max(fy, 0.15), 0.85), round(conf, 2))


def is_specific(query: str) -> bool:
    """Nome proprio que rende foto certa: marca conhecida ou 2+ palavras capitalizadas."""
    q = query.strip()
    if not q:
        return False
    if strip_accents(q.lower()) in {strip_accents(e) for e in BIG_ENTITIES}:
        return True
    toks = words(q)
    caps = [t for t in toks if t[0].isupper() or (t[0].islower() and any(c.isupper() for c in t[1:3]))]
    return len(caps) >= 2 and len(caps) == len([t for t in toks if not is_stop(t)])


ROMAN_NUM = {"1": "i", "2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi", "7": "vii", "8": "viii", "9": "ix", "10": "x"}


def _numerals(text: str) -> set[str]:
    """Numeros de modelo no texto, com romano e arabico igualados (Mark 2 == Mark II)."""
    out = set()
    for w in re.findall(r"\b(\d{1,2}|[ivx]{1,4})\b", text.lower()):
        out.add(ROMAN_NUM.get(w, w))
    return out


def _title_matches(query: str, credit: str) -> bool:
    """O titulo da pagina precisa conter o termo mais especifico da busca
    ("iPhone Duo" nao aceita "List of iPhone models")."""
    qt = [strip_accents(w.lower()) for w in words(query) if len(w) > 2 and not is_stop(w)]
    ct = {strip_accents(w.lower()) for w in words(credit) if len(w) > 1}
    if not qt:
        return False
    generic = {"apple", "iphone", "google", "samsung", "galaxy", "android", "microsoft", "windows", "meta", "brasil", "novo", "nova"}
    specific = [t for t in qt if t not in generic] or qt
    if _numerals(query) != _numerals(credit.split(":", 1)[-1]):
        return False   # "harvard mark 2" nao aceita a pagina do Harvard Mark I
    return all(t in ct for t in specific)


CANONICAL = {
    "apple": ("Apple Inc.", "en"), "google": ("Google", "en"), "microsoft": ("Microsoft", "en"), "meta": ("Meta Platforms", "en"),
    "amazon": ("Amazon (company)", "en"), "tesla": ("Tesla, Inc.", "en"), "nvidia": ("Nvidia", "en"), "samsung": ("Samsung Electronics", "en"),
    "twitter": ("Twitter", "en"), "oracle": ("Oracle Corporation", "en"), "uber": ("Uber", "en"), "pix": ("Pix (payment system)", "en"),
    "claro": ("Claro (company)", "en"), "vivo": ("Vivo (telecommunications)", "en"), "tim": ("TIM Brasil", "en"), "openai": ("OpenAI", "en"),
    "whatsapp": ("WhatsApp", "en"), "instagram": ("Instagram", "en"), "tiktok": ("TikTok", "en"), "netflix": ("Netflix", "en"),
    "nubank": ("Nubank", "en"), "windows": ("Microsoft Windows", "en"), "android": ("Android (operating system)", "en"),
    "iphone": ("iPhone", "en"), "chatgpt": ("ChatGPT", "en"), "steam": ("Steam (service)", "en"), "playstation": ("PlayStation", "en"),
    "xbox": ("Xbox", "en"), "nintendo": ("Nintendo", "en"), "youtube": ("YouTube", "en"), "spotify": ("Spotify", "en"), "intel": ("Intel", "en"),
    "amd": ("AMD", "en"), "linux": ("Linux", "en"), "spacex": ("SpaceX", "en"), "nasa": ("NASA", "en"), "elon musk": ("Elon Musk", "en"),
    "bill gates": ("Bill Gates", "en"), "ibm": ("IBM", "en"), "ifood": ("IFood", "en"), "mercado livre": ("Mercado Libre", "en"),
    "anatel": ("Agência Nacional de Telecomunicações", "pt"), "starlink": ("Starlink", "en"), "motorola": ("Motorola", "en"),
    "xiaomi": ("Xiaomi", "en"), "huawei": ("Huawei", "en"), "sony": ("Sony", "en"), "lg": ("LG Electronics", "en"), "dell": ("Dell", "en"),
    "lenovo": ("Lenovo", "en"), "asus": ("Asus", "en"), "hp": ("HP Inc.", "en"), "zuckerberg": ("Mark Zuckerberg", "en"),
    "mark zuckerberg": ("Mark Zuckerberg", "en"), "sam altman": ("Sam Altman", "en"), "jeff bezos": ("Jeff Bezos", "en"),
    "tim cook": ("Tim Cook", "en"), "satya nadella": ("Satya Nadella", "en"), "jensen huang": ("Jensen Huang", "en"),
    "galaxy": ("Samsung Galaxy", "en"), "gemini": ("Gemini (chatbot)", "en"), "copilot": ("Microsoft Copilot", "en"),
    "anthropic": ("Anthropic", "en"), "deepseek": ("DeepSeek", "en"), "telegram": ("Telegram (software)", "en"), "x": ("Twitter", "en"),
}


def _commons_ok(query: str, c: dict, max_year: int | None) -> bool:
    """Arquivo do Commons so entra se o nome do arquivo citar o assunto (metade das palavras da
    consulta) e, em historia antiga, se a foto nao for moderna."""
    qt = [strip_accents(w.lower()) for w in words(query) if len(w) > 2 and not is_stop(w)]
    qt = [w for w in qt if not w.isdigit() and w not in {"team", "old", "history", "historical", "photo", "with", "legend", "origin", "word", "timeline"}]
    title = strip_accents(c.get("title", "").lower())
    if re.search(r"\b(film|movie|screenshot|trailer|poster|painting|cartoon|comic|logo|icon|map|flag)\b", title):
        return False   # cena de filme, pintura, logo, mapa: nao e foto do fato
    # 1-2 palavras: todas; 3: duas; 4: tres ("notebook with butterfly" nao aceita quadro de borboletas)
    if not qt or sum(1 for w in qt if re.search(r"\b" + re.escape(w), title)) < len(qt) - len(qt) // 3:
        return False
    if _numerals(query) - _numerals(title):
        return False
    return not (max_year and c.get("year") and c["year"] > max_year)


def lookup(query: str, lang: str = "pt", strict: bool = True, max_n: int = 2, max_year: int | None = None) -> list[dict]:
    """Candidatos (url, credit) na Wikipedia/Commons para uma consulta.
    strict (noticia): so pagina canonica de marca/pessoa ou nome proprio composto
    com titulo conferido; nada de busca solta no Commons."""
    out = []
    key = strip_accents(query.lower().strip())
    if key in CANONICAL:
        title, lg = CANONICAL[key]
        r = wikipedia.page_image(title, lg)
        if r:
            out.append({"url": r["url"], "credit": r["credit"]})
        return out
    if strict and not is_specific(query):
        return out
    for lg in (lang, "en"):
        r = wikipedia.page_image(query, lg)
        if r and _title_matches(query, r["credit"]):
            out.append({"url": r["url"], "credit": r["credit"]})
            break
    if not strict and len(out) < max_n:
        for c in wikipedia.commons_search(query)[:6]:
            if len(out) >= max_n:
                break
            if not _commons_ok(query, c, max_year):
                continue
            if all(c["url"] != o["url"] for o in out):
                out.append({"url": c["url"], "credit": "Commons: " + c["credit"]})
    return out


def make_visual(path: Path, credit: str, query: str) -> dict:
    fx, fy, conf = focal_point(path)
    return {"path": str(path), "focal": (fx, fy), "focal_conf": conf, "credit": credit, "query": query}


def phash(path: Path) -> int:
    """Hash perceptual 64 bits (media de 8x8 em cinza). Fotos iguais em tamanhos
    diferentes dao o mesmo hash; parecidas ficam a poucos bits."""
    try:
        img = Image.open(path).convert("L").resize((8, 8), Image.BILINEAR)
    except Exception:
        return 0
    a = np.asarray(img, dtype=np.float32)
    bits = (a > a.mean()).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def dedupe(visuals: list[dict], max_dist: int = 5) -> list[dict]:
    out: list[dict] = []
    for v in visuals:
        h = v.get("hash")
        if h is None:
            h = phash(Path(v["path"]))
            v["hash"] = h
        if all(_hamming(h, o["hash"]) > max_dist for o in out):
            out.append(v)
    return out


def article_visuals(topic, log=None, max_n: int = 6) -> list[dict]:
    out = []
    urls = list(topic.image_urls)
    # outras redacoes sobre a mesma historia: og:image delas (e das materias, se der)
    for u in topic.extra.get("related_images", []):
        if u not in urls:
            urls.append(u)
    if len(urls) < 3:
        from ..sources.rss import fetch_article
        for ru in topic.extra.get("related_urls", [])[:2]:
            try:
                for u in fetch_article(ru)["images"][:3]:
                    if u not in urls:
                        urls.append(u)
            except Exception:
                pass
    for u in urls:
        if len(out) >= max_n + 4:
            break
        p = download(u, referer=topic.source_url)
        if p and all(v["path"] != str(p) for v in out):
            out.append(make_visual(p, topic.source_name if u in topic.image_urls else "imprensa", "materia"))
    out = dedupe(out)[:max_n]
    if log:
        log(f"  fotos da materia: {len(out)} distintas")
    return out


def query_visuals(query: str, strict: bool, lang: str = "pt", need: int = 1, log=None, max_year: int | None = None) -> list[dict]:
    out = []
    for c in lookup(query, lang, strict, max_n=need + 1, max_year=max_year):
        if len(out) >= need:
            break
        p = download(c["url"])
        if p:
            out.append(make_visual(p, c["credit"], query))
            if log:
                log(f"  imagem: {query} <- {c['credit'][:48]}")
    return out
