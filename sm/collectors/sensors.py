from __future__ import annotations

import os
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


# modelo da placa (board_name) -> (módulo, explicação); vem antes do fabricante
BOARD_MODELS = [
    ("bc-250", "nct6687", "A BC-250 usa o chip Nuvoton NCT6686D, atendido pelo driver <code>nct6687</code> (nct6687d: já vem "
                          "nos kernels bc250 do CachyOS ou no pacote <code>nct6687d-dkms-git</code>)"),
]
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


def module_available(name: str) -> bool:
    """O módulo existe para o kernel em uso (instalado, mesmo que não carregado)?"""
    import subprocess
    try:
        return subprocess.run(["modinfo", "-n", name], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def module_loaded(name: str) -> bool:
    return os.path.isdir(f"/sys/module/{name}")


@cache
def fan_hint() -> str | None:
    """Sem nenhuma ventoinha nos sensores: o driver do chip da placa-mãe provavelmente não está carregado."""
    vendor = (read("/sys/class/dmi/id/board_vendor") or read("/sys/class/dmi/id/sys_vendor") or "").strip()
    board = (read("/sys/class/dmi/id/board_name") or "").strip()
    where = " ".join(x for x in (vendor, board) if x) or "da placa-mãe"
    model = next(((m, t) for key, m, t in BOARD_MODELS if key in board.lower()), None)
    if model:
        mod, why = model
        if module_loaded(mod):
            state = f"O módulo <code>{mod}</code> está carregado mas não mostrou ventoinhas; veja <code>sudo dmesg | grep {mod}</code>."
            return f"Nenhuma ventoinha encontrada na {where}. {why}. {state}"
        if not module_available(mod):
            return (f"Nenhuma ventoinha encontrada na {where}. {why}. O módulo não existe para o kernel em uso "
                    f"({os.uname().release}): instale <code>nct6687d-dkms-git</code> (ou os headers do kernel, se já instalado).")
        return (f"Nenhuma ventoinha encontrada: o driver do chip de sensores da {where} não está carregado. {why}. "
                f"Ele já está instalado para este kernel; carregue agora com <code>sudo modprobe {mod}</code> e, para carregar "
                f"sempre ao ligar, <code>echo {mod} | sudo tee /etc/modules-load.d/{mod}.conf</code>. Depois reinicie o painel: "
                f"<code>sudo systemctl restart self-monitoring</code>.")
    tip = next((t for key, t in BOARD_DRIVERS if key in vendor.lower()), "rode <code>sudo sensors-detect</code> "
               "(pacote lm_sensors) e carregue o módulo que ele indicar")
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
