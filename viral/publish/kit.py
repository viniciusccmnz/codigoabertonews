"""Kit de postagem de cada video: capa e textos separados para TikTok e Instagram.

Pedido do dono em 13/09/2026: "miniaturas pro video, junto com legenda, titulo e hashtags, tudo separado
para instagram e tiktok". Ao lado do mp4 ficam:
  NN-slug.capa-tiktok.jpg / NN-slug.capa-instagram.jpg   (1080x1920)
  NN-slug.tiktok.txt / NN-slug.instagram.txt              (TITULO, LEGENDA, HASHTAGS)
e o NN-slug.txt (lido pelo `publish`) passa a ter o titulo, a legenda e as hashtags do TikTok.

A API do TikTok nao aceita capa enviada por arquivo: no modo rascunho (inbox) o dono escolhe a capa no app.
Legenda nunca tem palavrao (a voz tem, com piii; texto escrito sem censura derruba alcance).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .. import ai
from ..render.fonts import font
from ..script.zoeira import SWEAR_RE
from ..util.text import strip_accents

W, H = 1080, 1920
CHANNEL = "CÓDIGO ABERTO NEWS"
YELLOW, WHITE, BLUE = (255, 212, 0), (255, 255, 255), (63, 169, 255)
BANNED_TAGS = {"fyp", "foryou", "foryoupage", "viral", "paravoce", "fy", "xyzbca", "trend"}

SYSTEM = "Você escreve textos de postagem para um canal brasileiro de curiosidades de tecnologia. Responde apenas JSON."
RULES = """Com base no roteiro abaixo, escreva os textos de postagem do vídeo.

