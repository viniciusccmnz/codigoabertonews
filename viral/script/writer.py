"""Roteirista: jornalismo de tecnologia, direto, com estrutura fixa.

  gancho (viral, curto) -> segue o canal -> contexto -> desenvolvimento (fatos
  na ordem da fonte) -> virada (o dado mais forte) -> na pratica (o que muda
  para quem assiste) -> pergunta para comentar -> fechamento

Alvo: 190 a 240 palavras (65 a 80 s narrados). Sem repeticao: cada frase e
comparada com gancho, titulo e anteriores. Sem sarcasmo, sem opiniao solta.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field

from ..config import BASE_HASHTAGS, DATA, THEMES
from ..topic import Topic
from ..util.text import (BIG_ENTITIES, contract, emphasis_words, entities, has_number, is_stop, punchline, sentence_score,
                         short_title, slugify, strip_accents, trim_sentence, words)

WORD_MIN, WORD_MAX = 185, 245


@dataclass
class Beat:
    kind: str                      # hook | follow | context | fact | twist | impact | question | close | growth | projection | caveat | verdict
    text: str
    punchline: str | None = None
    image_query: str | None = None
    emphasis: list[str] = field(default_factory=list)
    chart: dict | None = None
    stat: dict | None = None
    image_index: int | None = None
    mascot: str | None = None      # expressao do personagem neste trecho (None = nao aparece)


@dataclass
class Script:
    topic_id: str
    theme: str
    title: str
    beats: list[Beat]
    hashtags: list[str]
    caption: str
    cta_screen: str
    seed: int
    source_name: str = ""
    source_url: str = ""

    @property
    def word_count(self) -> int:
        return sum(len(words(b.text)) for b in self.beats)

    def to_dict(self) -> dict:
        return {
            "topic_id": self.topic_id, "theme": self.theme, "title": self.title, "seed": self.seed,
            "hashtags": self.hashtags, "caption": self.caption, "cta_screen": self.cta_screen,
            "source_name": self.source_name, "source_url": self.source_url, "word_count": self.word_count,
            "beats": [b.__dict__ for b in self.beats],
        }

    def narration_text(self) -> str:
        return "\n".join(f"[{b.kind}] {b.text}" for b in self.beats)


def _templates() -> dict:
    return json.loads((DATA / "templates.json").read_text("utf-8"))


def _fill(tpl: str, slots: dict) -> str | None:
    for n in re.findall(r"{(\w+)}", tpl):
        if not slots.get(n):
            return None
    return contract(tpl.format(**slots))


def _pick(rng: random.Random, options: list[str], slots: dict, max_words: int = 18) -> str:
    cands = [_fill(t, slots) for t in options if _fill(t, slots)]
    if not cands:
        return options[0]
    short = [c for c in cands if len(words(c)) <= max_words]
    return rng.choice(short) if short else min(cands, key=lambda c: len(words(c)))


def _sim(a: str, b: str) -> float:
    wa = {strip_accents(w.lower()) for w in words(a) if len(w) > 3 and not is_stop(w)}
    wb = {strip_accents(w.lower()) for w in words(b) if len(w) > 3 and not is_stop(w)}
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


_BOILER = re.compile(r"leia (também|mais)|foto:|crédito|credito|imagem:|reprodução|divulgação|clique|assine|newsletter|siga (o|a|nos)|"
                     r"confira|veja também|saiba mais|nesta (segunda|terça|quarta|quinta|sexta)|neste (sábado|domingo)|\bg1\b|tecnoblog|"
                     r"canaltech|tecmundo|olhar digital|hardware\.com|mundo conectado|convergência digital|com informações|via |fonte:|"
                     r"ver preço|prós|contras|comprar agora|link de afiliado|cupom|frete|whatsapp do|nosso canal|inscreva|podcast|"
                     r"guia de compras|selecionou|nossa lista|nossa seleção|a reportagem|a redação|o repórter|em nossos testes|testamos|"
                     r"\b(acima|abaixo|a seguir|neste vídeo|nesse vídeo|no vídeo|assista|veja o|confira o)\b", re.I)


def _lower_first(s: str, ents: list[str]) -> str:
    first = s.split()[0] if s.split() else ""
    if not first:
        return s
    if is_stop(first):
        return s[0].lower() + s[1:]
    if any(e.split()[0] == first for e in ents) or first.isupper() or (len(first) > 1 and first[1:2].isupper()):
        return s
    return s[0].lower() + s[1:]


def _hashtags(topic: Topic, theme: str) -> list[str]:
    tags = list(THEMES[theme]["hashtags"])
    for e in topic.entities[:2]:
        t = slugify(e, 20).replace("-", "")
        if t and t not in tags and len(t) > 2:
            tags.append(t)
    tags += [t for t in BASE_HASHTAGS if t not in tags]
    return tags[:10]


def _clean_fact(s: str) -> str:
    s = re.sub(r"\s*\([^)]{0,40}\)", "", s)
    s = re.sub(r"\s+([.,;:])", r"\1", s)
    s = re.sub(r"^(Mas|E|Ah, sim,|Ah,|Porém|Porem|Além disso,|Alem disso,)\s+", "", s).strip()  # conjuncao solta no inicio
    return (s[0].upper() + s[1:]) if s else s


def pick_facts(topic: Topic, avoid: list[str], n: int, max_words: int = 26, keep_order: bool = True) -> list[str]:
    """Frases da fonte com substancia, sem repetir, na ordem original (narrativa)."""
    rel_src = topic.title + " " + topic.extra.get("summary", "")
    generic = {"apple", "iphone", "google", "samsung", "galaxy", "android", "microsoft", "windows", "meta", "empresa", "novo", "nova", "novos",
               "lanca", "lancou", "anuncia", "anunciou", "brasil", "celular", "smartphone", "modelo", "recurso", "versao", "usuarios", "tela"}
    rel = {strip_accents(w.lower()) for w in words(rel_src) if len(w) >= 4 and not is_stop(w)}
    rel_specific = rel - generic
    summary_count = topic.extra.get("summary_count", len(topic.sentences))
    cands = []
    for i, s in enumerate(topic.sentences):
        toks = words(s)
        if _BOILER.search(s) or len(toks) < 7 or len(toks) > 40:
            continue
        if s.strip().endswith((":", "?")) or s.count('"') % 2:
            continue
        if re.search(r"\b(\w+)\s+\1\b|\bfoi é\b|\bé foi\b|\bé é\b", s, re.I) or re.match(r"^(foi|é|são|era|tem|há|ou|ah|bom|enfim|aliás|alias|então|entao|pois)\b", s, re.I):
            continue  # frase quebrada pela limpeza do html ou comeco de conversa
        capnum = sum(1 for t in toks if t[0].isupper() or t[0].isdigit())
        if capnum / len(toks) > 0.5 or not any(len(t) >= 4 and t.islower() for t in toks):
            continue
        st = {strip_accents(w.lower()) for w in toks if len(w) >= 4 and not is_stop(w)}
        if i >= 2 and len(st & rel_specific) < 1 and not topic.extra.get("_relaxed"):
            continue  # paragrafo de "leia tambem" ou assunto lateral: nada especifico do tema
        low = strip_accents(s.lower())
        title_low = strip_accents((topic.title + " " + topic.extra.get("summary", "")).lower())
        foreign = sum(1 for e in BIG_ENTITIES if e in low and e not in title_low)  # outra marca que nao e o assunto
        sc = sentence_score(s) + (1.0 if i < summary_count else 0.0) - (1.5 if len(toks) > 28 else 0.0)
        sc += min(len(st & rel_specific), 3) * 0.8 - foreign * 1.5
        cands.append((sc, i, s))
    # escolhe as n mais fortes, depois devolve na ordem do texto
    cands.sort(key=lambda x: -x[0])
    chosen: list[tuple[int, str]] = []
    for sc, i, s in cands:
        if len(chosen) >= n:
            break
        s = _clean_fact(s)
        nums = len(re.findall(r"\d[\d.,]*", s))
        s2 = trim_sentence(s, max_words - 3 * max(0, nums - 1))
        if any(_sim(s2, a) > 0.45 for a in avoid) or any(_sim(s2, c) > 0.45 for _, c in chosen):
            continue
        chosen.append((i, s2))
    if len(chosen) < 3 and rel_specific and not topic.extra.get("_relaxed"):
        # poucos fatos no assunto estrito: segunda passada sem o filtro de relevancia
        topic.extra["_relaxed"] = True
        try:
            out = pick_facts(topic, avoid, n, max_words, keep_order)
        finally:
            topic.extra.pop("_relaxed", None)
        if len(out) > len(chosen):
            return out
    if keep_order:
        chosen.sort(key=lambda x: x[0])
    return [s for _, s in chosen]


def _impact(T: dict, rng: random.Random, text: str) -> dict:
    low = " " + strip_accents(text.lower()) + " "
    hits = []
    for j in T["impact"]:
        if "*" in j["keys"]:
            continue
        k = sum(1 for key in j["keys"] if strip_accents(key) in low)
        if k:
            hits.append((k, j))
    if hits:
        hits.sort(key=lambda x: -x[0])
        top = [j for k, j in hits if k == hits[0][0]]
        return rng.choice(top)
    return next(j for j in T["impact"] if "*" in j["keys"])


def _entity_query(sentence: str, cycle: list[str], i: int) -> str | None:
    ents = [e for e in entities(sentence) if len(e) > 3 and e.upper() not in {"MB", "GB", "TB", "KB", "US", "PC"}]
    if ents:
        return max(ents, key=lambda e: len(e.split()))
    if cycle:
        return cycle[i % len(cycle)]
    return None


def _cap(text: str, n: int = 4) -> str:
    """Corta em n palavras sem terminar em palavra vazia ('O 5G E A' vira 'O 5G')."""
    toks = text.split()[:n]
    while len(toks) > 1 and (is_stop(toks[-1]) or len(toks[-1]) < 2):
        toks.pop()
    return " ".join(toks)


def _hook_punch(topic: Topic) -> str:
    title_ents = [e for e in entities(topic.title) if len(e) > 2 and e.upper() not in {"IA", "US", "R"} and not re.match(r"^(US|R)\s", e)]
    p = (max(title_ents, key=lambda e: len(e.split())) if title_ents else short_title(topic.title, 4)).upper()
    return _cap(p, 4)


def write(topic: Topic, seed: int = 0) -> Script:
    T = _templates()
    theme = topic.theme
    rng = random.Random(f"{topic.id}|{seed}")
    ents = [e for e in topic.entities if len(e) > 2]
    t_short = short_title(topic.title, 12)
    hook_fact = topic.extra.get("hook", "")
    slots = {
        "t": t_short, "e": ents[0] if ents else "", "subject": topic.subject or "", "year": str(topic.year) if topic.year else "",
        "years_ago": str(topic.extra.get("years_ago", "")) if topic.extra.get("years_ago") else "",
        "source": topic.extra.get("source_spoken", topic.source_name), "hook": hook_fact,
        "hook_cap": (hook_fact[0].upper() + hook_fact[1:]) if hook_fact else "", "event": topic.extra.get("event", ""),
        "origin": topic.extra.get("origin", ""),
    }
    B: list[Beat] = []
    cycle = list(topic.image_queries) or list(ents)
    n_img = len(topic.image_urls)

    def fact(text: str, i: int, query: str | None, mascot: str | None = None) -> Beat:
        p = _cap(punchline(text), 4)
        return Beat("fact", text, p, query, emphasis_words(text), image_index=(i % n_img) if n_img else None, mascot=mascot)

    def era_line(year: int | None) -> str | None:
        """Uma frase de contexto da epoca, por decada, para situar quem assiste."""
        if not year:
            return None
        table = [
            (1900, "Para ter uma ideia, naquela época não existia nem rádio comercial."),
            (1940, "Para ter uma ideia, naquela época a televisão era uma novidade rara."),
            (1960, "Para ter uma ideia, naquela época computador era um armário que só governos e grandes empresas tinham."),
            (1980, "Para ter uma ideia, naquela época o computador pessoal estava chegando às primeiras casas."),
            (1990, "Para ter uma ideia, naquela época a internet comercial estava começando e celular era artigo de luxo."),
            (2000, "Para ter uma ideia, naquela época o iPhone ainda não existia e a internet era discada na maioria das casas."),
            (2010, "Para ter uma ideia, naquela época o smartphone já era comum, mas o 4G ainda estava chegando ao Brasil."),
            (2020, "Para ter uma ideia, isso é da era da pandemia, quando o trabalho remoto virou regra."),
        ]
        out = None
        for y0, line in table:
            if year >= y0:
                out = line
        return out

    hook = _pick(rng, T["hooks"][theme], slots, 12 if theme in ("tech_news", "story") else 16)   # gancho resolvido em 3 s
    B.append(Beat("hook", hook, _hook_punch(topic) if theme in ("tech_news", "story") else _cap((topic.subject or t_short).upper(), 4),
                  cycle[0] if cycle else None, emphasis_words(hook), image_index=0, mascot="surprised"))
    B.append(Beat("follow", rng.choice(T["follow"]), rng.choice(T["cta_screen"]), None, [], mascot="point_up"))

    if theme in ("tech_news", "story"):
        ctx = _pick(rng, T["context"][theme], slots, 22)
        if _sim(ctx, hook) < 0.6:
            B.append(Beat("context", ctx, None, cycle[1 % len(cycle)] if cycle else None, emphasis_words(ctx), image_index=1 if n_img > 1 else 0))
        facts = pick_facts(topic, [hook, ctx, topic.title], 6)
        impact = _impact(T, rng, topic.title + " " + " ".join(facts[:2]) + " " + topic.extra.get("summary", ""))
        if not facts:
            facts = [t_short + "."]
        # a virada leva o fato mais forte entre os ultimos; os demais seguem a ordem da materia
        twist_i = max(range(len(facts)), key=lambda i: sentence_score(facts[i]) + (0.5 if i >= len(facts) // 2 else 0)) if len(facts) >= 3 else None
        for i, f in enumerate(facts):
            if i == twist_i:
                B.append(Beat("twist", rng.choice(T["twists"]), None, None, [], mascot="serious"))
                B.append(fact(f, i + 2, _entity_query(f, cycle, i + 2), mascot=None))
            else:
                B.append(fact(f, i + 2, _entity_query(f, cycle, i + 2)))
        B.append(Beat("impact", impact["text"], impact["punch"], None, emphasis_words(impact["text"]), mascot="explain"))
        B.append(Beat("question", impact["question"], "COMENTA", None, [], mascot="point_down"))
    elif theme == "curiosity":
        ctx = topic.extra.get("context", "")
        if ctx:
            B.append(Beat("context", ctx, None, cycle[1 % len(cycle)] if cycle else None, emphasis_words(ctx)))
        era = era_line(topic.year) if topic.year and topic.year < 2019 else None
        if era and _sim(era, ctx) < 0.4:
            B.append(Beat("context", era, None, None, emphasis_words(era)))
        body = [s for s in topic.sentences if _sim(s, hook) < 0.6]
        for i, s in enumerate(body):
            if i == len(body) - 1 and len(body) >= 3:
                B.append(Beat("twist", rng.choice(T["twists"]), None, None, [], mascot="serious"))
            B.append(fact(trim_sentence(s, 28), i + 1, cycle[min(i + 1, len(cycle) - 1)] if cycle else None))
        from datetime import date as _date
        if topic.year and _date.today().year - topic.year >= 5:
            ago = _date.today().year - topic.year
            ya = f"Isso foi em {topic.year}. Há {ago} anos."
            B.append(Beat("fact", ya, f"HÁ {ago} ANOS", cycle[0] if cycle else None, emphasis_words(ya), stat={"value": ago, "label": "anos atrás"}))
        tk = topic.extra.get("takeaway", "")
        if tk:
            B.append(Beat("impact", tk, "HOJE", None, emphasis_words(tk), mascot="explain"))
        B.append(Beat("question", rng.choice(T["curiosity_lines"]["question"]), "COMENTA", None, [], mascot="point_down"))
    elif theme == "history":
        event = topic.extra.get("event", topic.title)
        ctx = f"Em {topic.year}, {_lower_first(event, ents)}" if topic.year else event
        ctx = ctx if ctx.endswith((".", "!", "?")) else ctx + "."
        B.append(Beat("context", ctx, _cap(punchline(event), 4), cycle[0] if cycle else None, emphasis_words(ctx)))
        era = era_line(topic.year)
        if era:
            B.append(Beat("context", era, None, None, emphasis_words(era)))
        facts = pick_facts(topic, [hook, event], 4, 24)
        for k, f in enumerate(facts):
            if k == len(facts) - 1 and len(facts) >= 2:
                B.append(Beat("twist", rng.choice(T["twists"]), None, None, [], mascot="serious"))
            B.append(fact(f, k + 1, cycle[(k + 1) % len(cycle)] if cycle else None))
        if slots["years_ago"]:
            ya = T["history_lines"]["years_ago"].format(years_ago=slots["years_ago"])
            B.append(Beat("fact", ya, f"HÁ {slots['years_ago']} ANOS", cycle[0] if cycle else None, emphasis_words(ya),
                          stat={"value": int(slots["years_ago"]), "label": "anos atrás"}))
        B.append(Beat("impact", T["history_lines"]["takeaway"], "HOJE", None, [], mascot="explain"))
        B.append(Beat("question", T["history_lines"]["question"], "COMENTA", None, [], mascot="point_down"))
    elif theme == "prediction":
        chart = topic.extra["chart"]
        ctx = topic.extra.get("context", "") or _pick(rng, T["context"]["prediction"], slots, 30)
        B.append(Beat("context", ctx, None, cycle[1 % len(cycle)] if cycle else None, emphasis_words(ctx)))
        B.append(Beat("context", _pick(rng, T["context"]["prediction"], slots, 30), None, None, []))
        pts = topic.extra["points"]
        n = len(pts)
        for i, (y, v) in enumerate(pts):
            key = "point_first" if i == 0 else ("point_last" if i == n - 1 else "point_mid")
            s = T["prediction_lines"][key].format(year=y, value=v)
            B.append(Beat("fact", s, v.upper().split(" DE ")[0], None, emphasis_words(s),
                          chart={"series": chart["series"], "projection": chart["projection"], "unit": chart["unit"], "title": chart["title"], "progress": (i + 1) / n}))
        growth = topic.sentences[-1]
        m = re.search(r"(\d+)\s?%", growth)
        stat = {"value": int(m.group(1)), "suffix": "%", "label": "de crescimento" if "crescimento" in growth else "de queda"} if m else None
        B.append(Beat("growth", growth, None if stat else punchline(growth), None, emphasis_words(growth), stat=stat))
        B.append(Beat("twist", rng.choice(T["twists"]), None, None, [], mascot="serious"))
        proj = topic.extra["projection_line"]
        unit_sp = topic.extra.get("unit_spoken", "")
        proj_punch = topic.extra["projected_text"] if unit_sp.startswith("de ") else f"{topic.extra['projected_text']} {unit_sp}"
        B.append(Beat("projection", proj, proj_punch.strip().upper(), None, emphasis_words(proj),
                      chart={"series": chart["series"], "projection": chart["projection"], "unit": chart["unit"], "title": chart["title"], "progress": 1.0, "project": True}))
        imp = topic.extra.get("implication", "")
        if imp:
            B.append(Beat("impact", imp, "O QUE ISSO SIGNIFICA", None, emphasis_words(imp), mascot="explain"))
        counter = topic.extra.get("counter", "")
        if counter:
            B.append(Beat("caveat", counter, "O QUE PODE MUDAR", cycle[0] if cycle else None, emphasis_words(counter)))
        B.append(Beat("caveat", T["prediction_lines"]["caveat"], None, None, []))
        B.append(Beat("question", T["prediction_lines"]["question"], "COMENTA", None, [], mascot="point_down"))
    elif theme == "theory":
        origin = topic.extra.get("origin", "")
        if origin:
            o = T["theory_lines"]["origin"].format(origin=origin)
            B.append(Beat("context", o, "DE ONDE VEIO", cycle[0] if cycle else None, emphasis_words(o)))
        for c in topic.extra.get("claims", []):
            B.append(Beat("claim", c, punchline(c), cycle[1 % len(cycle)] if cycle else None, emphasis_words(c), mascot=None))
        B.append(Beat("twist", "Só que os fatos contam outra história.", None, None, [], mascot="serious"))
        for i, f in enumerate(topic.sentences):
            B.append(fact(f, i + 1, cycle[min(i + 1, len(cycle) - 1)] if cycle else None))
        verdict = topic.extra.get("verdict", "")
        if verdict:
            B.append(Beat("verdict", verdict, "VEREDITO", None, emphasis_words(verdict), mascot="explain"))
        B.append(Beat("question", T["theory_lines"]["question"], "COMENTA", None, [], mascot="point_down"))

    close = rng.choice(T["closers"])
    cta_screen = T["cta_screen"][T["closers"].index(close)]
    B.append(Beat("close", close, cta_screen, None, [], mascot="wave"))

    # orcamento de palavras: corta o fato mais fraco se passar; se faltar, o roteiro fica curto e o QA avisa
    def wc():
        return sum(len(words(b.text)) for b in B)
    while wc() > WORD_MAX:
        fi = [i for i, b in enumerate(B) if b.kind == "fact" and not b.chart and not b.stat]
        if len(fi) <= 2:
            break
        weakest = min(fi, key=lambda i: sentence_score(B[i].text))
        B.pop(weakest)
        if 0 < weakest < len(B) and B[weakest - 1].kind == "twist" and B[weakest].kind != "fact":
            B.pop(weakest - 1)

    tags = _hashtags(topic, theme)
    first_fact = next((b.text for b in B if b.kind in ("context", "fact")), "")
    caption = f"{hook} {first_fact}".strip() + "\n\n" + " ".join("#" + t for t in tags)
    return Script(topic.id, theme, topic.title, B, tags, caption, cta_screen, seed, topic.source_name, topic.source_url)


def say_year_once(script: Script) -> Script:
    """O ano da historia e falado uma vez so. Repetido em outro trecho, sai:
    "Em 1947, o Mark 2 ocupava..." -> "O Mark 2 ocupava..."; "Isso foi em 1947. Ha 79 anos." -> "Isso foi ha 79 anos."."""
    import re as _re
    said: set[str] = set()
    for b in script.beats:
        text = b.text
        years = _re.findall(r"\b(1[5-9]\d\d|20\d\d)\b", text)
        for y in set(years) & said:
            text = _re.sub(rf"\bIsso foi em {y}\.\s*Há\b", "Isso foi há", text)
            text = _re.sub(rf"(^|[.!?]\s+)(?:Em|No ano de) {y},\s*(\w)", lambda m: m.group(1) + m.group(2).upper(), text)
            text = _re.sub(rf",?\s+(?:em|no ano de) {y}(?=[,.!?])", "", text)
        said.update(years)
        b.text = text
    return script
