from __future__ import annotations

import psutil

from . import Collector, register
from ..focus import FocusMonitor


@register
class Focus(Collector):
    """Janela em foco no PC (KWin via script + journal; X11 via xprop)."""
    name = "focus"
    interval = 2

    def setup(self) -> None:
        self.mon = FocusMonitor()
        self.mon.start()
        self.ctx.focus = self.mon       # server.py tira o script do KWin ao encerrar

    def collect(self) -> dict:
        self.mon.tick()
        st = self.mon.status()
        w = st["window"]
        if w and w.get("pid"):
            try:
                p = psutil.Process(int(w["pid"]))
                w["process"] = p.name()
            except (psutil.Error, ValueError):
                w["process"] = None
        return st
