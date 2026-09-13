"""B-roll por frase: a cada 3 a 5 s entra uma imagem nova, no comeco da frase ou oracao, mostrando a ideia dela
("chaves que abriam e fechavam" -> chave abrindo porta; "acharam uma mariposa na maquina" -> mariposa em maquina velha).
Quem assiste tem tempo de entender a imagem; troca por palavra solta cansava (pedido de 13/09/2026).
Uma chamada barata de gpt-4o-mini escolhe frase e busca; as imagens vem so do Pexels (livres)."""
from __future__ import annotations

import json
import math
import re

from .. import ai
from ..util.text import strip_accents

SHOT_SECONDS = 3.8   # uma imagem nova a cada ~3,8 s de fala (frase, nao palavra)

SYSTEM = "Você é editor de vídeos curtos de TikTok e escolhe b-roll em banco de imagens gratuito (Pexels)."

RULES = """Para cada trecho do roteiro, escolha imagens de banco que acompanhem a fala. A imagem troca a cada 3 a 5 segundos, sempre no começo de uma frase ou oração, e mostra a ideia da frase inteira: quem assiste precisa ter tempo de entender a imagem. Não troque a imagem a cada palavra concreta.
Responda só JSON: {"shots": [{"beat": <numero do trecho>, "word": "<2 ou 3 primeiras palavras da frase>", "query": "<busca em inglês>", "type": "clip" ou "photo"}]}
Regras:
1. Em cada trecho, gere o número de planos pedido em "planos", na ordem da fala, cada um numa frase ou oração diferente (se o trecho tiver menos frases, um plano por frase). O primeiro plano entra no começo do trecho.
2. "word" são as 2 ou 3 PRIMEIRAS palavras da frase ou oração em que a imagem entra, copiadas exatamente do texto, sem pontuação. Frase ou oração começa no início do trecho ou logo depois de ponto, vírgula, dois-pontos, ponto e vírgula, "e", "mas" ou "que".
3. "query": 2 a 4 palavras em inglês que mostram a ideia da frase, não a última palavra solta. Algo que dá para filmar ou fotografar: objeto, animal, lugar, ação. Pode ser literal ou uma associação visual simples. Exemplos: "chaves que abriam e fechavam" -> "key unlocking door"; "acharam uma mariposa presa dentro da máquina" -> "moth on old machine"; "a sala inteira era um computador" -> "old computer room"; "anotaram tudo no caderno" -> "old notebook handwriting"; "a peça quebrou" -> "broken gear"; "cada conta demorava horas" -> "vintage calculator buttons".
4. Proibido nome de pessoa, marca, empresa, produto, cidade ou país (o banco não tem); proibido pedir texto, logotipo, bandeira ou mapa.
5. Todas as buscas do vídeo diferentes entre si. Proibido busca vaga ("technology", "concept", "abstract", "history", "idea").
6. "clip" para ação ou movimento (voar, abrir, digitar, girar, andar); "photo" para objeto parado. Use clip em cerca de metade.
7. Quando o trecho fala de passado distante, use "vintage" ou "old" na busca se fizer sentido.
8. Nada abstrato ou de tempo ("years passing", "success", "time"): troque por objeto concreto ("old calendar", "clock", "dusty book").
9. Evite palavra de dois sentidos em inglês: "relay" vira "electrical relay switch", "bug" vira "moth" ou "insect", "key" vira "metal key", "table" vira "wooden table"."""


def tokens(text: str) -> list[str]:
    return [strip_accents(t.lower()) for t in re.findall(r"[\w%]+", text)]


def word_index(text: str, word: str, start: int = 0) -> int | None:
    """Posicao (em palavras) da primeira ocorrencia de `word` (uma ou mais palavras seguidas) a partir de `start`.
    Sem a sequencia inteira, tenta so as primeiras palavras."""
    want = tokens(word or "")
    toks = tokens(text)
    for n in range(len(want), 0, -1):
        for j in range(start, len(toks) - n + 1):
            if toks[j:j + n] == want[:n]:
                return j
    return None


def plan(script, timeline: list[dict], beats: list[int], log=None) -> dict[int, list[dict]]:
    """{trecho: [{"word", "idx", "query", "type"}]} em ordem de fala; buscas repetidas ou sem palavra valida saem."""
    if not beats or not ai.available():
        return {}
    payload = []
    for i in beats:
        dur = timeline[i]["end"] - timeline[i]["start"]
        payload.append({"beat": i, "tipo": script.beats[i].kind, "texto": script.beats[i].text,
                        "planos": max(1, math.ceil(dur / SHOT_SECONDS))})
    data = ai.chat_json(SYSTEM, RULES + "\n\nROTEIRO:\n" + json.dumps(payload, ensure_ascii=False),
                        max_tokens=1800, temperature=0.5, log=log)
    out: dict[int, list[dict]] = {}
    seen: set[str] = set()
    for s in (data or {}).get("shots", []):
        try:
            i = int(s.get("beat"))
        except (TypeError, ValueError):
            continue
        q = re.sub(r"\s+", " ", str(s.get("query", "")).strip().lower())
        if i not in beats or not q or not q.isascii() or not (1 <= len(q.split()) <= 6) or q in seen:
            continue
        prev = out.get(i, [])
        idx = word_index(script.beats[i].text, str(s.get("word", "")), prev[-1]["idx"] + 1 if prev else 0)
        if idx is None:
            continue
        seen.add(q)
        out.setdefault(i, []).append({"word": tokens(script.beats[i].text)[idx], "idx": idx, "query": q,
                                      "type": "clip" if s.get("type") == "clip" else "photo"})
    if log:
        log(f"  b-roll: {sum(len(v) for v in out.values())} planos sugeridos em {len(out)} trechos")
    return out


JUDGE = ("Você confere imagens de banco para um vídeo curto. Recebe a frase narrada, a imagem pedida pelo editor e as "
         "descrições das imagens encontradas. Aceite só a descrição que mostra de fato o objeto ou a ação pedidos. "
         "Recuse: sentido errado da palavra (corrida de revezamento para relé elétrico, academia ou esteira para sala de "
         "computadores), esporte, comida, gente posando sem o objeto pedido e descrição vazia. "
         'Responda só JSON: {"ok": [índices aceitos]}.')


def judge(phrase: str, query: str, alts: list[str], log=None) -> list[int]:
    """Indices das imagens (pela descricao do Pexels) que mostram o que a busca pede, no sentido da frase."""
    if not alts:
        return []
    if not ai.available():
        keys = [w for w in re.findall(r"[a-z]{3,}", query.lower())]
        return [k for k, a in enumerate(alts) if any(w in (a or "").lower() for w in keys)]
    lines = "\n".join(f"{k}: {re.sub(r'[-_]+', ' ', a or '').strip()[:160] or '(vazia)'}" for k, a in enumerate(alts))
    data = ai.chat_json(JUDGE, f'Frase: "{phrase}"\nImagem pedida: "{query}"\nDescrições:\n{lines}',
                        max_tokens=80, temperature=0.0, log=log)
    ok: set[int] = set()
    for k in (data or {}).get("ok", []):
        try:
            k = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= k < len(alts):
            ok.add(k)
    return sorted(ok)
