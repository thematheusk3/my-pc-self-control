"""Executa os coletores periodicamente e mantém o snapshot mais recente."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict

from . import collectors
from .config import Config
from .history import History
from .hw.drm_clients import DrmClientTracker

log = logging.getLogger(__name__)


class Context:
    """Estado compartilhado entre coletores, API e ações."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.history = History(maxlen=max(10, int(config.history_seconds / config.sample_interval)))
        self.drm = DrmClientTracker()
        self.processes: list[dict] = []
        self.data: dict[str, dict] = {}
        self.ts = 0.0
        self.seq = 0
        self.cond = threading.Condition()
        self.collectors: list[collectors.Collector] = []
        self.fan = None   # FanController, se ativo
        self.proctrack = None   # ProcessTracker, se ativo
        self.netmon = None      # NetMonitor, se ativo
        self.game = None        # GameMonitor (jogo atual / FPS)
        self.alerts = None      # Alerts
        self.updates = None     # Updates
        self.power_timer = None # PowerTimer
        self.media = None       # Media (MPRIS), criado pelo coletor "media"
        self.focus = None       # FocusMonitor, criado pelo coletor "focus"

    def snapshot(self) -> dict:
        return {"ts": self.ts, "seq": self.seq, "series": dict(self.history.latest), "data": self.data}

    def info(self) -> dict:
        out = {}
        for c in self.collectors:
            try:
                i = c.info()
            except Exception:  # noqa: BLE001
                i = None
            if i is not None:
                out[c.name] = i
        return out


class Sampler:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self._stop = threading.Event()

    def start(self) -> None:
        cfg = self.ctx.config
        for cls in collectors.load_all():
            if cls.name in cfg.disabled_collectors:
                continue
            c = cls(self.ctx)
            try:
                c.setup()
            except Exception:
                log.exception("setup do coletor %s falhou; desativado", cls.name)
                continue
            self.ctx.collectors.append(c)
        groups: dict[float, list] = defaultdict(list)
        for c in self.ctx.collectors:
            groups[c.interval or cfg.sample_interval].append(c)
        # primeira coleta síncrona para o snapshot nunca estar vazio
        for c in sorted(self.ctx.collectors, key=lambda c: c.order):
            self._run(c)
        time.sleep(0.3)
        for interval, cs in groups.items():
            cs.sort(key=lambda c: c.order)
            threading.Thread(target=self._loop, args=(interval, cs), daemon=True,
                             name=f"sampler-{interval}").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, c) -> None:
        try:
            self.ctx.data[c.name] = c.collect()
        except Exception as e:  # noqa: BLE001
            log.exception("coletor %s falhou", c.name)
            self.ctx.data[c.name] = {"error": str(e)}

    def _loop(self, interval: float, cs: list) -> None:
        notify = interval == self.ctx.config.sample_interval
        nxt = time.monotonic()
        while not self._stop.is_set():
            for c in cs:
                self._run(c)
            if notify:
                with self.ctx.cond:
                    self.ctx.ts = time.time()
                    self.ctx.seq += 1
                    self.ctx.cond.notify_all()
            nxt += interval
            delay = nxt - time.monotonic()
            if delay < 0:
                nxt = time.monotonic()
                delay = 0
            self._stop.wait(delay)
