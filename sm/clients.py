"""Quem está acessando o painel: de onde é o IP e que aparelho é.

- Origem: este PC, rede local, VPN (Tailscale/CGNAT), link-local ou internet (pelo endereço).
- Nome do aparelho: DNS reverso (com nss-mdns/systemd-resolved também mDNS/LLMNR), em segundo plano e em cache.
- MAC: tabela ARP do kernel (/proc/net/arp; só para IPv4 da mesma rede). Celulares costumam usar MAC aleatório por rede
  (bit "administrado localmente"), então o MAC identifica o aparelho naquela rede, não o fabricante.
- Nada é enviado para fora: a localização de um IP da internet fica só como link para o usuário abrir.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psutil

CGNAT = ipaddress.ip_network("100.64.0.0/10")    # Tailscale e operadoras (CGNAT)
KINDS = {"pc": "Este PC", "lan": "Rede local", "vpn": "VPN (Tailscale/CGNAT)", "link-local": "Rede local (link-local)",
         "internet": "Internet", "?": "Desconhecido"}


def _addr(ip: str):
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return None
    return getattr(a, "ipv4_mapped", None) or a


def _own_ips() -> set[str]:
    out = set()
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family in (socket.AF_INET, socket.AF_INET6):
                out.add(a.address.split("%")[0])
    return out


def kind(ip: str) -> str:
    a = _addr(ip)
    if a is None:
        return "?"
    if a.is_loopback or str(a) in _own_ips():
        return "pc"
    if a.version == 4 and a in CGNAT:
        return "vpn"
    if a.is_link_local:
        return "link-local"
    if a.is_private:
        return "lan"
    return "internet"


# ---------- MAC pela tabela ARP ----------
ARP_FILE = "/proc/net/arp"


def arp_table() -> dict[str, str]:
    """IPv4 -> MAC (aa:bb:cc:dd:ee:ff) das entradas válidas da tabela ARP."""
    out = {}
    try:
        with open(ARP_FILE) as f:
            next(f, None)                                   # cabeçalho
            for line in f:
                c = line.split()   # IP address, HW type, Flags, HW address, Mask, Device
                if len(c) < 4 or c[2] == "0x0" or c[3] in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
                    continue                                # 0x0 = incompleta
                out[c[0]] = c[3].lower()
    except OSError:
        pass
    return out


def mac_random(mac: str | None) -> bool:
    return bool(mac) and bool(int(mac[:2], 16) & 0x02)


# ---------- nome pelo DNS reverso (em cache) ----------
_names: dict[str, tuple[str | None, float]] = {}
_pending: dict[str, threading.Event] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clients-dns")
NAME_TTL = 1800


def _lookup(ip: str, ev: threading.Event) -> None:
    name = None
    try:
        name = socket.gethostbyaddr(ip)[0]
        if name == ip:
            name = None
    except (OSError, UnicodeError):
        pass
    with _lock:
        _names[ip] = (name, time.time())
        _pending.pop(ip, None)
    ev.set()


def hostname(ip: str, wait: float = 0) -> str | None:
    """Nome do aparelho, se já conhecido; dispara a busca em segundo plano (e espera até `wait` s)."""
    with _lock:
        hit = _names.get(ip)
        if hit and time.time() - hit[1] < NAME_TTL:
            return hit[0]
        ev = _pending.get(ip)
        if ev is None:
            ev = _pending[ip] = threading.Event()
            _pool.submit(_lookup, ip, ev)
    if wait:
        ev.wait(wait)
        with _lock:
            hit = _names.get(ip) or hit
    return hit[0] if hit else None


def describe(ip: str, wait: float = 0) -> dict:
    a = _addr(ip)
    ip = str(a) if a else ip
    k = kind(ip)
    mac = arp_table().get(ip) if k in ("lan", "link-local", "vpn") and a and a.version == 4 else None
    return {"ip": ip, "kind": k, "origin": KINDS[k], "host": None if k == "pc" else hostname(ip, wait),
            "mac": mac, "mac_random": mac_random(mac)}


def device(ua: str) -> str:
    """Resumo do User-Agent: 'Android · Chrome', 'Linux · Firefox'..."""
    os_ = next((n for k, n in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
                               ("Mac OS", "Mac"), ("CrOS", "ChromeOS"), ("Linux", "Linux")) if k in ua), "dispositivo")
    br = next((n for k, n in (("Firefox", "Firefox"), ("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"),
                              ("Chrome", "Chrome"), ("Safari", "Safari")) if k in ua), "navegador")
    return f"{os_} · {br}"


def summary(ip: str, ua: str, wait: float = 0) -> str:
    """Uma linha para notificações: 'Android · Chrome — 192.168.1.23 (galaxy-s24, rede local)'."""
    d = describe(ip, wait)
    where = ", ".join(x for x in (d["host"], d["origin"].lower()) if x)
    return f"{device(ua)} — {d['ip']} ({where})"
