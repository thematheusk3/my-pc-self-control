from __future__ import annotations

from . import Collector, register


@register
class Fan(Collector):
    """Expõe o estado do controlador de ventoinha no snapshot (o controle roda em thread própria)."""
    name = "fan"
    order = 45

    def setup(self) -> None:
        if not (self.ctx.fan and self.ctx.fan.available):
            raise RuntimeError("controle de ventoinha inativo")

    def collect(self) -> dict:
        s = self.ctx.fan.status()
        self.record("fan.rpm", s["rpm"])
        self.record("fan.duty", s["duty_pct"])
        self.record("fan.temp", s["temp"])
        return s
