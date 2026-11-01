from __future__ import annotations

from .. import __version__
from ..actions import ActionError, signal_process
from . import Response, route


@route("GET", "/api/info")
def info(req):
    ctx = req.ctx
    return {
        "version": __version__,
        "collectors": [c.name for c in ctx.collectors],
        "features": {"kill": ctx.config.allow_kill, "fan": bool(ctx.fan and ctx.fan.available),
                     "exited": ctx.proctrack is not None,
                     "power": ctx.config.allow_power,
                     "netmon": ctx.netmon is not None,
                     "unlock": ctx.config.allow_unlock, "launch": ctx.config.allow_launch},
        "sample_interval": ctx.config.sample_interval,
        "process_interval": ctx.config.process_interval,
        "info": ctx.info(),
    }


@route("GET", "/api/snapshot")
def snapshot(req):
    return req.ctx.snapshot()


@route("GET", "/api/history")
def history(req):
    keys = [k for k in req.query.get("keys", [""])[0].split(",") if k] or None
    since = float(req.query.get("since", ["0"])[0] or 0)
    return req.ctx.history.get(keys, since)


@route("GET", "/api/processes")
def processes(req):
    return {"ts": req.ctx.ts, "processes": req.ctx.processes}


@route("POST", "/api/processes/<pid>/signal")
def kill(req, pid):
    if not req.ctx.config.allow_kill:
        return Response({"error": "Finalizar processos está desativado na configuração."}, 403)
    body = req.json() or {}
    try:
        return signal_process(int(pid), str(body.get("signal", "TERM")), body.get("create_time"))
    except ActionError as e:
        return Response({"error": str(e)}, e.status)
    except ValueError:
        return Response({"error": "PID inválido"}, 400)


@route("GET", "/api/fan")
def fan_status(req):
    if not req.ctx.fan:
        return Response({"error": "Controle de ventoinha desativado."}, 404)
    return req.ctx.fan.status()


@route("POST", "/api/fan")
def fan_update(req):
    fan = req.ctx.fan
    if not fan or not fan.available:
        return Response({"error": "Controle de ventoinha indisponível."}, 404)
    try:
        fan.update(req.json() or {})
    except PermissionError as e:
        return Response({"error": str(e)}, 403)
    except ValueError as e:
        return Response({"error": str(e)}, 400)
    return fan.status()


# ---------- histórico e análise de processos ----------
def _tracker(req):
    if not req.ctx.proctrack:
        raise LookupError("Histórico de processos desativado (sampling.track_exited).")
    return req.ctx.proctrack


@route("GET", "/api/processes/exited")
def exited(req):
    try:
        return _tracker(req).list_exited()
    except LookupError as e:
        return Response({"error": str(e)}, 404)


@route("POST", "/api/processes/exited/clear")
def exited_clear(req):
    try:
        _tracker(req).clear()
    except LookupError as e:
        return Response({"error": str(e)}, 404)
    return {"ok": True}


@route("GET", "/api/processes/suspicious")
def suspicious(req):
    from ..procinspect import suspicious as scan
    try:
        tr = _tracker(req)
    except LookupError as e:
        return Response({"error": str(e)}, 404)
    return scan(tr, include_info=req.query.get("info", ["0"])[0] == "1")


@route("GET", "/api/processes/<pid>/inspect")
def inspect_live(req, pid):
    import psutil
    from ..procinspect import analyze_live
    try:
        pid = int(pid)
    except ValueError:
        return Response({"error": "PID inválido"}, 400)
    ct = req.query.get("create_time", [None])[0]
    base = req.ctx.proctrack.get_live(pid) if req.ctx.proctrack else None
    try:
        res = analyze_live(pid, float(ct) if ct else None, base)
    except psutil.NoSuchProcess:
        return Response({"error": "O processo já terminou."}, 404)
    except LookupError as e:
        return Response({"error": str(e)}, 404)
    if req.ctx.proctrack:
        res["ancestors"] = req.ctx.proctrack.ancestors(res["process"].get("ppid") or 0)
    return res


@route("GET", "/api/processes/record/<rid>/inspect")
def inspect_record(req, rid):
    from ..procinspect import analyze_record
    try:
        tr = _tracker(req)
    except LookupError as e:
        return Response({"error": str(e)}, 404)
    rec = tr.find(rid)
    if not rec:
        return Response({"error": "Registro não encontrado (o histórico pode ter sido limpo)."}, 404)
    res = analyze_record(rec)
    res["ancestors"] = tr.ancestors(rec.get("ppid") or 0)
    return res


