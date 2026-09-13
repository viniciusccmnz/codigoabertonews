"""Planejador: junta candidatos, mede potencial viral e monta o dia.

Potencial viral (heuristica, sem modelo):
- calor: a mesma historia em redacoes internacionais e em varias brasileiras;
- polemica: palavras que geram comentario (demissao, processo, vazamento, preco,
  proibicao, privacidade, IA substituindo emprego...);
- recencia, marca conhecida, numero no titulo, material suficiente, imagem;
- penalidade para conteudo comercial (review, cupom, oferta, tutorial);
- novidade: o que ja foi gerado sai da fila.
"""
from __future__ import annotations

import json
import random
import re
from datetime import date, datetime, timezone

from .config import DATA, OUTPUT
from .sources import facts, predictions, rss, wikipedia
from .topic import Topic
from .util.text import BIG_ENTITIES, STOPWORDS, has_number, strip_accents, words

HISTORY = OUTPUT / "history.json"
DAILY_SLOTS = ["tech_news", "story_or_news", "curiosity_or_theory", "tech_news", "history_or_prediction"]

TECH_TERMS = [
    "iphone", "android", "apple", "google", "microsoft", "windows", "linux", "samsung", "galaxy", "xiaomi", "motorola", "celular", "smartphone",
    "app", "aplicativo", "whatsapp", "instagram", "tiktok", "youtube", "facebook", "meta", "twitter", "telegram", "openai", "chatgpt", "gemini",
    "inteligência artificial", "inteligencia artificial", " ia ", "robô", "robo", "chip", "processador", "nvidia", "intel", "amd", "computador",
    "notebook", "pc ", "console", "playstation", "xbox", "nintendo", "steam", "jogo", "game", "internet", "wi-fi", "5g", "operadora", "vivo",
    "claro", "tim ", "anatel", "starlink", "satélite", "satelite", "spacex", "nasa", "foguete", "tesla", "carro elétrico", "elétrico", "bateria",
    "hacker", "vazamento", "senha", "golpe", "pix", "nubank", "banco digital", "cripto", "bitcoin", "software", "sistema", "atualização",
    "atualizacao", "tela", "câmera", "camera", "fone", "smartwatch", "relógio", "streaming", "netflix", "spotify", "amazon", "alexa", "nuvem",
    "cloud", "dados", "privacidade", "algoritmo", "rede social", "redes sociais", "site", "navegador", "chrome", "programador", "código", "codigo",
    "tecnologia", "tech", "digital", "eletrônico", "eletronico", "gadget", "drone", "óculos", "oculos", "realidade", "vr", "impressora", "usb",
    "bluetooth", "fibra", "servidor", "data center", "energia", "nuclear", "bilhões", "startup", "musk", "zuckerberg", "bezos", "altman",
]

SELF_PROMO = [
    " g1", "g1 ", "portal", "aniversário", "aniversario", "podcast", "ao vivo", "assista", "assistir", "vídeo:", "video:", "no vídeo", "no video",
    "entrevista", "coluna", "opinião", "opiniao", "editorial", "programa", "episódio", "episodio", "newsletter", "resumo da semana", "o que rolou",
    "retrospectiva", "boletim", "agenda", "evento", "palestra", "curso", "vagas", "concurso", "sorteio", "bastidores", "tecnocast",
]

AD_MARKERS = [
    "desconto", "descontos", "cupom", "cupons", "promocao", "promocoes", "promocional", "patrocinado", "patrocinada",
    "patrocinio", "publieditorial", "conteudo de marca", "black friday", "cashback", "frete gratis", "menor preco",
    "preco baixo", "achados", "imperdivel", "compre", "assine", "clube", "curso", "cursos", "matricula",
    "centro universitario", "faculdade", "sorteio", "brinde", "inscritos", "sponsored", "deals", "coupon",
]
GENERIC_SOURCE_WORDS = {"tecnologia", "tech", "news", "noticias", "br", "brasil", "online", "digital", "blog", "site", "portal"}


