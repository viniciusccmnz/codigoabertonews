"""Setas, circulos, caixas de destaque e grafico animado.

Desenho direto no quadro com sombra por baixo para legibilidade; tracos
progressivos (p de 0 a 1) para parecerem desenhados a mao na hora.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from ..util.text import humanize_number
from .fonts import ease_in_out, ease_out_cubic, font


def _shadowed(d: ImageDraw.ImageDraw, fn, *args, **kw):
    off = kw.pop("shadow", 3)
    color = kw.pop("fill")
    if off:
        kw2 = dict(kw)
        kw2["fill"] = (0, 0, 0)
        fn(*[_shift(a, off) for a in args], **kw2)
    kw["fill"] = color
    fn(*args, **kw)


def _shift(pts, off):
    if isinstance(pts, (list, tuple)) and pts and isinstance(pts[0], (list, tuple)):
        return [(x + off, y + off) for x, y in pts]
    if isinstance(pts, (list, tuple)) and len(pts) == 4 and all(isinstance(v, (int, float)) for v in pts):
        return (pts[0] + off, pts[1] + off, pts[2] + off, pts[3] + off)
    return pts


def arrow(frame: Image.Image, a: tuple[float, float], b: tuple[float, float], p: float, color: tuple, width: int, wobble: float = 6.0) -> None:
    """Seta de a ate b, desenhada ate a fracao p. Leve ondulacao a mao."""
    if p <= 0:
        return
    d = ImageDraw.Draw(frame)
    e = ease_out_cubic(min(1.0, p))
    n = 26
    pts = []
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L, dx / L
    for i in range(n + 1):
        s = i / n
        if s > e:
            break
        wob = math.sin(s * math.pi * 2.0) * wobble * (1 - s)
        pts.append((a[0] + dx * s + nx * wob, a[1] + dy * s + ny * wob))
    if len(pts) < 2:
        return
    _shadowed(d, d.line, pts, fill=color, width=width, joint="curve")
    if e > 0.86:
        tip = pts[-1]
        ang = math.atan2(dy, dy * 0 + dx)
        hl = width * 3.2
        for sgn in (1, -1):
            t = ang + math.pi + sgn * math.radians(28)
            end = (tip[0] + math.cos(t) * hl, tip[1] + math.sin(t) * hl)
            _shadowed(d, d.line, [tip, end], fill=color, width=width)


def circle(frame: Image.Image, center: tuple[float, float], r: float, p: float, color: tuple, width: int, pulse: float = 0.0) -> None:
    if p <= 0:
        return
    d = ImageDraw.Draw(frame)
    e = ease_in_out(min(1.0, p))
    r = r * (1.0 + 0.04 * math.sin(pulse * 7.0))
    box = (center[0] - r, center[1] - r, center[0] + r, center[1] + r)
    end = -90 + 360 * e
    _shadowed(d, d.arc, box, start=-90, end=end, fill=color, width=width)
    # segundo traco levemente deslocado: cara de caneta
    r2 = r * 0.955
    box2 = (center[0] - r2 + 3, center[1] - r2 + 2, center[0] + r2 + 3, center[1] + r2 + 2)
    d.arc(box2, start=-80, end=min(end, 270), fill=color, width=max(1, width // 2))


def highlight_box(frame: Image.Image, box: tuple[float, float, float, float], p: float, color: tuple, width: int) -> None:
    if p <= 0:
        return
    d = ImageDraw.Draw(frame)
    x0, y0, x1, y1 = box
    perim = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    total = 2 * ((x1 - x0) + (y1 - y0))
    e = ease_out_cubic(min(1.0, p)) * total
    pts = [perim[0]]
    for i in range(4):
        seg = math.hypot(perim[i + 1][0] - perim[i][0], perim[i + 1][1] - perim[i][1])
        if e >= seg:
            pts.append(perim[i + 1]); e -= seg
        else:
            s = e / seg
            pts.append((perim[i][0] + (perim[i + 1][0] - perim[i][0]) * s, perim[i][1] + (perim[i + 1][1] - perim[i][1]) * s))
            break
    if len(pts) > 1:
        _shadowed(d, d.line, pts, fill=color, width=width, joint="curve")


def underline(frame: Image.Image, x0: float, x1: float, y: float, p: float, color: tuple, width: int) -> None:
    if p <= 0:
        return
    d = ImageDraw.Draw(frame)
    e = ease_out_cubic(min(1.0, p))
    _shadowed(d, d.line, [(x0, y), (x0 + (x1 - x0) * e, y + 2)], fill=color, width=width)


def chart(W: int, H: int, theme: dict, series: list[tuple[float, float]], projection: list[tuple[float, float]],
          p_data: float, p_proj: float, unit: str, title: str) -> tuple[Image.Image, float, float]:
    """Grafico de linha com projecao tracejada. Retorna (camada, cx, cy)."""
    bw, bh = int(W * 0.86), int(H * 0.40)
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    surf = theme["surface"]
    d.rounded_rectangle((0, 0, bw - 1, bh - 1), radius=int(W * 0.03), fill=(surf[0], surf[1], surf[2], 225), outline=(255, 255, 255, 40), width=2)
    f_small = font("mono", int(W * 0.028))
    f_title = font("bold", int(W * 0.036))
    d.text((int(W * 0.04), int(H * 0.018)), title.upper(), font=f_title, fill=(255, 255, 255, 255))
    pad_l, pad_r, pad_t, pad_b = int(W * 0.05), int(W * 0.06), int(H * 0.055), int(H * 0.05)
    all_pts = series + projection
    xs = [x for x, _ in all_pts]
    ys = [y for _, y in all_pts]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = 0.0, max(ys) * 1.12
    gx0, gy0, gx1, gy1 = pad_l, pad_t, bw - pad_r, bh - pad_b

    def X(x):
        return gx0 + (x - x_min) / max(x_max - x_min, 1e-9) * (gx1 - gx0)

    def Y(y):
        return gy1 - (y - y_min) / max(y_max - y_min, 1e-9) * (gy1 - gy0)

    for k in range(5):
        yv = y_min + (y_max - y_min) * k / 4
        yy = Y(yv)
        d.line([(gx0, yy), (gx1, yy)], fill=(255, 255, 255, 28), width=1)
        d.text((gx0, yy - 2), humanize_number(yv) if yv else "0", font=f_small, fill=(255, 255, 255, 140), anchor="lb")
    for x, _ in series + projection[-1:]:
        d.text((X(x), gy1 + 6), str(int(x)), font=f_small, fill=(255, 255, 255, 170), anchor="mt")

    acc = theme["accent"]
    acc2 = theme["accent2"]
    # dados historicos progressivos
    n = len(series)
    if n >= 2 and p_data > 0:
        total = (n - 1) * ease_out_cubic(min(1.0, p_data))
        pts = []
        for i in range(n):
            if i <= total:
                pts.append((X(series[i][0]), Y(series[i][1])))
            else:
                prev = series[i - 1]
                s = total - (i - 1)
                cur = series[i]
                pts.append((X(prev[0] + (cur[0] - prev[0]) * s), Y(prev[1] + (cur[1] - prev[1]) * s)))
                break
        if len(pts) > 1:
            d.line(pts, fill=(acc[0], acc[1], acc[2], 255), width=max(3, int(W * 0.007)), joint="curve")
        for i, (x, y) in enumerate(series):
            if i <= total:
                r = int(W * 0.009)
                d.ellipse((X(x) - r, Y(y) - r, X(x) + r, Y(y) + r), fill=(255, 255, 255, 255))
    # projecao tracejada
    if projection and p_proj > 0:
        start = series[-1]
        end = projection[-1]
        e = ease_out_cubic(min(1.0, p_proj))
        ex = start[0] + (end[0] - start[0]) * e
        ey = start[1] + (end[1] - start[1]) * e
        x0, y0, x1, y1 = X(start[0]), Y(start[1]), X(ex), Y(ey)
        L = math.hypot(x1 - x0, y1 - y0)
        dash, gap = int(W * 0.02), int(W * 0.012)
        pos = 0.0
        while pos < L:
            s0, s1 = pos / L, min(L, pos + dash) / L
            d.line([(x0 + (x1 - x0) * s0, y0 + (y1 - y0) * s0), (x0 + (x1 - x0) * s1, y0 + (y1 - y0) * s1)],
                   fill=(acc2[0], acc2[1], acc2[2], 255), width=max(3, int(W * 0.006)))
            pos += dash + gap
        if e >= 0.999:
            r = int(W * 0.014)
            d.ellipse((x1 - r, y1 - r, x1 + r, y1 + r), fill=(acc2[0], acc2[1], acc2[2], 255), outline=(255, 255, 255, 255), width=3)
            lab = humanize_number(end[1]) + (" " + unit if unit else "")
            f_lab = font("heavy", int(W * 0.04))
            tw = f_lab.getlength(lab)
            lx = min(max(x1 - tw / 2, gx0), gx1 - tw)
            d.text((lx, y1 - r - 8), lab, font=f_lab, fill=(acc2[0], acc2[1], acc2[2], 255), anchor="lb", stroke_width=3, stroke_fill=(0, 0, 0, 255))
    return layer, W / 2, H * 0.36


def chart_point_position(W: int, H: int, series, projection) -> tuple[float, float]:
    """Posicao absoluta do ponto projetado, para a seta e o circulo apontarem."""
    layer_w, layer_h = int(W * 0.86), int(H * 0.40)
    pad_l, pad_r, pad_t, pad_b = int(W * 0.05), int(W * 0.06), int(H * 0.055), int(H * 0.05)
    all_pts = series + projection
    xs = [x for x, _ in all_pts]
    ys = [y for _, y in all_pts]
    x_min, x_max = min(xs), max(xs)
    y_max = max(ys) * 1.12
    gx0, gy0, gx1, gy1 = pad_l, pad_t, layer_w - pad_r, layer_h - pad_b
    end = projection[-1]
    lx = gx0 + (end[0] - x_min) / max(x_max - x_min, 1e-9) * (gx1 - gx0)
    ly = gy1 - end[1] / max(y_max, 1e-9) * (gy1 - gy0)
    cx, cy = W / 2, H * 0.36
    return cx - layer_w / 2 + lx, cy - layer_h / 2 + ly
