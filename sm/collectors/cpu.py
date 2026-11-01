from __future__ import annotations

import os

import psutil

from . import Collector, register
from .system import cpu_model
from ..hw import hwmon
from ..hw.util import read, read_int

# (driver hwmon, labels preferidos em ordem)
CPU_TEMP_SOURCES = [
    ("k10temp", ("Tctl", "Tdie", "Tccd1")),
    ("zenpower", ("Tdie", "Tctl")),
    ("coretemp", ("Package id 0", "Physical id 0")),
    ("cpu_thermal", ()),   # ARM / Raspberry Pi
    ("acpitz", ()),
]


def cpu_temp() -> tuple[float | None, str | None]:
    readings = hwmon.all_readings()
    for driver, labels in CPU_TEMP_SOURCES:
        temps = [r for r in readings if r.chip == driver and r.kind == "temp"]
        if not temps:
            continue
        for lab in labels:
            for r in temps:
                if r.label == lab:
                    return r.value, f"{driver}/{lab}"
        return temps[0].value, f"{driver}/{temps[0].label}"
    # fallback: thermal zones
    for i in range(20):
        z = f"/sys/class/thermal/thermal_zone{i}"
        t = read(f"{z}/type")
        if t is None:
            break
        if t in ("x86_pkg_temp", "cpu-thermal", "soc_thermal"):
            v = read_int(f"{z}/temp")
            if v and v > 1000:
                return v / 1000, f"thermal/{t}"
    return None, None


@register
class Cpu(Collector):
    name = "cpu"
    order = 10

    def setup(self) -> None:
        psutil.cpu_percent(percpu=True)
        self.model = cpu_model()
        self.physical = psutil.cpu_count(logical=False)
        self.logical = psutil.cpu_count()

    def info(self) -> dict:
        return {"model": self.model, "cores": self.physical, "threads": self.logical}

    def collect(self) -> dict:
        per = psutil.cpu_percent(percpu=True)
        total = sum(per) / len(per) if per else 0.0
        try:
            freqs = psutil.cpu_freq(percpu=True) or []
        except Exception:
            freqs = []
        cur = [round(f.current) for f in freqs]
        fmax = max((f.max for f in freqs), default=0) or None
        temp, src = cpu_temp()
        times = psutil.cpu_times_percent(interval=None)
        self.record("cpu.total", round(total, 1))
        self.record("cpu.temp", temp)
        if cur:
            self.record("cpu.freq", sum(cur) / len(cur))
        return {
            "model": self.model,
            "cores": self.physical, "threads": self.logical,
            "total": round(total, 1),
            "per_core": per,
            "freq": {"avg": round(sum(cur) / len(cur)) if cur else None, "max": fmax,
                     "per_core": cur},
            "temp": temp, "temp_source": src,
            "load": os.getloadavg(),
            "breakdown": {"user": times.user, "system": times.system,
                          "iowait": getattr(times, "iowait", 0)},
        }
