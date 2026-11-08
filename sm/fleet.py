"""Identidade da máquina e formato de envio para o futuro servidor central (várias máquinas).

Hoje: cada instalação tem um `machine` estável (derivado do /etc/machine-id, sem expor o valor original)
e o painel monta "pacotes" com o que mudou no banco (store.changes). O envio ainda não existe: a
configuração [fleet] fica reservada e o desenho está em FROTA.md.
"""
from __future__ import annotations

import hashlib
import platform
import socket
import time

from . import __version__

SCHEMA = 1          # versão do formato do pacote; o servidor recusa o que não conhece
_ID: str | None = None


def machine_id() -> str:
    """16 hex estáveis por instalação do sistema. Hash com prefixo: não dá para voltar ao machine-id."""
    global _ID
    if _ID is None:
        raw = ""
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                with open(p) as fh:
                    raw = fh.read().strip()
                if raw:
                    break
            except OSError:
                continue
        if not raw:
            raw = f"{socket.gethostname()}-{platform.machine()}"
        _ID = hashlib.sha256(f"self-monitoring:{raw}".encode()).hexdigest()[:16]
    return _ID


def identity() -> dict:
    return {"machine": machine_id(), "hostname": socket.gethostname(), "os": "linux",
            "kernel": platform.release(), "arch": platform.machine(), "version": __version__}


def package(store, since: int, limit: int = 1000) -> dict:
    """Pacote no formato que um agente mandaria ao servidor central (POST /api/fleet/ingest, futuro)."""
    return {"schema": SCHEMA, "sent": time.time(), "identity": identity(), **store.changes(since, limit)}
