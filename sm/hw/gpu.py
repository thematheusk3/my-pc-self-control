"""Detecção e leitura de GPUs (AMD, NVIDIA, Intel), com tratamento de quirks.

NVIDIA: NVML (libnvidia-ml.so.1, instalada com o driver) via ctypes; sem ela, cai para o nvidia-smi.

Para adicionar outro fabricante, crie uma classe com `discover()` (classmethod
que retorna instâncias) e `sample() -> dict`, e registre em BACKENDS.
"""
from __future__ import annotations

import ctypes
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


# ---------- NVML (biblioteca do driver NVIDIA) ----------
class _NvUtil(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvMem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _NvPci(ctypes.Structure):
    _fields_ = [("busIdLegacy", ctypes.c_char * 16), ("domain", ctypes.c_uint), ("bus", ctypes.c_uint),
                ("device", ctypes.c_uint), ("pciDeviceId", ctypes.c_uint), ("pciSubSystemId", ctypes.c_uint),
                ("busId", ctypes.c_char * 32)]


class Nvml:
    """libnvidia-ml.so.1 via ctypes: o mesmo que o nvidia-smi lê, sem abrir um processo por segundo."""

    def __init__(self) -> None:
        self.lib = None
        for path in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
            try:
                self.lib = ctypes.CDLL(path)
                break
            except OSError:
                continue
        try:
            if self.lib is not None and self.lib.nvmlInit_v2():
                self.lib = None
        except AttributeError:
            self.lib = None

    def devices(self) -> list[tuple[ctypes.c_void_p, str, str]]:
        """[(handle, pci "0000:01:00.0", nome)]"""
        if not self.lib:
            return []
        n = ctypes.c_uint()
        if self.lib.nvmlDeviceGetCount_v2(ctypes.byref(n)):
            return []
        out = []
        for i in range(n.value):
            h = ctypes.c_void_p()
            if self.lib.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(i), ctypes.byref(h)):
                continue
            pci = _NvPci()
            if self.lib.nvmlDeviceGetPciInfo_v3(h, ctypes.byref(pci)):
                continue
            name = ctypes.create_string_buffer(96)
            self.lib.nvmlDeviceGetName(h, name, ctypes.c_uint(96))
            out.append((h, pci.busId.decode(errors="replace").lower()[4:], name.value.decode(errors="replace")))
        return out

    def _uint(self, fn: str, h, *args) -> int | None:
        v = ctypes.c_uint()
        try:
            return v.value if getattr(self.lib, fn)(h, *args, ctypes.byref(v)) == 0 else None
        except AttributeError:
            return None

    def _codec(self, fn: str, h) -> int | None:
        util, period = ctypes.c_uint(), ctypes.c_uint()
        try:
            return util.value if getattr(self.lib, fn)(h, ctypes.byref(util), ctypes.byref(period)) == 0 else None
        except AttributeError:
            return None

    def sample(self, h) -> dict:
        lib = self.lib
        u, m = _NvUtil(), _NvMem()
        busy = u.gpu if lib.nvmlDeviceGetUtilizationRates(h, ctypes.byref(u)) == 0 else None
        mem = {"used": m.used, "total": m.total} if lib.nvmlDeviceGetMemoryInfo(h, ctypes.byref(m)) == 0 else None
        temp = self._uint("nvmlDeviceGetTemperature", h, ctypes.c_int(0))      # NVML_TEMPERATURE_GPU
        mw = self._uint("nvmlDeviceGetPowerUsage", h)
        sclk = self._uint("nvmlDeviceGetClockInfo", h, ctypes.c_int(0))        # NVML_CLOCK_GRAPHICS
        mclk = self._uint("nvmlDeviceGetClockInfo", h, ctypes.c_int(2))        # NVML_CLOCK_MEM
        fan = self._uint("nvmlDeviceGetFanSpeed", h)
        engines = {k: float(v) for k, v in (("gfx", busy), ("enc", self._codec("nvmlDeviceGetEncoderUtilization", h)),
                                             ("dec", self._codec("nvmlDeviceGetDecoderUtilization", h)))
                   if v is not None}
        return {"busy": busy, "temp": temp, "power": round(mw / 1000, 1) if mw is not None else None,
                "clocks": {k: v for k, v in (("sclk", sclk), ("mclk", mclk)) if v is not None},
                "fan_pct": fan, "vram": mem, "engines": engines}


_NVML: Nvml | None = None


def nvml() -> Nvml:
    global _NVML
    if _NVML is None:
        _NVML = Nvml()
    return _NVML


class NvidiaGpu:
    vendor = "NVIDIA"
    FIELDS = "index,pci.bus_id,name,utilization.gpu,temperature.gpu,power.draw,clocks.gr,clocks.mem,memory.used,memory.total,fan.speed"

    def __init__(self, index: int | None, pci: str, name: str, handle=None) -> None:
        self.index, self.pci, self.name, self.handle = index, pci, name, handle

    @classmethod
    def discover(cls) -> list["NvidiaGpu"]:
        devs = nvml().devices()
        if devs:
            return [cls(None, pci, name, handle=h) for h, pci, name in devs]
        if not shutil.which("nvidia-smi"):       # plano B: sem a biblioteca do driver
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

    def _base(self) -> dict:
        return {"id": self.pci, "vendor": self.vendor, "name": self.name, "driver": "nvidia",
                "integrated": False, "temp_source": "gpu", "fan": None, "gtt": None, "notes": []}

    def sample(self) -> dict:
        if self.handle is not None:
            nv = nvml().sample(self.handle)
            t = nv["temp"]
            return {**self._base(), "busy": nv["busy"], "busy_source": "nvml", "temp": t,
                    "temps": {"gpu": t} if t is not None else {}, "power": nv["power"], "clocks": nv["clocks"],
                    "fan_pct": nv["fan_pct"], "vram": nv["vram"], "engines": nv["engines"]}
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
            **self._base(), "busy": g(3), "temp": g(4),
            "temps": {"gpu": g(4)} if g(4) is not None else {}, "power": g(5),
            "clocks": {k: v for k, v in (("sclk", g(6)), ("mclk", g(7))) if v is not None},
            "fan_pct": g(10),
            "vram": {"used": used * mib, "total": total * mib} if used is not None and total else None,
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
