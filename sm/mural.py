"""Mural: tela cheia para deixar aberta (relógio, clima, jogo, música), como um mostrador de mesa.

Guarda as preferências em data/mural.json (valem para todos os aparelhos). O clima vem da Open-Meteo (sem chave), só
depois de o usuário escolher uma cidade: o que sai do PC são as coordenadas dela.

Arte dos jogos, nesta ordem: imagem enviada pelo usuário; cache local da biblioteca da Steam
(appcache/librarycache/<appid>); CDN da Steam. Jogos fora da Steam só têm arte se o usuário enviar uma.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .auth import _write_private
from .config import ROOT
from .desktop import STEAM_ROOTS
from .game import steam_games, valid_key

log = logging.getLogger(__name__)
CONF_FILE = ROOT / "data" / "mural.json"
ART_DIR = ROOT / "data" / "art"
WEATHER_EVERY = 20 * 60
WEATHER_RETRY = 5 * 60
TIMEOUT = 8
MAX_ART = 8_000_000            # bytes de uma arte baixada da Steam
MAX_UPLOAD = 900_000           # o navegador reduz a imagem antes de enviar
BACKGROUND_FILE = "fundo-inicio.jpg"
MISS_TTL = 6 * 3600            # não insiste numa arte que a Steam não tem

MODES = ("auto", "home", "game", "music")
BACKGROUNDS = ("sky", "ridge", "board", "aurora", "games", "photo")
# nomes no cache da Steam, do mais comum ao alternativo (jogos novos ficam em subpastas com hash e usam outros nomes);
# a CDN só é tentada com o primeiro
STEAM_ART = {"hero": ("library_hero.jpg",), "cover": ("library_600x900.jpg", "library_capsule.jpg"),
             "header": ("header.jpg", "library_header.jpg"), "logo": ("logo.png",)}
GAME_DEFAULTS = {"fps": True, "low1": True, "frametime": False, "cpu": True, "gpu": True, "temps": True, "vram": True,
                 "ram": False, "session": True, "music": True}

# código WMO -> (descrição, ícone de dia, ícone de noite)
WMO = {
    0: ("Céu limpo", "sun", "moon"),
    1: ("Quase limpo", "sun", "moon"), 2: ("Parcialmente nublado", "cloud-sun", "cloud-moon"), 3: ("Nublado", "cloud", "cloud"),
    45: ("Neblina", "cloud-fog", "cloud-fog"), 48: ("Neblina", "cloud-fog", "cloud-fog"),
    51: ("Garoa fraca", "cloud-rain", "cloud-rain"), 53: ("Garoa", "cloud-rain", "cloud-rain"), 55: ("Garoa forte", "cloud-rain", "cloud-rain"),
    56: ("Garoa congelante", "cloud-rain", "cloud-rain"), 57: ("Garoa congelante", "cloud-rain", "cloud-rain"),
    61: ("Chuva fraca", "cloud-rain", "cloud-rain"), 63: ("Chuva", "cloud-rain", "cloud-rain"), 65: ("Chuva forte", "cloud-rain", "cloud-rain"),
    66: ("Chuva congelante", "cloud-rain", "cloud-rain"), 67: ("Chuva congelante", "cloud-rain", "cloud-rain"),
    71: ("Neve fraca", "cloud-snow", "cloud-snow"), 73: ("Neve", "cloud-snow", "cloud-snow"), 75: ("Neve forte", "cloud-snow", "cloud-snow"),
    77: ("Neve", "cloud-snow", "cloud-snow"),
    80: ("Pancadas de chuva", "cloud-rain", "cloud-rain"), 81: ("Pancadas de chuva", "cloud-rain", "cloud-rain"),
    82: ("Pancadas fortes", "cloud-rain", "cloud-rain"),
    85: ("Pancadas de neve", "cloud-snow", "cloud-snow"), 86: ("Pancadas de neve", "cloud-snow", "cloud-snow"),
    95: ("Trovoada", "cloud-lightning", "cloud-lightning"), 96: ("Trovoada com granizo", "cloud-lightning", "cloud-lightning"),
    99: ("Trovoada com granizo", "cloud-lightning", "cloud-lightning"),
}


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "self-monitoring"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read(2_000_000))


def describe_weather(code, is_day: bool = True) -> tuple[str, str]:
    label, day, night = WMO.get(code, ("Tempo indefinido", "cloud", "cloud"))
    return label, day if is_day else night


def parse_weather(raw: dict) -> dict:
    cur = raw["current"]
    label, icon = describe_weather(cur.get("weather_code"), bool(cur.get("is_day", 1)))
    d = raw.get("daily") or {}
    days = []
    for i, day in enumerate(d.get("time") or []):
        dl, di = describe_weather(d["weather_code"][i])
        days.append({"date": day, "max": d["temperature_2m_max"][i], "min": d["temperature_2m_min"][i],
                     "rain": d["precipitation_probability_max"][i], "label": dl, "icon": di,
                     "sunrise": (d.get("sunrise") or [None] * (i + 1))[i], "sunset": (d.get("sunset") or [None] * (i + 1))[i]})
    return {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"), "wind": cur.get("wind_speed_10m"),
            "label": label, "icon": icon, "days": days}


def _custom_path(key: str) -> Path:
    return ART_DIR / f"custom-{hashlib.sha1(key.encode()).hexdigest()[:20]}.jpg"


class Mural:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.conf = self._load()
        self.weather_data: dict | None = None
        self.weather_at = 0.0
        self.weather_error: str | None = None
        self._busy = False
        self._miss: dict[str, float] = {}

    # ---------- preferências ----------
    def _load(self) -> dict:
        try:
            c = json.loads(CONF_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            c = {}
        return {"location": c.get("location"), "mode": c.get("mode") if c.get("mode") in MODES else "auto",
                "notifications": bool(c.get("notifications", True)), "takeover": bool(c.get("takeover", True)),
                "context": bool(c.get("context", True)),
                "background": c.get("background") if c.get("background") in BACKGROUNDS else "sky",
                "background_v": int(c.get("background_v") or 0),
                "game": {**GAME_DEFAULTS, **{k: bool(v) for k, v in (c.get("game") or {}).items() if k in GAME_DEFAULTS}},
                "art": c.get("art") or {}}

    def _save(self) -> None:
        _write_private(CONF_FILE, self.conf)

    def status(self) -> dict:
        with self.lock:
            out = {k: self.conf[k] for k in ("location", "mode", "notifications", "takeover", "context", "background", "background_v", "game")}
        return {**out, "weather": self.weather(), "weather_error": self.weather_error, "weather_at": self.weather_at or None}

    def update(self, body: dict) -> dict:
        moved = False
        with self.lock:
            if "mode" in body:
                if body["mode"] not in MODES:
                    raise ValueError("Modo desconhecido.")
                self.conf["mode"] = body["mode"]
            if "background" in body:
                if body["background"] not in BACKGROUNDS:
                    raise ValueError("Fundo desconhecido.")
                if body["background"] == "photo" and not self.conf["background_v"]:
                    raise ValueError("Envie uma imagem primeiro.")
                self.conf["background"] = body["background"]
            for k in ("notifications", "takeover", "context"):
                if k in body:
                    self.conf[k] = bool(body[k])
            for k, v in (body.get("game") or {}).items():
                if k in GAME_DEFAULTS:
                    self.conf["game"][k] = bool(v)
            if "location" in body:
                self.conf["location"] = self._clean_location(body["location"])
                self.weather_data, self.weather_error, self.weather_at = None, None, 0.0
                moved = True
            self._save()
        if moved and self.conf["location"]:
            self.refresh_weather()
        return self.status()

    @staticmethod
    def _clean_location(loc) -> dict | None:
        if loc is None:
            return None
        try:
            lat, lon = float(loc["lat"]), float(loc["lon"])
        except (TypeError, KeyError, ValueError):
            raise ValueError("Cidade inválida.") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Coordenadas inválidas.")
        return {"name": str(loc.get("name") or "")[:80], "admin": str(loc.get("admin") or "")[:80],
                "country": str(loc.get("country") or "")[:80], "lat": lat, "lon": lon}

    # ---------- clima ----------
    @staticmethod
    def search_city(q: str) -> list[dict]:
        q = (q or "").strip()
        if len(q) < 2:
            return []
        url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
            {"name": q[:80], "count": 8, "language": "pt", "format": "json"})
        return [{"name": r.get("name"), "admin": r.get("admin1") or "", "country": r.get("country") or "",
                 "lat": r.get("latitude"), "lon": r.get("longitude")} for r in _fetch_json(url).get("results") or []]

    def refresh_weather(self) -> None:
        loc = self.conf["location"]
        if not loc or self._busy:
            return
        self._busy = True
        try:
            url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
                "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "auto", "forecast_days": 4,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,is_day,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"})
            self.weather_data = parse_weather(_fetch_json(url))
            self.weather_error = None
            self.weather_at = time.time()
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            log.debug("clima: %s", e)
            self.weather_error = "Não foi possível atualizar o clima. Verifique a internet do PC."
            self.weather_at = time.time() - WEATHER_EVERY + WEATHER_RETRY     # tenta de novo em alguns minutos
        finally:
            self._busy = False

    def weather(self) -> dict | None:
        if self.conf["location"] and not self._busy and time.time() - self.weather_at >= WEATHER_EVERY:
            threading.Thread(target=self.refresh_weather, daemon=True, name="clima").start()
        return self.weather_data

    # ---------- imagem de fundo do Início ----------
    def set_background(self, image: str) -> dict:
        data = self._decode_image(image)
        ART_DIR.mkdir(parents=True, exist_ok=True)
        (ART_DIR / BACKGROUND_FILE).write_bytes(data)
        with self.lock:
            self.conf["background"], self.conf["background_v"] = "photo", int(time.time())
            self._save()
        return self.status()

    def reset_background(self) -> dict:
        (ART_DIR / BACKGROUND_FILE).unlink(missing_ok=True)
        with self.lock:
            self.conf["background_v"] = 0
            if self.conf["background"] == "photo":
                self.conf["background"] = "sky"
            self._save()
        return self.status()

    def background(self) -> bytes | None:
        try:
            return (ART_DIR / BACKGROUND_FILE).read_bytes()
        except OSError:
            return None

    # ---------- arte dos jogos ----------
    def games(self, current: dict | None = None) -> list[dict]:
        """Jogos instalados da Steam + os que já têm arte própria + o aberto agora."""
        with self.lock:
            art = dict(self.conf["art"])
        items = {f"steam:{appid}": name for appid, name in steam_games()}
        for key, e in art.items():
            items.setdefault(key, e.get("name") or key.split(":", 1)[1])
        if current and current.get("key"):
            items.setdefault(current["key"], current.get("name") or current["key"])
        return sorted(({"key": k, "name": n, "kind": art.get(k, {}).get("kind", "hero"), "v": art.get(k, {}).get("v", 0),
                        "steam": k.startswith("steam:")} for k, n in items.items()), key=lambda g: g["name"].lower())

    def set_art(self, key: str, name: str, kind: str | None = None, image: str | None = None) -> dict:
        if not valid_key(key):
            raise ValueError("Jogo inválido.")
        if image is not None:
            data = self._decode_image(image)
            ART_DIR.mkdir(parents=True, exist_ok=True)
            _custom_path(key).write_bytes(data)
            kind = "custom"
        elif kind not in ("hero", "cover", "header"):
            raise ValueError("Escolha de arte inválida.")
        elif not key.startswith("steam:"):
            raise ValueError("Este jogo não é da Steam: envie uma imagem.")
        with self.lock:
            prev = self.conf["art"].get(key, {})
            self.conf["art"][key] = {"kind": kind, "name": str(name or prev.get("name") or "")[:120], "v": int(time.time())}
            self._save()
            return self.conf["art"][key]

    def reset_art(self, key: str) -> None:
        if not valid_key(key):
            raise ValueError("Jogo inválido.")
        _custom_path(key).unlink(missing_ok=True)
        with self.lock:
            if self.conf["art"].pop(key, None) is not None:
                self._save()

    @staticmethod
    def _decode_image(image: str) -> bytes:
        try:
            data = base64.b64decode(image.split(",", 1)[-1], validate=True)
        except ValueError:
            raise ValueError("Imagem inválida.") from None
        if len(data) > MAX_UPLOAD:
            raise ValueError("Imagem grande demais.")
        if not data.startswith(b"\xff\xd8\xff"):          # o navegador sempre converte para JPEG
            raise ValueError("Imagem inválida: envie um JPEG.")
        return data

    def art(self, key: str, kind: str | None = None) -> tuple[bytes, str] | None:
        """Bytes e tipo da arte, ou None se não houver. kind=None usa a escolha do usuário."""
        if not valid_key(key):
            return None
        chosen = kind or self.conf["art"].get(key, {}).get("kind", "hero")
        if chosen == "custom":
            try:
                return _custom_path(key).read_bytes(), "image/jpeg"
            except OSError:
                return None
        files = STEAM_ART.get(chosen)
        if not files or not key.startswith("steam:"):
            return None
        return self._steam_art(key.split(":", 1)[1], files)

    def _steam_art(self, appid: str, files: tuple[str, ...]) -> tuple[bytes, str] | None:
        file = files[0]
        ctype = "image/png" if file.endswith(".png") else "image/jpeg"
        for root in STEAM_ROOTS:
            d = root / "appcache" / "librarycache" / appid
            for name in files:
                for p in (d / name, *sorted(d.glob(f"*/{name}"))):
                    try:
                        return p.read_bytes(), ctype
                    except OSError:
                        continue
        cached = ART_DIR / "steam" / f"{appid}_{file}"
        try:
            return cached.read_bytes(), ctype
        except OSError:
            pass
        miss = f"{appid}/{file}"
        if time.time() - self._miss.get(miss, 0) < MISS_TTL:
            return None
        try:
            url = f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/{file}"
            req = urllib.request.Request(url, headers={"User-Agent": "self-monitoring"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = r.read(MAX_ART)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
            return data, ctype
        except OSError as e:
            log.debug("arte da Steam %s: %s", miss, e)
            self._miss[miss] = time.time()
            return None

