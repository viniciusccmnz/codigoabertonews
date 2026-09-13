"""Personagem do canal "Codigo Aberto News", desenhado por codigo (sem imagem externa).

Fiel ao logo do canal: hacker de moletom cinza-grafite com capuz levantado,
contorno preto grosso de mascote de esports, brilho nas dobras do capuz e
um fio de luz ciano na borda direita. Dentro do capuz o rosto e preto; so os
dois olhos ciano angulados brilham. Mascara cinza cobre do nariz ao queixo,
com digitos binarios ciano bem apagados. Cordoes claros do capuz, luvas
brancas de desenho animado com contorno preto. Sem boca.

Movimento: so bracos e olhos.
- olhos: piscada periodica, olhar acompanhando a pose, formato pela expressao
  (neutral, surprised, serious, explain, happy, thinking) e brilho que pulsa
  de leve com a energia da voz (mouth_q);
- bracos: 12 poses (idle, point_up, point_right, point_down, explain, wave,
  thinking, thumbs_up, shrug, arms_crossed, hands_cheeks, clap), algumas com
  vaivem da mao (aceno, palmas, explicacao);
- o corpo nao balanca, nao inclina e nao pula; a entrada e a saida sao um
  deslizar em diagonal, sem passada.
Camadas em cache por estado quantizado, com teto de bytes por personagem (clear() no fim
do render); o brilho que segue a voz e um recorte pequeno so dos olhos.
"""
from __future__ import annotations

import math
import random
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# paleta do logo
LINE = (8, 9, 13)              # contorno grosso
HOOD = (70, 76, 88)            # tecido do capuz
HOOD_LIGHT = (104, 112, 126)   # brilho das dobras de cima
HOOD_SPEC = (150, 158, 172)    # reflexo pontual no alto do capuz
HOOD_DARK = (42, 46, 55)       # borda interna do capuz e vincos
BODY = (60, 65, 76)            # moletom (um tom abaixo do capuz)
BODY_LIGHT = (86, 92, 106)
BODY_DARK = (40, 44, 52)
FACE = (4, 5, 8)               # dentro do capuz: preto
MASK = (44, 48, 58)
MASK_LIGHT = (64, 70, 82)
CORD = (178, 184, 194)
CORD_TIP = (118, 124, 136)
GLOVE = (238, 240, 244)
GLOVE_SHADE = (190, 196, 206)
GLOVE_LINE = (150, 156, 168)
# nomes antigos, mantidos por compatibilidade
FABRIC, FABRIC_LIGHT, FABRIC_DARK, EDGE, CORD_OLD = HOOD, HOOD_LIGHT, HOOD_DARK, LINE, CORD
WHITE = (255, 255, 255)

# expressao -> (forma do olho, meia largura, meia altura, inclinacao (rad, canto de dentro para baixo),
#               lado do olho maior, lado semicerrado, arco de sobrancelha, olhar em y)
EXPRESSIONS = {
    "neutral":   ("blade", 0.078, 0.042, 0.20, 0, 0, False, 0.000),
    "surprised": ("round", 0.056, 0.056, 0.00, 0, 0, False, 0.000),
    "serious":   ("blade", 0.082, 0.028, 0.36, 0, 0, False, 0.000),
    "explain":   ("blade", 0.078, 0.044, 0.12, 1, 0, False, 0.000),
    "happy":     ("blade", 0.078, 0.030, 0.08, 0, 0, False, 0.000),
    "thinking":  ("blade", 0.078, 0.042, 0.20, 0, 1, False, -0.010),
}
POSE_FACE = {
    "idle": "neutral", "point_up": "explain", "point_right": "explain", "point_down": "happy", "explain": "explain", "wave": "happy",
    "thinking": "thinking", "thumbs_up": "happy", "shrug": "thinking", "arms_crossed": "serious", "hands_cheeks": "surprised", "clap": "happy",
}
POSES = list(POSE_FACE)
ANIMATED_POSES = ("explain", "wave", "clap")   # poses em que a fase (vaivem da mao) muda o desenho
LOOK_X = {"point_right": 0.016, "point_up": 0.010, "point_down": 0.006, "explain": 0.008, "thinking": -0.012, "wave": 0.006}
LOOK_Y = {"point_up": -0.010, "point_down": 0.010, "thinking": -0.008}


