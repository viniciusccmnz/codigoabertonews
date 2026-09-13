"""Processamento de texto sem modelo de linguagem: regras, regex e listas.

Tudo aqui e deterministico. A qualidade do roteiro depende destas funcoes, entao
cada uma faz uma coisa so e e testavel isoladamente.
"""
from __future__ import annotations

import html
import re
import unicodedata

STOPWORDS = set("""
a o os as um uma uns umas de do da dos das em no na nos nas por para com sem sob
sobre entre ate desde e ou mas que se nao sim ja mais menos muito muitos muita
muitas pouco poucos ao aos à às pelo pela pelos pelas este esta estes estas esse
essa esses essas aquele aquela isso isto aquilo ele ela eles elas eu tu voce
voces nos meu minha seu sua seus suas dele dela era foi ser esta estao sao tem
tinha havia ha como quando onde qual quais quem porque porquê pois entao assim
tambem ainda so apenas cada todo toda todos todas outro outra outros outras
mesmo mesma mesmos mesmas ano anos vez vezes dia dias hoje ontem amanha depois
antes agora aqui ali la sobre contra dentro fora bem mal grande grandes novo
nova novos novas pode podem foi foram sera serao segundo segunda disse afirma
diz the of and in to for on with is are was by
""".split())

# Palavras que, na fala, merecem destaque visual (zoom, cor, pop).
SUPERLATIVES = {
    "primeiro", "primeira", "maior", "menor", "unico", "unica", "nunca", "sempre",
    "todo", "toda", "todos", "todas", "recorde", "gigante", "enorme", "impossivel",
    "absurdo", "segredo", "proibido", "gratis", "milhoes", "milhao", "bilhoes",
    "bilhao", "trilhoes", "trilhao", "mil", "ultimo", "ultima", "pior", "melhor",
    "morreu", "acabou", "fim", "nova", "novo", "revela", "vazou", "quebrou",
}

HOOK_WORDS = [
    "primeir", "maior", "nunca", "recorde", "bilh", "milh", "gratis", "gratuit",
    "vazou", "vazamento", "proib", "segredo", "revela", "mudanc", "fim", "novo",
    "lanca", "hacker", "ataque", "falha", "apagao", "multa", "processo", "banido",
    "polemic", "urgente", "alerta", "golpe", "perig", "mist", "descobr", "cient",
]

BIG_ENTITIES = [
    "apple", "google", "microsoft", "openai", "nvidia", "samsung", "meta", "whatsapp",
    "instagram", "tiktok", "amazon", "tesla", "netflix", "nubank", "pix", "windows",
    "android", "iphone", "chatgpt", "steam", "playstation", "xbox", "nintendo",
    "youtube", "twitter", "spotify", "intel", "amd", "linux", "spacex", "nasa",
    "elon musk", "bill gates", "steve jobs", "ibm", "oracle", "uber", "ifood",
    "mercado livre", "anatel", "gov.br", "starlink", "claro", "vivo", "tim",
]

_NUM = r"\d[\d.,]*"
_UNITS = (
    r"%|mil|milh[oõ]es|milh[aã]o|bilh[oõ]es|bilh[aã]o|trilh[oõ]es|trilh[aã]o|km|kg|"
    r"toneladas?|anos?|horas?|dias?|segundos?|minutos?|d[oó]lares|reais|MB|GB|TB|KB|"
    r"metros?|bits?|bytes?|usu[aá]rios|pessoas|vezes|funcion[aá]rios|pa[ií]ses"
)
NUMBER_RE = re.compile(rf"({_NUM})\s?({_UNITS})?", re.IGNORECASE)
WORD_RE = re.compile(r"\d+(?:[.,]\d+)*%?|[\wÀ-ÿ]+(?:[-’'][\wÀ-ÿ]+)*")


def contract(s: str) -> str:
    """'de o Pix' -> 'do Pix'; 'em a Apple' -> 'na Apple'. Para texto de template."""
    rules = [
        (r"\bde o\b", "do"), (r"\bde a\b", "da"), (r"\bde os\b", "dos"), (r"\bde as\b", "das"),
        (r"\bem o\b", "no"), (r"\bem a\b", "na"), (r"\bem os\b", "nos"), (r"\bem as\b", "nas"),
        (r"\ba a\b", "à"), (r"\ba o\b", "ao"), (r"\bpor o\b", "pelo"), (r"\bpor a\b", "pela"),
        (r"\bDe o\b", "Do"), (r"\bDe a\b", "Da"), (r"\bEm o\b", "No"), (r"\bEm a\b", "Na"),
    ]
    for pat, rep in rules:
        s = re.sub(pat, rep, s)
    return s


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def clean_html(raw: str) -> str:
    if not raw:
        return ""
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", ". ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"https?://\S+", " ", s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\.\s*\.", ".", s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    return s


