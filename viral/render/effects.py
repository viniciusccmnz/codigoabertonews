"""Efeitos de imagem: Ken Burns, zoom de impacto, transicoes, grao e vinheta.

Tudo opera em PIL (crop+resize em C) ou numpy. Nada de loop por pixel.
"""
from __future__ import annotations

import functools

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .fonts import ease_in_out, ease_out_cubic


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Redimensiona cobrindo w x h sem distorcer (corta sobras)."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(w, int(iw * scale + 0.5)), max(h, int(ih * scale + 0.5))
    img = img.resize((nw, nh), Image.LANCZOS)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return img.crop((x0, y0, x0 + w, y0 + h))


def prepare_source(img: Image.Image, W: int, H: int, headroom: float = 1.3) -> Image.Image:
    """Imagem de origem com folga para o zoom, ja no aspecto do video."""
    return cover(img.convert("RGB"), int(W * headroom), int(H * headroom))


def kenburns(src: Image.Image, W: int, H: int, p: float, z0: float, z1: float,
             c0: tuple[float, float], c1: tuple[float, float]) -> Image.Image:
    """Quadro no instante p (0..1) de um movimento zoom/pan sobre src."""
    p = min(max(p, 0.0), 1.0)
    e = p  # movimento constante: nao "estaciona" no fim do plano
    z = z0 + (z1 - z0) * e
    cx = c0[0] + (c1[0] - c0[0]) * e
    cy = c0[1] + (c1[1] - c0[1]) * e
    sw, sh = src.size
    vw, vh = sw / z, sh / z
    x0 = min(max(cx * sw - vw / 2, 0), sw - vw)
    y0 = min(max(cy * sh - vh / 2, 0), sh - vh)
    return src.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh), reducing_gap=2.0)


def punch_zoom(frame: Image.Image, z: float, center: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    if z <= 1.001:
        return frame
    W, H = frame.size
    vw, vh = W / z, H / z
    x0 = min(max(center[0] * W - vw / 2, 0), W - vw)
    y0 = min(max(center[1] * H - vh / 2, 0), H - vh)
    return frame.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + vw, y0 + vh))


def punch_curve(dt: float, attack: float = 0.14, release: float = 0.55) -> float:
    """0..1: sobe rapido, desce suave. dt = segundos desde o gatilho."""
    if dt < 0:
        return 0.0
    if dt < attack:
        return ease_out_cubic(dt / attack)
    if dt < attack + release:
        return 1.0 - ease_in_out((dt - attack) / release)
    return 0.0


@functools.lru_cache(maxsize=8)
def vignette_mask(W: int, H: int, strength: float = 0.55) -> Image.Image:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    dx = (xx - W / 2) / (W / 2)
    dy = (yy - H / 2) / (H / 2)
    d = np.sqrt(dx * dx + dy * dy) / np.sqrt(2)
    m = 1.0 - strength * np.clip(d - 0.35, 0, 1) ** 1.6 / (0.65 ** 1.6)
    return Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8), "L")


@functools.lru_cache(maxsize=8)
def _black(W: int, H: int) -> Image.Image:
    return Image.new("RGB", (W, H), (0, 0, 0))


@functools.lru_cache(maxsize=8)
def _white(W: int, H: int) -> Image.Image:
    return Image.new("RGB", (W, H), (255, 255, 255))


def vignette(frame: Image.Image, strength: float = 0.55) -> Image.Image:
    W, H = frame.size
    return Image.composite(frame, _black(W, H), vignette_mask(W, H, strength))


