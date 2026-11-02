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
                     "unlock": ctx.config.allow_unlock, "launch": ctx.config.allow_launch,
                     "screenshot": ctx.config.allow_screenshot, "control": ctx.config.allow_control,
                     "alerts": ctx.alerts is not None, "updates": ctx.updates is not None,
                     "media": ctx.media is not None},
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


# ---------- jogo / FPS ----------
@route("GET", "/api/game")
def game_status(req):
    if not req.ctx.game:
        return Response({"error": "indisponível"}, 404)
    return req.ctx.game.status()


@route("POST", "/api/game")
def game_set(req):
    if not req.ctx.game:
        return Response({"error": "indisponível"}, 404)
    try:
        return req.ctx.game.set_enabled(bool((req.json() or {}).get("fps_enabled")))
    except RuntimeError as e:
        return Response({"error": str(e)}, 500)


# ---------- áudio ----------
def _control_off(req):
    return None if req.ctx.config.allow_control else Response(
        {"error": "Controle de áudio/tela desativado na configuração (security.allow_control)."}, 403)


@route("GET", "/api/audio")
def audio_state(req):
    from ..audio import AudioError, state
    if (r := _control_off(req)):
        return r
    try:
        return state()
    except AudioError as e:
        return Response({"error": str(e)}, e.status)


@route("POST", "/api/audio")
def audio_action(req):
    from ..audio import AudioError, action
    if (r := _control_off(req)):
        return r
    try:
        return action(req.json() or {})
    except (AudioError, KeyError, ValueError) as e:
        return Response({"error": str(e) if isinstance(e, AudioError) else "Pedido inválido."},
                        getattr(e, "status", 400))


# ---------- tela ----------
def _display(fn):
    from ..display import DisplayError
    try:
        return fn()
    except DisplayError as e:
        return Response({"error": str(e)}, e.status)
    except (KeyError, ValueError, TypeError):
        return Response({"error": "Pedido inválido."}, 400)


@route("GET", "/api/display")
def display_state(req):
    from .. import display
    if (r := _control_off(req)):
        return r
    return _display(lambda: {"outputs": display.outputs(), "pending": display.MODES.status(),
                             "revert_after": display.REVERT_AFTER})


@route("GET", "/api/display/ddc")
def display_ddc(req):
    from .. import display
    if (r := _control_off(req)):
        return r
    refresh = req.query.get("refresh", ["0"])[0] == "1"
    return _display(lambda: {"monitors": display.ddc_monitors(refresh=refresh)})


@route("POST", "/api/display/ddc")
def display_ddc_set(req):
    from .. import display
    if (r := _control_off(req)):
        return r
    b = req.json() or {}
    bus = int(b.get("bus", -1))
    op = b.get("op", "set")
    if op == "dim":
        return _display(lambda: {"monitors": display.dim(bus)})
    if op == "restore":
        return _display(lambda: {"monitors": display.restore(bus)})
    return _display(lambda: {"monitors": display.set_vcp(bus, b.get("brightness"), b.get("contrast"))})


@route("POST", "/api/display/mode")
def display_mode(req):
    from .. import display
    if (r := _control_off(req)):
        return r
    b = req.json() or {}
    op = b.get("op", "apply")
    if op == "confirm":
        display.MODES.confirm()
        return {"ok": True}
    if op == "revert":
        return _display(lambda: (display.MODES.revert(), {"ok": True})[1])
    return _display(lambda: {"pending": display.MODES.apply(str(b.get("output")), str(b.get("mode")))})


@route("POST", "/api/display/dpms")
def display_dpms(req):
    from .. import display
    if (r := _control_off(req)):
        return r
    on = bool((req.json() or {}).get("on"))
    return _display(lambda: (display.dpms(on), {"ok": True})[1])


# ---------- timer de energia ----------
@route("GET", "/api/power/timer")
def power_timer(req):
    if not req.ctx.power_timer:
        return Response({"error": "Desativado na configuração (security.allow_power)."}, 403)
    return {"timer": req.ctx.power_timer.status()}


@route("POST", "/api/power/timer")
def power_timer_set(req):
    from ..power import PowerError
    t = req.ctx.power_timer
    if not t:
        return Response({"error": "Desativado na configuração (security.allow_power)."}, 403)
    b = req.json() or {}
    if b.get("cancel"):
        t.cancel()
        return {"timer": None}
    try:
        return {"timer": t.schedule(str(b.get("action", "poweroff")), b.get("minutes"))}
    except PowerError as e:
        return Response({"error": str(e)}, e.status)


