"""Análise de processos e heurísticas de "pontos de atenção".

Nada aqui é um antivírus: são sinais comuns em malware para Linux (binário apagado,
executando de /tmp, disfarçado de thread do kernel, minerador, shell reverso...).
Cada sinal tem um nível: alto, médio ou info. Programas legítimos também podem
disparar alguns deles. O objetivo é chamar a atenção para investigar.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil

from .config import ROOT
from .desktop import HOME   # rodando como root: a pasta do dono da área de trabalho, não /root

TRUST_FILE = ROOT / "data" / "trusted.json"

# lugares onde executáveis normalmente NÃO deveriam estar
TEMP_DIRS = ("/tmp/", "/var/tmp/", "/dev/shm/", "/run/shm/")
SYSTEM_DIRS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/", "/etc/", "/boot/")
# diretórios ocultos do usuário onde apps legítimos ficam (Steam, Flatpak, Discord, pip --user...)
HOME_APP_DIRS = (".local/", ".config/", ".var/app/", ".steam/", ".cache/", ".cargo/", ".rustup/",
                 ".npm/", ".nvm/", ".pyenv/", ".vscode/", ".mozilla/", ".wine/")
SHELLS = {"sh", "bash", "dash", "zsh", "fish", "ksh"}
INTERPRETERS = SHELLS | {"python", "python3", "perl", "ruby", "node", "php", "lua"}
# LD_PRELOAD que costuma ser legítimo
PRELOAD_OK = ("libmozsandbox", "gameoverlayrenderer", "libgamemode", "mangohud", "libvkbasalt", "libstrangle",
              "libdlfaker", "libgtk3-nocsd", "libjemalloc", "libtcmalloc")

CMD_RULES: list[tuple[str, str, re.Pattern]] = [
    ("alto", "Parece um shell reverso (envia um terminal pela rede)",
     re.compile(r"/dev/(tcp|udp)/|\b(nc|ncat|netcat)\b.*\s-[a-z]*e\s|bash\s+-i\s*>&|socat\b.*exec:|"
                r"\bmkfifo\b.*\b(nc|ncat)\b|pty\.spawn", re.I)),
    ("alto", "Baixa algo da internet e executa direto no shell",
     re.compile(r"\b(curl|wget)\b[^|;]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", re.I)),
    ("alto", "Decodifica base64 e executa",
     re.compile(r"base64\s+(-d|--decode)[^|]*\|\s*(ba|z|da)?sh\b|echo\s+[A-Za-z0-9+/=]{80,}\s*\|", re.I)),
    ("alto", "Parece um minerador de criptomoeda",
     re.compile(r"stratum\+(tcp|ssl)://|\bxmrig\b|\bcpuminer\b|\bminerd\b|--donate-level|\bnicehash\b|"
                r"\bcryptonight\b|\brandomx\b", re.I)),
    ("medio", "Código Python/Perl executado inline com rede/sockets",
     re.compile(r"\b(python3?|perl)\b\s+-[ce]\s.*\b(socket|subprocess|os\.system|exec\()", re.I)),
    ("medio", "Apaga o histórico do shell ou rastros",
     re.compile(r"history\s+-c|unset\s+HISTFILE|HISTFILE=/dev/null|shred\s+.*\.(bash|zsh)_history", re.I)),
]

LEVEL_SCORE = {"alto": 50, "medio": 15, "info": 0}


# ---------- pacote dono do arquivo ----------
class PackageDB:
    """Descobre qual pacote instalou um arquivo (pacman, dpkg ou rpm), com cache."""

    def __init__(self) -> None:
        self.cache: dict[str, str | None] = {}
        self.lock = threading.Lock()
        self.tool = next((t for t in ("pacman", "dpkg-query", "rpm") if shutil.which(t)), None)

    def owners(self, paths: list[str]) -> dict[str, str | None]:
        paths = [p for p in dict.fromkeys(paths) if p]
        with self.lock:
            todo = [p for p in paths if p not in self.cache]
        if todo and self.tool:
            found = self._lookup(todo)
            with self.lock:
                for p in todo:
                    self.cache[p] = found.get(p)
        with self.lock:
            return {p: self.cache.get(p) for p in paths}

    def describe(self, pkg: str | None) -> dict | None:
        """Descrição, versão e site do pacote."""
        if not pkg or not self.tool:
            return None
        key = "desc:" + pkg
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        info: dict = {}
        if self.tool == "pacman":
            text = self._run(["pacman", "-Qi", pkg])
            fields = {"Description": "description", "Version": "version", "URL": "url", "Install Date": "installed"}
        elif self.tool == "dpkg-query":
            text = self._run(["dpkg-query", "-W", "-f=Description: ${binary:Summary}\nVersion: ${Version}\nURL: ${Homepage}\n", pkg])
            fields = {"Description": "description", "Version": "version", "URL": "url"}
        else:
            text = self._run(["rpm", "-qi", pkg])
            fields = {"Summary": "description", "Version": "version", "URL": "url", "Install Date": "installed"}
        for line in text.splitlines():
            k, _, v = line.partition(":")
            if k.strip() in fields and v.strip() and fields[k.strip()] not in info:
                info[fields[k.strip()]] = v.strip()
        info = info or None
        with self.lock:
            self.cache[key] = info
        return info

    def expected_digest(self, pkg: str, path: str) -> tuple[str, str] | None:
        """(algoritmo, hash) do arquivo como o pacote o instalou, lido do banco do gerenciador de pacotes
        (pacman: mtree; dpkg: md5sums; rpm: --dump). None = o pacote não registra esse arquivo."""
        real = os.path.realpath(path)
        key = f"dig:{pkg}:{real}"
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        found = None
        try:
            if self.tool == "pacman":
                found = _pacman_digest(pkg, real)
            elif self.tool == "dpkg-query":
                found = _dpkg_digest(pkg, real)
            elif self.tool == "rpm":
                for line in self._run(["rpm", "-q", "--dump", pkg]).splitlines():
                    f = line.split(" ")
                    if len(f) > 3 and f[0] in (real, path) and len(f[3]) in (32, 64):
                        found = ("md5" if len(f[3]) == 32 else "sha256", f[3])
                        break
        except (OSError, ValueError):
            found = None
        with self.lock:
            self.cache[key] = found
        return found

    def _run(self, args: list[str]) -> str:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=20,
                                  env={**os.environ, "LC_ALL": "C"}).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def _lookup(self, paths: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for i in range(0, len(paths), 200):
            chunk = paths[i:i + 200]
            if self.tool == "pacman":
                for line in self._run(["pacman", "-Qo", *chunk]).splitlines():
                    m = re.match(r"^(.*) is owned by (\S+) ", line)
                    if m:
                        out[m.group(1)] = m.group(2)
            elif self.tool == "dpkg-query":
                for line in self._run(["dpkg-query", "-S", *chunk]).splitlines():
                    pkg, _, path = line.partition(": ")
                    if path in chunk:
                        out[path] = pkg.split(":")[0]
            else:
                for p in chunk:
                    r = self._run(["rpm", "-qf", "--qf", "%{NAME}\n", p]).strip()
                    if r and "not owned" not in r:
                        out[p] = r
        # o pacote pode registrar o caminho com symlink (/bin -> /usr/bin): tenta o caminho real
        miss = [p for p in paths if p not in out]
        alt = {os.path.realpath(p): p for p in miss if os.path.realpath(p) != p}
        if alt:
            for real, pkg in self._lookup(list(alt)).items():
                out[alt[real]] = pkg
        return out


def _mtree_unescape(p: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), p)


def _pacman_digest(pkg: str, real: str) -> tuple[str, str] | None:
    import gzip
    for d in Path("/var/lib/pacman/local").glob(f"{glob_escape(pkg)}-*"):
        desc = (d / "desc").read_text(errors="replace").split("\n")
        if len(desc) < 2 or desc[0] != "%NAME%" or desc[1] != pkg:
            continue
        with gzip.open(d / "mtree", "rt", errors="replace") as f:
            for line in f:
                parts = line.split()
                if parts and _mtree_unescape(parts[0])[1:] == real:
                    for kv in parts[1:]:
                        if kv.startswith("sha256digest="):
                            return "sha256", kv.split("=", 1)[1]
                        if kv.startswith("md5digest="):
                            return "md5", kv.split("=", 1)[1]
        return None
    return None


def _dpkg_digest(pkg: str, real: str) -> tuple[str, str] | None:
    rel = real.lstrip("/")
    for f in Path("/var/lib/dpkg/info").glob(f"{glob_escape(pkg)}*.md5sums"):
        if f.name.split(".md5sums")[0].split(":")[0] != pkg:
            continue
        for line in f.read_text(errors="replace").splitlines():
            digest, _, fpath = line.partition("  ")
            if fpath in (rel, rel.removeprefix("usr/")):
                return "md5", digest
    return None


def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", s)


def verify_package_file(pkg: str | None, path: str, sha256: str | None) -> str | None:
    """"ok" se o arquivo é igual ao que o pacote instalou, "changed" se foi alterado depois, None se não deu
    para conferir. É o equivalente Linux a conferir a assinatura digital do executável no Windows."""
    if not pkg or not PKG.tool:
        return None
    exp = PKG.expected_digest(pkg, path)
    if not exp:
        return None
    algo, digest = exp
    if algo == "sha256":
        if not sha256:
            return None
        return "ok" if sha256 == digest.lower() else "changed"
    h = hashlib.md5(usedforsecurity=False)
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return "ok" if h.hexdigest() == digest.lower() else "changed"


PKG = PackageDB()


# ---------- hash ----------
_hash_cache: dict[tuple, str] = {}


def sha256_of(path: str, max_bytes: int = 512 * 1024 * 1024) -> tuple[str | None, str | None]:
    try:
        st = os.stat(path)
    except OSError as e:
        return None, f"não foi possível ler: {e.strerror}"
    if st.st_size > max_bytes:
        return None, "arquivo grande demais para calcular o hash"
    key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    if key in _hash_cache:
        return _hash_cache[key], None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        return None, f"não foi possível ler: {e.strerror}"
    _hash_cache[key] = h.hexdigest()
    return _hash_cache[key], None


# ---------- confiáveis ----------
def load_trusted() -> set[str]:
    import json
    try:
        return set(json.loads(TRUST_FILE.read_text()))
    except (OSError, ValueError):
        return set()


def set_trusted(exe: str, trusted: bool) -> set[str]:
    import json
    t = load_trusted()
    (t.add if trusted else t.discard)(exe)
    TRUST_FILE.write_text(json.dumps(sorted(t)))
    os.chmod(TRUST_FILE, 0o600)
    return t


# ---------- heurísticas ----------
def clean_exe(exe: str | None) -> tuple[str | None, bool]:
    if not exe:
        return None, False
    if exe.endswith(" (deleted)"):
        return exe[:-10], True
    return exe, False


def guess_exe(rec: dict) -> str | None:
    """Sem root não dá para ler /proc/<pid>/exe de outro usuário: deduz pelo comando."""
    a0 = (rec.get("argv0") or "").split(" ")[0].rstrip(":")
    if not a0 or a0.startswith("["):
        return None
    if a0.startswith("/"):
        return a0 if os.path.exists(a0) else None
    return shutil.which(a0)


def in_home_app_dir(path: str) -> bool:
    try:
        rel = Path(path).relative_to(HOME).as_posix()
    except ValueError:
        return False
    return rel.startswith(HOME_APP_DIRS)


def flags_for(rec: dict, pkg: str | None, extra: dict | None = None) -> list[dict]:
    """Sinais a partir do registro (vivo ou encerrado). `extra` traz dados de análise ao vivo."""
    extra = extra or {}
    out: list[dict] = []
    add = lambda lvl, txt: out.append({"level": lvl, "text": txt})  # noqa: E731
    exe, deleted = clean_exe(rec.get("exe"))
    verified = rec.get("exe_verified", False)
    if not exe:
        exe = guess_exe(rec)
    name = rec.get("name") or ""
    cmd = rec.get("cmd") or ""
    user = rec.get("user")

    # binário apagado / em memória
    if rec.get("exe", "") and "memfd:" in rec.get("exe", ""):
        add("alto", "Executa a partir da memória (memfd), sem arquivo no disco: técnica comum para esconder malware")
    elif deleted:
        if pkg:
            add("info", f"O binário foi atualizado pelo pacote {pkg} depois que o processo iniciou; reinicie-o para usar a versão nova")
        else:
            add("alto", "O executável foi apagado do disco enquanto o processo roda")

    # disfarce de thread do kernel
    a0 = (rec.get("argv0") or cmd.split(" ")[0] or "").strip()
    if a0.startswith("[") and a0.rstrip(":").endswith("]") or name.startswith("["):
        add("alto", "Nome entre colchetes, imitando uma thread do kernel, mas é um processo comum")

    if exe:
        base = os.path.basename(exe)
        if exe.startswith(TEMP_DIRS):
            add("alto", f"Executável em pasta temporária ({os.path.dirname(exe)})")
        if base.startswith("."):
            add("alto", "O executável é um arquivo oculto (nome começa com ponto)")
        hidden = any(part.startswith(".") for part in Path(exe).parts[1:-1])
        if hidden and not rec.get("container") and not in_home_app_dir(exe) and not exe.startswith(TEMP_DIRS):
            if exe.startswith(str(HOME) + "/"):
                add("info", "Executável dentro de uma pasta oculta da home (comum em ferramentas instaladas por script)")
            else:
                add("medio", "Executável dentro de uma pasta oculta fora da home")
        if exe.startswith(str(HOME / "Downloads")) or exe.startswith(str(HOME / "Transferências")):
            add("info", "Executado direto da pasta Downloads")
        if rec.get("container"):
            add("info", "Roda dentro de um container/sandbox (Flatpak, Steam Runtime, Docker): o caminho é de dentro dele")
        elif pkg is None and PKG.tool and not verified:
            add("info", "Executável deduzido pelo comando (sem root não dá para confirmar o arquivo real)")
        elif pkg is None and PKG.tool:
            if exe.startswith(SYSTEM_DIRS):
                add("medio", "Arquivo em pasta do sistema que não pertence a nenhum pacote instalado")
            elif user == "root" and not exe.startswith("/opt/"):
                add("medio", "Roda como root a partir de um executável que não é de nenhum pacote")
            else:
                add("info", "Não pertence a nenhum pacote do sistema (app instalado manualmente, AppImage, Steam, etc.)")

    # script de pasta temporária rodando em interpretador
    if os.path.basename(exe or name) in INTERPRETERS or name in INTERPRETERS:
        # o script é o primeiro argumento que não é opção (python -u /tmp/x.py, bash /dev/shm/y)
        script = next((a for a in cmd.split()[1:] if not a.startswith("-")), "")
        if "-c" not in cmd.split()[1:2] and script.startswith(TEMP_DIRS):
            add("alto", "Interpretador executando um script de pasta temporária")

    for lvl, txt, rx in CMD_RULES:
        if rx.search(cmd):
            add(lvl, txt)

    if extra.get("package_check") == "changed":
        add("alto", f"O executável é diferente do que o pacote {pkg} instalou: foi alterado depois da instalação "
                    "(substituído à mão, corrompido ou adulterado). Reinstale o pacote para restaurar o original")

    # dados ao vivo
    preload = extra.get("ld_preload")
    if preload:
        libs = [lib for lib in re.split(r"[:\s]+", preload) if lib]
        bad = [lib for lib in libs if not any(ok in lib for ok in PRELOAD_OK)]
        if bad:
            add("medio", f"LD_PRELOAD injeta biblioteca no processo: {', '.join(bad)[:200]}")
        else:
            add("info", f"LD_PRELOAD conhecido: {', '.join(os.path.basename(b) for b in libs)[:200]}")
    for lp in extra.get("listening", []):
        if lp["public"] and pkg is None:
            add("medio", f"Aceita conexões de outras máquinas na porta {lp['port']} e não é de nenhum pacote")
            break
    if rec.get("peak_cpu", 0) >= 50 and pkg is None and exe and not in_home_app_dir(exe):
        add("info", f"Uso alto de CPU ({rec['peak_cpu']:.0f}%) por programa fora dos pacotes")
    return out


def score(flags: list[dict]) -> int:
    return sum(LEVEL_SCORE[f["level"]] for f in flags)


# ---------- análise detalhada ----------
def _connections(p: psutil.Process) -> tuple[list[dict], list[dict]]:
    listening, remote = [], []
    try:
        conns = p.net_connections(kind="inet")
    except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
        try:
            conns = p.connections(kind="inet")
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            return listening, remote
    for c in conns:
        if c.status == psutil.CONN_LISTEN and c.laddr:
            ip = c.laddr.ip
            listening.append({"ip": ip, "port": c.laddr.port,
                              "public": ip in ("0.0.0.0", "::") or not (ip.startswith("127.") or ip == "::1")})
        elif c.raddr:
            remote.append({"ip": c.raddr.ip, "port": c.raddr.port, "status": c.status,
                           "proto": "tcp" if c.type == 1 else "udp"})
    uniq = {(x["ip"], x["port"]): x for x in listening}
    return sorted(uniq.values(), key=lambda x: x["port"]), remote[:50]


def analyze_live(pid: int, create_time: float | None, base: dict | None) -> dict:
    p = psutil.Process(pid)
    if create_time is not None and abs(p.create_time() - float(create_time)) > 1:
        raise LookupError("O processo já terminou (PID reaproveitado).")
    with p.oneshot():
        rec = dict(base or {})
        rec.setdefault("pid", pid)
        rec["name"] = p.name()
        rec["ppid"] = p.ppid()
        try:
            rec["cmd"] = " ".join(p.cmdline())[:2000]
            rec["argv0"] = (p.cmdline() or [None])[0]
        except psutil.AccessDenied:
            pass
        try:
            rec["user"] = p.username()
        except (psutil.AccessDenied, KeyError):
            pass
        try:
            rec["exe"] = os.readlink(f"/proc/{pid}/exe")
            rec["exe_verified"] = True
        except OSError:
            rec.setdefault("exe", None)
            rec["exe_verified"] = False
        rec["start"] = p.create_time()
        info: dict = {"alive": True}
        for key, fn in (("cwd", p.cwd), ("threads", p.num_threads), ("fds", p.num_fds),
                        ("status", p.status), ("terminal", p.terminal), ("nice", p.nice)):
            try:
                info[key] = fn()
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                info[key] = None
        try:
            mi = p.memory_info()
            info["rss"] = mi.rss
        except psutil.AccessDenied:
            info["rss"] = None
        try:
            env = p.environ()
            info["ld_preload"] = env.get("LD_PRELOAD")
            info["env_readable"] = True
        except (psutil.AccessDenied, OSError):
            info["ld_preload"] = None
            info["env_readable"] = False
        try:
            info["children"] = len(p.children())
        except psutil.Error:
            info["children"] = None
    info["listening"], info["remote"] = _connections(p)
    from .proctrack import in_container, read_unit
    rec["unit"] = read_unit(pid)
    rec["container"] = in_container(pid)
    return _finish(rec, info, hash_target=f"/proc/{pid}/exe" if rec.get("exe_verified") else None)


def analyze_record(rec: dict) -> dict:
    """Processo encerrado: só o que foi capturado + análise do arquivo, se ainda existir."""
    return _finish(dict(rec), {"alive": False, "listening": [], "remote": []}, hash_target=None)


def _finish(rec: dict, info: dict, hash_target: str | None) -> dict:
    exe, deleted = clean_exe(rec.get("exe"))
    guessed = False
    if not exe:
        exe = guess_exe(rec)
        guessed = exe is not None
    pkg = PKG.owners([exe])[exe] if exe else None
    file = None
    if exe and not exe.startswith("/memfd"):
        target = hash_target or exe
        sha, err = sha256_of(target)
        try:
            st = os.stat(exe)
            file = {"path": exe, "size": st.st_size, "mtime": st.st_mtime, "owner_uid": st.st_uid,
                    "owner": _user(st.st_uid), "mode": oct(st.st_mode & 0o7777), "exists": True}
        except OSError:
            file = {"path": exe, "exists": False}
        file["sha256"], file["hash_error"] = sha, err
        if deleted and hash_target:
            file["note"] = "hash do binário em memória (o arquivo foi apagado)"
        if not sha and rec.get("sha256"):     # capturado quando o processo apareceu
            file["sha256"], file["hash_error"] = rec["sha256"], None
            file["note"] = "hash calculado quando o processo rodava"
    check = None
    if pkg and file and file.get("exists") and not deleted:
        check = verify_package_file(pkg, exe, file.get("sha256"))   # sem "(deleted)": /proc/PID/exe é o próprio arquivo
    flags = flags_for(rec, pkg, {**info, "package_check": check})
    trusted = exe in load_trusted() if exe else False
    return {
        "process": rec, "info": info, "exe": exe, "exe_guessed": guessed, "deleted": deleted,
        "package": pkg, "package_info": PKG.describe(pkg), "package_tool": PKG.tool, "file": file,
        "package_check": check,
        "flags": flags, "score": score(flags), "trusted": trusted,
        "visibility": "full" if os.geteuid() == 0 or rec.get("exe_verified") else "limited",
    }


def _user(uid: int) -> str:
    import pwd
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


# ---------- lista de suspeitos ----------
_susp_cache: dict = {"ts": 0, "data": None}
_susp_lock = threading.Lock()


def suspicious(tracker, include_info: bool = False) -> dict:
    """Varre os processos vivos e encerrados com heurísticas baratas (sem hash)."""
    with _susp_lock:
        if time.time() - _susp_cache["ts"] < 3 and _susp_cache["data"] and not include_info:
            return _susp_cache["data"]
        with tracker._lock:
            live = [dict(r) for r in tracker.live.values()]
            dead = [dict(r) for r in tracker.exited]
        for r in live:
            r["_alive"] = True
            if r.get("exe_verified"):   # relê: o arquivo pode ter sido apagado depois que o processo iniciou
                try:
                    r["exe"] = os.readlink(f"/proc/{r['pid']}/exe")
                except OSError:
                    pass
        for r in dead:
            r["_alive"] = False
        allrecs = live + dead
        exes = {}
        for r in allrecs:
            exe, deleted = clean_exe(r.get("exe"))
            exes[r["id"]] = exe or guess_exe(r)
        owners = PKG.owners([e for e in exes.values() if e])
        trusted = load_trusted()
        items = []
        for r in allrecs:
            exe = exes[r["id"]]
            if exe and exe in trusted:
                continue
            extra = {}
            if r["_alive"]:
                env = _read_preload(r["pid"])
                if env:
                    extra["ld_preload"] = env
            fl = flags_for(r, owners.get(exe) if exe else None, extra)
            sc = score(fl)
            if sc == 0 and not include_info:
                continue
            if not fl:
                continue
            items.append({
                "id": r["id"], "pid": r["pid"], "name": r["name"], "user": r["user"],
                "cmd": r["cmd"], "exe": exe, "alive": r["_alive"], "start": r["start"],
                "end": r.get("end"), "create_time": r["start"],
                "flags": fl, "score": sc,
            })
        # agrupa repetições do mesmo executável + comando
        grouped: dict[tuple, dict] = {}
        for it in sorted(items, key=lambda x: (x["alive"], x["start"]), reverse=True):
            k = (it["exe"], it["cmd"])
            if k in grouped:
                grouped[k]["count"] += 1
            else:
                grouped[k] = {**it, "count": 1}
        res = sorted(grouped.values(), key=lambda x: (-x["score"], not x["alive"], -x["start"]))
        data = {"items": res, "trusted": sorted(trusted), "package_tool": PKG.tool,
                "limited": os.geteuid() != 0}
        if not include_info:
            _susp_cache.update(ts=time.time(), data=data)
        return data


def _read_preload(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            for item in f.read(65536).split(b"\0"):
                if item.startswith(b"LD_PRELOAD="):
                    return item[11:].decode(errors="replace")
    except OSError:
        pass
    return None
