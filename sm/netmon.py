"""Monitor de conexões de rede: quem conecta, para onde, quanto envia e recebe.

Fonte: `ss` (iproute2). Sem root:
  - bytes enviados/recebidos de cada conexão TCP: SIM (tcp_info não exige privilégio)
  - qual processo é dono do socket: só para processos do mesmo usuário do serviço
  - UDP (inclui QUIC/HTTP3 dos navegadores): conexões sim, bytes não
  - o CONTEÚDO não: quase tudo é criptografado (HTTPS/TLS). Ver pacotes exige root + captura
    (Wireshark/tcpdump), e ver dentro do HTTPS exige um proxy com certificado próprio (mitmproxy).
Nome dos hosts: DNS reverso (PTR) do IP, em segundo plano e com cache.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .config import ROOT

log = logging.getLogger(__name__)
CLOSED_FILE = ROOT / "data" / "connections.json"

SERVICES = {
    20: "FTP", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 67: "DHCP", 68: "DHCP",
    80: "HTTP", 110: "POP3", 123: "NTP", 143: "IMAP", 443: "HTTPS", 465: "SMTPS", 587: "SMTP",
    853: "DNS-over-TLS", 993: "IMAPS", 995: "POP3S", 1900: "SSDP/UPnP", 3478: "STUN/WebRTC",
    5228: "Google Play/FCM", 5353: "mDNS", 5355: "LLMNR", 1716: "KDE Connect", 4070: "Spotify",
    5222: "XMPP", 8080: "HTTP alt", 8443: "HTTPS alt", 27015: "Steam", 27036: "Steam",
    27017: "MongoDB", 3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis", 3389: "RDP", 5900: "VNC",
    50000: "Discord voz",
}
# portas muito usadas por mineradores, backdoors e botnets (sinal fraco, só para chamar atenção)
SUSPICIOUS_PORTS = {3333: "mineração (stratum)", 4444: "backdoor/minerador", 5555: "minerador/ADB",
                    6666: "IRC/botnet", 6667: "IRC/botnet", 7777: "minerador", 14444: "mineração (stratum)",
                    14433: "mineração (stratum)", 45700: "mineração", 1337: "backdoor", 31337: "backdoor"}

# estados de encerramento/abertura que duram pouco: não viram "conexão"
TRANSIENT = {"TIME-WAIT", "FIN-WAIT-1", "FIN-WAIT-2", "LAST-ACK", "CLOSING", "SYN-RECV"}
_ADDR = re.compile(r"^\[?(.*?)\]?(?:%[\w.-]+)?:(\d+|\*)$")
_PROC = re.compile(r'\("([^"]*)",pid=(\d+),fd=\d+\)')
_KV = re.compile(r"\b(bytes_acked|bytes_sent|bytes_received):(\d+)")


def split_addr(s: str) -> tuple[str, int | None]:
    m = _ADDR.match(s)
    if not m:
        return s, None
    ip = m.group(1).split("%")[0]
    return ip, None if m.group(2) == "*" else int(m.group(2))


def classify(ip: str) -> str:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "?"
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    if a.is_loopback:
        return "local"
    if a.is_multicast:
        return "multicast"
    if a.is_link_local:
        return "link-local"
    if a.is_private:
        return "lan"
    if a.is_unspecified:
        return "any"
    return "internet"


def service(port: int | None, proto: str) -> str | None:
    if port is None:
        return None
    if port in SERVICES:
        return SERVICES[port]
    if port >= 1024:      # acima disso o /etc/services traz nomes obscuros e enganosos
        return None
    try:
        return socket.getservbyport(port, proto).upper()
    except OSError:
        return None


class Resolver:
    """DNS reverso em segundo plano, com cache."""

    def __init__(self) -> None:
        self.cache: dict[str, tuple[str | None, float]] = {}
        self.pending: set[str] = set()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rdns")
        self.lock = threading.Lock()

    def get(self, ip: str) -> str | None:
        if classify(ip) in ("local", "any", "multicast"):
            return None
        with self.lock:
            hit = self.cache.get(ip)
            if hit and time.time() - hit[1] < 3600:
                return hit[0]
            if ip in self.pending or len(self.pending) > 64:
                return hit[0] if hit else None
            self.pending.add(ip)
        self.pool.submit(self._lookup, ip)
        return hit[0] if hit else None

    def _lookup(self, ip: str) -> None:
        name = None
        try:
            name = socket.gethostbyaddr(ip)[0]
        except (OSError, UnicodeError):
            pass
        with self.lock:
            self.cache[ip] = (name, time.time())
            self.pending.discard(ip)
            if len(self.cache) > 5000:
                for k in list(self.cache)[:1000]:
                    del self.cache[k]


def run_ss(args: list[str]) -> list[str]:
    try:
        return subprocess.run(["ss", "-H", "-O", "-n", "-p", *args], capture_output=True, text=True,
                              timeout=10).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def parse(line: str, proto: str) -> dict | None:
    parts = line.split()
    if len(parts) < 5:
        return None
    state = parts[0]
    lip, lport = split_addr(parts[3])
    rip, rport = split_addr(parts[4])
    procs = [{"name": n, "pid": int(p)} for n, p in _PROC.findall(line)]
    # um socket pode ser compartilhado por vários processos (fork); mantém PIDs únicos
    seen, uniq = set(), []
    for pr in procs:
        if pr["pid"] not in seen:
            seen.add(pr["pid"])
            uniq.append(pr)
    kv = {k: int(v) for k, v in _KV.findall(line)}
    sent = kv.get("bytes_acked", kv.get("bytes_sent"))
    return {
        "proto": proto, "state": state, "lip": lip, "lport": lport, "rip": rip, "rport": rport,
        "procs": uniq, "sent": sent, "recv": kv.get("bytes_received"),
    }


class NetMonitor:
    def __init__(self, ctx) -> None:
        cfg = ctx.config
        self.interval = cfg.net_interval
        self.persist = cfg.track_persist
        self.resolver = Resolver()
        self.conns: dict[tuple, dict] = {}
        self.listening: list[dict] = []
        self.closed: deque[dict] = deque(maxlen=cfg.net_closed_max)
        self.proc_totals: dict[str, dict] = {}   # nome -> bytes de conexões já encerradas
        self.started_at = time.time()
        self.total_closed = 0
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._load()

    # ---------- persistência ----------
    def _load(self) -> None:
        if not self.persist:
            return
        try:
            self.closed.extend(json.loads(CLOSED_FILE.read_text()).get("closed", [])[-self.closed.maxlen:])
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        if not self.persist:
            return
        try:
            with self._lock:
                data = {"closed": list(self.closed)}
            tmp = CLOSED_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")))
            os.chmod(tmp, 0o600)
            tmp.replace(CLOSED_FILE)
        except OSError as e:
            log.warning("não foi possível salvar o histórico de conexões: %s", e)

    # ---------- laço ----------
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="netmon").start()

    def stop(self) -> None:
        self._stop.set()
        self.save()

    def _loop(self) -> None:
        last_save = time.monotonic()
        while True:
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                log.exception("netmon: falha")
            if self._stop.wait(self.interval):
                return
            if time.monotonic() - last_save > 300:
                self.save()
                last_save = time.monotonic()

    def _tick(self) -> None:
        now = time.time()
        seen: dict[tuple, dict] = {}
        listening = []
        for proto, args in (("tcp", ["-t", "-i", "-a"]), ("udp", ["-u", "-a"])):
            for line in run_ss(args):
                c = parse(line, proto)
                if c is None:
                    continue
                if c["state"] in ("LISTEN",) or (proto == "udp" and c["state"] == "UNCONN" and c["rport"] is None):
                    listening.append({
                        "proto": proto, "ip": c["lip"], "port": c["lport"], "procs": c["procs"],
                        "scope": "rede" if classify(c["lip"]) in ("any", "lan", "internet") else "local",
                        "service": service(c["lport"], proto),
                    })
                    continue
                if c["rport"] is None or c["state"] in TRANSIENT:
                    continue
                key = (proto, c["lip"], c["lport"], c["rip"], c["rport"])
                seen[key] = c
        listen_ports = {(x["proto"], x["port"]) for x in listening}
        merged: dict[tuple, dict] = {}
        for x in listening:   # vários sockets na mesma porta (IPv4/IPv6, fork) viram uma linha
            k = (x["proto"], x["port"], x["scope"])
            m = merged.get(k)
            if m is None:
                merged[k] = {**x, "ips": [x["ip"]]}
            else:
                if x["ip"] not in m["ips"]:
                    m["ips"].append(x["ip"])
                pids = {pr["pid"] for pr in m["procs"]}
                m["procs"] += [pr for pr in x["procs"] if pr["pid"] not in pids]
        listening = list(merged.values())
        with self._lock:
            for key, c in seen.items():
                old = self.conns.get(key)
                if old is None:
                    self._seq += 1
                    kind = classify(c["rip"])
                    old = self.conns[key] = {
                        "id": f"{int(self.started_at)}-{self._seq}",
                        "proto": c["proto"], "lip": c["lip"], "lport": c["lport"],
                        "rip": c["rip"], "rport": c["rport"], "kind": kind,
                        "service": service(c["rport"], c["proto"]) or service(c["lport"], c["proto"]),
                        "first": now, "last": now, "procs": c["procs"],
                        "sent": c["sent"], "recv": c["recv"], "rate_up": 0.0, "rate_down": 0.0,
                        "warn": SUSPICIOUS_PORTS.get(c["rport"]) if kind == "internet" else None,
                        # entrada: alguém conectou numa porta que esta máquina escuta
                        "direction": "entrada" if (c["proto"], c["lport"]) in listen_ports and c["proto"] == "tcp" else "saída",
                    }
                else:
                    dt = max(0.001, now - old["last"])
                    for f, r in (("sent", "rate_up"), ("recv", "rate_down")):
                        if c[f] is not None and old[f] is not None:
                            old[r] = max(0.0, (c[f] - old[f]) / dt)
                        old[f] = c[f]
                    if c["procs"]:
                        old["procs"] = c["procs"]
                old["state"] = c["state"]
                old["last"] = now
            for key in set(self.conns) - set(seen):
                self._close(self.conns.pop(key), now)
            self.listening = sorted(listening, key=lambda x: (x["scope"] != "rede", x["port"] or 0))

    def _close(self, c: dict, now: float) -> None:
        c = dict(c)
        c["end"] = round(now, 2)
        c["duration"] = round(now - c["first"], 2)
        c.pop("rate_up", None)
        c.pop("rate_down", None)
        c["host"] = self.resolver.cache.get(c["rip"], (None, 0))[0]
        self.closed.append(c)
        self.total_closed += 1
        name = c["procs"][0]["name"] if c["procs"] else "?"
        t = self.proc_totals.setdefault(name, {"sent": 0, "recv": 0, "closed": 0})
        t["sent"] += c["sent"] or 0
        t["recv"] += c["recv"] or 0
        t["closed"] += 1

    # ---------- consultas ----------
    def _with_host(self, c: dict) -> dict:
        d = dict(c)
        d["host"] = self.resolver.get(c["rip"])
        return d

    def snapshot(self) -> dict:
        with self._lock:
            conns = [self._with_host(c) for c in self.conns.values()]
            listening = list(self.listening)
            totals = {k: dict(v) for k, v in self.proc_totals.items()}
        procs: dict[str, dict] = {}
        for c in conns:
            name = c["procs"][0]["name"] if c["procs"] else None
            key = name or "?"
            p = procs.setdefault(key, {"name": name, "pids": set(), "conns": 0, "internet": 0,
                                       "rate_up": 0.0, "rate_down": 0.0, "sent": 0, "recv": 0, "hosts": set()})
            p["conns"] += 1
            p["internet"] += c["kind"] == "internet"
            p["rate_up"] += c["rate_up"]
            p["rate_down"] += c["rate_down"]
            p["sent"] += c["sent"] or 0
            p["recv"] += c["recv"] or 0
            p["hosts"].add(c["host"] or c["rip"])
            for pr in c["procs"]:
                p["pids"].add(pr["pid"])
        for name, t in totals.items():
            p = procs.setdefault(name, {"name": None if name == "?" else name, "pids": set(), "conns": 0,
                                        "internet": 0, "rate_up": 0.0, "rate_down": 0.0, "sent": 0,
                                        "recv": 0, "hosts": set()})
            p["sent"] += t["sent"]
            p["recv"] += t["recv"]
        plist = []
        for p in procs.values():
            p["pids"] = sorted(p["pids"])
            p["hosts"] = sorted(p["hosts"])[:20]
            plist.append(p)
        plist.sort(key=lambda p: (-(p["rate_up"] + p["rate_down"]), -(p["sent"] + p["recv"])))
        return {
            "connections": sorted(conns, key=lambda c: -(c["rate_up"] + c["rate_down"])),
            "listening": listening, "processes": plist,
            "interval": self.interval, "since": self.started_at,
            "total_closed": self.total_closed, "limited": os.geteuid() != 0,
        }

    def list_closed(self, limit: int = 3000) -> dict:
        with self._lock:
            items = list(self.closed)[-limit:]
        items.reverse()
        return {"closed": items, "kept": len(items), "max": self.closed.maxlen, "since": self.started_at}

    def clear(self) -> None:
        with self._lock:
            self.closed.clear()
            self.proc_totals.clear()
        self.save()
