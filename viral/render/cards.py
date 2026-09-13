"""Cartoes de noticia: manchete estilo "print do portal" com marca-texto animado.

E um recurso classico de edicao de noticia no TikTok: mostrar a manchete como
se fosse a tela do site, dar zoom e passar um marcador nas palavras-chave.
Aqui o cartao e desenhado por codigo, entao nao depende de captura de tela.
"""
from __future__ import annotations

import functools

from PIL import Image, ImageDraw

from .fonts import ease_out_cubic, font


@functools.lru_cache(maxsize=32)
def headline_base(W: int, H: int, source: str, title: str, date_str: str, accent: tuple) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """Cartao estatico + caixas (x0,y0,x1,y1) de cada linha da manchete, para o marca-texto."""
    cw = int(W * 0.86)
    pad = int(W * 0.05)
    f_src = font("heavy", int(W * 0.03))
    f_title = font("heavy", int(W * 0.064))
    f_date = font("bold", int(W * 0.028))
    # quebra do titulo
    words = title.split()
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if cur and f_title.getlength(trial) > cw - 2 * pad:
            lines.append(" ".join(cur)); cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    lines = lines[:5]
    lh = int(W * 0.064 * 1.22)
    ch = pad * 2 + int(W * 0.075) + len(lines) * lh + int(W * 0.09)
    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=int(W * 0.03), fill=(250, 250, 252, 255))
    # faixa da fonte
    pill_w = int(f_src.getlength(source.upper())) + pad
    d.rounded_rectangle((pad, pad, pad + pill_w, pad + int(W * 0.05)), radius=int(W * 0.025), fill=accent + (255,))
    d.text((pad + pad // 2, pad + int(W * 0.025)), source.upper(), font=f_src, fill=(16, 16, 20, 255), anchor="lm")
    d.text((pad + pill_w + pad // 2, pad + int(W * 0.025)), "TECNOLOGIA", font=f_src, fill=(120, 120, 130, 255), anchor="lm")
    boxes = []
    y = pad + int(W * 0.075)
    for ln in lines:
        d.text((pad, y), ln, font=f_title, fill=(18, 18, 24, 255))
        bb = f_title.getbbox(ln)
        boxes.append((pad + bb[0], y + bb[1], pad + bb[2], y + bb[3]))
        y += lh
    d.text((pad, y + int(W * 0.015)), date_str, font=f_date, fill=(130, 130, 140, 255))
    # icones de rodape (compartilhar/comentar), so decoracao
    for k in range(3):
        cx = cw - pad - k * int(W * 0.07) - int(W * 0.02)
        d.ellipse((cx - int(W * 0.016), y + int(W * 0.018), cx + int(W * 0.016), y + int(W * 0.05)), outline=(170, 170, 180, 255), width=2)
    return card, boxes


def headline_layer(W: int, H: int, theme: dict, source: str, title: str, date_str: str, t: float, dur: float) -> tuple[Image.Image, float, float, float]:
    """Cartao no instante t: entra com zoom, marca-texto varre as linhas, sai em fade."""
    base, boxes = headline_base(W, H, source, title, date_str, tuple(theme["accent"]))
    hl = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(hl)
    sweep = ease_out_cubic(min(1.0, max(0.0, (t - 0.35) / 1.4)))
    total = sum(b[2] - b[0] for b in boxes) or 1
    remaining = sweep * total
    acc = tuple(theme["accent"])
    for (x0, y0, x1, y1) in boxes:
        w = x1 - x0
        take = min(w, remaining)
        if take <= 0:
            break
        d.rectangle((x0 - 4, y0 - 2, x0 + take + 4, y1 + 4), fill=acc + (95,))
        remaining -= take
    layer = Image.alpha_composite(base, hl)  # marca-texto translucido por cima da manchete
    s = 0.92 + 0.08 * ease_out_cubic(min(1.0, t / 0.45))
    if abs(s - 1.0) > 0.005:
        layer = layer.resize((int(layer.width * s), int(layer.height * s)), Image.BILINEAR)
    a = min(1.0, t / 0.2) * (1.0 if dur - t > 0.25 else max(0.0, (dur - t) / 0.25))
    # leve deriva para cima, como um scroll
    dy = -H * 0.02 * min(1.0, t / max(dur, 0.1))
    return layer, W / 2, H * 0.40 + dy, a
