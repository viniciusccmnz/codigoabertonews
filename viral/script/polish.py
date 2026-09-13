"""Revisao do roteiro por IA (gpt-4o-mini), com validacao dura.

O template monta a estrutura e escolhe os fatos; a IA reescreve para ficar
fluido e coerente, como um jornalista lendo, sem inventar nada:
- mesma quantidade e ordem de trechos, mesmos tipos;
- todos os numeros dos fatos preservados (conferido); nada de fato novo;
- cada transicao (virada) apresenta o fato que vem em seguida;
- contexto situa a historia em vez de repetir o titulo;
- termos em ingles ficam como sao (a voz pronuncia); siglas escritas por extenso
  quando ajuda a fala.
Se a resposta falhar na validacao, fica o roteiro original.
"""
from __future__ import annotations

import re

from .. import ai
from ..util.text import emphasis_words, punchline, words
from .writer import Beat, Script

VIRAL = (
    "O que retém no TikTok (dados de 2025-2026): o algoritmo decide em 1,5 s e o gancho tem que estar resolvido aos 3 s; 90% dos vídeos fracos morrem nos 3 primeiros segundos. Ganchos que funcionam: resultado concreto na primeira frase, número específico (exato, não redondo), afirmação contrária ao senso comum, aviso de erro ou risco para quem assiste. Nunca 'hoje vamos falar', 'deixa eu te contar', 'então'. Curiosidade aberta (a pessoa fica para ver o fechamento), um dado novo a cada frase, uma quebra de padrão a cada 10-15 s (a virada), consequência concreta para quem assiste, e pedido de ação no fim. Dado específico e cruzado (comparação, proporção, antes e depois) vale mais que adjetivo."
)

SYSTEM = (
    "Você é editor-chefe de um canal de jornalismo de tecnologia no TikTok, em português do Brasil. "
    "Reescreve roteiros para ficarem fluidos, claros e envolventes, como um apresentador experiente falando, "
    "mantendo rigor factual absoluto. Nunca inventa dado, nome, número ou fato. Sem sarcasmo, sem gíria pesada, "
    "sem emoji. Frases curtas. Responde apenas JSON. "
    + VIRAL
)

RULES = """Reescreva o roteiro abaixo seguindo estas regras:
1. Devolva EXATAMENTE a mesma quantidade de trechos, na mesma ordem, com os mesmos "kind".
2. Preserve todos os números, nomes, datas e valores que aparecem nos trechos. Para dar fluidez e chegar ao tamanho, pode usar informações do "material_de_apoio" (é a própria matéria). Nada que não esteja nos trechos ou no material.
3. "hook": até 12 palavras, resolvido em 3 segundos de fala: comece pelo dado mais forte (número exato, nome, consequência), sem introdução, sem pergunta genérica, sem clickbait vazio.
4. "follow": um aparte de passagem, até 9 palavras, dito no meio da história e não como encerramento ("Rapidinho: segue o perfil aí, e vamos lá."). Diga "o perfil".
5. "context": situe a história (quem, o quê, quando) em 1 ou 2 frases, sem repetir o hook nem o título.
6. "fact"/"claim": mantenha o conteúdo, melhore a fluidez e a ligação com o trecho anterior (use conectivos naturais). Cada fato carrega um dado concreto (número, nome, data, comparação). Cruze dados do material quando der: proporção, antes e depois, "isso é o dobro de", "de cada 10" (comparações só com palavras ou número de um dígito; nenhum número novo de dois dígitos ou mais). Corte frase vaga ("pode acelerar", "diversas funções") se não houver dado para sustentá-la.
7. "twist": uma frase que anuncia o fato seguinte de forma específica (ex.: "E o detalhe que muda o jogo é o preço:"), nunca genérica.
8. "impact"/"verdict": consequência concreta para quem assiste (preço, emprego, uso, decisão), 2 frases, tom sério, só o que decorre do material; sem "isso pode impactar" vazio.
9. "question": uma pergunta curta e direta para comentar.
10. "close": 1 frase pedindo para salvar ou comentar e avisando que hoje ainda tem mais vídeo (o canal posta 5 por dia; nunca diga "amanhã"); não peça para seguir de novo.
11. Total entre 200 e 260 palavras. Português do Brasil natural para leitura em voz alta: escreva siglas de forma falável (T.I. vira "TI"; "IA" pode ficar "inteligência artificial" na primeira vez).
12. Para cada trecho, sugira "image" com 2 a 4 palavras em inglês descrevendo uma foto concreta que ilustre o trecho (objeto, lugar, pessoa pública, produto). Se o trecho cita pessoa, máquina, lugar ou objeto pelo nome, use o nome (ex.: "grace hopper", "harvard mark ii", "first computer bug logbook"). Proibido palavra abstrata que não vira foto: legend, origin, timeline, history, historical, concept, idea, moment, curious, stories, team, room, people. Trecho genérico sobre época ou tipo de coisa vira um exemplo famoso com nome que tenha foto na Wikipedia (ex.: computadores de 1947 → "eniac"; televisão antiga → "rca 630-ts"; máquinas de relé → "harvard mark i"). Nunca ponha nome de pessoa ou máquina que o próprio trecho não cita. Traduza certo: mariposa é "moth" (nunca "butterfly"), inseto é "insect".
13. Sugira "caption" (descrição do vídeo para o TikTok, até 150 caracteres, sem hashtags) e "hashtags" (8, sem #).

Responda no formato:
{"beats": [{"i": 0, "kind": "hook", "text": "...", "image": "..."}, ...], "caption": "...", "hashtags": ["...", ...]}
"""


