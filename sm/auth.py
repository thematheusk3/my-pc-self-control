"""Autenticação por senha.

- A senha é guardada só como hash scrypt em data/auth.json.
- Login cria uma sessão aleatória (cookie), persistida em data/sessions.json.
- Trocar a senha encerra todas as outras sessões.
- Tentativas erradas por IP são limitadas.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from .config import ROOT

DATA = ROOT / "data"
AUTH_FILE = DATA / "auth.json"
SESSIONS_FILE = DATA / "sessions.json"
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
        with self._lock:
            self._sessions[sid] = {"exp": time.time() + SESSION_TTL, "ip": ip, "agent": agent[:200],
                                   "created": time.time()}
            self._save_sessions()
        return sid

    def valid(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return False
            if s["exp"] < time.time():
                del self._sessions[sid]
                self._save_sessions()
                return False
            return True

    def end_session(self, sid: str | None) -> None:
        with self._lock:
            if sid and self._sessions.pop(sid, None):
                self._save_sessions()

    def end_all_except(self, keep: str | None) -> None:
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if k == keep}
            self._save_sessions()