@route("POST", "/api/processes/trust")
def trust(req):
    from ..procinspect import set_trusted
    body = req.json() or {}
    exe = str(body.get("exe") or "")
    if not exe.startswith("/"):
        return Response({"error": "Caminho inválido."}, 400)
    return {"trusted": sorted(set_trusted(exe, bool(body.get("trusted", True))))}


# ---------- energia ----------
@route("GET", "/api/power")
def power_status(req):
    from .. import power
    if not req.ctx.config.allow_power:
        return Response({"error": "Desligar pelo painel está desativado na configuração."}, 403)
    return power.status()


@route("POST", "/api/power")
def power_request(req):
    from .. import power
    if not req.ctx.config.allow_power:
        return Response({"error": "Desligar pelo painel está desativado na configuração."}, 403)
    body = req.json() or {}
    if body.get("confirm") is not True:
        return Response({"error": "Confirmação ausente."}, 400)
    try:
        return power.request(str(body.get("action", "")), bool(body.get("force", False)))
    except power.PowerError as e:
        return Response({"error": str(e)}, e.status)


# ---------- conexões de rede ----------
def _netmon(req):
    if not req.ctx.netmon:
        raise LookupError("Monitor de rede desativado (sampling.net_monitor).")
    return req.ctx.netmon


@route("GET", "/api/net")
def net(req):
    try:
        return _netmon(req).snapshot()
    except LookupError as e:
        return Response({"error": str(e)}, 404)


@route("GET", "/api/net/closed")
def net_closed(req):
    try:
        return _netmon(req).list_closed()
    except LookupError as e:
        return Response({"error": str(e)}, 404)


@route("POST", "/api/net/closed/clear")
def net_clear(req):
    try:
        _netmon(req).clear()
    except LookupError as e:
        return Response({"error": str(e)}, 404)
    return {"ok": True}


# ---------- sessão gráfica ----------
@route("GET", "/api/session")
def session_status(req):
    from ..desktop import session_status as st
    return st()


@route("POST", "/api/session/lock")
def session_lock(req):
    from ..desktop import SessionError, set_locked
    if not req.ctx.config.allow_unlock:
        return Response({"error": "Desativado na configuração (security.allow_unlock)."}, 403)
    try:
        return set_locked(True)
    except SessionError as e:
        return Response({"error": str(e)}, e.status)


@route("POST", "/api/session/unlock")
def session_unlock(req):
    """Exige a senha do painel de novo (com o mesmo limite de tentativas do login)."""
    import time
    from ..desktop import SessionError, set_locked
    if not req.ctx.config.allow_unlock:
        return Response({"error": "Desativado na configuração (security.allow_unlock)."}, 403)
    ip = req.client_address[0]
    if (wait := req.auth.locked_for(ip)):
        return Response({"error": f"Muitas tentativas. Aguarde {wait}s."}, 429)
    if not req.auth.check_password(str((req.json() or {}).get("password", ""))):
        req.auth.register_fail(ip)
        time.sleep(1)
        return Response({"error": "Senha incorreta."}, 403)
    req.auth.clear_fails(ip)
    try:
        return set_locked(False)
    except SessionError as e:
        return Response({"error": str(e)}, e.status)


# ---------- aplicativos ----------
@route("GET", "/api/apps")
def apps_list(req):
    from ..desktop import APPS, graphical_session
    if not req.ctx.config.allow_launch:
        return Response({"error": "Desativado na configuração (security.allow_launch)."}, 403)
    keep = ("id", "name", "generic", "comment", "category", "terminal", "flatpak", "keywords")
    return {"apps": [{k: a.get(k) for k in keep} | {"has_icon": bool(a.get("icon"))} for a in APPS.list()],
            "session": graphical_session()}


@route("GET", "/api/apps/<aid>/icon")
def app_icon(req, aid):
    import mimetypes
    from ..desktop import APPS
    p = APPS.icon_path(aid)
    if not p:
        return Response({"error": "sem ícone"}, 404)
    try:
        data = p.read_bytes()
    except OSError:
        return Response({"error": "sem ícone"}, 404)
    ctype = "image/svg+xml" if p.suffix == ".svg" else mimetypes.guess_type(p.name)[0] or "image/png"
    if p.suffix == ".svgz":
        import gzip
        data, ctype = gzip.decompress(data), "image/svg+xml"
    return Response(data, 200, {"Content-Type": ctype, "Cache-Control": "private, max-age=86400",
                                "Content-Security-Policy": "script-src 'none'"})


@route("POST", "/api/apps/<aid>/launch")
def app_launch(req, aid):
    from ..desktop import APPS, SessionError
    if not req.ctx.config.allow_launch:
        return Response({"error": "Desativado na configuração (security.allow_launch)."}, 403)
    try:
        return APPS.launch(aid)
    except SessionError as e:
        return Response({"error": str(e)}, e.status)