def _eye_shape(shape: str, w: float, h: float, n: int = 40) -> list[tuple[float, float]]:
    """Contorno do olho em unidades normalizadas, centrado em (0, 0); y cresce para baixo."""
    pts: list[tuple[float, float]] = []
    if shape == "round":
        for i in range(n):
            a = 2 * math.pi * i / n
            pts.append((w * math.cos(a), h * math.sin(a)))
    elif shape == "arc":
        # arco ^ (olho feliz): faixa entre dois raios; w = raio externo, h = espessura
        ro, ri = w, max(w - h, w * 0.3)
        m = n // 2
        for i in range(m + 1):
            a = math.radians(200 + 140 * i / m)
            pts.append((ro * math.cos(a), ro * math.sin(a) * 0.9 + w * 0.25))
        for i in range(m, -1, -1):
            a = math.radians(200 + 140 * i / m)
            pts.append((ri * math.cos(a), ri * math.sin(a) * 0.9 + w * 0.25))
    else:  # lamina: cima arqueado, baixo quase reto, pontas afiadas (olho do logo)
        m = n // 2
        for i in range(m + 1):
            u = -1 + 2 * i / m
            pts.append((w * u, -h * (1 - u * u) ** 0.85))
        for i in range(m, -1, -1):
            u = -1 + 2 * i / m
            pts.append((w * u, h * 0.55 * (1 - u * u) ** 1.3))
    return pts


def _place(pts: list[tuple[float, float]], ex: float, ey: float, scale: float, angle: float, lid: float | None) -> list[tuple[float, float]]:
    """Escala, corta pela palpebra (lid = y maximo para cima), gira e desloca."""
    ca, sa = math.cos(angle), math.sin(angle)
    out = []
    for x, y in pts:
        x, y = x * scale, y * scale
        if lid is not None:
            y = max(y, lid * scale)
        out.append((ex + x * ca - y * sa, ey + x * sa + y * ca))
    return out


def _mix(c: tuple, k: float) -> tuple:
    """Clareia a cor em direcao ao branco (k = 0 cor pura, 1 branco)."""
    return tuple(int(v + (255 - v) * k) for v in c[:3])


