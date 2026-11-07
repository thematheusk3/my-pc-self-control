"""Jogo atual + FPS via logs do MangoHud.

O Linux não tem um contador de FPS global; quem mede é o MangoHud, dentro do jogo.
Com a leitura ativada, o painel acrescenta `autostart_log=1` ao MangoHud.conf (num bloco
marcado, removido ao desativar). O MangoHud passa a gravar um CSV enquanto o jogo roda
e o painel lê o final desse arquivo a cada segundo.

Requisito: o jogo precisa rodar com o MangoHud (Steam: opção de inicialização
`mangohud %command%`, ou MANGOHUD=1 para todos). Sem ele, o painel mostra qual jogo está
aberto, mas não o FPS.

Desativado, nada é lido nem gravado. Ativado, o custo é o MangoHud gravar ~10 linhas/s
(com log_interval=100) e o painel ler alguns KB por segundo.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

from .config import ROOT
from .desktop import HOME, STEAM_ROOTS

log = logging.getLogger(__name__)
STATE_FILE = ROOT / "data" / "game.json"
MANGO_CONF = HOME / ".config" / "MangoHud" / "MangoHud.conf"
BEGIN, END = "# >>> self-monitoring: leitura de FPS no painel", "# <<< self-monitoring"
ACTIVE_WINDOW = 5.0     # s sem escrever no CSV = jogo fechado/log parado
WINDOW_1PCT = 60.0      # s usados para o 1% low
MAX_NOT_GAMES = 200
MAX_RECENT = 8          # jogos vistos há pouco, para escolher o que não é jogo
RX_KEY = re.compile(r"(steam:\d{1,12}|exe:[a-z0-9._-]{1,80})")


def read_mango_conf() -> tuple[str, dict]:
    try:
        text = MANGO_CONF.read_text()
    except OSError:
        text = ""
    opts = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            opts[k.strip()] = v.strip()
    return text, opts


def strip_block(text: str) -> str:
    return re.sub(rf"\n?{re.escape(BEGIN)}.*?{re.escape(END)}\n?", "\n", text, flags=re.S).rstrip("\n") + "\n"


def log_folder() -> Path:
    _, opts = read_mango_conf()
    folder = opts.get("output_folder") or str(HOME)
    return Path(os.path.expanduser(folder))


def parse_rows(lines: list[str], header: list[str] | None) -> tuple[list[str] | None, list[dict]]:
    """Linhas do CSV do MangoHud -> (cabeçalho, [{fps, frametime, ...}]). O cabeçalho de dados
    é a linha que começa com 'fps,'; as anteriores são informações do sistema."""
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("fps,"):
            header = line.split(",")
            continue
        if header is None:
            continue
        vals = line.split(",")
        if len(vals) != len(header):
            continue
        try:
            rows.append({k: float(v) for k, v in zip(header, vals)})
        except ValueError:
            continue
    return header, rows


class GameMonitor:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.lock = threading.Lock()
        state = self._load()
        self.enabled = bool(state.get("fps_enabled"))
        saved = state.get("not_games")
        self.not_games: dict[str, str] = {str(k): str(v) for k, v in saved.items()} if isinstance(saved, dict) else {}
        self.recent: dict[str, str] = {}
        self.file: Path | None = None
        self.pos = 0
        self.header: list[str] | None = None
        self.samples: deque = deque()      # (t, fps, frametime)
        self.last: dict = {}
        self.last_write = 0.0

    # ---------- ligar/desligar ----------
    def _load(self) -> dict:
        try:
            state = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"fps_enabled": self.enabled, "not_games": self.not_games}))

    def set_not_game(self, key: str, name: str | None, ignored: bool) -> dict:
        """Marca (ou desmarca) um app como "não é jogo": ele some do jogo atual e do FPS."""
        if not valid_key(key):
            raise ValueError("Jogo inválido.")
        with self.lock:
            if not ignored:
                self.not_games.pop(key, None)
            elif key in self.not_games or len(self.not_games) < MAX_NOT_GAMES:
                self.not_games[key] = str(name or key.split(":", 1)[1])[:80]
            else:
                raise ValueError("A lista de apps que não são jogos está cheia.")
            self._save()
        return self.status()

    def set_enabled(self, on: bool) -> dict:
        text, opts = read_mango_conf()
        base = strip_block(text) if text else ""
        if on:
            lines = []
            if opts.get("autostart_log") in (None, "0") or BEGIN in text:
                lines.append("autostart_log=1")
            if "output_folder" not in opts:
                folder = HOME / ".local/share/self-monitoring/mangohud"
                folder.mkdir(parents=True, exist_ok=True)
                lines.append(f"output_folder={folder}")
            if "log_interval" not in opts:
                lines.append("log_interval=100")
            if lines:
                base = (base.rstrip("\n") + "\n\n" if base.strip() else "") + "\n".join([BEGIN, *lines, END]) + "\n"
        try:
            MANGO_CONF.parent.mkdir(parents=True, exist_ok=True)
            if base != text:
                MANGO_CONF.write_text(base)
        except OSError as e:
            raise RuntimeError(f"Não foi possível alterar {MANGO_CONF}: {e}") from None
        self.enabled = on
        self._save()
        log.info("leitura de FPS %s", "ativada" if on else "desativada")
        return self.status()

    def config_ok(self) -> bool:
        text, opts = read_mango_conf()
        return opts.get("autostart_log") not in (None, "0")

    # ---------- jogo atual ----------
    def running_game(self) -> dict | None:
        """Jogo da Steam em execução (processo 'reaper SteamLaunch AppId=N')."""
        for p in self.ctx.processes or []:
            cmd = p.get("cmd") or ""
            m = re.search(r"SteamLaunch AppId=(\d+)", cmd)
            if m and m.group(1) != "0":
                appid = m.group(1)
                return {"appid": appid, "name": steam_name(appid) or f"App {appid}", "since": p.get("create_time"),
                        "source": "steam", "pid": p.get("pid")}
        return None

    # ---------- leitura do CSV ----------
    def _newest_log(self) -> Path | None:
        folder = log_folder()
        try:
            files = [f for f in folder.glob("*.csv") if not f.name.endswith("_summary.csv")]
        except OSError:
            return None
        if not files:
            return None
        f = max(files, key=lambda p: p.stat().st_mtime)
        return f if time.time() - f.stat().st_mtime < ACTIVE_WINDOW else None

    def tick(self) -> None:
        if not self.enabled:
            return
        f = self._newest_log()
        now = time.time()
        with self.lock:
            if f is None:
                if self.file and now - self.last_write > ACTIVE_WINDOW:
                    self.file, self.header, self.samples = None, None, deque()
                    self.last = {}
                return
            if f != self.file:
                self.file, self.pos, self.header, self.samples = f, 0, None, deque()
            try:
                with open(f, "rb") as fh:
                    size = fh.seek(0, 2)
                    if size < self.pos:      # arquivo recriado
                        self.pos, self.header = 0, None
                    if self.header is None and self.pos == 0:
                        start = 0
                    else:
                        start = max(self.pos, size - 256 * 1024)   # não relê arquivos enormes
                    fh.seek(start)
                    chunk = fh.read(size - start).decode(errors="replace")
            except OSError:
                return
            # só linhas completas
            cut = chunk.rfind("\n")
            if cut < 0:
                return
            self.pos = start + len(chunk[:cut + 1].encode())
            if start > 0 and self.header is None:
                # pulou o começo: pega o cabeçalho lendo as primeiras linhas
                try:
                    head = f.open(errors="replace").read(4096).splitlines()
                    self.header, _ = parse_rows(head, None)
                except OSError:
                    pass
            self.header, rows = parse_rows(chunk[:cut].splitlines(), self.header)
            if rows:
                self.last_write = now
                step = 1.0 / max(1, len(rows))
                for i, r in enumerate(rows):   # espalha as linhas no último segundo
                    self.samples.append((now - (len(rows) - 1 - i) * step, r.get("fps"), r.get("frametime")))
                self.last = rows[-1]
            while self.samples and self.samples[0][0] < now - WINDOW_1PCT:
                self.samples.popleft()

    def status(self) -> dict:
        game = self.running_game()
        with self.lock:
            live = self.file is not None and time.time() - self.last_write < ACTIVE_WINDOW
            recent = [s for s in self.samples if s[0] >= time.time() - 1.5 and s[1] is not None]
            fps = round(sum(s[1] for s in recent) / len(recent), 1) if recent else None
            fts = sorted(s[2] for s in self.samples if s[2])
            low = None
            if len(fts) >= 20:
                p99 = fts[min(len(fts) - 1, int(len(fts) * 0.99))]
                low = round(1000 / p99, 1) if p99 else None
            last = dict(self.last)
            fname = self.file.name if self.file else None
        name = None
        if fname:
            name = re.sub(r"_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.csv$", "", fname)
        if game is None and live and name:
            # o CSV do MangoHud tem o nome do executável: serve de chave estável para o jogo sem Steam
            game = {"appid": None, "name": name, "since": None, "source": "mangohud", "exe": name}
        with self.lock:
            if game:
                game = {**game, "key": game_key(game)}
                if game["key"] in self.not_games:      # marcado como "não é jogo": some do jogo atual e do FPS
                    game, live = None, False
                else:
                    self.recent.pop(game["key"], None)
                    self.recent[game["key"]] = game["name"]
                    while len(self.recent) > MAX_RECENT:
                        self.recent.pop(next(iter(self.recent)))
            not_games = [{"key": k, "name": n} for k, n in self.not_games.items()]
            seen = [{"key": k, "name": n} for k, n in reversed(self.recent.items()) if k not in self.not_games]
        return {
            "enabled": self.enabled, "config_ok": self.config_ok() if self.enabled else None,
            "conf_path": str(MANGO_CONF), "log_folder": str(log_folder()),
            "game": game, "live": live,
            "fps": fps if live else None, "low1": low if live else None,
            "frametime": round(last["frametime"], 2) if live and last.get("frametime") else None,
            "cpu_load": last.get("cpu_load") if live else None, "gpu_load": last.get("gpu_load") if live else None,
            "log_file": fname, "not_games": not_games, "recent_games": seen,
        }


def valid_key(key: str) -> bool:
    return bool(RX_KEY.fullmatch(key or ""))


def game_key(game: dict) -> str:
    """Identifica o jogo nas preferências do Mural (arte escolhida) e na lista de "não é jogo"."""
    if game.get("appid"):
        return f"steam:{game['appid']}"
    return "exe:" + (re.sub(r"[^a-z0-9._-]+", "-", (game.get("exe") or game.get("name") or "?").lower()).strip("-")[:80] or "x")


_names: dict[str, str | None] = {}


def steam_name(appid: str) -> str | None:
    if appid in _names:
        return _names[appid]
    name = None
    for root in STEAM_ROOTS:
        for lib in _libraries(root):
            f = lib / "steamapps" / f"appmanifest_{appid}.acf"
            try:
                m = re.search(r'"name"\s+"([^"]+)"', f.read_text(errors="replace"))
            except OSError:
                continue
            if m:
                name = m.group(1)
                break
        if name:
            break
    _names[appid] = name
    return name


NOT_GAME_NAMES = re.compile(r"redistributable|steamworks|proton |steam linux runtime|^steamvr$", re.I)


def steam_games() -> list[tuple[str, str]]:
    """(appid, nome) dos jogos instalados, em todas as bibliotecas da Steam."""
    out: dict[str, str] = {}
    for root in STEAM_ROOTS:
        for lib in _libraries(root):
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                appid = acf.stem.split("_", 1)[1]
                try:
                    m = re.search(r'"name"\s+"([^"]+)"', acf.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if m and not NOT_GAME_NAMES.search(m.group(1)):
                    out[appid] = m.group(1)
    return list(out.items())


def _libraries(root: Path) -> list[Path]:
    libs = [root]
    try:
        vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(errors="replace")
        libs += [Path(p) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    return list(dict.fromkeys(libs))
