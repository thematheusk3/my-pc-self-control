"""Versão do painel × GitHub: mostra o changelog e avisa quando há versão nova.

Só consulta: não baixa nem instala nada. Compara `__version__` com o de `sm/__init__.py` no repositório. Quem atualiza é
o usuário (ver ATUALIZAR.md). A checagem acontece só quando alguém abre o painel, no máximo a cada CHECK_EVERY, e pode
ser desligada em [update] check = false. (Atualizações do sistema ficam em updates.py.)
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from .config import ROOT

log = logging.getLogger(__name__)
CHANGELOG = ROOT / "CHANGELOG.md"
CHECK_EVERY = 6 * 3600
MIN_GAP = 30               # s entre verificações pedidas à mão
MAX_BYTES = 1_000_000
TIMEOUT = 8

# o que muda entre as versões Windows e Linux; o frontend só exibe
UPDATE_STEPS = ["/opt/applications/self-monitoring/atualizar.sh"]
UPDATE_NOTE = "No terminal do PC (a cópia instalada acha o clone sozinha e pede a senha do sudo). Sua configuração, senha e históricos são mantidos."


def parse_version(v: str | None) -> tuple[int, ...] | None:
    """'0.6.0-win' -> (0, 6, 0). Sufixo ignorado; None se não for uma versão."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", v or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def compare(installed: str, latest: str | None) -> str:
    a, b = parse_version(installed), parse_version(latest)
    if a is None or b is None:
        return "unknown"
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return "update" if b > a else "ahead" if b < a else "current"


def remote_version(init_py: str) -> str | None:
    m = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", init_py, re.M)
    return m.group(1) if m else None


def parse_changelog(text: str) -> list[dict]:
    """CHANGELOG.md -> [{version, date, items}]. Cada item junta as linhas de um tópico ou parágrafo."""
    out: list[dict] = []
    item: str | None = None

    def flush() -> None:
        nonlocal item
        if item is not None and out:
            out[-1]["items"].append(item.strip())
        item = None

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            # "## 0.5.2 — 2026-11-08": a data é opcional
            m = re.match(r"(\S+)(?:\s+[—–-]\s+(\d{4}-\d{2}-\d{2}))?", line[3:].strip())
            out.append({"version": m.group(1) if m else line[3:].strip(), "date": m.group(2) if m else None, "items": []})
        elif not out or line.startswith("# "):
            continue
        elif line.startswith("- "):
            flush()
            item = line[2:]
        elif line.strip():
            item = f"{item} {line.strip()}" if item is not None else line.strip()
        else:
            flush()
    flush()
    return out


class SelfUpdate:
    def __init__(self, repo: str, enabled: bool = True, branch: str = "main", now=time.time) -> None:
        self.repo, self.enabled, self.branch, self.now = repo, enabled, branch, now
        self.latest: str | None = None
        self.remote_log: list[dict] | None = None
        self.checked = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()
        self._busy = False

    def _get(self, path: str) -> str:
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "self-monitoring"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read(MAX_BYTES).decode("utf-8", "replace")

    def check(self) -> None:
        """Consulta o GitHub. Em falha mantém o último resultado e guarda o motivo."""
        with self._lock:
            if self._busy:
                return
            self._busy = True
        try:
            latest = remote_version(self._get("sm/__init__.py"))
            if not latest:
                raise ValueError("versão não encontrada no repositório")
            self.latest, self.error = latest, None
            try:
                self.remote_log = parse_changelog(self._get("CHANGELOG.md")) or self.remote_log
            except (OSError, ValueError) as e:     # a versão vale mesmo sem o changelog novo
                log.debug("changelog do GitHub indisponível: %s", e)
        except urllib.error.HTTPError as e:
            self.error = f"O GitHub respondeu com erro {e.code}."
        except (OSError, ValueError) as e:
            log.debug("verificação de versão falhou: %s", e)
            self.error = "Não foi possível consultar o GitHub. Verifique a internet do PC."
        finally:
            self.checked = self.now()
            self._busy = False

    def check_now(self) -> None:
        if self.enabled and self.now() - self.checked >= MIN_GAP:
            self.check()

    def _local_log(self) -> list[dict]:
        try:
            return parse_changelog(CHANGELOG.read_text(encoding="utf-8"))
        except OSError:
            return []

    def status(self) -> dict:
        checking = self._busy
        if self.enabled and not checking and self.now() - self.checked >= CHECK_EVERY:
            threading.Thread(target=self.check, daemon=True, name="selfupdate").start()
            checking = True
        entries = self.remote_log or self._local_log()
        base = f"https://github.com/{self.repo}"
        return {
            "installed": __version__, "latest": self.latest, "enabled": self.enabled, "checking": checking,
            "state": "off" if not self.enabled else compare(__version__, self.latest),
            "checked": self.checked or None, "error": self.error,
            "repo_url": base, "changelog_url": f"{base}/blob/{self.branch}/CHANGELOG.md",
            "guide_url": f"{base}/blob/{self.branch}/ATUALIZAR.md",
            "steps": UPDATE_STEPS, "note": UPDATE_NOTE,
            "changelog": [{**e, "new": compare(__version__, e["version"]) == "update",
                           "installed": compare(__version__, e["version"]) == "current"} for e in entries],
        }
