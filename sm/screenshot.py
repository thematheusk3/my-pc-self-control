"""Captura de tela pelo painel.

Regra: TODA captura mostra antes um aviso visível na tela do PC (notificação crítica, que aparece
mesmo no modo "não perturbe"). Se o aviso não puder ser exibido, a captura é recusada.
A captura é feita pelo Spectacle (KDE), que tem permissão do KWin para capturar a tela no Wayland.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import ROOT
from .desktop import graphical_session, notify_pc, session_env

log = logging.getLogger(__name__)
DIR = ROOT / "data" / "screenshots"
KEEP = 30            # mantém as últimas N capturas
_NAME = re.compile(r"^shot-\d{8}-\d{6}(?:-\d{1,3})?\.png$")
PNG_END = b"IEND\xaeB`\x82"     # último chunk de um PNG completo


class ShotError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def take(requested_by: str) -> dict:
    if not graphical_session():
        raise ShotError("Ninguém logado na máquina: não há tela para capturar.", 409)
    if not shutil.which("spectacle"):
        raise ShotError("Spectacle não encontrado (necessário para capturar a tela no KDE/Wayland).", 501)
    # 1) aviso visível no PC (obrigatório)
    if not notify_pc("📸 Captura de tela pelo painel",
                     f"O self-monitoring tirou uma captura desta tela agora (pedido de {requested_by}).",
                     urgency="critical", icon="camera-photo", timeout_ms=8000):
        raise ShotError("Não foi possível mostrar o aviso na tela do PC; por segurança a captura não foi feita.", 503)
    # 2) captura
    DIR.mkdir(parents=True, exist_ok=True)
    DIR.chmod(0o700)
    stem = time.strftime("shot-%Y%m%d-%H%M%S")
    name = f"{stem}.png"
    n = 1
    while (DIR / name).exists():      # duas capturas no mesmo segundo
        name = f"{stem}-{n}.png"
        n += 1
    path = DIR / name
    cmd = ["systemd-run", "--user", "--wait", "--collect", "--quiet", "-p", "Type=exec", "--",
           "spectacle", "--background", "--nonotify", "--fullscreen", "--output", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=session_env())
    except (OSError, subprocess.SubprocessError) as e:
        raise ShotError(f"Falha ao capturar: {e}", 500) from None
    if not _wait_complete(path):
        path.unlink(missing_ok=True)
        raise ShotError(f"O Spectacle não gerou a imagem. {r.stderr.strip()[:200]}", 500)
    log.warning("captura de tela feita pelo painel (pedido de %s): %s", requested_by, name)
    _prune()
    return {"ok": True, "name": name, "size": path.stat().st_size}


def png_complete(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return False
            f.seek(-8, 2)
            return f.read(8) == PNG_END
    except OSError:
        return False


def _wait_complete(path: Path, timeout: float = 15.0) -> bool:
    """O Spectacle grava em segundo plano: espera o PNG terminar (chunk IEND no fim).
    Sem isso o painel podia servir um arquivo pela metade (imagem quebrada até dar F5)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if png_complete(path):
            return True
        time.sleep(0.2)
    return False


def _prune() -> None:
    files = sorted(DIR.glob("shot-*.png"))
    for f in files[:-KEEP]:
        f.unlink(missing_ok=True)


def list_shots() -> list[dict]:
    if not DIR.is_dir():
        return []
    files = [f for f in DIR.glob("shot-*.png") if _NAME.match(f.name)]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size": f.stat().st_size, "time": f.stat().st_mtime} for f in files]


def path_of(name: str) -> Path | None:
    if not _NAME.match(name or ""):
        return None
    p = DIR / name
    return p if p.is_file() else None


def delete(name: str) -> bool:
    p = path_of(name)
    if p:
        p.unlink()
        return True
    return False
