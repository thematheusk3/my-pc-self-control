from __future__ import annotations

from pathlib import Path


def read(path: str | Path, default: str | None = None) -> str | None:
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return default


def read_int(path: str | Path, default: int | None = None) -> int | None:
    v = read(path)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default
