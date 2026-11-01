"""Desligar / reiniciar a máquina pelo painel (via systemd-logind).

O serviço roda fora de uma sessão gráfica, então o logind pediria senha de admin.
O install.sh instala uma regra do polkit (deploy/50-self-monitoring.rules) que libera
estas ações para o usuário do serviço.

force=True: ignora programas que bloqueiam o desligamento (inibidores, ex.: download,
jogo, "deseja salvar?") e outras sessões logadas. Os programas ainda recebem SIGTERM e,
se não fecharem no tempo limite do systemd, SIGKILL; dados não salvos podem se perder.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)
ACTIONS = {"poweroff": ("PowerOff", "CanPowerOff"), "reboot": ("Reboot", "CanReboot")}
DELAY = 2.0   # s: dá tempo da resposta HTTP chegar ao navegador


class PowerError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _can(action: str) -> str:
    """Pergunta ao logind se a ação é permitida: yes | challenge | no | na."""
    method = ACTIONS[action][1]
    try:
        out = subprocess.run(["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1",
                              "org.freedesktop.login1.Manager", method],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    # saída: s "yes"
    return out.strip().split(" ", 1)[-1].strip('"') or "unknown"


def status() -> dict:
    return {a: _can(a) for a in ACTIONS}


def request(action: str, force: bool) -> dict:
    if action not in ACTIONS:
        raise PowerError("Ação inválida.")
    if not shutil.which("systemctl"):
        raise PowerError("systemctl não encontrado.", 501)
    can = _can(action)
    if can not in ("yes", "unknown") and os.geteuid() != 0:
        raise PowerError(
            "Sem permissão para desligar/reiniciar pelo serviço (o sistema pediria senha de administrador). "
            "Rode  sudo ./install.sh  na pasta do self-monitoring para instalar a regra do polkit.", 403)
    cmd = ["systemctl", action, "--no-wall"]
    if force:
        cmd.append("--check-inhibitors=no")   # ignora inibidores e outras sessões
    log.warning("energia: %s solicitado pelo painel (force=%s)", action, force)

    def run():
        time.sleep(DELAY)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            log.error("energia: '%s' falhou: %s", " ".join(cmd), r.stderr.strip())

    threading.Thread(target=run, daemon=True, name="power").start()
    return {"ok": True, "action": action, "force": force, "in": DELAY}


# ---------- timer ----------
class PowerTimer:
    """Desliga/reinicia num horário. Sempre forçado (ignora o que estiver bloqueando).
    Persistido em data/power-timer.json: sobrevive a reinícios do serviço."""

    WARN_AT = (300, 60)   # avisos na tela do PC: 5 min e 1 min antes

    def __init__(self) -> None:
        from .config import ROOT
        self.file = ROOT / "data" / "power-timer.json"
        self.lock = threading.Lock()
        self.timer: dict | None = None
        self._warned: set[int] = set()
        try:
            t = __import__("json").loads(self.file.read_text())
            if t.get("at", 0) > time.time() - 120:   # passou do horário com o serviço parado há pouco: executa
                self.timer = t
            else:
                self.file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        threading.Thread(target=self._loop, daemon=True, name="power-timer").start()

    def status(self) -> dict | None:
        with self.lock:
            if not self.timer:
                return None
            return {**self.timer, "seconds_left": max(0, round(self.timer["at"] - time.time()))}

    def schedule(self, action: str, minutes: float) -> dict:
        import json
        if action not in ACTIONS:
            raise PowerError("Ação inválida.")
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            raise PowerError("Tempo inválido.") from None
        if not 1 <= minutes <= 24 * 60:
            raise PowerError("O timer deve ser de 1 minuto a 24 horas.")
        can = _can(action)
        if can not in ("yes", "unknown") and os.geteuid() != 0:
            raise PowerError("Sem permissão para desligar/reiniciar pelo serviço. Rode  sudo ./install.sh .", 403)
        with self.lock:
            self.timer = {"action": action, "at": time.time() + minutes * 60, "created": time.time()}
            self._warned = {w for w in self.WARN_AT if w >= minutes * 60}
            self.file.write_text(json.dumps(self.timer))
        self._notify(f"{'Desligamento' if action == 'poweroff' else 'Reinício'} agendado pelo painel",
                     f"O PC vai {VERB[action]} às {time.strftime('%H:%M', time.localtime(self.timer['at']))} "
                     f"(em {_fmt(minutes * 60)}), sem esperar programas abertos.")
        log.warning("energia: %s agendado para daqui %.0f min", action, minutes)
        return self.status()

    def cancel(self) -> None:
        with self.lock:
            had, self.timer = self.timer, None
            self.file.unlink(missing_ok=True)
        if had:
            self._notify("Timer cancelado", f"O {'desligamento' if had['action'] == 'poweroff' else 'reinício'} "
                                            "agendado pelo painel foi cancelado.")

    def _loop(self) -> None:
        while True:
            time.sleep(2)
            with self.lock:
                t = self.timer
            if not t:
                continue
            left = t["at"] - time.time()
            for w in self.WARN_AT:
                if left <= w and w not in self._warned:
                    self._warned.add(w)
                    self._notify("O PC vai " + VERB[t["action"]], f"Em {_fmt(left)}. Salve o que estiver fazendo.",
                                 urgency="critical")
            if left <= 0:
                with self.lock:
                    self.timer = None
                    self.file.unlink(missing_ok=True)
                try:
                    request(t["action"], force=True)
                except PowerError as e:
                    log.error("energia: timer não conseguiu %s: %s", t["action"], e)

    @staticmethod
    def _notify(title: str, body: str, urgency: str = "normal") -> None:
        try:
            from .desktop import notify_pc
            notify_pc(title, body, urgency=urgency, icon="system-shutdown")
        except Exception:  # noqa: BLE001  (aviso é opcional; o timer não pode falhar por isso)
            pass


VERB = {"poweroff": "desligar", "reboot": "reiniciar"}


def _fmt(sec: float) -> str:
    sec = int(round(sec))
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h}h{m:02d}"
    if sec >= 60:
        return f"{sec // 60} min"
    return f"{sec} s"
