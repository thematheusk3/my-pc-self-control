from __future__ import annotations

from . import Collector, register
from ..hw import hwmon

FRIENDLY = {
    "k10temp": "CPU (AMD)", "zenpower": "CPU (AMD)", "coretemp": "CPU (Intel)",
    "amdgpu": "GPU (AMD)", "nvme": "SSD NVMe", "drivetemp": "Disco",
    "acpitz": "ACPI", "hidpp_battery_0": "Periférico Logitech", "iwlwifi_1": "Wi-Fi", "mt7921_phy0": "Wi-Fi",
}


BOARD_PREFIXES = ("nct", "it8", "f71", "w83", "asus", "gigabyte", "dell", "thinkpad")


def friendly(chip: str) -> str:
    if chip in FRIENDLY:
        return FRIENDLY[chip]
    if chip.startswith("hidpp"):
        return "Periférico"
    if chip.startswith(BOARD_PREFIXES):
        return f"Placa-mãe ({chip})"
    return chip


@register
class Sensors(Collector):
    name = "sensors"
    order = 40

    def collect(self) -> dict:
        chips: dict[str, dict] = {}
        for r in hwmon.all_readings():
            c = chips.setdefault(r.chip_id, {
                "id": r.chip_id, "driver": r.chip,
                "name": friendly(r.chip),
                "device": r.device, "readings": [],
            })
            c["readings"].append(r.as_dict())
        fans = [{"chip": c["name"], "label": r["label"], "rpm": r["value"]}
                for c in chips.values() for r in c["readings"] if r["kind"] == "fan"]
        return {"chips": list(chips.values()), "fans": fans}
