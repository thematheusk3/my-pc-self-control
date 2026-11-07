"""Janela em foco no PC.

KDE (Wayland ou X11): o Wayland não deixa um programa comum ver as janelas dos outros; quem sabe é
o KWin. O painel carrega no KWin um script pequeno que, a cada troca de foco (ou de título da
janela em foco, no máximo a cada 10 s), escreve uma linha "SMFOCUS {...}" no log do KWin; o painel acompanha o journal
(`journalctl --user -f`, só mensagens do próprio processo do KWin) e guarda a última.
Custo: zero quando nada muda; uma linha no journal por troca de janela.

Outros ambientes X11: lê a janela ativa com `xprop` a cada coleta.

Fechar as janelas de um processo ("Finalizar" educado, como clicar no X): no KDE, um script de uma vez só no
KWin chama `closeWindow()` nas janelas do PID e responde no journal ("SMCLOSE <token> <n>"); em outros X11,
`wmctrl -ic`. O app pode perguntar se quer salvar. Sem janela, quem chama manda SIGTERM.
"""
from __future__ import annotations

import json
import logging
import secrets
import shutil
import subprocess
import threading
import time

from .config import ROOT
from .desktop import as_user, graphical_session, session_env, user_env

log = logging.getLogger(__name__)
SCRIPT_NAME = "self-monitoring-focus"
MARK = "SMFOCUS "
SCRIPT = r"""
// self-monitoring: informa a janela em foco (lido do journal pelo painel)
const active = () => (workspace.activeWindow !== undefined ? workspace.activeWindow : workspace.activeClient);
const activated = workspace.windowActivated || workspace.clientActivated;
const watched = [];
const TITLE_EVERY = 10000;   // ms: títulos que mudam sem parar (terminal, player) no máximo a cada 10 s
let lastReport = 0;
function report(w) {
  lastReport = Date.now();
  print("SMFOCUS " + JSON.stringify(w ? {
    caption: String(w.caption || ""), cls: String(w.resourceClass || ""), name: String(w.resourceName || ""),
    pid: w.pid || null, desktop: String(w.desktopFileName || ""), fullscreen: !!w.fullScreen
  } : null));
}
function watch(w) {
  if (!w || watched.indexOf(w) >= 0) return;
  watched.push(w);
  w.captionChanged.connect(function () { if (active() === w && Date.now() - lastReport >= TITLE_EVERY) report(w); });
}
activated.connect(function (w) { watch(w); report(w); });
const removed = workspace.windowRemoved || workspace.clientRemoved;
if (removed) removed.connect(function (w) { const i = watched.indexOf(w); if (i >= 0) watched.splice(i, 1); });
watch(active());
report(active());
"""


CLOSE_MARK = "SMCLOSE "
CLOSE_SCRIPT = r"""
// self-monitoring: fecha as janelas de um processo (como clicar no X) e informa quantas
(function () {
  const list = workspace.windowList ? workspace.windowList() : workspace.clientList();
  let n = 0;
  for (let i = 0; i < list.length; i++) {
    const w = list[i];
    if (w.pid === %d && (w.normalWindow || w.dialog) && !w.deleted) { w.closeWindow(); n++; }
  }
  print("SMCLOSE %s " + n);
})();
"""


def _busctl(*args: str, timeout: float = 5) -> subprocess.CompletedProcess:
    return subprocess.run(as_user(["busctl", "--user", *args]), capture_output=True, text=True, timeout=timeout,
                          env=user_env())


def parse_line(line: str) -> tuple[bool, dict | None]:
    """'... SMFOCUS {json}' -> (é linha nossa, janela ou None)."""
    i = line.find(MARK)
    if i < 0:
        return False, None
    try:
        w = json.loads(line[i + len(MARK):])
    except ValueError:
        return False, None
    if w is not None and not isinstance(w, dict):
        return False, None
    return True, w


