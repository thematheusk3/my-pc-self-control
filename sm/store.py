"""Armazenamento persistente (SQLite em data/store.db): eventos, incidentes, métricas e hardware.

Pensado para o futuro gerenciador de várias máquinas: toda linha leva o `machine` (ver fleet.py) e um
`seq` crescente, então um agente pode mandar ao servidor central só o que mudou desde o último envio
(`changes(since)`), e o servidor pode usar o mesmo esquema com várias máquinas no mesmo banco.

Tabelas:
  events     linhas importantes do journal e de métricas, já classificadas (rules.py); repetições
             seguidas da mesma mensagem viram uma linha com `count`
  incidents  investigações (incidents.py): título, família, gravidade, estado e o relatório em JSON
  metrics    resumo por minuto (mín/méd/máx) das séries do painel; e por hora, guardado por mais tempo
  hardware   inventário (hwinfo.py), gravado quando muda: dá o histórico de trocas de peça/driver/kernel
  meta       chave/valor (cursor do journal, último boot verificado...)

Retenção (config [history]): eventos e métricas por minuto, N dias; por hora, 1 ano; incidentes e
hardware ficam (com teto de linhas).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time

from .config import ROOT

log = logging.getLogger(__name__)
DB_FILE = ROOT / "data" / "store.db"
SCHEMA_VERSION = 1
MAX_INCIDENTS = 5000
MAX_EVENTS = 300_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, seq INTEGER NOT NULL, machine TEXT NOT NULL,
  ts REAL NOT NULL, last_ts REAL NOT NULL, count INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL, kind TEXT NOT NULL, family TEXT NOT NULL, severity TEXT NOT NULL,
  unit TEXT, ident TEXT, pid INTEGER, boot TEXT, message TEXT NOT NULL, data TEXT, incident INTEGER
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS events_seq ON events(seq);
CREATE INDEX IF NOT EXISTS events_kind ON events(kind, ts);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY, seq INTEGER NOT NULL, machine TEXT NOT NULL, uid TEXT NOT NULL UNIQUE,
  opened REAL NOT NULL, updated REAL NOT NULL, trigger_ts REAL NOT NULL,
  family TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
  title TEXT NOT NULL, summary TEXT, subject TEXT, count INTEGER NOT NULL DEFAULT 1,
  report TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS incidents_ts ON incidents(trigger_ts);
CREATE INDEX IF NOT EXISTS incidents_seq ON incidents(seq);
CREATE TABLE IF NOT EXISTS metrics (
  machine TEXT NOT NULL, res INTEGER NOT NULL, ts INTEGER NOT NULL, key TEXT NOT NULL,
  avg REAL, min REAL, max REAL, n INTEGER, seq INTEGER NOT NULL,
  PRIMARY KEY (machine, res, key, ts)
);
CREATE INDEX IF NOT EXISTS metrics_seq ON metrics(seq);
CREATE TABLE IF NOT EXISTS hardware (
  id INTEGER PRIMARY KEY, seq INTEGER NOT NULL, machine TEXT NOT NULL, ts REAL NOT NULL,
  hash TEXT NOT NULL, data TEXT NOT NULL, changes TEXT
);
"""

SEV_ORDER = {"info": 0, "notice": 1, "warning": 2, "error": 3, "critical": 4}


