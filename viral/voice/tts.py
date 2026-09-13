"""Narracao com tempo por palavra.

Quatro caminhos:
1. OpenAI (gpt-4o-mini-tts, com chave): o roteiro inteiro numa leitura so,
   emendado por espaco e pontuacao (nunca paragrafo), com instrucoes de voz
   animada e ritmo continuo (TTS_INSTRUCTIONS); tempo por palavra pelo
   whisper-1. As pausas que sobram sao apertadas por tighten().
2. Edge TTS (gratuito, sem chave): mesma leitura so, com marcas de palavra.
3. Narracao gravada por voce (--narration arquivo.wav): o audio e segmentado
   por silencio e cada trecho do roteiro recebe um segmento; as palavras sao
   distribuidas por tamanho dentro do trecho. Sem reconhecimento de fala.
4. Voz do Windows (SAPI), offline, como reserva.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..audio.mix import decode_audio
from ..config import CACHE, SR, VOICE, VOICE_RATE, VOICE_SPEED
from ..util.text import strip_accents, words as tok_words

TTS_DIR = CACHE / "tts"
VOICES_PT = ["pt-BR-ThalitaMultilingualNeural", "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"]


@dataclass
class Narration:
    audio: np.ndarray
    timeline: list[dict] = field(default_factory=list)   # por trecho: start, end, words[(texto, ini, fim)]
    engine: str = "edge"

    @property
    def duration(self) -> float:
        return len(self.audio) / SR


def _key(text: str, voice: str, rate: str, pitch: str) -> str:
    return hashlib.sha1(f"{voice}|{rate}|{pitch}|{text}".encode("utf-8")).hexdigest()[:20]


async def _edge(text: str, voice: str, rate: str, pitch: str) -> tuple[bytes, list[tuple[str, float, float]]]:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    audio = b""
    words: list[tuple[str, float, float]] = []
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
        elif chunk["type"] == "WordBoundary":
            s = chunk["offset"] / 1e7
            words.append((str(chunk["text"]), round(s, 3), round(s + chunk["duration"] / 1e7, 3)))
    if not audio:
        raise RuntimeError("edge-tts sem audio")
    return audio, words


def _sapi(text: str, out_wav: Path, rate: int = 1) -> None:
    safe = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'pt-BR' } | Select-Object -First 1; "
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
        f"$s.Rate = {rate}; "
        f"$s.SetOutputToWaveFile('{str(out_wav)}'); "
        f"$s.Speak('{safe}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, capture_output=True, timeout=180)


def estimate_words(text: str, start: float, end: float) -> list[tuple[str, float, float]]:
    """Distribui as palavras de um trecho entre start e end, por tamanho."""
    toks = tok_words(text)
    if not toks:
        return []
    weights = np.array([len(t) + 1.5 for t in toks], np.float32)
    usable = max(end - start - 0.08, 0.2)
    spans = weights / weights.sum() * usable
    out, t = [], start + 0.04
    for w, d in zip(toks, spans):
        out.append((w, round(t, 3), round(t + d * 0.9, 3)))
        t += d
    return out


def _trim(audio: np.ndarray, thresh: float = 0.012, keep_head: float = 0.05, keep_tail: float = 0.25) -> tuple[np.ndarray, float]:
    idx = np.where(np.abs(audio) > thresh)[0]
    if len(idx) == 0:
        return audio, 0.0
    a = max(0, idx[0] - int(keep_head * SR))
    b = min(len(audio), idx[-1] + int(keep_tail * SR))
    return audio[a:b], a / SR


def _norm(s: str) -> str:
    return strip_accents(re.sub(r"[^\w]", "", s.lower()))


_NUM_WORDS = {
    "zero": "0", "um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3", "quatro": "4", "cinco": "5", "seis": "6", "sete": "7",
    "oito": "8", "nove": "9", "dez": "10", "vinte": "20", "trinta": "30", "cem": "100", "mil": "1000",
}


def _tok(s: str) -> str:
    n = strip_accents(re.sub(r"[^\w]", "", s.lower()))
    n = re.sub(r"(?<=\d)[.,](?=\d)", "", n)
    return _NUM_WORDS.get(n, n)


def align(words: list[tuple[str, float, float]], texts: list[str]) -> list[dict]:
    """Casa as palavras faladas (com tempo) com as palavras do roteiro por
    alinhamento de sequencia; o que nao casa recebe tempo interpolado entre as
    ancoras vizinhas. Assim um numero lido de outro jeito ou uma palavra
    transcrita diferente nao desloca os trechos seguintes."""
    import difflib
    script_tokens: list[tuple[int, str]] = []   # (indice do trecho, token normalizado)
    for bi, t in enumerate(texts):
        for w in tok_words(t):
            n = _tok(w)
            if n:
                script_tokens.append((bi, n))
    spoken = [(_tok(w), s, e, w) for w, s, e in words if _tok(w)]
    a = [n for _, n in script_tokens]
    b = [n for n, _, _, _ in spoken]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    anchor: dict[int, tuple[float, float, str]] = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            anchor[blk.a + k] = (spoken[blk.b + k][1], spoken[blk.b + k][2], spoken[blk.b + k][3])
    n = len(script_tokens)
    if n == 0:
        return [{"index": i, "start": 0.0, "end": 0.3, "words": []} for i in range(len(texts))]
    coverage = len(anchor) / n
    # tempos para tokens sem ancora: interpolacao entre as ancoras vizinhas
    starts = [None] * n
    ends = [None] * n
    for i, (s, e, _) in anchor.items():
        starts[i], ends[i] = s, e
    total_end = spoken[-1][2] if spoken else 1.0
    idx_anchor = sorted(anchor)
    for i in range(n):
        if starts[i] is not None:
            continue
        prev_i = max((j for j in idx_anchor if j < i), default=None)
        next_i = min((j for j in idx_anchor if j > i), default=None)
        t0 = ends[prev_i] if prev_i is not None else (spoken[0][1] if spoken else 0.0)
        t1 = starts[next_i] if next_i is not None else total_end
        span_n = (next_i if next_i is not None else n) - (prev_i + 1 if prev_i is not None else 0)
        k = i - (prev_i + 1 if prev_i is not None else 0)
        d = max(t1 - t0, 0.05) / max(span_n, 1)
        starts[i] = t0 + d * k
        ends[i] = t0 + d * (k + 0.9)
    per_beat: list[list[tuple[str, float, float]]] = [[] for _ in texts]
    orig_words = [w for t in texts for w in tok_words(t) if _tok(w)]
    for i, (bi, _) in enumerate(script_tokens):
        per_beat[bi].append((orig_words[i], round(float(starts[i]), 3), round(float(ends[i]), 3)))
    timeline = []
    last_end = 0.0
    for i, ws in enumerate(per_beat):
        ws.sort(key=lambda x: x[1])
        if ws:
            start, end = max(ws[0][1], last_end), max(ws[-1][2], ws[0][1] + 0.2)
        else:
            start, end = last_end, last_end + 0.3
        timeline.append({"index": i, "start": round(start, 3), "end": round(end, 3), "words": ws})
        last_end = end
    timeline[0]["coverage"] = round(coverage, 2)
    return timeline


def _quiet_run(audio: np.ndarray, a: int, b: int, thresh: float, blk: int) -> tuple[int, int] | None:
    """Maior trecho continuo de silencio (rms por bloco abaixo de thresh) dentro
    de audio[a:b]. Devolve (inicio, fim) em amostras absolutas, ou None."""
    seg = audio[a:b]
    n = len(seg) // blk
    if n == 0:
        return None
    rms = np.sqrt(np.mean(seg[: n * blk].reshape(n, blk).astype(np.float32) ** 2, axis=1))
    quiet = rms < thresh
    best: tuple[int, int] | None = None
    best_len, cur = 0, None
    for i in range(n + 1):
        q = bool(quiet[i]) if i < n else False
        if q and cur is None:
            cur = i
        elif not q and cur is not None:
            if i - cur > best_len:
                best_len, best = i - cur, (cur, i)
            cur = None
    if best is None:
        return None
    return a + best[0] * blk, a + best[1] * blk


def tighten(audio: np.ndarray, words: list[tuple[str, float, float]], max_gap: float = 0.26, min_gap: float = 0.14) -> tuple[np.ndarray, list[tuple[str, float, float]]]:
    """Corta o miolo das pausas entre palavras: toda pausa acima de max_gap vira
    ~min_gap, como um editor que emenda uma frase na outra. O corte so acontece
    onde o audio esta mesmo em silencio (energia por bloco de 5 ms), entao um
    tempo de palavra impreciso do Whisper nunca leva pedaco de fala junto. Os
    tempos das palavras seguintes sao deslocados pelo que foi cortado."""
    if len(words) < 2:
        return audio, words
    keep: list[tuple[int, int]] = []  # intervalos de amostras a manter
    new_words: list[tuple[str, float, float]] = []
    pos = 0
    shift = 0.0
    blk = max(1, int(0.005 * SR))
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    thresh = max(0.004, 0.06 * peak)
    half = int(min_gap / 2 * SR)
    for (w1, s1, e1), (w2, s2, e2) in zip(words, words[1:]):
        gap = s2 - e1
        new_words.append((w1, round(s1 - shift, 3), round(e1 - shift, 3)))
        if gap <= max_gap:
            continue
        a, b = max(pos, int(e1 * SR)), min(len(audio), int(s2 * SR))
        run = _quiet_run(audio, a, b, thresh, blk) if b > a else None
        if run is None:
            continue
        qa, qb = run
        if (qb - qa) / SR <= min_gap + 0.05:
            continue  # nao ha silencio real suficiente para cortar
        # mantem min_gap: metade depois da fala, metade antes da proxima
        cut_start, cut_end = qa + half, qb - half
        if cut_end > cut_start:
            keep.append((pos, cut_start))
            pos = cut_end
            shift += (cut_end - cut_start) / SR
    wl, sl, el = words[-1]
    new_words.append((wl, round(sl - shift, 3), round(el - shift, 3)))
    keep.append((pos, len(audio)))
    parts = []
    fade = int(0.006 * SR)
    for a, b in keep:
        seg = audio[a:b].copy()
        if len(seg) > 2 * fade:
            seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
            seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        parts.append(seg)
    return np.concatenate(parts), new_words


# Vozes do gpt-4o-mini-tts. Masculinas e energeticas primeiro: ash (padrao), verse, echo, cedar.
OPENAI_VOICES = ["ash", "verse", "echo", "cedar", "marin", "onyx", "alloy", "ballad", "coral", "fable", "nova", "sage", "shimmer"]
TTS_INSTRUCTIONS = (
    "Narrador masculino brasileiro de canal de tecnologia no TikTok. Português do Brasil, sotaque natural. "
    "Animado e empolgado, sem gritar: entonação viva e variada, como quem conta uma novidade quente para um amigo. "
    "Ritmo acelerado de notícia urgente, contínuo e direto: emende uma frase na outra, sem pausa dramática, "
    "sem respiração longa entre frases, sem silêncio entre parágrafos. Ênfase natural em números e nomes. "
    "Pronuncie termos em inglês (iPhone, Google, chip, software, Wi-Fi, streaming) corretamente, "
    "como brasileiro que domina o assunto. A sigla IA (inteligência artificial) é portuguesa: diga as duas letras, "
    "\"i-á\", nunca \"ai\" em inglês. Nunca monótono, nunca robótico, "
    "nunca voz de locutor de rádio antiga nem de comercial. "
    "Quando pedir para seguir o perfil, diga de passagem, rápido e leve, como um aparte entre parênteses no meio "
    "da história, sem tom de encerramento e sem parar o ritmo; a frase seguinte vem colada."
)


def join_for_tts(texts: list[str]) -> str:
    """Um texto so para a voz: cada trecho termina em pontuacao e o seguinte vem
    depois de um espaco simples. Nunca quebra de linha ou paragrafo, que o TTS
    le como pausa longa."""
    parts = []
    for t in texts:
        t = re.sub(r"\s+", " ", str(t)).strip().rstrip(",;:- ")
        # IA em portugues: letra por letra ("i-á"); a legenda continua mostrando "IA"
        t = re.sub(r"\bIAs\b", "i-ás", t)
        t = re.sub(r"\bIA\b", "i-á", t)
        if not t:
            continue
        if t[-1] not in ".!?…":
            t += "."
        parts.append(t)
    return " ".join(parts)


def narrate_openai(texts: list[str], voice: str = "ash", log=None, lead: float = 0.3, tight: bool = True,
                   speed: float = VOICE_SPEED) -> Narration | None:
    """Narracao pela OpenAI (gpt-4o-mini-tts) + tempo por palavra (whisper-1)."""
    from .. import ai
    if not ai.available():
        return None
    full = join_for_tts(texts)
    if len(full) > 4000:
        full = full[:4000]
    mp3 = ai.tts(full, voice, TTS_INSTRUCTIONS, log, speed=speed)
    if not mp3:
        return None
    audio = decode_audio(mp3)
    audio, cut = _trim(audio)
    words = ai.transcribe_words(mp3, "pt", log) or []
    if words:
        words = [(w, max(0.0, s - cut), max(0.0, e - cut)) for w, s, e in words]
    else:
        words = estimate_words(full, 0.05, len(audio) / SR)
    if tight:
        audio, words = tighten(audio.astype(np.float32), words)
    words = [(w, s + lead, e + lead) for w, s, e in words]
    track = np.concatenate([np.zeros(int(lead * SR), np.float32), audio.astype(np.float32), np.zeros(int(0.4 * SR), np.float32)])
    return Narration(track, align(words, texts), "openai")


def narrate(texts: list[str], voice: str = VOICE, rate: str = VOICE_RATE, pitch: str = "+0Hz", engine: str = "auto", log=None,
            lead: float = 0.3, tight: bool = True, speed: float = VOICE_SPEED) -> Narration:
    """Narra a lista de trechos como um texto so. Retorna audio e linha do tempo por trecho.
    engine: auto (OpenAI se houver chave, senao Edge), openai, edge, sapi.
    speed: so a OpenAI usa (1.0 normal; tema de noticia usa 1.05). rate/pitch sao do Edge."""
    if engine in ("auto", "openai"):
        ov = voice if voice in OPENAI_VOICES else VOICE if VOICE in OPENAI_VOICES else "ash"
        nar = narrate_openai(texts, ov, log, lead, tight, speed)
        if nar:
            return nar
        if engine == "openai":
            raise RuntimeError("OpenAI TTS indisponivel (chave ou rede)")
        if log:
            log("  OpenAI indisponivel; usando voz gratuita do Edge")
        if voice in OPENAI_VOICES:
            voice = "pt-BR-AntonioNeural"
    full = join_for_tts(texts)
    key = _key(full, voice, rate, pitch)
    mp3, meta_p, wav = TTS_DIR / f"{key}.mp3", TTS_DIR / f"{key}.json", TTS_DIR / f"{key}.wav"
    used, words = None, []
    if meta_p.exists() and (mp3.exists() or wav.exists()):
        meta = json.loads(meta_p.read_text("utf-8"))
        used = meta.get("engine", "edge")
        words = [tuple(w) for w in meta.get("words", [])]
    else:
        if engine in ("auto", "edge"):
            last = None
            for _ in range(3):
                try:
                    audio_bytes, words = asyncio.run(_edge(full, voice, rate, pitch))
                    mp3.write_bytes(audio_bytes)
                    used = "edge"
                    break
                except Exception as e:
                    last = e
            if used is None and log:
                log(f"  edge-tts falhou ({type(last).__name__}); usando voz do Windows")
        if used is None:
            if engine == "edge":
                raise RuntimeError("edge-tts indisponivel")
            _sapi(full, wav)
            used = "sapi"
            words = []
        meta_p.write_text(json.dumps({"engine": used, "words": words, "text": full}, ensure_ascii=False), "utf-8")
    audio = decode_audio(mp3 if used == "edge" else wav)
    audio, cut = _trim(audio)
    if words:
        words = [(w, max(0.0, s - cut), max(0.0, e - cut)) for w, s, e in words]
    else:
        words = estimate_words(full, 0.05, len(audio) / SR)
    if tight:
        audio, words = tighten(audio.astype(np.float32), words)
    words = [(w, s + lead, e + lead) for w, s, e in words]
    track = np.concatenate([np.zeros(int(lead * SR), np.float32), audio.astype(np.float32), np.zeros(int(0.4 * SR), np.float32)])
    return Narration(track, align(words, texts), used)


# --------------------------------------------------------- voz gravada por voce
def _speech_segments(audio: np.ndarray, min_silence: float = 0.35, thresh_db: float = -38.0) -> list[tuple[float, float]]:
    """Trechos de fala separados por silencio (energia por blocos de 20 ms)."""
    blk = int(0.02 * SR)
    n = len(audio) // blk
    if n == 0:
        return []
    rms = np.sqrt(np.mean(audio[: n * blk].reshape(n, blk) ** 2, axis=1) + 1e-9)
    ref = float(np.percentile(rms, 95)) or 1e-6
    db = 20 * np.log10(np.maximum(rms / ref, 1e-6))
    voiced = db > thresh_db
    segs: list[tuple[float, float]] = []
    start = None
    silent_run = 0
    for i, v in enumerate(voiced):
        t = i * blk / SR
        if v:
            if start is None:
                start = t
            silent_run = 0
        else:
            if start is not None:
                silent_run += 1
                if silent_run * blk / SR >= min_silence:
                    segs.append((start, t - silent_run * blk / SR))
                    start, silent_run = None, 0
    if start is not None:
        segs.append((start, n * blk / SR))
    return [(a, b) for a, b in segs if b - a > 0.25]


def narrate_from_file(path: str | Path, texts: list[str], log=None, lead: float = 0.3) -> Narration:
    """Alinha uma gravacao sua ao roteiro. Grave lendo os trechos na ordem, com
    uma pausa curta (meio segundo) entre eles: o alinhamento e por pausa."""
    audio = decode_audio(path).astype(np.float32)
    audio, _ = _trim(audio, thresh=0.01)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio / peak * 0.9
    segs = _speech_segments(audio)
    if log:
        log(f"  narracao gravada: {len(audio) / SR:.1f}s, {len(segs)} trechos de fala para {len(texts)} trechos de roteiro")
    weights = np.array([max(1, len(t)) for t in texts], np.float64)
    if len(segs) == len(texts):
        spans = segs
    else:
        # proporcional ao tamanho do texto, usando os segmentos como grade
        total = len(audio) / SR
        cum = np.cumsum(weights) / weights.sum() * total
        spans = []
        prev = 0.0
        for c in cum:
            # encaixa a fronteira na pausa mais proxima
            best = c
            for a, b in segs:
                if abs(b - c) < 0.6:
                    best = b
                    break
            spans.append((prev, best))
            prev = best
    timeline = []
    for i, (t, (a, b)) in enumerate(zip(texts, spans)):
        ws = estimate_words(t, a + lead, b + lead)
        timeline.append({"index": i, "start": round(a + lead, 3), "end": round(b + lead, 3), "words": ws})
    track = np.concatenate([np.zeros(int(lead * SR), np.float32), audio, np.zeros(int(0.4 * SR), np.float32)])
    return Narration(track, timeline, "gravada")


async def _sample(voice: str, text: str, out: Path) -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate="+0%")
    await comm.save(str(out))


SAMPLE_VOICES = ["ash", "verse", "echo", "cedar"]   # as animadas, para escolher com `voices`


def make_samples(out_dir: Path, text: str, log=print) -> list[Path]:
    """Amostras para escolher a voz: OpenAI (se houver chave, com a mesma instrucao
    e velocidade da producao) e as gratuitas do Edge."""
    from .. import ai
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    if ai.available():
        for v in SAMPLE_VOICES:
            p = out_dir / f"{v}.mp3"
            try:
                mp3 = ai.tts(join_for_tts([text]), v, TTS_INSTRUCTIONS, None, speed=VOICE_SPEED)
                if mp3:
                    p.write_bytes(Path(mp3).read_bytes())
                    done.append(p)
                    log(f"  {p.name} (OpenAI)")
            except Exception as e:
                log(f"  {v}: falhou ({type(e).__name__})")
    for v in VOICES_PT:
        p = out_dir / f"{v}.mp3"
        try:
            asyncio.run(_sample(v, text, p))
            done.append(p)
            log(f"  {p.name}")
        except Exception as e:
            log(f"  {v}: falhou ({type(e).__name__})")
    return done