COMPOSE_SYSTEM = (
    "Você é roteirista-chefe de um canal de jornalismo de tecnologia no TikTok, em português do Brasil. "
    "Escreve roteiros de 60 a 80 segundos para narração em voz alta: claros, diretos, com ritmo, sem sarcasmo, "
    "sem gíria pesada, sem emoji, sem opinião pessoal. Rigor factual absoluto: usa apenas o que está no material. "
    "Nunca inventa número, nome, data, empresa ou consequência. Responde apenas JSON. "
    + VIRAL
)

COMPOSE_RULES = """Escreva o roteiro de um vídeo a partir do MATERIAL abaixo, nesta estrutura e ordem exatas:

1. "hook" (1 frase, até 12 palavras, resolvida em 3 segundos de fala): comece pelo dado mais forte (número exato, nome, consequência concreta) ou por uma afirmação que contraria o que todo mundo pensa. Sem introdução, sem clickbait vazio, sem pergunta retórica genérica.
2. "follow" (até 9 palavras): aparte de passagem no meio da história, não encerramento ("Rapidinho: segue o perfil aí, e vamos lá."). Diga "o perfil".
3. "context" (1 ou 2 frases): quem, o quê, quando. Situe a história sem repetir o hook.
4. De 4 a 6 "fact": os fatos da matéria em ordem lógica (causa, consequência, número, comparação). Cada um com 12 a 26 palavras, ligado ao anterior por conectivos naturais, e cada um com um dado concreto (número, nome, data ou comparação). Cruze dados do material quando der: proporção, antes e depois, "o dobro", "de cada 10" (só com palavras ou número de um dígito; nenhum número novo de dois dígitos ou mais). Sem fato lateral, sem frase vaga sem dado ("pode acelerar", "diversas funções").
5. Exatamente um "twist" antes do fato mais surpreendente ou decisivo, entre o terceiro e o quinto fato (metade do vídeo, onde a atenção cai): uma frase que ANUNCIA especificamente esse fato (ex.: "E o número que muda essa conta é o preço por tela:"), nunca genérica.
6. "impact" (2 frases): o que isso muda na prática para quem assiste no Brasil (preço, uso, risco, decisão). Só consequência que decorra do material; sem "isso pode impactar" vazio.
7. "question" (1 frase): pergunta curta e direta para responder nos comentários.
8. "close" (1 frase): peça para salvar ou comentar e avise que hoje ainda tem mais vídeo (o canal posta 5 por dia; nunca diga "amanhã"); não peça para seguir de novo.

Regras:
- Total entre 200 e 260 palavras. Frases curtas, sem parênteses, sem siglas soletradas (escreva "TI", "inteligência artificial" na primeira vez).
- Todo número, valor, data e nome que você usar tem que estar no MATERIAL. Se o material não tem um dado, não invente: escreva sem ele.
- Não mencione o nome do site nem "segundo o portal". Não diga "matéria", "reportagem", "vídeo acima".
- Para cada trecho, "image": 2 a 4 palavras em inglês descrevendo um vídeo ou foto concreta que ilustre o trecho (ex.: "foldable phone hands", "samsung factory line", "oled panel closeup", "person paying phone"). Concreto, sem abstrações.
- "caption": descrição para o TikTok até 150 caracteres, sem hashtags. "hashtags": 8, sem #.

Responda no formato:
{"beats": [{"kind": "hook", "text": "...", "image": "..."}, ...], "caption": "...", "hashtags": ["..."]}
"""

