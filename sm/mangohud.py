"""Configuração do MangoHud pelo painel, ao vivo.

O MangoHud observa o arquivo de configuração (inotify) e recarrega sozinho quando ele muda,
então editar o arquivo aqui muda o HUD do jogo aberto na hora. É o mesmo arquivo que o
GOverlay gera (~/.config/MangoHud/MangoHud.conf); os dois convivem: o GOverlay reescreve o
arquivo inteiro quando você salva nele.

Regras:
  - só arquivos .conf da pasta do MangoHud (global, por jogo e os do GOverlay);
  - o bloco do painel (leitura de FPS, em game.py) é preservado e não aparece no editor;
  - a opção `exec` (roda comandos no PC) não pode ser criada nem alterada pelo painel.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading

from .game import BEGIN, END, MANGO_CONF, strip_block

CONF_DIR = MANGO_CONF.parent
RX_FILE = re.compile(r"[A-Za-z0-9_.+-]{1,80}\.conf")
RX_KEY = re.compile(r"[a-z0-9_]{1,40}")
BLOCKED = {"exec"}                      # roda comandos no shell
DEFAULT_ON = {"fps", "frametime", "frame_timing", "cpu_stats", "gpu_stats"}   # ligadas por padrão no layout antigo
MAX_TEXT = 64 * 1024
_lock = threading.Lock()
_version: str | None = None


class ConfError(ValueError):
    pass


def installed() -> bool:
    return bool(shutil.which("mangohud")) or MANGO_CONF.exists()


def version() -> str | None:
    global _version
    if _version is None:
        try:
            out = subprocess.run(["mangohud", "--version"], capture_output=True, text=True, timeout=3).stdout
            _version = out.strip().splitlines()[0] if out.strip() else ""
        except (OSError, subprocess.SubprocessError):
            _version = ""
    return _version or None


def goverlay_running() -> bool:
    from pathlib import Path
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if (d / "comm").read_text().strip() == "goverlay":
                    return True
            except OSError:
                pass
    return False


def _path(name: str | None):
    name = name or MANGO_CONF.name
    if not RX_FILE.fullmatch(name):
        raise ConfError("Arquivo inválido.")
    return CONF_DIR / name


def scope(name: str) -> str:
    if name == MANGO_CONF.name:
        return "global"
    return "other" if name == "custom.conf" else "app"


def files() -> list[dict]:
    try:
        names = sorted(p.name for p in CONF_DIR.glob("*.conf") if p.is_file() and RX_FILE.fullmatch(p.name))
    except OSError:
        names = []
    if MANGO_CONF.name not in names:
        names.insert(0, MANGO_CONF.name)
    names.sort(key=lambda n: (n != MANGO_CONF.name, n.lower()))
    return [{"name": n, "scope": scope(n)} for n in names]


def parse(text: str) -> tuple[dict, list[str]]:
    """Opções ativas (fora do bloco do painel): {chave: valor | True}, na ordem do arquivo."""
    opts: dict = {}
    for line in strip_block(text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, eq, v = line.partition("=")
        k = k.strip()
        if RX_KEY.fullmatch(k):
            opts[k] = v.strip() if eq else True
    return opts, list(opts)


def _split_block(text: str) -> tuple[str, str]:
    m = re.search(rf"{re.escape(BEGIN)}.*?{re.escape(END)}\n?", text, flags=re.S)
    if not m:
        return text, ""
    return strip_block(text), m.group(0).rstrip("\n") + "\n"


def _exec_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        k = line.strip().partition("=")[0].strip()
        if k in BLOCKED:
            out.append(line.strip())
    return out


def _read(path) -> str:
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        return ""


def _write(path, text: str) -> None:
    # escreve no mesmo arquivo (sem renomear): o inotify do MangoHud vê a mudança e recarrega
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
    except OSError as e:
        raise RuntimeError(f"Não foi possível gravar {path}: {e}") from None


def state(name: str | None = None) -> dict:
    path = _path(name)
    text = _read(path)
    body, block = _split_block(text)
    opts, order = parse(text)
    _, managed = parse(block.replace(BEGIN, "").replace(END, "")) if block else ({}, [])
    return {
        "installed": installed(), "version": version(), "goverlay": bool(shutil.which("goverlay")), "goverlay_running": goverlay_running(),
        "dir": str(CONF_DIR), "file": path.name, "path": str(path), "exists": path.exists(),
        "scope": scope(path.name), "files": files(),
        "options": opts, "order": order, "managed": managed,
        "legacy_layout": opts.get("legacy_layout", "1") not in ("0", "false"),
        "text": body, "blocked": sorted(BLOCKED),
    }


def apply(name: str | None, changes: dict) -> dict:
    """Altera opções: True = linha `chave`; False/None = remove; texto/número = `chave=valor`."""
    if not isinstance(changes, dict) or not changes or len(changes) > 40:
        raise ConfError("Nada para alterar.")
    path = _path(name)
    with _lock:
        text = _read(path)
        body, block = _split_block(text)
        legacy = parse(text)[0].get("legacy_layout", "1") not in ("0", "false")
        lines = body.rstrip("\n").splitlines() if body.strip() else []
        for key, val in changes.items():
            if not isinstance(key, str) or not RX_KEY.fullmatch(key):
                raise ConfError(f"Opção inválida: {key!r}")
            if key in BLOCKED:
                raise ConfError(f"A opção {key} não pode ser alterada pelo painel.")
            if val is False or val is None:
                new = f"{key}=0" if (legacy and key in DEFAULT_ON) else None
            elif val is True:
                new = key
            else:
                val = str(val).strip()
                if len(val) > 500 or "\n" in val or "\r" in val:
                    raise ConfError(f"Valor inválido para {key}.")
                new = f"{key}={val}"
            idx = [i for i, ln in enumerate(lines)
                   if not ln.strip().startswith("#") and ln.strip().partition("=")[0].strip() == key]
            if idx:
                first = idx[0]
                for i in reversed(idx[1:]):
                    del lines[i]
                if new is None:
                    del lines[first]
                else:
                    lines[first] = new
            elif new is not None:
                lines.append(new)
        out = "\n".join(lines).rstrip("\n") + "\n"
        if block:
            out += "\n" + block
        if out != text:
            _write(path, out)
    return state(path.name)


def save_text(name: str | None, new_body: str) -> dict:
    """Grava o texto do editor. O bloco do painel é mantido; linhas `exec` não podem mudar."""
    if not isinstance(new_body, str) or len(new_body) > MAX_TEXT:
        raise ConfError("Texto inválido ou grande demais.")
    path = _path(name)
    with _lock:
        text = _read(path)
        body, block = _split_block(text)
        new_body = new_body.replace("\r\n", "\n")
        if BEGIN in new_body or END in new_body:
            new_body = strip_block(new_body)
        if _exec_lines(new_body) != _exec_lines(body):
            raise ConfError("Linhas com exec (que rodam comandos no PC) não podem ser criadas ou alteradas pelo painel.")
        out = new_body.rstrip("\n") + "\n"
        if block:
            out += "\n" + block
        if out != text:
            _write(path, out)
    return state(path.name)