def grain(frame: Image.Image, amount: float, rng: np.random.Generator) -> Image.Image:
    if amount <= 0:
        return frame
    a = np.asarray(frame, dtype=np.int16)
    h, w = a.shape[:2]
    noise = rng.integers(-int(amount * 255), int(amount * 255) + 1, size=(h // 2, w // 2, 1), dtype=np.int16)
    noise = np.repeat(np.repeat(noise, 2, axis=0), 2, axis=1)[:h, :w]
    return Image.fromarray(np.clip(a + noise, 0, 255).astype(np.uint8), "RGB")


def fade_black(frame: Image.Image, amount: float) -> Image.Image:
    if amount <= 0:
        return frame
    return Image.blend(frame, _black(*frame.size), min(amount, 1.0))


def flash_white(frame: Image.Image, amount: float) -> Image.Image:
    if amount <= 0:
        return frame
    return Image.blend(frame, _white(*frame.size), min(amount, 1.0))


def brightness(frame: Image.Image, factor: float) -> Image.Image:
    if abs(factor - 1.0) < 0.005:
        return frame
    return frame.point(lambda v: min(255, int(v * factor)))


# ------------------------------------------------------------- transicoes
def _hblur(frame: Image.Image, shift: int) -> Image.Image:
    if shift < 2:
        return frame
    a = np.asarray(frame, dtype=np.float32)
    acc = a.copy()
    steps = 4
    for k in range(1, steps + 1):
        s = int(shift * k / steps)
        acc += np.roll(a, s, axis=1) + np.roll(a, -s, axis=1)
    return Image.fromarray((acc / (2 * steps + 1)).astype(np.uint8), "RGB")


def transition(a: Image.Image, b: Image.Image, p: float, kind: str, rng: np.random.Generator) -> Image.Image:
    """Mistura o quadro anterior (a) com o novo (b) em p (0..1)."""
    p = min(max(p, 0.0), 1.0)
    W, H = a.size
    if kind == "crossfade":
        return Image.blend(a, b, ease_in_out(p))
    if kind == "slide_left":
        e = ease_out_cubic(p)
        out = Image.new("RGB", (W, H))
        out.paste(a, (int(-W * e), 0))
        out.paste(b, (int(W * (1 - e)), 0))
        return out
    if kind == "slide_up":
        e = ease_out_cubic(p)
        out = Image.new("RGB", (W, H))
        out.paste(a, (0, int(-H * e)))
        out.paste(b, (0, int(H * (1 - e))))
        return out
    if kind == "zoom_in":
        e = ease_out_cubic(p)
        zb = punch_zoom(b, 1.0 + 0.35 * (1 - e))
        za = punch_zoom(a, 1.0 + 0.12 * e)
        return Image.blend(za, zb, min(1.0, e * 1.6))
    if kind == "whip":
        amt = np.sin(np.pi * p)
        shift = int(W * 0.09 * amt)
        src = a if p < 0.5 else b
        out = _hblur(src, shift)
        if 0.35 < p < 0.65:  # cruza os dois quadros no pico do movimento
            other = _hblur(b if p < 0.5 else a, shift)
            out = Image.blend(out, other, 0.5 - abs(p - 0.5) * 1.5)
        return out
    if kind == "glitch":
        base = a if p < 0.5 else b
        arr = np.asarray(base).copy()
        amt = np.sin(np.pi * p)
        bands = int(4 + 8 * amt)
        for _ in range(bands):
            y0 = int(rng.integers(0, H - 8))
            h = int(rng.integers(6, max(8, int(H * 0.09))))
            off = int(rng.integers(-int(W * 0.12 * amt) - 1, int(W * 0.12 * amt) + 2))
            arr[y0:y0 + h] = np.roll(arr[y0:y0 + h], off, axis=1)
        ch = int(W * 0.02 * amt)
        if ch > 0:
            arr[:, :, 0] = np.roll(arr[:, :, 0], ch, axis=1)
            arr[:, :, 2] = np.roll(arr[:, :, 2], -ch, axis=1)
        if amt > 0.85:
            y0 = int(rng.integers(0, H // 2))
            hh = int(rng.integers(H // 12, H // 4))
            arr[y0:y0 + hh] = np.roll(arr[y0:y0 + hh], int(rng.integers(-W // 4, W // 4)), axis=1)
        return Image.fromarray(arr, "RGB")
    if kind == "flash":
        out = Image.blend(a, b, ease_in_out(p))
        return flash_white(out, float(np.sin(np.pi * p)) ** 2 * 0.9)
    if kind == "wipe":
        e = ease_in_out(p)
        edge = int(W * 0.08)
        x = int((W + edge) * e) - edge
        mask = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(mask)
        if x > 0:
            d.rectangle((0, 0, min(x, W - 1), H), fill=255)
        if edge > 0 and x < W:
            grad = Image.linear_gradient("L").rotate(-90, expand=True).resize((edge, H))
            mask.paste(grad, (max(x, -edge), 0))
        return Image.composite(b, a, mask)
    return b


def blur(frame: Image.Image, radius: float) -> Image.Image:
    return frame.filter(ImageFilter.GaussianBlur(radius)) if radius > 0 else frame
