"""Servidor HTTP (stdlib) com arquivos estáticos, API JSON, SSE e autenticação por senha."""
from __future__ import annotations

import ipaddress
import json
import logging
import mimetypes
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import api, audit, clients
from .api import Response, routes  # noqa: F401  (registra rotas)
from .auth import MIN_LEN, Auth
from .config import ROOT
from .sampler import Context

log = logging.getLogger(__name__)
WEB = ROOT / "web"
COOKIE = "sm_session"
FONT = "/fonts/archivo.woff2"
# /api/webhook/notify não usa a sessão: a rota confere o token dela
PUBLIC = {"/login", "/setup", "/manifest.webmanifest", "/icon.svg", "/css/app.css", FONT, "/api/webhook/notify"}


class Handler(BaseHTTPRequestHandler):
    ctx: Context
    auth: Auth
    audit: audit.Audit
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
        return self.auth.valid(self._session_id(), self.client_address[0])

    def _is_local(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _session_cookie(self, sid: str, max_age: int = 30 * 24 * 3600) -> dict:
        return {"Set-Cookie": f"{COOKIE}={sid}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict"}

    MAX_BODY = 1_000_000

    def _read_body(self) -> bytes:
        """Lê SEMPRE o corpo inteiro do pedido, antes de qualquer rota. Com keep-alive (HTTP/1.1),
        um corpo não lido fica na conexão e vira o começo do próximo pedido: ex. o POST de captura
        (corpo "{}") fazia o GET seguinte chegar como "{}GET /api/screenshots" -> 501."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
            self.close_connection = True
        if n <= 0:
            return b""
        if n > self.MAX_BODY:          # grande demais: não lê, e fecha a conexão depois da resposta
            self.close_connection = True
            return b""
        return self.rfile.read(n)

    def _form(self) -> dict:
        return {k: v[0] for k, v in parse_qs(self._body[:10_000].decode(errors="replace")).items()}

    def _redirect(self, location: str, headers: dict | None = None) -> None:
        self._send(303, b"", "text/plain", {"Location": location, **(headers or {})})

    def json(self):
        if not self._body:
            return None
        try:
            return json.loads(self._body)
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
        self._body = self._read_body()
        url = urlsplit(self.path)
        self.query = parse_qs(url.query)
        path = url.path

        auth_on = self.ctx.config.auth_enabled
        if auth_on and not self.auth.has_password() and path not in ("/setup", "/css/app.css", "/icon.svg", FONT, "/api/webhook/notify"):
            return self._redirect("/setup") if not path.startswith("/api/") else \
                self._json({"error": "senha ainda não cadastrada"}, 401)

        if path == "/setup":
            return self._setup(method)
        if path == "/login" and method == "POST":
            return self._login()
        if path == "/logout" and method == "POST":
            if self.auth.valid(self._session_id()):
                self.auth.record("logout", self.client_address[0], self.headers.get("User-Agent", ""))
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
            act = audit.describe(fn.__name__, self, self.json(), params) if method == "POST" else None
            try:
                res = fn(self, **params)
            except Exception as e:  # noqa: BLE001
                log.exception("erro em %s", path)
                if act:
                    self._audit(act, False, str(e))
                return self._json({"error": str(e)}, 500)
            if act:
                failed = isinstance(res, Response) and res.status >= 400
                self._audit(act, not failed, (res.body or {}).get("error") if failed and isinstance(res.body, dict) else None)
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
        ua = self.headers.get("User-Agent", "")
        if self.auth.check_password(pw):
            self.auth.clear_fails(ip)
            sid = self.auth.create_session(ip, ua)
            self.auth.record("login", ip, ua)
            self._announce("login", ip, ua, key=sid)
            return self._redirect("/", self._session_cookie(sid))
        self.auth.register_fail(ip)
        if self.auth.locked_for(ip):
            self.auth.record("lockout", ip, ua)
            self._announce("lockout", ip, ua, key=ip)
        else:
            self.auth.record("fail", ip, ua)
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
        ua = self.headers.get("User-Agent", "")
        sid = self.auth.create_session(self.client_address[0], ua)
        self.auth.record("setup", self.client_address[0], ua)
        log.info("senha cadastrada pelo navegador local")
        self._redirect("/", self._session_cookie(sid))

    def who(self) -> dict:
        sid = self._session_id()
        ua = self.headers.get("User-Agent", "")
        return {"ip": clients.describe(self.client_address[0])["ip"], "device": clients.device(ua),
                "session": self.auth.public_id(sid) if sid else None}

    def _audit(self, act: tuple, ok: bool, error: str | None = None) -> None:
        cat, text, merge = act
        self.audit.record(cat, text, self.who(), ok, error, merge)

    def _announce(self, event: str, ip: str, ua: str, key: str) -> None:
        """Todo login (e todo bloqueio por senha errada) aparece na tela do PC, sempre: não depende dos
        alertas estarem ligados. Também vira alerta (painel/ntfy) conforme a aba Alertas."""
        alerts = self.ctx.alerts

        def run() -> None:
            who = clients.summary(ip, ua, wait=2)
            if event == "login":
                title, text, rid, level = "🔐 Novo acesso ao painel", f"Entrou com a senha: {who}.", "panel_login", "info"
            else:
                title, text, rid, level = ("⛔ Senha errada no painel",
                                           f"Bloqueado por errar a senha várias vezes: {who}.", "login_fail", "critical")
            log.warning("%s: %s", title, text)
            try:
                from .desktop import notify_pc
                notify_pc(title, text, urgency="critical" if level == "critical" else "normal")
            except Exception:  # noqa: BLE001
                log.exception("aviso de acesso na tela do PC")
            if alerts:
                alerts.event(rid, text, level, key=key, pc=False)

        threading.Thread(target=run, daemon=True, name="announce").start()

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
        self._audit(("panel", "Trocar a senha do painel (outras sessões encerradas)", None), True)
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
        # a fonte não muda entre versões; o resto continua sem cache para a atualização valer na hora
        headers = {"Cache-Control": "public, max-age=2592000"} if target.suffix == ".woff2" else None
        self._send(200, target.read_bytes(), ctype, headers)

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
    request_queue_size = 128     # o padrão (5) recusa conexões quando a página pede vários arquivos de uma vez
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return  # cliente fechou a conexão (comum no celular); não é erro
        super().handle_error(request, client_address)


def make_server(ctx: Context) -> Server:
    aud = audit.Audit()
    if getattr(ctx, "investigator", None):
        ctx.investigator.audit = aud
    handler = type("BoundHandler", (Handler,), {"ctx": ctx, "auth": Auth(), "audit": aud})
    return Server((ctx.config.host, ctx.config.port), handler)
