from __future__ import annotations

import psutil

from . import Collector, register


@register
class Memory(Collector):
    name = "memory"
    order = 20

    def collect(self) -> dict:
        v = psutil.virtual_memory()
        s = psutil.swap_memory()
        self.record("memory.percent", v.percent)
        return {
            "total": v.total, "used": v.total - v.available, "available": v.available,
            "percent": v.percent, "cached": getattr(v, "cached", 0), "buffers": getattr(v, "buffers", 0),
            "swap": {"total": s.total, "used": s.used, "percent": s.percent},
        }
