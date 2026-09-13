"""Publicacao no TikTok pela Content Posting API (Login Kit + Direct Post / Inbox).

O que a documentacao oficial (developers.tiktok.com, consultada em set/2026) manda, e que este
modulo segue:

- Login: https://www.tiktok.com/v2/auth/authorize/ (code) e https://open.tiktokapis.com/v2/oauth/token/.
  O redirect_uri precisa ser https, absoluto, sem parametros, registrado no app e com o dominio
  verificado. Localhost nao serve. Por isso o login aqui e "cole a URL de retorno" (fluxo manual).
- access_token vale 24 h; refresh_token vale 365 dias e pode ser trocado a cada refresh.
- FILE_UPLOAD em pedacos: cada pedaco entre 5 MB e 64 MB; o ultimo pode ir ate 128 MB (absorve a
  sobra); arquivo com menos de 5 MB sobe inteiro; no maximo 1000 pedacos; total_chunk_count e o
  tamanho dividido pelo pedaco, arredondado para baixo; cabecalho Content-Range: bytes a-b/total;
  resposta 206 nos pedacos do meio e 201 no ultimo. Arquivo ate 4 GB.
- Titulo (legenda) ate 2200 caracteres UTF-16. Campo is_aigc marca conteudo gerado por IA.
- App sem auditoria: publicacao direta so em conta privada e o video sai SELF_ONLY.
- Limites: 6 pedidos por minuto por token no init, cerca de 15 posts por dia por conta,
  5 rascunhos pendentes (inbox) por 24 h.
- A API nao agenda data futura. O agendamento e local (make.py tiktok-cron).

Segredos (TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI opcional) vem do ambiente
ou do .env na raiz, do mesmo jeito que viral/ai.py le a chave da OpenAI. Tokens ficam em
cache/tiktok_token.json. Nada disso e impresso, nem em erro.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import webbrowser
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..config import CACHE, OUTPUT, ROOT

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2/post/publish"
SCOPES = "user.info.basic,video.upload,video.publish"
DEFAULT_REDIRECT = "https://papiro.work/tiktok"

TOKEN_FILE = CACHE / "tiktok_token.json"
LOG_FILE = CACHE / "tiktok_publish.log"
PUBLISHED_NAME = "publicados.json"

TITLE_MAX = 2200
MIN_CHUNK = 5 * 1024 * 1024          # "at least 5 MB": usa a leitura mais exigente
MAX_CHUNK = 64 * 1000 * 1000         # "no greater than 64 MB": usa a leitura mais exigente
MAX_LAST = 128 * 1000 * 1000
MAX_FILE = 4 * 1000 * 1000 * 1000
MAX_CHUNKS = 1000
DEFAULT_CHUNK = 10 * 1000 * 1000     # mesmo valor do exemplo da documentacao
PRIVACY_LEVELS = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")
MODES = ("direct", "inbox")
REFRESH_MARGIN = 15 * 60             # renova o access_token 15 min antes de vencer
STATUS_TIMEOUT = 10 * 60
STATUS_EVERY = 5
TASK_PREFIX = "TikTokUsina-publish-"


class TikTokError(Exception):
    """Erro da API: codigo curto e mensagem curta. Nunca carrega token."""

    def __init__(self, code: str, message: str = "", http: int = 0):
        self.code = code
        self.message = (message or "")[:200]
        self.http = http
        super().__init__(f"{code}: {self.message}" if self.message else code)


# ----------------------------------------------------------------- segredos e tokens

def _env(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text("utf-8").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def missing_credentials() -> list[str]:
    return [n for n in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET") if not _env(n)]


def redirect_uri() -> str:
    return _env("TIKTOK_REDIRECT_URI") or DEFAULT_REDIRECT


def load_token() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("access_token") and data.get("refresh_token") else None


def save_token(resp: dict, previous: dict | None = None) -> dict:
    now = time.time()
    data = dict(previous or {})
    data.update({
        "access_token": resp["access_token"],
        "refresh_token": resp.get("refresh_token") or data.get("refresh_token"),
        "open_id": resp.get("open_id") or data.get("open_id"),
        "scope": resp.get("scope") or data.get("scope"),
        "expires_at": now + float(resp.get("expires_in", 86400)),
        "saved_at": now,
    })
    if resp.get("refresh_expires_in"):
        data["refresh_expires_at"] = now + float(resp["refresh_expires_in"])
    CACHE.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=1), "utf-8")
    return data


def logged_in() -> bool:
    return load_token() is not None


def _token_post(form: dict) -> dict:
    r = requests.post(TOKEN_URL, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=60)
    try:
        body = r.json()
    except ValueError:
        raise TikTokError("bad_response", f"HTTP {r.status_code}", r.status_code)
    if r.status_code >= 400 or body.get("error") or not body.get("access_token"):
        raise TikTokError(str(body.get("error") or "token_error"), str(body.get("error_description") or ""), r.status_code)
    return body


def exchange_code(code: str) -> dict:
    key, secret = _env("TIKTOK_CLIENT_KEY"), _env("TIKTOK_CLIENT_SECRET")
    body = _token_post({"client_key": key, "client_secret": secret, "code": code,
                        "grant_type": "authorization_code", "redirect_uri": redirect_uri()})
    return save_token(body)


def refresh_token(tok: dict) -> dict:
    key, secret = _env("TIKTOK_CLIENT_KEY"), _env("TIKTOK_CLIENT_SECRET")
    body = _token_post({"client_key": key, "client_secret": secret,
                        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    return save_token(body, tok)


def access_token() -> str:
    """Token valido, renovando com margem. Levanta TikTokError se nao houver login."""
    if missing_credentials():
        raise TikTokError("no_credentials", "faltam TIKTOK_CLIENT_KEY/TIKTOK_CLIENT_SECRET no .env")
    tok = load_token()
    if not tok:
        raise TikTokError("not_logged_in", "rode: python make.py tiktok-login")
    if tok.get("refresh_expires_at") and time.time() > tok["refresh_expires_at"]:
        raise TikTokError("refresh_expired", "login venceu (365 dias); rode: python make.py tiktok-login")
    if time.time() > float(tok.get("expires_at", 0)) - REFRESH_MARGIN:
        tok = refresh_token(tok)
    return tok["access_token"]


# ----------------------------------------------------------------- login manual

def auth_url(state: str) -> str:
    q = {"client_key": _env("TIKTOK_CLIENT_KEY"), "scope": SCOPES, "response_type": "code",
         "redirect_uri": redirect_uri(), "state": state}
    return AUTH_URL + "?" + urlencode(q)


def parse_callback(url: str, state: str) -> str:
    """Extrai o code da URL colada. Confere o state. Funcao pura (sem rede)."""
    url = url.strip()
    if not url:
        raise TikTokError("empty_url", "nada colado")
    qs = parse_qs(urlparse(url).query)
    if qs.get("error"):
        raise TikTokError(qs["error"][0], (qs.get("error_description") or [""])[0])
    got_state = (qs.get("state") or [""])[0]
    if got_state != state:
        raise TikTokError("state_mismatch", "a URL colada nao e desta tentativa de login; rode de novo")
    code = (qs.get("code") or [""])[0]
    if not code:
        raise TikTokError("no_code", "a URL colada nao tem code=; cole a URL inteira da barra de enderecos")
    return code


def login(log, open_browser: bool = True) -> int:
    miss = missing_credentials()
    if miss:
        log("Faltam no .env: " + ", ".join(miss))
        log("Veja no README: 'Publicar no TikTok', passos 1 a 5.")
        return 2
    state = secrets.token_urlsafe(24)
    url = auth_url(state)
    log("Login no TikTok (fluxo manual, porque o TikTok exige redirect https registrado):")
    log("1. Vai abrir o navegador. Se nao abrir, copie e cole este endereco:")
    log("   " + url)
    log("2. Entre na conta do canal e clique em Autorizar.")
    log(f"3. O TikTok vai mandar voce para {redirect_uri()} (pode aparecer pagina de erro ou 404: nao importa).")
    log("4. Copie a URL INTEIRA da barra de enderecos e cole aqui.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        pasted = input("URL de retorno: ")
    except (EOFError, KeyboardInterrupt):
        log("cancelado")
        return 1
    try:
        code = parse_callback(pasted, state)
        tok = exchange_code(code)
    except TikTokError as e:
        log(f"Login falhou: {e.code} {e.message}".rstrip())
        return 1
    except requests.RequestException as e:
        log(f"Login falhou: rede ({type(e).__name__})")
        return 1
    log("Login ok. Permissoes: " + str(tok.get("scope") or SCOPES))
    log(f"Token guardado em {TOKEN_FILE} (nao compartilhe esse arquivo).")
    _append_log("login ok")
    return 0


# ----------------------------------------------------------------- chamadas da API

def _api(path: str, payload: dict) -> dict:
    headers = {"Authorization": "Bearer " + access_token(), "Content-Type": "application/json; charset=UTF-8"}
    r = requests.post(API + path, json=payload, headers=headers, timeout=60)
    try:
        body = r.json()
    except ValueError:
        raise TikTokError("bad_response", f"HTTP {r.status_code}", r.status_code)
    err = body.get("error") or {}
    code = err.get("code", "ok")
    if r.status_code >= 400 or code != "ok":
        if code == "unaudited_client_can_only_post_to_private_accounts":
            _note_audit("unaudited")
        raise TikTokError(str(code or "http_error"), str(err.get("message") or ""), r.status_code)
    return body.get("data") or {}


def _note_audit(hint: str) -> None:
    tok = load_token()
    if tok is not None and tok.get("audit_hint") != hint:
        tok["audit_hint"] = hint
        tok["audit_hint_at"] = datetime.now().isoformat(timespec="minutes")
        TOKEN_FILE.write_text(json.dumps(tok, indent=1), "utf-8")


def creator_info() -> dict:
    return _api("/creator_info/query/", {})


def init_post(mode: str, size: int, plan: dict, title: str, privacy: str, aigc: bool) -> dict:
    source = {"source": "FILE_UPLOAD", "video_size": size,
              "chunk_size": plan["chunk_size"], "total_chunk_count": plan["total_chunk_count"]}
    if mode == "inbox":
        return _api("/inbox/video/init/", {"source_info": source})
    post = {"title": title, "privacy_level": privacy, "disable_duet": False, "disable_comment": False,
            "disable_stitch": False, "video_cover_timestamp_ms": 1000}
    if aigc:
        post["is_aigc"] = True
    return _api("/video/init/", {"post_info": post, "source_info": source})


def upload_file(path: Path, upload_url: str, plan: dict, log) -> None:
    size = plan["size"]
    with open(path, "rb") as f:
        for i, (start, end) in enumerate(plan["ranges"]):
            f.seek(start)
            chunk = f.read(end - start + 1)
            headers = {"Content-Type": "video/mp4", "Content-Length": str(len(chunk)),
                       "Content-Range": content_range(start, end, size)}
            last = i == len(plan["ranges"]) - 1
            for attempt in (1, 2):
                try:
                    r = requests.put(upload_url, data=chunk, headers=headers, timeout=600)
                    break
                except requests.RequestException as e:
                    if attempt == 2:
                        raise TikTokError("upload_network", type(e).__name__)
                    time.sleep(5)
            if r.status_code not in (200, 201, 206):
                raise TikTokError("upload_failed", f"pedaco {i + 1}/{len(plan['ranges'])} HTTP {r.status_code}", r.status_code)
            log(f"    pedaco {i + 1}/{len(plan['ranges'])} ok ({len(chunk) // 1000} kB){' final' if last else ''}")


def fetch_status(publish_id: str) -> dict:
    return _api("/status/fetch/", {"publish_id": publish_id})


def wait_status(publish_id: str, mode: str, log, timeout: int = STATUS_TIMEOUT) -> dict:
    done = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"}
    t0 = time.time()
    last = ""
    while True:
        st = fetch_status(publish_id)
        status = st.get("status", "")
        if status != last:
            log(f"    status: {status}")
            last = status
        if status in done:
            return st
        if time.time() - t0 > timeout:
            st["status"] = st.get("status") or "PENDENTE"
            st["timeout"] = True
            return st
        time.sleep(STATUS_EVERY)


# ----------------------------------------------------------------- funcoes puras

def content_range(start: int, end: int, total: int) -> str:
    return f"bytes {start}-{end}/{total}"


def plan_chunks(size: int, chunk_size: int = DEFAULT_CHUNK) -> dict:
    """Divide um arquivo em pedacos conforme a regra do TikTok. Sem rede.

    Devolve dict com size, chunk_size, total_chunk_count, ranges [(inicio, fim)] e
    content_ranges (cabecalhos prontos). Levanta ValueError se o arquivo nao cabe na regra.
    """
    if size <= 0:
        raise ValueError("arquivo vazio")
    if size > MAX_FILE:
        raise ValueError("arquivo maior que 4 GB")
    if size < MIN_CHUNK:
        chunk, count = size, 1
    else:
        chunk = max(MIN_CHUNK, min(int(chunk_size), MAX_CHUNK, size))
        count = size // chunk
        while count > MAX_CHUNKS and chunk < MAX_CHUNK:
            chunk = min(chunk * 2, MAX_CHUNK)
            count = size // chunk
        if count > MAX_CHUNKS:
            raise ValueError("arquivo precisaria de mais de 1000 pedacos")
    ranges = []
    for i in range(count):
        start = i * chunk
        end = size - 1 if i == count - 1 else start + chunk - 1
        ranges.append((start, end))
    last_len = ranges[-1][1] - ranges[-1][0] + 1
    if count > 1 and last_len > MAX_LAST:
        raise ValueError("ultimo pedaco maior que 128 MB")
    return {"size": size, "chunk_size": chunk, "total_chunk_count": count, "ranges": ranges,
            "content_ranges": [content_range(s, e, size) for s, e in ranges]}


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def caption_from_txt(text: str, limit: int = TITLE_MAX) -> str:
    """Monta a legenda a partir do .txt: texto, hashtags, fonte e creditos.

    Se passar do limite, corta primeiro os creditos de imagem, depois a fonte, depois o texto
    (em fim de palavra), sempre preservando as hashtags. Funcao pura.
    """
    body, tags, source, credits = [], [], [], []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        low = s.lower()
        if s.startswith("#"):
            tags.append(s)
        elif low.startswith("fonte:"):
            source.append(s)
        elif low.startswith(("imagens:", "imagem:", "creditos", "créditos", "credito")):
            credits.append(s)
        else:
            body.append(s)
    tag_line = " ".join(tags)

    def build(b: str, extras: list[str]) -> str:
        parts = [p for p in [b, tag_line, "\n".join(extras)] if p]
        return "\n\n".join(parts)

    body_text = "\n".join(body)
    for extras in ([*source, *credits], source, []):
        out = build(body_text, extras)
        if utf16_len(out) <= limit:
            return out
    room = limit - utf16_len(build("", []))
    if tag_line:
        room -= 2
    cut = body_text
    while cut and utf16_len(cut) > max(room, 0):
        cut = cut[: max(len(cut) - 1, 0)]
    if room > 20 and " " in cut:
        cut = cut[: cut.rfind(" ")]
    return build(cut.rstrip(), [])


def caption_for(mp4: Path) -> str:
    txt = mp4.with_suffix(".txt")
    if txt.exists():
        return caption_from_txt(txt.read_text("utf-8"))
    meta = mp4.with_suffix(".json")
    if meta.exists():
        try:
            data = json.loads(meta.read_text("utf-8"))
            if data.get("caption"):
                return caption_from_txt(str(data["caption"]))
        except ValueError:
            pass
    return caption_from_txt(mp4.stem.replace("-", " "))


# ----------------------------------------------------------------- pasta do dia

def videos_of(day: str) -> list[Path]:
    d = OUTPUT / day
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.mp4") if p.is_file())


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
    done = {r["arquivo"] for r in load_published(day) if r.get("status") != "FAILED"}
    return [p for p in videos_of(day) if p.name not in done]


def _append_log(line: str) -> None:
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M ") + line + "\n")
    except OSError:
        pass


# ----------------------------------------------------------------- publicar

def _explain(e: TikTokError) -> str:
    hints = {
        "unaudited_client_can_only_post_to_private_accounts": "o app ainda nao foi auditado: deixe a conta do TikTok privada ou use --mode inbox",
        "privacy_level_option_mismatch": "essa privacidade nao esta disponivel para a conta; veja: python make.py tiktok-status",
        "spam_risk_too_many_posts": "limite diario de posts pela API; tente amanha",
        "spam_risk_too_many_pending_share": "ja ha 5 rascunhos esperando no app do TikTok; poste-os la primeiro",
        "spam_risk_user_banned_from_posting": "a conta esta impedida de postar",
        "access_token_invalid": "login venceu; rode: python make.py tiktok-login",
        "scope_not_authorized": "permissao nao concedida no login; rode tiktok-login de novo e aceite tudo",
        "rate_limit_exceeded": "muitos pedidos por minuto; espere 1 minuto",
        "not_logged_in": "rode: python make.py tiktok-login",
        "no_credentials": "coloque TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET no .env",
    }
    extra = hints.get(e.code)
    base = f"{e.code}" + (f" ({e.message})" if e.message else "")
    return base + (f" -> {extra}" if extra else "")


def publish_file(path: Path, mode: str, privacy: str, aigc: bool, log) -> dict:
    size = path.stat().st_size
    plan = plan_chunks(size)
    title = caption_for(path)
    log(f"  {path.name}: {size // 1_000_000} MB, {plan['total_chunk_count']} pedaco(s), modo {mode}"
        + (f", privacidade {privacy}" if mode == "direct" else ""))
    data = init_post(mode, size, plan, title, privacy, aigc)
    publish_id, upload_url = data.get("publish_id", ""), data.get("upload_url", "")
    if not publish_id or not upload_url:
        raise TikTokError("init_incomplete", "resposta sem publish_id/upload_url")
    upload_file(path, upload_url, plan, log)
    st = wait_status(publish_id, mode, log)
    rec = {"arquivo": path.name, "publish_id": publish_id, "hora": datetime.now().isoformat(timespec="minutes"),
           "modo": mode, "privacidade": privacy if mode == "direct" else None, "status": st.get("status"),
           "post_id": (st.get("publicaly_available_post_id") or [None])[0]}
    if st.get("status") == "FAILED":
        rec["motivo"] = st.get("fail_reason")
        if st.get("fail_reason") == "unaudited_client_can_only_post_to_private_accounts":
            _note_audit("unaudited")
    elif st.get("status") == "PUBLISH_COMPLETE" and privacy == "PUBLIC_TO_EVERYONE":
        _note_audit("audited")
    return rec


def _wait_until(hhmm: str, log) -> bool:
    h, m = (int(x) for x in hhmm.split(":"))
    now = datetime.now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target < now:
        return False
    log(f"Esperando {hhmm} ({int((target - now).total_seconds() // 60)} min)...")
    while True:
        rest = (target - datetime.now()).total_seconds()
        if rest <= 0:
            return True
        time.sleep(min(60, rest))


def _parse_times(at: str | None) -> list[str]:
    if not at:
        return []
    out = []
    for piece in at.split(","):
        piece = piece.strip()
        if not piece:
            continue
        h, m = piece.split(":")
        out.append(f"{int(h):02d}:{int(m):02d}")
    return out


def dry_run(day: str, mode: str, privacy: str, aigc: bool, at: list[str], one: bool, log) -> int:
    vids = videos_of(day)
    todo = pending(day)
    log(f"[simulacao] pasta output/{day}: {len(vids)} video(s), {len(todo)} ainda nao publicado(s)")
    if not vids:
        log("  nada para publicar (gere com: python make.py daily)")
    plan_list = todo[:1] if one else todo
    for i, p in enumerate(plan_list):
        size = p.stat().st_size
        try:
            plan = plan_chunks(size)
            chunks = f"{plan['total_chunk_count']} pedaco(s) de {plan['chunk_size'] // 1_000_000} MB"
        except ValueError as e:
            chunks = f"NAO CABE: {e}"
        title = caption_for(p)
        when = at[i] if i < len(at) else ("agora" if not at else "sem horario (fica para depois)")
        log(f"  {i + 1}. {p.name}  {size // 1_000_000} MB  {chunks}  quando: {when}")
        log(f"     legenda ({utf16_len(title)}/{TITLE_MAX}): {title.splitlines()[0][:90]}")
    log(f"  modo: {mode}" + (f"  privacidade: {privacy}  marcado como IA: {'sim' if aigc else 'nao'}" if mode == "direct" else "  (rascunho no app; legenda voce cola la)"))
    miss = missing_credentials()
    if miss:
        log("  credenciais: faltam no .env: " + ", ".join(miss) + " (veja README, 'Publicar no TikTok')")
    else:
        log("  credenciais: ok")
    log("  login: " + ("ok (token guardado)" if logged_in() else "nao feito (python make.py tiktok-login)"))
    log("[simulacao] nada foi enviado.")
    return 0


def publish_day(day: str, mode: str, privacy: str | None, at: str | None, one: bool, dry: bool, aigc: bool, log) -> int:
    mode = (mode or "inbox").lower()
    if mode not in MODES:
        log("modo invalido: use --mode direct ou --mode inbox")
        return 2
    privacy = (privacy or "SELF_ONLY").upper()
    if privacy not in PRIVACY_LEVELS:
        log("privacidade invalida: use uma de " + ", ".join(PRIVACY_LEVELS))
        return 2
    try:
        times = _parse_times(at)
    except ValueError:
        log("--at invalido: use HH:MM separados por virgula, ex. --at 09:00,12:00")
        return 2
    if dry:
        return dry_run(day, mode, privacy, aigc, times, one, log)

    todo = pending(day)
    if not todo:
        log(f"output/{day}: nada pendente para publicar.")
        return 0
    try:
        access_token()
    except TikTokError as e:
        log("Nao da para publicar: " + _explain(e))
        return 1
    except requests.RequestException as e:
        log(f"Nao da para publicar: rede ({type(e).__name__})")
        return 1

    if mode == "direct":
        try:
            info = creator_info()
        except TikTokError as e:
            log("creator_info falhou: " + _explain(e))
            return 1
        opts = info.get("privacy_level_options") or []
        if opts and privacy not in opts:
            log(f"privacidade {privacy} nao disponivel para @{info.get('creator_username', '?')}; opcoes: {', '.join(opts)}")
            return 1
        if privacy != "SELF_ONLY" and (load_token() or {}).get("audit_hint") == "unaudited":
            log("Aviso: o app ainda parece nao auditado; o TikTok deixa o video privado mesmo pedindo " + privacy)
        if privacy == "SELF_ONLY":
            log("Aviso: privacidade SELF_ONLY: so voce ve. Para publico use --privacy PUBLIC_TO_EVERYONE (app auditado).")
        max_sec = info.get("max_video_post_duration_sec")
        if max_sec:
            log(f"Conta @{info.get('creator_username', '?')}: videos ate {max_sec}s")

    if one:
        todo = todo[:1]
    if times:
        todo = todo[: len(times)]

    records = load_published(day)
    failures = 0
    for i, path in enumerate(todo):
        if times:
            if not _wait_until(times[i], log):
                log(f"Horario {times[i]} ja passou hoje: pulado (o video fica pendente para o proximo horario ou para --one).")
                continue
        log(f"Publicando {i + 1}/{len(todo)}:")
        try:
            rec = publish_file(path, mode, privacy, aigc, log)
        except TikTokError as e:
            failures += 1
            log("  FALHOU: " + _explain(e))
            _append_log(f"publish {mode} {path.name} FALHOU {e.code}")
            if e.code in ("spam_risk_too_many_posts", "access_token_invalid", "not_logged_in", "no_credentials",
                          "spam_risk_too_many_pending_share", "unaudited_client_can_only_post_to_private_accounts"):
                break
            continue
        except (requests.RequestException, OSError) as e:
            failures += 1
            log(f"  FALHOU: rede/arquivo ({type(e).__name__})")
            _append_log(f"publish {mode} {path.name} FALHOU {type(e).__name__}")
            continue
        records = [r for r in records if r.get("arquivo") != path.name] + [rec]
        save_published(day, records)
        _append_log(f"publish {mode} {path.name} {rec['status']} publish_id={rec['publish_id']}")
        if rec["status"] == "FAILED":
            failures += 1
            log(f"  FALHOU no TikTok: {rec.get('motivo')}")
        elif rec["status"] == "SEND_TO_USER_INBOX":
            log("  ok: rascunho enviado. Abra a notificacao no app do TikTok, cole a legenda abaixo e poste.")
            log("  legenda: " + caption_for(path).replace("\n", " | ")[:300])
        elif rec["status"] == "PUBLISH_COMPLETE":
            log("  ok: publicado" + (f" (post {rec['post_id']})" if rec.get("post_id") else " (o TikTok ainda pode levar minutos para liberar)"))
        else:
            log("  enviado, mas o TikTok ainda esta processando. Confira depois: python make.py publish --check")
    return 1 if failures else 0


def check_pending(day: str, log) -> int:
    records = load_published(day)
    if not records:
        log(f"output/{day}: nenhum registro de publicacao.")
        return 0
    changed = False
    for r in records:
        if r.get("status") in ("PUBLISH_COMPLETE", "FAILED", "SEND_TO_USER_INBOX"):
            log(f"  {r['arquivo']}: {r['status']}" + (f" ({r.get('motivo')})" if r.get("motivo") else ""))
            continue
        try:
            st = fetch_status(r["publish_id"])
        except TikTokError as e:
            log(f"  {r['arquivo']}: consulta falhou: " + _explain(e))
            continue
        r["status"] = st.get("status")
        r["post_id"] = (st.get("publicaly_available_post_id") or [None])[0]
        if st.get("fail_reason"):
            r["motivo"] = st["fail_reason"]
        changed = True
        log(f"  {r['arquivo']}: {r['status']}")
    if changed:
        save_published(day, records)
    return 0


# ----------------------------------------------------------------- status

def status_report(log) -> int:
    miss = missing_credentials()
    log("Credenciais (.env): " + ("ok" if not miss else "faltam " + ", ".join(miss)))
    tok = load_token()
    if not tok:
        log("Login: nao feito. Rode: python make.py tiktok-login")
        return 0
    exp = datetime.fromtimestamp(float(tok.get("expires_at", 0))).strftime("%d/%m %H:%M")
    rexp = datetime.fromtimestamp(float(tok.get("refresh_expires_at", 0))).strftime("%d/%m/%Y") if tok.get("refresh_expires_at") else "?"
    log(f"Login: feito. Permissoes: {tok.get('scope')}. Token renova sozinho (vence {exp}); login vale ate {rexp}.")
    if miss:
        return 0
    try:
        info = creator_info()
    except TikTokError as e:
        log("creator_info falhou: " + _explain(e))
        return 1
    except requests.RequestException as e:
        log(f"creator_info falhou: rede ({type(e).__name__})")
        return 1
    opts = info.get("privacy_level_options") or []
    log(f"Criador: {info.get('creator_nickname', '?')} (@{info.get('creator_username', '?')})")
    log("Privacidades disponiveis: " + (", ".join(opts) or "?"))
    log(f"Duracao maxima por video: {info.get('max_video_post_duration_sec', '?')}s"
        + ("; comentarios desligados" if info.get("comment_disabled") else ""))
    if "PUBLIC_TO_EVERYONE" in opts:
        log("Conta: publica.")
    elif opts:
        log("Conta: privada (sem PUBLIC_TO_EVERYONE).")
    hint = tok.get("audit_hint")
    if hint == "audited":
        log("Auditoria do app: parece aprovada (um post publico ja completou).")
    elif hint == "unaudited":
        log(f"Auditoria do app: ainda nao (erro visto em {tok.get('audit_hint_at')}). Enquanto isso: conta privada + SELF_ONLY, ou --mode inbox.")
    else:
        log("Auditoria do app: a API nao informa. Se um post direto falhar com unaudited_client_can_only_post_to_private_accounts, o app ainda nao foi auditado.")
    return 0


# ----------------------------------------------------------------- agendador do Windows

def cron_commands(times: list[str], mode: str, privacy: str, python: str, make_py: str, remove: bool = False) -> list[list[str]]:
    """Comandos schtasks, um por horario. Funcao pura (nao executa)."""
    cmds = []
    for t in times:
        name = TASK_PREFIX + t.replace(":", "")
        if remove:
            cmds.append(["schtasks", "/Delete", "/F", "/TN", name])
        else:
            tr = f'"{python}" "{make_py}" publish --one --mode {mode} --privacy {privacy}'
            cmds.append(["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", name, "/ST", t, "/TR", tr])
    return cmds


def cron_install(at: str | None, mode: str, privacy: str, remove: bool, dry: bool, log) -> int:
    times = _parse_times(at or "09:00,12:00,15:00,18:00,21:00")
    mode = (mode or "inbox").lower()
    privacy = (privacy or "SELF_ONLY").upper()
    make_py = str(ROOT / "make.py")
    cmds = cron_commands(times, mode, privacy, sys.executable, make_py, remove)
    verb = "Removendo" if remove else "Criando"
    log(f"{verb} {len(cmds)} tarefa(s) no Agendador do Windows ({', '.join(times)}):")
    if not remove:
        log("  (cada uma roda: publish --one, ou seja, publica o proximo video de output/<hoje>/ ainda nao publicado)")
    failed = 0
    for c in cmds:
        if dry:
            log("  " + subprocess.list2cmdline(c))
            continue
        r = subprocess.run(c, capture_output=True, text=True)
        ok = r.returncode == 0
        failed += 0 if ok else 1
        log(f"  {c[5]}: {'ok' if ok else 'falhou (' + (r.stderr or r.stdout).strip()[:120] + ')'}")
    if dry:
        log("[simulacao] nada foi registrado.")
        return 0
    if not remove and not failed:
        log("Pronto. O PC precisa estar ligado e com voce logado no horario. Para desfazer: python make.py tiktok-cron --remove")
        log(f"Registro de cada execucao em {LOG_FILE}")
    return 1 if failed else 0


def today() -> str:
    return date.today().isoformat()
