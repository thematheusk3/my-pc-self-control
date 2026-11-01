"""Roteamento mínimo. Para adicionar endpoints, crie um módulo em sm/api/
usando @route e importe-o em sm/api/routes.py (ou só adicione lá)."""
from __future__ import annotations

import re
from urllib.parse import unquote
from dataclasses import dataclass
from typing import Callable

ROUTES: list[tuple[str, re.Pattern, Callable]] = []


@dataclass
class Response:
    body: object = None
    status: int = 200
    headers: dict | None = None


def route(method: str, pattern: str):
    rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method.upper(), rx, fn))
        return fn
    return deco


def match(method: str, path: str):
    allowed = False
    for m, rx, fn in ROUTES:
        mt = rx.match(path)
        if mt:
            if m == method:   # parâmetros vêm com %XX da URL (ex.: "ELDEN%20RING.desktop")
                return fn, {k: unquote(v) for k, v in mt.groupdict().items()}
            allowed = True
    return (None, "method") if allowed else (None, None)
