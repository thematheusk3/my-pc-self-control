"""Servidor HTTP (stdlib) com arquivos estáticos, API JSON, SSE e autenticação por senha."""
from __future__ import annotations

import ipaddress
import json
import logging
import mimetypes
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import api
from .api import Response, routes  # noqa: F401  (registra rotas)
from .auth import MIN_LEN, Auth
from .config import ROOT
from .sampler import Context

log = logging.getLogger(__name__)
WEB = ROOT / "web"
COOKIE = "sm_session"
PUBLIC = {"/login", "/setup", "/manifest.webmanifest", "/icon.svg", "/css/app.css"}


class Handler(BaseHTTPRequestHandler):
    ctx: Context
    auth: Auth
    server_version = "self-monitoring"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silencia log por requisição
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---------- helpers ----------
    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not headers or "Cache-Control" not in headers:
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200, headers: dict | None = None) -> None:
        self._send(status, json.dumps(obj, separators=(",", ":")).encode(), "application/json", headers)

    def _session_id(self) -> str | None:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else None

    def _authed(self) -> bool:
        if not self.ctx.config.auth_enabled:
            return True
        return self.auth.valid(self._session_id())

    def _is_local(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _session_cookie(self, sid: str, max_age: int = 30 * 24 * 3600) -> dict:
        return {"Set-Cookie": f"{COOKIE}={sid}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict"}

    def _form(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return {k: v[0] for k, v in parse_qs(self.rfile.read(min(n, 10_000)).decode(errors="replace")).items()}

    def _redirect(self, location: str, headers: dict | None = None) -> None:
        self._send(303, b"", "text/plain", {"Location": location, **(headers or {})})

    def json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return None
        try:
            return json.loads(self.rfile.read(min(n, 1_000_000)))
        except ValueError:
            return None

    # ---------- dispatch ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        self.query = parse_qs(url.query)
        path = url.path

        auth_on = self.ctx.config.auth_enabled
        if auth_on and not self.auth.has_password() and path not in ("/setup", "/css/app.css", "/icon.svg"):
            return self._redirect("/setup") if not path.startswith("/api/") else \
                self._json({"error": "senha ainda não cadastrada"}, 401)

        if path == "/setup":
            return self._setup(method)
        if path == "/login" and method == "POST":
            return self._login()
        if path == "/logout" and method == "POST":
            self.auth.end_session(self._session_id())
            return self._redirect("/login", self._session_cookie("", 0))

        if path not in PUBLIC and not self._authed():
            if path.startswith("/api/"):
                return self._json({"error": "não autenticado"}, 401)
            return self._redirect("/login")

        if path == "/api/auth/password" and method == "POST":
            return self._change_password()

        if path.startswith("/api/"):
            if path == "/api/stream" and method == "GET":
                return self._stream()
            fn, params = api.match(method, path)
            if fn is None:
                return self._json({"error": "não encontrado"}, 405 if params == "method" else 404)
            if method == "POST" and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self._json({"error": "Content-Type deve ser application/json"}, 415)
            try:
                res = fn(self, **params)
            except Exception as e:  # noqa: BLE001
                log.exception("erro em %s", path)
                return self._json({"error": str(e)}, 500)
            if isinstance(res, Response):
                if isinstance(res.body, (bytes, bytearray)):   # arquivo (ex.: ícone)
                    hdrs = dict(res.headers or {})
                    return self._send(res.status, bytes(res.body), hdrs.pop("Content-Type", "application/octet-stream"), hdrs)
                return self._json(res.body, res.status, res.headers)
            return self._json(res)

        if method != "GET":
            return self._json({"error": "método não permitido"}, 405)
        self._static(path)

    def _login(self) -> None:
        ip = self.client_address[0]
        if (wait := self.auth.locked_for(ip)):
            return self._redirect(f"/login?bloqueado={wait}")
        pw = self._form().get("password", "")
        if self.auth.check_password(pw):
            self.auth.clear_fails(ip)
            sid = self.auth.create_session(ip, self.headers.get("User-Agent", ""))
            return self._redirect("/", self._session_cookie(sid))
        self.auth.register_fail(ip)
        time.sleep(1)  # desacelera força bruta
        self._redirect("/login?erro=1")

    def _setup(self, method: str) -> None:
        """Cadastro da primeira senha: só se ainda não existe e só a partir da própria máquina."""
        if self.auth.has_password():
            return self._redirect("/")
        if not self._is_local():
            return self._static("/setup-remote.html")
        if method == "GET":
            return self._static("/setup.html")
        f = self._form()
        pw, pw2 = f.get("password", ""), f.get("password2", "")
        if pw != pw2:
            return self._redirect("/setup?erro=diferentes")
        if len(pw) < MIN_LEN:
            return self._redirect("/setup?erro=curta")
        self.auth.set_password(pw)
        sid = self.auth.create_session(self.client_address[0], self.headers.get("User-Agent", ""))
        log.info("senha cadastrada pelo navegador local")
        self._redirect("/", self._session_cookie(sid))

    def _change_password(self) -> None:
        body = self.json() or {}
        ip = self.client_address[0]
        if (wait := self.auth.locked_for(ip)):
            return self._json({"error": f"Muitas tentativas. Aguarde {wait}s."}, 429)
        if not self.auth.check_password(str(body.get("current", ""))):
            self.auth.register_fail(ip)
            time.sleep(1)
            return self._json({"error": "Senha atual incorreta."}, 403)
        new = str(body.get("new", ""))
        if len(new) < MIN_LEN:
            return self._json({"error": f"A nova senha precisa ter pelo menos {MIN_LEN} caracteres."}, 400)
        self.auth.set_password(new)
        self.auth.end_all_except(self._session_id())
        log.info("senha alterada pelo painel (%s); outras sessões encerradas", ip)
        self._json({"ok": True})

    def _static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        elif path == "/login":
            path = "/login.html"
        elif path.endswith(".html") and path not in ("/index.html", "/login.html", "/setup.html", "/setup-remote.html"):
            return self._send(404, b"not found", "text/plain")
        target = (WEB / path.lstrip("/")).resolve()
        if not target.is_relative_to(WEB) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript"
        elif target.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype.endswith(("json", "javascript", "svg+xml")):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _stream(self) -> None:
        """Server-Sent Events: um snapshot a cada amostra."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True
        last = -1
        try:
            while True:
                with self.ctx.cond:
                    self.ctx.cond.wait_for(lambda: self.ctx.seq != last, timeout=15)
                if self.ctx.seq == last:
                    self.wfile.write(b": ping\n\n")
                else:
                    last = self.ctx.seq
                    data = json.dumps(self.ctx.snapshot(), separators=(",", ":"))
                    self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return  # cliente fechou a conexão (comum no celular); não é erro
        super().handle_error(request, client_address)


def make_server(ctx: Context) -> Server:
    handler = type("BoundHandler", (Handler,), {"ctx": ctx, "auth": Auth()})
    return Server((ctx.config.host, ctx.config.port), handler)
