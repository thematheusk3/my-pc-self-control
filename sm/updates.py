"""Atualizações pendentes (somente consulta; o painel não instala nada).

Arch: checkupdates (pacman-contrib; usa uma cópia temporária do banco, sem root) + AUR (paru/yay -Qua).
Debian/Ubuntu: apt list --upgradable (depende do último `apt update` feito pelo sistema).
Fedora: dnf check-update. Todos: flatpak remote-ls --updates.
Também informa se o PC precisa reiniciar (kernel novo instalado e o antigo ainda rodando).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .config import ROOT

log = logging.getLogger(__name__)
CACHE = ROOT / "data" / "updates.json"
AUTO_EVERY = 6 * 3600


def _run(cmd: list[str], timeout: float = 180) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def _arrow(lines: str, source: str) -> list[dict]:
    """formato 'nome versão_atual -> versão_nova'"""
    out = []
    for line in lines.splitlines():
        m = re.match(r"^(\S+)\s+(\S+)\s+->\s+(\S+)", line.strip())
        if m:
            out.append({"name": m.group(1), "current": m.group(2), "new": m.group(3), "source": source})
    return out


def check() -> dict:
    items: list[dict] = []
    errors: list[str] = []
    if shutil.which("checkupdates"):
        rc, out = _run(["checkupdates"])
        if rc in (0, 2):          # 2 = nada para atualizar
            items += _arrow(out, "pacman")
        else:
            errors.append("checkupdates falhou (sem internet?)")
        aur = shutil.which("paru") or shutil.which("yay")
        if aur:
            rc, out = _run([aur, "-Qua"])
            items += _arrow(out, "AUR")
    elif shutil.which("apt"):
        rc, out = _run(["apt", "list", "--upgradable"])
        for line in out.splitlines():
            m = re.match(r"^([^/]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]", line)
            if m:
                items.append({"name": m.group(1), "current": m.group(3), "new": m.group(2), "source": "apt"})
    elif shutil.which("dnf"):
        rc, out = _run(["dnf", "-q", "check-update"])
        for line in out.splitlines():
            f = line.split()
            if len(f) == 3 and "." in f[0]:
                items.append({"name": f[0].rsplit(".", 1)[0], "current": None, "new": f[1], "source": "dnf"})
    if shutil.which("flatpak"):
        rc, out = _run(["flatpak", "remote-ls", "--updates", "--columns=application,version"])
        for line in out.splitlines():
            f = line.split("\t")
            if f and f[0] and "." in f[0]:
                items.append({"name": f[0], "current": None, "new": f[1] if len(f) > 1 else None, "source": "flatpak"})
    for i in items:
        i["sensitive"] = sensitive(i["name"])
    kernel = any(i["sensitive"] == "kernel" for i in items)
    return {"items": items, "count": len(items), "checked": time.time(), "errors": errors, "kernel": kernel}


def sensitive(name: str) -> str | None:
    """Pacotes cuja atualização exige reinício ou pode deixar o PC sem vídeo/rede/boot."""
    if re.match(r"^linux(-[\w.]+)*$", name) and not re.search(r"-(headers|firmware|docs|api-headers|tools)$", name):
        return "kernel"
    if name.endswith("-dkms") or "-dkms-" in name or name.endswith("-dkms-git"):
        return "driver"
    if re.match(r"^(mesa|lib32-mesa|vulkan-radeon|nvidia.*|systemd|glibc|grub|limine|mkinitcpio|dracut|"
                r"plasma-workspace|kwin|plasma-login-manager|sddm|pipewire|networkmanager|linux-firmware.*)$", name):
        return "sistema"
    return None


MODULES = Path("/usr/lib/modules")
REBOOT_FLAGS = ("/var/run/reboot-required", "/run/reboot-required")     # Debian/Ubuntu


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def reboot_required(release: str | None = None, modules: Path = MODULES) -> dict | None:
    """O PC precisa reiniciar para usar o que foi atualizado? None = não; senão {"reason", "running", "installed"}.

    - Arch/CachyOS: o pacote do kernel apaga /usr/lib/modules/<versão antiga>; se a pasta do kernel que está
      rodando sumiu, ou há outra versão do mesmo pacote (pkgbase) instalada, o kernel novo só vale após reiniciar.
    - Debian/Ubuntu: /var/run/reboot-required. Fedora: `needs-restarting -r` (sai com 1).
    """
    release = release or os.uname().release
    running = modules / release
    if modules.is_dir():
        if not running.is_dir():
            newer = sorted(d.name for d in modules.iterdir() if (d / "vmlinuz").exists())
            return {"reason": "kernel", "running": release, "installed": ", ".join(newer) or None}
        base = (running / "pkgbase").read_text().strip() if (running / "pkgbase").exists() else None
        if base:
            mine = _mtime(running / "vmlinuz")
            for d in modules.iterdir():
                # outra versão do mesmo pacote, instalada depois (cópias antigas guardadas por hooks são mais velhas)
                if d != running and (d / "pkgbase").exists() and (d / "pkgbase").read_text().strip() == base \
                        and _mtime(d / "vmlinuz") > mine:
                    return {"reason": "kernel", "running": release, "installed": d.name}
    for f in REBOOT_FLAGS:
        if os.path.exists(f):
            return {"reason": "sistema", "running": release, "installed": None}
    if shutil.which("needs-restarting"):
        rc, _ = _run(["needs-restarting", "-r"], timeout=30)
        if rc == 1:
            return {"reason": "sistema", "running": release, "installed": None}
    return None


class Updates:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self._reboot: tuple[float, dict | None] = (0.0, None)
        try:
            self.data = json.loads(CACHE.read_text())
        except (OSError, ValueError):
            self.data = None
        threading.Thread(target=self._loop, daemon=True, name="updates").start()

    def status(self) -> dict:
        now = time.time()
        if now - self._reboot[0] > 60:      # barato, mas não precisa a cada pedido
            try:
                self._reboot = (now, reboot_required())
            except OSError:
                self._reboot = (now, None)
        with self.lock:
            return {"running": self.running, **(self.data or {"items": [], "count": None, "checked": None}),
                    "reboot": self._reboot[1]}

    def refresh(self) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
        threading.Thread(target=self._do, daemon=True, name="updates-check").start()

    def _do(self) -> None:
        try:
            d = check()
            CACHE.write_text(json.dumps(d))
            with self.lock:
                self.data = d
            log.info("atualizações pendentes: %d", d["count"])
        finally:
            with self.lock:
                self.running = False

    def _loop(self) -> None:
        time.sleep(60)   # não pesa no boot
        while True:
            with self.lock:
                last = (self.data or {}).get("checked") or 0
            if time.time() - last > AUTO_EVERY:
                self.refresh()
            time.sleep(600)
