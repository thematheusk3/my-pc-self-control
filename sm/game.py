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
CONF_CHECK = 10.0       # s entre verificações do MangoHud.conf (bloco do painel apagado por outro programa)
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
        self._conf_check = 0.0

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
            if "SteamLaunch AppId=" not in cmd:
                continue
            # o cmd da lista de processos é cortado em 300 caracteres e pode partir o AppId ao meio
            m = re.search(r"SteamLaunch AppId=(\d+)", _full_cmdline(p.get("pid")) or cmd)
            if m and m.group(1) != "0":
                appid, name, exe = resolve_launch(m.group(1), p.get("pid"), p.get("create_time"), cmd)
                game = {"appid": appid, "name": name, "since": p.get("create_time"), "source": "steam", "pid": p.get("pid")}
                if exe:
                    game["exe"] = exe
                return game
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
        now = time.time()
        if now - self._conf_check > CONF_CHECK:
            # o GOverlay reescreve o MangoHud.conf inteiro ao abrir/salvar e apaga o bloco do painel
            self._conf_check = now
            if not self.config_ok():
                try:
                    self.set_enabled(True)
                    log.info("bloco da leitura de FPS recolocado no MangoHud.conf (o arquivo foi reescrito)")
                except RuntimeError as e:
                    log.warning("%s", e)
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
_launches: dict[tuple, tuple] = {}
RX_COMMON = re.compile(r"steamapps/common/([^/\x00]+)/")


def resolve_launch(appid: str, pid, create_time, cmd: str) -> tuple[str | None, str, str | None]:
    """AppId do 'reaper SteamLaunch' -> (appid da Steam ou None, nome, exe).

    Jogo da Steam: o nome vem do appmanifest. Atalho de jogo não-Steam (ex.: launcher de mod,
    como o Seamless Coop do Elden Ring), o AppId é um número gerado que não tem manifest:
      1. se o executável está dentro de steamapps/common/<pasta> de um jogo instalado, usa esse
         jogo (nome e arte certos);
      2. senão, o nome que o usuário deu ao atalho (userdata/*/config/shortcuts.vdf);
      3. senão, o nome do executável.
    Atalhos se reconhecem pelo AppId >= 2^31 (a Steam liga o bit mais alto)."""
    name = steam_name(appid)
    if name or int(appid) < 2 ** 31:      # AppIds de atalhos não-Steam têm o bit mais alto ligado
        return appid, name or f"App {appid}", None
    key = (pid, create_time)
    if key not in _launches:
        full = _full_cmdline(pid) or cmd     # o cmd da lista de processos vem cortado
        real = None
        for d in reversed(RX_COMMON.findall(full)):     # o jogo vem depois do runtime e do Proton
            real = _installdir_appid(d)
            if real:
                break
        if real:
            res = (real, steam_name(real) or d, None)
        else:
            short = steam_shortcuts().get(appid)
            exe = _exe_name(full)
            res = (None, short or exe or f"App {appid}", short or exe or f"app-{appid}")
        if len(_launches) > 32:
            _launches.clear()
        _launches[key] = res
    return _launches[key]


def _full_cmdline(pid) -> str | None:
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()[:16384]
    except (OSError, TypeError, ValueError):
        return None
    return raw.replace(b"\0", b" ").decode(errors="replace").strip() or None


def _exe_name(cmd: str) -> str | None:
    exes = re.findall(r"([^/\\\s\"']+)\.exe\b", cmd, re.I)
    exes = [e for e in exes if e.lower() not in ("steam", "proton")]
    return exes[-1] if exes else None


def _installdir_appid(folder: str) -> str | None:
    for root in STEAM_ROOTS:
        for lib in _libraries(root):
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                try:
                    text = acf.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                m = re.search(r'"installdir"\s+"([^"]+)"', text)
                n = re.search(r'"name"\s+"([^"]+)"', text)
                if m and m.group(1) == folder and not (n and NOT_GAME_NAMES.search(n.group(1))):
                    return acf.stem.split("_", 1)[1]
    return None


_shortcuts_cache: tuple[tuple, dict] = ((), {})


def steam_shortcuts() -> dict[str, str]:
    """AppId (uint32, como aparece no 'SteamLaunch AppId=') -> nome dos atalhos de jogos não-Steam."""
    global _shortcuts_cache
    files = []
    for root in STEAM_ROOTS:
        files += sorted((root / "userdata").glob("*/config/shortcuts.vdf"))
    stamp = []
    for f in files:
        try:
            stamp.append((str(f), f.stat().st_mtime))
        except OSError:
            pass
    stamp = tuple(stamp)
    if stamp == _shortcuts_cache[0]:
        return _shortcuts_cache[1]
    out: dict[str, str] = {}
    for path, _ in stamp:
        try:
            data, _ = parse_binary_vdf(Path(path).read_bytes()[:4 * 1024 * 1024])
        except (OSError, ValueError, IndexError):
            continue
        for entry in (data.get("shortcuts") or {}).values():
            if isinstance(entry, dict) and isinstance(entry.get("appid"), int) and entry.get("appname"):
                aid = str(entry["appid"] & 0xFFFFFFFF)
                out[aid] = str(entry["appname"])[:80]
                _shortcut_exes[aid] = str(entry.get("exe") or "").strip('"')[:500]
    _shortcuts_cache = (stamp, out)
    return out


_shortcut_exes: dict[str, str] = {}


def steam_shortcut(appid: str) -> dict | None:
    """Atalho não-Steam: {name, exe, game_appid?, game?}. game = jogo instalado em cuja pasta o exe está."""
    name = steam_shortcuts().get(appid)
    if not name:
        return None
    exe = _shortcut_exes.get(appid, "")
    out = {"name": name, "exe": exe}
    for folder in reversed(RX_COMMON.findall(exe.replace("\\", "/"))):
        real = _installdir_appid(folder)
        if real:
            out["game_appid"], out["game"] = real, steam_name(real) or folder
            break
    return out


def parse_binary_vdf(d: bytes, i: int = 0, depth: int = 0) -> tuple[dict, int]:
    """VDF binário da Steam (shortcuts.vdf). Chaves em minúsculas."""
    if depth > 16:
        raise ValueError("VDF aninhado demais")
    out: dict = {}
    while i < len(d):
        t = d[i]
        i += 1
        if t == 8:
            return out, i
        j = d.index(b"\0", i)
        key = d[i:j].decode(errors="replace").lower()
        i = j + 1
        if t == 0:
            out[key], i = parse_binary_vdf(d, i, depth + 1)
        elif t == 1:
            j = d.index(b"\0", i)
            out[key] = d[i:j].decode(errors="replace")
            i = j + 1
        elif t in (2, 3, 4, 6):
            out[key] = int.from_bytes(d[i:i + 4], "little")
            i += 4
        elif t == 7:
            out[key] = int.from_bytes(d[i:i + 8], "little")
            i += 8
        else:
            raise ValueError(f"tipo VDF desconhecido: {t}")
    return out, i


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
