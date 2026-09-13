"""Publicacao de Reels no Instagram pela API oficial ("Instagram API with Instagram Login").

O que a documentacao da Meta (developers.facebook.com, consultada em set/2026) manda, e que este
modulo segue:

- Conta profissional (Criador ou Empresa). Token de usuario de longa duracao (60 dias), gerado no
  painel do app; renova com GET /refresh_access_token?grant_type=ig_refresh_token (o token precisa
  ter mais de 24 h e ainda estar valido). Aqui a renovacao e automatica quando passa de 30 dias.
- Publicar e em dois passos: POST /me/media (media_type=REELS, video_url, cover_url, caption,
  share_to_feed) cria um "container"; espera status_code FINISHED; POST /me/media_publish publica.
  video_url e cover_url precisam ser enderecos publicos: o notebook serve os dois por um tunel
  temporario da Cloudflare (tunnel.py) so enquanto a Meta baixa. Capa: JPEG.
- Reels: MP4 H.264 + AAC, 9:16, ate 15 min, ate 1 GB. Legenda ate 2200 caracteres, ate 30 hashtags.
- Limite: 100 posts por 24 h por conta (GET /me/content_publishing_limit).
- A API nao agenda data futura. O agendamento e local (make.py agendar).
- Marca de conteudo gerado por IA: a API nao tem campo; a Meta rotula sozinha pelo sinal C2PA.

Segredo no .env: INSTAGRAM_ACCESS_TOKEN. Registro dos posts em output/<dia>/instagram_publicado.json.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from ..config import CACHE, OUTPUT
from . import tunnel
from .tiktok import _env, videos_of

API = "https://graph.instagram.com/v23.0"
TOKEN_FILE = CACHE / "instagram_token.json"
PUBLISHED_NAME = "instagram_publicado.json"
LOG_FILE = CACHE / "instagram_publish.log"
CAPTION_MAX = 2200
HASHTAG_MAX = 30
STATUS_TIMEOUT = 10 * 60
REFRESH_AFTER_DAYS = 30
TIMEOUT = 60


class InstagramError(RuntimeError):
    def __init__(self, message: str, code: int = 0, http: int = 0):
        super().__init__(message)
        self.code, self.http = code, http


# ----------------------------------------------------------------- token

def missing_credentials() -> list[str]:
    return [] if _env("INSTAGRAM_ACCESS_TOKEN") else ["INSTAGRAM_ACCESS_TOKEN"]


def _load_token_file() -> dict:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text("utf-8"))
        except ValueError:
            pass
    return {}


def access_token() -> str:
    """Token do .env; se ja foi renovado, vale o renovado (cache). Renova quando passa de 30 dias."""
    env_tok = _env("INSTAGRAM_ACCESS_TOKEN")
    if not env_tok:
        raise InstagramError("falta INSTAGRAM_ACCESS_TOKEN no .env")
    st = _load_token_file()
    if st.get("origem") != env_tok[-12:]:   # token novo colado no .env: zera o cache
        st = {"origem": env_tok[-12:], "token": env_tok, "renovado_em": datetime.now().isoformat(timespec="minutes")}
        _save_token_file(st)
    age = (datetime.now() - datetime.fromisoformat(st["renovado_em"])).days
    if age >= REFRESH_AFTER_DAYS:
        try:
            r = requests.get("https://graph.instagram.com/refresh_access_token", timeout=TIMEOUT,
                             params={"grant_type": "ig_refresh_token", "access_token": st["token"]})
            data = r.json()
            if r.ok and data.get("access_token"):
                st.update(token=data["access_token"], renovado_em=datetime.now().isoformat(timespec="minutes"),
                          expira_em_s=data.get("expires_in"))
                _save_token_file(st)
        except (requests.RequestException, ValueError):
            pass   # segue com o token atual; tenta renovar de novo no proximo post
    return st["token"]


def _save_token_file(st: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(st, indent=1), "utf-8")


def token_age_days() -> int | None:
    st = _load_token_file()
    return (datetime.now() - datetime.fromisoformat(st["renovado_em"])).days if st.get("renovado_em") else None


# ----------------------------------------------------------------- API

def _call(method: str, path: str, **params) -> dict:
    params["access_token"] = access_token()
    r = requests.request(method, f"{API}/{path.lstrip('/')}", timeout=TIMEOUT,
                         **({"params": params} if method == "GET" else {"data": params}))
    try:
        data = r.json()
    except ValueError:
        raise InstagramError(f"resposta invalida ({r.status_code}): {r.text[:160]}", http=r.status_code)
    if "error" in data:
        err = data["error"]
        raise InstagramError(err.get("message", "erro"), code=int(err.get("code", 0)), http=r.status_code)
    return data


def me() -> dict:
    return _call("GET", "me", fields="id,username,account_type")


def publishing_limit() -> dict:
    data = _call("GET", "me/content_publishing_limit", fields="quota_usage,config")
    return (data.get("data") or [{}])[0]


def create_container(video_url: str, cover_url: str | None, caption: str) -> str:
    params = {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"}
    if cover_url:
        params["cover_url"] = cover_url
    return str(_call("POST", "me/media", **params)["id"])


def wait_container(container_id: str, log, timeout: int = STATUS_TIMEOUT) -> str:
    t0 = time.time()
    while True:
        st = _call("GET", container_id, fields="status_code,status")
        code = st.get("status_code", "")
        if code == "FINISHED":
            return code
        if code in ("ERROR", "EXPIRED"):
            raise InstagramError(f"container {code}: {st.get('status', '')}")
        if time.time() - t0 > timeout:
            raise InstagramError("o Instagram nao terminou de processar o video em 10 min")
        log(f"    processando ({code or '...'}), {int(time.time() - t0)}s")
        time.sleep(10)


def publish_container(container_id: str) -> str:
    return str(_call("POST", "me/media_publish", creation_id=container_id)["id"])


def permalink(media_id: str) -> str | None:
    try:
        return _call("GET", media_id, fields="permalink").get("permalink")
    except InstagramError:
        return None


# ----------------------------------------------------------------- legenda e arquivos

def caption_for(mp4: Path) -> str:
    """Legenda + hashtags + creditos do <video>.instagram.txt (kit). Sem o kit, cai no .txt do TikTok."""
    kit_txt = Path(f"{mp4.with_suffix('')}.instagram.txt")
    src = kit_txt if kit_txt.exists() else mp4.with_suffix(".txt")
    if not src.exists():
        return mp4.stem.replace("-", " ")
    text = src.read_text("utf-8")
    if kit_txt.exists():
        sec = {k: v.strip() for k, v in re.findall(r"^(TÍTULO|LEGENDA|HASHTAGS)\n(.*?)(?=^\S+\n|\Z)", text, re.S | re.M)}
        extras = [l for l in text.splitlines() if l.lower().startswith(("fonte:", "imagens:", "música:", "musica:"))]
        body = sec.get("LEGENDA") or sec.get("TÍTULO", "")
        tags = sec.get("HASHTAGS", "").split()[:HASHTAG_MAX]
        parts = [body, " ".join(tags), "\n".join(extras)]
    else:
        parts = [text.strip()]
    cap = "\n\n".join(p for p in parts if p).strip()
    if len(cap) > CAPTION_MAX:   # corta creditos primeiro, nunca a legenda
        cap = "\n\n".join(p for p in parts[:2] if p).strip()[:CAPTION_MAX]
    return cap


def cover_for(mp4: Path) -> Path | None:
    p = Path(f"{mp4.with_suffix('')}.capa-instagram.jpg")
    return p if p.exists() else None


def load_published(day: str) -> list[dict]:
    f = OUTPUT / day / PUBLISHED_NAME
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text("utf-8"))
    except ValueError:
        return []


def save_published(day: str, records: list[dict]) -> None:
    (OUTPUT / day / PUBLISHED_NAME).write_text(json.dumps(records, indent=1, ensure_ascii=False), "utf-8")


def pending(day: str) -> list[Path]:
    done = {r["arquivo"] for r in load_published(day) if r.get("status") == "PUBLISHED"}
    return [p for p in videos_of(day) if p.name not in done]


def _append_log(line: str) -> None:
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M ") + line + "\n")
    except OSError:
        pass


# ----------------------------------------------------------------- publicar

def publish_file(path: Path, day: str, log) -> dict:
    caption = caption_for(path)
    cover = cover_for(path)
    log(f"  {path.name}: {path.stat().st_size // 1_000_000} MB, capa {'sim' if cover else 'nao'}, legenda {len(caption)} caracteres")
    with tunnel.serve([path, cover], log) as urls:   # a Meta baixa o video e a capa daqui; fecha ao sair do bloco
        log("    pedindo ao Instagram para baixar")
        cid = create_container(urls[path], urls[cover] if cover else None, caption)
        wait_container(cid, log)
        media_id = publish_container(cid)
    link = permalink(media_id)
    log(f"    publicado: {link or media_id}")
    return {"arquivo": path.name, "hora": datetime.now().isoformat(timespec="minutes"), "status": "PUBLISHED",
            "post_id": media_id, "link": link}


def dry_run(day: str, one: bool, log) -> int:
    vids, todo = videos_of(day), pending(day)
    log(f"[simulacao] Instagram, pasta output/{day}: {len(vids)} video(s), {len(todo)} ainda nao publicado(s)")
    for i, p in enumerate(todo[:1] if one else todo):
        cap = caption_for(p)
        log(f"  {i + 1}. {p.name}  capa: {'sim' if cover_for(p) else 'NAO (rode python make.py kit)'}")
        log(f"     legenda ({len(cap)}/{CAPTION_MAX}): {cap.splitlines()[0][:90]}")
    miss = missing_credentials()
    log("  credenciais: " + ("faltam no .env: " + ", ".join(miss) if miss else "ok"))
    if not missing_credentials():
        try:
            u = me()
            log(f"  conta: @{u.get('username')} ({u.get('account_type', '?')}), token com {token_age_days()} dia(s)")
            q = publishing_limit()
            log(f"  posts nas ultimas 24 h: {q.get('quota_usage', '?')} de {q.get('config', {}).get('quota_total', 100)}")
        except (InstagramError, requests.RequestException) as e:
            log(f"  conta: falhou ({e})")
    tunnel.check(log)
    log("[simulacao] nada foi enviado.")
    return 0


def publish_day(day: str, one: bool, dry: bool, log) -> int:
    if dry:
        return dry_run(day, one, log)
    miss = missing_credentials()
    if miss:
        log("Instagram: faltam no .env: " + ", ".join(miss))
        return 2
    if tunnel.missing():
        log("Instagram: falta bin/cloudflared.exe (tunel para a Meta baixar o video)")
        return 2
    todo = pending(day)
    if not todo:
        log(f"Instagram: nada pendente em output/{day}")
        return 0
    records = load_published(day)
    failed = 0
    for p in todo[:1] if one else todo:
        try:
            rec = publish_file(p, day, log)
        except (InstagramError, tunnel.TunnelError, requests.RequestException) as e:
            rec = {"arquivo": p.name, "hora": datetime.now().isoformat(timespec="minutes"), "status": "FAILED", "motivo": str(e)}
            failed += 1
            log(f"    FALHOU: {e}")
        records = [r for r in records if r["arquivo"] != p.name] + [rec]
        save_published(day, records)
        _append_log(f"{day} {p.name} {rec['status']} {rec.get('link') or rec.get('motivo', '')}")
    return 1 if failed else 0
