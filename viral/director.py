"""Diretor de edicao: roteiro + tempos da voz + imagens -> storyboard.

Regras (o "editor" que nao depende de IA):
- foto quando ha foto da materia ou nome proprio com pagina certa; comentario
  (segue o canal, virada, na pratica, pergunta, fechamento) vira cartao de
  texto grande no fundo do tema: foto -> cartao -> foto da ritmo;
- corte a cada ~4,5 s no maximo; quando a mesma foto continua, o proximo plano
  e um reenquadramento (mais fechado no ponto focal), como um editor faria;
- gancho: carimbo/pop + impacto + tremor; virada: mergulho a preto + riser +
  flash + zoom; numero: contagem;
- personagem entra nos trechos marcados (gancho, segue, virada, na pratica,
  pergunta, fechamento), com expressao e pose por trecho, em segmentos
  continuos para nao ficar entrando e saindo;
- seta ou circulo apenas quando o ponto focal da foto e confiavel;
- efeito sonoro so onde marca algo, em volume baixo;
- legenda karaoke 3 palavras por linha, quebra na pausa da fala, sem pontuacao.
"""
from __future__ import annotations

import math
import random
import re

from .config import MAX_SCENE_SECONDS, THEMES
from .render.shapes import chart_point_position
from .script.writer import Script
from .storyboard import CaptionLine, Overlay, Scene, Storyboard
from .util.text import BIG_ENTITIES, strip_accents

TRANSITION_DUR = {"crossfade": 0.5, "slide_left": 0.34, "slide_up": 0.34, "zoom_in": 0.4, "whip": 0.3, "glitch": 0.26, "flash": 0.3, "wipe": 0.42, "cut": 0.0}
TRANSITION_SFX = {"whip": ("whoosh", 0.4), "glitch": ("glitch", 0.35), "flash": ("flash", 0.3), "slide_left": ("whoosh_short", 0.28), "slide_up": ("whoosh_short", 0.28), "wipe": ("whoosh_short", 0.25), "zoom_in": ("swipe", 0.22)}
HOOK_STYLE = {"tech_news": "pop", "story": "glitch", "curiosity": "stamp", "history": "stamp", "prediction": "pop", "theory": "pop"}
CARD_KINDS = {"twist", "impact", "question", "close", "growth", "caveat", "verdict"}   # follow fica sobre a imagem: e um aparte, nao um cartao
MASCOT = {
    "surprised": ("surprised", "hands_cheeks"), "point_up": ("explain", "point_right"), "serious": ("serious", "arms_crossed"),
    "explain": ("explain", "explain"), "point_down": ("happy", "point_down"), "wave": ("happy", "wave"), "neutral": ("neutral", "idle"),
}


def pose_for(kind: str, text: str, default: tuple[str, str]) -> tuple[str, str]:
    """Expressao e pose pelo conteudo do trecho, como um apresentador reagiria."""
    low = strip_accents(text.lower())
    if kind == "hook":
        return ("surprised", "hands_cheeks") if re.search(r"\d|!|\bnunca\b|\bninguem\b|\bassust", low) else ("explain", "point_up")
    if kind == "follow":
        return ("happy", "point_right")
    if kind == "close":
        return ("happy", "wave")
    if kind == "question":
        return ("thinking", "shrug") if "?" in text else ("happy", "point_down")
    if kind == "twist":
        return ("thinking", "thinking") if re.search(r"detalhe|mas |porem|so que", low) else ("serious", "arms_crossed")
    if kind in ("impact", "verdict"):
        if re.search(r"\bnao\b.*(evidencia|base|verdade|sustenta)|\bmito\b|\bfalso\b", low):
            return ("serious", "arms_crossed")
        if re.search(r"\bverdade\b|\bconfirm|\bfato\b|\bfunciona\b", low):
            return ("happy", "thumbs_up")
        return ("explain", "explain")
    if kind in ("fact", "claim", "growth", "projection"):
        if re.search(r"\d", text):
            return ("explain", "point_up")
        return ("neutral", "explain")
    return default


