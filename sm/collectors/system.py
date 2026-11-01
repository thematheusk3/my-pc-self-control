from __future__ import annotations

import platform
import socket
import time

import psutil

from . import Collector, register
from ..hw.util import read


def _os_name() -> str:
    try:
        for line in open("/etc/os-release"):
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def cpu_model() -> str:
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "CPU"


def _battery() -> dict | None:
    """Bateria do sistema (notebook). Ignora baterias de periféricos (mouse, controle)."""
    from pathlib import Path
    for ps in sorted(Path("/sys/class/power_supply").glob("*")):
        if read(ps / "type") != "Battery" or read(ps / "scope") == "Device":
            continue
        cap = read(ps / "capacity")
        if cap and cap.isdigit():
            return {"percent": int(cap), "status": read(ps / "status") or ""}
    return None


@register
class System(Collector):
    name = "system"
    interval = 10
    order = 0

    def setup(self) -> None:
        model = cpu_model()
        board = read("/sys/class/dmi/id/board_name") or ""
        self.static = {
            "hostname": socket.gethostname(),
            "os": _os_name(),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "cpu_model": model,
            "board": board,
            "board_vendor": read("/sys/class/dmi/id/board_vendor") or "",
            "boot_time": psutil.boot_time(),
            "is_bc250": "BC-250" in model or "BC250" in board.upper(),
        }

    def info(self) -> dict:
        return self.static

    def collect(self) -> dict:
        d = dict(self.static)
        d["uptime"] = time.time() - self.static["boot_time"]
        d["users"] = len(psutil.users())
        d["battery"] = _battery()
        return d
