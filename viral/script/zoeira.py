"""Tom engracado com palavrao censurado, no jeito carioca (pedidos do dono em 13/09/2026: palavrao, mais palavrao
e um no gancho, e depois "usar igual carioca": interjeicao e intensificador com giria, nao xingamento duro).

Depois do roteiro pronto, uma chamada barata escreve piadas curtas de amigo zoeiro que entram depois de alguns
trechos, com palavrao marcado entre colchetes: "o bicho travou esse [caralho] inteiro". A voz fala a palavra e o
"piii" cobre a segunda metade dela (da para ouvir o comeco e entender); na legenda sai "cara***".
A fala original nao muda: reescrita livre trocava fato ("primeiro caso real de [merda]" no lugar de "bug")."""
from __future__ import annotations

from collections import Counter
import json
import math
import re

from .. import ai
from ..config import ZOEIRA
from ..util.text import strip_accents

JOKE_KINDS = ("context", "fact", "claim", "impact")   # gancho, aparte, virada, pergunta e fim ficam sem piada
MAX_JOKES = 6
BLEEP_FROM = 0.45   # fracao da palavra que a voz deixa ouvir antes do piii
# palavrao que a IA escreve sem colchete ganha o colchete aqui: nenhum sai sem piii
SWEAR_RE = re.compile(r"(?<![\[\w])(merdas?|bostas?|caralhos?|porra|putinh[ao]s?|putas?|arrombad[ao]s?|cacetes?|"
                      r"fod[aeio]\w*|cus?|desgra[çc]ad[ao]s?)(?![\]\w])", re.I)
PEOPLE_RE = re.compile(r"\b(engenheir\w*|cientist\w*|programador\w*|pesquisador\w*|operador\w*|ele|ela|eles|elas|"
                       r"galera|os caras|uns caras|esse cara|o cara|mulher|homem|time|equipe|gente|pessoal|inventor\w*|"
                       r"fundador\w*|s[oó]ci[oa]s?|ceo|dono\w*|criador\w*|chefe\w*|president\w*|funcion[aá]ri\w*|"
                       r"executiv\w*|usu[aá]ri\w*|cliente\w*|governo|pol[ií]tic\w*|inscrit\w*|seguidor\w*|"
                       r"assinante\w*|pessoas?|p[uú]blico|f[aã]s|brasileir\w*|consumidor\w*|devs?|jogador\w*)\b", re.I)
# xingamento de gente: derruba a piada inteira, mesmo sem pessoa por perto ("um milhao de otarios")
INSULT_RE = re.compile(r"\b(ot[aá]ri\w*|trouxas?|babacas?|idiotas?|imbecil|imbecis|burr[oa]s?|man[eé]s?|bund[aã]o|bund[oõ]es|"
                       r"palha[çc]\w*|corn[oa]s?|arrombad\w*|escrot\w*|vagabund\w*|retardad\w*|mongol\w*|viad\w*|bichas?|"
                       r"pat[eé]tic\w*|fracassad\w*|perdedor\w*|bab[aã]o|puxa.sacos?|cuz[aã]o|cuz[oõ]es|filhos? da puta|fdp|"
                       r"panacas?|jument\w*|piranhas?|vadias?|safad\w*|canalhas?|pilantras?|lixo humano|"
                       r"vai se f\w+|tomar no cu|vai tomar)\b", re.I)

SYSTEM = ("Você é roteirista de humor de um canal de curiosidades de tecnologia no TikTok, em português do Brasil. "
          "Escreve comentários curtos e engraçados, no tom de um amigo carioca zoeiro (fala do Rio, com gíria e palavrão do jeito que carioca usa), sem mudar nenhum fato. Responde apenas JSON.")