def _source_phrases(name: str) -> list[str]:
    """"Olhar Digital" -> ["olhar digital"]; "G1 Tecnologia" -> ["g1 tecnologia", "g1"]. Palavra generica sozinha nao conta."""
    full = strip_accents(name.lower()).strip()
    words = [w for w in re.findall(r"\w+", full) if w not in GENERIC_SOURCE_WORDS]
    out = [full] if len(full) >= 3 else []
    if words and " ".join(words) != full and re.search(r"\d", " ".join(words)):   # "g1"; "olhar" sozinho seria verbo
        out.append(" ".join(words))
    return out


def is_advert(t) -> bool:
    """Materia do proprio portal sobre ele mesmo, ou texto comercial (desconto, cupom, patrocinio, curso)."""
    head = strip_accents((t.title + " " + " ".join(t.sentences[:3])).lower())
    if any(re.search(r"\b" + re.escape(ph) + r"\b", head) for ph in _source_phrases(t.source_name or "")):
        return True
    return any(re.search(r"\b" + re.escape(m) + r"\b", head) for m in AD_MARKERS)


EN_STOP = set("the a an of to in on for and with is are was were be by from at as it its this that new says said after over into about how why what who will can could has have had not just more than".split())


def load_history() -> dict:
    if HISTORY.exists():
        return json.loads(HISTORY.read_text("utf-8"))
    return {"used": {}}


def mark_used(topic_id: str, title: str, path: str = "") -> None:
    h = load_history()
    h["used"][topic_id] = {"title": title, "when": datetime.now(timezone.utc).isoformat(), "path": path}
    HISTORY.write_text(json.dumps(h, ensure_ascii=False, indent=1), "utf-8")


def _tokens(text: str) -> set[str]:
    out = set()
    for w in words(text):
        n = strip_accents(w.lower())
        if len(n) >= 4 and n not in STOPWORDS and n not in EN_STOP:
            out.add(n)
    return out


