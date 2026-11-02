from __future__ import annotations

from . import Collector, register
from ..steamdl import SteamDownloads

STEAM_PROCS = {"steam", "steamwebhelper"}


@register
class Steam(Collector):
    """Downloads e atualizações da Steam em andamento (progresso, velocidade, previsão)."""
    name = "steam"
    interval = 2

    def setup(self) -> None:
        self.dl = SteamDownloads()

    def _steam_rate(self) -> float | None:
        nm = self.ctx.netmon
        if not nm:
            return None
        with nm._lock:
            conns = list(nm.conns.values())
        rate = sum(c.get("rate_down") or 0 for c in conns
                   if c.get("procs") and c["procs"][0].get("name") == "steam")
        return rate or None

    def collect(self) -> dict:
        running = any(p.get("name") in STEAM_PROCS for p in self.ctx.processes or [])
        return self.dl.collect(running, self._steam_rate)