RULES = """Cada item é um trecho da narração de um vídeo. O campo "piada" diz o que escrever:
- "com palavrão": UMA frase curta (4 a 12 palavras) que a voz fala logo depois do trecho, comentando como um amigo carioca zoeiro. Leva UM palavrão, e a palavra que recebe o piii vai entre colchetes.
- "sem palavrão": a mesma coisa, só com gíria carioca, sem palavrão.
- "nenhuma": piada "".
Como o carioca usa palavrão (é assim que tem que soar):
- Palavrão é interjeição ou intensificador, quase nunca xingamento. Abre a frase ("[Porra], mermão, ...", "[Caralho], que parada sinistra.", "[Puta] que pariu, ..."), reforça ("lento pra [caralho]", "sinistro pra [caralho]", "uma máquina da [porra]", "é [foda]") ou vira pergunta ("que [porra] é essa?").
- Gíria junto: mermão, caraca, sinistro, maneiro, mó, bagulho, parada, papo reto, tá ligado, caô, coé, fala sério, sacanagem.
- Exemplos bons: "[Porra], mermão, um bicho travou a máquina inteira.", "Lenta pra [caralho] essa parada, papo reto.", "[Puta] que pariu, uma mariposa derrubou o computador.", "Que [porra] de bagulho gigante é esse?", "Uma conta dessa demorava, é [foda], tá ligado?", "Esse relé [arrombado] não aguentou nem um inseto."
- Exemplos ruins (duros, travados, fora de lugar): "Essa máquina era um caralho de um trambolho.", "Imagina ver uma televisão desse arrombado.", "Que putinha de inovação."
Regras:
1. A piada não repete o trecho e não traz fato novo: sem número, sem data, sem nome.
2. O palavrão comenta a situação, o bicho, a máquina ou o defeito. Nunca xinga pessoa: nada de ele, ela, os caras, a galera, engenheiros, cientistas, time, profissão, cidade ou país. Nada de "vai tomar no cu" nem "vai se foder" (soa como xingar quem assiste). Nada de preconceito.
3. Varie: no vídeo inteiro cada palavrão aparece no máximo duas vezes, e as piadas começam de jeitos diferentes.
4. Sem palavrão: gíria carioca engraçada ("Caraca, imagina a cara de quem abriu a máquina.").
5. Português falado do Rio, frase completa com pontuação, sem emoji.

Formato: {"piadas": [{"i": 0, "piada": "..."}]}"""


FEM_RE = re.compile(r"\b(um|esse|desse|nesse)(\s+\[(?:porras?|merdas?|bostas?|putinhas?|putas?)\])", re.I)
MASC_RE = re.compile(r"\b(uma|essa|dessa|nessa)(\s+\[(?:caralhos?|cacetes?|arrombados?)\])", re.I)
TO_FEM = {"um": "uma", "esse": "essa", "desse": "dessa", "nesse": "nessa"}
TO_MASC = {v: k for k, v in TO_FEM.items()}


def _case(src: str, dst: str) -> str:
    return dst[:1].upper() + dst[1:] if src[:1].isupper() else dst


def _mark(text: str) -> str:
    """Poe colchete no palavrao que veio sem e acerta o artigo ("um [porra]" -> "uma [porra]").

    Giria entre colchetes sai deles: no daily de 13/09 "[Caraca]" e "[Sacanagem]" levaram piii sem ser palavrao."""
    text = re.sub(r"\[([^\]]+)\]", lambda m: m.group(0) if SWEAR_RE.fullmatch(m.group(1).strip()) else m.group(1), text)
    text = SWEAR_RE.sub(lambda m: "[" + m.group(1) + "]", text)
    text = FEM_RE.sub(lambda m: _case(m.group(1), TO_FEM[m.group(1).lower()]) + m.group(2), text)
    return MASC_RE.sub(lambda m: _case(m.group(1), TO_MASC[m.group(1).lower()]) + m.group(2), text)


def _stem(word: str) -> str:
    return word[:5]   # caralho/caralhos, putinha/putinhas contam como o mesmo palavrao


def plain(text: str) -> str:
    """Texto para a voz: sem os colchetes (a palavra e falada; o piii entra no audio)."""
    return text.replace("[", "").replace("]", "")


def _norm(w: str) -> str:
    return strip_accents(re.sub(r"[^\w]", "", w.lower()))


def marks(text: str) -> list[str]:
    return [_norm(m) for m in re.findall(r"\[([^\]]+)\]", text)]


def _names(text: str) -> set[str]:
    """Palavras com maiuscula fora do comeco de frase (nomes proprios)."""
    out, start = set(), True
    for tok in re.findall(r"[\w]+|[.!?:]", text):
        if tok in ".!?:":
            start = True
            continue
        if not start and tok[:1].isupper() and len(tok) > 1:
            out.add(strip_accents(tok.lower()))
        start = False
    return out


def _joke_ok(joke: str) -> bool:
    words = plain(joke).split()
    if not 3 <= len(words) <= 12 or re.search(r"\d", joke) or _names(". " + re.sub(r"\b[A-Z]{2,5}\b", "", plain(joke))):
        return False
    if any(not (3 <= len(m) <= 12) for m in marks(joke)):
        return False
    if INSULT_RE.search(plain(joke)):
        return False
    return not (marks(joke) and PEOPLE_RE.search(plain(joke)))