def _similar(a: str, b: str) -> float:
    wa, wb = _tokens(a), _tokens(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _cfg() -> dict:
    return json.loads((DATA / "feeds.json").read_text("utf-8"))


def heat_of(topic: Topic, heat_items: list[dict], pt_topics: list[Topic], cfg: dict) -> tuple[float, list[str]]:
    """Quantas outras redacoes cobrem a mesma historia (0..). Entidade conhecida
    em comum vale mais que palavra generica."""
    mine = (_tokens(topic.title) | {strip_accents(e.lower()) for e in topic.entities}) - GENERIC
    mine |= {GLOSSARY[t] for t in list(mine) if t in GLOSSARY}
    big = {e for e in BIG_ENTITIES if e in strip_accents(topic.title.lower())}
    best_per_source: dict[str, float] = {}
    for it in heat_items:
        if it["age_hours"] > cfg.get("heat_hours", 48):
            continue
        theirs = _tokens(it["title"]) - GENERIC
        shared = mine & theirs
        their_big = {e for e in BIG_ENTITIES if e in strip_accents(it["title"].lower())}
        ok = len(shared) >= 3 or (big & their_big and len(shared) >= 2) or (len(shared) >= 2 and any(len(s) >= 7 for s in shared))
        if ok:
            v = it["weight"] + min(it.get("points", 0), 600) / 300
            best_per_source[it["source"]] = max(best_per_source.get(it["source"], 0.0), v)
    for other in pt_topics:
        if other.id != topic.id and other.source_name != topic.source_name and _similar(other.title, topic.title) >= 0.3:
            best_per_source[other.source_name] = max(best_per_source.get(other.source_name, 0.0), 0.8)
    return round(sum(best_per_source.values()), 2), sorted(best_per_source)


GLOSSARY = {
    "dobravel": "foldable", "tela": "display", "preco": "price", "precos": "price", "vazamento": "leak", "vaza": "leak", "vazou": "leak",
    "processo": "lawsuit", "processa": "sues", "multa": "fine", "demissao": "layoffs", "demissoes": "layoffs", "demite": "layoffs",
    "proibe": "bans", "proibicao": "ban", "falha": "outage", "atualizacao": "update", "bateria": "battery", "camera": "camera",
    "relogio": "watch", "relogios": "watch", "oculos": "glasses", "fone": "headphones", "fones": "headphones", "carro": "car", "carros": "cars",
    "robo": "robot", "robos": "robots", "satelite": "satellite", "foguete": "rocket", "jogo": "game", "jogos": "games", "aplicativo": "app",
    "senha": "password", "senhas": "passwords", "golpe": "scam", "ataque": "attack", "dados": "data", "privacidade": "privacy",
    "governo": "government", "tarifa": "tariff", "tarifas": "tariffs", "bilhoes": "billion", "milhoes": "million", "inteligencia": "intelligence",
    "artificial": "ai", "chip": "chip", "chips": "chips", "computador": "computer", "notebook": "laptop", "navegador": "browser",
    "assistente": "assistant", "modelo": "model", "usuarios": "users", "empresa": "company", "compra": "acquires", "comprou": "acquires",
    "presidente": "ceo", "executivo": "ceo", "lancamento": "launch", "memoria": "memory", "processador": "processor", "energia": "energy",
    "nuclear": "nuclear", "espaco": "space", "lua": "moon", "marte": "mars", "voo": "flight", "acidente": "crash", "explosao": "explosion",
    "hacker": "hacker", "hackers": "hackers", "criancas": "kids", "adolescentes": "teens", "escola": "school", "escolas": "schools",
}

GENERIC = {
    "novo", "nova", "novos", "novas", "lanca", "lancamento", "lancado", "preco", "precos", "brasil", "celular", "celulares", "apple", "iphone",
    "google", "samsung", "galaxy", "android", "update", "atualizacao", "chega", "ganha", "veja", "como", "after", "says", "launch", "launches",
    "review", "smartphone", "phone", "phones", "device", "devices", "modelo", "modelos", "versao", "version", "series", "linha", "tela", "screen",
    "recurso", "recursos", "feature", "features", "anuncia", "announces", "announced", "confira", "saiba", "entenda", "tudo", "sobre", "about",
    "primeiro", "primeira", "first", "melhor", "melhores", "best", "mundo", "world", "dobravel", "foldable", "folding",
}


def viral_score(t: Topic, cfg: dict | None = None) -> float:
    cfg = cfg or _cfg()
    low = strip_accents((t.title + " " + " ".join(t.sentences[:2])).lower())
    title_low = strip_accents(t.title.lower())
    sc = 0.0
    if t.theme in ("tech_news", "story"):
        age = t.extra.get("age_hours", 999)
        sc += 28 if age < 18 else (22 if age < 36 else (12 if age < 72 else 0))
        sc *= t.extra.get("weight", 1.0)
        sc += min(t.extra.get("heat", 0.0), 8) * 10
        if any(k in title_low for k in SELF_PROMO):
            sc -= 40  # materia institucional, podcast, video do proprio portal
        pol = 0
        for k, wgt in cfg.get("polemic", {}).items():
            if strip_accents(k) in title_low:
                pol += wgt * 1.5
            elif strip_accents(k) in low:
                pol += wgt * 0.6
        sc += min(pol, 30)
        if any(c in title_low for c in cfg.get("commercial", [])):
            sc -= 45
        if re.match(r"^\d+\s", title_low) or " x " in title_low or re.search(r"\bquais\b|\bquem s[aã]o\b|\brivais\b|\balternativas\b|\bop[cç][oõ]es\b|\bcoisas que\b|\bdicas\b", title_low):
            sc -= 25  # listas e comparativos: pouco fato, pouco comentario
        padded = " " + title_low + " "
        if not any(strip_accents(k) in padded for k in TECH_TERMS):
            sc -= 40  # titulo nao e de tecnologia (evento, celebridade, esporte)
        if title_low.endswith("?"):
            sc += 4
    if has_number(t.title):
        sc += 8
    if any(e in title_low for e in BIG_ENTITIES):
        sc += 12
    if 3 <= len(t.sentences) <= 16:
        sc += 8
    elif len(t.sentences) < 2:
        sc -= 20
    if t.image_urls or t.image_queries:
        sc += 5
    if t.theme == "history":
        sc += min(t.extra.get("tech_hits", 0), 4) * 5 + 20
    if t.theme in ("curiosity", "prediction", "theory"):
        sc += 30
    return round(sc, 1)


def gather(log=None, today: date | None = None, max_age_hours: float = 60.0) -> list[Topic]:
    today = today or date.today()
    cfg = _cfg()
    out: list[Topic] = []
    heat_items: list[dict] = []
    if log:
        log("Buscando noticias (RSS nacionais e internacionais)...")
    try:
        pt, heat_items = rss.fetch_all(max_age_hours, log)
        for t in pt:
            t.extra["heat"], t.extra["heat_sources"] = heat_of(t, heat_items, pt, cfg)
            # fotos e materias das outras redacoes sobre a mesma historia: mais imagem no assunto
            related = [o for o in pt if o.id != t.id and o.source_name != t.source_name and _similar(o.title, t.title) >= 0.3]
            t.extra["related_images"] = [u for o in related for u in o.image_urls][:6]
            t.extra["related_urls"] = [o.source_url for o in related][:3]
        out += pt
        if log:
            log(f"  {len(pt)} noticias em portugues, {len(heat_items)} itens internacionais para medir calor")
    except Exception as e:
        if log:
            log(f"  rss falhou: {type(e).__name__}: {e}")
    if log:
        log("Buscando 'neste dia' na Wikipedia...")
    try:
        out += wikipedia.on_this_day("pt", today, log)
    except Exception as e:
        if log:
            log(f"  wikipedia falhou: {type(e).__name__}")
    out += facts.load_topics()
    out += facts.load_theories()
    out += predictions.load_topics(today.year)
    used = load_history()["used"]
    used_titles = [u.get("title", "") for u in used.values()]
    fresh = []
    for t in out:
        if t.id in used:
            continue
        if t.theme in ("tech_news", "story") and is_advert(t):
            continue  # propaganda de portal ou de marca nao vira video
        # mesma historia com outro titulo (outra redacao, outro dia) tambem nao repete
        if any(_similar(t.title, ut) >= 0.45 for ut in used_titles):
            continue
        t.score = viral_score(t, cfg)
        fresh.append(t)
    fresh.sort(key=lambda t: -t.score)
    return fresh


def plan_day(topics: list[Topic], n: int = 5, rng_seed: int = 0) -> list[Topic]:
    """n temas com mix: 2 noticias quentes, 1 incidente (ou noticia), 1 curiosidade,
    1 'neste dia' ou previsao. Sem repetir historia."""
    rng = random.Random(rng_seed)
    pool = list(topics)
    chosen: list[Topic] = []
    for slot in DAILY_SLOTS[:n]:
        if slot == "story_or_news":
            want = ["story", "tech_news"]
        elif slot == "curiosity_or_theory":
            want = ["curiosity", "theory"] if rng.random() < 0.65 else ["theory", "curiosity"]
        elif slot == "history_or_prediction":
            want = ["history", "prediction"] if rng.random() < 0.6 else ["prediction", "history"]
        else:
            want = [slot]
        cands = [t for t in pool if t.theme in want]
        if slot == "story_or_news":
            stories = [t for t in cands if t.theme == "story"]
            cands = stories if stories and stories[0].score >= cands[0].score * 0.7 else cands
        if slot == "curiosity_or_theory" and cands:
            first = [t for t in cands if t.theme == want[0]] or cands
            pick = rng.choice(first[: min(8, len(first))])
        elif slot == "history_or_prediction" and cands:
            first = [t for t in cands if t.theme == want[0]]
            pick = (first or cands)[0]
        else:
            pick = cands[0] if cands else (pool[0] if pool else None)
        if pick is None:
            break
        # noticia fraca (sem calor e sem polemica) nao entra: vale mais uma curiosidade inedita
        if pick.theme in ("tech_news", "story") and pick.score < 55:
            alt = [t for t in pool if t.theme in ("curiosity", "theory") and t.id not in {c.id for c in chosen}]
            if alt:
                pick = rng.choice(alt[:10])
        chosen.append(pick)
        subj = strip_accents((pick.subject or "").lower())
        pool = [t for t in pool if t.id != pick.id and _similar(t.title, pick.title) < 0.3
                and not (subj and len(subj) > 3 and strip_accents((t.subject or "").lower()) == subj)]
    while len(chosen) < n and pool:
        chosen.append(pool.pop(0))
    return chosen
