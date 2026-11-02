"""O que está tocando no PC (Spotify, navegador, VLC...) via MPRIS no D-Bus da sessão.

Usa só o `busctl` (systemd), sem bibliotecas extras. Cada player publica
org.mpris.MediaPlayer2.<nome>; lemos título/artista/álbum/capa/posição e, com
`security.allow_control`, dá para tocar/pausar e pular faixa pelo painel.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from .desktop import user_env

log = logging.getLogger(__name__)
PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
OPS = {"play_pause": "PlayPause", "next": "Next", "previous": "Previous", "play": "Play", "pause": "Pause"}
ART_MAX = 10 * 1024 * 1024
IMG_MAGIC = ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"),
             (b"RIFF", "image/webp"))


class MediaError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _busctl(*args: str, timeout: float = 3) -> subprocess.CompletedProcess:
    return subprocess.run(["busctl", "--user", "--json=short", *args], capture_output=True, text=True,
                          timeout=timeout, env=user_env())


def unwrap(v):
    """Variante do busctl --json ({"type": "s", "data": ...}) -> valor Python."""
    if isinstance(v, dict) and set(v) == {"type", "data"}:
        return unwrap(v["data"])
    if isinstance(v, dict):
        return {k: unwrap(x) for k, x in v.items()}
    if isinstance(v, list):
        return [unwrap(x) for x in v]
    return v


def _get_all(name: str, iface: str) -> dict:
    r = _busctl("call", name, PATH, "org.freedesktop.DBus.Properties", "GetAll", "s", iface)
    if r.returncode:
        return {}
    try:
        data = json.loads(r.stdout)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return {}
    return unwrap(data)


def player_names() -> list[str]:
    r = _busctl("call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames")
    if r.returncode:
        return []
    try:
        names = json.loads(r.stdout)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return []
    return sorted(n for n in names if n.startswith(PREFIX))


def parse_player(name: str, root: dict, player: dict) -> dict:
    md = player.get("Metadata") or {}
    artist = md.get("xesam:artist") or md.get("xesam:albumArtist") or []
    if isinstance(artist, str):
        artist = [artist]
    length = md.get("mpris:length")
    pos = player.get("Position")
    return {
        "id": name[len(PREFIX):],
        "app": root.get("Identity") or name[len(PREFIX):].split(".")[0],
        "desktop": root.get("DesktopEntry") or None,
        "status": (player.get("PlaybackStatus") or "Stopped").lower(),   # playing | paused | stopped
        "title": md.get("xesam:title") or None,
        "artist": ", ".join(a for a in artist if a) or None,
        "album": md.get("xesam:album") or None,
        "art": md.get("mpris:artUrl") or None,
        "url": md.get("xesam:url") or None,
        "length": round(length / 1e6, 1) if isinstance(length, (int, float)) and length > 0 else None,
        "position": round(pos / 1e6, 1) if isinstance(pos, (int, float)) and pos >= 0 else None,
        "rate": player.get("Rate") if isinstance(player.get("Rate"), (int, float)) else 1.0,
        "can": {"play_pause": bool(player.get("CanPause") or player.get("CanPlay")),
                "next": bool(player.get("CanGoNext")), "previous": bool(player.get("CanGoPrevious"))},
    }


class Media:
    def __init__(self) -> None:
        self.players: list[dict] = []
        self.last_playing: dict[str, float] = {}

    def collect(self) -> dict:
        players, seen = [], set()
        now = time.time()
        for name in player_names():
            root = _get_all(name, "org.mpris.MediaPlayer2")
            pl = _get_all(name, "org.mpris.MediaPlayer2.Player")
            if not pl:
                continue
            p = parse_player(name, root, pl)
            p["at"] = now
            # o plasma-browser-integration repete o player do navegador: mostra uma vez só
            key = (p["title"], p["artist"], p["status"])
            if p["title"] and key in seen:
                continue
            seen.add(key)
            if p["status"] == "playing":
                self.last_playing[p["id"]] = now
            players.append(p)
        players.sort(key=lambda p: (p["status"] != "playing", p["status"] != "paused",
                                    -self.last_playing.get(p["id"], 0)))
        self.players = players
        current = next((p for p in players if p["status"] != "stopped" and p["title"]), None)
        return {"current": current, "players": players}

    def _find(self, pid: str) -> dict:
        p = next((p for p in self.players if p["id"] == pid), None)
        if not p:
            raise MediaError("Player não encontrado (fechou?).", 404)
        return p

    def control(self, pid: str, op: str) -> dict:
        if op not in OPS:
            raise MediaError("Operação inválida.")
        self._find(pid)
        r = _busctl("call", PREFIX + pid, PATH, "org.mpris.MediaPlayer2.Player", OPS[op])
        if r.returncode:
            raise MediaError(f"O player recusou: {r.stderr.strip()[:200]}", 500)
        time.sleep(0.25)       # o player leva um instante para atualizar o estado
        return self.collect()

    def art_file(self, pid: str) -> tuple[bytes, str] | None:
        """Capa local (file://) do player. Só serve exatamente o arquivo que o player anunciou
        agora, e só se for imagem."""
        p = self._find(pid)
        u = urlparse(p.get("art") or "")
        if u.scheme != "file":
            return None
        f = Path(unquote(u.path))
        try:
            if not f.is_file() or f.stat().st_size > ART_MAX:
                return None
            data = f.read_bytes()
        except OSError:
            return None
        for magic, ctype in IMG_MAGIC:
            if data.startswith(magic):
                return data, ctype
        return None