HOOK_RULES = """Reescreva o gancho (a primeira fala) de um vídeo curto no jeito de um amigo carioca zoeiro, com UM palavrão, e mais curto.
1. Números, datas e nomes ficam exatamente iguais, e o fato principal também.
2. Um único palavrão do jeito que carioca usa: interjeição no começo ou intensificador, com a palavra que leva o piii entre colchetes. Nunca xingando pessoa, profissão, cidade ou país. Exemplos: "[Porra], mermão, em 1947 acharam uma mariposa dentro de um computador.", "[Caralho], um vírus derrubou metade da internet num dia.", "[Puta] que pariu, um relé parou a máquina inteira."
3. Corte enrolação ("poucas pessoas sabem disso", "você não vai acreditar", "olha só"). No máximo MAX palavras.
4. Português falado do Rio, frase completa, sem emoji.

Formato: {"gancho": "..."}"""


def _hook_ok(old: str, new: str) -> bool:
    """Gancho reescrito vale se manteve numeros, nomes e o grosso das palavras, ficou menor e tem um palavrao
    que nao cai perto de pessoa."""
    o, n = plain(old), plain(new)
    if INSULT_RE.search(n) and not INSULT_RE.search(o):
        return False
    if sorted(re.findall(r"\d+", o)) != sorted(re.findall(r"\d+", n)):
        return False
    low = strip_accents(n.lower())
    if any(name not in low for name in _names(o)):
        return False
    if len(n.split()) > len(o.split()) + 2 or len(n.split()) < 4:   # +2: "Porra, mermao," no comeco
        return False
    long_old = {t for t in re.findall(r"\w+", strip_accents(o.lower())) if len(t) >= 5}
    if long_old and len(long_old & set(re.findall(r"\w+", low))) < 0.5 * len(long_old):
        return False
    mk = marks(new)
    if len(mk) != 1 or not 3 <= len(mk[0]) <= 12 or not re.search(r"\[\w+\]", new):
        return False   # colchete com duas palavras nao casa com a legenda e ficaria sem piii
    if re.match(r"\W*\[\w+\]\W*(,|!|que pariu)", new, re.I):
        return True   # interjeicao abrindo a frase ("[Porra], ...", "[Puta] que pariu"): nao xinga ninguem
    toks = re.findall(r"\[?\w+\]?", new)
    at = next(k for k, t in enumerate(toks) if t.startswith("["))
    near = " ".join(toks[max(0, at - 4):at] + toks[at + 1:at + 3])
    return not (PEOPLE_RE.search(near) or _names(". " + re.sub(r"\b[A-Z]{2,5}\b", "", near)))


OPENERS = ("[Porra], mermão, ", "[Caralho], ", "[Puta] que pariu, ")
LOWER_START = {"a", "o", "as", "os", "um", "uma", "em", "no", "na", "nos", "nas", "de", "do", "da", "esse", "essa",
               "isso", "este", "esta", "hoje", "quando", "se", "você", "ninguém", "todo", "toda", "poucas", "poucos",
               "imagina", "existe", "já", "até", "por", "para", "com", "mais", "nunca", "sabia", "tem", "foi", "era"}


def _interjection(old: str) -> str:
    """Gancho original com a interjeicao carioca na frente; so o comeco muda de maiuscula ("A Apple" -> "a Apple")."""
    first = old.split()[0] if old.split() else ""
    body = old[:1].lower() + old[1:] if strip_accents(re.sub(r"\W", "", first).lower()) in {strip_accents(w) for w in LOWER_START} else old
    return OPENERS[sum(map(ord, old)) % len(OPENERS)] + body


def _hook(script, log=print) -> None:
    """Poe um palavrao com piii dentro do gancho e corta a enrolacao. Se mexer em fato, o gancho fica como estava."""
    bt = next((b for b in script.beats if b.kind == "hook"), None)
    if bt is None or marks(bt.text):
        return
    old = bt.text.strip()
    rules = HOOK_RULES.replace("MAX", str(len(old.split()) + 2))
    for attempt in range(2):
        extra = ("\n\nATENÇÃO: a versão anterior foi recusada. Mantenha cada número, nome, sigla e moeda do original "
                 "(não troque dólar por conto nem IPO por abrir capital) e ponha o palavrão como interjeição no começo.") if attempt else ""
        data = ai.chat_json(SYSTEM, rules + extra + "\n\nGANCHO:\n" + old, max_tokens=120, temperature=0.8, log=log)
        new = _mark(str((data or {}).get("gancho") or "").strip())
        if new and _hook_ok(old, new):
            if new[-1] not in ".!?":
                new += "."
            bt.text = new
            log(f"  tom zoeiro: gancho com palavrao ({len(plain(old).split())} -> {len(plain(new).split())} palavras)")
            return
    bt.text = _interjection(old)
    log(f"  tom zoeiro: reescrita recusada; gancho original com interjeicao ({bt.text.split(',')[0]})")