def _norm(w: str) -> str:
    return strip_accents(re.sub(r"[^\w%]", "", w).lower())


def _clean(w: str) -> str:
    return w.strip().strip(".,;:!?\"'“”…")


def caption_lines(words: list[tuple[str, float, float]], emphasis: list[str], max_words: int = 3, max_chars: int = 20) -> list[CaptionLine]:
    lines: list[CaptionLine] = []
    cur: list[tuple[str, float, float]] = []
    emph = tuple(_norm(e) for e in emphasis)

    def flush():
        if cur:
            lines.append(CaptionLine(cur[0][1] - 0.03, cur[-1][2] + 0.4, list(cur), emph))
            cur.clear()

    expanded: list[tuple[str, float, float]] = []
    for w, s, e in words:
        parts = [p for p in w.split() if _clean(p)]
        if len(parts) <= 1:
            expanded.append((w, s, e))
            continue
        total = sum(len(p) for p in parts)
        t = s
        for p in parts:
            d = (e - s) * len(p) / max(total, 1)
            expanded.append((p, t, t + d))
            t += d
    for w, s, e in expanded:
        txt = _clean(w)
        if not txt:
            continue
        chars = sum(len(x[0]) for x in cur) + len(cur) + len(txt)
        pause = cur and (s - cur[-1][2]) > 0.26
        if cur and (len(cur) >= max_words or chars > max_chars or pause):
            flush()
        cur.append((txt, s, e))
    flush()
    for i in range(len(lines) - 1):
        lines[i].end = min(lines[i].end, lines[i + 1].start)
    return lines


MIN_SHOT = 1.8      # plano mais curto que isso nao da tempo de entender a imagem
PHRASE_SNAP = 1.6   # o corte procura o comeco de frase ate essa distancia (antes da palavra vale mais que depois)


def _phrase_starts(words: list[tuple[str, float, float]]) -> list[float]:
    """Onde comeca frase ou oracao: primeira palavra, ou palavra depois de pontuacao. O corte entra na pausa,
    um pouco antes da voz."""
    out = []
    for j, (_, ws, _) in enumerate(words):
        if j == 0:
            out.append(ws - 0.15)
        elif re.search(r"[,.:;!?]\W*$", words[j - 1][0]):
            out.append(max(min(words[j - 1][2], ws - 0.05), ws - 0.15))
    return out


def _split_points(start: float, end: float, n: int, words: list[tuple[str, float, float]]) -> list[float]:
    """Divide [start, end] em n planos, cortando de preferencia em comeco de frase (senao, em comeco de palavra)."""
    phrases = _phrase_starts(words)
    top = MAX_SCENE_SECONDS + 0.4
    cuts: list[float] = []
    for k in range(1, n):
        ideal = start + (end - start) * k / n
        prev = cuts[-1] if cuts else start
        lo, hi = prev + MIN_SHOT, end - MIN_SHOT
        ok = [t for t in phrases if lo <= t <= hi and abs(t - ideal) <= 1.0
              and t - prev <= top and end - t <= top * (n - k)]
        if ok:
            cuts.append(min(ok, key=lambda t: abs(t - ideal)))
            continue
        best, best_d = ideal, 0.6
        for _, ws, _ in words:
            d = abs(ws - ideal)
            if d < best_d and lo <= ws - 0.05 <= hi:
                best, best_d = ws - 0.05, d
        cuts.append(best)
    return cuts


def _word_pos(idx: int | None, words: list[tuple[str, float, float]]) -> int | None:
    """Indice em `words` da palavra numero `idx` contada como o b-roll conta (broll.tokens)."""
    if idx is None:
        return None
    n = 0
    for j, (w, _, _) in enumerate(words):
        n += len(re.findall(r"[\w%]+", w))
        if n > idx:
            return j
    return None


