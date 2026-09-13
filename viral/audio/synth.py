"""Trilha sonora e efeitos sonoros sintetizados com numpy.

Nada aqui vem de arquivo de terceiro: cada som e gerado por formula, entao nao
existe direito autoral envolvido e nenhum video depende de biblioteca externa.
A trilha muda por tema e por assunto (synthwave, lo-fi, cinematografica, tensa,
arpejo, suspense, urgente) e a semente controla variacoes para dois videos nao
soarem iguais. Cada estilo tem um motivo curto que repete (o gancho), bumbo com
corpo e sidechain para a batida respirar; "suspense" e "urgent" aceitam os tempos
da virada do roteiro para alinhar riser e impacto grave.
"""
from __future__ import annotations

import zlib

import numpy as np

from ..config import SR

TWO_PI = 2.0 * np.pi


# ----------------------------------------------------------------- utilidades
def _t(n: int) -> np.ndarray:
    return np.arange(n, dtype=np.float32) / SR


def _n(sec: float) -> int:
    return max(1, int(sec * SR))


def env_ad(n: int, attack: float, decay: float, curve: float = 5.0) -> np.ndarray:
    t = _t(n)
    a = np.clip(t / max(attack, 1e-4), 0.0, 1.0)
    d = np.exp(-curve * np.clip((t - attack) / max(decay, 1e-4), 0.0, None))
    return (a * d).astype(np.float32)


def env_adsr(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    t = _t(n)
    dur = n / SR
    env = np.full(n, s, dtype=np.float32)
    att = t < a
    env[att] = t[att] / max(a, 1e-4)
    dec = (t >= a) & (t < a + d)
    env[dec] = 1.0 - (1.0 - s) * (t[dec] - a) / max(d, 1e-4)
    rel = t >= dur - r
    env[rel] = np.minimum(env[rel], s * np.clip((dur - t[rel]) / max(r, 1e-4), 0.0, 1.0))
    return env


def osc(freq: float, n: int, wave: str = "sine", harmonics: int = 10, detune: float = 0.0) -> np.ndarray:
    t = _t(n)
    if wave == "sine":
        return np.sin(TWO_PI * freq * t).astype(np.float32)
    if wave == "triangle":
        out = np.zeros(n, np.float32)
        for i, k in enumerate(range(1, harmonics * 2, 2)):
            if freq * k > SR / 2.2:
                break
            out += ((-1) ** i) * (np.sin(TWO_PI * freq * k * t) / (k * k)).astype(np.float32)
        return out * (8 / np.pi ** 2)
    if wave == "square":
        out = np.zeros(n, np.float32)
        for k in range(1, harmonics * 2, 2):
            if freq * k > SR / 2.2:
                break
            out += (np.sin(TWO_PI * freq * k * t) / k).astype(np.float32)
        return out * (4 / np.pi)
    if wave == "supersaw":
        return (osc(freq * (1 - detune), n, "saw", harmonics) + osc(freq, n, "saw", harmonics)
                + osc(freq * (1 + detune), n, "saw", harmonics)) / 3.0
    # saw aditivo, ja limitado em banda pelo numero de harmonicos
    out = np.zeros(n, np.float32)
    for k in range(1, harmonics + 1):
        if freq * k > SR / 2.2:
            break
        out += (np.sin(TWO_PI * freq * k * t) / k).astype(np.float32)
    return out * (2 / np.pi)


def lowpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    n = len(x)
    if n < 16:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mask = 1.0 / np.sqrt(1.0 + (f / max(cutoff, 20.0)) ** (2 * order))
    return np.fft.irfft(X * mask, n).astype(np.float32)


def highpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    n = len(x)
    if n < 16:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mask = 1.0 / np.sqrt(1.0 + (max(cutoff, 20.0) / np.maximum(f, 1e-3)) ** (2 * order))
    return np.fft.irfft(X * mask, n).astype(np.float32)


def sweep_filter(x: np.ndarray, f_start: float, f_end: float, bandwidth: float = 1.2, chunk: int = 1024) -> np.ndarray:
    """Passa-banda cujo centro desliza de f_start a f_end ao longo do som.
    Feito por blocos com janela Hann e overlap-add. Base do whoosh e do riser."""
    n = len(x)
    hop = chunk // 2
    win = np.hanning(chunk).astype(np.float32)
    out = np.zeros(n + chunk, np.float32)
    pad = np.concatenate([x, np.zeros(chunk, np.float32)])
    f = np.fft.rfftfreq(chunk, 1.0 / SR)
    pos = 0
    while pos < n:
        p = pos / max(n, 1)
        center = f_start * (f_end / f_start) ** p
        seg = pad[pos:pos + chunk] * win
        S = np.fft.rfft(seg)
        mask = np.exp(-((np.log(np.maximum(f, 1.0)) - np.log(center)) ** 2) / (2 * (bandwidth / 3) ** 2))
        out[pos:pos + chunk] += np.fft.irfft(S * mask, chunk).astype(np.float32) * win
        pos += hop
    return out[:n]


def lowpass_sweep(x: np.ndarray, f_start: float, f_end: float, chunk: int = 2048, order: int = 2) -> np.ndarray:
    """Passa-baixas cujo corte desliza (em escala exponencial) de f_start a f_end
    ao longo do som: e o pad "abrindo devagar". Blocos com janela Hann e 75% de
    sobreposicao, para a soma das janelas ao quadrado ser constante (sem tremor)."""
    n = len(x)
    if n < chunk * 2:
        return lowpass(x, (f_start + f_end) / 2.0, order)
    hop = chunk // 4
    win = np.hanning(chunk).astype(np.float32)
    out = np.zeros(n + chunk, np.float32)
    pad = np.concatenate([x, np.zeros(chunk, np.float32)])
    f = np.fft.rfftfreq(chunk, 1.0 / SR)
    pos = 0
    while pos < n:
        cutoff = f_start * (f_end / f_start) ** (pos / n)
        mask = 1.0 / np.sqrt(1.0 + (f / max(cutoff, 20.0)) ** (2 * order))
        seg = pad[pos:pos + chunk] * win
        out[pos:pos + chunk] += np.fft.irfft(np.fft.rfft(seg) * mask, chunk).astype(np.float32) * win
        pos += hop
    return (out[:n] / 1.5).astype(np.float32)


def delay(x: np.ndarray, seconds: float, feedback: float = 0.35, taps: int = 3) -> np.ndarray:
    out = x.copy()
    d = _n(seconds)
    g = feedback
    for i in range(1, taps + 1):
        shift = d * i
        if shift >= len(x):
            break
        out[shift:] += x[:-shift] * g
        g *= feedback
    return out


def reverb(x: np.ndarray, decay: float = 1.4, mix: float = 0.25, tone: float = 3500.0, seed: int = 7) -> np.ndarray:
    """Reverb por convolucao com resposta sintetica (ruido decaindo, filtrado)."""
    if mix <= 0 or len(x) < SR // 4:
        return x
    rng = np.random.default_rng(seed)
    n_ir = _n(decay * 2.5)
    t = _t(n_ir)
    ir = rng.standard_normal(n_ir).astype(np.float32) * np.exp(-t / decay * 3.0)
    ir = lowpass(ir, tone)
    ir[: _n(0.01)] *= np.linspace(0, 1, _n(0.01))
    ir /= (np.sqrt(np.sum(ir ** 2)) + 1e-6) * 3.0
    n = len(x) + n_ir
    size = 1 << (n - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)].astype(np.float32)
    return x * (1 - mix) + wet * mix


