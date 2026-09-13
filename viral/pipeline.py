"""Linha de producao de um video: tema -> roteiro -> voz -> imagens -> storyboard -> audio -> render -> QA."""
from __future__ import annotations

import json
import random
import zlib
from datetime import date
from pathlib import Path

import numpy as np

from . import qa
from .audio import mix, synth
from .audio import library
from .config import ASSETS, MUSIC_STYLES, OUTPUT, QUALITY, SR, THEMES, VOICE, VOICE_PITCH, VOICE_RATE, VOICE_SPEED
from .director import Director
from .planner import mark_used
from .render.engine import Engine
from .script import polish, writer
from .sources import rss
from .topic import Topic
from .util.text import slugify, to_speech
from .visuals import images
from .voice import tts


class LowQuality(Exception):
    """O melhor roteiro/edicao do tema nao atingiu a nota minima: pular para o proximo tema."""


def load_user_music(style: str, seconds: float, seed: int) -> np.ndarray | None:
    """assets/music/<estilo>/*.mp3|wav|m4a|ogg: musica sua (livre de direitos) passa na frente da sintese."""
    folder = ASSETS / "music" / style
    if not folder.exists():
        return None
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac"))
    if not files:
        return None
    pick = random.Random(seed).choice(files)
    try:
        x = mix.decode_audio(pick)
    except Exception:
        return None
    if len(x) < SR * 5:
        return None
    if len(x) < int(seconds * SR):
        x = np.tile(x, int(np.ceil(seconds * SR / len(x))))
    start = random.Random(seed + 1).randrange(0, max(1, len(x) - int(seconds * SR)))
    return synth.normalize(x[start:start + int(seconds * SR)], 0.85)


MUSIC_CREDIT: dict = {}   # faixa do catalogo usada no ultimo video (titulo e credito para o .txt)


def _library_music(style: str, dur: float, seed: int) -> np.ndarray | None:
    """Jazz do catalogo (viral/audio/library.py): tenta a categoria, depois a vizinha; sem rede, None."""
    neighbor = library.NEIGHBOR.get(library.category(style))
    order = library.ranked(style, seed) + (library.ranked(neighbor, seed) if neighbor else [])
    for track in order[:6]:
        path = library.fetch(track)
        if path is None:
            continue
        try:
            x = mix.decode_audio(path, None, dur + 2.0, channels=2)
        except Exception:
            continue
        if len(x) < SR * 20:
            continue
        if len(x) < int(dur * SR):
            x = np.tile(x, (int(np.ceil(dur * SR / len(x))), 1))
        library.record(style, track)
        MUSIC_CREDIT.update(title=track["title"], artist=track.get("artist"), license=track.get("license"),
                            page_url=track.get("page_url"), credit=library.credit(track))
        return synth.normalize(x[:int(dur * SR)], 0.85)
    return None


def _pick_music(style: str, dur: float, seed: int, topic_id: str, hits: list[float] | None = None,
                bpm: float | None = None, root: float | None = None) -> np.ndarray:
    """Musica do usuario ou trilha sintetica. `x or y` com ndarray levanta ValueError, por isso o teste explicito
    de None; a semente usa crc32 (hash() muda a cada processo com PYTHONHASHSEED)."""
    MUSIC_CREDIT.clear()
    music = load_user_music(style, dur, seed)
    if music is None:
        music = _library_music(style, dur, seed)
    if music is None:
        spec = MUSIC_STYLES.get(style, MUSIC_STYLES["lofi"])
        music = synth.music(style, dur, spec["bpm"] if bpm is None else bpm, spec["root"] if root is None else root,
                            seed=zlib.crc32(topic_id.encode()) % 10000 + seed, hits=hits)
    return music


