"""Avaliacao automatica do video antes de sair da linha.

Nao substitui assistir, mas pega o que da para medir: repeticao de fala,
ritmo, pausas longas, excesso de efeitos, plano parado, falta de imagem,
punchline comprida, legenda longa. Nota de 0 a 100 e lista de avisos.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .script.writer import Script
from .storyboard import Storyboard
from .util.text import is_stop, strip_accents, words


@dataclass
class Report:
    score: int
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def lines(self) -> list[str]:
        out = [f"QA: nota {self.score}/100"]
        for k, v in self.metrics.items():
            out.append(f"   {k}: {v}")
        for w in self.warnings:
            out.append(f"   AVISO: {w}")
        return out


def _sim(a: str, b: str) -> float:
    wa = {strip_accents(w.lower()) for w in words(a) if len(w) > 3 and not is_stop(w)}
    wb = {strip_accents(w.lower()) for w in words(b) if len(w) > 3 and not is_stop(w)}
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def text_score(script: Script) -> int:
    """Nota so do texto (antes de gastar voz): repeticao, tamanho, substancia, gancho."""
    from .util.text import entities, has_number
    score = 100
    beats = script.beats
    for i in range(len(beats)):
        for j in range(i + 1, len(beats)):
            if _sim(beats[i].text, beats[j].text) > 0.5:
                score -= 12
    wc = script.word_count
    if wc < 175:
        score -= min(30, (175 - wc) // 4)
    if wc > 270:
        score -= 10
    facts = [b for b in beats if b.kind == "fact"]
    weak = [b for b in facts if not has_number(b.text) and not entities(b.text)]
    if facts and len(weak) / len(facts) > 0.5:
        score -= 12
    if len(facts) < 3:
        score -= 10
    hook_words = len(words(beats[0].text)) if beats else 0
    if hook_words > (22 if script.theme in ("curiosity", "theory") else 12):
        score -= 5
    return max(0, score)


def evaluate(script: Script, timeline: list[dict], sb: Storyboard, visuals: dict[int, list[dict]]) -> Report:
    warns: list[str] = []
    score = 100
    beats = script.beats
    # 1. repeticao
    worst = 0.0
    for i in range(len(beats)):
        for j in range(i + 1, len(beats)):
            s = _sim(beats[i].text, beats[j].text)
            if s > worst:
                worst = s
            if s > 0.5:
                warns.append(f"falas repetidas ({s:.0%}): '{beats[i].text[:40]}' x '{beats[j].text[:40]}'")
                score -= 12
    # 2. ritmo e pausas
    all_words = [w for b in timeline for w in b["words"]]
    dur = sb.duration
    spoken = timeline[-1]["end"] - timeline[0]["start"] if timeline else 0
    wps = script.word_count / spoken if spoken else 0
    longest_gap, gap_at = 0.0, 0.0
    for a, b in zip(all_words, all_words[1:]):
        g = b[1] - a[2]
        if g > longest_gap:
            longest_gap, gap_at = g, a[2]
    # narracao animada e continua: 2,2 a 3,9 palavras/s; numero se fala devagar (prediction)
    slow_limit = 1.87 if script.theme == "prediction" else 2.2
    if wps < slow_limit:
        warns.append(f"narracao lenta ({wps:.1f} palavras/s)"); score -= 8
    if wps > 3.9:
        warns.append(f"narracao apressada ({wps:.1f} palavras/s)"); score -= 8
    if longest_gap > 0.9:
        warns.append(f"pausa de {longest_gap:.1f}s aos {gap_at:.1f}s"); score -= 6
    # 3. duracao
    if dur < 60:
        warns.append(f"video abaixo de 1 minuto ({dur:.0f}s): nao monetiza"); score -= 15
    if dur > 95:
        warns.append(f"video longo ({dur:.0f}s)"); score -= 8
    # 4. efeitos sonoros
    density = len(sb.sfx) / max(dur, 1) * 10
    if density > 7:
        warns.append(f"efeitos demais ({density:.1f} por 10s)"); score -= 10
    # 5. cortes
    longest_scene, longest_beat, still = 0.0, -1, None
    for s in sb.scenes:
        d = s.end - s.start
        if d > longest_scene:
            longest_scene, longest_beat = d, s.beat
        # clipe se mexe: so conta como parado depois de 7,5 s; foto depois de 5,5 s
        limit = 7.5 if (s.visual or {}).get("type") == "clip" else 5.5
        if d > limit and (still is None or d > still[0]):
            still = (d, s.beat)
    if still:
        kind = beats[still[1]].kind if 0 <= still[1] < len(beats) else "?"
        warns.append(f"plano parado de {still[0]:.1f}s (trecho {still[1]}, {kind})"); score -= 6
    cuts_per_10 = len(sb.scenes) / max(dur, 1) * 10
    if cuts_per_10 < 1.6:
        warns.append(f"poucos cortes ({cuts_per_10:.1f} por 10s)"); score -= 6
    # 6. imagens
    fact_idx = [i for i, b in enumerate(beats) if b.kind in ("fact", "hook", "context", "claim")]
    with_img = sum(1 for i in fact_idx if visuals.get(i))
    cover = with_img / len(fact_idx) if fact_idx else 0
    if script.theme in ("tech_news", "story", "curiosity", "history", "theory") and cover < 0.5:
        warns.append(f"poucas imagens ({with_img}/{len(fact_idx)} trechos de fato)"); score -= 10
    consecutive = 0
    prev, prev_beat = None, -1
    for sc in sb.scenes:
        p = sc.visual.get("path") if sc.visual.get("type") in ("image", "clip") else None
        if p and p == prev and sc.beat != prev_beat:
            consecutive += 1
        prev, prev_beat = p, sc.beat
    if consecutive > 1:
        warns.append(f"mesma foto repetida em {consecutive} cortes seguidos"); score -= 5
    # 7. punchlines e legendas
    for b in beats:
        if b.punchline and len(b.punchline.split()) > 5:
            warns.append(f"punchline comprida: '{b.punchline}'"); score -= 3
    long_caps = sum(1 for c in sb.captions if len(c.words) > 4)
    if long_caps:
        warns.append(f"{long_caps} linhas de legenda com mais de 4 palavras"); score -= 3
    # 8. substancia: fato sem numero nem nome proprio e conversa fiada
    from .util.text import entities, has_number
    facts = [b for b in beats if b.kind == "fact"]
    weak = [b for b in facts if not has_number(b.text) and not entities(b.text)]
    if facts and len(weak) / len(facts) > 0.5:
        warns.append(f"fatos sem substancia ({len(weak)}/{len(facts)} sem numero nem nome proprio)"); score -= 12
    if len(facts) < 2:
        warns.append("menos de 2 fatos"); score -= 15
    # 9. gancho
    hook_words = len(words(beats[0].text)) if beats else 0
    if hook_words > (22 if script.theme in ("curiosity", "theory") else 12):
        warns.append(f"gancho comprido ({hook_words} palavras)"); score -= 5
    # o publico decide em 1,5 s e o gancho tem que estar fechado aos 3 s de fala
    hook_end = timeline[0]["end"] if timeline else 0.0
    if hook_end > 4.5:
        warns.append(f"gancho termina aos {hook_end:.1f}s (ideal ate 3-4s)"); score -= 6
    # quebra de padrao: nada de 15 s seguidos sem virada, personagem ou punchline
    marks = sorted({0.0} | {o.start for sc in sb.scenes for o in sc.overlays if o.kind == "punch"} | {m.start for m in sb.mascots} | set(sb.flashes))
    biggest = max([b - a for a, b in zip(marks, marks[1:] + [dur])] or [0.0])
    if biggest > 15:
        warns.append(f"{biggest:.0f}s sem quebra de padrao (virada, personagem ou punchline)"); score -= 5
    # 9. setas/circulos sem alvo
    ptr = sum(1 for sc in sb.scenes for o in sc.overlays if o.kind in ("arrow", "circle") and sc.visual.get("type") == "image")
    metrics = {
        "duracao": f"{dur:.1f}s", "palavras": len(all_words), "ritmo": f"{wps:.2f} palavras/s", "maior pausa": f"{longest_gap:.2f}s",
        "planos": len(sb.scenes), "maior plano": f"{longest_scene:.1f}s", "efeitos": len(sb.sfx), "zooms": len(sb.punch_zooms),
        "imagens": f"{with_img}/{len(fact_idx)} trechos", "apontadores": ptr, "repeticao maxima": f"{worst:.0%}",
        "gancho": f"{hook_words} palavras, termina aos {hook_end:.1f}s", "maior trecho sem quebra": f"{biggest:.0f}s",
    }
    return Report(max(0, score), warns, metrics)
