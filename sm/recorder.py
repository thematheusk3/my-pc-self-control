"""Grava no banco o resumo das métricas: por minuto (mín/méd/máx de cada série) e por hora.

As séries vêm do histórico em memória (ctx.history, 1 amostra/s). Uma vez por dia apaga o que passou
da retenção ([history] days).
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)
SKIP_PREFIXES = ("meu.",)


class MetricsRecorder:
    def __init__(self, ctx, store, days: int = 30) -> None:
        self.ctx = ctx
        self.store = store
        self.days = max(1, int(days))
        self._stop = threading.Event()
        self.last_hour = int(time.time() // 3600 * 3600)
        self.last_prune = 0.0

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="recorder").start()

    def stop(self) -> None:
        self._stop.set()

    def minute(self, minute_ts: int) -> int:
        data = self.ctx.history.get(None, since=minute_ts - 0.01)
        rows = {}
        for k, pts in data.items():
            if k.startswith(SKIP_PREFIXES):
                continue
            vals = [v for ts, v in pts if minute_ts <= ts < minute_ts + 60]
            if vals:
                rows[k] = (sum(vals) / len(vals), min(vals), max(vals), len(vals))
        self.store.add_metrics(60, minute_ts, rows)
        return len(rows)

    def _loop(self) -> None:
        # alinha no começo do próximo minuto
        self._stop.wait(60 - time.time() % 60 + 1)
        while not self._stop.is_set():
            now = time.time()
            minute_ts = int(now // 60 * 60) - 60      # o minuto que acabou de fechar
            try:
                self.minute(minute_ts)
                hour = int(now // 3600 * 3600)
                if hour > self.last_hour:
                    self.store.rollup_hour(self.last_hour)
                    self.last_hour = hour
                if now - self.last_prune > 86400:
                    self.store.prune(self.days)
                    self.last_prune = now
            except Exception:  # noqa: BLE001
                log.exception("falha ao gravar métricas")
            self._stop.wait(60 - time.time() % 60 + 1)
