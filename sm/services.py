"""Serviços do systemd (sistema e do usuário): estado, consumo e ações.

Consumo vem do cgroup de cada unidade (`systemctl show`: MemoryCurrent, CPUUsageNSec, TasksCurrent);
o % de CPU é a diferença entre duas leituras. Ações (iniciar/parar/reiniciar) em serviços do usuário
funcionam direto; nos do sistema dependem do polkit (sem senha interativa, costuma ser negado — o
painel mostra o erro).
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time

from .desktop import user_env

RX_UNIT = re.compile(r"[\w@:.\\-]{1,200}\.(service|socket|timer|mount|target|path|scope|slice|swap)")
PROPS = ["Id", "Description", "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID", "MemoryCurrent",
         "CPUUsageNSec", "TasksCurrent", "NRestarts", "Result", "ActiveEnterTimestamp", "InactiveEnterTimestamp",
         "ExecMainStatus", "ExecMainCode", "Type", "FragmentPath", "Restart", "IOReadBytes", "IOWriteBytes"]
DETAIL_PROPS = PROPS + ["Requires", "Wants", "After", "WantedBy", "TriggeredBy", "User", "ExecStart", "StateChangeTimestamp",
                        "MemoryPeak", "MemoryMax", "CPUQuotaPerSecUSec", "WatchdogUSec", "TimeoutStartUSec", "Documentation",
                        "ActiveExitTimestamp", "StatusText", "ControlGroup"]
ACTIONS = {"start", "stop", "restart", "reload"}
SELF_UNIT = "self-monitoring.service"
_lock = threading.Lock()
_prev: dict[tuple[bool, str], tuple[float, int]] = {}   # (user, unit) -> (t, cpu_ns)


def _systemctl(args: list[str], user: bool, timeout: float = 15) -> subprocess.CompletedProcess:
    cmd = ["systemctl", "--user" if user else "--system", "--no-pager", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=user_env() if user else None)


def _int(v) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return None if n >= 2 ** 63 - 1 or n < 0 else n     # "[not set]" = UINT64_MAX


def _ts(v: str | None) -> float | None:
    if not v:
        return None
    m = re.match(r"@(\d+)", v)
    return float(m.group(1)) if m else None


def parse_show(text: str) -> list[dict]:
    """Saída de `systemctl show A B C` -> uma entrada por unidade (blocos separados por linha em branco)."""
    out, cur = [], {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
                cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    if cur:
        out.append(cur)
    return out


def list_units(user: bool = False) -> dict:
    p = _systemctl(["list-units", "--type=service", "--all", "--output=json"], user)
    try:
        units = json.loads(p.stdout or "[]")
    except ValueError:
        units = []
    names = [u["unit"] for u in units if u.get("load") != "not-found"]
    props: dict[str, dict] = {}
    for i in range(0, len(names), 150):
        chunk = names[i:i + 150]
        q = _systemctl(["show", "--timestamp=unix", "-p", ",".join(PROPS), *chunk], user)
        for d in parse_show(q.stdout):
            if d.get("Id"):
                props[d["Id"]] = d
    now = time.time()
    out = []
    with _lock:
        for u in units:
            name = u["unit"]
            d = props.get(name, {})
            cpu_ns = _int(d.get("CPUUsageNSec"))
            cpu_pct = None
            key = (user, name)
            if cpu_ns is not None:
                prev = _prev.get(key)
                if prev and now > prev[0] and cpu_ns >= prev[1]:
                    cpu_pct = round((cpu_ns - prev[1]) / 1e9 / (now - prev[0]) * 100, 1)
                _prev[key] = (now, cpu_ns)
            out.append({
                "unit": name, "description": u.get("description") or d.get("Description"),
                "load": u.get("load"), "active": u.get("active"), "sub": u.get("sub"),
                "enabled": d.get("UnitFileState") or None, "pid": _int(d.get("MainPID")) or None,
                "mem": _int(d.get("MemoryCurrent")), "cpu": cpu_pct, "cpu_total": cpu_ns / 1e9 if cpu_ns else None,
                "tasks": _int(d.get("TasksCurrent")), "restarts": _int(d.get("NRestarts")),
                "result": d.get("Result"), "since": _ts(d.get("ActiveEnterTimestamp")) if u.get("active") == "active"
                else _ts(d.get("InactiveEnterTimestamp")),
                "exit": d.get("ExecMainStatus"), "type": d.get("Type"), "restart_policy": d.get("Restart"),
                "io_read": _int(d.get("IOReadBytes")), "io_write": _int(d.get("IOWriteBytes")),
                "self": name == SELF_UNIT and not user,
            })
    counts = {"total": len(out), "running": sum(1 for x in out if x["sub"] == "running"),
              "failed": sum(1 for x in out if x["active"] == "failed")}
    return {"scope": "user" if user else "system", "units": out, "counts": counts, "ts": now,
            "error": (p.stderr.strip()[:300] or None) if p.returncode else None}


def valid_unit(unit: str) -> bool:
    return bool(RX_UNIT.fullmatch(unit or ""))


def unit_info(unit: str, user: bool = False) -> dict:
    if not valid_unit(unit):
        raise ValueError("Unidade inválida.")
    q = _systemctl(["show", "--timestamp=unix", "-p", ",".join(DETAIL_PROPS), unit], user)
    d = (parse_show(q.stdout) or [{}])[0]
    return d


def unit_logs(unit: str, user: bool = False, n: int = 80) -> list[dict]:
    from . import journal
    if not valid_unit(unit):
        raise ValueError("Unidade inválida.")
    args = ["--user-unit" if user else "-u", unit, "-n", str(max(1, min(n, 500)))]
    return [journal.normalize(e) for e in journal.run_json(args, limit=n)]


def detail(unit: str, user: bool = False) -> dict:
    d = unit_info(unit, user)
    status = _systemctl(["status", "--lines=0", unit], user, timeout=8).stdout[:3000]
    return {"unit": unit, "scope": "user" if user else "system", "props": d,
            "since": _ts(d.get("ActiveEnterTimestamp")), "status": status, "logs": unit_logs(unit, user, 120),
            "self": unit == SELF_UNIT and not user}


def action(unit: str, act: str, user: bool = False) -> dict:
    if not valid_unit(unit):
        raise ValueError("Unidade inválida.")
    if act not in ACTIONS:
        raise ValueError("Ação inválida.")
    if unit == SELF_UNIT and not user and act == "stop":
        raise PermissionError("Parar o próprio painel pelo painel o deixaria fora do ar.")
    args = [act, unit] if user else ["--no-ask-password", act, unit]
    p = _systemctl(args, user, timeout=60)
    if p.returncode:
        err = (p.stderr or p.stdout).strip()
        if re.search(r"authenticat|Access denied|Interactive authentication required|polkit", err, re.I):
            raise PermissionError("O sistema pediu senha (polkit) para mexer neste serviço. Serviços do sistema só podem "
                                  "ser controlados pelo painel com uma regra de polkit para o usuário do painel.")
        raise RuntimeError(err[:400] or f"systemctl {act} falhou")
    return {"ok": True, "unit": unit, "action": act}
