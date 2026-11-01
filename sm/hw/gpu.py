"""Detecção e leitura de GPUs (AMD, NVIDIA, Intel), com tratamento de quirks.

Para adicionar outro fabricante, crie uma classe com `discover()` (classmethod
que retorna instâncias) e `sample() -> dict`, e registre em BACKENDS.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from . import hwmon
from .util import read, read_int

DRM = Path("/sys/class/drm")

# Nomes amigáveis para placas sem nome bom no lspci / sem lspci
KNOWN_DEVICES = {
    ("0x1002", "0x13fe"): "AMD BC-250 (Cyan Skillfish)",
}


def _lspci_name(pci: str) -> str | None:
    if not shutil.which("lspci"):
        return None
    try:
        out = subprocess.run(["lspci", "-s", pci], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if ": " in out:
        name = out.split(": ", 1)[1].strip()
        return name.replace("Advanced Micro Devices, Inc. ", "").replace("[AMD/ATI] ", "")
    return None


def _cards() -> list[Path]:
    out = []
    try:
        for p in sorted(DRM.iterdir()):
            if p.name.startswith("card") and p.name[4:].isdigit():
                out.append(p)
    except OSError:
        pass
    return out


def _driver(card: Path) -> str:
    try:
        return (card / "device" / "driver").resolve().name
    except OSError:
        return ""


def _cpu_die_temp() -> float | None:
    """Tctl/Tdie da CPU AMD — usado em APUs onde a GPU não reporta temperatura."""
    for chip in hwmon.chips():
        if read(chip / "name") in ("k10temp", "zenpower"):
            for r in hwmon.read_chip(chip):
                if r.kind == "temp" and r.label in ("Tctl", "Tdie"):
                    return r.value
    return None


class AmdGpu:
    vendor = "AMD"

    def __init__(self, card: Path) -> None:
        self.card = card
        self.dev = card / "device"
        self.pci = self.dev.resolve().name
        ids = (read(self.dev / "vendor", ""), read(self.dev / "device", ""))
        self.device_id = ids[1]
        self.name = KNOWN_DEVICES.get(ids) or _lspci_name(self.pci) or f"AMD GPU {ids[1]}"
        self.vram_total = read_int(self.dev / "mem_info_vram_total", 0) or 0
        self.gtt_total = read_int(self.dev / "mem_info_gtt_total", 0) or 0
        # APU / memória unificada: pouca VRAM dedicada (carve-out)
        self.integrated = self.vram_total <= 2 * 1024**3
        self.is_bc250 = ids == ("0x1002", "0x13fe")
        self.hwmon_dir = next(iter(sorted((self.dev / "hwmon").glob("hwmon*"))), None)
        self.notes: list[str] = []
        self.sclk_max = self._od_sclk_max()
        # leituras corrompidas da BC-250 (clock/potência) são descartadas em hw/quirks.py
        if self.is_bc250:
            self.notes.append("BC-250: CPU e GPU no mesmo die, memória GDDR6 unificada. "
                              "Temperatura vem do sensor Tctl da CPU; clock e potência "
                              "reportados pelo driver não são confiáveis e foram ocultados.")

    def _od_sclk_max(self) -> float | None:
        txt = read(self.dev / "pp_od_clk_voltage") or ""
        m = re.search(r"SCLK:\s*\d+\s*MHz\s+(\d+)\s*MHz", txt, re.I)
        return float(m.group(1)) if m else None

    def _busy(self) -> float | None:
        """gpu_busy_percent é instantâneo e oscila muito; faz média de algumas leituras."""
        vals = []
        for i in range(5):
            v = read_int(self.dev / "gpu_busy_percent")
            if v is not None:
                vals.append(v)
            if i < 4:
                time.sleep(0.015)
        return round(sum(vals) / len(vals), 1) if vals else None

    @classmethod
    def discover(cls) -> list["AmdGpu"]:
        return [cls(c) for c in _cards() if _driver(c) == "amdgpu"]

    def sample(self) -> dict:
        temps, power, clocks, fan = {}, None, {}, None
        if self.hwmon_dir:
            for r in hwmon.read_chip(self.hwmon_dir):
                if r.kind == "temp":
                    temps[r.label] = r.value
                elif r.kind == "power" and power is None:
                    power = r.value
                elif r.kind == "freq":
                    if r.label == "sclk" and self.sclk_max and r.value > self.sclk_max * 1.05:
                        continue
                    clocks[r.label] = round(r.value)
                elif r.kind == "fan":
                    fan = r.value
        temp = temps.get("junction") or temps.get("edge") or next(iter(temps.values()), None)
        temp_source = "gpu"
        if temp is None and self.integrated:
            temp = _cpu_die_temp()
            temp_source = "cpu_die" if temp is not None else None
        busy = self._busy()
        return {
            "id": self.pci, "vendor": self.vendor, "name": self.name, "driver": "amdgpu",
            "integrated": self.integrated, "busy": busy,
            "temp": temp, "temp_source": temp_source, "temps": temps,
            "power": power, "clocks": clocks, "fan": fan,
            "vram": {"used": read_int(self.dev / "mem_info_vram_used"), "total": self.vram_total},
            "gtt": {"used": read_int(self.dev / "mem_info_gtt_used"), "total": self.gtt_total},
            "notes": self.notes,
        }


class IntelGpu:
    vendor = "Intel"

    def __init__(self, card: Path, driver: str) -> None:
        self.card = card
        self.driver = driver
        self.pci = (card / "device").resolve().name
        self.name = _lspci_name(self.pci) or "Intel GPU"

    @classmethod
    def discover(cls) -> list["IntelGpu"]:
        return [cls(c, d) for c in _cards() if (d := _driver(c)) in ("i915", "xe")]

    def sample(self) -> dict:
        freq = read_int(self.card / "gt_act_freq_mhz") or read_int(self.card / "gt" / "gt0" / "rps_act_freq_mhz")
        return {
            "id": self.pci, "vendor": self.vendor, "name": self.name, "driver": self.driver,
            "integrated": True, "busy": None,  # preenchido via fdinfo pelo coletor
            "temp": None, "temp_source": None, "temps": {}, "power": None,
            "clocks": {"gt": freq} if freq else {}, "fan": None,
            "vram": None, "gtt": None, "notes": [],
        }


class NvidiaGpu:
    vendor = "NVIDIA"
    FIELDS = "index,pci.bus_id,name,utilization.gpu,temperature.gpu,power.draw,clocks.gr,clocks.mem,memory.used,memory.total,fan.speed"

    def __init__(self, index: int, pci: str, name: str) -> None:
        self.index, self.pci, self.name = index, pci, name

    @classmethod
    def discover(cls) -> list["NvidiaGpu"]:
        if not shutil.which("nvidia-smi"):
            return []
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=index,pci.bus_id,name", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        gpus = []
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append(cls(int(parts[0]), parts[1].lower()[4:], parts[2]))
        return gpus

    @staticmethod
    def _num(v: str) -> float | None:
        try:
            return float(v)
        except ValueError:
            return None

    def sample(self) -> dict:
        vals: list[str] = []
        try:
            out = subprocess.run(["nvidia-smi", f"--id={self.index}", f"--query-gpu={self.FIELDS}",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout
            vals = [v.strip() for v in out.strip().split(",")]
        except (OSError, subprocess.SubprocessError):
            pass
        g = lambda i: self._num(vals[i]) if len(vals) > i else None  # noqa: E731
        mib = 1024**2
        used, total = g(8), g(9)
        return {
            "id": self.pci, "vendor": self.vendor, "name": self.name, "driver": "nvidia",
            "integrated": False, "busy": g(3), "temp": g(4), "temp_source": "gpu",
            "temps": {"gpu": g(4)} if g(4) is not None else {}, "power": g(5),
            "clocks": {k: v for k, v in (("sclk", g(6)), ("mclk", g(7))) if v is not None},
            "fan": None, "fan_pct": g(10),
            "vram": {"used": used * mib, "total": total * mib} if used is not None and total else None,
            "gtt": None, "notes": [],
        }


BACKENDS = [AmdGpu, NvidiaGpu, IntelGpu]


def discover_all() -> list:
    gpus = []
    for b in BACKENDS:
        try:
            gpus.extend(b.discover())
        except Exception:  # um backend com problema não derruba os outros
            pass
    return gpus