_ABBR = ["Dr", "Sr", "Sra", "Prof", "EUA", "Inc", "Ltda", "vs", "No", "Nº", "p", "ex", "etc", "St", "Mr", "Ms"]


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    t = text
    for a in _ABBR:
        t = re.sub(rf"\b{re.escape(a)}\.", a + "<DOT>", t)
    t = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", t)
    parts = re.split(r"(?<=[.!?…])\s+(?=[A-ZÀ-Ú\"“(\d])", t)
    out = []
    for p in parts:
        p = p.replace("<DOT>", ".").strip()
        if len(p.split()) >= 3:
            out.append(p)
    return out


def words(s: str) -> list[str]:
    return WORD_RE.findall(s)


def is_stop(w: str) -> bool:
    return strip_accents(w.lower()) in STOPWORDS


def has_number(s: str) -> bool:
    return bool(re.search(r"\d", s))


def entities(sentence: str, max_len: int = 3) -> list[str]:
    """Sequencias de palavras capitalizadas fora do inicio da frase. Sem NER,
    mas pega nome de empresa, produto e pessoa na maioria dos casos."""
    toks = words(sentence)
    if not toks:
        return []
    found, cur = [], []
    for i, w in enumerate(toks):
        # iPhone, iOS, eBay: minuscula inicial com maiuscula dentro tambem conta
        cap = len(w) > 1 and (w[0].isupper() or (w[0].islower() and any(c.isupper() for c in w[1:3])))
        if cap or (cur and re.fullmatch(r"\d+[A-Za-z]*", w)):
            cur.append(w)
            if len(cur) >= max_len:
                found.append(" ".join(cur)); cur = []
        else:
            if cur:
                found.append(" ".join(cur)); cur = []
    if cur:
        found.append(" ".join(cur))
    out = []
    for e in found:
        first_only = (e == toks[0] and len(e.split()) == 1)
        known = any(x in e.lower() for x in BIG_ENTITIES)
        if first_only and not known and not e.isupper():
            continue  # capitalizacao normal de inicio de frase
        if len(e.split()) == 1 and e in MODEL_SUFFIXES:
            continue  # "Pro", "Max" sozinhos nao identificam nada
        out.append(e)
    return out


MODEL_SUFFIXES = {"Pro", "Max", "Duo", "Plus", "Ultra", "Air", "Mini", "Lite", "Series", "Edition", "Fold", "Flip", "Note", "Prime", "Go", "One", "Neo", "Tab", "Book"}


def emphasis_words(sentence: str) -> list[str]:
    """Palavras que pedem destaque visual: numeros, superlativos, entidades, CAPS."""
    out = []
    toks = words(sentence)
    for i, w in enumerate(toks):
        if len(w) < 2 or w.upper() in {"US", "R"}:
            continue
        lw = strip_accents(w.lower())
        if re.match(r"^\d", w) or lw in SUPERLATIVES or (w.isupper() and len(w) > 1):
            out.append(w)
        elif i > 0 and w[0].isupper() and not is_stop(w):
            out.append(w)
    return out


def keywords(sentence: str, n: int = 4) -> list[str]:
    toks = words(sentence)
    scored = []
    for i, w in enumerate(toks):
        if is_stop(w) or len(w) < 3:
            continue
        sc = len(w) * 0.1
        if re.match(r"^\d", w):
            sc += 5
        if i > 0 and w[0].isupper():
            sc += 3
        if strip_accents(w.lower()) in SUPERLATIVES:
            sc += 2
        if any(h in strip_accents(w.lower()) for h in HOOK_WORDS):
            sc += 1.5
        scored.append((sc, i, w))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = sorted(scored[:n], key=lambda x: x[1])
    return [w for _, _, w in top]


def sentence_score(sentence: str) -> float:
    """Quanto uma frase rende como 'ponto' do video: numero, entidade, gancho e
    tamanho confortavel para narrar em ate 4 segundos."""
    n = len(words(sentence))
    sc = 0.0
    if has_number(sentence):
        sc += 2.0
    sc += min(len(entities(sentence)), 2) * 1.2
    low = strip_accents(sentence.lower())
    sc += min(sum(1 for h in HOOK_WORDS if h in low), 3) * 0.8
    if 7 <= n <= 20:
        sc += 1.5
    elif n > 30:
        sc -= 1.5
    if sentence.endswith("?"):
        sc += 0.5
    return sc