TikTok:
- "titulo": até 60 caracteres, começa pela palavra que a pessoa buscaria (o assunto), sem clickbait mentiroso.
- "legenda": 1 ou 2 frases curtas que dão vontade de ver até o fim, terminando com uma pergunta para comentar.
- "hashtags": 4 hashtags do assunto (nada de #fyp, #viral, #foryou).

Instagram (Reels):
- "titulo": até 60 caracteres, vai na primeira linha da legenda.
- "legenda": 3 a 5 frases: gancho, o fato principal, por que importa, e um pedido para salvar ou mandar para um amigo.
- "hashtags": 5 hashtags do assunto (limite do Instagram).

Capa (texto grande em cima da imagem):
- "capa": 3 a 6 palavras fortes que resumem o vídeo, sem ponto final.
- "destaque": a palavra da capa que vai em amarelo (a mais forte).

Regras:
1. Só fatos que estão no roteiro. Não invente número, data, nome ou lugar.
2. Sem palavrão, sem emoji, sem "amanhã".
3. Português do Brasil, jeito leve de falar com amigo.

Formato: {"tiktok": {"titulo": "", "legenda": "", "hashtags": []}, "instagram": {"titulo": "", "legenda": "", "hashtags": []}, "capa": "", "destaque": ""}"""


def _narration(meta: dict) -> str:
    beats = (meta.get("script") or {}).get("beats") or []
    text = " ".join(b.get("text", "") for b in beats)
    return re.sub(r"\s+", " ", re.sub(r"\[\w+\]", "", text)).strip()


def _tags(raw, n: int) -> list[str]:
    out = []
    for t in raw if isinstance(raw, list) else str(raw or "").split():
        t = re.sub(r"[^a-z0-9]", "", strip_accents(str(t)).lower())
        if 2 < len(t) <= 30 and t not in BANNED_TAGS and "#" + t not in out:
            out.append("#" + t)
    return out[:n]


def _no_emoji(s: str) -> str:
    return re.sub("[\U00010000-\U0010FFFF☀-➿️]", "", str(s or "")).strip()


def _ok(text: str, script_text: str) -> bool:
    if SWEAR_RE.search(text) or re.search(r"amanh[ãa]", text, re.I):
        return False
    nums = set(re.findall(r"\d+", script_text))
    return all(n in nums for n in re.findall(r"\d+", text))


def texts(meta: dict, log=print) -> dict:
    """Titulo, legenda e hashtags por rede, mais o texto da capa. Sem IA, cai na legenda do roteiro."""
    story = _narration(meta)
    base_tags = _tags(meta.get("hashtags") or re.findall(r"#\w+", meta.get("caption", "")), 5)
    caption = re.sub(r"\s*#\w+", "", meta.get("caption", "")).strip()
    title = meta.get("title", "")
    fallback = {
        "tiktok": {"titulo": title[:60], "legenda": caption, "hashtags": base_tags[:4]},
        "instagram": {"titulo": title[:60], "legenda": caption, "hashtags": base_tags[:5]},
        "capa": " ".join(title.split()[:5]), "destaque": "",
    }
    for attempt in range(2):
        data = ai.chat_json(SYSTEM, RULES + ("\n\n(Tentativa nova: siga as regras 1 e 2 à risca.)" if attempt else "")
                            + "\n\nTÍTULO DO TEMA: " + title + "\n\nROTEIRO:\n" + story,
                            max_tokens=700, temperature=0.6 + 0.2 * attempt, log=log)
        if not isinstance(data, dict):
            break
        try:
            out = {"capa": _no_emoji(data.get("capa", "")).rstrip(".!"), "destaque": _no_emoji(data.get("destaque", ""))}
            for net, n in (("tiktok", 4), ("instagram", 5)):
                d = data.get(net) or {}
                out[net] = {"titulo": _no_emoji(d.get("titulo", ""))[:70], "legenda": _no_emoji(d.get("legenda", "")),
                            "hashtags": _tags(d.get("hashtags"), n) or base_tags[:n]}
        except (AttributeError, TypeError):
            continue
        written = " ".join([out["capa"]] + [out[k][f] for k in ("tiktok", "instagram") for f in ("titulo", "legenda")])
        if all([out["capa"], out["tiktok"]["legenda"], out["instagram"]["legenda"]]) and 2 <= len(out["capa"].split()) <= 7 \
                and _ok(written, story + " " + title):
            return out
        log("  kit: texto recusado (palavrao, numero fora do roteiro ou capa ruim)")
    log("  kit: usando a legenda do roteiro")
    return fallback


# ---------- capa ----------

def _busy(im: Image.Image) -> float:
    """Fracao de bordas fortes: print de tela e foto com letreiro dao muita borda e brigam com o texto da capa."""
    g = ImageOps.fit(im.convert("L"), (180, 320)).filter(ImageFilter.FIND_EDGES)
    return sum(1 for v in g.getdata() if v > 48) / (180 * 320)


def _background(images: list[str], mp4: Path) -> Image.Image:
    # entre as primeiras fotos do video, a mais limpa (em 13/09 um print do perfil do Instagram virou capa)
    best = None
    for p in images[:8]:
        try:
            im = Image.open(p).convert("RGB")
        except OSError:
            continue
        if min(im.size) < 400:
            continue
        score = _busy(im) + (0.05 if min(im.size) < 700 else 0.0)
        if best is None or score < best[0]:
            best = (score, im)
    if best:
        return ImageOps.fit(best[1], (W, H), Image.LANCZOS, centering=(0.5, 0.45)).filter(ImageFilter.GaussianBlur(3))
    # video antigo sem foto guardada: quadro do video, bem desfocado (some a legenda queimada)
    tmp = mp4.with_suffix(".frame.jpg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "8", "-i", str(mp4), "-frames:v", "1", str(tmp)], check=False)
    try:
        im = ImageOps.fit(Image.open(tmp).convert("RGB"), (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(22))
    except OSError:
        im = Image.new("RGB", (W, H), (12, 18, 30))
    tmp.unlink(missing_ok=True)
    return im


def _shade(im: Image.Image) -> Image.Image:
    im = Image.eval(im, lambda v: int(v * 0.48))
    grad = Image.linear_gradient("L").resize((W, H))            # 0 em cima, 255 embaixo
    edge = Image.eval(grad, lambda v: int(abs(v - 128) * 1.5))  # escuro nas pontas, claro no meio
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, edge.point(lambda v: min(200, v)))


def _layout(words: list[str], max_w: int) -> tuple[int, list[list[str]]]:
    for size in range(170, 70, -6):
        f = font("heavy", size)
        lines, cur = [], []
        for w in words:
            trial = " ".join(cur + [w])
            if cur and f.getlength(trial) > max_w:
                lines.append(cur)
                cur = [w]
            else:
                cur.append(w)
        lines.append(cur)
        if len(lines) <= 4 and all(f.getlength(" ".join(l)) <= max_w for l in lines):
            return size, lines
    return 70, [words]


def cover(images: list[str], mp4: Path, text: str, highlight: str, out: Path, center_y: float) -> Path:
    im = _shade(_background(images, mp4))
    d = ImageDraw.Draw(im)
    words = text.upper().split()
    key = strip_accents(highlight.upper())
    size, lines = _layout(words, 900)
    f = font("heavy", size)
    lh = int(size * 1.08)
    y = int(H * center_y - lh * len(lines) / 2)
    stroke = max(6, size // 14)
    for line in lines:
        x = (W - f.getlength(" ".join(line))) / 2
        for w in line:
            color = YELLOW if key and strip_accents(w.strip(",.!?:")) == key else WHITE
            d.text((x, y), w, font=f, fill=color, stroke_width=stroke, stroke_fill=(0, 0, 0))
            x += f.getlength(w + " ")
        y += lh
    # selo do canal logo abaixo do texto, dentro do recorte 4:5 do perfil
    tf = font("mono", 40)
    tw = tf.getlength(CHANNEL)
    ty = y + 40
    d.rounded_rectangle(((W - tw) / 2 - 28, ty - 14, (W + tw) / 2 + 28, ty + 62), radius=18, fill=(8, 12, 22), outline=BLUE, width=4)
    d.text(((W - tw) / 2, ty), CHANNEL, font=tf, fill=BLUE)
    im.save(out, quality=92)
    return out


def _short_credit(line: str) -> str:
    """Descricao enxuta (dono 13/09): Pexels nao exige credito e 20 nomes poluiam a descricao. Fica so o que a
    licenca exige (Commons CC BY/BY-SA, Wikipedia). Idempotente: o .txt reescrito passa por aqui de novo."""
    if not line.lower().startswith("imagens"):
        return line
    items = [x.strip() for x in line.split(":", 1)[1].split(";") if x.strip()]
    keep = [re.sub(r"^Commons:\s*", "", x) for x in items if not x.lower().startswith("pexels")]
    return ("Imagens: " + "; ".join(keep)) if keep else ""


def _txt(net: str, t: dict, extras: list[str]) -> str:
    return (f"{net.upper()}\n\nTÍTULO\n{t['titulo']}\n\nLEGENDA\n{t['legenda']}\n\nHASHTAGS\n{' '.join(t['hashtags'])}\n"
            + ("\n" + "\n".join(extras) + "\n" if extras else ""))


def build(mp4: Path, meta: dict | None = None, images: list[str] | None = None, log=print) -> dict:
    """Gera capas e textos ao lado do mp4 e reescreve o .txt que o `publish` le."""
    mp4 = Path(mp4)
    meta = meta or json.loads(mp4.with_suffix(".json").read_text("utf-8"))
    images = images or meta.get("cover_images") or []
    t = texts(meta, log)
    stem = mp4.with_suffix("")
    # fonte e creditos ficam como estavam no .txt original
    old = mp4.with_suffix(".txt")
    extras = [_short_credit(_no_emoji(l)) for l in (old.read_text("utf-8").splitlines() if old.exists() else [])
              if l.lower().startswith(("fonte:", "imagens:", "música:", "musica:"))]
    extras = [l for l in extras if l]
    for net in ("tiktok", "instagram"):
        Path(f"{stem}.{net}.txt").write_text(_txt(net, t[net], extras), "utf-8")
    tk = t["tiktok"]
    old.write_text(tk["titulo"] + "\n" + tk["legenda"] + "\n\n" + " ".join(tk["hashtags"]) + ("\n\n" + "\n".join(extras) if extras else ""), "utf-8")
    capa_tt = cover(images, mp4, t["capa"], t["destaque"], Path(f"{stem}.capa-tiktok.jpg"), 0.40)
    capa_ig = cover(images, mp4, t["capa"], t["destaque"], Path(f"{stem}.capa-instagram.jpg"), 0.47)
    log(f"  kit: capa \"{t['capa']}\", textos TikTok e Instagram")
    return {"texts": t, "covers": [capa_tt.name, capa_ig.name]}


def build_day(folder: Path, log=print) -> int:
    n = 0
    for mp4 in sorted(Path(folder).glob("*.mp4")):
        if mp4.with_suffix(".json").exists():
            log(mp4.name)
            build(mp4, log=log)
            n += 1
    return n
