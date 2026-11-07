"""Ações que alteram o sistema. Mantidas separadas dos coletores (leitura)."""
from __future__ import annotations

import os
import signal
from typing import Callable

import psutil

SIGNALS = {"TERM": signal.SIGTERM, "KILL": signal.SIGKILL, "STOP": signal.SIGSTOP, "CONT": signal.SIGCONT}

# processos essenciais: finalizá-los derruba a sessão gráfica, o som, a rede ou o próprio sistema.
# Não é bloqueio total (às vezes reiniciar o plasmashell ou o pipewire é justamente o que se quer):
# o painel avisa o que acontece e só segue com a confirmação explícita (confirm_critical).
CRITICAL = {
    "systemd": "o sistema inteiro ou a sessão do usuário cai",
    "systemd-logind": "as sessões e o login param de funcionar",
    "systemd-journal": "o registro do sistema para",
    "systemd-udevd": "dispositivos novos (USB, discos) deixam de ser reconhecidos",
    "dbus-daemon": "a área de trabalho e vários serviços param de se comunicar",
    "dbus-broker": "a área de trabalho e vários serviços param de se comunicar",
    "dbus-broker-lau": "a área de trabalho e vários serviços param de se comunicar",
    "kwin_wayland": "a sessão gráfica fecha (apps abertos e trabalho não salvo são perdidos)",
    "kwin_x11": "as janelas ficam sem bordas e sem gerenciador até ele voltar",
    "Xwayland": "todos os apps X11 (Steam, jogos, Discord...) fecham",
    "Xorg": "a sessão gráfica fecha (apps abertos e trabalho não salvo são perdidos)",
    "plasmashell": "a barra de tarefas e a área de trabalho somem até reiniciar",
    "gnome-shell": "a sessão gráfica fecha ou reinicia",
    "sddm": "a tela de login e a sessão gráfica caem",
    "sddm-helper": "a sessão gráfica cai",
    "plasmalogin": "a tela de login e a sessão gráfica caem",
    "gdm": "a tela de login e a sessão gráfica caem",
    "pipewire": "o som para em todos os apps",
    "pipewire-pulse": "o som para nos apps que usam PulseAudio",
    "wireplumber": "o som deixa de trocar de dispositivo e de volume",
    "NetworkManager": "a rede pode cair (e o painel ficar inacessível pelo celular)",
    "polkitd": "pedidos de senha de administrador param de funcionar",
}


class ActionError(Exception):
    def __init__(self, message: str, status: int = 400, extra: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra or {}


def critical_effect(name: str | None) -> str | None:
    return CRITICAL.get(name or "")


def signal_process(pid: int, sig_name: str, create_time: float | None, confirm_critical: bool = False,
                   close_windows: Callable[[int], int | None] | None = None) -> dict:
    """`close_windows(pid)`: fecha as janelas do processo como clicar no X e devolve quantas (None = não
    deu para saber). Com janelas, "TERM" só pede para fechar (o app pode perguntar se quer salvar);
    sem janela, manda SIGTERM."""
    sig = SIGNALS.get(sig_name.upper())
    if sig is None:
        raise ActionError(f"Sinal inválido: {sig_name}")
    if pid <= 1 or pid == os.getpid():
        raise ActionError("Este processo é protegido.", 403)
    note = None
    try:
        p = psutil.Process(pid)
        # evita matar outro processo que reaproveitou o mesmo PID
        if create_time is not None and abs(p.create_time() - float(create_time)) > 1:
            raise ActionError("O processo já terminou (PID reaproveitado).", 409)
        if p.ppid() == 2 or pid == 2:
            raise ActionError("Threads do kernel não podem ser finalizadas.", 403)
        name = p.name()
        effect = critical_effect(name)
        if effect and sig in (signal.SIGTERM, signal.SIGKILL, signal.SIGSTOP) and not confirm_critical:
            raise ActionError(f"{name} é um processo essencial do sistema: se ele parar, {effect}. "
                              "Confirme para continuar mesmo assim.", 409, {"critical": True, "effect": effect})
        if sig == signal.SIGTERM and close_windows is not None:
            n = close_windows(pid)
            if n:
                return {"ok": True, "pid": pid, "name": name, "signal": "TERM", "closed_windows": n}
            if n == 0:
                note = "O processo não tem janela; foi encerrado direto (SIGTERM)."
        p.send_signal(sig)
    except psutil.NoSuchProcess:
        raise ActionError("Processo não existe mais.", 404) from None
    except psutil.AccessDenied:
        raise ActionError("Permissão negada: o processo pertence a outro usuário. "
                          "Rode o servidor como root para gerenciá-lo.", 403) from None
    out = {"ok": True, "pid": pid, "name": name, "signal": sig_name.upper()}
    if note:
        out["note"] = note
    return out
