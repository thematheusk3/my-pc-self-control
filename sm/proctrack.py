"""Histórico de processos: registra quem aparece e quem some do /proc.

O kernel não guarda histórico de processos encerrados. Este rastreador varre /proc
a cada `track_interval` segundos (padrão 0,5 s) e, quando um PID some, move o registro
para a lista de encerrados. Processos que vivem menos que o intervalo podem escapar.
Para ver 100% seria preciso root + audit/eBPF.

Threads do kernel e os subprocessos do próprio monitor são ignorados.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter, deque
from pathlib import Path

import psutil

from .config import ROOT

log = logging.getLogger(__name__)
HIST_FILE = ROOT / "data" / "exited.json"
CLK = os.sysconf("SC_CLK_TCK")


def _read(path: str, limit: int = 8192) -> str | None:
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode(errors="replace")
    except OSError:
        return None


def _uid_names() -> dict[int, str]:
    import pwd
    try:
        return {p.pw_uid: p.pw_name for p in pwd.getpwall()}
    except Exception:  # noqa: BLE001
        return {}


def read_unit(pid: int | str) -> str | None:
    """Unidade systemd (serviço/scope) a partir do cgroup."""
    cg = _read(f"/proc/{pid}/cgroup", 2048)
    if not cg:
        return None
    path = cg.strip().splitlines()[-1].partition("::")[2]
    for part in reversed(path.split("/")):
        if part.endswith((".service", ".scope")):
            return part
    return None


def _mntns(pid) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/ns/mnt")
    except OSError:
        return None


SELF_MNTNS = _mntns("self")


def in_container(pid) -> bool | None:
    """True se o processo roda em outro namespace de montagem (Flatpak, Steam Runtime, Docker...)."""
    ns = _mntns(pid)
    return None if ns is None or SELF_MNTNS is None else ns != SELF_MNTNS


RISKY_PREFIXES = ("/tmp/", "/var/tmp/", "/dev/shm/", "/run/shm/", "/memfd:")


def _risky(exe: str) -> bool:
    """Executáveis que costumam se apagar logo: guarda o hash enquanto ainda dá para ler."""
    return (exe.startswith(RISKY_PREFIXES) or exe.endswith(" (deleted)")
            or os.path.basename(exe).startswith("."))


def _quick_hash(pid: int, max_bytes: int = 64 * 1024 * 1024) -> str | None:
    import hashlib
    h = hashlib.sha256()
    try:
        with open(f"/proc/{pid}/exe", "rb") as f:   # funciona mesmo com o arquivo apagado
            n = 0
            for chunk in iter(lambda: f.read(1 << 20), b""):
                n += len(chunk)
                if n > max_bytes:
                    return None
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def capture(pid: int, boot: float, users: dict[int, str]) -> dict | None:
    """Lê o mínimo de /proc/<pid> rapidamente (o processo pode sumir a qualquer momento)."""
    stat = _read(f"/proc/{pid}/stat", 1024)
    if not stat:
        return None
    r = stat.rfind(")")
    comm = stat[stat.find("(") + 1:r]
    fields = stat[r + 2:].split()
    try:
        ppid, start_ticks = int(fields[1]), int(fields[19])
    except (IndexError, ValueError):
        return None
    if pid == 2 or ppid == 2:     # threads do kernel
        return None
    raw = _read(f"/proc/{pid}/cmdline", 4096) or ""
    argv = [a for a in raw.split("\0") if a]
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
        exe_ok = True
    except OSError:              # processo de outro usuário (sem root) ou já encerrado
        exe, exe_ok = None, False
    uid = None
    st = _read(f"/proc/{pid}/status", 4096) or ""
    for line in st.splitlines():
        if line.startswith("Uid:"):
            try:
                uid = int(line.split()[1])
            except (IndexError, ValueError):
                pass
            break
    sha = None
    if exe and _risky(exe):
        sha = _quick_hash(pid)
    return {
        "id": f"{pid}-{start_ticks}",
        "sha256": sha,
        "pid": pid, "ppid": ppid, "name": comm,
        "cmd": " ".join(argv)[:1000], "argv0": argv[0] if argv else None,
        "exe": exe, "exe_verified": exe_ok,
        "uid": uid, "user": users.get(uid, str(uid)) if uid is not None else "?",
        "start": round(boot + start_ticks / CLK, 2),
        "unit": read_unit(pid),
        "container": in_container(pid),
        "peak_cpu": 0.0, "peak_mem": 0,
    }


class ProcessTracker:
    def __init__(self, ctx) -> None:
        cfg = ctx.config
        self.interval = cfg.track_interval
        self.persist = cfg.track_persist
        self.self_pid = os.getpid()
        self.boot = psutil.boot_time()
        self.users = _uid_names()
        self.live: dict[int, dict] = {}
        self.exited: deque[dict] = deque(maxlen=cfg.exited_max)
        self.total_exited = 0
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._n = 0
        self._load()

    # ---------- persistência ----------
    def _load(self) -> None:
        if not self.persist:
            return
        try:
            data = json.loads(HIST_FILE.read_text())
            self.exited.extend(data.get("exited", [])[-self.exited.maxlen:])
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        if not self.persist:
            return
        try:
            with self._lock:
                data = {"exited": list(self.exited)}
            tmp = HIST_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")))
            os.chmod(tmp, 0o600)
            tmp.replace(HIST_FILE)
        except OSError as e:
            log.warning("não foi possível salvar o histórico de processos: %s", e)

    # ---------- laço ----------
    def start(self) -> None:
        self._scan(initial=True)
        threading.Thread(target=self._loop, daemon=True, name="proctrack").start()

    def stop(self) -> None:
        self._stop.set()
        self.save()

    def _loop(self) -> None:
        last_save = time.monotonic()
        while not self._stop.wait(self.interval):
            try:
                self._scan()
            except Exception:  # noqa: BLE001
                log.exception("proctrack: falha na varredura")
            if time.monotonic() - last_save > 300:
                self.save()
                last_save = time.monotonic()

    def _ignored(self, rec: dict) -> bool:
        return rec["ppid"] == self.self_pid or rec["pid"] == self.self_pid

    def _reused(self, pids: set[int]) -> set[int]:
        """PIDs conhecidos que agora são outro processo (horário de início diferente do registrado).
        Raro no Linux (pid_max de 4 milhões), mas acontece em quem cria muitos processos."""
        out = set()
        with self._lock:
            ids = {pid: self.live[pid]["id"] for pid in pids if pid in self.live}
        for pid, rid in ids.items():
            stat = _read(f"/proc/{pid}/stat", 1024)
            if not stat:
                continue
            try:
                ticks = stat[stat.rfind(")") + 2:].split()[19]
            except IndexError:
                continue
            if rid != f"{pid}-{ticks}":
                out.add(pid)
        return out

    def _scan(self, initial: bool = False) -> None:
        now = time.time()
        self._n += 1
        pids = {int(p) for p in os.listdir("/proc") if p.isdigit()}
        with self._lock:
            known = set(self.live)
        reused = self._reused(known & pids) if self._n % 2 == 0 else set()    # a cada ~1 s
        new = (pids - known) | reused
        gone = (known - pids) | reused
        recs = {}
        for pid in new:
            rec = capture(pid, self.boot, self.users)
            if rec is None or self._ignored(rec):
                continue
            rec["preexisting"] = initial
            recs[pid] = rec
        with self._lock:
            for pid in gone:
                if pid in self.live:
                    self._finish(self.live.pop(pid), now)
            for pid, rec in recs.items():
                parent = self.live.get(rec["ppid"])
                rec["parent_name"] = parent["name"] if parent else None
                self.live[pid] = rec

    def _finish(self, rec: dict, now: float) -> None:
        rec["end"] = round(now, 2)
        rec["duration"] = round(max(0.0, now - rec["start"]), 2)
        self.exited.append(rec)
        self.total_exited += 1

    def observe(self, procs: list[dict]) -> None:
        """Chamado pelo coletor de processos: guarda picos de CPU e memória."""
        with self._lock:
            for p in procs:
                rec = self.live.get(p["pid"])
                if rec is None:
                    continue
                if p["cpu"] > rec["peak_cpu"]:
                    rec["peak_cpu"] = p["cpu"]
                if p["mem"] > rec["peak_mem"]:
                    rec["peak_mem"] = p["mem"]

    # ---------- consultas ----------
    def get_live(self, pid: int) -> dict | None:
        with self._lock:
            r = self.live.get(pid)
            return dict(r) if r else None

    def find(self, rid: str) -> dict | None:
        with self._lock:
            for r in reversed(self.exited):
                if r["id"] == rid:
                    return dict(r)
            for r in self.live.values():
                if r["id"] == rid:
                    return dict(r)
        return None

    def ancestors(self, ppid: int, limit: int = 12) -> list[dict]:
        """Cadeia de pais (usa os registros vivos e encerrados)."""
        out = []
        with self._lock:
            by_pid = {r["pid"]: r for r in self.exited}
            by_pid.update(self.live)
            seen = set()
            while ppid and ppid not in seen and len(out) < limit:
                seen.add(ppid)
                r = by_pid.get(ppid)
                if not r:
                    out.append({"pid": ppid, "name": "?", "alive": False})
                    break
                out.append({"pid": r["pid"], "name": r["name"], "user": r["user"],
                            "alive": r["pid"] in self.live and "end" not in r})
                ppid = r["ppid"]
        return out

    def list_exited(self, limit: int = 2000) -> dict:
        with self._lock:
            items = list(self.exited)[-limit:]
            counts = Counter(r["name"] for r in self.exited)
        items.reverse()
        return {
            "exited": items,
            "total": self.total_exited,
            "kept": len(self.exited),
            "max": self.exited.maxlen,
            "interval": self.interval,
            "since": self.started_at,
            "top_names": counts.most_common(10),
        }

    def clear(self) -> None:
        with self._lock:
            self.exited.clear()
        self.save()