def _bez(p0, p1, p2, p3, n: int = 18) -> list[tuple[float, float]]:
    out = []
    for i in range(1, n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _path(start, *curves) -> list[tuple[float, float]]:
    """Caminho de curvas de Bezier: cada curva e (controle1, controle2, fim)."""
    pts = [start]
    for c1, c2, end in curves:
        pts += _bez(pts[-1], c1, c2, end)
    return pts


def _mirror(right: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Lado direito (de cima para baixo) -> contorno fechado simetrico."""
    return right + [(1.0 - x, y) for x, y in reversed(right)]


def _nbytes(im: Image.Image) -> int:
    return im.width * im.height * len(im.getbands())


class Mascot:
    def __init__(self, size: int, accent: tuple, accent2: tuple, max_bytes: int = 200 * 2**20, max_items: int = 512):
        self.S = size
        self.accent = tuple(accent[:3])
        self.accent2 = tuple(accent2[:3])
        self.shirt = self.accent          # nomes antigos, mantidos por compatibilidade
        self.shirt2 = self.accent2
        self.X0 = 0.16                    # margem lateral (fracao de S): mao esticada nao corta na borda
        self.W, self.H = int(size * (1 + 2 * self.X0)), int(size * 1.35)
        self._base_img: Image.Image | None = None
        # cache LRU do personagem (camadas, brilho dos olhos, espelhadas, sombras): teto em bytes e em entradas.
        # Cada imagem conta uma vez, mesmo citada por varias entradas; a base entra na conta e nunca sai.
        self.max_bytes, self.max_items = max_bytes, max_items
        self._cache: OrderedDict[tuple, tuple] = OrderedDict()   # chave -> (valor, imagens que ele segura)
        self._refs: dict[int, list] = {}                           # id(imagem) -> [imagem, entradas que a citam]
        self.cache_bytes = 0

    # ------------------------------------------------------------------ cache
    def _hold(self, im: Image.Image) -> None:
        ref = self._refs.get(id(im))
        if ref is None:
            self._refs[id(im)] = [im, 1]
            self.cache_bytes += _nbytes(im)
        else:
            ref[1] += 1

    def _release(self, im: Image.Image) -> None:
        ref = self._refs[id(im)]
        ref[1] -= 1
        if ref[1] <= 0:
            del self._refs[id(im)]
            self.cache_bytes -= _nbytes(im)

    def _cget(self, key: tuple):
        hit = self._cache.get(key)
        if hit is None:
            return None
        self._cache.move_to_end(key)
        return hit[0]

    def _cput(self, key: tuple, value, *imgs: Image.Image):
        old = self._cache.pop(key, None)
        if old is not None:
            for im in old[1]:
                self._release(im)
        for im in imgs:
            self._hold(im)
        self._cache[key] = (value, imgs)
        while (self.cache_bytes > self.max_bytes or len(self._cache) > self.max_items) and len(self._cache) > 1:
            _k, (_v, gone) = self._cache.popitem(last=False)
            for im in gone:
                self._release(im)
        return value

    def clear(self) -> None:
        """Solta tudo que o personagem guarda (fim do render: o processo diario segue para o proximo video)."""
        self._cache.clear()
        self._refs.clear()
        self._base_img = None
        self.cache_bytes = 0

    def _p(self, x: float, y: float) -> tuple[float, float]:
        return ((x + self.X0) * self.S, y * self.S)

    def _px(self, pts) -> list[tuple[float, float]]:
        return [((x + self.X0) * self.S, y * self.S) for x, y in pts]

    # ------------------------------------------------------------------ geometria fixa
    @staticmethod
    def _hood_pts():
        right = _path((0.5, 0.055),
                      ((0.565, 0.062), (0.80, 0.16), (0.815, 0.43)),
                      ((0.83, 0.62), (0.79, 0.77), (0.72, 0.85)),
                      ((0.65, 0.91), (0.56, 0.935), (0.5, 0.935)))
        return _mirror(right)

    @staticmethod
    def _opening_pts(k: float = 1.0):
        right = _path((0.5, 0.19),
                      ((0.60, 0.185), (0.705, 0.28), (0.705, 0.44)),
                      ((0.705, 0.60), (0.60, 0.72), (0.5, 0.82)))
        pts = _mirror(right)
        if k != 1.0:
            pts = [(0.5 + (x - 0.5) * k, 0.47 + (y - 0.47) * k) for x, y in pts]
        return pts

    @staticmethod
    def _body_pts():
        right = _path((0.5, 0.80),
                      ((0.58, 0.80), (0.63, 0.80), (0.68, 0.825)),
                      ((0.76, 0.855), (0.825, 0.895), (0.845, 0.99)),
                      ((0.865, 1.10), (0.862, 1.24), (0.855, 1.36)))
        return right + [(0.5, 1.36)] + [(1.0 - x, y) for x, y in reversed(right)]

    @staticmethod
    def _mask_pts():
        top = _path((0.285, 0.435), ((0.36, 0.405), (0.44, 0.372), (0.5, 0.384)), ((0.56, 0.372), (0.64, 0.405), (0.715, 0.435)))
        side = _path((0.715, 0.435), ((0.725, 0.57), (0.61, 0.70), (0.5, 0.765)), ((0.39, 0.70), (0.275, 0.57), (0.285, 0.435)))
        return top + side[1:]

    def _outlined(self, d: ImageDraw.ImageDraw, pts, fill, ow: int) -> None:
        px = self._px(pts)
        d.polygon(px, fill=fill)
        d.line(px + [px[0]], fill=LINE, width=ow, joint="curve")

    def _base(self) -> Image.Image:
        """Corpo, capuz, rosto e mascara: tudo que nao muda com pose nem expressao (desenhado uma vez)."""
        if self._base_img is not None:
            return self._base_img
        S = self.S
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        P = self._p
        ow = max(3, int(S * 0.020))          # contorno grosso de mascote
        thin = max(2, int(S * 0.009))

        # ------------------------------------------------ moletom
        self._outlined(d, self._body_pts(), BODY, ow)
        # luz no alto dos ombros e vincos
        for sx in (-1, 1):
            sh = _path((0.5 + sx * 0.20, 0.855), ((0.5 + sx * 0.27, 0.87), (0.5 + sx * 0.315, 0.91), (0.5 + sx * 0.33, 0.99)))
            d.line(self._px(sh), fill=BODY_LIGHT if sx < 0 else BODY, width=int(S * 0.022), joint="curve")
            d.line(self._px(_path((0.5 + sx * 0.24, 1.04), ((0.5 + sx * 0.23, 1.12), (0.5 + sx * 0.21, 1.20), (0.5 + sx * 0.20, 1.30)))),
                   fill=BODY_DARK, width=thin, joint="curve")
        d.polygon(self._px([(0.43, 0.90), (0.57, 0.90), (0.5, 1.02)]), fill=BODY_DARK)    # sombra do V no peito

        # ------------------------------------------------ capuz
        hood = self._hood_pts()
        self._outlined(d, hood, HOOD, ow)
        # brilho grande na dobra de cima, lado esquerdo (luz do logo)
        hl = [(x, y) for x, y in hood if x < 0.5 and 0.09 < y < 0.50]
        if hl:
            inner = [(x + 0.055 * (1 - abs(y - 0.25) / 0.3), y + 0.01) for x, y in reversed(hl)]
            d.polygon(self._px(hl + inner), fill=HOOD_LIGHT)
        d.ellipse([P(0.395, 0.100), P(0.455, 0.128)], fill=HOOD_SPEC)                      # reflexo pontual
        d.line(self._px([(0.5, 0.062), (0.506, 0.12), (0.5, 0.19)]), fill=HOOD_DARK, width=thin, joint="curve")   # costura
        # vincos laterais do capuz
        for sx in (-1, 1):
            d.line(self._px(_path((0.5 + sx * 0.255, 0.60), ((0.5 + sx * 0.27, 0.68), (0.5 + sx * 0.24, 0.76), (0.5 + sx * 0.19, 0.83)))),
                   fill=HOOD_DARK, width=thin, joint="curve")
        # borda interna (profundidade do capuz)
        d.polygon(self._px(self._opening_pts(1.10)), fill=HOOD_DARK)

        # ------------------------------------------------ rosto: preto, mascara com digitos binarios
        opening = self._px(self._opening_pts())
        face = Image.new("RGBA", (self.W, self.H), FACE + (255,))
        fd = ImageDraw.Draw(face)
        mask_px = self._px(self._mask_pts())
        fd.polygon(mask_px, fill=MASK)
        # brilho suave na metade esquerda da mascara
        hi = Image.new("L", (self.W, self.H), 0)
        ImageDraw.Draw(hi).ellipse([P(0.33, 0.43), P(0.48, 0.66)], fill=120)
        hi = hi.filter(ImageFilter.GaussianBlur(S * 0.03))
        mclip = Image.new("L", (self.W, self.H), 0)
        ImageDraw.Draw(mclip).polygon(mask_px, fill=255)
        hi = Image.fromarray((np.asarray(hi, np.float32) * np.asarray(mclip, np.float32) / 255).astype(np.uint8), "L")
        face.paste(Image.new("RGBA", face.size, MASK_LIGHT + (255,)), (0, 0), hi)
        # digitos binarios ciano apagados (na mascara e na sombra ao redor dos olhos)
        code = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(code)
        try:
            font = ImageFont.load_default(size=max(8, int(S * 0.019)))
        except TypeError:
            font = ImageFont.load_default()
        rng = random.Random(7)
        step_x, step_y = S * 0.021, S * 0.022
        for col in range(int(0.44 / 0.021)):
            xn = 0.28 + col * 0.021
            x = (xn + self.X0) * S
            if abs(xn - 0.5) < 0.045:
                continue                      # centro da mascara limpo, como no logo
            for row in range(int(0.40 / 0.022)):
                y = (0.30 + row * 0.022) * S
                if rng.random() < 0.35:
                    continue
                a = 70 if y / S > 0.42 else 28
                cd.text((x, y), rng.choice("01"), font=font, fill=self.accent + (a,))
        face.alpha_composite(code)
        fd = ImageDraw.Draw(face)
        fd.line(mask_px[: len(mask_px) // 2 + 1], fill=MASK_LIGHT, width=thin, joint="curve")   # quina de cima da mascara
        clip = Image.new("L", (self.W, self.H), 0)
        ImageDraw.Draw(clip).polygon(opening, fill=255)
        img.paste(face, (0, 0), clip)
        d = ImageDraw.Draw(img)
        d.line(opening + [opening[0]], fill=LINE, width=thin, joint="curve")

        # ------------------------------------------------ cordoes do capuz
        for sx in (-1, 1):
            cord = _path((0.5 + sx * 0.045, 0.87), ((0.5 + sx * 0.05, 0.93), (0.5 + sx * 0.058, 1.00), (0.5 + sx * 0.062, 1.07)))
            cp = self._px(cord)
            d.line(cp, fill=LINE, width=int(S * 0.024), joint="curve")
            d.line(cp, fill=CORD, width=int(S * 0.012), joint="curve")
            tx, ty = cp[-1]
            d.rounded_rectangle([tx - S * 0.014, ty - S * 0.004, tx + S * 0.014, ty + S * 0.042], radius=int(S * 0.008), fill=CORD_TIP, outline=LINE, width=thin)

        # ------------------------------------------------ fio de luz ciano na borda direita (capuz e ombro)
        rim = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        rd = ImageDraw.Draw(rim)
        edge = [(x - 0.016, y) for x, y in hood if x > 0.62 and 0.14 < y < 0.80]
        shoulder = [(x - 0.016, y + 0.010) for x, y in self._body_pts() if x > 0.72 and y < 1.12]
        for seg in (edge, shoulder):
            if len(seg) > 1:
                rd.line(self._px(seg), fill=self.accent + (120,), width=int(S * 0.014), joint="curve")
        rim = rim.filter(ImageFilter.GaussianBlur(S * 0.006))
        rd = ImageDraw.Draw(rim)
        for seg in (edge, shoulder):
            if len(seg) > 1:
                rd.line(self._px(seg), fill=_mix(self.accent, 0.3) + (210,), width=max(2, int(S * 0.005)), joint="curve")
        img.alpha_composite(rim)
        self._base_img = img
        self._hold(img)
        return img

    # ------------------------------------------------------------------ bracos e luvas
    def _cap(self, d: ImageDraw.ImageDraw, a, b, w: float, fill) -> None:
        d.line([a, b], fill=fill, width=max(1, int(w)))
        r = w / 2
        for c in (a, b):
            d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=fill)

    def _arm(self, d: ImageDraw.ImageDraw, a: tuple, b: tuple) -> None:
        """Manga do moletom em dois segmentos (braco e antebraco, cotovelo para fora do corpo),
        contorno preto e punho mais escuro."""
        S = self.S
        sw = S * 0.088
        ow = max(3, int(S * 0.018))
        L = S * 0.21
        dist = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        half = dist / 2
        h = math.sqrt(max(L * L - half * half, 0.0))
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        px_, py_ = -(b[1] - a[1]) / dist, (b[0] - a[0]) / dist
        mid = (0.5 + self.X0) * S
        score = lambda k: abs(mx + k * px_ * h - mid) + 0.6 * k * py_ * h   # para fora e, no empate, para baixo
        k = 1 if score(1) >= score(-1) else -1
        e = (mx + k * px_ * h, my + k * py_ * h)
        for w, col in ((sw + 2 * ow, LINE), (sw, BODY)):
            self._cap(d, a, e, w, col)
            self._cap(d, e, b, w * 0.95, col)
        a = e   # punho e luz calculados no antebraco
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1.0
        ux, uy = dx / ln, dy / ln
        c0 = (b[0] - ux * S * 0.035, b[1] - uy * S * 0.035)
        self._cap(d, (c0[0] - ux * ow * 0.5, c0[1] - uy * ow * 0.5), (c0[0] + ux * ow * 0.5, c0[1] + uy * ow * 0.5), sw + ow, LINE)
        self._cap(d, c0, b, sw * 0.96, BODY_DARK)
        # luz na parte de cima da manga
        nx, ny = -uy, ux
        if ny > 0:
            nx, ny = -nx, -ny
        off = sw * 0.24
        self._cap(d, (a[0] + nx * off, a[1] + ny * off), (c0[0] + nx * off - ux * S * 0.02, c0[1] + ny * off - uy * S * 0.02), sw * 0.18, BODY_LIGHT)

    def _glove(self, d: ImageDraw.ImageDraw, c: tuple, ang: float, kind: str = "fist", r: float = 0.054) -> None:
        """Luva branca de desenho animado. ang = para onde a mao aponta (do punho para fora)."""
        S = self.S
        R = S * r
        ow = max(3, int(S * 0.018))

        def at(a: float, k: float) -> tuple[float, float]:
            return (c[0] + math.cos(a) * R * k, c[1] + math.sin(a) * R * k)

        def lat(k_along: float, k_side: float) -> tuple[float, float]:
            """Ponto a k_along raios na direcao da mao e k_side raios para o lado."""
            ca, sa = math.cos(ang), math.sin(ang)
            return (c[0] + (ca * k_along - sa * k_side) * R, c[1] + (sa * k_along + ca * k_side) * R)

        shapes: list[tuple] = []   # ("o", centro, raio) | ("c", a, b, largura)
        if kind == "open":
            shapes.append(("o", c, R * 0.92))
            for off in (-0.62, -0.21, 0.21, 0.62):
                shapes.append(("c", at(ang + off, 0.55), at(ang + off, 1.75), R * 0.42))
            shapes.append(("c", at(ang - 1.45, 0.45), at(ang - 1.15, 1.40), R * 0.44))
        else:
            shapes.append(("o", c, R))
            if kind == "point":
                # indicador saindo da lateral do punho e polegar do outro lado: le como apontar
                shapes.append(("c", lat(0.2, -0.42), lat(2.2, -0.42), R * 0.50))
                shapes.append(("c", lat(-0.15, 0.62), lat(0.70, 0.95), R * 0.44))
            elif kind == "thumb":
                shapes[0] = ("o", (c[0] - R * 0.15, c[1] + R * 0.10), R * 0.85)
                for k in range(4):   # dedos enrolados, empilhados na horizontal
                    yk = c[1] - R * 0.52 + k * R * 0.40
                    shapes.append(("c", (c[0] - R * 0.30, yk), (c[0] + R * 0.62, yk), R * 0.46))
                shapes.append(("c", (c[0] - R * 0.50, c[1] - R * 0.45), (c[0] - R * 0.40, c[1] - R * 1.85), R * 0.52))
            else:
                shapes.append(("c", at(ang - 1.7, 0.35), at(ang - 1.1, 0.95), R * 0.42))
        # 1) contorno, 2) sombra, 3) branco deslocado para cima e esquerda (volume)
        for sh in shapes:
            if sh[0] == "o":
                rr = sh[2] + ow
                d.ellipse([sh[1][0] - rr, sh[1][1] - rr, sh[1][0] + rr, sh[1][1] + rr], fill=LINE)
            else:
                self._cap(d, sh[1], sh[2], sh[3] + 2 * ow, LINE)
        for sh in shapes:
            if sh[0] == "o":
                rr = sh[2]
                d.ellipse([sh[1][0] - rr, sh[1][1] - rr, sh[1][0] + rr, sh[1][1] + rr], fill=GLOVE_SHADE)
            else:
                self._cap(d, sh[1], sh[2], sh[3], GLOVE_SHADE)
        ox_, oy_ = -R * 0.10, -R * 0.12
        for sh in shapes:
            if sh[0] == "o":
                rr = sh[2] * 0.80
                cx_, cy_ = sh[1][0] + ox_, sh[1][1] + oy_
                d.ellipse([cx_ - rr, cy_ - rr, cx_ + rr, cy_ + rr], fill=GLOVE)
            else:
                a, b = sh[1], sh[2]
                self._cap(d, (a[0] + ox_ * 0.5, a[1] + oy_ * 0.5), (b[0] + ox_ * 0.5, b[1] + oy_ * 0.5), sh[3] * 0.62, GLOVE)
        # dobras dos dedos no punho fechado
        lw = max(2, int(S * 0.006))
        if kind == "fist":
            for off in (-0.45, 0.0, 0.45):
                d.line([at(ang + off, 0.45), at(ang + off, 0.85)], fill=GLOVE_LINE, width=lw)
        elif kind == "point":
            for k_side in (0.15, 0.55):   # dedos dobrados ao lado do indicador
                d.line([lat(0.35, k_side), lat(0.90, k_side)], fill=GLOVE_LINE, width=lw)
        elif kind == "thumb":
            for k in range(1, 4):
                yk = c[1] - R * 0.72 + k * R * 0.40
                d.line([(c[0] - R * 0.05, yk), (c[0] + R * 0.78, yk)], fill=GLOVE_LINE, width=lw)

    def _pose_arms(self, d: ImageDraw.ImageDraw, pose: str, phase: float) -> None:
        P = self._p
        sh_l, sh_r = P(0.215, 0.955), P(0.785, 0.955)
        rest_l = (P(0.37, 1.22), 1.35, "open")     # mao esquerda apoiada, dedos para baixo (como no logo)
        rest_r = (P(0.63, 1.22), 1.80, "fist")

        def arm(sh, hand):
            pos, ang, kind = hand
            self._arm(d, sh, pos)
            self._glove(d, pos, ang, kind)

        one_arm = {
            "point_up": (P(0.97, 0.56), -math.pi / 2 + 0.45, "point"),
            "point_right": (P(1.03, 0.86), 0.0, "point"),
            "point_down": (P(0.98, 1.14), math.pi / 2 - 0.2, "point"),
            "explain": (P(1.00, 0.90 + 0.03 * math.sin(phase)), -1.15, "open"),
            "wave": (P(0.99 + 0.03 * math.sin(phase), 0.58 + 0.03 * math.cos(phase)), -math.pi / 2 + 0.25 * math.sin(phase), "open"),
            "thinking": (P(0.60, 0.88), -math.pi / 2, "fist"),
            "thumbs_up": (P(1.00, 0.80), -math.pi / 2, "thumb"),
        }
        if pose in one_arm:
            arm(sh_l, rest_l)
            arm(sh_r, one_arm[pose])
        elif pose == "shrug":
            arm(sh_l, (P(0.00, 0.82), -math.pi / 2 - 0.5, "open"))
            arm(sh_r, (P(1.00, 0.82), -math.pi / 2 + 0.5, "open"))
        elif pose == "arms_crossed":
            arm(sh_l, (P(0.66, 1.08), 0.1, "fist"))
            arm(sh_r, (P(0.34, 1.08), math.pi - 0.1, "fist"))
        elif pose == "hands_cheeks":
            arm(sh_l, (P(0.29, 0.60), -math.pi / 2 + 0.25, "open"))
            arm(sh_r, (P(0.71, 0.60), -math.pi / 2 - 0.25, "open"))
        elif pose == "clap":
            off = 0.03 * math.sin(phase)
            arm(sh_l, (P(0.46 - off, 1.02), -math.pi / 2 + 0.3, "open"))
            arm(sh_r, (P(0.54 + off, 1.02), -math.pi / 2 - 0.3, "open"))
        else:  # idle
            arm(sh_l, rest_l)
            arm(sh_r, rest_r)

    # ------------------------------------------------------------------ camada
    def _eye_geom(self, expression: str, pose: str):
        shape, ew, eh, tilt, big_side, half_side, _brow, look_y_e = EXPRESSIONS.get(expression, EXPRESSIONS["neutral"])
        cx = 0.5
        eye_y = 0.328 + LOOK_Y.get(pose, 0.0) + look_y_e
        look_x = LOOK_X.get(pose, 0.0)
        eyes = []
        for sx in (-1, 1):
            ex = cx + sx * 0.098 + look_x
            sc = 1.15 if big_side == sx else 1.0
            lid = -eh * 0.2 if half_side == sx else None
            eyes.append((sx, ex, eye_y, _eye_shape(shape, ew * sc, eh * sc), lid))
        return eyes, ew, tilt

    def _halo(self, eyes, ew: float, tilt: float, blink: bool, voice: float) -> tuple[Image.Image, int, int]:
        """Halo desfocado dos olhos no retangulo da regiao dos olhos, e o canto dele na camada."""
        S = self.S
        acc = self.accent
        gx0, gy0 = int((0.18 + self.X0) * S), int(0.18 * S)
        gw, gh = int(0.64 * S), int(0.34 * S)
        glow = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
        g = ImageDraw.Draw(glow)
        G = lambda pts: [((x + self.X0) * S - gx0, y * S - gy0) for x, y in pts]
        halo_a = int(90 + 50 * voice)
        for sx, ex, ey, pts, lid in eyes:
            if blink:
                g.line(G([(ex - ew, ey), (ex + ew, ey)]), fill=acc + (int(halo_a * 0.5),), width=int(S * 0.03))
                continue
            g.polygon(G(_place(pts, ex, ey, 1.55, -sx * tilt, lid)), fill=acc + (int(halo_a * 0.35),))
            g.polygon(G(_place(pts, ex, ey, 1.15, -sx * tilt, lid)), fill=acc + (halo_a,))
        return glow.filter(ImageFilter.GaussianBlur(S * 0.026)), gx0, gy0

    def _cores(self, d: ImageDraw.ImageDraw, eyes, ew: float, tilt: float, blink: bool, voice: float, dx: int = 0, dy: int = 0) -> None:
        """Nucleo solido dos olhos (dx, dy: canto da imagem de destino na camada)."""
        S = self.S
        acc = self.accent
        for sx, ex, ey, pts, lid in eyes:
            ang = -sx * tilt
            if blink:
                d.line([(ex + self.X0 - ew * 0.95) * S - dx, ey * S - dy, (ex + self.X0 + ew * 0.95) * S - dx, ey * S - dy], fill=_mix(acc, 0.35), width=max(2, int(S * 0.010)))
                continue
            d.polygon(_place(pts, (ex + self.X0) * S - dx, ey * S - dy, S, ang, lid), fill=acc)
            d.polygon(_place(pts, (ex + self.X0) * S - dx, ey * S - dy, S * 0.55, ang, lid), fill=_mix(acc, 0.55 + 0.2 * voice))

    def layer(self, expression: str, pose: str, blink: bool, phase_q: int) -> Image.Image:
        """Personagem inteiro com a voz em 0. O brilho que segue a voz vem de glow(), recorte pequeno dos olhos."""
        key = ("layer", expression, pose, blink, phase_q)
        hit = self._cget(key)
        if hit is not None:
            return hit
        img = self._base().copy()
        eyes, ew, tilt = self._eye_geom(expression, pose)
        glow, gx0, gy0 = self._halo(eyes, ew, tilt, blink, 0.0)
        img.alpha_composite(glow, (gx0, gy0))
        self._cores(ImageDraw.Draw(img), eyes, ew, tilt, blink, 0.0)
        self._pose_arms(ImageDraw.Draw(img), pose, phase_q / 4.0 * 2 * math.pi)
        return self._cput(key, img, img)

    def glow(self, expression: str, pose: str, mouth_q: int, blink: bool, mirror: bool = False) -> tuple[Image.Image, int, int] | None:
        """Brilho dos olhos com a voz em mouth_q (1-4): (recorte RGBA, x, y na camada) para colar sobre layer().
        O recorte leva so o que a voz acrescenta ao halo (alfa extra sobre fundo opaco) e os nucleos redesenhados.
        mirror: posicao e desenho para a camada espelhada. Voz em 0 devolve None."""
        if mouth_q <= 0:
            return None
        key = ("glow", expression, LOOK_X.get(pose, 0.0), LOOK_Y.get(pose, 0.0), blink, mouth_q, mirror)
        hit = self._cget(key)
        if hit is not None:
            return hit
        voice = mouth_q / 4.0
        eyes, ew, tilt = self._eye_geom(expression, pose)
        g0, gx0, gy0 = self._halo(eyes, ew, tilt, blink, 0.0)
        patch, _, _ = self._halo(eyes, ew, tilt, blink, voice)
        a0 = np.asarray(g0.getchannel("A"), np.float32) / 255.0
        av = np.asarray(patch.getchannel("A"), np.float32) / 255.0
        extra = np.clip((av - a0) / np.maximum(1.0 - a0, 1e-6), 0.0, 1.0)   # a0 + extra * (1 - a0) = av
        patch.putalpha(Image.fromarray((extra * 255.0 + 0.5).astype(np.uint8), "L"))
        self._cores(ImageDraw.Draw(patch), eyes, ew, tilt, blink, voice, gx0, gy0)
        box = patch.getchannel("A").getbbox()
        if box is None:
            return None
        patch = patch.crop(box)
        x, y = gx0 + box[0], gy0 + box[1]
        if mirror:
            patch, x = patch.transpose(Image.FLIP_LEFT_RIGHT), self.W - x - patch.width
        return self._cput(key, (patch, x, y), patch)

    @staticmethod
    def face(expression: str, pose: str) -> str:
        return expression if expression in EXPRESSIONS else POSE_FACE.get(pose, "neutral")

    def render(self, t: float, expression: str, pose: str, mouth_open: float, blink: bool) -> Image.Image:
        phase_q = int((t * 2.2) % 4) if pose in ANIMATED_POSES else 0   # pose parada: a fase nao muda o desenho
        return self.layer(self.face(expression, pose), pose, blink, phase_q)

    def voice_glow(self, expression: str, pose: str, mouth_open: float, blink: bool, mirror: bool = False) -> tuple[Image.Image, int, int] | None:
        mouth_q = int(min(max(mouth_open, 0.0), 1.0) * 4 + 0.5)
        return self.glow(self.face(expression, pose), pose, mouth_q, blink, mirror)


def blink_at(t: float, seed: float = 0.0) -> bool:
    period = 3.1
    phase = (t + seed * 1.7) % period
    return phase < 0.11


def entry_scale(dt: float, total: float, in_dur: float = 0.34, out_dur: float = 0.22) -> float:
    """Visivel ou nao (sem pop de escala: o corpo nao muda de tamanho)."""
    return 1.0 if 0 <= dt <= total else 0.0


def hop_scale(since_change: float) -> float:
    """Sem pulinho na troca de pose: so os bracos mudam."""
    return 1.0


def walk_in(dt: float, total: float, in_dur: float = 0.6, out_dur: float = 0.4) -> tuple[float, float, float, float, float]:
    """Entrada deslizando pela diagonal (do canto de baixo ate o lugar) e saida
    pelo mesmo caminho, sem passada, sem inclinacao e sem mudar de tamanho.
    Devolve (escala, dx, dy, inclinacao, passo); inclinacao e passo sempre 0."""
    if dt < 0 or dt > total:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    if dt < in_dur:
        e = 1.0 - (1.0 - dt / in_dur) ** 3       # ease-out: chega desacelerando
        return 1.0, -0.55 * (1.0 - e), 0.40 * (1.0 - e), 0.0, 0.0
    if total - dt < out_dur:
        e = ((total - dt) / out_dur) ** 2         # 1 -> 0
        return 1.0, -0.55 * (1.0 - e), 0.40 * (1.0 - e), 0.0, 0.0
    return 1.0, 0.0, 0.0, 0.0, 0.0


def flipped(layer: Image.Image, mascot: Mascot) -> Image.Image:
    """Camada espelhada (personagem entrando pela direita), no cache do personagem.
    A entrada guarda a camada original: id() reaproveitado por outra imagem nunca devolve sprite velho."""
    key = ("flip", id(layer))
    hit = mascot._cget(key)
    if hit is not None and hit[0] is layer:
        return hit[1]
    out = layer.transpose(Image.FLIP_LEFT_RIGHT)
    mascot._cput(key, (layer, out), layer, out)
    return out


def _posed(mascot: Mascot, layer: Image.Image, s: float, tilt: float) -> tuple[Image.Image, Image.Image]:
    """Camada redimensionada e girada, com a sombra (mascara L), no cache do personagem.
    A entrada guarda a camada de origem e confere a identidade, como flipped()."""
    key = ("posed", id(layer), round(s / 0.02), round(tilt / 0.5))
    hit = mascot._cget(key)
    if hit is not None and hit[0] is layer:
        return hit[1], hit[2]
    sq = key[2] * 0.02
    tq = key[3] * 0.5
    lay = layer
    if abs(sq - 1.0) > 0.005:
        lay = lay.resize((max(1, int(lay.width * sq)), max(1, int(lay.height * sq))), Image.BILINEAR)
    if abs(tq) > 0.01:
        lay = lay.rotate(tq, resample=Image.BILINEAR, expand=True)
    # sombra: desfoque em 1/4 da resolucao; guardada como mascara L (um quarto de um RGBA preto)
    q = 4
    small = lay.getchannel("A").resize((max(1, lay.width // q), max(1, lay.height // q)), Image.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(mascot.S * 0.03 / q)).point(lambda v: v * 110 // 255)
    shadow = small.resize(lay.size, Image.BILINEAR)
    mascot._cput(key, (layer, lay, shadow), layer, lay, shadow)
    return lay, shadow


def draw_mascot(frame: Image.Image, mascot: Mascot, layer: Image.Image, cx: float, cy: float, scale: float, t: float, hop: float = 1.0,
                tilt_extra: float = 0.0, bob_extra: float = 0.0, glow: tuple[Image.Image, int, int] | None = None) -> None:
    """Cola o personagem parado: sem balanco, sem inclinacao, sem pulo (so bracos e olhos mexem, dentro da camada).
    glow: recorte de Mascot.glow() (brilho da voz), colado por cima quando a camada vai sem mudar de tamanho."""
    if scale <= 0.02:
        return
    lay, shadow = _posed(mascot, layer, scale, 0.0)
    x = int(cx - lay.width / 2)
    y = int(cy - lay.height / 2)
    frame.paste(0, (x + int(mascot.S * 0.03), y + int(mascot.S * 0.04)), shadow)
    frame.paste(lay, (x, y), lay)
    if glow is not None and lay is layer:
        patch, gx, gy = glow
        frame.paste(patch, (x + gx, y + gy), patch)
