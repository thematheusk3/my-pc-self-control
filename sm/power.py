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
