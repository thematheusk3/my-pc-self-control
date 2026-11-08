"""Investigador de incidentes: correlação de eventos + regras, sem IA.

Fluxo:
  1. Um evento "gatilho" (rules.py: reset/travamento da GPU, core dump, OOM, serviço que falhou, erro de
     disco, MCE, falha do kernel, temperatura crítica) abre um incidente. Gatilhos próximos (2 min) de
     famílias relacionadas entram no mesmo incidente (ex.: GPU travou e, 3 s depois, o jogo caiu).
  2. Espera alguns segundos para juntar o que vem depois e monta o relatório:
     - linha do tempo: eventos classificados, ações feitas pelo painel, arquivos de overlay alterados,
       pacotes atualizados, tudo na janela (5 min antes até 1 min depois);
     - métricas em volta (CPU, temperaturas, GPU, memória, FPS): antes, no momento e pico;
     - processos e jogo no momento (se o incidente é ao vivo);
     - o programa/serviço envolvido (executável, sinal, pilha do core dump, tempo rodando);
     - mudanças recentes (kernel, Mesa, firmware, Proton, hardware);
     - hipóteses com confiança, evidências e o que verificar;
     - um texto em Markdown pronto para colar num chat com uma IA.
  3. Na partida, verifica se o boot anterior terminou sem desligamento limpo (queda de energia,
     travamento total) e abre um incidente com as últimas linhas daquele boot.
Também dá para pedir uma investigação manual de um momento (aba Logs/Incidentes).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections import Counter

from . import fleet, hwinfo, journal

log = logging.getLogger(__name__)
PRE = 300            # s antes do gatilho na janela do relatório
POST = 60            # s depois do último gatilho
SETTLE = 25          # s esperando eventos que vêm depois, antes de montar o relatório
GROUP = 120          # s: gatilhos próximos entram no mesmo incidente
REPEAT = 15 * 60     # s: o mesmo programa/serviço caindo de novo vira repetição do mesmo incidente
RELATED = {"gpu": {"gpu", "crash", "session", "kernel"}, "crash": {"gpu", "crash", "memory", "session"},
           "memory": {"memory", "crash", "service"}, "service": {"service", "memory"},
           "kernel": {"kernel", "gpu", "hardware"}, "hardware": {"hardware", "kernel", "storage"},
           "storage": {"storage", "hardware"}, "thermal": {"thermal", "gpu", "crash"}, "session": {"session", "gpu", "crash"}}
KIND_RANK = ["gpu_reset", "gpu_hang", "mce", "kernel_bug", "disk_io", "oom", "thermal", "gpu_fault", "unexpected_shutdown",
             "coredump", "unit_failed", "manual"]
SEV = {"info": 0, "notice": 1, "warning": 2, "error": 3, "critical": 4}
OVERLAY_FILES = ["~/.config/MangoHud/MangoHud.conf", "~/.config/vkBasalt/vkBasalt.conf", "~/.config/goverlay/goverlay.conf"]
RELEVANT_PKGS = re.compile(r"^(linux|linux-cachyos.*|linux-firmware.*|mesa|lib32-mesa|vulkan-.*|lib32-vulkan-.*|nvidia.*|"
                           r"proton.*|wine.*|dxvk.*|vkd3d.*|mangohud.*|gamescope|steam|kwin|xorg-server|amd-ucode|"
                           r"intel-ucode|systemd|pipewire.*)$")
CLEAN_SHUTDOWN = re.compile(r"Stopping Flush Journal to Persistent Storage|Record System Boot/Shutdown in UTMP|"
                            r"Journal stopped|Reached target .*(Shutdown|Power-Off|Reboot|Halt|Final Step)|"
                            r"System is (powering down|rebooting|halting)|systemd-shutdown")
NOISE = re.compile(r"^\[UFW (BLOCK|ALLOW|AUDIT)\]|^audit: type=|nft.*(DROP|REJECT)|^IN=\S* OUT=")
METRICS = {   # chave -> (rótulo, unidade)
    "cpu.total": ("Uso da CPU", "%"), "cpu.temp": ("Temperatura da CPU", "°C"), "cpu.freq": ("Clock da CPU", "MHz"),
    "memory.percent": ("Memória usada", "%"), "memory.swap": ("Swap usada", "%"),
    "gpu.0.busy": ("Uso da GPU", "%"), "gpu.0.temp": ("Temperatura da GPU", "°C"), "gpu.0.vram": ("VRAM usada", "%"),
    "gpu.0.gtt": ("Memória GTT da GPU", "%"), "gpu.0.power": ("Consumo da GPU", "W"), "gpu.0.clock": ("Clock da GPU", "MHz"),
    "game.fps": ("FPS", ""), "fan.rpm": ("Ventoinha", "RPM"), "disks.read": ("Leitura de disco", "B/s"),
    "disks.write": ("Escrita de disco", "B/s"),
}
TITLES = {
    "gpu_reset": "GPU travou e foi reiniciada pelo driver", "gpu_hang": "GPU travou (comandos sem resposta)",
    "gpu_fault": "Acesso inválido à memória da GPU", "oom": "Memória esgotada: o sistema encerrou um processo",
    "kernel_bug": "Falha no kernel", "mce": "Erro de hardware (MCE)", "disk_io": "Erro de leitura/escrita no disco",
    "thermal": "Temperatura crítica", "unexpected_shutdown": "Desligamento inesperado",
    "manual": "Investigação manual",
}


def _sev_max(a: str, b: str) -> str:
    return a if SEV.get(a, 0) >= SEV.get(b, 0) else b


def _rank(kind: str) -> int:
    return KIND_RANK.index(kind) if kind in KIND_RANK else len(KIND_RANK)


def _hm(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _when(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(ts))


class Investigator:
    def __init__(self, ctx, store) -> None:
        self.ctx = ctx
        self.store = store
        self.lock = threading.Lock()
        self.pending: dict[int, dict] = {}     # id -> {first, last, due, events, family, live_ctx}
        self._stop = threading.Event()
        self.audit = None                        # sm.audit.Audit (definido pelo servidor)

    # ---------- entrada de eventos ----------
    def on_event(self, ev: dict, eid: int) -> None:
        if not ev.get("trigger"):
            return
        now = time.time()
        with self.lock:
            for iid, p in self.pending.items():
                if abs(ev["ts"] - p["last"]) <= GROUP and ev["family"] in RELATED.get(p["family"], {p["family"]}):
                    p["events"].append((ev, eid))
                    p["last"] = max(p["last"], ev["ts"])
                    p["due"] = max(p["due"], self._due(ev["ts"], now))
                    if _rank(ev["kind"]) < _rank(p["kind"]):
                        p["kind"], p["family"] = ev["kind"], ev["family"]
                    p["severity"] = _sev_max(p["severity"], ev["severity"])
                    self.store.link_events([eid], iid)
                    return
        subject = self._subject_key(ev)
        rep = self._recent_same(subject, ev["ts"])
        if rep:
            self.store.link_events([eid], rep["id"])
            report = (self.store.incident(rep["id"]) or {}).get("report") or {}
            report.setdefault("repeats", []).append(ev["ts"])
            self.store.update_incident(rep["id"], count=rep["count"] + 1, report=report)
            return
        live = ev["ts"] > now - 60
        inc = {"uid": uuid.uuid4().hex[:12], "opened": now, "trigger_ts": ev["ts"], "family": ev["family"],
               "kind": ev["kind"], "severity": ev["severity"], "status": "analyzing",
               "title": self._title(ev["kind"], [ev]), "subject": subject}
        iid = self.store.add_incident(inc)
        self.store.link_events([eid], iid)
        with self.lock:
            self.pending[iid] = {"first": ev["ts"], "last": ev["ts"], "due": self._due(ev["ts"], now),
                                 "events": [(ev, eid)], "family": ev["family"], "kind": ev["kind"],
                                 "severity": ev["severity"], "live": self._live_context() if live else None}
        if live and self.ctx.alerts:
            self.ctx.alerts.event("incident", f"{inc['title']} — abra Incidentes para ver a análise.",
                                  "critical" if ev["severity"] == "critical" else "warning", key=inc["uid"])

    @staticmethod
    def _due(ts: float, now: float) -> float:
        return max(ts + SETTLE, now + 3)

    def _subject_key(self, ev: dict) -> str | None:
        d = ev.get("data") or {}
        if ev["kind"] == "coredump":
            return "exe:" + _proc_name(d)
        if ev["kind"] == "unit_failed":
            return "unit:" + (ev.get("unit") or "?")
        if ev["family"] == "gpu":
            return "gpu"
        return f"{ev['family']}:{ev['kind']}"

    def _recent_same(self, subject: str | None, ts: float) -> dict | None:
        if not subject:
            return None
        for inc in self.store.incidents(limit=30):
            if inc["subject"] == subject and 0 <= ts - inc["trigger_ts"] <= REPEAT and inc["id"] not in self.pending:
                return inc
        return None

    def _live_context(self) -> dict:
        procs = list(self.ctx.processes or [])
        top_cpu = sorted(procs, key=lambda p: p.get("cpu") or 0, reverse=True)[:8]
        top_mem = sorted(procs, key=lambda p: p.get("mem") or 0, reverse=True)[:8]
        keep = ("pid", "name", "user", "cpu", "mem", "gpu", "gpu_mem")
        data = self.ctx.data or {}
        game = (data.get("game") or {})
        mem = data.get("memory") or {}
        gpus = [{k: g.get(k) for k in ("name", "busy", "temp", "vram", "gtt", "power", "clocks")}
                for g in (data.get("gpu") or {}).get("gpus", [])]
        return {"captured": time.time(),
                "top_cpu": [{k: p.get(k) for k in keep} for p in top_cpu],
                "top_mem": [{k: p.get(k) for k in keep} for p in top_mem],
                "game": {"name": (game.get("game") or {}).get("name"), "fps": game.get("fps"), "low1": game.get("low1")}
                if game.get("game") else None,
                "memory": {k: mem.get(k) for k in ("total", "used", "available", "percent", "swap_total", "swap_used")
                           if k in mem}, "gpus": gpus,
                "uptime": time.time() - _boot_time()}

    # ---------- laço ----------
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="investigator").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(8)
        try:
            self.check_previous_boot()
        except Exception:  # noqa: BLE001
            log.exception("verificação do boot anterior falhou")
        while not self._stop.is_set():
            now = time.time()
            with self.lock:
                due = [(i, p) for i, p in self.pending.items() if p["due"] <= now]
                for i, _ in due:
                    self.pending.pop(i, None)
            for iid, p in due:
                try:
                    self.build(iid, p)
                except Exception:  # noqa: BLE001
                    log.exception("relatório do incidente %s falhou", iid)
                    self.store.update_incident(iid, status="open", summary="Falha ao montar o relatório; veja o log do painel.")
            self._stop.wait(2)

    # ---------- investigação manual ----------
    def investigate(self, at: float, before: float = PRE, after: float = POST, note: str | None = None) -> int:
        now = time.time()
        at = min(float(at), now)
        inc = {"uid": uuid.uuid4().hex[:12], "opened": now, "trigger_ts": at, "family": "manual", "kind": "manual",
               "severity": "info", "status": "analyzing", "title": f"Investigação manual de {_when(at)}", "subject": None}
        iid = self.store.add_incident(inc)
        p = {"first": at, "last": at, "events": [], "family": "manual", "kind": "manual", "severity": "info",
             "live": self._live_context() if now - at < 120 else None, "pre": max(30, min(before, 6 * 3600)),
             "post": max(0, min(after, 3600)), "note": note}
        threading.Thread(target=self._safe_build, args=(iid, p), daemon=True).start()
        return iid

    def _safe_build(self, iid: int, p: dict) -> None:
        try:
            self.build(iid, p)
        except Exception:  # noqa: BLE001
            log.exception("investigação manual falhou")
            self.store.update_incident(iid, status="open", summary="Falha ao montar o relatório.")

    # ---------- boot anterior ----------
    def check_previous_boot(self) -> int | None:
        boots = journal.boots(limit=3)
        prev = next((b for b in boots if b["index"] == -1), None)
        if not prev or self.store.get_meta("boot_checked") == prev["id"]:
            return None
        self.store.set_meta("boot_checked", prev["id"])
        tail = journal.last_of_boot(prev["id"], 400)
        msgs = [journal.normalize(e) for e in tail]
        if any(CLEAN_SHUTDOWN.search(m["msg"]) for m in msgs):
            return None
        last = msgs[-1]["ts"] if msgs else prev["last"]
        ev = {"ts": last, "kind": "unexpected_shutdown", "family": "power", "severity": "critical", "trigger": True,
              "source": "boot", "unit": None, "ident": None, "pid": None, "boot": prev["id"],
              "message": f"O boot anterior terminou sem desligamento normal (último registro às {_hm(last)}).",
              "data": {"label": "Desligamento inesperado", "boot": prev["id"]}}
        eid = self.store.add_event(ev)
        inc = {"uid": uuid.uuid4().hex[:12], "opened": time.time(), "trigger_ts": last, "family": "power",
               "kind": "unexpected_shutdown", "severity": "critical", "status": "analyzing",
               "title": TITLES["unexpected_shutdown"], "subject": "power:unexpected_shutdown"}
        iid = self.store.add_incident(inc)
        self.store.link_events([eid], iid)
        self.build(iid, {"first": last, "last": last, "events": [(ev, eid)], "family": "power",
                         "kind": "unexpected_shutdown", "severity": "critical", "live": None, "boot": prev["id"],
                         "tail": msgs[-60:]})
        if self.ctx.alerts:
            self.ctx.alerts.event("incident", f"O PC desligou sem desligamento normal às {_when(last)}.", "critical",
                                  key=inc["uid"])
        return iid

    # ---------- relatório ----------
    def build(self, iid: int, p: dict) -> dict:
        evs = [e for e, _ in p["events"]]
        t = p["first"]
        t0, t1 = t - p.get("pre", PRE), p["last"] + p.get("post", POST)
        stored = self.store.events(since=t0, until=t1, limit=600)
        raw = journal.window(t0, t1, limit=500, boot=p.get("boot"))
        if p.get("tail"):
            raw = [m for m in p["tail"] if m["ts"] >= t0] or p["tail"]
        main = min(evs, key=lambda e: (_rank(e["kind"]), e["ts"])) if evs else None
        subject = self._subject(evs)
        metrics = self._metrics(t0, t1, t)
        changes = self._changes(t0, t1, t)
        recurrence = self._recurrence(p, subject)
        facts = self._facts(evs, stored, metrics, changes, subject, p)
        hyps = hypotheses(facts)
        timeline = self._timeline(stored, changes, evs, t0, t1)
        title = self._title(p["kind"], evs, subject)
        summary = (hyps[0]["title"] + f" (confiança {hyps[0]['level']})") if hyps else "Sem hipótese forte; veja a linha do tempo."
        report = {
            "version": 1, "window": [t0, t1], "trigger_ts": t, "main": main, "triggers": evs, "subject": subject,
            "timeline": timeline, "metrics": metrics, "context": p.get("live"), "changes": changes,
            "logs": _collapse(raw)[-200:],
            "hypotheses": hyps, "recurrence": recurrence, "system": self._system(), "note": p.get("note"),
            "built": time.time(),
        }
        report["ai_text"] = ai_text(title, report)
        self.store.update_incident(iid, report=report, status="open", title=title, summary=summary,
                                   kind=p["kind"], family=p["family"], severity=p["severity"],
                                   subject=self._subject_key(main) if main else None)
        log.info("incidente %s: %s — %s", iid, title, summary)
        return report

    def _title(self, kind: str, evs: list[dict], subject: dict | None = None) -> str:
        crash = next((e for e in evs if e["kind"] == "coredump"), None)
        name = None
        if subject and subject.get("name"):
            name = subject.get("game") or subject["name"]
        elif crash:
            name = _proc_name(crash.get("data") or {})
        gpu = any(e["family"] == "gpu" for e in evs)
        if kind == "coredump" or (kind in ("gpu_hang", "gpu_reset", "gpu_fault") and crash):
            sig = (crash.get("data") or {}).get("signal") if crash else None
            if gpu:
                return f"GPU travou e {name or 'um programa'} caiu"
            if subject and subject.get("launcher") and subject.get("game"):
                return f"{subject['game']} travou ({sig or '?'}, aberto pelo {subject['launcher']})"
            return f"{name or 'Programa'} travou" + (f" ({sig})" if sig else "")
        if kind == "unit_failed":
            unit = next((e.get("unit") for e in evs if e["kind"] == "unit_failed"), None)
            return f"Serviço {unit or '?'} falhou"
        return TITLES.get(kind, "Incidente")

    def _subject(self, evs: list[dict]) -> dict | None:
        crash = next((e for e in evs if e["kind"] == "coredump"), None)
        if crash:
            d = crash.get("data") or {}
            exe = d.get("exe") or ""
            s = {"type": "process", "name": _proc_name(d), "exe": exe, "pid": crash.get("pid"),
                 "signal": d.get("signal"), "cmdline": d.get("cmdline"), "unit": d.get("core_unit") or crash.get("unit"),
                 "stack": _stack(crash), "core": d.get("storage")}
            s.update(_process_extra(self.ctx, crash))
            return s
        unitf = next((e for e in evs if e["kind"] == "unit_failed"), None)
        if unitf:
            from . import services
            unit = unitf.get("unit")
            info = services.unit_info(unit, user=_is_user_unit(unitf)) if unit else {}
            return {"type": "unit", "name": unit, "result": (unitf.get("data") or {}).get("result"),
                    "unit_info": info, "logs": services.unit_logs(unit, user=_is_user_unit(unitf), n=40) if unit else []}
        if any(e["family"] == "gpu" for e in evs):
            gpus = (self.ctx.data.get("gpu") or {}).get("gpus") or []
            return {"type": "gpu", "name": gpus[0].get("name") if gpus else "GPU"}
        return None

    def _metrics(self, t0: float, t1: float, t: float) -> dict:
        live = self.ctx.history.get(None, since=t0)
        series: dict[str, list] = {}
        source = "ao vivo (1 s)"
        if live and any(v and v[0][0] <= t for v in live.values()):
            for k, pts in live.items():
                if k in METRICS:
                    series[k] = [[ts, v] for ts, v in pts if ts <= t1]
        if not series:
            source = "resumo por minuto"
            for k, pts in self.store.metrics(list(METRICS), t0 - 120, t1 + 60, res=60).items():
                series[k] = [[ts + 30, avg] for ts, avg, _mn, _mx in pts]
                series[k + ":max"] = [[ts + 30, mx] for ts, _a, _mn, mx in pts]
        summary = []
        for k, (label, unit) in METRICS.items():
            pts = series.get(k)
            if not pts:
                continue
            maxs = series.get(k + ":max") or pts
            before = [v for ts, v in pts if ts < t - 30]
            near = [v for ts, v in pts if t - 15 <= ts <= t + 5] or [v for ts, v in pts if abs(ts - t) <= 90]
            summary.append({"key": k, "label": label, "unit": unit,
                            "before": round(sum(before) / len(before), 1) if before else None,
                            "at": round(max(near), 1) if near else None,
                            "max": round(max(v for _ts, v in maxs), 1), "min": round(min(v for _ts, v in pts), 1)})
        compact = {k: _downsample(v, 120) for k, v in series.items() if not k.endswith(":max")}
        return {"source": source if summary else None, "summary": summary, "series": compact}

    def _changes(self, t0: float, t1: float, t: float) -> dict:
        pkgs = [u for u in hwinfo.recent_upgrades(days=3, now=t) if RELEVANT_PKGS.match(u["package"]) and u["ts"] <= t]
        hw = [h for h in self.store.hardware_history(limit=20) if h.get("changes") and t - 7 * 86400 <= h["ts"] <= t]
        panel = []
        if self.audit:
            for a in self.audit.list(limit=300):
                if t0 <= a["time"] <= t1:
                    panel.append({"ts": a["time"], "text": a["text"], "cat": a["cat"], "device": a.get("device")})
        files = []
        for f in OVERLAY_FILES:
            path = f.replace("~", str(_home()), 1)
            try:
                m = os.stat(path).st_mtime
            except OSError:
                continue
            if t0 <= m <= t1 or (t - 600 <= m <= t):
                files.append({"ts": m, "path": path})
        return {"packages": pkgs[-40:], "hardware": hw, "panel": panel, "files": files}

    def _recurrence(self, p: dict, subject: dict | None) -> dict:
        key = None
        main = min((e for e, _ in p["events"]), key=lambda e: _rank(e["kind"]), default=None)
        if main:
            key = self._subject_key(main)
        if not key:
            return {}
        week = [i for i in self.store.incidents(limit=200) if i["subject"] == key and i["trigger_ts"] >= p["first"] - 7 * 86400]
        return {"key": key, "last_7d": len(week), "total_repeats": sum(i["count"] for i in week)}

    def _facts(self, evs, stored, metrics, changes, subject, p) -> dict:
        t = p["first"]
        kinds = Counter(e["kind"] for e in stored)
        near_gpu = [e for e in stored if e["family"] == "gpu" and e["kind"] in ("gpu_hang", "gpu_reset", "gpu_fault")
                    and t - 120 <= e["ts"] <= t + 15]
        msum = {m["key"]: m for m in metrics.get("summary", [])}
        crash = next((e for e in evs if e["kind"] == "coredump"), None)
        cdata = (crash or {}).get("data") or {}
        exe = (cdata.get("exe") or "") + " " + (cdata.get("cmdline") or "")
        unitf = next((e for e in evs if e["kind"] == "unit_failed"), None)
        hw = self.store.hardware_latest()
        return {
            "t": t, "kind": p["kind"], "family": p["family"], "kinds": kinds, "near_gpu": near_gpu,
            "gpu_fence": [e for e in stored if e["kind"] == "gpu_fence" and t - 300 <= e["ts"] <= t + 15],
            "crash": crash, "signal": cdata.get("signal"),
            "is_game": bool(re.search(r"\.exe\b|wine|proton|steamapps|/Steam/", exe, re.I)
                            or re.search(r"app-steam-app\d+", str(cdata.get("core_unit")))),
            "mod_launcher": bool(re.search(r"launcher|ersc|seamless|modengine|dinput8|reshade|\bmods?\b",
                                           exe + " " + str((subject or {}).get("launcher") or ""), re.I)),
            "proton": (re.search(r"Proton[^/]*|proton-[\w.-]+", exe, re.I) or [None])[0],
            "temps": {k: msum[k] for k in ("cpu.temp", "gpu.0.temp") if k in msum},
            "mem": msum.get("memory.percent"), "gtt": msum.get("gpu.0.gtt"), "vram": msum.get("gpu.0.vram"),
            "swap_total": ((hw or {}).get("data") or {}).get("memory", {}).get("swap") if hw else None,
            "oom": [e for e in stored if e["kind"] == "oom"],
            "storage": [e for e in stored if e["family"] == "storage"],
            "mce": [e for e in stored if e["kind"] == "mce"], "aer": [e for e in stored if e["kind"] == "pcie_aer"],
            "kernel_bug": [e for e in stored if e["kind"] == "kernel_bug"],
            "thermal": [e for e in stored if e["kind"] == "thermal"],
            "unit": unitf, "unit_result": ((unitf or {}).get("data") or {}).get("result"), "subject": subject,
            "files": [f for f in changes["files"] if f["ts"] <= t + 2],
            "panel": [a for a in changes["panel"] if a["ts"] <= t + 2],
            "packages": changes["packages"], "hardware": changes["hardware"],
            "metrics_available": bool(metrics.get("summary")), "tail": p.get("tail"),
        }

    def _timeline(self, stored, changes, evs, t0, t1) -> list[dict]:
        out = []
        for e in stored:
            label = (e.get("data") or {}).get("label") if isinstance(e.get("data"), dict) else None
            out.append({"ts": e["ts"], "kind": e["kind"], "family": e["family"], "severity": e["severity"],
                        "source": e["source"], "who": e.get("ident") or e.get("unit"), "text": e["message"][:400],
                        "label": label, "count": e.get("count", 1), "trigger": e["kind"] in {x["kind"] for x in evs}})
        for a in changes["panel"]:
            out.append({"ts": a["ts"], "kind": "panel_action", "family": "change", "severity": "info", "source": "painel",
                        "who": a.get("device"), "text": a["text"], "label": "Ação feita pelo painel"})
        for f in changes["files"]:
            if t0 <= f["ts"] <= t1:
                out.append({"ts": f["ts"], "kind": "file_changed", "family": "change", "severity": "notice", "source": "arquivo",
                            "who": None, "text": f"{f['path']} foi alterado", "label": "Configuração de overlay alterada"})
        for u in changes["packages"]:
            if t0 <= u["ts"] <= t1:
                out.append({"ts": u["ts"], "kind": "package", "family": "change", "severity": "notice", "source": "pacman",
                            "who": None, "text": f"{u['action']} {u['package']} {u['version']}", "label": "Pacote"})
        out.sort(key=lambda x: x["ts"])
        return out[-300:]

    def _system(self) -> dict:
        hw = self.store.hardware_latest()
        d = (hw or {}).get("data") or {}
        return {**fleet.identity(), "board": d.get("board"), "cpu": (d.get("cpu") or {}).get("model"),
                "gpus": [g.get("name") or g.get("device_id") for g in d.get("gpus") or []],
                "memory": (d.get("memory") or {}).get("total"), "packages": d.get("packages"), "os_name": d.get("os")}


# ---------- hipóteses ----------
def _h(hid: str, title: str, score: int, evidence: list[str], checks: list[str]) -> dict:
    score = max(0, min(100, score))
    return {"id": hid, "title": title, "score": score, "level": "alta" if score >= 75 else "média" if score >= 50 else "baixa",
            "evidence": [e for e in evidence if e], "checks": checks}


def hypotheses(f: dict) -> list[dict]:
    """Regras de correlação -> hipóteses ordenadas por confiança."""
    out: list[dict] = []
    t = f["t"]
    crash = f["crash"]

    def rel(ts: float) -> str:
        d = ts - t
        return f"{abs(d):.0f} s {'antes' if d < 0 else 'depois'}" if abs(d) >= 1 else "no mesmo segundo"

    if f["near_gpu"]:
        ev = f["near_gpu"]
        kinds = {e["kind"] for e in ev}
        evidence = [f"{_hm(e['ts'])} ({rel(e['ts'])}): {e['message'][:160]}" for e in ev[:5]]
        score = 88 if kinds & {"gpu_hang", "gpu_reset"} else 70
        if crash:
            evidence.append(f"{crash['data'].get('comm') or 'o programa'} caiu {rel(crash['ts']).replace('no mesmo segundo', 'junto')} do erro da GPU")
        title = ("Falha na execução de comandos da GPU: o driver detectou travamento e reiniciou a GPU"
                 if kinds & {"gpu_hang", "gpu_reset"} else "Acesso inválido à memória da GPU (bug do driver, do jogo ou dos shaders)")
        out.append(_h("gpu", title, score, evidence, [
            "Temperatura, clock e consumo da GPU no momento (tabela de métricas)",
            "Overclock/undervolt ou perfil de clocks agressivo (na BC-250, o governor/perfil de frequência)",
            "Mesa/kernel/firmware atualizados há pouco (seção de mudanças)",
            "Trocar a versão do Proton/DXVK/VKD3D ou desligar efeitos (vkBasalt, MangoHud) no jogo",
        ]))
    elif f["gpu_fence"] and crash:
        out.append(_h("gpu_soft", "GPU respondendo com atraso (fence fallback) pouco antes da queda", 40,
                      [f"{len(f['gpu_fence'])} aviso(s) de fence fallback nos 5 min antes"],
                      ["Esses avisos são comuns na BC-250; só pesam se aparecerem em rajada perto da queda"]))

    hot = [(k, m) for k, m in f["temps"].items() if (m.get("max") or 0) >= 90]
    if f["thermal"] or hot:
        evidence = [f"{m['label']}: pico de {m['max']}{m['unit']} (antes: {m['before']}{m['unit']})" for _k, m in hot]
        evidence += [f"{_hm(e['ts'])}: {e['message'][:140]}" for e in f["thermal"][:3]]
        out.append(_h("thermal", "Superaquecimento / throttling", 80 if f["thermal"] else 60, evidence, [
            "Poeira, pasta térmica e fluxo de ar; curva da ventoinha (aba Ventoinha)",
            "Temperatura ambiente; limite de potência",
        ]))
    elif f["temps"]:
        pass

    mem = f["mem"]
    if f["oom"] or (mem and (mem.get("max") or 0) >= 95):
        evidence = [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["oom"][:3]]
        if mem:
            evidence.append(f"Memória: pico de {mem['max']}% (antes: {mem['before']}%)")
        if f["swap_total"] == 0:
            evidence.append("O sistema está sem swap: quando a RAM acaba, algo é encerrado na hora")
        out.append(_h("memory", "Falta de memória", 92 if f["oom"] else 62, evidence, [
            "Processos que mais usavam memória no momento (contexto)",
            "Criar swap/zram; fechar navegador/apps pesados durante o jogo",
            "Na BC-250 a VRAM sai da mesma memória: ver o tamanho reservado no BIOS",
        ]))

    elif mem and (mem.get("max") or 0) >= 88 and f["swap_total"] == 0:
        out.append(_h("memory_low", "Pouca folga de memória e sem swap", 40, [
            f"Memória: pico de {mem['max']}% e o sistema não tem swap"], [
            "Qualquer pico (navegador, shader cache, jogo carregando) pode encerrar um processo",
            "Criar swap/zram resolve a maior parte dos casos",
        ]))

    gtt = f["gtt"]
    if crash and gtt and (gtt.get("max") or 0) >= 95:
        out.append(_h("gpu_mem", "Memória da GPU (GTT) no limite", 55, [f"GTT: pico de {gtt['max']}%"],
                      ["Reduzir texturas/resolução; ver se há vazamento (uso subindo até cair)"]))

    if crash:
        recent_files = [x for x in f["files"] if 0 <= t - x["ts"] <= 300]
        panel = [a for a in f["panel"] if 0 <= t - a["ts"] <= 300 and re.search(r"mangohud|vkbasalt|goverlay", a["text"], re.I)]
        if recent_files or panel:
            near = min([t - x["ts"] for x in recent_files] + [t - a["ts"] for a in panel])
            ev = [f"{_hm(x['ts'])} ({rel(x['ts'])}): {x['path']} alterado" for x in recent_files]
            ev += [f"{_hm(a['ts'])} ({rel(a['ts'])}): pelo painel — {a['text'][:120]}" for a in panel[:4]]
            out.append(_h("overlay", "Mudança na configuração do overlay (MangoHud/vkBasalt) com o jogo aberto",
                          62 if near <= 60 else 42, ev, [
                              "O MangoHud recarrega o arquivo dentro do jogo; alguns jogos caem na recarga",
                              "Editar a configuração com o jogo fechado, ou testar o jogo sem o MangoHud",
                              "O GOverlay reescreve o arquivo inteiro ao abrir/salvar",
                          ]))
        if f["mod_launcher"]:
            launcher = (f["subject"] or {}).get("launcher")
            out.append(_h("mod", "Mod ou launcher de terceiros", 55, [
                f"Aberto pelo atalho \"{f['subject'].get('shortcut')}\" → {launcher}" if launcher else
                f"Executado por: {(crash['data'].get('cmdline') or crash['data'].get('exe') or '')[:200]}"], [
                "Versão do mod compatível com a versão do jogo (atualizações do jogo quebram mods)",
                "Testar o jogo sem o mod; ver o log do próprio mod",
            ]))
        sig = f["signal"] or "?"
        others = [h for h in out if h["score"] >= 60]
        base = 58 if not others else 30
        ev = [f"Sinal {sig}: " + {"SIGSEGV": "acesso inválido à memória", "SIGABRT": "o próprio programa abortou",
                                   "SIGBUS": "erro de barramento/arquivo mapeado", "SIGILL": "instrução inválida",
                                   "SIGFPE": "erro aritmético", "SIGTRAP": "armadilha de depuração/assert"}.get(sig, "encerrado pelo sistema")]
        if f["is_game"]:
            ev.append("Programa Windows rodando no Proton/Wine" + (f" ({f['proton']})" if f["proton"] else ""))
        if f["subject"] and f["subject"].get("runtime"):
            ev.append(f"Rodou por {_dur(f['subject']['runtime'])} antes de cair")
        if not f["near_gpu"] and not hot and not f["oom"]:
            ev.append("Nenhum erro de GPU, temperatura ou memória perto da queda")
        out.append(_h("app", "Falha do próprio programa (bug, versão do Proton ou dos arquivos do jogo)", base, ev, [
            "Verificar a integridade dos arquivos (Steam)",
            "Trocar a versão do Proton (Experimental/GE/CachyOS)",
            "Ver se cai sempre no mesmo ponto (repetições abaixo)",
        ]))

    if f["storage"]:
        out.append(_h("storage", "Problema no disco ou no sistema de arquivos", 85,
                      [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["storage"][:4]],
                      ["SMART do disco (smartctl -a)", "Cabo/slot do disco; espaço livre", "Rodar fsck/btrfs scrub"]))
    if f["mce"]:
        out.append(_h("mce", "Erro de hardware da CPU ou da memória", 85, [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["mce"][:3]],
                      ["Overclock/undervolt de CPU ou RAM", "Teste de memória (memtest86+)"]))
    if f["aer"]:
        out.append(_h("pcie", "Erros no barramento PCIe", 45, [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["aer"][:3]],
                      ["Placa bem encaixada; riser/cabos", "Atualização do BIOS"]))
    if f["kernel_bug"]:
        out.append(_h("kernel", "Falha no kernel (driver)", 78, [f"{_hm(e['ts'])}: {e['message'][:160]}" for e in f["kernel_bug"][:3]],
                      ["Kernel atualizado há pouco? Testar o kernel anterior/LTS", "Módulo citado no rastreio"]))

    if f["kind"] == "unexpected_shutdown":
        tail = f.get("tail") or []
        errs = [m for m in tail[-40:] if m["prio"] <= 3]
        gpu_tail = [m for m in tail[-60:] if re.search(r"amdgpu|ring .* timeout|GPU reset|Xid", m["msg"], re.I)]
        if gpu_tail:
            out.append(_h("shutdown_gpu", "Travamento total causado pela GPU", 70, [f"{_hm(m['ts'])}: {m['msg'][:150]}" for m in gpu_tail[-3:]],
                          ["Mesmos pontos de 'GPU travou'; ver temperatura nas métricas por minuto"]))
        hot_tail = [m for m in f["temps"].values() if (m.get("max") or 0) >= 90]
        if hot_tail:
            out.append(_h("shutdown_thermal", "Desligamento por temperatura", 65,
                          [f"{m['label']}: {m['max']}{m['unit']} no último minuto registrado" for m in hot_tail], ["Ventilação"]))
        out.append(_h("shutdown_power", "Queda de energia, botão de reset ou travamento sem registro",
                      60 if not errs and not gpu_tail else 35,
                      ["O journal parou sem as mensagens de desligamento"] + [f"Últimos erros: {m['msg'][:120]}" for m in errs[-2:]],
                      ["Houve queda de luz? Fonte/estabilizador", "Travou com a tela congelada antes?"]))

    if f["unit"]:
        res = f["unit_result"] or "?"
        info = (f["subject"] or {}).get("unit_info") or {}
        ev = [f"Resultado: {res}", f"Reinícios: {info.get('NRestarts', '?')}"]
        if info.get("ExecMainStatus"):
            ev.append(f"Código de saída: {info.get('ExecMainStatus')}")
        title, score = {
            "exit-code": ("O serviço terminou com erro (configuração, dependência ou arquivo ausente)", 65),
            "signal": ("O processo do serviço foi encerrado por um sinal", 55),
            "core-dump": ("O processo do serviço travou (core dump)", 70),
            "timeout": ("O serviço demorou demais para iniciar/parar", 65),
            "start-limit-hit": ("O serviço reiniciou demais e o systemd desistiu", 75),
            "oom-kill": ("O serviço foi encerrado por falta de memória", 85),
            "watchdog": ("O serviço parou de responder (watchdog)", 70),
            "resources": ("Faltou recurso para iniciar o serviço (arquivo, permissão, porta)", 60),
        }.get(res, ("O serviço falhou", 50))
        out.append(_h("unit", title, score, ev, ["Logs do serviço (abaixo)", "systemctl status " + ((f["subject"] or {}).get("name") or "")]))

    if f["packages"] and (crash or f["near_gpu"] or f["kind"] in ("kernel_bug", "unexpected_shutdown")):
        names = sorted({u["package"] for u in f["packages"]})
        newest = max(u["ts"] for u in f["packages"])
        out.append(_h("update", "Mudança recente de software (kernel, Mesa, firmware, Proton...)", 35 if t - newest > 86400 else 45,
                      [f"Atualizados nos 3 dias antes: {', '.join(names[:12])}", f"Mais recente: {_when(newest)}"],
                      ["Se o problema começou depois da atualização, testar a versão anterior (downgrade/snapshot)"]))
    if f["hardware"]:
        out.append(_h("hwchange", "Mudança de hardware/BIOS/driver na última semana", 35,
                      [", ".join(c["path"] for c in (h.get("changes") or [])[:6]) for h in f["hardware"][:3]],
                      ["Comparar com o histórico de hardware"]))
    if not f["metrics_available"] and f["kind"] != "manual":
        for h in out:
            h["evidence"].append("(sem métricas daquele momento: o painel não estava gravando)")
            break
    out.sort(key=lambda h: h["score"], reverse=True)
    return out


# ---------- texto para IA ----------
def ai_text(title: str, r: dict) -> str:
    s = r.get("system") or {}
    lines = [f"# Incidente: {title}", "",
             f"- Quando: {_when(r['trigger_ts'])} (janela analisada: {_hm(r['window'][0])} a {_hm(r['window'][1])})",
             f"- Máquina: {s.get('hostname')} · {s.get('os_name') or 'Linux'} · kernel {s.get('kernel')}",
             f"- Placa: {((s.get('board') or {}).get('vendor') or '')} {((s.get('board') or {}).get('model') or '')}"
             f" (BIOS {((s.get('board') or {}).get('bios_version') or '?')}) · CPU {s.get('cpu')} · GPU {', '.join(filter(None, s.get('gpus') or []))}",
             ]
    pk = s.get("packages") or {}
    if pk:
        lines.append("- Versões: " + ", ".join(f"{k} {v}" for k, v in list(pk.items())[:10]))
    sub = r.get("subject") or {}
    if sub.get("type") == "process":
        lines += ["", "## Programa envolvido",
                  f"- {sub.get('game') or sub.get('name')} — executável `{sub.get('exe')}` (PID {sub.get('pid')}), sinal {sub.get('signal')}"]
        if sub.get("cmdline"):
            lines.append(f"- Linha de comando: `{sub['cmdline'][:400]}`")
        if sub.get("runtime"):
            lines.append(f"- Tempo rodando antes de cair: {_dur(sub['runtime'])}")
        if sub.get("stack"):
            lines += ["- Pilha (core dump):", "```", *sub["stack"][:25], "```"]
    elif sub.get("type") == "unit":
        lines += ["", "## Serviço envolvido", f"- {sub.get('name')} — resultado: {sub.get('result')}"]
        if sub.get("logs"):
            lines += ["```", *[f"{_hm(m['ts'])} {m['msg'][:200]}" for m in sub["logs"][-25:]], "```"]
    if r.get("repeats"):
        lines.append(f"- Aconteceu de novo {len(r['repeats'])} vez(es) em seguida")
    rec = r.get("recurrence") or {}
    if rec.get("last_7d", 0) > 1:
        lines.append(f"- Incidentes iguais nos últimos 7 dias: {rec['last_7d']}")
    lines += ["", "## Linha do tempo"]
    for e in r.get("timeline", [])[-60:]:
        mark = " **[gatilho]**" if e.get("trigger") else ""
        cnt = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
        lines.append(f"- {_hm(e['ts'])} [{e['source']}{'/' + e['who'] if e.get('who') else ''}] {e['text'][:220]}{cnt}{mark}")
    m = r.get("metrics") or {}
    if m.get("summary"):
        lines += ["", f"## Métricas ({m.get('source')})", "| Métrica | Antes | No momento | Pico | Mínimo |", "|---|---|---|---|---|"]
        for x in m["summary"]:
            u = x["unit"]
            lines.append(f"| {x['label']} | {_fmt(x['before'], u)} | {_fmt(x['at'], u)} | {_fmt(x['max'], u)} | {_fmt(x['min'], u)} |")
    c = r.get("context") or {}
    if c:
        lines += ["", "## Processos no momento (mais CPU)"]
        for p in c.get("top_cpu", [])[:6]:
            lines.append(f"- {p.get('name')} (PID {p.get('pid')}): CPU {p.get('cpu')}%, memória {_bytes(p.get('mem'))}")
        if c.get("game"):
            lines.append(f"- Jogo aberto: {c['game'].get('name')} · FPS {c['game'].get('fps')}")
    ch = r.get("changes") or {}
    if ch.get("packages"):
        lines += ["", "## Pacotes atualizados nos 3 dias antes"]
        lines += [f"- {_when(u['ts'])}: {u['action']} {u['package']} {u['version']}" for u in ch["packages"][-15:]]
    lines += ["", "## Hipóteses do painel (regras, sem IA)"]
    for h in r.get("hypotheses", []):
        lines.append(f"- **{h['title']}** — confiança {h['level']} ({h['score']}/100)")
        lines += [f"  - evidência: {e}" for e in h["evidence"][:5]]
    if r.get("logs"):
        lines += ["", "## Trecho do log do sistema (avisos e erros + kernel)", "```", *r["logs"][-80:], "```"]
    if r.get("note"):
        lines += ["", f"Observação do usuário: {r['note']}"]
    lines += ["", "## Pergunta",
              "Com base nesses dados, qual a causa mais provável? As hipóteses acima fazem sentido? "
              "O que devo verificar ou testar, em ordem, para confirmar e resolver?"]
    return "\n".join(lines)


# ---------- auxiliares ----------
def _proc_name(d: dict) -> str:
    """No Wine/Proton o executável é sempre o wine64-preloader: o nome do processo (eldenring.exe) diz mais."""
    exe = os.path.basename(d.get("exe") or "")
    if not exe or re.match(r"(wine|proton|python|java|mono|electron)", exe, re.I):
        return d.get("comm") or exe or "?"
    return exe


def _collapse(raw: list[dict]) -> list[str]:
    """Linhas de log; repetições seguidas viram uma com (xN)."""
    out: list[str] = []
    prev, n = None, 0
    for m in raw:
        if NOISE.search(m["msg"] or ""):
            continue
        key = (m["ident"], m["msg"])
        if key == prev:
            n += 1
            continue
        if prev is not None and n > 1:
            out[-1] += f" (x{n})"
        prev, n = key, 1
        first = m["msg"].splitlines()[0] if m["msg"] else ""
        out.append(f"{_hm(m['ts'])} {m['ident']}: {first}"[:300])
    if n > 1 and out:
        out[-1] += f" (x{n})"
    return out


def _fmt(v, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "B/s":
        return _bytes(v) + "/s"
    return f"{v}{(' ' + unit) if unit and unit not in ('%', '°C') else unit}"


def _bytes(n) -> str:
    if n is None:
        return "?"
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


def _dur(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {(s % 3600) // 60} min"


def _downsample(pts: list, n: int) -> list:
    if len(pts) <= n:
        return pts
    step = len(pts) / n
    return [pts[int(i * step)] for i in range(n)] + [pts[-1]]


def _stack(crash: dict) -> list[str]:
    """O core dump grava a pilha no próprio MESSAGE do journal ("Stack trace of thread ...")."""
    cur = (crash.get("data") or {}).get("cursor")
    if not cur:
        return []
    try:
        e = journal.entry(cur)
    except ValueError:
        return []
    msg = (e or {}).get("fields", {}).get("MESSAGE") or ""
    lines = [ln.strip() for ln in str(msg).splitlines()]
    try:
        i = next(i for i, ln in enumerate(lines) if ln.startswith("Stack trace of thread"))
    except StopIteration:
        return []
    out = []
    for ln in lines[i:i + 40]:
        if not ln and out:
            break
        out.append(ln)
    return out


def _process_extra(ctx, crash: dict) -> dict:
    """Jogo (Steam), tempo rodando (rastreador de processos)."""
    out: dict = {}
    d = crash.get("data") or {}
    unit = str(d.get("core_unit") or crash.get("unit") or "")
    m = re.search(r"app-steam-app(\d+)", unit)
    if m:
        from . import game
        appid = m.group(1)
        name = game.steam_name(appid)
        if not name and int(appid) >= 2 ** 31:
            sc = game.steam_shortcut(appid) or {}
            name = sc.get("game") or sc.get("name")
            if sc.get("exe"):
                # jogo aberto por um atalho não-Steam: normalmente um launcher de mod (ex.: Seamless Coop)
                out["launcher"] = os.path.basename(sc["exe"])
                out["shortcut"] = sc.get("name")
        out["game"] = name
        out["appid"] = appid
    pt = getattr(ctx, "proctrack", None)
    if pt and crash.get("pid"):
        try:
            for r in pt.list_exited(limit=2000).get("exited", []):
                if r.get("pid") == crash["pid"] and r.get("start"):
                    out["runtime"] = max(0, crash["ts"] - r["start"])
                    break
        except Exception:  # noqa: BLE001
            pass
    return out


def _is_user_unit(ev: dict) -> bool:
    return bool(ev.get("data") and ev["data"].get("user")) or (ev.get("ident") == "systemd" and (ev.get("pid") or 0) != 1)


def _boot_time() -> float:
    try:
        with open("/proc/stat") as fh:
            for line in fh:
                if line.startswith("btime"):
                    return float(line.split()[1])
    except OSError:
        pass
    return time.time()


def _home():
    from .desktop import HOME
    return HOME
