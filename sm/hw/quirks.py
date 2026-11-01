"""Correções específicas de hardware.

Cada quirk é uma função (chip_name, pci_ids, kind, label, value) -> bool
que retorna False para descartar a leitura. Adicione novas placas aqui.
"""
from __future__ import annotations

BC250 = ("0x1002", "0x13fe")


def bc250_amdgpu(chip, ids, kind, label, value) -> bool:
    # O SMU da BC-250 reporta clock, tensões e potência corrompidos; só temperatura
    # (quando > 0) é aproveitável — e normalmente ela vem 0 e já é filtrada.
    if chip == "amdgpu" and ids == BC250:
        return kind == "temp"
    return True


def amdgpu_sane_voltage(chip, ids, kind, label, value) -> bool:
    # vddgfx/vddnb acima de 2 V em GPU AMD é leitura inválida
    return not (chip == "amdgpu" and kind == "in" and value > 2.0)


QUIRKS = [bc250_amdgpu, amdgpu_sane_voltage]


def keep(chip: str, ids: tuple[str, str], kind: str, label: str, value: float) -> bool:
    return all(q(chip, ids, kind, label, value) for q in QUIRKS)