def trim_sentence(sentence: str, max_words: int = 22) -> str:
    toks = sentence.split()
    if len(toks) <= max_words:
        return sentence
    cut = " ".join(toks[:max_words])
    # corta em virgula/ponto-e-virgula (oracao inteira); nunca em "que"/"quando", que deixam a frase pendurada
    m = list(re.finditer(r",|;| e | mas | enquanto | além de | apesar de ", cut))
    if m and m[-1].start() > len(cut) * 0.45:
        cut = cut[: m[-1].start()]
    cut = cut.rstrip(" ,;:-")
    if cut.count('"') % 2 == 1:  # aspa aberta sem fechar: corta antes dela
        cut = cut[: cut.rfind('"')].rstrip(" ,;:-")
    toks = cut.split()
    dangling = {"quando", "porque", "onde", "como", "se", "após", "antes", "depois", "segundo", "conforme", "entre", "cerca", "quase", "mais", "menos"}
    while toks and len(toks) > 4 and (is_stop(toks[-1]) or toks[-1].lower() in dangling or re.fullmatch(r"\d[\d.,]*", toks[-1])):
        toks.pop()
    cut = " ".join(toks)
    if not cut.endswith((".", "!", "?")):
        cut += "."
    return cut


def punchline(sentence: str, max_words: int = 5) -> str:
    """Texto curto para a tela. Ordem de preferencia: numero+unidade+substantivo,
    entidade, palavras-chave. Sempre em caixa alta."""
    best, best_score = None, -1.0
    for m in NUMBER_RE.finditer(sentence):
        num = m.group(1)
        sc = 0.0
        if m.group(2):
            sc += 3.0  # numero com unidade e o que mais rende na tela
        if re.fullmatch(r"(19|20)\d\d", num):
            sc -= 1.0  # ano sozinho e fraco como punchline
        if sentence[max(0, m.start() - 4):m.start()].rstrip().endswith("$"):
            sc += 1.5
        sc += min(len(re.sub(r"\D", "", num)), 4) * 0.4  # numero maior chama mais atencao
        nxt = words(sentence[m.end():])[:1]
        if nxt and not is_stop(nxt[0]) and len(nxt[0]) > 2:
            sc += 1.2  # numero seguido de substantivo: "100 zeros", "27 toneladas"
        if sc > best_score:
            best, best_score = m, sc
    ents_all = [e for e in entities(sentence) if not re.match(r"^(US|R)\s", e)]  # "US 250" e moeda, nao nome
    if best is not None:
        num_txt = best.group(1).rstrip(".,")
        owner = next((e for e in ents_all if re.search(rf"(^|[\s-]|[A-Za-z]){re.escape(num_txt)}([\s-]|$|[A-Za-z])", e)), None)
        if owner and not best.group(2):
            return owner.upper().rstrip(".,;")  # "Windows 11", "GPT-6 Astra": o nome inteiro, nao o numero solto
    if best is not None:
        m = best
        prefix = ""
        pre = sentence[:m.start()].rstrip()
        if pre.endswith("US$"):
            prefix = "US$ "
        elif pre.endswith("R$"):
            prefix = "R$ "
        after = sentence[m.end():].strip()
        tail = words(after)[:3]
        tail_clean = []
        for w in tail:
            lw = strip_accents(w.lower())
            if lw in {"e", "mas", "que", "ou", "porque", "quando"}:
                break
            if is_stop(w):
                if tail_clean or lw not in {"de", "do", "da", "dos", "das", "em", "no", "na"}:
                    break
                tail_clean.append(w)
                continue
            tail_clean.append(w)
            break
        if tail_clean and is_stop(tail_clean[-1]):
            tail_clean = []
        core = prefix + m.group(0).strip()
        text = (core + " " + " ".join(tail_clean)).strip()
        if len(text.split()) <= max_words + 1:
            return text.upper().rstrip(".,;")
    ents = [e for e in entities(sentence) if e.upper() not in {"MB", "GB", "KB", "TB", "PC", "TV"}]
    if ents:
        e = max(ents, key=lambda x: (len(x.split()), -ents.index(x)))
        ent_words = {w for x in ents for w in x.split()}
        kws = [k for k in keywords(sentence, 3) if k not in ent_words]
        text = e + ((" " + kws[0]) if kws and len(e.split()) == 1 else "")
        return text.upper().rstrip(".,;")
    # sem numero nem entidade: comeco natural da frase, sem terminar em palavra vazia
    toks = words(sentence)[:max_words]
    while toks and (is_stop(toks[-1]) or len(toks[-1]) < 2):
        toks.pop()
    if len(toks) >= 2:
        return " ".join(toks).upper()
    kws = keywords(sentence, 3)
    return " ".join(kws).upper() if kws else sentence.split(".")[0][:28].upper()


