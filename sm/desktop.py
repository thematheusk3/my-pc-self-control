"""Sessão gráfica (bloquear/desbloquear) e aplicativos instalados (listar/abrir).

Desbloqueio remoto: o painel pede ao systemd-logind para desbloquear a sessão do usuário
(`loginctl unlock-session`); o bloqueador de tela do KDE (e do GNOME) obedece. O dono da sessão
pode fazer isso sem privilégio extra. Para "entrar pelo celular" ao ligar o PC, use login
automático + sessão iniciando bloqueada (tools/login-pelo-celular.sh).

Usuário da área de trabalho: normalmente o próprio usuário do serviço. Com o serviço rodando como root
(install.sh --root), os comandos que falam com a sessão (PipeWire, KScreen, notificações...) rodam como o dono
da área de trabalho (SM_DESKTOP_USER, gravado pelo instalador; senão o do login automático ou da sessão aberta).

Abrir aplicativos: roda o .desktop dentro do gerenciador de serviços do usuário
(`systemd-run --user`), que já tem o ambiente da sessão gráfica (WAYLAND_DISPLAY etc.).
"""
from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
import pwd
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)
SERVICE_UID = os.getuid()
SERVICE_USER = pwd.getpwuid(SERVICE_UID).pw_name
LANGS = [os.environ.get("LANG", "pt_BR.UTF-8").split(".")[0], "pt_BR", "pt"]
LOGIN_CONFS = ("/etc/plasmalogin.conf", "/etc/sddm.conf")


