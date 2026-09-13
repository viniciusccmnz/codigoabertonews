"""Cliente minimo da OpenAI (sem SDK), com cache e registro de gasto.

Tres usos, todos baratos e com cache por hash:
- chat_json: revisao do roteiro com gpt-4o-mini (menos de 1 centavo por video);
- tts: narracao com gpt-4o-mini-tts, voz masculina, instrucoes de sotaque e tom;
- transcribe_words: whisper-1 com tempo por palavra, para legenda e sincronia.

A chave vem de OPENAI_API_KEY no ambiente ou do arquivo .env na raiz.
Nunca e impressa. Sem chave, tudo cai para o caminho gratuito (edge-tts, templates).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import requests

from .config import CACHE, ROOT

AI_DIR = CACHE / "ai"
AI_DIR.mkdir(parents=True, exist_ok=True)
USAGE = AI_DIR / "usage.json"
BASE = "https://api.openai.com/v1"

CHAT_MODEL = "gpt-4o-mini"
TTS_MODEL = "gpt-4o-mini-tts"
STT_MODEL = "whisper-1"


def api_key() -> str | None:
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text("utf-8").splitlines():
            if line.strip().startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def available() -> bool:
    return bool(api_key())


def _headers() -> dict:
    return {"Authorization": f"Bearer {api_key()}"}


def _log_usage(kind: str, amount: float, unit: str) -> None:
    try:
        data = json.loads(USAGE.read_text("utf-8")) if USAGE.exists() else {}
        day = time.strftime("%Y-%m-%d")
        d = data.setdefault(day, {})
        d[kind] = round(d.get(kind, 0.0) + amount, 3)
        d[kind + "_unit"] = unit
        USAGE.write_text(json.dumps(data, indent=1), "utf-8")
    except Exception:
        pass


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def chat_json(system: str, user: str, max_tokens: int = 1200, temperature: float = 0.4, log=None) -> dict | None:
    """Uma chamada de chat que devolve JSON. Cache por conteudo."""
    if not available():
        return None
    cache = AI_DIR / f"chat_{_key(CHAT_MODEL, system, user)}.json"
    if cache.exists():
        return json.loads(cache.read_text("utf-8"))
    body = {
        "model": CHAT_MODEL, "temperature": temperature, "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    for attempt in range(2):
        try:
            r = requests.post(f"{BASE}/chat/completions", headers=_headers(), json=body, timeout=90)
            if r.status_code == 200:
                data = r.json()
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                _log_usage("chat_tokens", usage.get("total_tokens", 0), "tokens")
                out = json.loads(text)
                cache.write_text(json.dumps(out, ensure_ascii=False), "utf-8")
                return out
            if log:
                log(f"  openai chat: HTTP {r.status_code} {r.text[:120]}")
            if r.status_code in (401, 402, 403):
                return None
        except (requests.RequestException, ValueError, KeyError) as e:
            if log:
                log(f"  openai chat falhou: {type(e).__name__}")
        time.sleep(2)
    return None


def tts(text: str, voice: str = "ash", instructions: str = "", log=None, speed: float = 1.0) -> Path | None:
    """Narracao em mp3. Cache por modelo+voz+instrucoes+velocidade+texto: trocar a
    voz ou as instrucoes gera audio novo, nunca devolve o antigo.
    speed: testado em 2026-09, o gpt-4o-mini-tts aceita e encurta o audio de fato
    (1.5 deixou 8,1 s em 4,9 s); acima de ~1.1 pode soar esticado."""
    if not available():
        return None
    speed = float(speed or 1.0)
    out = AI_DIR / f"tts_{_key(TTS_MODEL, voice, instructions, f'{speed:.2f}', text)}.mp3"
    if out.exists():
        return out
    body = {"model": TTS_MODEL, "voice": voice, "input": text, "response_format": "mp3"}
    if instructions:
        body["instructions"] = instructions
    if abs(speed - 1.0) > 1e-6:
        body["speed"] = round(speed, 2)
    for attempt in range(2):
        try:
            r = requests.post(f"{BASE}/audio/speech", headers=_headers(), json=body, timeout=180)
            if r.status_code == 200 and r.content:
                out.write_bytes(r.content)
                _log_usage("tts_chars", len(text), "caracteres")
                return out
            if log:
                log(f"  openai tts: HTTP {r.status_code} {r.text[:120]}")
            if r.status_code in (401, 402, 403):
                return None
        except requests.RequestException as e:
            if log:
                log(f"  openai tts falhou: {type(e).__name__}")
        time.sleep(2)
    return None


def transcribe_words(audio_path: Path, language: str = "pt", log=None) -> list[tuple[str, float, float]] | None:
    """Tempo por palavra da narracao (whisper-1). Cache por arquivo."""
    if not available():
        return None
    data = audio_path.read_bytes()
    cache = AI_DIR / f"stt_{hashlib.sha1(data).hexdigest()[:20]}.json"
    if cache.exists():
        return [tuple(w) for w in json.loads(cache.read_text("utf-8"))]
    files = {"file": (audio_path.name, data, "audio/mpeg")}
    form = {"model": STT_MODEL, "response_format": "verbose_json", "language": language, "timestamp_granularities[]": "word"}
    for attempt in range(2):
        try:
            r = requests.post(f"{BASE}/audio/transcriptions", headers=_headers(), files=files, data=form, timeout=180)
            if r.status_code == 200:
                js = r.json()
                words = [(str(w["word"]), round(float(w["start"]), 3), round(float(w["end"]), 3)) for w in js.get("words", [])]
                _log_usage("stt_seconds", float(js.get("duration", 0.0)), "segundos")
                cache.write_text(json.dumps(words, ensure_ascii=False), "utf-8")
                return words
            if log:
                log(f"  openai stt: HTTP {r.status_code} {r.text[:120]}")
            if r.status_code in (401, 402, 403):
                return None
        except (requests.RequestException, ValueError, KeyError) as e:
            if log:
                log(f"  openai stt falhou: {type(e).__name__}")
        time.sleep(2)
    return None


def usage_summary() -> str:
    if not USAGE.exists():
        return "sem uso registrado"
    data = json.loads(USAGE.read_text("utf-8"))
    day = time.strftime("%Y-%m-%d")
    d = data.get(day, {})
    tok = d.get("chat_tokens", 0)
    chars = d.get("tts_chars", 0)
    secs = d.get("stt_seconds", 0)
    # precos de referencia (USD): gpt-4o-mini ~0.0004/1k tokens; tts ~0.015/min; whisper 0.006/min
    est = tok / 1000 * 0.0004 + (chars / 1000 * 1.0 / 60) * 0.015 * 6 + secs / 60 * 0.006
    return f"hoje: {tok} tokens de texto, {chars} caracteres de voz, {secs:.0f}s transcritos (~US$ {est:.3f})"