# ---------- atualizações ----------
@route("GET", "/api/updates")
def updates_status(req):
    return req.ctx.updates.status() if req.ctx.updates else Response({"error": "indisponível"}, 404)


@route("POST", "/api/updates/refresh")
def updates_refresh(req):
    if not req.ctx.updates:
        return Response({"error": "indisponível"}, 404)
    req.ctx.updates.refresh()
    return req.ctx.updates.status()


# ---------- alertas ----------
@route("GET", "/api/alerts")
def alerts_list(req):
    return req.ctx.alerts.list()


@route("POST", "/api/alerts/read")
def alerts_read(req):
    req.ctx.alerts.mark_read()
    return {"ok": True}


@route("POST", "/api/alerts/clear")
def alerts_clear(req):
    req.ctx.alerts.clear()
    return {"ok": True}


@route("GET", "/api/alerts/config")
def alerts_config(req):
    return req.ctx.alerts.config()


@route("POST", "/api/alerts/config")
def alerts_config_set(req):
    try:
        return req.ctx.alerts.update_config(req.json() or {})
    except ValueError as e:
        return Response({"error": str(e)}, 400)


@route("POST", "/api/alerts/test")
def alerts_test(req):
    """Envia uma mensagem de teste pelos canais configurados (PC e ntfy)."""
    from ..desktop import notify_pc
    a = req.ctx.alerts
    ch = a.config()["channels"]
    res = {}
    if ch["pc"]:
        res["pc"] = notify_pc("⚠ Teste de alerta", "Os alertas do self-monitoring vão aparecer assim.")
    if ch["ntfy"]:
        err = a.send_ntfy(ch["ntfy"], "Teste de alerta", "Os alertas do self-monitoring vão chegar assim.")
        res["ntfy"] = err is None
        if err:
            res["ntfy_error"] = err
    return res


# ---------- captura de tela ----------
@route("POST", "/api/screenshot")
def screenshot_take(req):
    from ..screenshot import ShotError, take
    if not req.ctx.config.allow_screenshot:
        return Response({"error": "Captura de tela desativada (security.allow_screenshot)."}, 403)
    try:
        return take(req.client_address[0])
    except ShotError as e:
        return Response({"error": str(e)}, e.status)


@route("GET", "/api/screenshots")
def screenshots(req):
    from ..screenshot import list_shots
    if not req.ctx.config.allow_screenshot:
        return Response({"error": "Captura de tela desativada (security.allow_screenshot)."}, 403)
    return {"shots": list_shots()}


@route("GET", "/api/screenshots/<name>")
def screenshot_file(req, name):
    from ..screenshot import path_of, png_complete
    if not req.ctx.config.allow_screenshot:
        return Response({"error": "desativado"}, 403)
    p = path_of(name)
    if not p:
        return Response({"error": "não encontrada"}, 404)
    if not png_complete(p):    # ainda sendo gravada: não deixa o navegador guardar a imagem quebrada
        return Response({"error": "captura ainda sendo gravada"}, 503, {"Retry-After": "1"})
    hdr = {"Content-Type": "image/png", "Cache-Control": "private, max-age=3600"}
    if req.query.get("download"):
        hdr["Content-Disposition"] = f'attachment; filename="{p.name}"'
    return Response(p.read_bytes(), 200, hdr)


@route("POST", "/api/screenshots/<name>/delete")
def screenshot_delete(req, name):
    from ..screenshot import delete
    return {"ok": delete(name)}


# ---------- mídia (MPRIS) ----------
@route("POST", "/api/media")
def media_control(req):
    from ..media import MediaError
    if not req.ctx.media:
        return Response({"error": "Leitura de mídia indisponível."}, 404)
    if not req.ctx.config.allow_control:
        return Response({"error": "Controles desativados (security.allow_control)."}, 403)
    body = req.json() or {}
    try:
        return req.ctx.media.control(str(body.get("player", "")), str(body.get("op", "")), body.get("offset", 0))
    except MediaError as e:
        return Response({"error": str(e)}, e.status)


@route("GET", "/api/media/art")
def media_art(req):
    from ..media import MediaError
    if not req.ctx.media:
        return Response({"error": "indisponível"}, 404)
    try:
        r = req.ctx.media.art_file(req.query.get("player", [""])[0])
    except MediaError as e:
        return Response({"error": str(e)}, e.status)
    if not r:
        return Response({"error": "sem capa"}, 404)
    return Response(r[0], 200, {"Content-Type": r[1], "Cache-Control": "private, max-age=600"})