class Store:
    def __init__(self, machine: str, path=DB_FILE) -> None:
        self.machine = machine
        self.path = path
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
            self.db.commit()
        try:
            path.chmod(0o600)
        except OSError:
            pass

    # ---------- utilidades ----------
    def _seq(self) -> int:
        cur = self.db.execute("SELECT v FROM meta WHERE k='seq'").fetchone()
        n = int(cur[0]) + 1 if cur else 1
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('seq', ?)", (str(n),))
        return n

    def get_meta(self, k: str, default: str | None = None) -> str | None:
        with self.lock:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def set_meta(self, k: str, v: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, v))
            self.db.commit()

    @staticmethod
    def _row(r: sqlite3.Row | None, json_cols=("data", "report", "changes")) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for c in json_cols:
            if d.get(c):
                try:
                    d[c] = json.loads(d[c])
                except ValueError:
                    pass
        return d

    # ---------- eventos ----------
    def add_event(self, ev: dict, merge_window: float = 120) -> int:
        """Grava um evento. A mesma mensagem (tipo + unidade + texto) repetida dentro da janela vira
        count+1 na linha anterior (o journal costuma repetir avisos em rajada)."""
        with self.lock:
            prev = self.db.execute(
                "SELECT id, count FROM events WHERE machine=? AND kind=? AND IFNULL(unit,'')=? AND message=? "
                "AND last_ts>=? ORDER BY id DESC LIMIT 1",
                (self.machine, ev["kind"], ev.get("unit") or "", ev["message"], ev["ts"] - merge_window)).fetchone()
            if prev:
                self.db.execute("UPDATE events SET count=count+1, last_ts=MAX(last_ts, ?), seq=? WHERE id=?",
                                (ev["ts"], self._seq(), prev["id"]))
                self.db.commit()
                return prev["id"]
            cur = self.db.execute(
                "INSERT INTO events (seq, machine, ts, last_ts, source, kind, family, severity, unit, ident, pid, boot,"
                " message, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self._seq(), self.machine, ev["ts"], ev["ts"], ev.get("source", "journal"), ev["kind"],
                 ev.get("family", "other"), ev.get("severity", "warning"), ev.get("unit"), ev.get("ident"),
                 ev.get("pid"), ev.get("boot"), ev["message"][:2000],
                 json.dumps(ev["data"], ensure_ascii=False)[:20000] if ev.get("data") else None))
            self.db.commit()
            return cur.lastrowid

    def link_events(self, ids: list[int], incident: int) -> None:
        if not ids:
            return
        with self.lock:
            self.db.executemany("UPDATE events SET incident=? WHERE id=? AND incident IS NULL",
                                [(incident, i) for i in ids])
            self.db.commit()

    def events(self, since: float = 0, until: float | None = None, kinds: list[str] | None = None,
               family: str | None = None, min_severity: str | None = None, limit: int = 500) -> list[dict]:
        q = "SELECT * FROM events WHERE machine=? AND last_ts>=? AND ts<=?"
        args: list = [self.machine, since, until if until is not None else 1e12]
        if kinds:
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        if family:
            q += " AND family=?"
            args.append(family)
        if min_severity:
            sevs = [s for s, n in SEV_ORDER.items() if n >= SEV_ORDER.get(min_severity, 0)]
            q += f" AND severity IN ({','.join('?' * len(sevs))})"
            args += sevs
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(max(1, min(int(limit), 5000)))
        with self.lock:
            return [self._row(r) for r in self.db.execute(q, args).fetchall()]

    # ---------- incidentes ----------
    def add_incident(self, inc: dict) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO incidents (seq, machine, uid, opened, updated, trigger_ts, family, kind, severity, status,"
                " title, summary, subject, count, report) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self._seq(), self.machine, inc["uid"], inc.get("opened", time.time()), time.time(), inc["trigger_ts"],
                 inc["family"], inc["kind"], inc["severity"], inc.get("status", "analyzing"), inc["title"],
                 inc.get("summary"), inc.get("subject"), inc.get("count", 1),
                 json.dumps(inc["report"], ensure_ascii=False) if inc.get("report") is not None else None))
            self.db.execute(f"DELETE FROM incidents WHERE id IN (SELECT id FROM incidents ORDER BY id DESC "
                            f"LIMIT -1 OFFSET {MAX_INCIDENTS})")
            self.db.commit()
            return cur.lastrowid

    def update_incident(self, iid: int, **fields) -> None:
        allowed = {"severity", "status", "title", "summary", "subject", "count", "report", "note", "kind", "family"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            args.append(json.dumps(v, ensure_ascii=False) if k == "report" and v is not None else v)
        if not sets:
            return
        with self.lock:
            self.db.execute(f"UPDATE incidents SET {', '.join(sets)}, updated=?, seq=? WHERE id=?",
                            (*args, time.time(), self._seq(), iid))
            self.db.commit()

    def incident(self, iid: int) -> dict | None:
        with self.lock:
            return self._row(self.db.execute("SELECT * FROM incidents WHERE id=? AND machine=?",
                                             (iid, self.machine)).fetchone())

    def incidents(self, limit: int = 100, status: str | None = None, before: float | None = None) -> list[dict]:
        q = ("SELECT id, uid, opened, updated, trigger_ts, family, kind, severity, status, title, summary, subject,"
             " count, note FROM incidents WHERE machine=?")
        args: list = [self.machine]
        if status:
            q += " AND status=?"
            args.append(status)
        if before:
            q += " AND trigger_ts<?"
            args.append(before)
        q += " ORDER BY trigger_ts DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        with self.lock:
            return [dict(r) for r in self.db.execute(q, args).fetchall()]

    def delete_incident(self, iid: int) -> bool:
        with self.lock:
            self.db.execute("UPDATE events SET incident=NULL WHERE incident=?", (iid,))
            n = self.db.execute("DELETE FROM incidents WHERE id=? AND machine=?", (iid, self.machine)).rowcount
            self.db.commit()
        return n > 0

    # ---------- métricas ----------
    def add_metrics(self, res: int, ts: int, rows: dict[str, tuple[float, float, float, int]]) -> None:
        if not rows:
            return
        with self.lock:
            seq = self._seq()
            self.db.executemany(
                "INSERT OR REPLACE INTO metrics (machine, res, ts, key, avg, min, max, n, seq) VALUES (?,?,?,?,?,?,?,?,?)",
                [(self.machine, res, ts, k, round(a, 2), round(mn, 2), round(mx, 2), n, seq)
                 for k, (a, mn, mx, n) in rows.items()])
            self.db.commit()

    def metrics(self, keys: list[str] | None, since: float, until: float | None = None, res: int | None = None) -> dict:
        """{key: [[ts, avg, min, max], ...]}. Sem `res`, escolhe por hora para períodos longos."""
        until = until or time.time()
        if res is None:
            res = 3600 if until - since > 3 * 86400 else 60
        q = "SELECT key, ts, avg, min, max FROM metrics WHERE machine=? AND res=? AND ts>=? AND ts<=?"
        args: list = [self.machine, res, since, until]
        if keys:
            q += f" AND key IN ({','.join('?' * len(keys))})"
            args += keys
        q += " ORDER BY ts"
        out: dict[str, list] = {}
        with self.lock:
            for r in self.db.execute(q, args):
                out.setdefault(r["key"], []).append([r["ts"], r["avg"], r["min"], r["max"]])
        return out

    def metric_keys(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT key FROM metrics WHERE machine=?", (self.machine,))]

    def rollup_hour(self, hour_ts: int) -> None:
        """Junta os minutos de uma hora em uma linha por chave (res=3600)."""
        with self.lock:
            rows = self.db.execute(
                "SELECT key, SUM(avg*n)/SUM(n), MIN(min), MAX(max), SUM(n) FROM metrics WHERE machine=? AND res=60 "
                "AND ts>=? AND ts<? GROUP BY key", (self.machine, hour_ts, hour_ts + 3600)).fetchall()
        self.add_metrics(3600, hour_ts, {r[0]: (r[1], r[2], r[3], r[4]) for r in rows if r[1] is not None})

    # ---------- hardware ----------
    def add_hardware(self, h: str, data: dict, changes: list | None) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO hardware (seq, machine, ts, hash, data, changes) VALUES (?,?,?,?,?,?)",
                                  (self._seq(), self.machine, time.time(), h, json.dumps(data, ensure_ascii=False),
                                   json.dumps(changes, ensure_ascii=False) if changes else None))
            self.db.commit()
            return cur.lastrowid

    def hardware_latest(self) -> dict | None:
        with self.lock:
            return self._row(self.db.execute("SELECT * FROM hardware WHERE machine=? ORDER BY id DESC LIMIT 1",
                                             (self.machine,)).fetchone())

    def hardware_history(self, limit: int = 50) -> list[dict]:
        with self.lock:
            return [self._row(r) for r in self.db.execute(
                "SELECT id, ts, hash, changes FROM hardware WHERE machine=? ORDER BY id DESC LIMIT ?",
                (self.machine, limit)).fetchall()]

    # ---------- frota (futuro servidor central) ----------
    def changes(self, since: int, limit: int = 1000) -> dict:
        """Tudo que mudou depois de `since` (seq), para um agente enviar ao servidor central."""
        limit = max(1, min(int(limit), 5000))
        out: dict = {}
        top = since
        with self.lock:
            for table in ("events", "incidents", "hardware", "metrics"):
                rows = [self._row(r) for r in self.db.execute(
                    f"SELECT * FROM {table} WHERE machine=? AND seq>? ORDER BY seq LIMIT ?",
                    (self.machine, since, limit)).fetchall()]
                out[table] = rows
                if rows:
                    top = max(top, max(r["seq"] for r in rows))
        out["next"] = top
        out["more"] = any(len(v) >= limit for k, v in out.items() if isinstance(v, list))
        return out

    # ---------- limpeza ----------
    def prune(self, days: int) -> None:
        cut = time.time() - days * 86400
        with self.lock:
            self.db.execute("DELETE FROM events WHERE last_ts<? AND incident IS NULL", (cut,))
            self.db.execute("DELETE FROM events WHERE last_ts<?", (time.time() - 365 * 86400,))
            self.db.execute(f"DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id DESC LIMIT -1 OFFSET {MAX_EVENTS})")
            self.db.execute("DELETE FROM metrics WHERE res=60 AND ts<?", (cut,))
            self.db.execute("DELETE FROM metrics WHERE res=3600 AND ts<?", (time.time() - 365 * 86400,))
            self.db.commit()

    def size(self) -> int:
        try:
            return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*"))
        except OSError:
            return 0

    def close(self) -> None:
        with self.lock:
            self.db.close()
