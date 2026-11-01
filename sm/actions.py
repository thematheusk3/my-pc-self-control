"""Ações que alteram o sistema. Mantidas separadas dos coletores (leitura)."""
from __future__ import annotations

import os
import signal

import psutil

SIGNALS = {"TERM": signal.SIGTERM, "KILL": signal.SIGKILL, "STOP": signal.SIGSTOP, "CONT": signal.SIGCONT}


class ActionError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def signal_process(pid: int, sig_name: str, create_time: float | None) -> dict:
    sig = SIGNALS.get(sig_name.upper())
    if sig is None:
        raise ActionError(f"Sinal inválido: {sig_name}")
    if pid <= 1 or pid == os.getpid():
        raise ActionError("Este processo é protegido.", 403)
    try:
        p = psutil.Process(pid)
        # evita matar outro processo que reaproveitou o mesmo PID
        if create_time is not None and abs(p.create_time() - float(create_time)) > 1:
            raise ActionError("O processo já terminou (PID reaproveitado).", 409)
        if p.ppid() == 2 or pid == 2:
            raise ActionError("Threads do kernel não podem ser finalizadas.", 403)
        name = p.name()
        p.send_signal(sig)
    except psutil.NoSuchProcess:
        raise ActionError("Processo não existe mais.", 404) from None
    except psutil.AccessDenied:
        raise ActionError("Permissão negada: o processo pertence a outro usuário. "
                          "Rode o servidor como root para gerenciá-lo.", 403) from None
    return {"ok": True, "pid": pid, "name": name, "signal": sig_name.upper()}