def format_number_br(n: float, decimals: int = 0) -> str:
    if decimals:
        s = f"{n:,.{decimals}f}"
    else:
        s = f"{int(round(n)):,}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def humanize_number(n: float) -> str:
    """5400000000 -> 5,4 bilhoes. Usado em previsao com dados."""
    a = abs(n)
    for div, name_s, name_p in ((1e12, "trilhão", "trilhões"), (1e9, "bilhão", "bilhões"), (1e6, "milhão", "milhões"), (1e3, "mil", "mil")):
        if a >= div:
            v = n / div
            txt = format_number_br(v, 1 if v < 10 else 0)
            txt = txt[:-2] if txt.endswith(",0") else txt
            unit = name_s if txt == "1" else name_p
            return f"{txt} {unit}"
    if a == 0:
        return "0"
    if a < 1:
        txt = format_number_br(n, 3).rstrip("0").rstrip(",")
        return txt if txt not in ("0", "-0", "") else format_number_br(n, 4)
    if a < 10:
        txt = format_number_br(n, 1)
        return txt[:-2] if txt.endswith(",0") else txt
    return format_number_br(n, 0)


def to_speech(text: str) -> str:
    """Versao para a voz: tira URL, expande moeda e porcentagem, resolve siglas."""
    s = text
    s = re.sub(r"https?://\S+|www\.\S+", "", s)
    s = re.sub(r"#\w+", "", s)

    def money(sym_word):
        def rep(m):
            num, unit = m.group(1), (m.group(2) or "").strip()
            return f"{num} {unit} de {sym_word}" if unit else f"{num} {sym_word}"
        return rep

    units = r"(?:\s?(mil|milh[oõ]es|milh[aã]o|bilh[oõ]es|bilh[aã]o|trilh[oõ]es|trilh[aã]o)\b)?"
    s = re.sub(rf"US\$\s?({_NUM})(?<=[\d]){units}", money("dólares"), s)
    s = re.sub(rf"R\$\s?({_NUM})(?<=[\d]){units}", money("reais"), s)
    s = s.replace("&", " e ").replace(" x ", " vezes ").replace("×", " vezes ")
    s = re.sub(r"\bvs\.?\b", "contra", s)
    s = re.sub(r"\bT\.I\.?\b", "T I", s)
    s = re.sub(r"\bEUA\b", "Estados Unidos", s)
    s = re.sub(r"\bIA\b", "inteligencia artificial", s)
    s = re.sub(r"\bkm/h\b", "quilometros por hora", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def slugify(s: str, n: int = 48) -> str:
    s = strip_accents(s.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:n].rstrip("-") or "video"


def short_title(title: str, max_words: int = 9) -> str:
    """Titulo em forma de frase curta: corta em ' | ', ' - ', ':' ou ';' quando a
    primeira parte ja diz o que importa; nunca termina em palavra vazia."""
    t = re.sub(r"^\[.*?\]\s*", "", title).strip()
    t = re.sub(r"\b(veja|saiba|entenda|confira|descubra|conheça)\s+", "", t, flags=re.I)  # verbo de lista nao vira fala
    parts = re.split(r"\s[|–—-]\s|:\s|;\s", t)
    if len(parts) > 1 and len(parts[0].split()) >= 4:
        t = parts[0]
    toks = t.split()
    if len(toks) > max_words:
        cut = " ".join(toks[:max_words])
        m = list(re.finditer(r",| antes d| após | depois d| enquanto | para | e | mas | que ", cut))
        if m and m[-1].start() > len(cut) * 0.45:
            cut = cut[: m[-1].start()]
        toks = cut.split()
        while toks and (is_stop(toks[-1]) or len(toks[-1]) < 2 or toks[-1].lower() in {"antes", "após", "depois", "quando", "onde", "como"}):
            toks.pop()
        t = " ".join(toks)
    return t.strip().rstrip(".,;:!?")