ORDER_FIRST = ["hook", "follow", "context"]
ORDER_LAST = ["impact", "question", "close"]


def _numbers_in(text: str) -> set[str]:
    return {n.replace(".", "").replace(",", "") for n in re.findall(r"\d[\d.,]*", text) if len(re.sub(r"\D", "", n)) >= 2}


def _repair(data: dict) -> dict:
    """Conserta a estrutura quando da: virada no lugar errado, virada faltando,
    trechos de tipo desconhecido no miolo viram fato."""
    beats = data.get("beats")
    if not isinstance(beats, list):
        return data
    for b in beats:
        if isinstance(b, dict):
            b["kind"] = str(b.get("kind", "")).strip().lower()
    head = [b for b in beats if b.get("kind") in ORDER_FIRST]
    tail = [b for b in beats if b.get("kind") in ORDER_LAST]
    middle = [b for b in beats if b.get("kind") not in ORDER_FIRST + ORDER_LAST]
    for b in middle:
        if b.get("kind") not in ("fact", "twist"):
            b["kind"] = "fact"
    twists = [b for b in middle if b["kind"] == "twist"]
    facts = [b for b in middle if b["kind"] == "fact"]
    if len(twists) > 1:
        for b in twists[1:]:
            b["kind"] = "fact"
        twists = twists[:1]
    if not twists and len(facts) >= 2:
        twists = [{"kind": "twist", "text": "E o detalhe que muda essa história vem agora.", "image": facts[-1].get("image", "")}]
    # virada sempre antes do ultimo fato (ou do penultimo, se houver 5+), nunca no fim
    facts = [b for b in middle if b["kind"] == "fact"]
    pos = max(1, len(facts) - 1) if len(facts) < 5 else len(facts) - 2
    middle = facts[:pos] + twists + facts[pos:]
    head_sorted = sorted(head, key=lambda b: ORDER_FIRST.index(b["kind"]))
    tail_sorted = sorted(tail, key=lambda b: ORDER_LAST.index(b["kind"]))
    data["beats"] = head_sorted + middle + tail_sorted
    return data


def _validate_composed(data: dict, material: str) -> tuple[bool, str]:
    beats = data.get("beats")
    if not isinstance(beats, list) or len(beats) < 8:
        return False, "poucos trechos"
    kinds = [str(b.get("kind", "")) for b in beats]
    if kinds[:3] != ORDER_FIRST or kinds[-3:] != ORDER_LAST:
        return False, f"ordem errada: faltam ou sobram trechos de abertura/fechamento ({kinds})"
    middle = kinds[3:-3]
    if middle.count("fact") < 3:
        return False, f"menos de 3 fatos ({middle})"
    total = 0
    mat_nums = _numbers_in(material)
    for i, b in enumerate(beats):
        text = str(b.get("text", "")).strip()
        n = len(words(text))
        if n < 2:
            return False, f"trecho {i} vazio"
        if n > 45:
            return False, f"trecho {i} longo demais ({n} palavras)"
        total += n
        extra = _numbers_in(text) - mat_nums
        if extra:
            return False, f"numero fora do material no trecho {i}: {sorted(extra)[:3]}"
    if not 165 <= total <= 290:
        return False, f"total de palavras {total} fora da faixa 200-260 (escreva mais fatos do material, sem inventar)"
    return True, "ok"


