from __future__ import annotations

from . import Collector, register


@register
class Game(Collector):
    """Jogo atual e FPS (MangoHud). Desativado: só verifica se há jogo da Steam aberto."""
    name = "game"
    order = 95      # depois de "processes", que preenche ctx.processes

    def setup(self) -> None:
        if not self.ctx.game:
            raise RuntimeError("monitor de jogo inativo")

    def collect(self) -> dict:
        self.ctx.game.tick()
        s = self.ctx.game.status()
        self.record("game.fps", s["fps"])
        return s