def _run(cmd: list[str], timeout: float = 10, env: dict | None = None) -> subprocess.CompletedProcess:
    """env != None = comando da sessão do usuário (roda como o dono da área de trabalho)."""
    if env is not None:
        cmd = as_user(cmd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def list_sessions() -> list[dict]:
    """Sessões do logind: [{"session", "uid", "user"}, ...]."""
    try:
        out = subprocess.run(["loginctl", "list-sessions", "--json=short"], capture_output=True, text=True,
                             timeout=10).stdout
        sessions = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        sessions = []
    if not sessions:   # systemd antigo: sem --json; colunas SESSION UID USER ...
        try:
            out = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        for line in out.splitlines():
            f = line.split()
            if len(f) >= 3 and f[1].isdigit():
                sessions.append({"session": f[0], "uid": int(f[1]), "user": f[2]})
    return sessions


def autologin_user() -> str | None:
    for conf in LOGIN_CONFS:
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            cp.read(conf)
        except configparser.Error:
            continue
        if cp.has_option("Autologin", "User"):
            return cp.get("Autologin", "User").strip() or None
    return None


def _desktop_account() -> pwd.struct_passwd:
    me = pwd.getpwuid(SERVICE_UID)
    if SERVICE_UID != 0:
        return me
    names = [os.environ.get("SM_DESKTOP_USER"), autologin_user()]
    names += [s.get("user") for s in list_sessions() if int(s.get("uid", 0)) >= 1000]
    for name in names:
        if name and name != "root":
            try:
                return pwd.getpwnam(name)
            except KeyError:
                continue
    return me


_ACCOUNT = _desktop_account()
UID, GID = _ACCOUNT.pw_uid, _ACCOUNT.pw_gid
USER = _ACCOUNT.pw_name
HOME = Path(_ACCOUNT.pw_dir)
RUNTIME = f"/run/user/{UID}"


def as_user(cmd: list[str]) -> list[str]:
    """Serviço como root: roda o comando como o dono da área de trabalho (o barramento da sessão só
    aceita o próprio usuário)."""
    if SERVICE_UID == 0 and UID != 0:
        return ["setpriv", f"--reuid={UID}", f"--regid={GID}", "--init-groups", "--", *cmd]
    return cmd


def user_env() -> dict:
    """Ambiente para falar com o systemd --user (o serviço roda fora da sessão)."""
    return {**os.environ, "HOME": str(HOME), "USER": USER, "LOGNAME": USER,
            "XDG_RUNTIME_DIR": RUNTIME, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={RUNTIME}/bus"}


_SESSION_ENV: tuple[float, dict] | None = None
SESSION_KEYS = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                "XDG_SESSION_DESKTOP", "XDG_DATA_DIRS", "QT_QPA_PLATFORM", "KDE_FULL_SESSION", "KDE_SESSION_VERSION",
                "DESKTOP_SESSION", "LANG", "LANGUAGE")


def session_env() -> dict:
    """user_env() + variáveis da sessão gráfica (WAYLAND_DISPLAY etc.) exportadas ao systemd --user.
    Necessário para falar com o PipeWire, o KScreen, notificações e o Spectacle."""
    global _SESSION_ENV
    if _SESSION_ENV and time.time() - _SESSION_ENV[0] < 30:
        return dict(_SESSION_ENV[1])
    env = user_env()
    try:
        for line in _run(["systemctl", "--user", "show-environment"], env=env, timeout=5).stdout.splitlines():
            k, _, v = line.partition("=")
            if k in SESSION_KEYS:
                env[k] = v
    except (OSError, subprocess.SubprocessError):
        pass
    _SESSION_ENV = (time.time(), env)
    return dict(env)


def run_in_session(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return _run(cmd, timeout=timeout, env=session_env())


def notify_pc(title: str, body: str, urgency: str = "normal", icon: str = "dialog-information",
              timeout_ms: int = 10000) -> bool:
    """Notificação na tela do PC (notify-send). Retorna False se não foi possível exibir."""
    if not shutil.which("notify-send"):
        return False
    try:
        r = run_in_session(["notify-send", "-a", "self-monitoring", "-u", urgency, "-i", icon,
                            "-t", str(timeout_ms), title, body], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


# ---------- sessão ----------
def graphical_session(uid: int | None = None) -> dict | None:
    """Sessão gráfica (wayland/x11) do dono da área de trabalho (ou de `uid`), se existir."""
    uid = UID if uid is None else uid
    best = None
    for s in list_sessions():
        if int(s.get("uid", -1)) != uid:
            continue
        props = {}
        r = _run(["loginctl", "show-session", str(s["session"]),
                  "-p", "Type", "-p", "LockedHint", "-p", "Active", "-p", "State", "-p", "Class",
                  "-p", "Seat", "-p", "Desktop", "-p", "Timestamp"])
        for line in r.stdout.splitlines():
            k, _, v = line.partition("=")
            props[k] = v
        if props.get("Type") not in ("wayland", "x11") or props.get("Class") != "user":
            continue
        cand = {"id": str(s["session"]), "type": props["Type"], "locked": props.get("LockedHint") == "yes",
                "active": props.get("Active") == "yes", "state": props.get("State"),
                "seat": props.get("Seat") or None, "desktop": props.get("Desktop") or None,
                "since": props.get("Timestamp") or None}
        if best is None or (cand["active"] and not best["active"]):
            best = cand
    return best


def other_desktop_users() -> list[str]:
    """Usuários com sessão gráfica aberta que não são o do painel (painel instalado na conta errada)."""
    seen = {}
    for s in list_sessions():
        uid = int(s.get("uid", -1))
        if uid in (UID, 0) or uid in seen:
            continue
        if graphical_session(uid):
            seen[uid] = s.get("user") or str(uid)
    return list(seen.values())


def autologin_status() -> dict:
    """Lê (sem root) se o login automático + bloqueio ao iniciar estão configurados."""
    auto_user = autologin_user()
    lock_on_start = False
    rc = HOME / ".config" / "kscreenlockerrc"
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        cp.read(rc)
        lock_on_start = cp.get("Daemon", "LockOnStart", fallback="false").strip().lower() == "true"
    except configparser.Error:
        pass
    return {"autologin_user": auto_user, "autologin_me": auto_user == USER, "lock_on_start": lock_on_start}


def session_status() -> dict:
    s = graphical_session()
    return {"user": USER, "service_user": SERVICE_USER, "session": s,
            "other_users": [] if s else other_desktop_users(), **autologin_status()}


class SessionError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def set_locked(lock: bool) -> dict:
    s = graphical_session()
    if not s:
        raise SessionError("Nenhuma sessão gráfica aberta para este usuário: a máquina está na tela de login. "
                           "Veja como ativar o login automático com tela bloqueada na aba Sistema.", 409)
    r = _run(["loginctl", "lock-session" if lock else "unlock-session", s["id"]])
    if r.returncode:
        raise SessionError(f"O logind recusou: {r.stderr.strip() or r.returncode}", 500)
    # o bloqueador leva um instante para atualizar o LockedHint
    for _ in range(10):
        time.sleep(0.3)
        cur = graphical_session()
        if cur and cur["locked"] == lock:
            return {"ok": True, "session": cur}
    return {"ok": True, "session": graphical_session(), "note": "pedido enviado; o estado ainda não mudou"}


# ---------- aplicativos ----------
def data_dirs() -> list[Path]:
    dirs = os.environ.get("XDG_DATA_DIRS") or ""
    try:   # o do usuário (inclui flatpak) é o que vale
        for line in _run(["systemctl", "--user", "show-environment"], env=user_env()).stdout.splitlines():
            if line.startswith("XDG_DATA_DIRS="):
                dirs = line.split("=", 1)[1]
    except (OSError, subprocess.SubprocessError):
        pass
    out = [HOME / ".local/share"] + [Path(d) for d in (dirs or "/usr/local/share:/usr/share").split(":") if d]
    for extra in (HOME / ".local/share/flatpak/exports/share", Path("/var/lib/flatpak/exports/share"),
                  Path("/var/lib/snapd/desktop")):
        if extra not in out:
            out.append(extra)
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def _localized(sec, key: str) -> str | None:
    for lang in LANGS:
        v = sec.get(f"{key}[{lang}]")
        if v:
            return v
    return sec.get(key)


def _app_id(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix().replace("/", "-")


class Apps:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: tuple[float, list[dict], dict[str, dict]] | None = None
        self._icons: dict[str, Path] | None = None

    def list(self) -> list[dict]:
        with self._lock:
            if self._cache and time.time() - self._cache[0] < 60:
                return self._cache[1]
            apps: dict[str, dict] = {}
            for d in data_dirs():
                base = d / "applications"
                if not base.is_dir():
                    continue
                for f in sorted(base.rglob("*.desktop")):
                    aid = _app_id(f, base)
                    if aid in apps:      # o primeiro diretório (do usuário) tem prioridade
                        continue
                    apps[aid] = self._parse(f, aid)
            visible = sorted((a for a in apps.values() if a and a.get("show")),
                             key=lambda a: a["name"].lower())
            by_id = {a["id"]: a for a in visible}
            self._cache = (time.time(), visible, by_id)
            return visible

    def get(self, aid: str) -> dict | None:
        self.list()
        return self._cache[2].get(aid) if self._cache else None

    @staticmethod
    def _parse(path: Path, aid: str) -> dict | None:
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError, OSError):
            return None
        if not cp.has_section("Desktop Entry"):
            return None
        e = cp["Desktop Entry"]
        if e.get("Type") != "Application":
            return {"show": False}
        hidden = e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true"
        only = [x for x in e.get("OnlyShowIn", "").split(";") if x]
        notin = [x for x in e.get("NotShowIn", "").split(";") if x]
        if only and "KDE" not in only:
            hidden = True
        if "KDE" in notin:
            hidden = True
        tryexec = e.get("TryExec")
        if tryexec and not (shutil.which(tryexec) or os.path.exists(tryexec)):
            hidden = True
        cats = [c for c in e.get("Categories", "").split(";") if c]
        return {
            "id": aid, "path": str(path), "show": not hidden,
            "name": _localized(e, "Name") or aid,
            "generic": _localized(e, "GenericName"),
            "comment": _localized(e, "Comment"),
            "icon": e.get("Icon"),
            "categories": cats,
            "category": category(cats),
            "terminal": e.get("Terminal", "").lower() == "true",
            "keywords": (_localized(e, "Keywords") or "").lower(),
            "flatpak": "flatpak" in str(path),
        }

    # ---------- ícones ----------
    def _index_icons(self) -> dict[str, Path]:
        theme = "breeze"
        try:
            cp = configparser.ConfigParser(strict=False, interpolation=None)
            cp.read(HOME / ".config/kdeglobals")
            theme = cp.get("Icons", "Theme", fallback="breeze")
        except configparser.Error:
            pass
        idx: dict[str, tuple[int, Path]] = {}
        # prioridade: tema atual > breeze > hicolor > pixmaps; tamanho: svg > 128 > 64 > 48...
        themes = [theme, "breeze", "breeze-dark", "Adwaita", "hicolor"]
        for prio, th in enumerate(themes):
            for d in data_dirs():
                root = d / "icons" / th
                if not root.is_dir():
                    continue
                for f in root.rglob("*"):
                    if f.suffix not in (".svg", ".png", ".svgz") or "/apps" not in f.as_posix() and th != "hicolor":
                        continue
                    if f.suffix != ".png" or "/scalable/" in f.as_posix():
                        size = 512                     # vetorial: escala bem
                    else:
                        m = re.search(r"/(\d+)(?:x\d+)?(?:@\d+x?)?/", f.as_posix())
                        size = min(int(m.group(1)), 256) if m else 32
                    score = prio * 10000 - size
                    name = f.stem
                    if name not in idx or score < idx[name][0]:
                        idx[name] = (score, f)
        for d in [Path("/usr/share/pixmaps")]:
            if d.is_dir():
                for f in d.iterdir():
                    if f.suffix in (".svg", ".png", ".xpm") and f.stem not in idx:
                        idx[f.stem] = (99999, f)
        return {k: v[1] for k, v in idx.items()}

    def icon_path(self, aid: str) -> Path | None:
        app = self.get(aid)
        if not app or not app.get("icon"):
            return None
        icon = app["icon"]
        if icon.startswith("/"):
            p = Path(icon)
            return p if p.is_file() else None
        with self._lock:
            if self._icons is None:
                t0 = time.time()
                self._icons = self._index_icons()
                log.info("índice de ícones: %d ícones em %.1fs", len(self._icons), time.time() - t0)
            found = self._icons.get(icon) or self._icons.get(icon.removesuffix(".png").removesuffix(".svg"))
        if not found and icon.startswith("steam_icon_"):
            found = steam_cache_icon(icon[len("steam_icon_"):])
        return found

    # ---------- abrir ----------
    def launch(self, aid: str) -> dict:
        app = self.get(aid)
        if not app:
            raise SessionError("Aplicativo não encontrado.", 404)
        s = graphical_session()
        if not s:
            raise SessionError("Ninguém está logado na máquina (tela de login). Entre na sessão primeiro.", 409)
        if shutil.which("kioclient"):
            launcher = ["kioclient", "exec", app["path"]]
        elif shutil.which("gio"):
            launcher = ["gio", "launch", app["path"]]
        else:
            launcher = ["gtk-launch", app["id"].removesuffix(".desktop")]
        unit = "app-selfmon-" + hashlib.sha1(f"{aid}{time.time()}".encode()).hexdigest()[:10]
        # ExitType=cgroup: a unidade dura enquanto o app rodar (o lançador sai logo)
        cmd = ["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
               "-p", "ExitType=cgroup", "--", *launcher]
        r = _run(cmd, env=user_env())
        if r.returncode:
            raise SessionError(f"Falha ao abrir: {r.stderr.strip()[:300]}", 500)
        log.info("app aberto pelo painel: %s", app["name"])
        return {"ok": True, "name": app["name"], "locked": s["locked"]}


STEAM_ROOTS = [HOME / ".local/share/Steam", HOME / ".steam/steam",
               HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]


def steam_cache_icon(appid: str) -> Path | None:
    """A Steam nem sempre instala o ícone do jogo no tema; o cache da biblioteca tem um."""
    if not appid.isdigit():
        return None
    for root in STEAM_ROOTS:
        d = root / "appcache" / "librarycache" / appid
        if not d.is_dir():
            continue
        # ícone quadrado: arquivo com nome em hash (40 hex); senão, a capa
        for f in d.iterdir():
            if f.suffix == ".jpg" and len(f.stem) == 40:
                return f
        for name in ("library_600x900.jpg", "header.jpg"):
            if (d / name).is_file():
                return d / name
        old = root / "appcache" / "librarycache" / f"{appid}_icon.jpg"   # formato antigo
        if old.is_file():
            return old
    return None


CATEGORIES = [
    ("Game", "Jogos"), ("Network", "Internet"), ("AudioVideo", "Multimídia"), ("Audio", "Multimídia"),
    ("Video", "Multimídia"), ("Graphics", "Gráficos"), ("Development", "Desenvolvimento"),
    ("Office", "Escritório"), ("Settings", "Configurações"), ("System", "Sistema"),
    ("Utility", "Utilitários"), ("Education", "Educação"), ("Science", "Educação"),
]


def category(cats: list[str]) -> str:
    for key, label in CATEGORIES:
        if key in cats:
            return label
    return "Outros"


APPS = Apps()