def glue(x: np.ndarray, thresh: float = 0.35, ratio: float = 2.5, block: float = 0.03, smooth: float = 0.12) -> np.ndarray:
    """Compressao de cola simples: reduz picos acima do limiar em blocos suavizados."""
    n = len(x)
    w = max(1, _n(block))
    m = n // w
    if m < 2:
        return x
    rms = np.sqrt(np.mean(x[: m * w].reshape(m, w) ** 2, axis=1) + 1e-9)
    gain = np.where(rms > thresh, (thresh + (rms - thresh) / ratio) / rms, 1.0).astype(np.float32)
    g = np.repeat(gain, w)
    g = np.concatenate([g, np.full(n - len(g), g[-1], np.float32)])
    k = max(1, _n(smooth))
    g = np.convolve(g, np.ones(k, np.float32) / k, mode="same")
    return (x * g).astype(np.float32)


def normalize(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return (x / m * peak).astype(np.float32) if m > 1e-6 else x.astype(np.float32)


def soft_clip(x: np.ndarray, drive: float = 1.0) -> np.ndarray:
    return np.tanh(x * drive).astype(np.float32)


# --------------------------------------------------------------- percussao
def kick(rng: np.random.Generator, dur: float = 0.42, f0: float = 160.0, f1: float = 42.0, punch: float = 1.0, body: float = 0.45) -> np.ndarray:
    """Bumbo: varredura de f0 a f1 com um corpo (cauda mais longa na fundamental)
    para o grave encher; a saturacao final poe harmonicos que o celular reproduz."""
    n = _n(dur)
    t = _t(n)
    f = f1 + (f0 - f1) * np.exp(-t * 28.0)
    ph = TWO_PI * np.cumsum(f) / SR
    x = np.sin(ph) * (np.exp(-t * 7.5) + body * np.exp(-t * 4.2))
    click = rng.standard_normal(_n(0.004)).astype(np.float32) * 0.5
    x[: len(click)] += click
    return soft_clip(x * 1.6 * punch)


def tick(rng: np.random.Generator, low: bool = False) -> np.ndarray:
    """Tique seco de relogio (tique/taque): estalo curto de ruido agudo mais um clique senoidal."""
    n = _n(0.03)
    t = _t(n)
    noise = highpass(rng.standard_normal(n).astype(np.float32), 4000.0) * np.exp(-t * 260.0)
    click = np.sin(TWO_PI * (1900.0 if low else 2600.0) * t) * np.exp(-t * 180.0)
    return normalize(noise * 0.5 + click * 0.55, 0.6)


def heartbeat(rng: np.random.Generator) -> np.ndarray:
    """Batida de coracao: dois pulsos graves abafados, o segundo mais fraco 0.19 s depois."""
    one = kick(rng, 0.3, 95.0, 46.0, 0.8, 0.3)
    n = _n(0.62)
    out = np.zeros(n, np.float32)
    out[: len(one)] += one
    j = _n(0.19)
    seg = one[: n - j]
    out[j:j + len(seg)] += seg * 0.62
    return normalize(lowpass(out, 220.0), 0.9)


def snare(rng: np.random.Generator, dur: float = 0.22, tone: float = 185.0) -> np.ndarray:
    n = _n(dur)
    t = _t(n)
    noise = rng.standard_normal(n).astype(np.float32)
    noise = highpass(noise, 1800.0) * np.exp(-t * 16.0)
    body = np.sin(TWO_PI * tone * t) * np.exp(-t * 22.0)
    return soft_clip(noise * 0.9 + body * 0.6)


def clap(rng: np.random.Generator) -> np.ndarray:
    n = _n(0.3)
    t = _t(n)
    noise = highpass(rng.standard_normal(n).astype(np.float32), 1200.0)
    env = np.zeros(n, np.float32)
    for k in range(3):
        s = _n(0.011 * k)
        seg = np.exp(-(t[: n - s]) * 90.0)
        env[s:] = np.maximum(env[s:], seg)
    env = np.maximum(env, np.exp(-t * 14.0) * 0.8)
    return soft_clip(noise * env * 0.9)


def hat(rng: np.random.Generator, dur: float = 0.05, open_: bool = False) -> np.ndarray:
    n = _n(0.28 if open_ else dur)
    t = _t(n)
    noise = highpass(rng.standard_normal(n).astype(np.float32), 6500.0)
    return (noise * np.exp(-t * (9.0 if open_ else 70.0)) * 0.5).astype(np.float32)


def boom(rng: np.random.Generator, dur: float = 1.6) -> np.ndarray:
    n = _n(dur)
    t = _t(n)
    f = 34.0 + 60.0 * np.exp(-t * 6.0)
    ph = TWO_PI * np.cumsum(f) / SR
    x = np.sin(ph) * np.exp(-t * 2.2)
    rumble = lowpass(rng.standard_normal(n).astype(np.float32), 120.0) * np.exp(-t * 3.0) * 0.6
    return soft_clip((x + rumble) * 1.4)


# ----------------------------------------------------------------- notas
CHORDS = {
    "m": [0, 3, 7], "M": [0, 4, 7], "m7": [0, 3, 7, 10], "M7": [0, 4, 7, 11],
    "sus2": [0, 2, 7], "dim": [0, 3, 6], "m9": [0, 3, 7, 10, 14],
}
PROGRESSIONS = {
    "synthwave": [(0, "m"), (8, "M"), (3, "M"), (10, "M")],
    "lofi": [(0, "m9"), (5, "m7"), (10, "M7"), (3, "M7")],
    "cinematic": [(0, "m"), (5, "m"), (8, "M"), (7, "sus2")],
    "tense": [(0, "m"), (1, "M"), (0, "m"), (10, "dim")],
    "arp": [(0, "M"), (9, "m"), (5, "M"), (7, "M")],           # I vi IV V: alegre, curioso
    "suspense": [(0, "m"), (0, "m9"), (8, "M"), (1, "M")],     # i i b6 b2: escuro, thriller
    "urgent": [(0, "m"), (10, "M"), (8, "M"), (10, "M")],      # i b7 b6 b7: noticia quente
}
STYLES = tuple(PROGRESSIONS)

# Motivo curto que repete (o gancho da trilha): (posicao em tempos, semitons acima
# da referencia, duracao em tempos). A referencia e a tonica da faixa, nao o acorde
# da vez, para a frase voltar sempre igual e grudar.
MOTIFS = {
    "synthwave": [(0, 12, .5), (.5, 15, .5), (1, 19, 1), (2, 17, .5), (2.5, 15, .5), (3, 12, 1), (4, 19, .75), (4.75, 17, .75), (5.5, 15, 1), (6.5, 10, 1.5)],
    "lofi": [(0, 7, 1.5), (1.5, 10, 1), (3, 12, 1), (4.5, 10, 1.5), (6, 7, 2)],
    "cinematic": [(0, 12, 3), (3, 15, 1.5), (4.5, 10, 3.5)],
    "tense": [(0, 12, .5), (.75, 13, .5), (1.5, 12, .5), (3, 15, 1), (4, 12, .5), (4.75, 13, .5), (5.5, 12, .5), (7, 10, 1)],
    "arp": [(0, 12, .5), (.5, 16, .5), (1, 19, .5), (1.5, 21, 1), (3, 19, .5), (3.5, 16, .5), (4, 12, 1.5), (6, 19, .5), (6.5, 24, 1.5)],
    "suspense": [(0, 12, 1.5), (2, 15, 1), (3.5, 13, 2.5), (6.5, 12, 1.5)],
    "urgent": [(0, 12, .25), (.5, 12, .25), (.75, 15, .25), (1.5, 17, .25), (2, 12, .25), (2.5, 12, .25), (2.75, 19, .25), (3.5, 15, .25),
               (4, 12, .25), (4.5, 12, .25), (4.75, 15, .25), (5.5, 17, .25), (6, 19, .25), (6.5, 17, .25), (6.75, 15, .25), (7.5, 10, .5)],
}


def freq_of(root: float, semitones: float) -> float:
    return root * 2.0 ** (semitones / 12.0)


def pluck(freq: float, dur: float, bright: float = 0.5) -> np.ndarray:
    n = _n(dur)
    x = osc(freq, n, "sine") * (1 - bright) + osc(freq, n, "triangle", 6) * bright
    x += osc(freq * 2, n, "sine") * 0.25 * bright
    return (x * env_ad(n, 0.004, dur * 0.7, 6.0)).astype(np.float32)


def keys_note(freq: float, dur: float) -> np.ndarray:
    """Timbre de piano eletrico: senoide com harmonicos e tremolo leve."""
    n = _n(dur)
    t = _t(n)
    x = osc(freq, n, "sine") + 0.35 * osc(freq * 2, n, "sine") + 0.12 * osc(freq * 3, n, "sine")
    trem = 1.0 - 0.12 * (0.5 + 0.5 * np.sin(TWO_PI * 5.2 * t))
    return (x * trem * env_adsr(n, 0.01, 0.6, 0.45, 0.25)).astype(np.float32)


def pad_chord(freqs: list[float], dur: float, cutoff: float = 1400.0, detune: float = 0.006) -> np.ndarray:
    n = _n(dur)
    x = np.zeros(n, np.float32)
    for f in freqs:
        x += osc(f, n, "supersaw", 9, detune)
    x = lowpass(x / max(len(freqs), 1), cutoff)
    return (x * env_adsr(n, min(0.6, dur * 0.3), 0.3, 0.8, min(0.5, dur * 0.25))).astype(np.float32)


def bass_note(freq: float, dur: float, wave: str = "saw", cutoff: float = 500.0) -> np.ndarray:
    n = _n(dur)
    x = osc(freq, n, wave, 8) * 0.7 + osc(freq, n, "sine") * 0.6
    x = lowpass(x, cutoff)
    return (x * env_adsr(n, 0.005, 0.1, 0.8, min(0.08, dur * 0.3))).astype(np.float32)


# ------------------------------------------------------------------ trilha
class _Track:
    def __init__(self, seconds: float):
        self.n = _n(seconds)
        self.buf = np.zeros(self.n, np.float32)

    def put(self, x: np.ndarray, at: float, gain: float = 1.0) -> None:
        i = int(at * SR)
        if i >= self.n or i < 0:
            return
        L = min(len(x), self.n - i)
        if L > 0:
            self.buf[i:i + L] += x[:L] * gain


def _sidechain(n: int, kick_times: list[float], depth: float = 0.6, release: float = 0.22) -> np.ndarray:
    g = np.ones(n, np.float32)
    L = _n(release)
    curve = 1.0 - depth * np.exp(-_t(L) / (release / 4.0))
    for kt in kick_times:
        i = int(kt * SR)
        if 0 <= i < n:
            seg = min(L, n - i)
            g[i:i + seg] = np.minimum(g[i:i + seg], curve[:seg])
    return g


def _music_v1(style: str, seconds: float, bpm: float, root: float, seed: int = 0) -> np.ndarray:
    """Versao antiga (mantida para comparacao)."""
    rng = np.random.default_rng(seed)
    total = seconds + 3.0
    beat = 60.0 / bpm
    bar = beat * 4
    prog = PROGRESSIONS.get(style, PROGRESSIONS["synthwave"])
    drums, tonal = _Track(total), _Track(total)
    kick_times: list[float] = []

    k = kick(rng)
    sn = snare(rng)
    cp = clap(rng)
    hh = hat(rng)
    hho = hat(rng, open_=True)
    variant = int(rng.integers(0, 3))

    bars_per_chord = 2 if style in ("lofi", "cinematic") else 1
    n_bars = int(total / bar) + 1
    for b in range(n_bars):
        t0 = b * bar
        semi, ctype = prog[(b // bars_per_chord) % len(prog)]
        chord = [freq_of(root, semi + iv) for iv in CHORDS[ctype]]
        chord_hi = [f * 2 for f in chord]

        if style == "synthwave":
            for q in range(4):
                drums.put(k, t0 + q * beat, 1.0); kick_times.append(t0 + q * beat)
                drums.put(hh, t0 + q * beat + beat / 2, 0.55)
                if q in (1, 3):
                    drums.put(cp, t0 + q * beat, 0.7)
            if b % 2 == 1:
                drums.put(hho, t0 + 3 * beat + beat / 2, 0.35)
            for e in range(8):
                tonal.put(bass_note(chord[0] / 2, beat / 2 * 0.9, "saw", 420), t0 + e * beat / 2, 0.55)
            seq = [0, 1, 2, 1] if variant == 0 else ([0, 2, 1, 2] if variant == 1 else [0, 1, 2, 3 % len(chord)])
            for s in range(16):
                idx = seq[s % 4]
                f = chord_hi[idx % len(chord_hi)] * (2 if (s // 4) % 2 else 1)
                tonal.put(pluck(f, beat / 4 * 1.6, 0.7), t0 + s * beat / 4, 0.22)
            tonal.put(pad_chord(chord, bar * 1.02, 1300.0), t0, 0.4)

        elif style == "lofi":
            sw = beat * 0.08  # swing nas colcheias fracas
            drums.put(k, t0, 0.85); kick_times.append(t0)
            drums.put(k, t0 + 2.5 * beat + sw, 0.7); kick_times.append(t0 + 2.5 * beat + sw)
            drums.put(sn, t0 + beat, 0.5)
            drums.put(sn, t0 + 3 * beat, 0.55)
            for e in range(8):
                off = sw if e % 2 else 0.0
                drums.put(hh, t0 + e * beat / 2 + off, 0.28 + 0.12 * rng.random())
            tonal.put(bass_note(chord[0] / 2, beat * 1.8, "sine", 300), t0, 0.7)
            tonal.put(bass_note(chord[(2 if variant else 1) % len(chord)] / 2, beat * 1.4, "sine", 300), t0 + 2 * beat + sw, 0.55)
            for f in chord[:4]:
                tonal.put(keys_note(f, beat * 1.9), t0, 0.28)
                tonal.put(keys_note(f, beat * 1.4), t0 + 2.5 * beat + sw, 0.22)

        elif style == "cinematic":
            if b % 2 == 0:
                drums.put(boom(rng), t0, 0.7); kick_times.append(t0)
            drums.put(k, t0 + 2 * beat, 0.25)
            tonal.put(pad_chord(chord, bar * bars_per_chord * 1.05, 900.0, 0.004), t0, 0.5 if b % bars_per_chord == 0 else 0.0)
            tonal.put(bass_note(chord[0] / 4, bar * 1.0, "sine", 200), t0, 0.8)
            mel = chord_hi[int(rng.integers(0, len(chord_hi)))]
            tonal.put(pluck(mel, beat * 2.5, 0.25), t0 + (beat if variant else 2 * beat), 0.3)
            if b >= 2 and rng.random() < 0.5:
                tonal.put(pluck(mel * 1.5, beat * 2.0, 0.2), t0 + 3 * beat, 0.2)

        elif style == "tense":
            for q in range(4):
                if q in (0, 2) or (q == 3 and b % 2):
                    drums.put(k, t0 + q * beat, 0.9); kick_times.append(t0 + q * beat)
            drums.put(sn, t0 + 3 * beat + beat / 2, 0.45)
            for s in range(16):
                drums.put(hh, t0 + s * beat / 4, 0.18 + (0.12 if s % 4 == 2 else 0.0))
            for e in range(8):
                f = chord[0] / 2 * (1.0 if e % 4 != 3 else 2 ** (1 / 12))
                tonal.put(bass_note(f, beat / 2 * 0.85, "square", 380), t0 + e * beat / 2, 0.5)
            tonal.put(pad_chord([chord[0], chord[0] * 2 ** (1 / 12) if b % 4 == 3 else chord[1], chord[2]], bar * 1.02, 800.0, 0.009), t0, 0.35)

        else:  # arp
            for q in range(4):
                drums.put(k, t0 + q * beat, 0.7); kick_times.append(t0 + q * beat)
                drums.put(hh, t0 + q * beat + beat / 2, 0.4)
            drums.put(sn, t0 + beat, 0.35); drums.put(sn, t0 + 3 * beat, 0.4)
            pent = [0, 3, 5, 7, 10, 12, 15, 17]
            for s in range(16):
                deg = pent[(s * (3 if variant == 2 else 1) + (s // 4)) % len(pent)]
                f = freq_of(root * 2, semi + deg)
                tonal.put(pluck(f, beat / 4 * 1.8, 0.6), t0 + s * beat / 4, 0.24)
            tonal.put(bass_note(chord[0] / 2, beat * 3.6, "saw", 350), t0, 0.5)
            tonal.put(pad_chord(chord, bar * 1.02, 1000.0), t0, 0.25)

    tonal_buf = tonal.buf
    if style in ("synthwave", "arp"):
        tonal_buf = tonal_buf * _sidechain(tonal.n, kick_times, 0.55 if style == "synthwave" else 0.35)
        tonal_buf = delay(tonal_buf, beat * 0.75, 0.22, 2)
    if style == "lofi":
        n = tonal.n
        crackle = np.zeros(n, np.float32)
        idx = rng.integers(0, n, size=int(total * 9))
        crackle[idx] = rng.uniform(-0.25, 0.25, size=len(idx)).astype(np.float32)
        bed = lowpass(rng.standard_normal(n).astype(np.float32), 2500.0) * 0.012
        tonal_buf = lowpass(tonal_buf, 3800.0) + crackle * 0.35 + bed
    if style == "cinematic":
        tonal_buf = delay(tonal_buf, beat * 1.5, 0.3, 3)

    mix = drums.buf * (0.9 if style != "cinematic" else 0.7) + tonal_buf
    mix = soft_clip(mix, 1.1)
    return normalize(mix[: _n(seconds)], 0.85)


# ------------------------------------------------------------- efeitos sonoros
def sfx(name: str, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed + zlib.crc32(name.encode()) % 1000)
    if name == "whoosh":
        n = _n(0.45)
        t = _t(n)
        noise = rng.standard_normal(n).astype(np.float32)
        x = sweep_filter(noise, 250.0, 3200.0, 1.0)
        env = np.sin(np.pi * np.clip(t / 0.45, 0, 1)) ** 1.5
        return normalize(x * env, 0.7)
    if name == "whoosh_short":
        n = _n(0.22)
        t = _t(n)
        x = sweep_filter(rng.standard_normal(n).astype(np.float32), 400.0, 4000.0, 0.9)
        return normalize(x * np.sin(np.pi * np.clip(t / 0.22, 0, 1)), 0.6)
    if name == "riser":
        n = _n(1.6)
        t = _t(n)
        noise = sweep_filter(rng.standard_normal(n).astype(np.float32), 200.0, 2600.0, 1.1)
        f = 120.0 * (6.0 ** (t / 1.6))
        tone = np.sin(TWO_PI * np.cumsum(f) / SR) * 0.35
        env = (t / 1.6) ** 2.2
        return normalize((noise + tone) * env, 0.75)
    if name == "impact":
        n = _n(0.8)
        t = _t(n)
        f = 32.0 + 90.0 * np.exp(-t * 14.0)
        x = np.sin(TWO_PI * np.cumsum(f) / SR) * np.exp(-t * 4.5)
        burst = lowpass(rng.standard_normal(n).astype(np.float32), 900.0) * np.exp(-t * 30.0) * 0.8
        return normalize(soft_clip((x + burst) * 1.8), 0.9)
    if name == "pop":
        n = _n(0.09)
        t = _t(n)
        f = 260.0 + 700.0 * np.exp(-t * 60.0)
        x = np.sin(TWO_PI * np.cumsum(f) / SR) * np.exp(-t * 38.0)
        return normalize(x, 0.6)
    if name == "tick":
        n = _n(0.025)
        t = _t(n)
        x = highpass(rng.standard_normal(n).astype(np.float32), 3000.0) * np.exp(-t * 220.0)
        return normalize(x, 0.45)
    if name == "count":
        n = _n(0.03)
        t = _t(n)
        return normalize(np.sin(TWO_PI * 1650.0 * t) * np.exp(-t * 160.0), 0.4)
    if name == "ding":
        n = _n(0.7)
        t = _t(n)
        x = (np.sin(TWO_PI * 1568.0 * t) + 0.45 * np.sin(TWO_PI * 3136.0 * t) + 0.25 * np.sin(TWO_PI * 2349.0 * t))
        return normalize(x * np.exp(-t * 5.0), 0.55)
    if name == "glitch":
        n = _n(0.26)
        out = np.zeros(n, np.float32)
        for _ in range(7):
            L = _n(rng.uniform(0.012, 0.03))
            s = int(rng.integers(0, max(1, n - L)))
            f = float(rng.uniform(300.0, 2800.0))
            seg = osc(f, L, "square", 4)
            seg = np.round(seg * 6) / 6  # bitcrush
            out[s:s + L] += seg * float(rng.uniform(0.4, 0.9))
        return normalize(out, 0.55)
    if name == "typewriter":
        n = _n(0.05)
        t = _t(n)
        x = highpass(rng.standard_normal(n).astype(np.float32), 2000.0) * np.exp(-t * 180.0)
        x += np.sin(TWO_PI * 140.0 * t) * np.exp(-t * 90.0) * 0.5
        return normalize(x, 0.4)
    if name == "flash":
        n = _n(0.35)
        t = _t(n)
        x = highpass(rng.standard_normal(n).astype(np.float32), 2500.0)
        env = (t / 0.35) ** 3
        env[-_n(0.02):] *= np.linspace(1, 0, _n(0.02))
        return normalize(x * env, 0.5)
    if name == "swipe":
        n = _n(0.3)
        t = _t(n)
        x = sweep_filter(rng.standard_normal(n).astype(np.float32), 3500.0, 300.0, 1.0)
        return normalize(x * np.sin(np.pi * np.clip(t / 0.3, 0, 1)) ** 2, 0.55)
    if name == "boom":
        return normalize(boom(rng), 0.85)
    raise KeyError(name)


SFX_NAMES = ["whoosh", "whoosh_short", "riser", "impact", "pop", "tick", "count", "ding", "glitch", "typewriter", "flash", "swipe", "boom"]


# ------------------------------------------------------------ trilha (v2)
def _voicing(root: float, semi: float, ctype: str, spread: bool = True) -> list[float]:
    iv = CHORDS[ctype]
    if spread and len(iv) >= 3:
        # posicao aberta: fundamental, quinta, setima/terca uma oitava acima
        order = [iv[0], iv[2]] + [x + 12 for x in iv[1:] if x != iv[2]]
    else:
        order = iv
    return [freq_of(root, semi + x) for x in order]


def string_note(freq: float, dur: float, cutoff: float = 700.0) -> np.ndarray:
    n = _n(dur)
    t = _t(n)
    vib = 1.0 + 0.004 * np.sin(TWO_PI * 5.0 * t)
    ph = TWO_PI * np.cumsum(freq * vib) / SR
    x = np.zeros(n, np.float32)
    for k in range(1, 7):
        x += (np.sin(ph * k) / k).astype(np.float32)
    x = lowpass(x, cutoff)
    return (x * env_adsr(n, min(0.8, dur * 0.35), 0.3, 0.85, min(0.7, dur * 0.3))).astype(np.float32)


def sub_note(freq: float, dur: float) -> np.ndarray:
    n = _n(dur)
    x = osc(freq, n, "sine") + 0.15 * osc(freq * 2, n, "sine")
    return (x * env_adsr(n, 0.01, 0.05, 0.9, min(0.1, dur * 0.3))).astype(np.float32)


def drone(freq: float, dur: float, cutoff: float = 260.0, beat_hz: float = 0.35) -> np.ndarray:
    """Bordao grave continuo: sub + quinta, com duas vozes quase iguais batendo
    devagar (a inquietude), e um pouco de harmonico para existir no celular."""
    n = _n(dur)
    x = osc(freq, n, "sine") + 0.7 * osc(freq + beat_hz, n, "sine")
    x += 0.5 * osc(freq * 1.5, n, "sine") + 0.3 * osc(freq * 1.5 + beat_hz, n, "sine")
    x += 0.35 * osc(freq * 2, n, "saw", 6)
    x = lowpass(x, cutoff)
    env = env_adsr(n, min(1.2, dur * 0.3), 0.2, 0.9, min(1.0, dur * 0.3))
    return (x * env).astype(np.float32)


def pulse_note(freq: float, dur: float, cutoff: float = 1200.0) -> np.ndarray:
    """Nota curta e seca do pulso em semicolcheias (serra + quadrada, filtro fechado)."""
    n = _n(dur)
    x = osc(freq, n, "saw", 12) * 0.7 + osc(freq * 1.004, n, "square", 6) * 0.35
    x = lowpass(x, cutoff)
    return (x * env_ad(n, 0.003, dur * 0.8, 5.0)).astype(np.float32)


def stab_chord(freqs: list[float], dur: float) -> np.ndarray:
    """Acorde curto e brilhante (o "stab" de vinheta de noticia)."""
    n = _n(dur)
    x = np.zeros(n, np.float32)
    for f in freqs:
        x += osc(f, n, "supersaw", 8, 0.008)
    x = highpass(lowpass(x / max(len(freqs), 1), 2600.0), 180.0)
    return (x * env_ad(n, 0.004, dur * 0.5, 4.0)).astype(np.float32)


def lead_note(freq: float, dur: float, bright: float = 0.6) -> np.ndarray:
    """Lead do gancho: tres serras desafinadas mais oitava, vibrato que entra
    depois do ataque. Corta na mixagem sem competir com a voz."""
    n = _n(dur)
    t = _t(n)
    vib = 1.0 + 0.003 * np.sin(TWO_PI * 5.5 * t) * np.clip(t / 0.25, 0.0, 1.0)
    ph = TWO_PI * np.cumsum((freq * vib).astype(np.float64)) / SR
    x = np.zeros(n, np.float32)
    for k, g in ((1.0, 1.0), (1.007, 0.6), (0.993, 0.6), (2.0, 0.3 * bright)):
        for h in range(1, 7):
            if freq * k * h > SR / 2.2:
                break
            x += (np.sin(ph * k * h) / h).astype(np.float32) * g
    x = lowpass(x, 1800.0 + 2200.0 * bright)
    return (x * env_adsr(n, 0.01, 0.15, 0.7, min(0.12, dur * 0.3))).astype(np.float32)


def _motif(track: _Track, notes: list[tuple], t0: float, beat: float, ref: float, gain: float, timbre) -> None:
    """Toca o motivo (posicao em tempos, semitons acima de `ref`, duracao em tempos) a partir de t0."""
    for pos, st, ln in notes:
        track.put(timbre(freq_of(ref, st), beat * ln), t0 + pos * beat, gain)


def _hit_dip(n: int, hits: list[float], pre: float = 0.35, floor: float = 0.12) -> np.ndarray:
    """Ganho que cai nos instantes antes de cada virada, para o impacto entrar no silencio."""
    g = np.ones(n, np.float32)
    L = _n(pre)
    ramp = np.linspace(1.0, floor, L, dtype=np.float32)
    for h in hits:
        i = int(h * SR) - L
        a, b = max(i, 0), min(i + L, n)
        if b > a:
            g[a:b] = np.minimum(g[a:b], ramp[a - i:b - i])
    return g


SIDECHAIN_DEPTH = {"synthwave": 0.55, "arp": 0.5, "urgent": 0.6, "suspense": 0.45, "tense": 0.4, "lofi": 0.3, "cinematic": 0.2}
HEAVY = ("suspense", "urgent", "tense")


def music(style: str, seconds: float, bpm: float, root: float, seed: int = 0, hits: list[float] | None = None) -> np.ndarray:
    """Trilha por camadas: bateria, baixo, pad, lead, arpejo e efeitos. Cada
    camada tem seu processamento (sidechain, delay, reverb) antes da soma e da
    cola final. `hits` sao os instantes da virada do roteiro (segundos): a trilha
    poe riser antes, impacto grave em cima e abre o arranjo depois.
    Saida: float32 mono em SR, pico 0.85, exatamente int(seconds * SR) amostras."""
    if style not in PROGRESSIONS:
        style = "synthwave"
    rng = np.random.default_rng(seed)
    total = seconds + 3.0
    beat = 60.0 / bpm
    bar = beat * 4
    prog = PROGRESSIONS[style]
    hit_times = sorted(float(h) for h in (hits or []) if 0.5 < h < seconds - 0.5)
    drums, bass, pad, lead, arp, fx = (_Track(total) for _ in range(6))
    kick_times: list[float] = []
    k = kick(rng, 0.44, 155.0, 44.0, 1.05, 0.45)
    kb = kick(rng, 0.5, 170.0, 44.0, 1.15, 0.6)   # bumbo pesado (suspense, urgente, tenso)
    sn = snare(rng)
    cp = clap(rng)
    hh = hat(rng, 0.04)
    hho = hat(rng, open_=True)
    tk, tk_low = tick(rng), tick(rng, low=True)
    hb = heartbeat(rng)
    variant = int(rng.integers(0, 3))
    bars_per_chord = 2 if style in ("lofi", "cinematic", "suspense") else 1
    n_bars = int(total / bar) + 1
    motif = MOTIFS[style]
    ref = root * 2   # referencia do motivo: uma oitava acima da tonica

    for b in range(n_bars):
        t0 = b * bar
        semi, ctype = prog[(b // bars_per_chord) % len(prog)]
        chord = _voicing(root, semi, ctype, spread=True)
        tones = [freq_of(root, semi + x) for x in CHORDS[ctype]]
        new_chord = (b % bars_per_chord == 0)
        p = min(1.0, t0 / max(seconds, 1.0))                  # progresso na faixa (0..1)
        after_hit = any(h <= t0 + 1e-3 for h in hit_times)    # a virada ja passou
        play_motif = b >= 2 and b % 2 == 0 and (b // 2) % 4 != 3   # frase a cada 2 compassos, com respiro

        if style == "synthwave":
            for q in range(4):
                drums.put(k, t0 + q * beat, 1.0); kick_times.append(t0 + q * beat)
                drums.put(hh, t0 + q * beat + beat / 2, 0.32)
                if q in (1, 3):
                    drums.put(cp, t0 + q * beat, 0.55)
                    drums.put(sn, t0 + q * beat, 0.35)
            if b % 2 == 1:
                drums.put(hho, t0 + 3.5 * beat, 0.22)
            for e in range(8):
                gate = 0.55 if e % 2 == 0 else 0.42
                bass.put(bass_note(tones[0] / 2, beat / 2 * gate * 1.7, "saw", 320), t0 + e * beat / 2, 0.5)
                bass.put(sub_note(tones[0] / 4, beat / 2 * 0.9), t0 + e * beat / 2, 0.45)
            seq = [[0, 1, 2, 1], [0, 2, 1, 2], [0, 1, 2, 3]][variant]
            hi = [f * 2 for f in tones] + [tones[0] * 4]
            for s in range(16):
                f = hi[seq[s % 4] % len(hi)] * (2 if (s // 8) % 2 else 1)
                arp.put(pluck(f, beat / 4 * 1.5, 0.45), t0 + s * beat / 4, 0.14)
            if new_chord:
                pad.put(pad_chord(chord, bar * bars_per_chord * 1.03, 900.0, 0.007), t0, 0.35)
            if play_motif:
                _motif(lead, motif, t0, beat, ref, 0.16, lambda f, d: lead_note(f, d, 0.55))

        elif style == "lofi":
            sw = beat * 0.09
            drums.put(k, t0, 0.7); kick_times.append(t0)
            drums.put(k, t0 + 2.5 * beat + sw, 0.55); kick_times.append(t0 + 2.5 * beat + sw)
            drums.put(sn, t0 + beat, 0.4)
            drums.put(sn, t0 + 3 * beat, 0.45)
            for e in range(8):
                off = sw if e % 2 else 0.0
                drums.put(hh, t0 + e * beat / 2 + off, 0.16 + 0.1 * float(rng.random()))
            bass.put(sub_note(tones[0] / 2, beat * 1.9), t0, 0.6)
            bass.put(sub_note(tones[(2 if variant else 1) % len(tones)] / 2, beat * 1.3), t0 + 2 * beat + sw, 0.45)
            if new_chord or b % 2 == 1:
                for f in chord[:4]:
                    lead.put(keys_note(f, beat * 2.2), t0 + (0.0 if new_chord else 2.5 * beat + sw), 0.22)
                    lead.put(keys_note(f * 1.003, beat * 2.2), t0 + (0.0 if new_chord else 2.5 * beat + sw), 0.12)
            if new_chord:
                pad.put(pad_chord(chord, bar * bars_per_chord * 1.05, 600.0, 0.004), t0, 0.16)
            if b >= 1:   # movimento: arpejo suave em colcheias sobre o acorde
                for e in range(8):
                    off = sw if e % 2 else 0.0
                    arp.put(pluck(chord[e % min(len(chord), 4)] * (2 if e >= 4 else 1), beat / 2 * 1.6, 0.2), t0 + e * beat / 2 + off, 0.07)
            if play_motif:
                _motif(lead, motif, t0, beat, ref, 0.2, keys_note)

        elif style == "cinematic":
            if b % 2 == 0:
                drums.put(boom(rng), t0, 0.55); kick_times.append(t0)
            drums.put(k, t0 + 2 * beat, 0.22)
            if new_chord:
                pad.put(pad_chord(chord, bar * bars_per_chord * 1.06, 700.0, 0.004), t0, 0.4)
                for f in chord[:3]:
                    pad.put(string_note(f * 2, bar * bars_per_chord * 1.02, 800.0), t0, 0.14)
            bass.put(sub_note(tones[0] / 4, bar * 1.0), t0, 0.7)
            if b >= 2:   # movimento: arpejo de harpa, sobe e desce pelo acorde
                up = [f * 2 for f in chord[:3]] + [chord[0] * 4]
                seq = up + up[-2:0:-1]
                for e in range(8):
                    arp.put(pluck(seq[e % len(seq)], beat / 2 * 2.2, 0.25), t0 + e * beat / 2, 0.09)
            if play_motif:
                _motif(lead, motif, t0, beat, ref, 0.22, lambda f, d: string_note(f, d, 1200.0))
            elif b % 2 == 1 and rng.random() < 0.5:
                lead.put(pluck(tones[int(rng.integers(0, len(tones)))] * 3, beat * 2.0, 0.15), t0 + 3 * beat, 0.16)

        elif style == "tense":
            for q in range(4):
                if q in (0, 2) or (q == 3 and b % 2):
                    drums.put(kb, t0 + q * beat, 0.8); kick_times.append(t0 + q * beat)
            drums.put(sn, t0 + 3.5 * beat, 0.35)
            for s in range(16):
                drums.put(hh, t0 + s * beat / 4, 0.1 + (0.1 if s % 4 == 2 else 0.0))
            for e in range(8):
                f = tones[0] / 2 * (1.0 if e % 4 != 3 else 2 ** (1 / 12))
                bass.put(bass_note(f, beat / 2 * 0.8, "square", 300), t0 + e * beat / 2, 0.42)
            bass.put(sub_note(tones[0] / 4, bar), t0, 0.5)
            if new_chord:
                dis = [tones[0], tones[0] * 2 ** (1 / 12) if b % 4 == 3 else tones[1], tones[2]]
                pad.put(pad_chord(dis, bar * 1.03, 700.0, 0.01), t0, 0.28)
            if b % 4 == 3 and not hit_times:
                fx.put(sfx("riser", seed + b)[: _n(bar)], t0, 0.18)
            if play_motif:
                _motif(lead, motif, t0, beat, ref, 0.15, lambda f, d: pluck(f, d, 0.6))

        elif style == "suspense":
            # tensao que cresce ao longo da faixa; depois da virada o arranjo abre de vez
            dens = min(1.0, p * 1.2 + (0.45 if after_hit else 0.0))
            drums.put(kb, t0, 0.9); kick_times.append(t0)
            if dens > 0.25:
                drums.put(kb, t0 + 2 * beat, 0.75); kick_times.append(t0 + 2 * beat)
            if dens > 0.7:
                drums.put(kb, t0 + 2.5 * beat, 0.5); kick_times.append(t0 + 2.5 * beat)
                drums.put(sn, t0 + 3.5 * beat, 0.2)
            steps = 8 if dens < 0.5 else 16   # tique de relogio: colcheias, depois semicolcheias
            for s in range(steps):
                low = (s % 2 == 1) if steps == 8 else (s % 4 == 2)
                acc = 0.1 if s % (steps // 4) == 0 else 0.0
                drums.put(tk_low if low else tk, t0 + s * bar / steps, 0.2 + acc + 0.1 * dens)
            near_hit = any(0.0 < h - t0 <= 2 * bar + 1e-3 for h in hit_times)
            if near_hit or (b % 4 == 1 and not after_hit):   # batida de coracao: de vez em quando, e sempre antes da virada
                drums.put(hb, t0 + 1.5 * beat, 0.55)
                drums.put(hb, t0 + 3.5 * beat, 0.55)
            for e in range(8):   # pulso de baixo em colcheias; a segunda menor na ultima e o "erro" que incomoda
                f = tones[0] * (2 ** (1 / 12) if (e == 7 and b % 2 == 1) else 1.0)
                bass.put(bass_note(f, beat / 2 * 0.75, "saw", 380.0 + 500.0 * dens), t0 + e * beat / 2, 0.34 + 0.2 * dens)
            if new_chord:
                bass.put(drone(tones[0] / 2, bar * bars_per_chord * 1.04, 240.0 + 200.0 * dens), t0, 0.55)
                dark = chord[:3] + ([freq_of(root, semi + 13)] if (b // bars_per_chord) % 2 == 1 else [])
                cut = 420.0 * (2600.0 / 420.0) ** dens
                pad.put(lowpass_sweep(pad_chord(dark, bar * bars_per_chord * 1.04, 6000.0, 0.012), cut * 0.7, cut * 1.4), t0, 0.32)
            if play_motif:
                _motif(lead, motif, t0, beat, ref, 0.2, lambda f, d: pluck(f, d, 0.3))
            if b % 8 == 7 and not hit_times:   # sem virada informada: sobe e bate a cada 8 compassos
                fx.put(sfx("riser", seed + b)[: _n(bar)], t0, 0.22)
                fx.put(boom(rng), t0 + bar, 0.5)

        else:   # urgent
            for q in range(4):
                drums.put(kb, t0 + q * beat, 1.0); kick_times.append(t0 + q * beat)
                if q in (1, 3):
                    drums.put(cp, t0 + q * beat, 0.5)
                    drums.put(sn, t0 + q * beat, 0.45)
            for s in range(16):
                drums.put(hh, t0 + s * beat / 4, 0.16 + (0.14 if s % 4 == 2 else 0.0))
            if b % 2 == 1:
                drums.put(hho, t0 + 3.5 * beat, 0.2)
            for s in range(16):   # pulso constante em semicolcheias, filtro abrindo a cada 2 compassos
                frac = ((b % 2) * 16 + s) / 32.0
                cut = 700.0 * (2600.0 / 700.0) ** frac
                f = tones[2 % len(tones)] * 2 if s % 8 == 6 else tones[0] * (2 if s % 4 in (0, 2) else 4)
                arp.put(pulse_note(f, beat / 4 * 0.85, cut), t0 + s * beat / 4, 0.2)
            for e in range(8):
                bass.put(bass_note(tones[0] / 2, beat / 2 * 0.8, "saw", 340), t0 + e * beat / 2, 0.5)
                bass.put(sub_note(tones[0] / 4, beat / 2 * 0.9), t0 + e * beat / 2, 0.4)
            pad.put(pad_chord(chord, bar * 1.03, 1000.0, 0.008), t0, 0.28)
            hi = [f * 2 for f in tones[:3]]
            if b % 2 == 0:
                lead.put(stab_chord(hi, beat * 0.6), t0, 0.3)
            else:
                lead.put(stab_chord(hi, beat * 0.45), t0 + 2.5 * beat, 0.22)
            if play_motif:
                _motif(lead, motif, t0, beat, ref, 0.15, lambda f, d: lead_note(f, d, 0.8))
            if b % 4 == 3 and not hit_times:
                fx.put(sfx("riser", seed + b)[: _n(bar)], t0, 0.2)

    heavy = style in HEAVY
    for h in hit_times:   # virada do roteiro: riser terminando em cima, impacto grave no ponto
        r = sfx("riser", seed + int(h * 10))
        start = h - 1.6
        if start < 0:
            r, start = r[_n(-start):], 0.0
        fx.put(r, start, 0.5 if heavy else 0.3)
        fx.put(boom(rng), h, 0.9 if heavy else 0.6)
        fx.put(sfx("impact", seed + 3), h, 0.5)

    n = drums.n
    sc = _sidechain(n, kick_times, SIDECHAIN_DEPTH[style], 0.3 if style == "suspense" else 0.26) if kick_times else np.ones(n, np.float32)
    sc_soft = 1.0 - (1.0 - sc) * 0.6   # pad e lead respiram menos que o baixo
    bass_b = bass.buf * sc
    pad_b = reverb(pad.buf * sc_soft, 1.8 if style in ("cinematic", "suspense") else 1.2, 0.3, 3000.0, seed + 1)
    arp_b = arp.buf * sc
    lead_b = lead.buf * (sc_soft if style in ("synthwave", "arp", "urgent") else 1.0)
    if style in ("synthwave", "arp", "urgent"):
        arp_b = delay(arp_b, beat * 0.75, 0.25, 2)
        lead_b = delay(lead_b, beat * 0.75, 0.3, 3)
    elif style == "suspense":
        lead_b = delay(lead_b, beat * 1.5, 0.35, 3)
    lead_b = reverb(lead_b, 2.2 if style in ("cinematic", "suspense") else 1.0, 0.22 if style != "lofi" else 0.15, 4000.0, seed + 2)
    arp_b = reverb(arp_b, 1.0, 0.15, 4000.0, seed + 4)
    drums_b = drums.buf
    if style in ("synthwave", "lofi", "urgent"):
        drums_b = reverb(drums_b, 0.5, 0.08, 5000.0, seed + 3)
    drum_gain = {"cinematic": 0.7, "suspense": 0.8}.get(style, 0.85)
    mix = (drums_b * drum_gain + bass_b * 0.9 + pad_b + lead_b + arp_b) * _hit_dip(n, hit_times) + fx.buf
    if style == "lofi":
        crackle = np.zeros(n, np.float32)
        idx = rng.integers(0, n, size=int(total * 5))
        crackle[idx] = rng.uniform(-0.2, 0.2, size=len(idx)).astype(np.float32)
        mix = lowpass(mix, 4200.0) + crackle * 0.25
    mix = lowpass(mix, 15000.0)
    mix = glue(mix, 0.3, 2.5)
    mix = soft_clip(mix, 1.05)
    return normalize(mix[: _n(seconds)], 0.85)
