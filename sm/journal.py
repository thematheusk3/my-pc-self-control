"""Journal do systemd: vigia ao vivo (alimenta eventos e incidentes) e consultas da aba Logs.

O vigia roda um `journalctl -f -o json` só com avisos ou piores (prioridade <= 4) e tudo do kernel,
retomando do último cursor gravado (não perde nada entre reinícios do painel). Na primeira vez lê
as últimas 24 h. Cada linha passa pelas regras (rules.py); o que casar vai para o banco e para o
investigador (incidents.py).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time

from . import rules

log = logging.getLogger(__name__)
MATCH_IMPORTANT = ["PRIORITY=0", "PRIORITY=1", "PRIORITY=2", "PRIORITY=3", "PRIORITY=4", "+", "_TRANSPORT=kernel"]
FIRST_SINCE = "-24h"
CURSOR_SAVE = 10.0
RX_UNIT = re.compile(r"[\w@:.\\-]{1,200}")
RX_BOOT = re.compile(r"[0-9a-f]{32}")
RX_CURSOR = re.compile(r"[\w=;:-]{10,400}")
RX_IDENT = re.compile(r"[\w@:./+-]{1,100}")
MAX_GREP = 200


def _iso(ts: float) -> str:
    return "@" + str(int(ts))


def run_json(args: list[str], limit: int = 2000, timeout: float = 15) -> list[dict]:
    """journalctl ... -o json -> lista de entradas (no máximo `limit`)."""
    try:
        p = subprocess.run(["journalctl", "--no-pager", "-o", "json", *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("journalctl falhou: %s", e)
        return []
    out = []
    for line in p.stdout.splitlines():
        if len(out) >= limit:
            break
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def normalize(e: dict) -> dict:
    """Entrada crua -> o que a interface mostra."""
    try:
        ts = int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6
    except (TypeError, ValueError):
        ts = 0
    try:
        prio = int(e.get("PRIORITY", 6))
    except (TypeError, ValueError):
        prio = 6
    ident = rules.text(e.get("SYSLOG_IDENTIFIER") or e.get("_COMM")) or ("kernel" if rules.is_kernel(e) else "?")
    return {"ts": ts, "prio": prio, "ident": ident, "pid": e.get("_PID") or e.get("SYSLOG_PID"),
            "unit": rules.unit_of(e), "msg": rules.text(e.get("MESSAGE"))[:4000], "cursor": e.get("__CURSOR"),
            "boot": e.get("_BOOT_ID"), "kernel": rules.is_kernel(e)}


# ---------- consultas (aba Logs) ----------
def query(q: dict) -> dict:
    """Filtros: grep, unit, user_unit, ident, priority (0-7), kernel, boot, since, until (epoch), limit,
    before (cursor: página mais antiga), after (cursor: novas desde)."""
    args: list[str] = []
    limit = max(1, min(int(q.get("limit") or 300), 2000))
    if q.get("priority") not in (None, ""):
        args += ["-p", str(max(0, min(7, int(q["priority"]))))]
    if q.get("kernel"):
        args.append("-k")
    boot = q.get("boot")
    if boot and boot != "all":
        if not RX_BOOT.fullmatch(boot) and not re.fullmatch(r"-?\d{1,3}", boot):
            raise ValueError("Boot inválido.")
        args += ["-b", boot]
    for key, flag in (("unit", "-u"), ("user_unit", "--user-unit")):
        v = q.get(key)
        if v:
            if not RX_UNIT.fullmatch(v):
                raise ValueError("Unidade inválida.")
            args += [flag, v]
    if q.get("ident"):
        if not RX_IDENT.fullmatch(q["ident"]):
            raise ValueError("Identificador inválido.")
        args += ["-t", q["ident"]]
    if q.get("grep"):
        g = str(q["grep"])[:MAX_GREP]
        try:
            re.compile(g)
        except re.error:
            g = re.escape(g)
        args += ["--grep", g, "--case-sensitive=false"]
    if q.get("since"):
        args += ["--since", _iso(float(q["since"]))]
    if q.get("until"):
        args += ["--until", _iso(float(q["until"]))]
    newer = False
    if q.get("after"):
        if not RX_CURSOR.fullmatch(q["after"]):
            raise ValueError("Cursor inválido.")
        args += ["--after-cursor", q["after"]]
        newer = True
        rows = run_json(args, limit=limit)
    else:
        if q.get("before"):
            if not RX_CURSOR.fullmatch(q["before"]):
                raise ValueError("Cursor inválido.")
            args += ["--cursor", q["before"]]
            rows = run_json([*args, "-r", "-n", str(limit + 1)], limit=limit + 1)[1:]   # a própria linha do cursor
        else:
            rows = run_json([*args, "-r", "-n", str(limit)], limit=limit)
        rows.reverse()
    items = [normalize(e) for e in rows]
    return {"items": items, "newer": newer, "more": len(rows) >= limit and not newer}


def entry(cursor: str) -> dict | None:
    if not RX_CURSOR.fullmatch(cursor or ""):
        raise ValueError("Cursor inválido.")
    rows = run_json(["--cursor", cursor, "-n", "1"], limit=1)
    if not rows:
        return None
    raw = {k: (rules.text(v) if isinstance(v, list) else v) for k, v in rows[0].items()}
    return {**normalize(rows[0]), "fields": raw}


def boots(limit: int = 30) -> list[dict]:
    try:
        p = subprocess.run(["journalctl", "--list-boots", "-o", "json", "--no-pager"], capture_output=True, text=True, timeout=10)
        data = json.loads(p.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    out = [{"index": b.get("index"), "id": b.get("boot_id"), "first": (b.get("first_entry") or 0) / 1e6,
            "last": (b.get("last_entry") or 0) / 1e6} for b in data]
    out.sort(key=lambda b: b["index"] or 0, reverse=True)
    return out[:limit]


def window(t0: float, t1: float, limit: int = 400, boot: str | None = None) -> list[dict]:
    """Avisos e piores + kernel entre t0 e t1 (trecho de log dos incidentes)."""
    args = ["--since", _iso(t0), "--until", _iso(t1 + 1)]
    if boot:
        args += ["-b", boot]
    return [normalize(e) for e in run_json([*args, *MATCH_IMPORTANT], limit=limit)]


def last_of_boot(boot_id: str, n: int = 300) -> list[dict]:
    return run_json(["-b", boot_id, "-n", str(n)], limit=n)


# ---------- vigia ao vivo ----------
class JournalWatcher:
    def __init__(self, store, on_event) -> None:
        self.store = store
        self.on_event = on_event          # (evento, id) -> None
        self.proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self.cursor = store.get_meta("journal_cursor")
        self.seen = 0
        self.matched = 0
        self.started = time.time()
        self.backlog_done = False
        self.error: str | None = None

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="journal").start()

    def stop(self) -> None:
        self._stop.set()
        if self.proc:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def status(self) -> dict:
        return {"running": bool(self.proc and self.proc.poll() is None), "seen": self.seen, "matched": self.matched,
                "backlog_done": self.backlog_done, "error": self.error}

    def _cmd(self) -> list[str]:
        cmd = ["journalctl", "-f", "-o", "json", "--no-pager"]
        cmd += ["--after-cursor", self.cursor] if self.cursor else ["--since", FIRST_SINCE]
        return cmd + MATCH_IMPORTANT

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._follow()
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                log.exception("vigia do journal caiu")
            self._stop.wait(5)

    def _follow(self) -> None:
        self.proc = subprocess.Popen(self._cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 16)
        last_save = time.time()
        assert self.proc.stdout
        for line in self.proc.stdout:
            if self._stop.is_set():
                break
            try:
                e = json.loads(line)
            except ValueError:
                continue
            self.seen += 1
            self.handle(e)
            cur = e.get("__CURSOR")
            if cur:
                self.cursor = cur
            now = time.time()
            if now - last_save > CURSOR_SAVE:
                self.store.set_meta("journal_cursor", self.cursor or "")
                last_save = now
            if not self.backlog_done:
                try:
                    if int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6 > now - 5:
                        self.backlog_done = True
                except (TypeError, ValueError):
                    pass
        if self.cursor:
            self.store.set_meta("journal_cursor", self.cursor)
        self.proc.wait(timeout=5)

    def handle(self, e: dict) -> None:
        ev = rules.classify(e)
        if not ev:
            return
        self.matched += 1
        try:
            eid = self.store.add_event(ev)
        except Exception:  # noqa: BLE001
            log.exception("falha ao gravar evento")
            return
        try:
            self.on_event(ev, eid)
        except Exception:  # noqa: BLE001
            log.exception("investigador falhou com o evento %s", ev.get("kind"))
