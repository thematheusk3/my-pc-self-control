"""Registro de coletores.

Para criar um coletor novo, adicione um arquivo neste pacote:

    from . import Collector, register

    @register
    class MeuColetor(Collector):
        name = "meu"            # chave no snapshot
        interval = None         # None = intervalo padrão; ou segundos
        def collect(self) -> dict:
            self.record("meu.valor", 42)   # opcional: entra no histórico/gráfico
            return {"valor": 42}

Ele é importado automaticamente e aparece em snapshot["data"]["meu"].
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..sampler import Context

log = logging.getLogger(__name__)
REGISTRY: list[type["Collector"]] = []


class Collector:
    name: str = ""
    interval: float | None = None
    order: int = 100   # ordem de execução dentro do mesmo intervalo

    def __init__(self, ctx: "Context") -> None:
        self.ctx = ctx

    def setup(self) -> None:
        """Chamado uma vez antes da primeira coleta."""

    def collect(self) -> dict:
        raise NotImplementedError

    def info(self) -> dict | None:
        """Informações estáticas (expostas em /api/info)."""
        return None

    def record(self, key: str, value: float | None) -> None:
        self.ctx.history.add(key, value)


def register(cls: type[Collector]) -> type[Collector]:
    REGISTRY.append(cls)
    return cls


def load_all() -> list[type[Collector]]:
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{mod.name}")
        except Exception:
            log.exception("falha ao carregar coletor %s", mod.name)
    return REGISTRY