def _word_time(word: str | None, words: list[tuple[str, float, float]], start: int,
               idx: int | None = None) -> tuple[float | None, int]:
    """Inicio da palavra na fala (a partir do indice `start`). Com `idx`, procura primeiro perto dessa posicao:
    palavra comum ("a", "e") nao casa com a ocorrencia errada."""
    if not word:
        return None, start
    w = _norm(word)
    order = list(range(start, len(words)))
    pos = _word_pos(idx, words)
    if pos is not None:
        order = sorted((j for j in range(pos - 2, pos + 3) if start <= j < len(words)), key=lambda j: abs(j - pos)) + order
    for j in order:
        ww = _norm(words[j][0])
        if ww == w or (len(w) >= 5 and ww.startswith(w[:5])):
            return words[j][1] - 0.05, j
    return None, start


def _snap_phrase(t: float, lo: float, hi: float, phrases: list[float]) -> float | None:
    """Comeco de frase mais perto de `t` entre lo e hi; o que vem antes da palavra ganha do que vem depois."""
    best, best_d = None, PHRASE_SNAP
    for p in phrases:
        if lo <= p <= hi:
            dist = t - p if p <= t else (p - t) * 2.0
            if dist < best_d:
                best, best_d = p, dist
    return best


def _beat_cuts(s0: float, s1: float, vis: list[dict], words: list[tuple[str, float, float]]) -> tuple[list[float], list[dict | None]]:
    """Cortes do trecho: cada imagem entra no comeco da frase em que a voz fala dela; sem palavra, o tempo e
    dividido por igual. Imagem que cairia a menos de MIN_SHOT da anterior vira reserva (nao encurta o plano).
    Plano que passaria de MAX_SCENE_SECONDS recebe uma reserva, cortada em comeco de frase; sem reserva, foto
    ganha reenquadramento e clipe segue inteiro."""
    spares = [v for v in vis if v.get("spare")]
    vis = [v for v in vis if not v.get("spare")] or spares[:1]
    spares = [v for v in spares if v is not vis[0]] if vis else []
    if not vis:
        n = max(1, math.ceil((s1 - s0) / MAX_SCENE_SECONDS))
        return [s0] + _split_points(s0, s1, n, words) + [s1], [None] * n
    phrases = _phrase_starts(words)
    ideal = [s0] + _split_points(s0, s1, len(vis), words)
    starts, seq, wi, late = [s0], [vis[0]], 0, []
    for k in range(1, len(vis)):
        t, j = _word_time(vis[k].get("word"), words, wi, vis[k].get("idx"))
        if t is None:
            t = ideal[k] if k < len(ideal) else None
        else:
            wi = j + 1
        if t is not None:
            snap = _snap_phrase(t, starts[-1] + MIN_SHOT, s1 - MIN_SHOT, phrases)
            t = snap if snap is not None else t
        if t is None or t < starts[-1] + MIN_SHOT or t > s1 - MIN_SHOT:
            late.append(vis[k])
            continue
        starts.append(t)
        seq.append(vis[k])
    spares = late + spares
    bounds = starts + [s1]
    cuts: list[float] = []
    out: list[dict | None] = []
    for k, v in enumerate(seq):
        a, z = bounds[k], bounds[k + 1]
        m = max(1, math.ceil((z - a) / MAX_SCENE_SECONDS))
        for q, c in enumerate([a] + _split_points(a, z, m, words)):
            if q and spares:
                u = spares.pop(0)
            elif q and v.get("type") == "clip":
                continue
            else:
                u = v
            cuts.append(c)
            out.append(u)
    return cuts + [s1], out


def _close_screen(spoken: str, default: str) -> str:
    """Texto do fechamento igual ao que a voz pede (salvar ou comentar); segue o perfil nunca fecha o video."""
    low = strip_accents(spoken.lower())
    if "salva" in low:
        return "SALVA O VIDEO" if "video" in low else "SALVA"
    if "coment" in low:
        return "COMENTA"
    return default if "segue" not in strip_accents(default.lower()) else "SALVA O VIDEO"