def compose(topic, theme: str, log=None) -> Script | None:
    """A IA escreve o roteiro inteiro a partir do material da fonte (noticia,
    incidente, neste dia). Validado contra invencao. None se indisponivel ou reprovado."""
    if not ai.available():
        return None
    import json
    from ..util.text import short_title
    from datetime import date as _date
    material = (topic.extra.get("summary", "") + "\n" + "\n".join(topic.sentences[:24]))[:3200]
    material += f"\nData de hoje: {_date.today().isoformat()}. Publicado em: {topic.published or ''}."
    if topic.extra.get("event"):
        material = f"Evento: {topic.extra['event']}\nAno: {topic.year}\n" + material
    related = topic.extra.get("heat_sources") or []
    payload = {
        "titulo": topic.title, "fonte": topic.source_name, "tema": theme, "data_publicacao": topic.published,
        "outras_redacoes_que_cobriram": related[:6], "MATERIAL": material,
    }
    user = COMPOSE_RULES + "\n\n" + json.dumps(payload, ensure_ascii=False)
    data = ai.chat_json(COMPOSE_SYSTEM, user, max_tokens=1800, temperature=0.5, log=log)
    if not data:
        return None
    data = _repair(data)
    ok, why = _validate_composed(data, material + " " + topic.title)
    if not ok:
        if log:
            log(f"  roteiro por IA rejeitado ({why}); segunda tentativa")
        prev = json.dumps(data, ensure_ascii=False)[:2500]
        data = ai.chat_json(COMPOSE_SYSTEM, user + f"\n\nSUA RESPOSTA ANTERIOR:\n{prev}\n\nELA FOI REJEITADA PORQUE: {why}. Corrija mantendo o que estava bom e responda de novo.",
                            max_tokens=1800, temperature=0.3, log=log)
        data = _repair(data) if data else data
        ok, why = _validate_composed(data, material + " " + topic.title) if data else (False, "sem resposta")
        if not ok:
            if log:
                log(f"  roteiro por IA rejeitado de novo ({why}); usando template")
            return None
    from ..config import THEMES
    from .writer import _hashtags
    beats: list[Beat] = []
    mascot_for = {"hook": "surprised", "follow": "point_up", "twist": "serious", "impact": "explain", "question": "point_down", "close": "wave"}
    fact_i = 0
    for b in data["beats"]:
        kind, text = str(b["kind"]), str(b["text"]).strip()
        image = str(b.get("image", "") or "").strip()[:60].lower()
        punch = None
        if kind == "hook":
            punch = " ".join(punchline(text).split()[:4])
        elif kind in ("fact", "context"):
            punch = " ".join(punchline(text).split()[:4])
        elif kind == "follow":
            punch = "SEGUE O CANAL"
        elif kind == "impact":
            punch = "NA PRÁTICA"
        elif kind == "question":
            punch = "COMENTA"
        idx = None
        if kind in ("hook", "context", "fact"):
            idx = fact_i
            fact_i += 1
        beats.append(Beat(kind, text, punch, image or None, emphasis_words(text), None, None, idx, mascot_for.get(kind)))
    from ..util.text import strip_accents
    tags = [re.sub(r"[^\w]", "", strip_accents(str(t))).lower() for t in data.get("hashtags", [])][:8]
    tags = [t for t in tags if t] or _hashtags(topic, theme)
    cap = str(data.get("caption", "")).strip()[:150] or short_title(topic.title, 14)
    caption = cap + "\n\n" + " ".join("#" + t for t in tags)
    cta = "SEGUE O CANAL"
    if log:
        log(f"  roteiro por IA: ok ({sum(len(words(b.text)) for b in beats)} palavras, {len(beats)} trechos)")
    return Script(topic.id, theme, topic.title, beats, tags, caption, cta, 0, topic.source_name, topic.source_url)


def _numbers(text: str) -> set[str]:
    return {n.replace(".", "").replace(",", "") for n in re.findall(r"\d[\d.,]*", text)}


