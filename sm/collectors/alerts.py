from __future__ import annotations

from . import Collector, register


@register
class AlertsBadge(Collector):
    """Só o contador de alertas não lidos (para o sino atualizar ao vivo)."""
    name = "alerts"
    order = 99

    def setup(self) -> None:
        if not self.ctx.alerts:
            raise RuntimeError("alertas inativos")

    def collect(self) -> dict:
        h = self.ctx.alerts.history
        latest = h[0] if h else None
        return {"unread": self.ctx.alerts.unread(), "latest": latest}
