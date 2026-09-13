"""Motor de render: percorre o tempo quadro a quadro e entrega ao ffmpeg.

Ordem por quadro: base (foto com Ken Burns ou fundo do tema) -> transicao com
o plano anterior -> vinheta/grao -> overlays da cena (grafico, formas, numero,
punchline) -> zoom de impacto e tremor de camera -> personagem (com boca pela
voz) -> legenda -> selo -> flash -> mergulho a preto -> fade global.
A legenda fica fora do zoom e do tremor de proposito: ela nunca pode "pular".
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import SR, THEMES
from ..storyboard import Overlay, Storyboard
from ..visuals.code_rain import CodeRain
from ..visuals.procedural import Procedural
from . import cards, effects, shapes
from .mascot import Mascot, blink_at, draw_mascot, flipped, hop_scale, walk_in
from .typography import Typo, paste_center

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


class Engine:
    def __init__(self, sb: Storyboard, W: int, H: int, fps: int, voice: np.ndarray | None = None):
        self.sb, self.W, self.H, self.fps = sb, W, H, fps
        self.th = THEMES[sb.theme]
        self.typo = Typo(W, H, self.th, sb.seed)
        self.rng = np.random.default_rng(sb.seed)
        self._sources: dict[int, object] = {}
        self._proc = Procedural(self.th["procedural"], W, H, self.th, sb.seed)
        self._rain = CodeRain(W, H, sb.seed)   # fundo de toda cena sem imagem
        self.mascot = Mascot(int(W * 0.92), (0, 229, 255), self.th["accent"], max_bytes=(400 if W >= 1080 else 200) * 2**20)   # olhos sempre ciano; accent2 = cor do tema
        self.voice_env = self._envelope(voice) if voice is not None else None

    def _envelope(self, voice: np.ndarray) -> np.ndarray:
        """Abertura da boca por quadro: RMS da voz em janelas de 1/fps, normalizado."""
        n_frames = int(self.sb.duration * self.fps) + 2
        win = int(SR / self.fps)
        env = np.zeros(n_frames, np.float32)
        for f in range(n_frames):
            a = f * win
            seg = voice[a:a + win]
            if len(seg):
                env[f] = float(np.sqrt(np.mean(seg ** 2)))
        ref = float(np.percentile(env[env > 0], 92)) if np.any(env > 0) else 1.0
        env = np.clip((env / max(ref, 1e-6) - 0.07) / 0.6, 0.0, 1.0)
        # suaviza um pouco (boca nao treme) mas mantem ataque
        out = env.copy()
        for f in range(1, n_frames):
            out[f] = max(env[f], out[f - 1] * 0.55)
        return out

    # --------------------------------------------------------------- base
    def _source(self, i: int):
        if i in self._sources:
            return self._sources[i]
        sc = self.sb.scenes[i]
        src = None
        if sc.visual.get("type") == "image":
            try:
                img = Image.open(sc.visual["path"])
                src = effects.prepare_source(img, self.W, self.H, 1.45)
            except Exception:
                src = None
        self._sources[i] = src
        if len(self._sources) > 6:
            for k in list(self._sources)[:-4]:
                if k < i - 1:
                    self._sources.pop(k, None)
        return src

    def _blurred(self, path: str) -> Image.Image | None:
        """Foto desfocada e escurecida como fundo de cartao (cache por foto)."""
        if not hasattr(self, "_blur_cache"):
            self._blur_cache: dict[str, Image.Image] = {}
        if path in self._blur_cache:
            return self._blur_cache[path]
        try:
            img = effects.cover(Image.open(path).convert("RGB"), self.W // 2, self.H // 2)
            img = effects.blur(img, self.W * 0.012).resize((self.W, self.H), Image.BILINEAR)
            img = effects.fade_black(img, 0.55)
        except Exception:
            img = None
        if len(self._blur_cache) > 4:
            self._blur_cache.pop(next(iter(self._blur_cache)))
        self._blur_cache[path] = img
        return img

    def base_frame(self, i: int, t: float) -> Image.Image:
        sc = self.sb.scenes[i]
        if sc.visual.get("type") == "clip":
            n, fps = int(sc.visual.get("n", 1)), int(sc.visual.get("fps", 30))
            idx = int(max(0.0, t - sc.start) * fps) % max(n, 1) + 1
            try:
                frame = Image.open(Path(sc.visual["frames_dir"]) / f"f{idx:04d}.jpg").convert("RGB")
                if frame.size != (self.W, self.H):
                    frame = effects.cover(frame, self.W, self.H)
            except Exception:
                return self._rain_frame(sc, t)
            p = (t - sc.start) / max(sc.end - sc.start, 0.1)
            frame = effects.punch_zoom(frame, 1.0 + 0.07 * min(max(p, 0.0), 1.0), (0.5, 0.5))  # leve push-in no clipe
            return effects.vignette(frame, 0.45)
        if sc.visual.get("type") == "image_blur":
            frame = self._blurred(sc.visual["path"])
            if frame is None:
                return self._rain_frame(sc, t)
            p = (t - sc.start) / max(sc.end - sc.start, 0.1)
            return effects.punch_zoom(frame, 1.02 + 0.05 * min(max(p, 0.0), 1.0), (0.5, 0.5))
        src = self._source(i)
        if src is None:
            return self._rain_frame(sc, t)
        dur = max(sc.end - sc.start, 0.1)
        p = (t - sc.start) / dur
        frame = effects.kenburns(src, self.W, self.H, p, sc.zoom[0], sc.zoom[1], sc.pan[0], sc.pan[1])
        if self.th.get("grain", 0) > 0:
            frame = effects.grain(frame, self.th["grain"], self.rng)
        return effects.vignette(frame, 0.5)

    def _rain_frame(self, sc, t: float) -> Image.Image:
        """Sem imagem: chuva de codigo azul. Com o personagem no centro fica viva; atras de cartao, escurecida."""
        center = sc.visual.get("mascot") == "center"
        return self._rain.frame(t, center=center, dim=1.0 if center else 0.55)

    def scene_index(self, t: float) -> int:
        for i, sc in enumerate(self.sb.scenes):
            if t < sc.end:
                return i
        return len(self.sb.scenes) - 1

    # ----------------------------------------------------------- overlays
    def draw_overlay(self, frame: Image.Image, ov: Overlay, t: float) -> None:
        W, H, th = self.W, self.H, self.th
        P = ov.params
        t0 = P.get("t0", ov.start)
        rel = t - t0
        dur = P.get("t1", ov.end) - t0
        acc = th["accent"]
        if ov.kind == "punch":
            res = self.typo.punch(P["text"], P.get("style", th["punch_style"]), rel, dur, self.sb.seed + int(t0 * 10), P.get("max_w"), P.get("boxed", False))
            if res:
                layer, dx, dy, a = res
                sc = P.get("scale", 1.0)
                if sc != 1.0:   # etiqueta pequena (aparte "segue o perfil")
                    layer = layer.resize((max(1, int(layer.width * sc)), max(1, int(layer.height * sc))), Image.BILINEAR)
                paste_center(frame, layer, W * P.get("x", 0.5) + dx, H * P.get("y", 0.30) + dy, a)
        elif ov.kind == "stat":
            res = self.typo.stat(P["value"], P.get("label", ""), rel, dur, P.get("prefix", ""), P.get("suffix", ""), P.get("decimals", 0))
            if res:
                layer, dx, dy, a = res
                paste_center(frame, layer, W / 2 + dx, H * P.get("y", 0.34) + dy, a)
        elif ov.kind == "arrow":
            a = (P["from"][0] * W, P["from"][1] * H)
            b = (P["to"][0] * W, P["to"][1] * H)
            shapes.arrow(frame, a, b, rel / P.get("draw", 0.45), P.get("color", acc), max(4, int(W * P.get("width", 0.011))))
        elif ov.kind == "circle":
            c = (P["center"][0] * W, P["center"][1] * H)
            shapes.circle(frame, c, P.get("r", 0.16) * W, rel / P.get("draw", 0.5), P.get("color", acc), max(4, int(W * 0.010)), pulse=rel)
        elif ov.kind == "box":
            x0, y0, x1, y1 = P["box"]
            shapes.highlight_box(frame, (x0 * W, y0 * H, x1 * W, y1 * H), rel / P.get("draw", 0.5), P.get("color", acc), max(4, int(W * 0.009)))
        elif ov.kind == "chart":
            p_data = rel / max(P.get("data_dur", 2.0), 0.1)
            p_proj = (t - P.get("proj_start", t0 + 2.0)) / 0.8
            layer, cx, cy = shapes.chart(W, H, th, P["series"], P["projection"], p_data, p_proj, P.get("unit", ""), P.get("title", ""))
            a = min(1.0, rel / 0.25) * (1.0 if dur - rel > 0.25 else max(0.0, (dur - rel) / 0.25))
            paste_center(frame, layer, cx, cy, a)
        elif ov.kind == "label":
            layer = self.typo.small_label(P["text"], P.get("color"))
            a = min(1.0, rel / 0.3) * (1.0 if dur - rel > 0.3 else max(0.0, (dur - rel) / 0.3))
            paste_center(frame, layer, W * P.get("x", 0.5), H * P.get("y", 0.80), a)
        elif ov.kind == "headline":
            layer, cx, cy, a = cards.headline_layer(W, H, th, P.get("source", ""), P.get("title", ""), P.get("date", ""), rel, dur)
            paste_center(frame, layer, cx, cy, a)

    def draw_captions(self, frame: Image.Image, t: float) -> None:
        for line in self.sb.captions:
            if line.start <= t < line.end:
                active = -1
                for k, (_, ws, _) in enumerate(line.words):
                    if ws <= t + 0.02:
                        active = k
                words = tuple(w for w, _, _ in line.words)
                # mascote na tela: a legenda vai para a direita, mais estreita, fora do corpo dele
                beside = next((ov.params.get("side", "left") for ov in self.sb.mascots if ov.start <= t < ov.end), None)
                layer = self.typo.caption(words, active, line.emphasis, 0.52 if beside in ("left", "right") else 0.90)
                cx = self.W * ({"left": 0.72, "right": 0.28}.get(beside, 0.5))
                cx = min(max(cx, 24 + layer.width / 2), self.W - 24 - layer.width / 2)   # nunca sai pela borda
                paste_center(frame, layer, cx, self.H * (0.30 if beside == "center" else 0.70))   # centro: acima da cabeca
                return

    def draw_mascots(self, frame: Image.Image, t: float, f: int) -> None:
        for ov in self.sb.mascots:
            if not (ov.start - 0.05 <= t < ov.end + 0.05):
                continue
            P = ov.params
            dt = t - ov.start
            total = ov.end - ov.start
            s, wdx, wdy, wtilt, wstep = walk_in(dt, total)   # entra e sai andando pela diagonal
            if P.get("side") == "center":   # cena sem imagem: no centro, entra e sai com corte seco
                s, wdx, wdy, wtilt, wstep = (1.0 if 0.0 <= dt < total else 0.0), 0.0, 0.0, 0.0, 0.0
            if s <= 0.02:
                continue
            mouth = float(self.voice_env[f]) if self.voice_env is not None and f < len(self.voice_env) else 0.0
            expr, pose = P.get("expr", "neutral"), P.get("pose", "idle")
            seg_start = ov.start
            for seg_t, e, p in P.get("segments", []):
                if t >= seg_t - 0.05:
                    expr, pose, seg_start = e, p, seg_t
            blink = blink_at(t, self.sb.seed)
            right = P.get("side", "left") == "right"
            layer = self.mascot.render(t, expr, pose, mouth, blink)
            glow = self.mascot.voice_glow(expr, pose, mouth, blink, mirror=right)   # brilho da voz: recorte so dos olhos
            if right:   # entra pelo canto inferior direito, espelhado
                layer, wdx, wtilt = flipped(layer, self.mascot), -wdx, -wtilt
            x = self.W * (P.get("x", 0.20) + wdx)
            y = self.H * (P.get("y", 0.52) + wdy)
            hop = hop_scale(t - seg_start) if seg_start > ov.start + 0.1 else 1.0
            draw_mascot(frame, self.mascot, layer, x, y, s, t, hop, wtilt, wstep, glow)

    # ------------------------------------------------------------ quadro
    def frame_at(self, t: float, f: int = 0) -> Image.Image:
        sb = self.sb
        i = self.scene_index(t)
        sc = sb.scenes[i]
        frame = self.base_frame(i, t)
        if i > 0 and sc.transition != "cut" and sc.transition_dur > 0 and t < sc.start + sc.transition_dur:
            prev = self.base_frame(i - 1, t)
            p = (t - sc.start) / sc.transition_dur
            frame = effects.transition(prev, frame, p, sc.transition, self.rng)
        for ov in sc.overlays:
            if ov.start <= t < ov.end:
                self.draw_overlay(frame, ov, t)
        # zoom de impacto + tremor de camera (um so recorte)
        z, center = 1.0, [0.5, 0.5]
        for (t0, strength, c) in sb.punch_zooms:
            k = effects.punch_curve(t - t0)
            if k > 0:
                z = max(z, 1.0 + strength * k)
                center = list(c)
        for (t0, strength) in sb.shakes:
            dt = t - t0
            if 0 <= dt < 0.4:
                k = (1 - dt / 0.4) ** 2 * strength
                z = max(z, 1.035)
                center[0] += float(self.rng.uniform(-k, k))
                center[1] += float(self.rng.uniform(-k, k))
        if z > 1.001:
            frame = effects.punch_zoom(frame, z, (min(max(center[0], 0.3), 0.7), min(max(center[1], 0.3), 0.7)))
        self.draw_mascots(frame, t, f)
        self.draw_captions(frame, t)
        if sb.badge:
            b = self.typo.badge(sb.badge)
            frame.paste(b, (int(self.W * 0.06), int(self.H * 0.105)), b)
        for tf in sb.flashes:
            if 0 <= t - tf < 0.14:
                frame = effects.flash_white(frame, (1 - (t - tf) / 0.14) * 0.9)
        for td in sb.dips:
            if abs(t - td) < 0.18:
                frame = effects.fade_black(frame, (1 - abs(t - td) / 0.18) ** 1.5)
        if t < sb.fade_in:
            frame = effects.fade_black(frame, 1 - t / sb.fade_in)
        if sb.duration - t < sb.fade_out:
            frame = effects.fade_black(frame, 1 - max(0.0, sb.duration - t) / sb.fade_out)
        return frame

    # ------------------------------------------------------------- render
    def render(self, audio_path: Path, out_path: Path, crf: int = 23, preset: str = "veryfast", contact: bool = True, log=print) -> Path:
        W, H, fps = self.W, self.H, self.fps
        n_frames = int(self.sb.duration * fps)
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0",
               "-i", str(audio_path), "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-threads", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
               "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path)]
        # maquina fraca: encoder com 2 threads e prioridade abaixo do normal (no Windows) para nao travar o resto
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        sheet_idx = set(int(n_frames * (k + 0.5) / 12) for k in range(12))
        sheet: list[Image.Image] = []
        t_start = time.time()
        last_pct = -1
        try:
            for f in range(n_frames):
                t = f / fps
                frame = self.frame_at(t, f)
                proc.stdin.write(frame.tobytes())
                if f in sheet_idx:
                    sheet.append(frame.resize((W // 4, H // 4), Image.BILINEAR))
                pct = int(100 * f / max(n_frames, 1))
                if pct // 10 != last_pct // 10:
                    last_pct = pct
                    log(f"  render {pct:3d}%  {f}/{n_frames} quadros  {time.time() - t_start:.0f}s")
        finally:
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", "ignore")
            proc.wait()
            self.mascot.clear()   # camadas do personagem nao passam para o proximo video
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg falhou: " + err[-800:])
        if contact and sheet:
            cols = 4
            rows = (len(sheet) + cols - 1) // cols
            cw, ch = sheet[0].size
            grid = Image.new("RGB", (cw * cols, ch * rows), (0, 0, 0))
            for k, im in enumerate(sheet):
                grid.paste(im, ((k % cols) * cw, (k // cols) * ch))
            grid.save(out_path.with_name(out_path.stem + "_contato.jpg"), quality=82)
        log(f"  pronto: {out_path.name} em {time.time() - t_start:.0f}s")
        return out_path