def _visuals_for(script: writer.Script, topic: Topic, timeline: list[dict], W: int, H: int, log=print) -> dict[int, list[dict]]:
    """Imagens e clipes por trecho, sem voltar para a mesma foto logo em seguida.

    Noticia: foto da materia (e das outras redacoes) alternando com clipe de b-roll
    pela consulta em ingles do roteiro; nome proprio com pagina certa; contexto
    leva o cartao de manchete. Curiosidade/historia/teoria: consultas curadas e
    da IA, clipes quando nao ha foto nova. Sem material bom, o trecho vira cartao."""
    from .visuals import stock
    visuals: dict[int, list[dict]] = {}
    news = topic.theme in ("tech_news", "story")
    picture_beats = [i for i, b in enumerate(script.beats) if b.kind in ("hook", "context", "fact", "claim")]
    needs = {i: (2 if timeline[i]["end"] - timeline[i]["start"] > 4.2 else 1) for i in picture_beats}
    cache: dict[str, list[dict]] = {}
    clip_cache: dict[str, list[dict]] = {}
    lookups = 0
    clip_lookups = 0

    def lookup(q: str, strict: bool, need: int = 1) -> list[dict]:
        nonlocal lookups
        if not q:
            return []
        key = q + ("|estrita" if strict else "|solta")
        if key not in cache and lookups < 18:
            cache[key] = images.query_visuals(q, strict, need=need, log=log, max_year=(era + 40) if era else None)
            lookups += 1
        return cache.get(key, [])

    def clips(q: str) -> list[dict]:
        nonlocal clip_lookups
        if not q:
            return []
        if q not in clip_cache and clip_lookups < 7:
            clip_cache[q] = stock.find_clips(q, W, H, need=1, log=log)
            clip_lookups += 1
        return clip_cache.get(q, [])

    # historia antiga (ano antes de 1995 no roteiro): banco de video generico e anacronico, entao sem clipe
    import re as _re
    years = [int(y) for y in _re.findall(r"\b(1[5-9]\d\d)\b", " ".join(b.text for b in script.beats) + " " + topic.title)]
    era = max(years) if (years and not news) else None

    def names_in(text: str) -> set[str]:
        """Palavras de nome proprio no texto (maiuscula fora do inicio de frase)."""
        out: set[str] = set()
        for sent in _re.split(r"[.!?:]\s+", text):
            toks = _re.findall(r"[\w']+", sent)
            out |= {t.lower() for t in toks[1:] if t[:1].isupper() and len(t) > 2}
        return out

    topic_names = {w.lower() for e in (topic.entities or []) for w in _re.findall(r"\w{3,}", e)}

    ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V"}
    # pais e continente: a pagina da Wikipedia mostra bandeira ou mapa, nao o fato
    PLACES = {"Estados Unidos", "Brasil", "Europa", "China", "Japão", "Rússia", "Índia", "Alemanha", "Inglaterra",
              "França", "Coreia", "Coreia do Sul", "Canadá", "México", "Argentina", "Portugal", "Reino Unido", "América"}

    def spoken_names(text: str) -> list[str]:
        """Nomes proprios ditos no trecho (\"Grace Hopper\", \"Harvard Mark 2\"): a pagina da Wikipedia
        deles e a imagem mais fiel ao que a narracao fala naquele momento."""
        out: list[str] = []
        for sent in _re.split(r"[.!?:]\s+", text):
            toks = _re.findall(r"[\w']+", sent)
            run: list[str] = []
            nxt = toks[1:] + ["", ""]
            for j, t in enumerate(toks + [""]):
                if (j > 0 and t[:1].isupper()) or (run and t in ROMAN) \
                        or (run and t in ("de", "do", "da", "dos", "das") and nxt[j][:1].isupper()):
                    run.append(t)
                    continue
                if run and (len(run) > 1 or len(run[0]) > 5) and " ".join(run) not in PLACES:
                    out.append(" ".join(run))
                run = []
        return out

    def name_hits(i: int) -> list[tuple[float, list[dict]]]:
        """(posicao do nome na frase de 0 a 1, fotos do nome) para cada nome falado no trecho."""
        text = script.beats[i].text
        hits: list[tuple[float, list[dict]]] = []
        for n in spoken_names(text)[:2]:
            parts = n.split()
            tries = [" ".join(parts[:-1] + [ROMAN[parts[-1]]]), n] if parts[-1] in ROMAN else [n]
            for t in tries:
                r = lookup(t, True)
                if r:
                    hits.append((max(text.find(n), 0) / max(len(text), 1), r))
                    break
        return hits

    def name_photos(i: int) -> list[dict]:
        return [v for _, r in name_hits(i) for v in r]

    ROMAN_BACK = {v.lower(): k for k, v in ROMAN.items()}

    def norm_name(n: str) -> str:
        return " ".join(ROMAN_BACK.get(t, t) for t in n.lower().split())

    all_names = {norm_name(n) for bb in script.beats for n in spoken_names(bb.text)} | {norm_name(e) for e in (topic.entities or [])}

    story: list[dict] = []
    story_ready = False

    def story_photos() -> list[dict]:
        """Reserva: fotos de tudo que a historia cita pelo nome (pessoa, maquina, lugar). Trecho sem imagem
        propria mostra uma delas (no maximo 2 vezes cada) em vez de foto generica fora de contexto."""
        nonlocal story, story_ready
        if not story_ready:
            story_ready = True
            names: list[str] = list((topic.entities or [])[:3])
            for j in range(len(script.beats)):
                for n in spoken_names(script.beats[j].text)[:2]:
                    parts = n.split()
                    names.append(" ".join(parts[:-1] + [ROMAN[parts[-1]]]) if parts[-1] in ROMAN else n)
            got: list[dict] = []
            seen_names: set[str] = set()
            for n in names:
                if n.lower() in seen_names:
                    continue
                seen_names.add(n.lower())
                got += lookup(n, True)
                if len(n.split()) > 1:
                    # nome composto no Commons ("Grace Hopper"): o filtro exige as palavras no titulo do arquivo
                    got += lookup(n, False, 3)
            story = images.dedupe(got)
        return story

    def clip_ok(i: int, q: str) -> bool:
        """Clipe de banco so para assunto generico: nunca pessoa, maquina ou evento com nome, nunca historia antiga."""
        if not q or era:
            return False
        if news:
            return True   # noticia: o filtro de descricao do Pexels ja barra nome que o video nao cita
        qt = set(_re.findall(r"[a-z]{3,}", q.lower()))
        return not (qt & (names_in(script.beats[i].text) | topic_names))

    shown: list[str] = []
    canon: dict[str, str] = {}
    known: list[tuple[str, int]] = []

    def K(v: dict) -> str:
        """Mesma foto em arquivos diferentes (Wikipedia e Commons) conta como uma so."""
        path = v["path"]
        if v.get("type") == "clip":
            return path
        if path not in canon:
            try:
                h = v.get("hash")
                if h is None:
                    h = v["hash"] = images.phash(Path(path))
            except Exception:
                canon[path] = path
                return path
            canon[path] = next((q for q, qh in known if images._hamming(h, qh) <= 5), path)
            if canon[path] == path:
                known.append((path, h))
        return canon[path]

    def fresh(cands: list[dict], cont: bool = False, own_paths: frozenset = frozenset(),
              moment: frozenset = frozenset()) -> dict | None:
        """Escolhe o plano. Ordem: o que a frase cita > continuidade do trecho anterior > foto nova > reuso.
        Reuso no maximo 2 vezes por foto e nunca as duas ultimas mostradas.
        Sem candidata boa, devolve None: o trecho vira cartao, que e melhor que repetir."""
        def reuse_ok(v: dict) -> bool:
            return False   # nenhuma imagem repete no video
        for v in cands:
            # foto do nome falado exatamente neste plano: vale mesmo ja vista uma vez, so nao repete o plano anterior
            if v["path"] in moment and K(v) not in shown:
                return v
        for v in cands:
            if v["path"] in own_paths and (K(v) not in shown or reuse_ok(v)):
                return v
        for v in cands:
            if K(v) not in shown:
                return v
        old = [v for v in cands if reuse_ok(v)]
        return old[0] if old else None

    pool: list[dict] = []
    if news:
        # Fotos livres: Pexels (com chave) + Wikipedia/Commons pelas entidades do artigo.
        # Nao baixamos imagens de redacoes (G1, TechCrunch etc.) -- copyright.
        from .visuals import stock as _stock
        for e in topic.entities[:4]:
            if len(pool) >= 6:
                break
            # Pexels primeiro: retorna fotos com licenca de uso livre
            pexels = _stock.find_photos(e, need=2, log=log)
            # Wikipedia/Commons como complemento
            wiki = lookup(e, True, need=2)
            pool = images.dedupe(pool + pexels + wiki)
        if not pool:
            # fallback: consulta pelo assunto principal
            q_fb = topic.subject or topic.title
            pexels = _stock.find_photos(q_fb, need=3, log=log)
            pool = images.dedupe(pexels)
    own: list[dict] = []
    for u in (topic.image_urls[:4] if not news else []):
        pth = images.download(u)
        if pth:
            own.append(images.make_visual(pth, "Wikipedia", "evento"))
    own = images.dedupe(own)

    want_clip = False
    last_pic = -9
    for i in picture_beats:
        b = script.beats[i]
        q = b.image_query or ""
        if not news and b.kind == "context" and q and norm_name(q) in all_names \
                and norm_name(q) not in {norm_name(n) for n in spoken_names(b.text)}:
            q = ""   # contexto nao mostra pessoa ou maquina que a frase ainda nao citou
        english = bool(q) and q == q.lower() and " " in q.strip() and q.isascii() and clip_ok(i, q)   # consulta da IA (minuscula, ingles) sobre assunto generico: boa para b-roll
        chosen: list[dict] = []
        own_paths: frozenset = frozenset()
        hits = name_hits(i) if not news else []
        if not news:
            own_paths = frozenset(x["path"] for x in name_photos(i) + (lookup(q, False, needs[i]) if q else []))
        for k in range(needs[i]):
            moment = frozenset(v["path"] for f, r in hits if min(int(f * needs[i]), needs[i] - 1) == k for v in r)
            order: list[list[dict]] = []
            if news:
                photos = list(pool)
                if b.kind == "hook":
                    order = [photos, clips(q) if english else []]
                elif b.kind == "context":
                    order = [photos]
                else:
                    cl = clips(q) if english else []
                    order = [cl, photos] if want_clip else [photos, cl]
                    if not fresh(photos) and not english:
                        order.append(lookup(q, True))
            else:
                # primeiro o que a narracao cita pelo nome, depois a consulta da IA, depois fotos do tema
                # contexto vem antes da historia: nao gasta as fotos dela
                if b.kind == "context":
                    named = name_photos(i)
                    ban = [x for x in story_photos() if all(x["path"] != y["path"] for y in named)]
                    ban_paths = {x["path"] for x in ban}
                    # a foto da historia entra primeiro so para o dedupe derrubar as copias dela; depois sai
                    kept = images.dedupe(named + ban + (lookup(q, False, needs[i]) if q else []) + own)
                    photos = [x for x in kept if x["path"] not in ban_paths]
                else:
                    photos = images.dedupe(name_photos(i) + (lookup(q, False, needs[i]) if q else []) + own + story_photos())
                cl = clips(q) if english else []
                order = [cl, photos] if want_clip else [photos, cl]
            v = None
            same = chosen[0].get("type") == "clip" if chosen else None
            for cands in order:
                # dentro do mesmo trecho nao mistura foto e clipe: a segunda imagem e do mesmo tipo da primeira
                cands = [c for c in cands if all(c["path"] != x["path"] for x in chosen) and (same is None or (c.get("type") == "clip") == same)]
                v = fresh(cands, cont=(last_pic == i - 1 and not chosen), own_paths=own_paths, moment=moment)
                if v:
                    break
            if not v:
                break
            chosen.append(v)
            shown.append(K(v))
            want_clip = v.get("type") != "clip"   # alterna foto e clipe entre trechos, nunca dentro de um
        if chosen:
            visuals[i] = chosen
            last_pic = i

    # b-roll por palavra: cada trecho ganha uma imagem nova a cada ~2,4 s, ligada a palavra dita naquele instante
    import math as _math
    from .visuals import broll, stock as _stk
    roll_beats = [i for i, bb in enumerate(script.beats) if bb.kind in ("hook", "context", "fact", "claim", "impact")]
    shots = broll.plan(script, timeline, roll_beats, log)
    budget = {"photo": 20, "clip": 8}

    def name_word(i: int, v: dict) -> str | None:
        """Palavra em que a foto de um nome falado entra (o proprio nome)."""
        for n in spoken_names(script.beats[i].text)[:2]:
            parts = n.split()
            tries = [" ".join(parts[:-1] + [ROMAN[parts[-1]]]), n] if parts[-1] in ROMAN else [n]
            if any(x["path"] == v["path"] for t in tries for x in cache.get(t + "|estrita", [])):
                return parts[0]
        return None

    spoken_tokens = {t for j in roll_beats for t in broll.tokens(script.beats[j].text)}

    def name_of(v: dict) -> tuple[str, str] | None:
        """Foto buscada por nome proprio que a voz fala em algum trecho: (primeira palavra normalizada, como no nome)."""
        q = str(v.get("query") or "").strip()
        if not q[:1].isupper():
            return None
        first = q.split()[0]
        key = broll.tokens(first)[:1]
        return (key[0], first) if key and len(key[0]) >= 4 and key[0] in spoken_tokens else None

    names_pool: dict[str, list[dict]] = {}
    for j in sorted(visuals):
        for v in visuals[j]:
            nm = name_of(v)
            if nm:
                names_pool.setdefault(nm[0], []).append(v)

    for i in roll_beats:
        text = script.beats[i].text
        slots = max(1, _math.ceil((timeline[i]["end"] - timeline[i]["start"]) / broll.SHOT_SECONDS))
        have = []
        toks_i = broll.tokens(text)
        for v in visuals.get(i, []):
            nm = name_of(v)
            if nm and nm[0] not in toks_i:
                if K(v) in shown:   # foto de nome falado em outro trecho: sai daqui e entra la
                    shown.remove(K(v))
                continue
            w = v.get("word") or (nm[1] if nm else name_word(i, v))
            have.append(dict(v, word=w, idx=broll.word_index(text, w) if w else None))
        for key, items in names_pool.items():
            if key not in toks_i or any(broll.tokens(str(h.get("word") or ""))[:1] == [key] for h in have):
                continue
            got = next((x for x in items if K(x) not in shown), None)
            if got:
                w = name_of(got)[1]
                shown.append(K(got))
                have.append(dict(got, word=w, idx=broll.word_index(text, w)))
        spares: list[dict] = []
        for sh in shots.get(i, []):
            if len(have) >= slots:
                break
            if any(h["idx"] is not None and abs(h["idx"] - sh["idx"]) < 4 for h in have):
                continue   # mesma frase de uma imagem que ja entra ali
            acc = (lambda alts, q=sh["query"], t=text: broll.judge(t, q, alts, log))
            got = None
            for kind in (["clip", "photo"] if sh["type"] == "clip" else ["photo", "clip"]):
                if budget[kind] <= 0:
                    continue
                budget[kind] -= 1
                cands = (_stk.find_broll_clip(sh["query"], W, H, log=log, accept=acc) if kind == "clip"
                         else _stk.find_photos(sh["query"], need=2, log=log, accept=acc))
                fresh = [c for c in cands if K(c) not in shown]
                if fresh:
                    got = fresh[0]
                    spares += [dict(c, query=sh["query"]) for c in fresh[1:]]
                    break
            if got:
                shown.append(K(got))
                have.append(dict(got, word=sh["word"], idx=sh["idx"]))
        # imagem sem palavra abre o trecho; as outras entram na ordem em que a voz fala
        have.sort(key=lambda h: -1 if h["idx"] is None else h["idx"])
        for sp in spares:   # reserva: entra no lugar de reenquadrar a mesma foto quando um plano passaria de 3 s
            if len(have) < slots + 2 and K(sp) not in shown:
                shown.append(K(sp))
                have.append(dict(sp, word=None, idx=None, spare=True))
        if have:
            visuals[i] = have
            log(f"    trecho {i}: " + " | ".join(f"{h.get('word') or ('reserva' if h.get('spare') else '-')}="
                                             f"{str(h.get('query') or h.get('credit', ''))[:24]}" for h in have))
    return visuals


