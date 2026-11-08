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
        fd_engines = self.ctx.drm.per_device_engines
        for i, g in enumerate(self.gpus):
            try:
                s = g.sample()
            except Exception as e:  # noqa: BLE001
                s = {"id": g.pci, "name": g.name, "vendor": g.vendor, "error": str(e)}
            if s.get("busy") is None and s.get("id") in fd_busy:
                s["busy"] = round(fd_busy[s["id"]], 1)
                s["busy_source"] = "fdinfo"
            # uso por tipo de engine (3D, compute, vídeo...), somando os processos visíveis
            s["engines"] = {**fd_engines.get(s.get("id"), {}), **(s.get("engines") or {})}   # NVML tem prioridade
            self.record(f"gpu.{i}.busy", s.get("busy"))
            self.record(f"gpu.{i}.temp", s.get("temp"))
            for name in ("vram", "gtt"):          # % da memória da GPU (vazamento/estouro aparece nos incidentes)
                m = s.get(name) or {}
                if m.get("total"):
                    self.record(f"gpu.{i}.{name}", 100 * m["used"] / m["total"])
            if isinstance(s.get("power"), (int, float)):
                self.record(f"gpu.{i}.power", s["power"])
            clk = (s.get("clocks") or {}).get("sclk") or (s.get("clocks") or {}).get("core") or s.get("freq")
            if isinstance(clk, (int, float)):
                self.record(f"gpu.{i}.clock", clk)
            out.append(s)
        return {"gpus": out}
