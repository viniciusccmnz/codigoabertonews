"""Chuva de codigo azul: fundo das cenas sem imagem (personagem no centro, falando) e dos cartoes.
Nao copia o Matrix: hexadecimal e simbolos de codigo em tons de azul, tres planos de profundidade.
frame(t) nao guarda estado: cada coluna e uma faixa pre-desenhada que desce em velocidade constante."""
from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "JetBrainsMono-Bold.ttf"
GLYPHS = "0123456789ABCDEF{};=<>/()"
BG = np.array([3, 10, 24], np.float32)                 # #030A18
TRAIL_DIM = np.array([10, 61, 145], np.float32)       # #0A3D91
TRAIL = np.array([30, 107, 255], np.float32)          # #1E6BFF
HEAD = np.array([191, 227, 255], np.float32)          # #BFE3FF
# (fonte em fracao da largura, brilho, velocidade em alturas por segundo, fracao de colunas acesas)
LAYERS = ((0.024, 0.34, 0.09, 0.55), (0.032, 0.60, 0.15, 0.40), (0.044, 1.00, 0.22, 0.26))


class CodeRain:
    def __init__(self, W: int, H: int, seed: int = 0):
        self.W, self.H = W, H
        rng = random.Random(seed * 7919 + 17)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        nx, ny = xx / W - 0.5, yy / H - 0.5
        vig = np.clip(1.05 - np.sqrt(nx * nx * 1.3 + ny * ny) * 1.1, 0.30, 1.0)
        # atras do personagem (centro, parte de baixo) a chuva cai para ~25%: o corpo fica legivel
        ell = np.sqrt((nx / 0.30) ** 2 + ((yy / H - 0.70) / 0.26) ** 2)
        soft = np.clip((ell - 0.75) / 0.45, 0.0, 1.0)
        self.mask_plain = vig[..., None]
        self.mask_center = (vig * (0.25 + 0.75 * soft))[..., None]
        self.cols: list[tuple[np.ndarray, int, float, float, float]] = []
        for frac, bright, speed, density in LAYERS:
            size = max(8, int(W * frac))
            font = ImageFont.truetype(str(FONT), size)
            cw, ch = int(size * 0.66), int(size * 1.18)
            for c in range(W // cw):
                if rng.random() > density:
                    continue
                n = rng.randint(7, 20)
                strip = Image.new("RGB", (cw, n * ch), (0, 0, 0))
                d = ImageDraw.Draw(strip)
                for g in range(n):
                    k = g / max(n - 1, 1)                     # 0 = ponta de cima (apagada), 1 = cabeca
                    if g == n - 1:
                        col = HEAD
                    elif k > 0.5:
                        col = TRAIL_DIM + (TRAIL - TRAIL_DIM) * ((k - 0.5) / 0.5)
                    else:
                        col = TRAIL_DIM * (0.25 + 0.75 * k / 0.5)
                    col = tuple(int(v) for v in col * bright)
                    d.text((cw / 2, g * ch + ch / 2), rng.choice(GLYPHS), font=font, fill=col, anchor="mm")
                arr = np.asarray(strip, dtype=np.uint8)
                period = H + arr.shape[0] + rng.uniform(0.0, 0.6) * H
                v = H * speed * rng.uniform(0.75, 1.3)
                x = c * cw + rng.randint(0, max(1, cw // 4))
                self.cols.append((arr, x, period, v, rng.uniform(0.0, period)))

    def frame(self, t: float, center: bool = False, dim: float = 1.0) -> Image.Image:
        W, H = self.W, self.H
        canvas = np.zeros((H, W, 3), np.uint8)
        for arr, x, period, v, ph in self.cols:
            sh, sw = arr.shape[0], arr.shape[1]
            y = int((t * v + ph) % period) - sh
            y0, y1 = max(0, y), min(H, y + sh)
            x1 = min(W, x + sw)
            if y1 <= y0 or x1 <= x:
                continue
            dst = canvas[y0:y1, x:x1]
            np.maximum(dst, arr[y0 - y:y1 - y, :x1 - x], out=dst)
        mask = self.mask_center if center else self.mask_plain
        out = BG + canvas.astype(np.float32) * (mask * dim)
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
