"""Autenticação por senha.

- A senha é guardada só como hash scrypt em data/auth.json.
- Login cria uma sessão aleatória (cookie), persistida em data/sessions.json.
- Trocar a senha encerra todas as outras sessões.
- Tentativas erradas por IP são limitadas.
- Cada sessão guarda IP, navegador e última atividade (aba Sistema > Quem está acessando, onde dá para
  encerrar qualquer uma). Logins, erros de senha e bloqueios ficam em data/access-log.json.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from .config import ROOT

DATA = ROOT / "data"
AUTH_FILE = DATA / "auth.json"
SESSIONS_FILE = DATA / "sessions.json"
ACCESS_FILE = DATA / "access-log.json"
ACCESS_KEEP = 200
SEEN_SAVE = 300      # grava a última atividade no disco no máximo a cada 5 min por sessão
SESSION_TTL = 30 * 24 * 3600
MIN_LEN = 6
SCRYPT = {"n": 2**14, "r": 8, "p": 1}

MAX_FAILS = 5        # tentativas erradas...
FAIL_WINDOW = 300    # ...dentro desta janela (s)
LOCKOUT = 120        # bloqueiam o IP por este tempo (s)


def _write_private(path: Path, obj) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)


class Auth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._fails: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._load_sessions()
        self.access: deque[dict] = deque(maxlen=ACCESS_KEEP)
        try:
            self.access.extend(json.loads(ACCESS_FILE.read_text())[:ACCESS_KEEP])
        except (OSError, ValueError, TypeError):
            pass

    # ---------- senha ----------
    def has_password(self) -> bool:
        return AUTH_FILE.exists()

    def set_password(self, password: str) -> None:
        if len(password) < MIN_LEN:
            raise ValueError(f"A senha precisa ter pelo menos {MIN_LEN} caracteres.")
        salt = secrets.token_bytes(16)
        h = _hash(password, salt, **SCRYPT)
        _write_private(AUTH_FILE, {"algo": "scrypt", **SCRYPT, "salt": salt.hex(), "hash": h.hex()})

    def check_password(self, password: str) -> bool:
        try:
            d = json.loads(AUTH_FILE.read_text())
            h = _hash(password, bytes.fromhex(d["salt"]), d["n"], d["r"], d["p"])
            return hmac.compare_digest(h.hex(), d["hash"])
        except (OSError, ValueError, KeyError):
            return False

    # ---------- limite de tentativas ----------
    def locked_for(self, ip: str) -> int:
        with self._lock:
            return max(0, int(self._locked_until.get(ip, 0) - time.time()))

    def register_fail(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            fails = [t for t in self._fails.get(ip, []) if now - t < FAIL_WINDOW] + [now]
            self._fails[ip] = fails
            if len(fails) >= MAX_FAILS:
                self._locked_until[ip] = now + LOCKOUT
                self._fails[ip] = []

    def clear_fails(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    # ---------- sessões ----------
    def _load_sessions(self) -> None:
        try:
            data = json.loads(SESSIONS_FILE.read_text())
            now = time.time()
            self._sessions = {k: v for k, v in data.items() if v.get("exp", 0) > now}
        except (OSError, ValueError):
            self._sessions = {}

    def _save_sessions(self) -> None:
        _write_private(SESSIONS_FILE, self._sessions)

    def create_session(self, ip: str, agent: str) -> str:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions[sid] = {"exp": now + SESSION_TTL, "ip": ip, "agent": agent[:200],
                                   "created": now, "seen": now, "last_ip": ip, "saved": now}
            self._save_sessions()
        return sid

    def valid(self, sid: str | None, ip: str | None = None) -> bool:
        if not sid:
            return False
        now = time.time()
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return False
            if s["exp"] < now:
                del self._sessions[sid]
                self._save_sessions()
                return False
            s["seen"] = now
            moved = ip is not None and ip != s.get("last_ip", s.get("ip"))
            if moved:
                s["last_ip"] = ip
            if moved or now - s.get("saved", 0) > SEEN_SAVE:
                s["saved"] = now
                self._save_sessions()
            return True

    @staticmethod
    def public_id(sid: str) -> str:
        """Identifica a sessão na API sem expor o cookie."""
        return hashlib.sha256(sid.encode()).hexdigest()[:16]

    def list_sessions(self, current: str | None) -> list[dict]:
        now = time.time()
        with self._lock:
            items = [{"id": self.public_id(k), "ip": v.get("ip"), "last_ip": v.get("last_ip", v.get("ip")),
                      "agent": v.get("agent", ""), "created": v.get("created"), "seen": v.get("seen", v.get("created")),
                      "expires": v["exp"], "current": k == current}
                     for k, v in self._sessions.items() if v["exp"] > now]
        return sorted(items, key=lambda x: (not x["current"], -(x["seen"] or 0)))

    def end_by_id(self, pub: str) -> dict | None:
        """Encerra a sessão com esse id público; devolve o que ela era (ou None)."""
        with self._lock:
            for k, v in list(self._sessions.items()):
                if self.public_id(k) == pub:
                    del self._sessions[k]
                    self._save_sessions()
                    return v
        return None

    # ---------- histórico de acessos ----------
    def record(self, event: str, ip: str, agent: str = "", **extra) -> None:
        """event: login, setup, fail, lockout, logout, ended (sessão encerrada por outro aparelho)."""
        with self._lock:
            self.access.appendleft({"time": time.time(), "event": event, "ip": ip, "agent": agent[:200], **extra})
            try:
                _write_private(ACCESS_FILE, list(self.access))
            except OSError:
                pass

    def access_log(self) -> list[dict]:
        with self._lock:
            return list(self.access)

    def end_session(self, sid: str | None) -> None:
        with self._lock:
            if sid and self._sessions.pop(sid, None):
                self._save_sessions()

    def end_all_except(self, keep: str | None) -> None:
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if k == keep}
            self._save_sessions()