class Director:
    def __init__(self, script: Script, timeline: list[dict], visuals: dict[int, list[dict]], seed: int = 0, with_mascot: bool = True):
        self.script, self.tl, self.vis = script, timeline, visuals
        self.theme = script.theme
        self.th = THEMES[self.theme]
        self.rng = random.Random(f"dir|{script.topic_id}|{seed}")
        self.seed = seed
        self.with_mascot = with_mascot
        self._ti = self.rng.randrange(len(self.th["transitions"]))
        self._zoom_dir = 1
        self._cuts_since_sfx = 0
        self._pointers = 0

    def _next_transition(self) -> str:
        t = self.th["transitions"][self._ti % len(self.th["transitions"])]
        self._ti += 1
        return t

    def _kenburns(self, focal: tuple[float, float], reframe: bool = False) -> tuple[tuple, tuple]:
        z0, z1 = self.th["kenburns"]
        self._zoom_dir *= -1
        center = (0.5, 0.5)
        mid = ((0.5 + focal[0]) / 2, (0.5 + focal[1]) / 2)
        if reframe:  # mesmo material, plano mais fechado no ponto de interesse
            return (z1 + 0.14, z1 + 0.22), (focal, (focal[0] * 0.9 + 0.05, focal[1] * 0.9 + 0.05))
        if self._zoom_dir > 0:
            return (z0, z1), (center, mid)
        return (z1, z0), (mid, center)

    def build(self) -> Storyboard:
        S, tl = self.script, self.tl
        duration = tl[-1]["end"] + 0.9
        scenes: list[Scene] = []
        captions: list[CaptionLine] = []
        punch_zooms: list[tuple[float, float, tuple]] = []
        flashes: list[float] = []
        shakes: list[tuple[float, float]] = []
        dips: list[float] = []
        sfx: list[tuple[str, float, float]] = []
        mascot_segs: list[tuple[float, float, str, str, str]] = []   # (start, end, expr, pose, lado)
        m_side, last_m_end = "right", -9.0   # cada aparicao nova entra pelo lado oposto da anterior
        last_pz = -9.0
        shown_credits: set[str] = set()
        prev_visual_path = None
        last_photo = None

        for bi, beat in enumerate(S.beats):
            b = tl[bi]
            seg_start = 0.0 if bi == 0 else max(scenes[-1].end, b["start"] - 0.1)
            seg_end = duration if bi == len(S.beats) - 1 else tl[bi + 1]["start"] - 0.1
            if seg_end <= seg_start + 0.2:
                seg_end = seg_start + 0.2
            # impacto e cartao, mas ganha imagem quando o b-roll achou uma (cartao parado 10 s cansa)
            vis = [] if beat.kind in CARD_KINDS and beat.kind != "impact" else self.vis.get(bi, [])
            words = b["words"]
            cuts, seq = _beat_cuts(seg_start, seg_end, vis, words)
            n_sub = len(seq)
            beat_scenes: list[Scene] = []
            for k in range(n_sub):
                s0, s1 = cuts[k], cuts[k + 1]
                v = seq[k]
                if v and v.get("type") == "clip":
                    zoom, pan = (1.0, 1.0), ((0.5, 0.5), (0.5, 0.5))
                    visual = {"type": "clip", "frames_dir": v["frames_dir"], "n": v["n"], "fps": v["fps"], "path": v["path"], "credit": v.get("credit", "")}
                    prev_visual_path = v["path"]
                elif v:
                    reframe = (v["path"] == prev_visual_path)
                    zoom, pan = self._kenburns(tuple(v["focal"]), reframe)
                    visual = {"type": "image", "path": v["path"], "focal": v["focal"], "credit": v.get("credit", "")}
                    prev_visual_path = v["path"]
                else:
                    # sem imagem: chuva de codigo azul; fora dos cartoes, com o personagem no centro falando
                    zoom, pan = (1.0, 1.0), ((0.5, 0.5), (0.5, 0.5))
                    visual = {"type": "procedural"} if beat.kind in CARD_KINDS or not self.with_mascot else {"type": "procedural", "mascot": "center"}
                    prev_visual_path = None
                if visual.get("type") == "image":
                    last_photo = visual["path"]
                if bi == 0 and k == 0:
                    trans = "cut"
                elif beat.kind == "twist" and k == 0:
                    trans = "flash"
                elif beat.kind in ("impact", "verdict") and k == 0:
                    trans = "cut"  # depois do mergulho a preto
                elif v and k > 0 and seq[k - 1] and seq[k - 1]["path"] == v["path"]:
                    trans = "cut"  # reenquadramento: corte seco
                elif not v and k > 0:
                    trans = "cut"  # subdivisao de cartao: troca de fundo sem efeito
                elif beat.kind in CARD_KINDS or (bi > 0 and S.beats[bi - 1].kind in CARD_KINDS):
                    trans = "slide_up" if self.theme in ("curiosity", "history") else "whip"
                else:
                    trans = self._next_transition()
                tdur = TRANSITION_DUR.get(trans, 0.3) * (1.25 if self.theme == "history" else 1.0)
                sc = Scene(s0, s1, visual, zoom, pan, trans, tdur, [], bi)
                self._cuts_since_sfx += 1
                if trans in TRANSITION_SFX and (self._cuts_since_sfx >= 2 or trans in ("flash", "glitch")):
                    name, gain = TRANSITION_SFX[trans]
                    sfx.append((name, max(0.0, s0 - 0.06), gain))
                    self._cuts_since_sfx = 0
                beat_scenes.append(sc)
            scenes.extend(beat_scenes)

            def attach(ov: Overlay):
                for sc in beat_scenes:
                    a, z = max(ov.start, sc.start), min(ov.end, sc.end)
                    if z > a + 0.05:
                        params = dict(ov.params)
                        params.setdefault("t0", ov.start)
                        params.setdefault("t1", ov.end)
                        sc.overlays.append(Overlay(ov.kind, a, z, params))

            focal = tuple(vis[0]["focal"]) if vis else (0.5, 0.5)
            conf = vis[0].get("focal_conf", 0.0) if vis else 0.0
            bs, be = b["start"], b["end"]
            num_times = [ws for w, ws, _ in words if re.search(r"\d", w)]
            big_times = [ws for w, ws, _ in words if _norm(w) in {strip_accents(e) for e in BIG_ENTITIES}]
            center = self.with_mascot and not vis and beat.kind not in CARD_KINDS   # sem imagem: personagem no centro
            has_mascot = bool(beat.mascot) and self.with_mascot and not center
            # texto do cartao desloca para a direita quando o personagem esta na tela
            num_no_photo = self.with_mascot and not center and beat.kind in ("fact", "growth") and not vis and bool(re.search(r"\d", beat.text))
            m_on = has_mascot or num_no_photo
            if m_on:
                m0 = max(0.0, bs - 0.15)
                if m0 - last_m_end >= 0.6:   # aparicao nova: troca de lado
                    m_side = "left" if m_side == "right" else "right"
                last_m_end = min(be + 0.35, seg_end)
            # cartao e punchline ficam no lado livre
            px = (0.68 if m_side == "left" else 0.32) if m_on else 0.5
            pw = 0.50 if m_on else 0.84
            card_y = 0.14 if center else (0.42 if not vis else 0.30)

            if has_mascot:
                expr, pose = pose_for(beat.kind, beat.text, MASCOT.get(beat.mascot, MASCOT["neutral"]))
                m0, m1 = max(0.0, bs - 0.15), min(be + 0.35, seg_end)
                if beat.kind == "twist" and m1 - m0 > 1.2:
                    # reacao: leva um susto na virada e so depois cruza os bracos
                    mascot_segs.append((m0, bs + 0.55, "surprised", "hands_cheeks", m_side))
                    mascot_segs.append((bs + 0.55, m1, expr, pose, m_side))
                else:
                    mascot_segs.append((m0, m1, expr, pose, m_side))
            elif num_no_photo:
                # fato com numero sem foto: o personagem aparece apontando o numero
                expr, pose = pose_for(beat.kind, beat.text, ("explain", "point_up"))
                mascot_segs.append((max(0.0, bs - 0.15), min(be + 0.3, seg_end), expr, pose, m_side))
            elif center:
                # cena sem imagem: o personagem ocupa o centro e fala o trecho inteiro (olho na voz, braco no gesto)
                expr, pose = pose_for(beat.kind, beat.text, MASCOT.get(beat.mascot or "neutral", MASCOT["neutral"]))
                mascot_segs.append((seg_start, seg_end, expr, pose, "center"))

            if beat.kind == "hook":
                style = HOOK_STYLE.get(self.theme, "pop")
                attach(Overlay("punch", bs + 0.05, min(be + 0.4, seg_end), {"text": beat.punchline, "style": style, "y": 0.30, "x": px, "max_w": pw}))
                sfx.append(("impact", bs, 0.7))
                shakes.append((bs, 0.012))
                if style == "stamp":
                    flashes.append(bs + 0.2)
                punch_zooms.append((bs + 0.05, 0.05, (0.5, 0.5)))
                last_pz = bs
            elif beat.kind == "follow":
                # aparte "segue o perfil": so voz e mascote; sem etiqueta visual (redundante com a legenda)
                sfx.append(("pop", bs + 0.05, 0.22))
            elif beat.kind == "twist":
                dips.append(max(0.0, seg_start))
                sfx.append(("riser", max(0.0, bs - 1.4), 0.45))
                sfx.append(("impact", bs, 0.6))
                shakes.append((bs, 0.01))
                punch_zooms.append((bs, 0.08, (0.5, 0.5)))
                last_pz = bs
                if beat.text:
                    twist_txt = " ".join(beat.text.rstrip(".:").split()[:6]).upper()
                    attach(Overlay("punch", bs + 0.05, seg_end, {"text": twist_txt, "style": "slide", "y": 0.42, "x": px, "max_w": pw}))
            elif beat.kind in ("impact", "verdict"):
                dips.append(max(0.0, seg_start))
                sfx.append(("whoosh", max(0.0, seg_start - 0.2), 0.35))
                if beat.punchline:
                    attach(Overlay("punch", bs + 0.05, min(bs + 3.0, seg_end), {"text": beat.punchline, "style": "stamp", "y": 0.30, "x": px, "max_w": pw}))
            elif beat.kind == "question":
                attach(Overlay("punch", bs + 0.05, seg_end, {"text": beat.punchline or "COMENTA", "style": "pop", "y": 0.42, "x": px, "max_w": pw, "boxed": True}))
                sfx.append(("pop", bs + 0.05, 0.35))
            elif beat.kind == "close":
                attach(Overlay("punch", bs + 0.05, seg_end - 0.1, {"text": _close_screen(beat.text, S.cta_screen), "style": "pop", "y": 0.42, "x": px, "max_w": pw}))
                sfx.append(("ding", bs + 0.05, 0.4))
                # fonte nao aparece na tela: vai na descricao do post
            elif beat.stat:
                st = beat.stat
                attach(Overlay("stat", bs, min(be + 0.6, seg_end), {"value": st["value"], "label": st.get("label", ""), "prefix": st.get("prefix", ""), "suffix": st.get("suffix", ""), "y": 0.36}))
                for i in range(6):
                    sfx.append(("count", bs + 0.05 + i * 0.13, 0.22))
                t_num = num_times[0] if num_times else bs + 0.4
                punch_zooms.append((t_num, 0.05, (0.5, 0.38)))
                last_pz = t_num
            elif beat.chart:
                ch = beat.chart
                prog = max(0.05, ch.get("progress", 1.0))
                if ch.get("project"):
                    year = str(int(ch["projection"][-1][0]))
                    t_year = next((ws for w, ws, _ in words if _norm(w) == year), bs + 0.6)
                    attach(Overlay("chart", bs, seg_end, {"series": ch["series"], "projection": ch["projection"], "unit": ch["unit"], "title": ch["title"], "data_dur": 0.2, "proj_start": t_year}))
                    pxl, pyl = chart_point_position(1000, 1778, ch["series"], ch["projection"])
                    fx, fy = pxl / 1000, pyl / 1778
                    attach(Overlay("circle", t_year + 0.8, seg_end, {"center": (fx, fy), "r": 0.075, "draw": 0.45}))
                    attach(Overlay("arrow", t_year + 1.0, seg_end, {"from": (0.5, 0.62), "to": (fx + 0.02, fy + 0.06), "draw": 0.45}))
                    sfx.append(("impact", t_year + 0.8, 0.55))
                    punch_zooms.append((t_year + 0.8, 0.06, (fx, fy)))
                    last_pz = t_year + 0.8
                    if beat.punchline:
                        attach(Overlay("punch", t_year + 1.2, seg_end, {"text": beat.punchline, "style": "pop", "y": 0.62}))
                else:
                    attach(Overlay("chart", bs, seg_end, {"series": ch["series"], "projection": ch["projection"], "unit": ch["unit"], "title": ch["title"], "data_dur": max(0.3, (be - bs)) / prog, "proj_start": 9999}))
                    if beat.punchline:
                        attach(Overlay("punch", bs + 0.2, seg_end, {"text": beat.punchline, "style": "pop", "y": 0.62}))
                    sfx.append(("tick", bs + 0.2, 0.25))
            elif beat.kind == "context" and self.theme in ("tech_news", "story") and S.source_name:
                # manchete estilo print do portal, com zoom e marca-texto: o "print da noticia" dos videos de TikTok
                from datetime import date as _date
                meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
                today = _date.today()
                attach(Overlay("headline", bs + 0.05, min(be + 0.5, seg_end), {"source": "NOTÍCIA", "title": S.title, "date": f"{today.day} de {meses[today.month - 1]} de {today.year}"}))
                sfx.append(("swipe", bs + 0.05, 0.3))
                sfx.append(("tick", bs + 0.5, 0.25))
            else:
                # punchline de fato/context/claim suprimida: a legenda palavra-por-palavra ja cobre
                if beat.kind in ("caveat", "growth") and beat.punchline:
                    sfx.append(("pop", bs + 0.08, 0.3))
                if vis and beat.kind in ("fact", "claim") and conf >= 0.45 and self._pointers < 2:
                    self._pointers += 1
                    t_ptr = num_times[0] if num_times else (big_times[0] if big_times else bs + 0.7)
                    if conf >= 0.6 and focal[1] > 0.45:
                        attach(Overlay("arrow", t_ptr, min(be + 0.3, seg_end), {"from": (0.5, 0.37), "to": (focal[0], focal[1] - 0.05), "draw": 0.45}))
                    else:
                        attach(Overlay("circle", t_ptr, min(be + 0.3, seg_end), {"center": focal, "r": 0.14, "draw": 0.5}))
                    sfx.append(("whoosh_short", t_ptr, 0.25))
                # credito da imagem nao aparece na tela: vai na descricao do post (image_credits no .json/.txt)

            for t in (num_times[:1] or big_times[:1]):
                if t - last_pz >= 1.5:
                    punch_zooms.append((t, 0.04, focal if vis else (0.5, 0.5)))
                    last_pz = t
            captions.extend(caption_lines(words, beat.emphasis))

        # personagem: junta segmentos vizinhos numa presenca continua (troca so expressao/pose)
        mascots: list[Overlay] = []
        for s0, s1, expr, pose, side in sorted(mascot_segs):
            if mascots and s0 - mascots[-1].end < 0.6 and mascots[-1].params["side"] == side:
                mascots[-1].end = max(mascots[-1].end, s1)
                mascots[-1].params["segments"].append((s0, expr, pose))
            else:
                mascots.append(Overlay("mascot", s0, s1, {"segments": [(s0, expr, pose)], "side": side,
                                                            "x": {"left": 0.20, "right": 0.80}.get(side, 0.50),
                                                            "y": 0.70 if side == "center" else 0.72}))
        for m in mascots:
            if m.params["side"] != "center":
                sfx.append(("whoosh_short", m.start, 0.22))

        return Storyboard(
            theme=self.theme, duration=duration, scenes=scenes, captions=captions, punch_zooms=punch_zooms,
            flashes=flashes, sfx=sfx, fade_in=0.3, fade_out=0.7, badge=self.th["label"], seed=self.seed,
            shakes=shakes, dips=dips, mascots=mascots,
        )
