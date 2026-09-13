"""Link publico temporario para o video e a capa, servido pelo proprio notebook.

A API do Instagram nao recebe arquivo: ela baixa o video e a capa de um endereco publico
(video_url / cover_url). Em vez de hospedar em algum servico, o notebook serve os arquivos
por alguns minutos atraves de um "quick tunnel" da Cloudflare (bin/cloudflared.exe, sem
conta, endereco aleatorio *.trycloudflare.com). Fechou o post, fecha o tunel: nada fica
guardado fora da maquina e nao ha nada para limpar.

O servidor local so entrega os arquivos registrados, atras de um caminho aleatorio,
e responde HEAD e Range (o baixador da Meta pede pedacos do video).
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from ..config import ROOT

BIN = ROOT / "bin" / "cloudflared.exe"
TUNNEL_TIMEOUT = 60
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CTYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".txt": "text/plain"}


class TunnelError(RuntimeError):
    pass


def cloudflared() -> str | None:
    if BIN.exists():
        return str(BIN)
    return shutil.which("cloudflared")


def missing() -> list[str]:
    return [] if cloudflared() else ["bin/cloudflared.exe"]


# ----------------------------------------------------------------- servidor local

class _Handler(BaseHTTPRequestHandler):
    files: dict[str, Path] = {}     # "/<token>/<nome>" -> caminho
    hits: list[str] = []

    def log_message(self, fmt, *args):   # silencio; o publish ja narra
        pass

    def _lookup(self) -> Path | None:
        return self.files.get(self.path.split("?", 1)[0])

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head: bool):
        p = self._lookup()
        if not p or not p.exists():
            self.send_error(404)
            return
        size = p.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
        partial = False
        if m and (m.group(1) or m.group(2)):
            partial = True
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:                                     # "bytes=-500": ultimos 500
                start = max(0, size - int(m.group(2)))
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", CTYPES.get(p.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        self.hits.append(f"{self.command} {p.name} {start}-{end}")
        if head:
            return
        try:
            with open(p, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class LocalServer:
    def __init__(self, files: list[Path]):
        self.token = secrets.token_urlsafe(18)
        _Handler.files = {f"/{self.token}/{p.name}": p for p in files}
        _Handler.hits = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def path_of(self, p: Path) -> str:
        return f"/{self.token}/{p.name}"

    @property
    def hits(self) -> list[str]:
        return list(_Handler.hits)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        _Handler.files = {}


# ----------------------------------------------------------------- tunel

class Tunnel:
    """cloudflared tunnel --url http://127.0.0.1:<porta>; o endereco publico sai no stderr."""

    def __init__(self, port: int, timeout: int = TUNNEL_TIMEOUT):
        exe = cloudflared()
        if not exe:
            raise TunnelError("falta bin/cloudflared.exe (baixe de github.com/cloudflare/cloudflared/releases)")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate", "--protocol", "http2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            creationflags=flags)
        self.url: str | None = None
        self.lines: list[str] = []
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        t0 = time.time()
        while not self.url and time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                break
            time.sleep(0.3)
        if not self.url:
            self.close()
            tail = " | ".join(l.strip()[-120:] for l in self.lines[-3:])
            raise TunnelError(f"o tunel da Cloudflare nao abriu em {timeout}s ({tail or 'sem saida'})")

    def _pump(self):
        for line in self.proc.stderr:   # type: ignore[union-attr]
            self.lines.append(line)
            if not self.url:
                m = URL_RE.search(line)
                if m:
                    self.url = m.group(0)
        # o processo fechou o stderr

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ----------------------------------------------------------------- uso

@contextmanager
def serve(files: list[Path], log=None):
    """with serve([mp4, capa]) as urls: urls[mp4] e urls[capa] sao enderecos publicos validos so ate o fim do bloco."""
    files = [p for p in files if p]
    srv = LocalServer(files)
    tun = None
    try:
        tun = Tunnel(srv.port)
        if log:
            log(f"    tunel aberto ({tun.url.split('//')[1]}), porta local {srv.port}")
        # o tunel leva alguns segundos para propagar; confere antes de entregar o link
        probe = f"{tun.url}{srv.path_of(files[0])}"
        for _ in range(20):
            try:
                if requests.head(probe, timeout=15).status_code in (200, 206):
                    break
            except requests.RequestException:
                pass
            time.sleep(3)
        else:
            raise TunnelError("o tunel abriu mas o endereco publico nao responde")
        yield {p: f"{tun.url}{srv.path_of(p)}" for p in files}
    finally:
        if tun:
            tun.close()
        srv.close()
        if log:
            log(f"    tunel fechado ({len(srv.hits)} acesso(s))")


def check(log=print) -> bool:
    """Abre o tunel com um arquivo minusculo, baixa pelo endereco publico e fecha."""
    if missing():
        log("  tunel: falta bin/cloudflared.exe")
        return False
    probe = Path(__file__).with_name("__init__.py")
    try:
        with serve([probe], log) as urls:
            r = requests.get(urls[probe], timeout=30)
            ok = r.status_code == 200 and r.content == probe.read_bytes()
            r2 = requests.get(urls[probe], headers={"Range": "bytes=0-3"}, timeout=30)
            ok = ok and r2.status_code == 206 and len(r2.content) == 4
        log("  tunel: ok (arquivo baixado pelo endereco publico, Range funciona)" if ok else "  tunel: abriu, mas o conteudo nao bateu")
        return ok
    except (TunnelError, requests.RequestException) as e:
        log(f"  tunel: falhou ({e})")
        return False
