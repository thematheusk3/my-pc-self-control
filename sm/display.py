"""Tela: resolução/taxa de atualização (KScreen), brilho/contraste do monitor (DDC/CI) e desligar (DPMS).

- Resolução: `kscreen-doctor` (KDE). Toda mudança precisa ser confirmada em REVERT_AFTER segundos;
  sem confirmação volta sozinha ao modo anterior (evita ficar com tela preta sem poder desfazer).
- Brilho/contraste: `ddcutil` fala direto com o monitor (DDC/CI). Brilho e contraste em 0 deixam a tela
  praticamente apagada SEM desligar o monitor; o PC continua vendo o monitor conectado.
- DPMS: `kscreen-doctor --dpms off` põe o monitor em espera; qualquer tecla/mouse acorda. Alguns monitores
  em DisplayPort "somem" do sistema em espera profunda; por isso o brilho zero é a opção recomendada.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time

from .config import ROOT
from .desktop import run_in_session

REVERT_AFTER = 20
VCP_BRIGHTNESS, VCP_CONTRAST = "10", "12"


class DisplayError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---------- KScreen ----------
def _kscreen(*args: str, json_out: bool = False):
    if not shutil.which("kscreen-doctor"):
        raise DisplayError("kscreen-doctor não encontrado (só funciona no KDE Plasma).", 501)
    try:
        r = run_in_session(["kscreen-doctor", *(["-j"] if json_out else []), *args], timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise DisplayError(f"Falha ao falar com o KScreen: {e}", 500) from None
    if r.returncode:
        raise DisplayError(f"kscreen-doctor: {(r.stderr or r.stdout).strip()[:200] or r.returncode}", 500)
    if json_out:
        try:
            return json.loads(r.stdout)
        except ValueError:
            raise DisplayError("Ninguém logado na máquina ou KScreen indisponível.", 409) from None
    return r.stdout


def outputs() -> list[dict]:
    out = []
    for o in _kscreen(json_out=True).get("outputs", []):
        if not o.get("connected"):
            continue
        modes = []
        for m in o.get("modes", []):
            modes.append({"id": str(m["id"]), "w": m["size"]["width"], "h": m["size"]["height"],
                          "hz": round(m["refreshRate"], 2)})
        modes.sort(key=lambda m: (-m["w"] * m["h"], -m["hz"]))
        cur = next((m for m in modes if m["id"] == str(o.get("currentModeId"))), None)
        out.append({"name": o["name"], "enabled": o.get("enabled"), "current": cur, "modes": modes,
                    "scale": o.get("scale"), "vrr": o.get("vrrPolicy"), "hdr": o.get("hdr"),
                    "preferred": [str(x) for x in o.get("preferredModes", [])]})
    return out


class ModeChanger:
    """Aplica um modo e reverte se não for confirmado a tempo."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: dict | None = None   # {output, previous, new, deadline}

    def status(self) -> dict | None:
        with self.lock:
            if not self.pending:
                return None
            return {**self.pending, "seconds_left": max(0, round(self.pending["deadline"] - time.time()))}

    def apply(self, output: str, mode_id: str) -> dict:
        if not re.fullmatch(r"[\w.-]{1,40}", output or "") or not re.fullmatch(r"\d{1,4}", str(mode_id)):
            raise DisplayError("Saída ou modo inválido.")
        outs = {o["name"]: o for o in outputs()}
        o = outs.get(output)
        if not o:
            raise DisplayError("Monitor não encontrado.", 404)
        mode = next((m for m in o["modes"] if m["id"] == str(mode_id)), None)
        if not mode:
            raise DisplayError("Modo não suportado por este monitor.")
        with self.lock:
            if self.pending:
                raise DisplayError("Confirme ou desfaça a mudança anterior primeiro.", 409)
            prev = o["current"]["id"] if o["current"] else None
            _kscreen(f"output.{output}.mode.{mode_id}")
            self.pending = {"output": output, "previous": prev, "new": str(mode_id),
                            "label": f"{mode['w']}x{mode['h']} @ {mode['hz']:g} Hz",
                            "deadline": time.time() + REVERT_AFTER}
            token = self.pending
        threading.Timer(REVERT_AFTER, self._timeout, args=(token,)).start()
        return self.status()

    def confirm(self) -> None:
        with self.lock:
            self.pending = None

    def revert(self) -> None:
        with self.lock:
            p, self.pending = self.pending, None
        if p and p["previous"]:
            _kscreen(f"output.{p['output']}.mode.{p['previous']}")

    def _timeout(self, token: dict) -> None:
        with self.lock:
            if self.pending is not token:   # já confirmado/desfeito
                return
        try:
            self.revert()
        except DisplayError:
            pass


