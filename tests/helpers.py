"""Utilitários de teste: sysfs falso para hwmon e contexto mínimo."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sm import config  # noqa: E402


def fake_hwmon(chips: dict[str, dict[str, str]]) -> Path:
    """chips = {"hwmon4": {"name": "nct6686", "pwm2": "130", ...}} -> diretório temporário."""
    base = Path(tempfile.mkdtemp(prefix="sm-hwmon-"))
    for chip, files in chips.items():
        d = base / chip
        d.mkdir()
        for name, value in files.items():
            (d / name).write_text(f"{value}\n")
    return base


class Ctx:
    def __init__(self, **overrides) -> None:
        self.config = config.load(Path("/nonexistent.toml"))   # só os padrões do código
        for k, v in overrides.items():
            setattr(self.config, k, v)
