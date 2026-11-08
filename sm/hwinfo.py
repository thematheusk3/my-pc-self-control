"""Inventário de hardware e software base (placa, BIOS, CPU, GPU, memória, discos, kernel, drivers).

Gravado no banco quando muda (store.hardware): o histórico mostra quando uma peça, o BIOS, o kernel
ou o Mesa mudou, o que ajuda a explicar incidentes ("começou depois da atualização do kernel").
Coleta barata (arquivos do /sys e /proc, lsblk, pacman -Q); roda na partida e a cada 6 h.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)
INTERVAL = 6 * 3600
PACKAGES = ("linux", "linux-cachyos", "linux-firmware", "mesa", "lib32-mesa", "vulkan-radeon", "nvidia", "nvidia-utils",
            "nvidia-open", "systemd", "pipewire", "kwin", "plasma-workspace", "mangohud", "gamescope", "proton-cachyos",
            "wine", "steam")


def _read(p: str) -> str | None:
    try:
        return Path(p).read_text(errors="replace").strip() or None
    except OSError:
        return None


def _run(cmd: list[str], timeout: float = 8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def board() -> dict:
    d = "/sys/class/dmi/id/"
    return {k: _read(d + f) for k, f in (("vendor", "board_vendor"), ("model", "board_name"), ("bios_vendor", "bios_vendor"),
                                          ("bios_version", "bios_version"), ("bios_date", "bios_date"),
                                          ("product", "product_name"))}


def cpu() -> dict:
    model, cores, threads = None, set(), 0
    for line in (_read("/proc/cpuinfo") or "").splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k == "model name" and not model:
            model = v
        elif k == "processor":
            threads += 1
        elif k == "core id":
            cores.add(v)
    micro = None
    m = re.search(r"^microcode\s*:\s*(\S+)", _read("/proc/cpuinfo") or "", re.M)
    if m:
        micro = m.group(1)
    return {"model": model, "cores": len(cores) or None, "threads": threads or None, "microcode": micro,
            "governor": _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")}


def memory() -> dict:
    m = re.search(r"MemTotal:\s+(\d+)", _read("/proc/meminfo") or "")
    s = re.search(r"SwapTotal:\s+(\d+)", _read("/proc/meminfo") or "")
    return {"total": int(m.group(1)) * 1024 if m else None, "swap": int(s.group(1)) * 1024 if s else None}


def gpus() -> list[dict]:
    out = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        dev = card / "device"
        try:
            driver = os.path.basename(os.readlink(dev / "driver"))
        except OSError:
            driver = None
        pci = os.path.basename(os.path.realpath(dev))
        item = {"card": card.name, "pci": pci, "driver": driver,
                "vendor_id": _read(str(dev / "vendor")), "device_id": _read(str(dev / "device")),
                "vbios": _read(str(dev / "vbios_version")),
                "vram": int(_read(str(dev / "mem_info_vram_total")) or 0) or None}
        out.append(item)
    try:
        from .hw import gpu as gpu_hw
        names = {g.pci: g.name for g in gpu_hw.discover_all()}
        for g in out:
            g["name"] = names.get(g["pci"])
    except Exception:  # noqa: BLE001
        pass
    return out


def disks() -> list[dict]:
    try:
        data = json.loads(_run(["lsblk", "-J", "-d", "-b", "-o", "NAME,MODEL,SERIAL,SIZE,ROTA,TRAN,TYPE"]) or "{}")
    except ValueError:
        return []
    out = []
    for d in data.get("blockdevices", []):
        if d.get("type") != "disk" or str(d.get("name", "")).startswith(("zram", "loop")):
            continue
        serial = d.get("serial")
        out.append({"name": d.get("name"), "model": (d.get("model") or "").strip() or None, "size": d.get("size"),
                    "rotational": bool(d.get("rota")), "transport": d.get("tran"),
                    # só o fim do número de série: identifica a troca de disco sem expor o número inteiro
                    "serial_tail": serial[-4:] if serial else None})
    return out


def packages() -> dict:
    out = _run(["pacman", "-Q", *PACKAGES])
    return {k: v for k, v in (line.split(None, 1) for line in out.splitlines() if " " in line)}


def collect() -> dict:
    os_name = None
    for line in (_read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
    return {"board": board(), "cpu": cpu(), "memory": memory(), "gpus": gpus(), "disks": disks(),
            "kernel": platform.release(), "os": os_name, "packages": packages()}


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def diff(old: dict | None, new: dict, prefix: str = "") -> list[dict]:
    """Lista de mudanças [{path, old, new}] entre dois inventários."""
    if old is None:
        return []
    out = []
    keys = set(old) | set(new) if isinstance(old, dict) and isinstance(new, dict) else set()
    if not keys:
        if old != new:
            out.append({"path": prefix or "/", "old": old, "new": new})
        return out
    for k in sorted(keys, key=str):
        a, b = old.get(k), new.get(k)
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(a, dict) and isinstance(b, dict):
            out += diff(a, b, path)
        elif a != b:
            out.append({"path": path, "old": a, "new": b})
    return out


class HardwareInventory:
    def __init__(self, store) -> None:
        self.store = store
        self._stop = threading.Event()

    def check(self) -> dict | None:
        """Coleta; grava se mudou. Devolve a linha nova (ou None se igual)."""
        data = collect()
        h = fingerprint(data)
        last = self.store.hardware_latest()
        if last and last["hash"] == h:
            return None
        changes = diff(last["data"] if last else None, data)
        self.store.add_hardware(h, data, changes)
        if changes:
            log.info("hardware/software base mudou: %s", ", ".join(c["path"] for c in changes[:8]))
        return {"hash": h, "changes": changes}

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="hwinfo").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(20)      # deixa a partida terminar
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:  # noqa: BLE001
                log.exception("inventário de hardware falhou")
            self._stop.wait(INTERVAL)


def recent_upgrades(days: float = 7, now: float | None = None) -> list[dict]:
    """Pacotes atualizados nos últimos dias (/var/log/pacman.log): kernel, Mesa, firmware, Proton..."""
    now = now or time.time()
    cut = now - days * 86400
    out = []
    try:
        with open("/var/log/pacman.log", errors="replace") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 2_000_000))
            lines = fh.read().splitlines()
    except OSError:
        return []
    rx = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{4})?\] \[ALPM\] (upgraded|installed|downgraded|removed) "
                    r"(\S+) \((.+)\)$")
    import datetime as dt
    for line in lines:
        m = rx.match(line)
        if not m:
            continue
        try:
            t = dt.datetime.strptime(m.group(1) + (m.group(2) or "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except ValueError:
            continue
        if t >= cut:
            out.append({"ts": t, "action": m.group(3), "package": m.group(4), "version": m.group(5)})
    return out
