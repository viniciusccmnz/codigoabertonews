"""Texto na tela: legenda karaoke, punchlines animadas, numero em contagem,
selo de serie. Cada camada e RGBA e o motor cola sobre o quadro.

Estilos de entrada de punchline: pop (escala com overshoot), stamp (carimbo
que bate), typewriter (maquina de escrever com cursor), slide (desliza com
rastro), glitch (fatias e separacao de canal). O tema escolhe o padrao.
"""
from __future__ import annotations

import functools
import math

import numpy as np
from PIL import Image, ImageDraw

from ..util.text import format_number_br
from .fonts import ease_in_cubic, ease_in_out, ease_out_back, ease_out_cubic, font

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)


def _rgba(c, a: int = 255):
    return (c[0], c[1], c[2], a)


class Typo:
    def __init__(self, W: int, H: int, theme: dict, seed: int = 0):
        self.W, self.H, self.th = W, H, theme
        self.cap_size = int(W * 0.066)
        self.punch_size = int(W * 0.092)
        self.stat_size = int(W * 0.2)
        self.badge_size = int(W * 0.031)
        self.stroke = max(2, int(W * 0.0075))
        self.max_text_w = int(W * 0.84)
        self.rng = np.random.default_rng(seed)
        self.caption_style = "box" if theme.get("punch_style") in ("pop", "glitch") else "color"

    # ------------------------------------------------------------ base
    def _line_h(self, size: int) -> int:
        return int(size * 1.22)

    def wrap(self, text: str, kind: str, size: int, max_w: int | None = None) -> list[str]:
        f = font(kind, size)
        max_w = max_w or self.max_text_w
        lines, cur = [], []
        for w in text.split():
            trial = " ".join(cur + [w])
            if cur and f.getlength(trial) > max_w:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))
        return lines

    def fit_size(self, text: str, kind: str, size: int, max_lines: int = 3, max_w: int | None = None) -> int:
        while size > 18:
            lines = self.wrap(text, kind, size, max_w)
            f = font(kind, size)
            if len(lines) <= max_lines and all(f.getlength(l) <= (max_w or self.max_text_w) for l in lines):
                return size
            size = int(size * 0.9)
        return size

    def words_layer(self, lines: list[list[tuple[str, tuple, float, tuple | None]]], kind: str, size: int,
                    stroke: int | None = None, pad: int | None = None, align: str = "center") -> Image.Image:
        """lines: lista de linhas; cada palavra e (texto, cor RGBA, escala, caixa RGBA ou None)."""
        stroke = self.stroke if stroke is None else stroke
        pad = int(size * 0.5) if pad is None else pad
        f = font(kind, size)
        space = f.getlength(" ")
        lh = self._line_h(size)
        widths = []
        for line in lines:
            w = 0.0
            for i, (txt, _, sc, _) in enumerate(line):
                w += font(kind, int(size * sc)).getlength(txt) + (space if i else 0)
            widths.append(w)
        Wl = int(max(widths) if widths else 0) + pad * 2 + stroke * 2
        Hl = lh * len(lines) + pad * 2 + stroke * 2
        layer = Image.new("RGBA", (max(Wl, 4), max(Hl, 4)), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        y = pad + stroke + int(size * 0.95)
        for li, line in enumerate(lines):
            x = (Wl - widths[li]) / 2 if align == "center" else pad + stroke
            for i, (txt, color, sc, box) in enumerate(line):
                fw = font(kind, int(size * sc))
                wlen = fw.getlength(txt)
                if i:
                    x += space
                if box is not None:
                    bb = fw.getbbox(txt, anchor="ls")
                    px, py = int(size * 0.18), int(size * 0.14)
                    d.rounded_rectangle((x + bb[0] - px, y + bb[1] - py, x + bb[2] + px, y + bb[3] + py),
                                        radius=int(size * 0.18), fill=box)
                    d.text((x, y), txt, font=fw, fill=color, anchor="ls")
                else:
                    d.text((x, y), txt, font=fw, fill=color, anchor="ls", stroke_width=stroke, stroke_fill=BLACK)
                x += wlen
            y += lh
        return layer

    # -------------------------------------------------------- legenda
    @functools.lru_cache(maxsize=512)
    def caption(self, words: tuple[str, ...], active: int, emphasis: tuple[str, ...] = (), width_frac: float = 0.90) -> Image.Image:
        acc = _rgba(self.th["accent"])
        line = []
        for i, w in enumerate(words):
            txt = w.upper()
            if i == active:
                big = 1.18 if txt.strip(".,!?") in emphasis else 1.1
                if self.caption_style == "box":
                    line.append((txt, _rgba((12, 12, 16)), big, acc))
                else:
                    line.append((txt, acc, big, None))
            else:
                line.append((txt, WHITE, 1.0, None))
        size = self.cap_size
        limit = int(self.W * width_frac)   # com o mascote na tela a legenda fica mais estreita, a direita
        layer = self.words_layer([line], "heavy", size)
        while layer.width > limit and size > self.cap_size * 0.72:
            size = int(size * 0.92)
            layer = self.words_layer([line], "heavy", size)
        if layer.width > limit and len(line) > 1:
            # quebra em 2 linhas; ao lado do mascote (faixa estreita) aceita ate 3
            for n_lines in (2, 3):
                per = -(-len(line) // n_lines)
                rows = [line[i:i + per] for i in range(0, len(line), per)]
                layer = self.words_layer(rows, "heavy", size)
                if layer.width <= limit or n_lines >= len(line) or width_frac >= 0.7:
                    break
        return layer

    # ------------------------------------------------------- punchline
    @functools.lru_cache(maxsize=256)
    def _punch_base(self, text: str, boxed: bool, mono: bool, chars: int = -1, max_w: int | None = None) -> Image.Image:
        kind = "mono" if mono else "heavy"
        size = self.fit_size(text, kind, self.punch_size, 3, max_w)
        shown = text if chars < 0 else text[:chars]
        lines_txt = self.wrap(text, kind, size, max_w)
        # distribui os caracteres mostrados pelas linhas ja quebradas
        lines = []
        budget = len(shown)
        acc = _rgba(self.th["accent"])
        for lt in lines_txt:
            if chars >= 0:
                take = lt[:max(0, budget)]
                budget -= len(lt) + 1
                if not take and lines:
                    break
                lt = take
            wl = []
            for w in lt.split(" "):
                color = acc if any(ch.isdigit() for ch in w) or w.endswith("%") else WHITE
                if boxed:
                    wl.append((w, _rgba((12, 12, 16)), 1.0, acc))
                else:
                    wl.append((w, color, 1.0, None))
            if wl:
                lines.append(wl)
        if not lines:
            lines = [[(" ", WHITE, 1.0, None)]]
        return self.words_layer(lines, kind, size)

    def punch(self, text: str, style: str, t: float, dur: float, seed: int = 0, max_w: float | None = None, boxed: bool = False) -> tuple[Image.Image, float, float, float] | None:
        """Camada da punchline no instante t (segundos desde a entrada).
        Retorna (camada, dx, dy, alpha) ou None quando invisivel."""
        if t < 0 or t > dur:
            return None
        mw = int(self.W * max_w) if max_w else None
        out_a = 1.0 if dur - t > 0.18 else max(0.0, (dur - t) / 0.18)
        if style == "typewriter":
            n = len(text)
            ct = min(0.055, max(0.02, (dur * 0.45) / max(n, 1)))
            chars = min(n, int(t / ct))
            layer = self._punch_base(text, False, True, chars, mw)
            typing = chars < n
            show_cursor = (typing or t < (n * ct + 0.9)) and (int(t * 6) % 2 == 0)
            if show_cursor:
                layer = layer.copy()
                d = ImageDraw.Draw(layer)
                cw = int(self.punch_size * 0.12)
                d.rectangle((layer.width - cw - 4, int(layer.height * 0.35), layer.width - 4, int(layer.height * 0.72)), fill=_rgba(self.th["accent"]))
            return layer, 0.0, 0.0, out_a
        base = self._punch_base(text, boxed or style == "boxed", False, -1, mw)
        if style == "stamp":
            if t < 0.16:
                e = ease_in_cubic(t / 0.16)
                s = 1.8 - 0.8 * e
                rot = -6 + 4 * e
            else:
                s, rot = 1.0, -2.0
            layer = base.rotate(rot, resample=Image.BICUBIC, expand=True)
            if abs(s - 1.0) > 0.01:
                layer = layer.resize((max(1, int(layer.width * s)), max(1, int(layer.height * s))), Image.BILINEAR)
            return layer, 0.0, 0.0, out_a
        if style == "slide":
            e = ease_out_cubic(min(1.0, t / 0.32))
            dx = -(1 - e) * self.W * 0.5
            if t < 0.32:
                layer = Image.new("RGBA", (base.width + int(abs(dx) * 0.8) + 8, base.height), (0, 0, 0, 0))
                for k, ga in ((0.8, 0.12), (0.45, 0.25)):
                    ghost = base.copy()
                    ghost.putalpha(ghost.getchannel("A").point(lambda v: int(v * ga)))
                    layer.paste(ghost, (int(abs(dx) * k), 0), ghost)
                layer.paste(base, (0, 0), base)
                return layer, dx + (layer.width - base.width) / 2, 0.0, out_a
            return base, 0.0, 0.0, out_a
        if style == "glitch":
            if t < 0.24:
                rng = np.random.default_rng(seed + int(t * 60))
                arr = np.asarray(base).copy()
                h = arr.shape[0]
                for _ in range(int(rng.integers(2, 6))):
                    y0 = int(rng.integers(0, max(1, h - 4)))
                    hh = int(rng.integers(3, max(4, h // 5)))
                    arr[y0:y0 + hh] = np.roll(arr[y0:y0 + hh], int(rng.integers(-40, 41)), axis=1)
                r = np.roll(arr, 6, axis=1)
                b = np.roll(arr, -6, axis=1)
                mix = arr.copy()
                mix[:, :, 0] = r[:, :, 0]
                mix[:, :, 2] = b[:, :, 2]
                mix[:, :, 3] = np.maximum(arr[:, :, 3], np.maximum(r[:, :, 3], b[:, :, 3]) // 2)
                return Image.fromarray(mix, "RGBA"), 0.0, 0.0, out_a * (0.6 if int(t * 30) % 4 == 0 else 1.0)
            return base, 0.0, 0.0, out_a
        # pop (padrao)
        s = ease_out_back(min(1.0, t / 0.26))
        a_in = min(1.0, t / 0.08)
        layer = base
        if abs(s - 1.0) > 0.01 and s > 0.05:
            layer = base.resize((max(1, int(base.width * s)), max(1, int(base.height * s))), Image.BILINEAR)
        return layer, 0.0, 0.0, min(a_in, out_a)

    # ----------------------------------------------------------- numero
    @functools.lru_cache(maxsize=256)
    def _stat_base(self, value_txt: str, label: str) -> Image.Image:
        size = self.fit_size(value_txt, "heavy", self.stat_size, 1, int(self.W * 0.86))
        acc = _rgba(self.th["accent"])
        lines = [[(value_txt, acc, 1.0, None)]]
        top = self.words_layer(lines, "heavy", size, stroke=int(self.stroke * 1.3))
        if not label:
            return top
        lsize = self.fit_size(label.upper(), "bold", int(self.W * 0.05), 2)
        lab_lines = [[(w, WHITE, 1.0, None) for w in l.split(" ")] for l in self.wrap(label.upper(), "bold", lsize)]
        bottom = self.words_layer(lab_lines, "bold", lsize)
        layer = Image.new("RGBA", (max(top.width, bottom.width), top.height + bottom.height - int(size * 0.25)), (0, 0, 0, 0))
        layer.paste(top, ((layer.width - top.width) // 2, 0), top)
        layer.paste(bottom, ((layer.width - bottom.width) // 2, top.height - int(size * 0.25)), bottom)
        return layer

    def stat(self, value: float, label: str, t: float, dur: float, prefix: str = "", suffix: str = "", decimals: int = 0) -> tuple[Image.Image, float, float, float] | None:
        if t < 0 or t > dur:
            return None
        p = ease_out_cubic(min(1.0, t / 0.9))
        v = value * p
        txt = f"{prefix}{format_number_br(v, decimals)}{suffix}"
        layer = self._stat_base(txt, label)
        s = 1.0 + 0.06 * (1 - p)
        if s > 1.01:
            layer = layer.resize((int(layer.width * s), int(layer.height * s)), Image.BILINEAR)
        out_a = 1.0 if dur - t > 0.2 else max(0.0, (dur - t) / 0.2)
        return layer, 0.0, 0.0, min(1.0, t / 0.08) * out_a

    # ------------------------------------------------------------- selo
    @functools.lru_cache(maxsize=8)
    def badge(self, text: str) -> Image.Image:
        f = font("heavy", self.badge_size)
        w = int(f.getlength(text)) + self.badge_size * 2
        h = int(self.badge_size * 2.1)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=_rgba(self.th["accent"]))
        d.text((w / 2, h / 2), text, font=f, fill=_rgba((12, 12, 16)), anchor="mm")
        return layer

    @functools.lru_cache(maxsize=8)
    def small_label(self, text: str, color: tuple | None = None) -> Image.Image:
        f = font("bold", int(self.W * 0.028))
        w = int(f.getlength(text)) + 16
        layer = Image.new("RGBA", (w, int(self.W * 0.045)), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text((8, layer.height / 2), text, font=f, fill=_rgba(color or self.th["text"]), anchor="lm", stroke_width=2, stroke_fill=BLACK)
        return layer


def paste_center(frame: Image.Image, layer: Image.Image, cx: float, cy: float, alpha: float = 1.0) -> None:
    if layer is None or alpha <= 0.0:
        return
    if alpha < 0.999:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    x = int(cx - layer.width / 2)
    y = int(cy - layer.height / 2)
    frame.paste(layer, (x, y), layer)