MODES = ModeChanger()


def dpms(on: bool) -> None:
    _kscreen("--dpms", "on" if on else "off")


# ---------- DDC/CI ----------
_ddc_lock = threading.Lock()
_ddc_cache: dict = {"ts": 0, "data": None}
SAVED_FILE = ROOT / "data" / "display-dim.json"   # valores antes de "apagar", para restaurar


def _load_saved() -> dict:
    try:
        return {int(k): v for k, v in json.loads(SAVED_FILE.read_text()).items()}
    except (OSError, ValueError):
        return {}


def _store_saved(d: dict) -> None:
    try:
        SAVED_FILE.write_text(json.dumps(d))
    except OSError:
        pass


_saved: dict = _load_saved()


def _ddc(*args: str, timeout: float = 15) -> str:
    if not shutil.which("ddcutil"):
        raise DisplayError("ddcutil não encontrado.", 501)
    r = subprocess.run(["ddcutil", "--brief", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        msg = (r.stderr or r.stdout).strip()
        if "Permission denied" in msg or "permission" in msg.lower():
            raise DisplayError("Sem permissão no barramento i2c do monitor. Rode  sudo ./install.sh  "
                               "(adiciona o usuário ao grupo i2c) e reinicie a sessão.", 403)
        raise DisplayError(f"ddcutil: {msg[:200] or r.returncode}", 500)
    return r.stdout


def ddc_monitors(refresh: bool = False) -> list[dict]:
    """Monitores com DDC/CI: [{bus, model, brightness, contrast}] (cache de 30 s: o DDC é lento)."""
    with _ddc_lock:
        if not refresh and _ddc_cache["data"] is not None and time.time() - _ddc_cache["ts"] < 30:
            return _ddc_cache["data"]
        mons = []
        for block in _ddc("detect").split("\n\n"):
            bus = re.search(r"/dev/i2c-(\d+)", block)
            model = re.search(r"Monitor:\s+(.+)", block)
            conn = re.search(r"DRM connector:\s+card\d+-(\S+)", block)
            if not bus or "Invalid display" in block:
                continue
            m = {"bus": int(bus.group(1)), "model": (model.group(1).split(":")[1] if model and ":" in model.group(1)
                                                     else model.group(1) if model else "Monitor"),
                 "connector": conn.group(1) if conn else None}
            m.update(_read_vcp(m["bus"]))
            m["dimmed"] = m["bus"] in _saved
            mons.append(m)
        _ddc_cache.update(ts=time.time(), data=mons)
        return mons


def _read_vcp(bus: int) -> dict:
    out = {}
    try:
        text = _ddc("--bus", str(bus), "getvcp", VCP_BRIGHTNESS, VCP_CONTRAST)
    except DisplayError:
        return {"brightness": None, "contrast": None}
    # formato --brief: "VCP 10 C 99 100"
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "VCP" and f[2] == "C":
            key = "brightness" if f[1].upper() == VCP_BRIGHTNESS else "contrast" if f[1].upper() == VCP_CONTRAST else None
            if key:
                out[key] = int(f[3])
                out[key + "_max"] = int(f[4])
    return out


def set_vcp(bus: int, brightness: int | None = None, contrast: int | None = None) -> list[dict]:
    if not isinstance(bus, int) or not 0 <= bus < 64:
        raise DisplayError("Monitor inválido.")
    for code, val in ((VCP_BRIGHTNESS, brightness), (VCP_CONTRAST, contrast)):
        if val is None:
            continue
        if not 0 <= int(val) <= 100:
            raise DisplayError("Valor deve estar entre 0 e 100.")
        _ddc("--bus", str(bus), "setvcp", code, str(int(val)))
    return ddc_monitors(refresh=True)


def dim(bus: int) -> list[dict]:
    """'Quase desligada': brilho e contraste em 0, lembrando os valores para restaurar."""
    cur = next((m for m in ddc_monitors(refresh=True) if m["bus"] == bus), None)
    if not cur:
        raise DisplayError("Monitor não encontrado no DDC.", 404)
    if bus not in _saved:
        _saved[bus] = {"brightness": cur.get("brightness"), "contrast": cur.get("contrast")}
        _store_saved(_saved)
    return set_vcp(bus, 0, 0)


def restore(bus: int) -> list[dict]:
    prev = _saved.pop(bus, None) or {"brightness": 80, "contrast": 70}
    _store_saved(_saved)
    return set_vcp(bus, prev.get("brightness") or 80, prev.get("contrast") or 70)
