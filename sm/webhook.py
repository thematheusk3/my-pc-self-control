"""Notificações recebidas por webhook: outros programas avisam pelo painel.

POST /api/webhook/notify com `Authorization: Bearer <token>` e JSON (ver FIELDS abaixo e o README).
A notificação entra no histórico de alertas, vai aos canais ligados (tela do PC, ntfy) e aparece no Mural.

Desligado até o usuário gerar um token. O token é mostrado uma vez; aqui fica só o hash dele.
A rota fica fora da sessão de login, por isso os limites: erros por IP e pedidos aceitos por minuto.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import deque

from .auth import _write_private
from .config import ROOT

log = logging.getLogger(__name__)
FILE = ROOT / "data" / "webhook.json"
MAX_FAILS = 5               # tokens errados por IP dentro de WINDOW
MAX_ACCEPTED = 60           # notificações aceitas dentro de WINDOW
WINDOW = 60
LEVELS = ("info", "warning", "critical")
DISPLAYS = ("auto", "toast", "fullscreen")
RX_ICON = re.compile(r"[a-z0-9-]{1,40}")


class WebhookError(Exception):
    def __init__(self, message: str, status: int = 400, retry: int | None = None) -> None:
        super().__init__(message)
        self.status, self.retry = status, retry


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean(body) -> dict:
    """Valida o JSON recebido. Texto é guardado cru: quem exibe (frontend) escapa."""
    if not isinstance(body, dict):
        raise WebhookError("Envie um objeto JSON.")
    title = str(body.get("title") or "").strip()
    if not title:
        raise WebhookError('O campo "title" é obrigatório.')
    level = body.get("level", "info")
    if level not in LEVELS:
        raise WebhookError(f'"level" deve ser um destes: {", ".join(LEVELS)}.')
    url = str(body.get("url") or "").strip()
    if url and not re.fullmatch(r"https?://[^\s\"'<>]{1,500}", url):
        raise WebhookError('"url" deve começar com http:// ou https://.')
    icon = str(body.get("icon") or "").strip()
    if icon and not RX_ICON.fullmatch(icon) and (len(icon) > 8 or icon.isascii()):
        raise WebhookError('"icon" deve ser o nome de um ícone ou um emoji.')
    display = body.get("display", "auto")
    if display not in DISPLAYS:
        raise WebhookError(f'"display" deve ser um destes: {", ".join(DISPLAYS)}.')
    try:      # 0: o Mural usa o tempo padrão do nível (info 5 s, warning 8 s, critical 12 s, tela inteira 20 s)
        duration = int(body.get("duration") or 0)
        duration = max(3, min(300, duration)) if duration else 0
    except (TypeError, ValueError):
        raise WebhookError('"duration" deve ser um número de segundos.') from None
    return {"title": title[:120], "text": str(body.get("message") or "").strip()[:1000], "level": level, "icon": icon,
            "url": url, "source": str(body.get("source") or "").strip()[:60], "duration": duration, "display": display,
            "silent": bool(body.get("silent"))}


class Webhook:
    def __init__(self, alerts, now=time.time) -> None:
        self.alerts, self.now = alerts, now
        self.lock = threading.Lock()
        self.fails: dict[str, deque] = {}
        self.accepted: deque = deque()
        try:
            self.state = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}

    def status(self) -> dict:
        s = self.state
        return {"enabled": bool(s.get("hash")), "created": s.get("created"), "last_used": s.get("last_used"),
                "count": s.get("count", 0), "hint": s.get("hint")}

    def new_token(self) -> str:
        """Gera um token (invalida o anterior). É a única vez que ele fica visível."""
        token = "smw_" + secrets.token_urlsafe(32)
        with self.lock:
            self.state = {"hash": _hash(token), "created": self.now(), "hint": token[-4:], "count": 0}
            _write_private(FILE, self.state)
        return token

    def revoke(self) -> None:
        with self.lock:
            self.state = {}
            _write_private(FILE, self.state)

    def authorize(self, header: str | None, ip: str) -> None:
        now = self.now()
        with self.lock:
            q = self.fails.setdefault(ip, deque())
            while q and q[0] < now - WINDOW:
                q.popleft()
            if not q:
                del self.fails[ip]
            if len(q) >= MAX_FAILS:
                raise WebhookError("Muitas tentativas com token errado. Aguarde.", 429, retry=WINDOW)
            stored = self.state.get("hash")
        if not stored:
            raise WebhookError("Webhook desligado: gere um token na aba Alertas.", 403)
        scheme, _, token = (header or "").partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(_hash(token.strip()), stored):
            return
        with self.lock:
            self.fails.setdefault(ip, deque()).append(now)
        log.warning("webhook: token inválido vindo de %s", ip)
        raise WebhookError("Token inválido.", 401)

    def receive(self, body) -> dict:
        n = clean(body)
        now = self.now()
        with self.lock:
            while self.accepted and self.accepted[0] < now - WINDOW:
                self.accepted.popleft()
            if len(self.accepted) >= MAX_ACCEPTED:
                raise WebhookError("Limite de notificações por minuto atingido.", 429, retry=WINDOW)
            self.accepted.append(now)
            self.state.update(last_used=now, count=self.state.get("count", 0) + 1)
            _write_private(FILE, self.state)
        a = self.alerts.external(**n)
        return {"ok": True, "id": a["seq"]}
