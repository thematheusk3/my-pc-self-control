"""Uso de GPU por processo via /proc/<pid>/fdinfo (padrão DRM do kernel).

Funciona com amdgpu, i915, nouveau, panfrost, msm... Sem root só enxerga
processos do mesmo usuário que rodar o servidor.
"""
from __future__ import annotations

import os
import time

_ENGINE = "drm-engine-"


def _scan() -> dict[int, dict[tuple[str, str], dict]]:
    """pid -> {(pdev, client_id): {"engines": {nome: ns}, "mem": bytes}}"""
    result: dict[int, dict] = {}
    for pid_s in os.listdir("/proc"):
        if not pid_s.isdigit():
            continue
        fd_dir = f"/proc/{pid_s}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        clients: dict[tuple[str, str], dict] = {}
        for fd in fds:
            try:
                if not os.readlink(f"{fd_dir}/{fd}").startswith("/dev/dri/"):
                    continue
                with open(f"/proc/{pid_s}/fdinfo/{fd}") as f:
                    text = f.read()
            except OSError:
                continue
            info = {}
            for line in text.splitlines():
                k, _, v = line.partition(":")
                info[k] = v.strip()
            cid = info.get("drm-client-id")
            if cid is None:
                continue
            key = (info.get("drm-pdev", ""), cid)
            if key in clients:
                continue
            engines = {}
            mem = 0
            for k, v in info.items():
                if k.startswith(_ENGINE) and not k.startswith("drm-engine-capacity"):
                    try:
                        engines[k[len(_ENGINE):]] = int(v.split()[0])
                    except (ValueError, IndexError):
                        pass
                elif k.startswith("drm-resident-"):
                    parts = v.split()
                    try:
                        n = int(parts[0])
                        unit = parts[1] if len(parts) > 1 else ""
                        mem += n * {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}.get(unit, 1)
                    except (ValueError, IndexError):
                        pass
            clients[key] = {"engines": engines, "mem": mem, "pdev": key[0]}
        if clients:
            result[int(pid_s)] = clients
    return result


class DrmClientTracker:
    def __init__(self) -> None:
        self._prev: dict[int, dict] = {}
        self._prev_t = 0.0
        self.per_pid: dict[int, dict] = {}      # pid -> {"gpu": %, "gpu_mem": bytes}
        self.per_device_busy: dict[str, float] = {}
        self.per_device_engines: dict[str, dict[str, float]] = {}   # pdev -> {engine (gfx, compute, dec...): %}

    def update(self) -> None:
        now = time.monotonic()
        cur = _scan()
        elapsed = (now - self._prev_t) * 1e9 if self._prev_t else 0
        per_pid: dict[int, dict] = {}
        dev_engine_totals: dict[str, dict[str, float]] = {}
        for pid, clients in cur.items():
            prev_clients = self._prev.get(pid, {})
            best = 0.0
            mem = 0
            for key, c in clients.items():
                mem += c["mem"]
                p = prev_clients.get(key)
                if not p or elapsed <= 0:
                    continue
                for eng, ns in c["engines"].items():
                    d = ns - p["engines"].get(eng, ns)
                    if d <= 0:
                        continue
                    pct = min(100.0, d / elapsed * 100)
                    best = max(best, pct)
                    tot = dev_engine_totals.setdefault(c["pdev"], {})
                    tot[eng] = tot.get(eng, 0.0) + pct
            per_pid[pid] = {"gpu": round(best, 1), "gpu_mem": mem}
        self.per_pid = per_pid
        self.per_device_busy = {dev: min(100.0, max(e.values())) for dev, e in dev_engine_totals.items() if e}
        self.per_device_engines = {dev: {k: round(min(100.0, v), 1) for k, v in e.items()}
                                   for dev, e in dev_engine_totals.items() if e}
        self._prev, self._prev_t = cur, now
