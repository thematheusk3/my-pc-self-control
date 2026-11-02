"""Downloads/atualizações da Steam em andamento.

A Steam não tem API local de progresso. Juntamos duas fontes que ela mesma grava:
- `logs/content_log.txt`: estado de cada AppID ("Update Running", "Update Queued", "Downloading",
  "Staging"...) e o total da atualização ("update started : download 0/N, ..., stage 0/M");
- `steamapps/appmanifest_<id>.acf`: BytesDownloaded/BytesToDownload e BytesStaged/BytesToStage,
  atualizados pela Steam durante o download (em saltos, não byte a byte).

A velocidade vem do monitor de rede (tráfego do processo `steam`) ou, sem ele, da variação
de BytesDownloaded. Só lê arquivos pequenos e só o trecho novo do log.
"""
from __future__ import annotations

import re
import time
from collections import deque
from pathlib import Path

from .desktop import STEAM_ROOTS
from .game import _libraries, steam_name

LOG_TAIL = 512 * 1024        # na primeira leitura, só o fim do log
RX_STATE = re.compile(r"AppID (\d+) state changed : (.*)$")
RX_PHASE = re.compile(r"AppID (\d+) (?:\w+ )?update changed : (.*)$")
RX_START = re.compile(r"AppID (\d+) update started : download (\d+)/(\d+).*?stage (\d+)/(\d+)")
RX_DONE = re.compile(r"AppID (\d+) (?:scheduler finished|update canceled|finished update)")
ACF_KEYS = ("name", "StateFlags", "BytesToDownload", "BytesDownloaded", "BytesToStage", "BytesStaged")
PHASES = {"Downloading": "baixando", "Staging": "instalando", "Committing": "finalizando",
          "Verifying": "verificando", "Validating": "verificando", "Preallocating": "reservando espaço",
          "Reconfiguring": "preparando"}


def parse_acf(text: str) -> dict:
    out = {}
    for k in ACF_KEYS:
        m = re.search(rf'"{k}"\s+"([^"]*)"', text)
        if m:
            out[k] = m.group(1) if k == "name" else int(m.group(1) or 0)
    return out


def parse_log_line(line: str, apps: dict[str, dict]) -> None:
    """Atualiza `apps` (appid -> estado) com uma linha do content_log."""
    m = RX_STATE.search(line)
    if m:
        a = apps.setdefault(m.group(1), {})
        flags = {f.strip() for f in m.group(2).split(",") if f.strip()}
        a["flags"] = flags
        if "Update Running" not in flags:
            a.pop("phase", None)
        return
    m = RX_START.search(line)
    if m:
        a = apps.setdefault(m.group(1), {})
        a["dl_total"], a["stage_total"] = int(m.group(3)), int(m.group(5))
        return
    m = RX_PHASE.search(line)
    if m:
        a = apps.setdefault(m.group(1), {})
        parts = [p.strip() for p in m.group(2).split(",") if p.strip()]
        a["phase"] = next((PHASES[p] for p in parts if p in PHASES), None)
        return
    m = RX_DONE.search(line)
    if m and m.group(1) in apps:
        apps[m.group(1)].pop("phase", None)


def status_of(flags: set[str]) -> str | None:
    """Estados do content_log -> running | paused | queued | None (nada a mostrar).
    Ex.: "Update Required,Fully Installed,Update Queued,Update Running,Update Started,"."""
    if "Update Running" in flags:
        return "running"
    if any("Suspended" in f or "Paused" in f for f in flags) or "Update Started" in flags:
        return "paused"
    if "Update Queued" in flags and "Update Required" in flags and not any("delayed" in f for f in flags):
        return "queued"
    return None


class SteamDownloads:
    def __init__(self) -> None:
        self.log: Path | None = None
        self.pos = 0
        self.apps: dict[str, dict] = {}
        self.samples: dict[str, deque] = {}     # appid -> (t, bytes baixados)

    def _root(self) -> Path | None:
        roots = [r for r in STEAM_ROOTS if (r / "logs" / "content_log.txt").is_file()]
        return max(roots, key=lambda r: (r / "logs" / "content_log.txt").stat().st_mtime) if roots else None

    def _read_log(self, root: Path) -> None:
        f = root / "logs" / "content_log.txt"
        try:
            size = f.stat().st_size
            if f != self.log or size < self.pos:      # outro arquivo ou log rotacionado
                self.log, self.pos, self.apps = f, max(0, size - LOG_TAIL), {}
            if size == self.pos:
                return
            with open(f, "rb") as fh:
                fh.seek(self.pos)
                chunk = fh.read(size - self.pos).decode(errors="replace")
        except OSError:
            return
        cut = chunk.rfind("\n")
        if cut < 0:
            return
        self.pos += len(chunk[:cut + 1].encode())
        for line in chunk[:cut].splitlines():
            parse_log_line(line, self.apps)

    def _acf(self, root: Path, appid: str) -> dict:
        for lib in _libraries(root):
            try:
                return parse_acf((lib / "steamapps" / f"appmanifest_{appid}.acf").read_text(errors="replace"))
            except OSError:
                continue
        return {}

    def collect(self, steam_running: bool, rate_fn=None) -> dict:
        """rate_fn(): bytes/s baixados pelo processo da Steam (monitor de rede), ou None."""
        root = self._root()
        if root is None:
            return {"available": False, "running": False, "downloads": []}
        self._read_log(root)
        if not steam_running:
            self.samples.clear()
            return {"available": True, "running": False, "downloads": []}
        now = time.time()
        out = []
        steam_rate = ...
        for appid, a in self.apps.items():
            st = status_of(a.get("flags", set()))
            if not st or appid == "0":
                continue
            acf = self._acf(root, appid)
            total = acf.get("BytesToDownload") or a.get("dl_total") or 0
            done = min(acf.get("BytesDownloaded") or 0, total) if total else 0
            s_total, s_done = acf.get("BytesToStage") or a.get("stage_total") or 0, acf.get("BytesStaged") or 0
            hist = self.samples.setdefault(appid, deque(maxlen=60))
            if not hist or hist[-1][1] != done:
                hist.append((now, done))
            while hist and now - hist[0][0] > 60 and len(hist) > 2:
                hist.popleft()
            acf_rate = None
            if len(hist) >= 2 and hist[-1][0] > hist[0][0]:
                acf_rate = max(0.0, (hist[-1][1] - hist[0][1]) / (now - hist[0][0]))
            downloading = st == "running" and a.get("phase") in (None, "baixando") and (not total or done < total)
            rate = None
            if downloading:
                if steam_rate is ...:
                    steam_rate = rate_fn() if rate_fn else None
                rate = steam_rate or acf_rate
            left = total - done if total else None
            out.append({
                "appid": appid,
                "name": acf.get("name") or steam_name(appid) or f"App {appid}",
                "status": st, "phase": a.get("phase") if st == "running" else None,
                "total": total or None, "done": done if total else None,
                "pct": round(done / total * 100, 1) if total else None,
                "stage_total": s_total or None, "stage_done": s_done if s_total else None,
                "rate": round(rate) if rate else None,
                "eta": round(left / rate) if rate and left else None,
            })
        for gone in set(self.samples) - {d["appid"] for d in out}:
            del self.samples[gone]
        order = {"running": 0, "paused": 1, "queued": 2}
        out.sort(key=lambda d: (order[d["status"]], d["name"].lower()))
        return {"available": True, "running": True, "downloads": out}
