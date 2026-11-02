from __future__ import annotations

from functools import cache

from . import Collector, register
from ..hw import hwmon
from ..hw.util import read

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


# fabricante da placa-mãe -> driver do chip de sensores (Super I/O) que costuma faltar
BOARD_DRIVERS = [
    ("micro-star", "MSI usa o chip Nuvoton NCT6687D: <code>sudo modprobe nct6683 force=1</code> (só leitura) "
                   "ou o driver <code>nct6687d-dkms-git</code> do AUR (leitura e controle)"),
    ("gigabyte", "Gigabyte usa chip ITE: instale <code>it87-dkms-git</code> (AUR) e rode "
                 "<code>sudo modprobe it87 ignore_resource_conflict=1</code>"),
    ("asus", "ASUS: <code>sudo modprobe nct6775</code>; nas placas novas também <code>asus_ec_sensors</code>"),
    ("asrock", "ASRock: <code>sudo modprobe nct6775</code>"),
    ("dell", "Dell: <code>sudo modprobe dell_smm_hwmon</code>"),
]


@cache
def fan_hint() -> str | None:
    """Sem nenhuma ventoinha nos sensores: o driver do chip da placa-mãe provavelmente não está carregado."""
    vendor = (read("/sys/class/dmi/id/board_vendor") or read("/sys/class/dmi/id/sys_vendor") or "").strip()
    board = (read("/sys/class/dmi/id/board_name") or "").strip()
    tip = next((t for key, t in BOARD_DRIVERS if key in vendor.lower()), "rode <code>sudo sensors-detect</code> "
               "(pacote lm_sensors) e carregue o módulo que ele indicar")
    where = " ".join(x for x in (vendor, board) if x) or "da placa-mãe"
    return (f"Nenhuma ventoinha encontrada: o driver do chip de sensores da placa ({where}) não está carregado. "
            f"{tip}. Para carregar sempre ao ligar, crie <code>/etc/modules-load.d/sensores.conf</code> com o nome "
            f"do módulo. Depois reinicie o painel: <code>sudo systemctl restart self-monitoring</code>.")


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
        return {"chips": list(chips.values()), "fans": fans, "fan_hint": None if fans else fan_hint()}
