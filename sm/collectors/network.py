from __future__ import annotations

import socket
import time

import psutil

from . import Collector, register

SKIP = ("lo", "docker", "veth", "br-", "virbr")


@register
class Network(Collector):
    name = "network"
    order = 60

    def setup(self) -> None:
        self._prev = psutil.net_io_counters(pernic=True)
        self._t = time.monotonic()

    def collect(self) -> dict:
        now = time.monotonic()
        cur = psutil.net_io_counters(pernic=True)
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        dt = max(now - self._t, 1e-3)
        ifaces = []
        for name, c in cur.items():
            if name.startswith(SKIP):
                continue
            p = self._prev.get(name, c)
            st = stats.get(name)
            ipv4 = [a.address for a in addrs.get(name, []) if a.family == socket.AF_INET]
            ifaces.append({
                "name": name, "up": bool(st and st.isup), "speed": st.speed if st else 0,
                "ipv4": ipv4,
                "rx": max(0, (c.bytes_recv - p.bytes_recv) / dt),
                "tx": max(0, (c.bytes_sent - p.bytes_sent) / dt),
                "rx_total": c.bytes_recv, "tx_total": c.bytes_sent,
            })
        self._prev, self._t = cur, now
        rx = sum(i["rx"] for i in ifaces)
        tx = sum(i["tx"] for i in ifaces)
        self.record("network.rx", rx)
        self.record("network.tx", tx)
        return {"interfaces": ifaces, "rx": rx, "tx": tx}
