"""Leitura genérica de /sys/class/hwmon, com filtro de valores absurdos.

Algumas placas (ex.: BC-250) expõem sensores que retornam 0 ou lixo
(temperatura 0 °C, potência de megawatts). Esses valores são descartados
aqui para que o restante do código não precise se preocupar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import quirks
from .util import read, read_int

HWMON = Path("/sys/class/hwmon")

# tipo -> (divisor, unidade, faixa plausível)
KINDS = {
    "temp": (1000, "°C", (1, 150)),
    "fan": (1, "RPM", (1, 20000)),
    "in": (1000, "V", (0.05, 60)),
    "power": (1_000_000, "W", (0.05, 1500)),
    "curr": (1000, "A", (0.01, 300)),
    "freq": (1_000_000, "MHz", (1, 10000)),
}
_RE = re.compile(r"^(temp|fan|in|power|curr|freq)(\d+)_(input|average)$")


@dataclass
class Reading:
    chip: str        # nome do driver (k10temp, amdgpu, nvme...)
    chip_id: str     # identificador único (hwmon3)
    device: str      # dispositivo PCI/plataforma, se houver
    kind: str
    label: str
    value: float
    unit: str
    crit: float | None = None

    def as_dict(self) -> dict:
        return {
            "chip": self.chip, "chip_id": self.chip_id, "device": self.device,
            "kind": self.kind, "label": self.label, "value": round(self.value, 2),
            "unit": self.unit, "crit": self.crit,
        }


def chips() -> list[Path]:
    try:
        return sorted(HWMON.iterdir(), key=lambda p: int(p.name[5:]) if p.name[5:].isdigit() else 0)
    except OSError:
        return []


def chip_device(chip: Path) -> str:
    try:
        return (chip / "device").resolve().name
    except OSError:
        return ""


def read_chip(chip: Path) -> list[Reading]:
    name = read(chip / "name", chip.name) or chip.name
    dev = chip_device(chip)
    ids = (read(chip / "device" / "vendor", "") or "", read(chip / "device" / "device", "") or "")
    out: dict[tuple[str, str], Reading] = {}
    try:
        files = sorted(f.name for f in chip.iterdir())
    except OSError:
        return []
    for fname in files:
        m = _RE.match(fname)
        if not m:
            continue
        kind, idx, _suffix = m.groups()
        key = (kind, idx)
        if key in out:          # prefere *_input; *_average só se faltar
            continue
        raw = read_int(chip / fname)
        if raw is None:
            continue
        div, unit, (lo, hi) = KINDS[kind]
        val = raw / div
        if not (lo <= abs(val) <= hi):
            continue
        label = read(chip / f"{kind}{idx}_label") or f"{kind}{idx}"
        if not quirks.keep(name, ids, kind, label, val):
            continue
        crit = None
        if kind == "temp":
            c = read_int(chip / f"temp{idx}_crit")
            if c and 20_000 < c < 150_000:
                crit = c / 1000
        out[key] = Reading(name, chip.name, dev, kind, label, val, unit, crit)
    return list(out.values())


def all_readings() -> list[Reading]:
    res: list[Reading] = []
    for c in chips():
        res.extend(read_chip(c))
    return res
