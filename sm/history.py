from __future__ import annotations

import threading
import time
from collections import deque


class History:
    """Séries temporais em memória (ring buffer por chave)."""

    def __init__(self, maxlen: int) -> None:
        self.maxlen = maxlen
        self._data: dict[str, deque] = {}
        self.latest: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, key: str, value: float | None) -> None:
        if value is None:
            return
        with self._lock:
            dq = self._data.get(key)
            if dq is None:
                dq = self._data[key] = deque(maxlen=self.maxlen)
            v = round(float(value), 2)
            dq.append((round(time.time(), 2), v))
            self.latest[key] = v

    def get(self, keys: list[str] | None = None, since: float = 0) -> dict[str, list]:
        with self._lock:
            ks = keys or list(self._data)
            return {k: [p for p in self._data.get(k, ()) if p[0] > since] for k in ks}
