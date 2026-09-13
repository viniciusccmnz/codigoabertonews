"""Fundos gerados por codigo, para o video nunca ficar sem imagem.

Cada tema tem um fundo animado proprio: grade em perspectiva (tech), bokeh
(curiosidade), filme com grao e vinheta (historia), ruido vermelho (aconteceu),
scanlines (previsao). Base estatica pre-renderizada + animacao barata por quadro.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def _gradient(W: int, H: int, top: tuple, bottom: tuple) -> Image.Image:
    t = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    a = np.array(top, np.float32)[None, None, :]
    b = np.array(bottom, np.float32)[None, None, :]
    arr = a + (b - a) * t
    return Image.fromarray(np.repeat(arr, W, axis=1).astype(np.uint8), "RGB")


def _lift(c: tuple, k: float) -> tuple:
    return tuple(min(255, int(v + (255 - v) * k)) for v in c)


class Procedural:
    def __init__(self, kind: str, W: int, H: int, theme: dict, seed: int = 0):
        self.kind, self.W, self.H, self.th = kind, W, H, theme
        self.rng = np.random.default_rng(seed)
        bg, surf = theme["bg"], theme["surface"]
        self.base = _gradient(W, H, _lift(surf, 0.08), bg)
        self.acc = theme["accent"]
        if kind == "bokeh":
            self.blobs = []
            for _ in range(14):
                r = int(self.rng.uniform(W * 0.05, W * 0.18))
                layer = Image.new("RGBA", (r * 2 + 40, r * 2 + 40), (0, 0, 0, 0))
                d = ImageDraw.Draw(layer)
                col = self.acc if self.rng.random() < 0.5 else theme["accent2"]
                d.ellipse((20, 20, 20 + 2 * r, 20 + 2 * r), fill=(col[0], col[1], col[2], int(self.rng.uniform(28, 70))))
                layer = layer.filter(ImageFilter.GaussianBlur(r * 0.35))
                self.blobs.append((layer, self.rng.uniform(0, W), self.rng.uniform(0, H), self.rng.uniform(-14, 14), self.rng.uniform(-20, -6)))
        if kind == "noise":
            small = self.rng.uniform(0, 1, size=(H // 40 + 2, W // 40 + 2)).astype(np.float32)
            self.cloud = Image.fromarray((small * 255).astype(np.uint8), "L").resize((W + 80, H + 80), Image.BICUBIC).filter(ImageFilter.GaussianBlur(24))
        if kind == "scanlines":
            arr = np.zeros((H, W, 4), np.uint8)
            arr[::4, :, 3] = 60
            self.lines = Image.fromarray(arr, "RGBA")

    def frame(self, t: float) -> Image.Image:
        W, H = self.W, self.H
        img = self.base.copy()
        d = ImageDraw.Draw(img)
        acc = self.acc
        if self.kind == "grid":
            horizon = int(H * 0.58)
            glow = (acc[0] // 3, acc[1] // 3, acc[2] // 3)
            d.rectangle((0, horizon - 3, W, horizon + 3), fill=glow)
            n = 12
            speed = (t * 0.35) % 1.0
            for i in range(1, n + 1):
                k = ((i + speed) / n) ** 2.2
                y = horizon + int((H - horizon) * k)
                col = tuple(int(acc[c] * (0.12 + 0.5 * k)) for c in range(3))
                d.line([(0, y), (W, y)], fill=col, width=2 if k > 0.5 else 1)
            for i in range(-8, 9):
                x_bottom = W / 2 + i * W * 0.28
                col = tuple(int(acc[c] * 0.35) for c in range(3))
                d.line([(W / 2 + i * W * 0.03, horizon), (x_bottom, H)], fill=col, width=1)
            for i in range(24):
                px = (i * 97 + int(t * 9)) % W
                py = int((math.sin(i * 1.7 + t * 0.5) * 0.5 + 0.5) * horizon * 0.9)
                d.ellipse((px - 2, py - 2, px + 2, py + 2), fill=tuple(int(acc[c] * 0.8) for c in range(3)))
        elif self.kind == "bokeh":
            for layer, x, y, vx, vy in self.blobs:
                xx = (x + vx * t) % (W + layer.width) - layer.width / 2
                yy = (y + vy * t) % (H + layer.height) - layer.height / 2
                img.paste(layer, (int(xx), int(yy)), layer)
        elif self.kind == "film":
            flick = 1.0 + 0.03 * math.sin(t * 23.0) + 0.02 * math.sin(t * 7.3)
            img = img.point(lambda v: min(255, int(v * flick)))
            arr = np.asarray(img, dtype=np.int16)
            noise = self.rng.integers(-18, 19, size=(H // 2, W // 2, 1), dtype=np.int16)
            noise = np.repeat(np.repeat(noise, 2, axis=0), 2, axis=1)[:H, :W]
            img = Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8), "RGB")
            if int(t * 4) % 9 == 0:
                x = int((t * 300) % W)
                ImageDraw.Draw(img).line([(x, 0), (x + 2, H)], fill=(70, 60, 50), width=1)
        elif self.kind == "noise":
            ox, oy = int(-40 + 30 * math.sin(t * 0.3)), int(-40 + 30 * math.cos(t * 0.23))
            tint = Image.new("RGB", (W + 80, H + 80), (acc[0] // 4, acc[1] // 5, acc[2] // 5))
            mask = self.cloud
            crop_tint = tint.crop((-ox, -oy, -ox + W, -oy + H))
            crop_mask = mask.crop((-ox, -oy, -ox + W, -oy + H))
            img = Image.composite(crop_tint, img, crop_mask)
        elif self.kind == "scanlines":
            off = int(t * 40) % H
            for i in range(0, H, int(H / 10)):
                y = (i + off) % H
                d.line([(0, y), (W, y)], fill=(acc[0] // 5, acc[1] // 5, acc[2] // 5), width=1)
            for i in range(0, W, int(W / 6)):
                d.line([(i, 0), (i, H)], fill=(acc[0] // 6, acc[1] // 6, acc[2] // 6), width=1)
            img.paste(self.lines, (0, 0), self.lines)
        return img
