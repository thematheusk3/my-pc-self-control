from __future__ import annotations

import time

import psutil

from . import Collector, register

SKIP_FS = {"squashfs", "tmpfs", "devtmpfs", "overlay", "iso9660", "efivarfs"}


@register
class Disks(Collector):
    name = "disks"
    order = 50

    def setup(self) -> None:
        self._prev = psutil.disk_io_counters(perdisk=True) or {}
        self._t = time.monotonic()

    def collect(self) -> dict:
        parts, seen = [], set()
        for p in psutil.disk_partitions(all=False):
            if p.fstype in SKIP_FS or p.device in seen or p.device.startswith("/dev/loop"):
                continue
            seen.add(p.device)
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            parts.append({"device": p.device, "mount": p.mountpoint, "fs": p.fstype,
                          "total": u.total, "used": u.used, "percent": u.percent})
        now = time.monotonic()
        cur = psutil.disk_io_counters(perdisk=True) or {}
        dt = max(now - self._t, 1e-3)
        io = []
        for name, c in cur.items():
            p = self._prev.get(name)
            # só discos inteiros (sda, nvme0n1, mmcblk0), não partições
            if not p or name.startswith(("loop", "ram", "zram", "dm-")):
                continue
            io.append({"disk": name, "read": (c.read_bytes - p.read_bytes) / dt,
                       "write": (c.write_bytes - p.write_bytes) / dt})
        self._prev, self._t = cur, now
        disks = {d["disk"] for d in io}
        io = [d for d in io if not any(d["disk"] != o and d["disk"].startswith(o) for o in disks)]
        tr = sum(d["read"] for d in io)
        tw = sum(d["write"] for d in io)
        self.record("disks.read", tr)
        self.record("disks.write", tw)
        return {"partitions": parts, "io": io, "read": tr, "write": tw}
