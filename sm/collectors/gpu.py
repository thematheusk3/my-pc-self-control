from __future__ import annotations

from . import Collector, register
from ..hw import gpu as gpu_hw


@register
class Gpu(Collector):
    name = "gpu"
    order = 30

    def setup(self) -> None:
        self.gpus = gpu_hw.discover_all()

    def info(self) -> dict:
        return {"gpus": [{"id": g.pci, "name": g.name, "vendor": g.vendor} for g in self.gpus]}

    def collect(self) -> dict:
        out = []
        fd_busy = self.ctx.drm.per_device_busy
        for i, g in enumerate(self.gpus):
            try:
                s = g.sample()
            except Exception as e:  # noqa: BLE001
                s = {"id": g.pci, "name": g.name, "vendor": g.vendor, "error": str(e)}
            if s.get("busy") is None and s.get("id") in fd_busy:
                s["busy"] = round(fd_busy[s["id"]], 1)
                s["busy_source"] = "fdinfo"
            self.record(f"gpu.{i}.busy", s.get("busy"))
            self.record(f"gpu.{i}.temp", s.get("temp"))
            out.append(s)
        return {"gpus": out}