class FocusMonitor:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.window: dict | None = None
        self.since: float | None = None
        self.source: str | None = None
        self.proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._last_check = 0.0
        self._session: dict | None = None
        self.mode: str | None = None      # kwin | x11 | None
        self.script_file = ROOT / "data" / "kwin-focus.js"
        self._closing: dict[str, dict] = {}    # token -> {"ev", "n"} (pedidos de fechar janelas)

    # ---------- KWin ----------
    def kwin_available(self) -> bool:
        try:
            return _busctl("status", "org.kde.KWin", timeout=3).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _script_loaded(self) -> bool:
        try:
            r = _busctl("call", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "isScriptLoaded", "s",
                        SCRIPT_NAME)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0 and r.stdout.strip() == "b true"

    def _run_script(self, path, name: str) -> bool:
        """Carrega e executa um script no KWin (descarrega antes um de mesmo nome)."""
        _busctl("call", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s", name)
        r = _busctl("call", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript", "ss", str(path), name)
        if r.returncode:
            log.warning("KWin recusou o script %s: %s", name, r.stderr.strip())
            return False
        sid = r.stdout.split()[-1]
        if sid == "-1":
            return False
        # KWin 6: /Scripting/ScriptN; KWin 5: /N
        for obj in (f"/Scripting/Script{sid}", f"/{sid}"):
            if _busctl("call", "org.kde.KWin", obj, "org.kde.kwin.Script", "run").returncode == 0:
                return True
        return False

    def _load_script(self) -> bool:
        try:
            self.script_file.parent.mkdir(parents=True, exist_ok=True)
            self.script_file.write_text(SCRIPT)
            self.script_file.chmod(0o644)
            # versão anterior (outra execução do painel) é descarregada e a atual carregada
            if self._run_script(self.script_file, SCRIPT_NAME):
                log.info("script de janela em foco carregado no KWin")
                return True
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("falha ao carregar o script de foco no KWin: %s", e)
        return False

    # ---------- fechar as janelas de um processo ----------
    def close_windows(self, pid: int, timeout: float = 2.0) -> int | None:
        """Pede para as janelas do processo fecharem. Devolve quantas, ou None se não deu para saber
        (sem KWin/X11, ou o KWin não respondeu a tempo)."""
        if self.mode is None:
            self.tick(force=True)
        try:
            if self.mode == "kwin":
                return self._kwin_close(int(pid), timeout)
            if self.mode == "x11":
                return self._x11_close(int(pid))
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("falha ao fechar as janelas do PID %s: %s", pid, e)
        return None

    def _kwin_close(self, pid: int, timeout: float) -> int | None:
        token = secrets.token_hex(6)
        name = f"self-monitoring-close-{token}"
        path = self.script_file.with_name(f"kwin-close-{token}.js")
        ev = threading.Event()
        with self.lock:
            self._closing[token] = {"ev": ev, "n": None}
        try:
            path.write_text(CLOSE_SCRIPT % (pid, token))
            path.chmod(0o644)
            if not self._run_script(path, name):
                return None
            ev.wait(timeout)
            with self.lock:
                return self._closing[token]["n"]
        finally:
            with self.lock:
                self._closing.pop(token, None)
            try:
                _busctl("call", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s", name)
            except (OSError, subprocess.SubprocessError):
                pass
            path.unlink(missing_ok=True)

    def _close_reply(self, line: str) -> None:
        parts = line[line.find(CLOSE_MARK) + len(CLOSE_MARK):].split()
        if len(parts) < 2 or not parts[1].isdigit():
            return
        with self.lock:
            w = self._closing.get(parts[0])
            if w:
                w["n"] = int(parts[1])
                w["ev"].set()

    def _x11_close(self, pid: int) -> int | None:
        if not shutil.which("wmctrl"):
            return None
        env = session_env()
        out = subprocess.run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=3, env=env).stdout
        ids = [f[0] for f in (line.split() for line in out.splitlines()) if len(f) > 2 and f[2] == str(pid)]
        for wid in ids:
            subprocess.run(["wmctrl", "-ic", wid], capture_output=True, timeout=3, env=env)
        return len(ids)

    def _follow(self) -> None:
        """Acompanha o journal do usuário (só mensagens do processo do KWin)."""
        while not self._stop.is_set():
            try:
                self.proc = subprocess.Popen(
                    as_user(["journalctl", "--user", "-f", "-n", "0", "-o", "cat", "_COMM=kwin_wayland", "_COMM=kwin_x11"]),
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=user_env())
                for line in self.proc.stdout:
                    if CLOSE_MARK in line:
                        self._close_reply(line)
                        continue
                    ours, w = parse_line(line)
                    if ours:
                        self._set(w, "kwin")
            except OSError as e:
                log.warning("journalctl indisponível para a janela em foco: %s", e)
                return
            if not self._stop.wait(5):
                log.info("journalctl (janela em foco) terminou; reiniciando")

    def _set(self, w: dict | None, source: str) -> None:
        with self.lock:
            same = (self.window or {}).get("pid") == (w or {}).get("pid") and \
                   (self.window or {}).get("cls") == (w or {}).get("cls")
            if not same or self.since is None:
                self.since = time.time()
            self.window, self.source = w, source

    def start(self) -> None:
        threading.Thread(target=self._follow, daemon=True, name="focus-journal").start()

    def stop(self) -> None:
        """Ao encerrar o painel: tira o script do KWin (para de escrever no journal)."""
        self._stop.set()
        if self.proc:
            self.proc.terminate()
        if self.mode == "kwin":
            try:
                _busctl("call", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s",
                        SCRIPT_NAME, timeout=3)
            except (OSError, subprocess.SubprocessError):
                pass

    # ---------- X11 genérico ----------
    def _x11(self) -> dict | None:
        env = session_env()
        if not env.get("DISPLAY") or not shutil.which("xprop"):
            return None
        try:
            out = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True,
                                 timeout=2, env=env).stdout
            wid = out.strip().split()[-1]
            if not wid.startswith("0x") or int(wid, 16) == 0:
                return None
            props = subprocess.run(["xprop", "-id", wid, "_NET_WM_NAME", "WM_CLASS", "_NET_WM_PID"],
                                   capture_output=True, text=True, timeout=2, env=env).stdout
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return None
        w = {"caption": "", "cls": "", "name": "", "pid": None, "desktop": "", "fullscreen": False}
        for line in props.splitlines():
            k, _, v = line.partition(" = ")
            if k.startswith("_NET_WM_NAME"):
                w["caption"] = v.strip().strip('"')
            elif k.startswith("WM_CLASS"):
                parts = [p.strip().strip('"') for p in v.split(",")]
                w["name"], w["cls"] = parts[0], parts[-1]
            elif k.startswith("_NET_WM_PID") and v.strip().isdigit():
                w["pid"] = int(v.strip())
        return w

    # ---------- estado ----------
    def tick(self, force: bool = False) -> None:
        """Chamado pelo coletor. A cada 30 s: vê se há sessão/KWin e garante o script carregado
        (o KWin o perde ao reiniciar ou relogar)."""
        now = time.time()
        if now - self._last_check < 30 and not force:
            return
        self._last_check = now
        self._session = graphical_session()
        if self.kwin_available():
            self.mode = "kwin"
            if not self._script_loaded():
                self._load_script()
        elif self._session and self._session.get("type") == "x11":
            self.mode = "x11"
        else:
            self.mode = None
            self._set(None, None)

    def status(self) -> dict:
        if self.mode == "x11":
            self._set(self._x11(), "x11")
        s = self._session
        with self.lock:
            w = dict(self.window) if self.window else None
            since = self.since
        return {"window": w, "since": since if w else None, "source": self.mode,
                "locked": bool(s and s.get("locked")), "session": bool(s)}