def produce(topic: Topic, quality: str = "poc", seed: int = 0, voice: str = VOICE, rate: str = VOICE_RATE,
            out_dir: Path | None = None, index: int = 1, engine: str = "auto", log=print, no_images: bool = False,
            render: bool = True, best_of: int = 3, min_qa: int = 70, narration: str | None = None, with_mascot: bool = True) -> dict:
    q = QUALITY[quality]
    W, H, fps = q["width"], q["height"], q["fps"]
    out_dir = out_dir or (OUTPUT / date.today().isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{index:02d}-{topic.theme}-{slugify(topic.title, 40)}"
    log(f"[{stem}]")

    if topic.theme in ("tech_news", "story"):
        rss.enrich(topic, log)

    # 1. roteiro. Noticia/incidente/neste dia: a IA escreve a partir da materia inteira (validado);
    #    demais temas (material curado): template + revisao por IA. Sem chave: template.
    script = None
    if topic.theme in ("tech_news", "story", "history") and not narration and len(" ".join(topic.sentences)) > 300:
        script = polish.compose(topic, topic.theme, log)
        if script is not None:
            script.seed = seed
    if script is None:
        best_script, best_text = None, -1
        for sd in range(seed, seed + max(1, best_of if not narration else 1)):
            cand = writer.write(topic, sd)
            tq = qa.text_score(cand)
            log(f"  variacao {sd}: texto {tq} ({cand.word_count} palavras)")
            if tq > best_text:
                best_script, best_text = cand, tq
        script = best_script
        seed = script.seed
        if best_text < 60 and not narration:
            raise LowQuality(f"texto fraco (nota {best_text}): poucos fatos na fonte; tema pulado antes de gastar voz")
        if not narration:
            summary = topic.extra.get("summary", "") + " " + " ".join(topic.sentences[:8])
            script = polish.polish(script, summary, log)
    script = writer.say_year_once(script)
    for bt in script.beats:
        if bt.kind == "close":   # canal posta 5 por dia: nunca "amanha tem mais"
            bt.text = bt.text.replace("amanhã tem mais", "hoje ainda tem mais").replace("Amanhã tem mais", "Hoje ainda tem mais")
    from .script import zoeira
    script = writer.say_year_once(zoeira.apply(script, log))
    log(f"  roteiro (semente {seed}): {len(script.beats)} trechos, {script.word_count} palavras")
    for bt in script.beats:
        log(f"    {bt.kind:10s} {bt.text}")
    # 3. narracao continua com tempo por palavra (OpenAI se houver chave; ou a sua gravacao)
    if narration:
        nar = tts.narrate_from_file(narration, [zoeira.plain(bt.text) for bt in script.beats], log)
    else:
        rate_eff = THEMES[topic.theme].get("rate", rate) if rate == VOICE_RATE else rate
        speed_eff = THEMES[topic.theme].get("speed", VOICE_SPEED)  # so a voz OpenAI usa
        # trechos viram um texto so, emendados por espaco e pontuacao (nunca paragrafo: vira pausa longa)
        nar = tts.narrate([to_speech(zoeira.plain(bt.text)) for bt in script.beats], voice, rate_eff, VOICE_PITCH, engine, log, speed=speed_eff)
    timeline = nar.timeline
    bleeps = zoeira.censor_timeline(script, timeline)   # legenda "cara***"; o piii entra no audio
    log(f"  narracao: {timeline[-1]['end']:.1f}s ({nar.engine})")
    # 4. imagens
    visuals = {} if no_images else _visuals_for(script, topic, timeline, W, H, log)
    log(f"  imagens: {sum(len(v) for v in visuals.values())} em {len(visuals)} trechos")
    # 5. storyboard
    sb = Director(script, timeline, visuals, seed, with_mascot).build()
    log(f"  storyboard: {len(sb.scenes)} planos, {len(sb.captions)} legendas, {len(sb.sfx)} efeitos, {len(sb.mascots)} entradas do personagem")
    # 6. trilha: clima pelo conteudo e instante da virada
    from .audio.mood import mood_for
    m = mood_for(topic.theme, topic.title + " " + script.narration_text(), THEMES[topic.theme]["music"])
    # instante da virada: a trilha poe riser antes e impacto em cima
    hits = [timeline[i]["start"] for i, b in enumerate(script.beats) if b.kind == "twist" and i < len(timeline)]
    log(f"  trilha: {m['style']} {m['bpm']} bpm" + (f", virada aos {hits[0]:.1f}s" if hits else ""))
    # 7. QA
    report = qa.evaluate(script, timeline, sb, visuals)
    for line in report.lines():
        log("  " + line)
    if report.score < min_qa:
        raise LowQuality(f"QA {report.score} < {min_qa}: " + "; ".join(report.warnings[:3]))

    meta = {
        "file": f"{stem}.mp4", "theme": topic.theme, "title": topic.title, "duration": round(sb.duration, 1),
        "caption": script.caption, "hashtags": script.hashtags, "source": {"name": topic.source_name, "url": topic.source_url},
        "image_credits": sorted({v.get("credit", "") for vs in visuals.values() for v in vs if v.get("credit")}),
        "script": script.to_dict(), "timeline": timeline, "quality": quality, "seed": seed, "voice": voice, "music": m,
        "qa": {"score": report.score, "warnings": report.warnings, "metrics": report.metrics},
        "heat": topic.extra.get("heat"), "heat_sources": topic.extra.get("heat_sources"), "score": topic.score,
        # fotos para a capa: as do gancho primeiro (clipe nao serve de fundo parado)
        "cover_images": list(dict.fromkeys(v["path"] for i in sorted(visuals) for v in visuals[i]
                                           if v.get("path") and v.get("type") != "clip" and not v.get("spare"))),
    }
    (out_dir / f"{stem}.roteiro.txt").write_text(
        "ROTEIRO PARA GRAVAR (leia na ordem, pausa de meio segundo entre os trechos)\n\n" + script.narration_text() + "\n", "utf-8")
    if not render:
        (out_dir / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
        return meta

    # 8. audio
    music = _pick_music(m["style"], sb.duration, seed, topic.id, hits, m["bpm"], m["root"])
    voice_track = mix.bleep(mix.process_voice(nar.audio), bleeps)
    mx = mix.Mixer(sb.duration)
    mx.add(voice_track, 0.0, 1.0)
    m["track"] = dict(MUSIC_CREDIT)
    mx.add_music(music, voice_track)   # niveis de config (MUSIC_GAIN / MUSIC_DUCK), medidos na voz
    for name, t, gain in sb.sfx:
        mx.add_sfx(name, t, gain * 0.5, seed=int(t * 100))
    wav = out_dir / f"{stem}.wav"
    mix.write_wav(wav, mx.render())

    # 9. render
    mp4 = out_dir / f"{stem}.mp4"
    Engine(sb, W, H, fps, voice=voice_track).render(wav, mp4, q["crf"], q["preset"], contact=True, log=log)
    wav.unlink(missing_ok=True)

    (out_dir / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    credits = meta["image_credits"]
    (out_dir / f"{stem}.txt").write_text(script.caption + "\n\nFonte: " + (topic.source_url or topic.source_name) + ("\nImagens: " + "; ".join(credits) if credits else "")
        + ("\nMúsica: " + MUSIC_CREDIT["credit"] if MUSIC_CREDIT.get("credit") else ""), "utf-8")
    try:   # capa e textos por rede; falha aqui nao perde o video
        from .publish import kit
        kit.build(mp4, meta, log=log)
    except Exception as e:
        log(f"  kit: falhou ({type(e).__name__}: {e}); rode python make.py kit")
    mark_used(topic.id, topic.title, str(mp4))
    return meta