def _validate(script: Script, data: dict) -> tuple[bool, str]:
    beats = data.get("beats")
    if not isinstance(beats, list) or len(beats) != len(script.beats):
        return False, f"quantidade de trechos {len(beats) if isinstance(beats, list) else '?'} != {len(script.beats)}"
    total = 0
    need: set[str] = set()
    have: set[str] = set()
    for i, (b, nb) in enumerate(zip(script.beats, beats)):
        if not isinstance(nb, dict) or nb.get("kind") != b.kind:
            return False, f"kind diferente no trecho {i}"
        text = str(nb.get("text", "")).strip()
        if len(words(text)) < 2:
            return False, f"trecho {i} vazio"
        if len(words(text)) > 48:
            return False, f"trecho {i} longo demais"
        total += len(words(text))
        if b.kind in ("fact", "claim", "projection", "growth"):
            need |= _numbers(b.text)
        have |= _numbers(text)
    missing = {n for n in need - have if len(n) >= 2}  # numero de um digito pode virar extenso ("dois")
    if missing:
        return False, f"numeros perdidos: {sorted(missing)[:4]}"
    # piso de 170 palavras: abaixo disso o video fica com menos de 1 minuto e nao monetiza
    if not 170 <= total <= 290:
        return False, f"total de palavras {total} fora da faixa"
    return True, "ok"


def polish(script: Script, topic_summary: str, log=None) -> Script:
    """Devolve um Script revisado; em falha (sem chave, erro, validacao), o original."""
    if not ai.available():
        return script
    payload = {
        "tema": script.title, "material_de_apoio": topic_summary[:900],
        "trechos": [{"i": i, "kind": b.kind, "text": b.text} for i, b in enumerate(script.beats)],
    }
    import json
    user = RULES + "\n\nROTEIRO:\n" + json.dumps(payload, ensure_ascii=False)
    data = ai.chat_json(SYSTEM, user, max_tokens=1600, temperature=0.4, log=log)
    if not data:
        if log:
            log("  revisao por IA indisponivel; roteiro do template")
        return script
    ok, why = _validate(script, data)
    if not ok:
        if log:
            log(f"  revisao por IA rejeitada ({why}); tentando de novo com aviso")
        user2 = user + f"\n\nATENÇÃO: a resposta anterior foi rejeitada porque: {why}. Corrija e responda de novo."
        if "total de palavras" in why:
            got = sum(len(words(str(nb.get("text", "")))) for nb in data.get("beats", []))
            if got < 170:
                user2 += (f" Você escreveu {got} palavras e o mínimo é 170 (abaixo disso o vídeo não passa de 1 minuto)."
                          " Acrescente cerca de " + str(190 - got) + " palavras com detalhes concretos do material_de_apoio que"
                          " ainda não usou (nome, lugar, data, como aconteceu, quem estava). Proibido frase vazia de enchimento"
                          " (\"isso foi crucial\", \"se tornou um marco\", \"preservando essa história\").")
        data = ai.chat_json(SYSTEM, user2, max_tokens=1600, temperature=0.3, log=log)
        ok, why = _validate(script, data) if data else (False, "sem resposta")
        if not ok:
            if log:
                log(f"  revisao por IA rejeitada de novo ({why}); roteiro do template")
            return script
    new_beats: list[Beat] = []
    for b, nb in zip(script.beats, data["beats"]):
        text = str(nb["text"]).strip()
        image = str(nb.get("image", "") or "").strip()[:60].lower()
        punch = b.punchline
        if b.kind in ("fact", "claim", "context") and punch is not None:
            punch = " ".join(punchline(text).split()[:4])
        new_beats.append(Beat(b.kind, text, punch, image or b.image_query, emphasis_words(text), b.chart, b.stat, b.image_index, b.mascot))
    from ..util.text import strip_accents
    tags = [re.sub(r"[^\w]", "", strip_accents(str(t))).lower() for t in data.get("hashtags", [])][:8]
    tags = [t for t in tags if t] or script.hashtags
    cap = str(data.get("caption", "")).strip()[:150] or script.caption.split("\n")[0]
    caption = cap + "\n\n" + " ".join("#" + t for t in tags)
    if log:
        log(f"  revisao por IA: ok ({sum(len(words(b.text)) for b in new_beats)} palavras)")
    return Script(script.topic_id, script.theme, script.title, new_beats, tags, caption, script.cta_screen, script.seed,
                  script.source_name, script.source_url)
