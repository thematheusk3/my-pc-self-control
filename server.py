#!/usr/bin/env python3
"""Ponto de entrada: python3 server.py [--port N] [--host H]"""
from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sm import __version__, config  # noqa: E402
from sm.sampler import Context, Sampler  # noqa: E402
from sm.server import make_server  # noqa: E402


def lan_ips() -> list[str]:
    import psutil
    ips = []
    for name, addrs in psutil.net_if_addrs().items():
        if name == "lo" or name.startswith(("docker", "veth", "br-", "virbr")):
            continue
        ips += [a.address for a in addrs if a.family == socket.AF_INET and not a.address.startswith("127.")]
    return ips


def set_password() -> None:
    import getpass
    from sm.auth import MIN_LEN, Auth
    auth = Auth()
    print("Trocar senha do self-monitoring" if auth.has_password() else "Cadastrar senha do self-monitoring")
    while True:
        pw = getpass.getpass("Nova senha: ")
        if len(pw) < MIN_LEN:
            print(f"  A senha precisa ter pelo menos {MIN_LEN} caracteres.")
            continue
        if pw != getpass.getpass("Repita a senha: "):
            print("  As senhas não conferem.")
            continue
        break
    auth.set_password(pw)
    auth.end_all_except(None)
    print("Senha salva. Todas as sessões abertas foram encerradas.")
    print("Se o serviço estiver rodando, reinicie-o: sudo systemctl restart self-monitoring")


def main() -> None:
    ap = argparse.ArgumentParser(description="self-monitoring")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--config", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--set-password", action="store_true", help="cadastra/troca a senha de acesso e sai")
    args = ap.parse_args()

    if args.set_password:
        return set_password()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    (config.ROOT / "data").mkdir(mode=0o700, exist_ok=True)   # num clone novo ainda não existe
    cfg = config.load(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port

    ctx = Context(cfg)
    if cfg.fan_enabled:
        from sm.fan import FanController
        ctx.fan = FanController(ctx)
        ctx.fan.start()
    if cfg.track_exited:
        from sm.proctrack import ProcessTracker
        ctx.proctrack = ProcessTracker(ctx)
        ctx.proctrack.start()
    if cfg.net_monitor:
        from sm.netmon import NetMonitor
        ctx.netmon = NetMonitor(ctx)
        ctx.netmon.start()
    from sm.alerts import Alerts
    from sm.game import GameMonitor
    from sm.power import PowerTimer
    from sm.updates import Updates
    ctx.game = GameMonitor(ctx)
    ctx.updates = Updates()
    ctx.power_timer = PowerTimer() if cfg.allow_power else None
    ctx.alerts = Alerts(ctx)
    ctx.alerts.start()
    Sampler(ctx).start()
    srv = make_server(ctx)

    log = logging.getLogger("self-monitoring")
    log.info("self-monitoring %s | coletores: %s", __version__, ", ".join(c.name for c in ctx.collectors))
    hosts = lan_ips() if cfg.host in ("0.0.0.0", "") else [cfg.host]
    for ip in hosts:
        log.info("acesse: http://%s:%d", ip, cfg.port)
    if cfg.auth_enabled:
        from sm.auth import Auth
        if not Auth().has_password():
            log.warning("nenhuma senha cadastrada: rode  python3 server.py --set-password  "
                        "ou abra http://localhost:%d nesta máquina", cfg.port)
    def on_term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)   # systemctl stop -> encerra limpo
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if ctx.fan:
            ctx.fan.release()
        if ctx.proctrack:
            ctx.proctrack.stop()
        if ctx.netmon:
            ctx.netmon.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
