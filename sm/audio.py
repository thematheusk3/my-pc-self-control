"""Áudio (PipeWire/PulseAudio via pactl): saídas, entradas, volume, mudo, áudio por aplicativo.

Roda os comandos no ambiente da sessão do usuário (o serviço não está numa sessão).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

from .desktop import run_in_session


class AudioError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _pactl(*args: str, json_out: bool = False):
    if not shutil.which("pactl"):
        raise AudioError("pactl não encontrado (instale pipewire-pulse ou pulseaudio-utils).", 501)
    cmd = ["pactl", *(["-f", "json"] if json_out else []), *args]
    try:
        r = run_in_session(cmd, timeout=8)
    except (OSError, subprocess.SubprocessError) as e:
        raise AudioError(f"Falha ao falar com o servidor de áudio: {e}", 500) from None
    if r.returncode:
        msg = r.stderr.strip() or r.stdout.strip()
        if "Connection refused" in msg or "Connection failure" in msg:
            raise AudioError("Servidor de áudio indisponível (ninguém logado na máquina?).", 409)
        raise AudioError(f"pactl: {msg[:200]}", 500)
    if json_out:
        try:
            return json.loads(r.stdout or "[]")
        except ValueError:
            raise AudioError("Resposta inválida do pactl.", 500) from None
    return r.stdout.strip()


def _vol(v: dict) -> int | None:
    """Média dos canais em % (pactl devolve '94%')."""
    vals = [int(c["value_percent"].rstrip("%")) for c in (v or {}).values() if "value_percent" in c]
    return round(sum(vals) / len(vals)) if vals else None


def _kind(props: dict, name: str) -> str:
    """Tipo do dispositivo para ícone/rótulo: hdmi, headset, headphones, speaker, bluetooth, usb, mic."""
    n = name.lower()
    bus = props.get("device.bus", "")
    form = props.get("device.form_factor", "")
    if "hdmi" in n or "displayport" in n:
        return "hdmi"
    if bus == "bluetooth" or "bluez" in n:
        return "bluetooth"
    if form in ("headset", "headphone", "headphones") or re.search(r"headset|headphone|cloud|fone", n):
        return "headset"
    if bus == "usb":
        return "usb"
    return "speaker"


def state() -> dict:
    default_sink = _pactl("get-default-sink")
    default_source = _pactl("get-default-source")
    sinks = _pactl("list", "sinks", json_out=True)
    sources = _pactl("list", "sources", json_out=True)
    inputs = _pactl("list", "sink-inputs", json_out=True)
    outputs = _pactl("list", "source-outputs", json_out=True)
    sink_by_index = {s["index"]: s["name"] for s in sinks}
    src_by_index = {s["index"]: s["name"] for s in sources}

    def dev(d, default):
        props = d.get("properties", {})
        return {
            "name": d["name"], "index": d["index"], "description": d.get("description") or d["name"],
            "volume": _vol(d.get("volume")), "mute": bool(d.get("mute")), "state": d.get("state"),
            "default": d["name"] == default, "kind": _kind(props, d["name"] + " " + (d.get("description") or "")),
            "port": d.get("active_port"),
        }

    def stream(st, by_index, target_key):
        props = st.get("properties", {})
        app = props.get("application.name") or props.get("node.name") or props.get("media.name") or "?"
        return {
            "index": st["index"], "app": app, "media": props.get("media.name"),
            "binary": props.get("application.process.binary"), "pid": _int(props.get("application.process.id")),
            "icon": props.get("application.icon_name"),
            "volume": _vol(st.get("volume")), "mute": bool(st.get("mute")), "corked": bool(st.get("corked")),
            "device": by_index.get(st.get(target_key)),
        }

    return {
        "sinks": [dev(s, default_sink) for s in sinks],
        "sources": [dev(s, default_source) for s in sources
                    if s.get("properties", {}).get("device.class") != "monitor" and not s["name"].endswith(".monitor")],
        "apps": [stream(s, sink_by_index, "sink") for s in inputs],
        "recording": [stream(s, src_by_index, "source") for s in outputs
                      if not (s.get("properties", {}).get("application.name") or "").lower().startswith("pipewire")],
        "default_sink": default_sink, "default_source": default_source,
    }


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _name_ok(n: str) -> str:
    if not isinstance(n, str) or not re.fullmatch(r"[\w.:@+-]{1,200}", n):
        raise AudioError("Dispositivo inválido.")
    return n


def _pct(v) -> str:
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise AudioError("Volume inválido.") from None
    if not 0 <= v <= 150:
        raise AudioError("Volume deve estar entre 0 e 150%.")
    return f"{v}%"


def action(body: dict) -> dict:
    """{"op": ..., ...}
    set_default  {kind: sink|source, name}
    volume       {kind: sink|source|app|rec, name|index, volume}
    mute         {kind: ..., name|index, mute: bool}
    move         {index, sink}           (app -> outra saída)
    """
    op = body.get("op")
    kind = body.get("kind", "sink")
    if op == "set_default":
        name = _name_ok(body.get("name"))
        _pactl("set-default-sink" if kind == "sink" else "set-default-source", name)
        if kind == "sink" and body.get("move_apps", True):   # leva os apps junto, como o KDE faz
            for st in _pactl("list", "sink-inputs", json_out=True):
                _pactl("move-sink-input", str(st["index"]), name)
    elif op in ("volume", "mute"):
        target = {"sink": ("set-sink-volume", "set-sink-mute"), "source": ("set-source-volume", "set-source-mute"),
                  "app": ("set-sink-input-volume", "set-sink-input-mute"),
                  "rec": ("set-source-output-volume", "set-source-output-mute")}.get(kind)
        if not target:
            raise AudioError("Tipo inválido.")
        ident = str(int(body["index"])) if kind in ("app", "rec") else _name_ok(body.get("name"))
        if op == "volume":
            _pactl(target[0], ident, _pct(body.get("volume")))
        else:
            _pactl(target[1], ident, "1" if body.get("mute") else "0")
    elif op == "move":
        _pactl("move-sink-input", str(int(body["index"])), _name_ok(body.get("sink")))
    else:
        raise AudioError("Operação inválida.")
    return state()