def apply(script, log=print):
    """Acrescenta ate MAX_JOKES piadas curtas depois de trechos de contexto e fato; a fala original fica igual.
    Palavrao vai entre colchetes (piii na voz, asterisco na legenda). Piada que cita pessoa, numero ou nome sai."""
    if not ZOEIRA or not ai.available():
        return script
    _hook(script, log)
    hook_marks = [m for b in script.beats if b.kind == "hook" for m in marks(b.text)]
    used0 = Counter(_stem(m) for m in hook_marks)
    idx = [i for i, b in enumerate(script.beats) if b.kind in JOKE_KINDS]
    if not idx:
        return script
    n = min(MAX_JOKES, len(idx))
    slots = sorted({idx[round(k * (len(idx) - 1) / max(1, n - 1))] for k in range(n)})
    plain_slot = slots[len(slots) // 2] if len(slots) >= 5 else None   # uma piada sem palavrao no meio
    payload = [{"i": i, "kind": script.beats[i].kind, "text": script.beats[i].text,
                "piada": "nenhuma" if i not in slots else ("sem palavrão" if i == plain_slot else "com palavrão")}
               for i in idx]
    best = None
    for attempt in range(2):   # menos de 3 palavroes validos: pede de novo e fica com a melhor
        extra = ("\n\nATENÇÃO: a versão anterior teve poucos palavrões válidos. Toda piada \"com palavrão\" leva um "
                 "palavrão entre colchetes, sobre bicho, máquina ou defeito, nunca sobre pessoa.") if attempt else ""
        if hook_marks:
            extra += "\n\nPalavrão que já está no gancho (pode voltar no máximo mais uma vez): " + ", ".join(hook_marks)
        data = ai.chat_json(SYSTEM, RULES + extra + "\n\nTRECHOS:\n" + json.dumps(payload, ensure_ascii=False),
                            max_tokens=900, temperature=0.8, log=log)
        jokes: dict[int, str] = {}
        dropped = 0
        for item in (data or {}).get("piadas", []):
            try:
                i = int(item.get("i"))
            except (TypeError, ValueError, AttributeError):
                continue
            joke = _mark(str(item.get("piada") or "").strip())
            if i not in slots or i in jokes or not joke:
                continue
            if not _joke_ok(joke):
                dropped += 1
                continue
            if joke[-1] not in ".!?":
                joke += "."
            jokes[i] = joke
        # palavrao primeiro; o resto na ordem da fala, ate MAX_JOKES
        keep, used = [], Counter(used0)
        for i in sorted(jokes, key=lambda i: (not marks(jokes[i]), i)):
            mk = [_stem(m) for m in marks(jokes[i])]
            if any(used[x] >= 2 for x in mk):   # carioca repete porra e caralho, mas a terceira vez cansa
                dropped += 1
                continue
            used.update(mk)
            keep.append(i)
        keep = keep[:MAX_JOKES]
        n_marks = sum(len(marks(jokes[i])) for i in keep)
        if best is None or n_marks > best[2]:
            best = (jokes, keep, n_marks, dropped)
        if n_marks >= 4:
            break
    jokes, keep, n_marks, dropped = best
    if n_marks == 0:
        log("  tom zoeiro: nenhuma piada com palavrao valida; roteiro sem piada")
        return script
    for i in keep:
        script.beats[i].text = script.beats[i].text.rstrip() + " " + jokes[i]
    log(f"  tom zoeiro: {len(keep)} piadas, {n_marks} palavroes com piii" + (f", {dropped} recusadas" if dropped else ""))
    return script


def censor(word: str) -> str:
    """"caralho" -> "cara***": a primeira metade fica, o resto vira asterisco."""
    m = re.match(r"^(\W*)(\w+)(\W*)$", word)
    if not m:
        return word
    core = m.group(2)
    k = max(1, math.ceil(len(core) * 0.5))
    return m.group(1) + core[:k] + "*" * (len(core) - k) + m.group(3)


def censor_timeline(script, timeline: list[dict]) -> list[tuple[float, float]]:
    """Troca o palavrao da legenda por asterisco e devolve onde entra o piii (segundos)."""
    spans: list[tuple[float, float]] = []
    for bt, tl in zip(script.beats, timeline):
        want = marks(bt.text)
        if not want:
            continue
        ws = []
        for w, s, e in tl["words"]:
            if want and _norm(w) == want[0]:
                want.pop(0)
                spans.append((s + BLEEP_FROM * (e - s), e + 0.04))
                w = censor(w)
            ws.append((w, s, e))
        tl["words"] = ws
    return spans
