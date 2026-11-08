from __future__ import annotations

from .. import __version__
from ..actions import ActionError, signal_process
from ..webhook import WebhookError
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
                     "media": ctx.media is not None, "mangohud": _mangohud_installed(),
                     "incidents": ctx.investigator is not None, "history": ctx.store is not None,
                     "services": True, "services_control": ctx.config.allow_services, "logs": ctx.config.allow_logs},
        "machine": _machine(),
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
        focus = getattr(req.ctx, "focus", None)
        return signal_process(int(pid), str(body.get("signal", "TERM")), body.get("create_time"),
                              confirm_critical=bool(body.get("confirm_critical")),
                              close_windows=focus.close_windows if focus else None)
    except ActionError as e:
        return Response({"error": str(e), **e.extra}, e.status)
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


# ---------- quem está acessando o painel ----------
@route("GET", "/api/auth/sessions")
def auth_sessions(req):
    from ..clients import describe, device
    if not req.ctx.config.auth_enabled:
        return {"enabled": False, "sessions": [], "log": []}
    where = {}

    def info(ip):
        if ip and ip not in where:
            where[ip] = describe(ip)
        return where.get(ip)

    sessions = [{**s, "device": device(s["agent"]), "where": info(s["last_ip"])}
                for s in req.auth.list_sessions(req._session_id())]
    log = [{**e, "device": device(e.get("agent", "")), "where": info(e.get("ip"))} for e in req.auth.access_log()[:100]]
    return {"enabled": True, "sessions": sessions, "log": log}


@route("POST", "/api/auth/sessions/end")
def auth_sessions_end(req):
    from ..clients import device
    body = req.json() or {}
    ip, ua = req.client_address[0], req.headers.get("User-Agent", "")
    current = req._session_id()
    if body.get("others"):
        n = len(req.auth.list_sessions(current)) - 1
        req.auth.end_all_except(current)
        req.auth.record("ended", ip, ua, target="todas as outras sessões")
        return {"ok": True, "ended": max(0, n)}
    sid = str(body.get("id", ""))
    if current and sid == req.auth.public_id(current):
        return Response({"error": "Esta é a sessão deste aparelho: use \"Sair\"."}, 400)
    gone = req.auth.end_by_id(sid)
    if not gone:
        return Response({"error": "Sessão não encontrada (já encerrada?)."}, 404)
    req.auth.record("ended", ip, ua, target=f"{device(gone.get('agent', ''))} — {gone.get('last_ip', gone.get('ip'))}")
    return {"ok": True, "ended": 1}


# ---------- ações feitas pelo painel (auditoria) ----------
@route("GET", "/api/audit")
def audit_list(req):
    from ..audit import CATEGORIES
    q = {k: v[0] for k, v in req.query.items()}
    try:
        before = int(q["before"]) if q.get("before") else None
        limit = max(1, min(500, int(q.get("limit", 100))))
    except ValueError:
        return Response({"error": "Parâmetro inválido."}, 400)
    sid = req._session_id()
    return {"items": req.audit.list(q.get("cat") or None, q.get("session") or None, limit, before),
            "categories": CATEGORIES, "current_session": req.auth.public_id(sid) if sid else None}


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


@route("POST", "/api/game/not-game")
def game_not_game(req):
    if not req.ctx.game:
        return Response({"error": "indisponível"}, 404)
    b = req.json() or {}
    try:
        return req.ctx.game.set_not_game(b.get("key"), b.get("name"), b.get("ignored", True) is not False)
    except ValueError as e:
        return Response({"error": str(e)}, 400)


# ---------- MangoHud (configuração ao vivo) ----------
def _mangohud_installed() -> bool:
    from ..mangohud import installed
    return installed()


@route("GET", "/api/mangohud")
def mangohud_state(req):
    from ..mangohud import ConfError, state
    try:
        return state(req.query.get("file", [None])[0])
    except ConfError as e:
        return Response({"error": str(e)}, 400)


@route("POST", "/api/mangohud")
def mangohud_update(req):
    from ..mangohud import ConfError, apply, save_text
    b = req.json() or {}
    try:
        if "text" in b:
            return save_text(b.get("file"), b["text"])
        return apply(b.get("file"), b.get("set"))
    except ConfError as e:
        return Response({"error": str(e)}, 400)
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


@route("GET", "/api/mural")
def mural_get(req):
    return req.ctx.mural.status()


@route("POST", "/api/mural")
def mural_set(req):
    try:
        return req.ctx.mural.update(req.json() or {})
    except ValueError as e:
        return Response({"error": str(e)}, 400)


@route("GET", "/api/mural/cities")
def mural_cities(req):
    try:
        return {"cities": req.ctx.mural.search_city(req.query.get("q", [""])[0])}
    except (OSError, ValueError):
        return Response({"error": "Não foi possível buscar a cidade. Verifique a internet do PC."}, 502)


@route("GET", "/api/mural/games")
def mural_games(req):
    return {"games": req.ctx.mural.games((req.ctx.data.get("game") or {}).get("game"))}


@route("GET", "/api/mural/art/<key>")
def mural_art_get(req, key):
    kind = req.query.get("kind", [None])[0]
    res = req.ctx.mural.art(key, kind)
    if not res:
        return Response({"error": "sem arte"}, 404)
    # sem "kind" é a arte escolhida, que o usuário pode trocar: não fica em cache
    return Response(res[0], 200, {"Content-Type": res[1], **({"Cache-Control": "private, max-age=86400"} if kind else {})})


@route("POST", "/api/mural/art/<key>")
def mural_art_set(req, key):
    b = req.json() or {}
    try:
        return req.ctx.mural.set_art(key, b.get("name"), b.get("kind"), b.get("image"))
    except ValueError as e:
        return Response({"error": str(e)}, 400)


@route("POST", "/api/mural/art/<key>/reset")
def mural_art_reset(req, key):
    try:
        req.ctx.mural.reset_art(key)
    except ValueError as e:
        return Response({"error": str(e)}, 400)
    return {"ok": True}


@route("GET", "/api/mural/background")
def mural_background_get(req):
    data = req.ctx.mural.background()
    if data is None:
        return Response({"error": "sem imagem"}, 404)
    return Response(data, 200, {"Content-Type": "image/jpeg"})


@route("POST", "/api/mural/background")
def mural_background_set(req):
    try:
        return req.ctx.mural.set_background((req.json() or {}).get("image") or "")
    except ValueError as e:
        return Response({"error": str(e)}, 400)


@route("POST", "/api/mural/background/reset")
def mural_background_reset(req):
    return req.ctx.mural.reset_background()


@route("POST", "/api/webhook/notify")
def webhook_notify(req):
    """Sem sessão: autorizado pelo token (ver sm/webhook.py)."""
    hook = req.ctx.webhook
    if not hook:
        return Response({"error": "indisponível"}, 404)
    try:
        hook.authorize(req.headers.get("Authorization"), req.client_address[0])
        return hook.receive(req.json())
    except WebhookError as e:
        return Response({"error": str(e)}, e.status, {"Retry-After": str(e.retry)} if e.retry else None)


@route("GET", "/api/webhook")
def webhook_status(req):
    return req.ctx.webhook.status() if req.ctx.webhook else Response({"error": "indisponível"}, 404)


@route("POST", "/api/webhook/token")
def webhook_token(req):
    return {"token": req.ctx.webhook.new_token(), **req.ctx.webhook.status()}


@route("POST", "/api/webhook/revoke")
def webhook_revoke(req):
    req.ctx.webhook.revoke()
    return req.ctx.webhook.status()


@route("POST", "/api/webhook/test")
def webhook_test(req):
    return req.ctx.webhook.receive({"title": "Teste do webhook", "message": "As notificações por webhook estão funcionando.",
                                    "icon": "check", "source": "Painel"})


@route("GET", "/api/panel-update")
def panel_update(req):
    return req.ctx.selfupdate.status() if req.ctx.selfupdate else Response({"error": "indisponível"}, 404)


@route("POST", "/api/panel-update/check")
def panel_update_check(req):
    if not req.ctx.selfupdate:
        return Response({"error": "indisponível"}, 404)
    req.ctx.selfupdate.check_now()
    return req.ctx.selfupdate.status()


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


# ---------- máquina / frota ----------
def _machine() -> dict:
    from ..fleet import identity
    return identity()


def _q(req, k, default=None):
    v = req.query.get(k, [None])[0]
    return default if v in (None, "") else v


def _no_store(req):
    return None if req.ctx.store else Response({"error": "Histórico persistente desativado ([history] enabled = false)."}, 404)


@route("GET", "/api/fleet")
def fleet_info(req):
    from ..fleet import SCHEMA, identity
    st = req.ctx.store
    return {"identity": identity(), "schema": SCHEMA, "store": {"size": st.size(), "seq": int(st.get_meta("seq", "0"))} if st else None,
            "server": None, "note": "Envio para um servidor central ainda não implementado (ver FROTA.md)."}


@route("GET", "/api/fleet/export")
def fleet_export(req):
    from ..fleet import package
    if (r := _no_store(req)):
        return r
    try:
        return package(req.ctx.store, int(_q(req, "since", 0)), int(_q(req, "limit", 1000)))
    except ValueError:
        return Response({"error": "Parâmetros inválidos."}, 400)


# ---------- histórico persistente ----------
@route("GET", "/api/history/metrics")
def history_metrics(req):
    if (r := _no_store(req)):
        return r
    import time as _t
    keys = [k for k in (_q(req, "keys", "") or "").split(",") if k] or None
    try:
        since = float(_q(req, "since", _t.time() - 86400))
        until = float(_q(req, "until", _t.time()))
        res = int(_q(req, "res")) if _q(req, "res") else None
    except ValueError:
        return Response({"error": "Parâmetros inválidos."}, 400)
    if res not in (None, 60, 3600):
        return Response({"error": "res deve ser 60 ou 3600."}, 400)
    return {"series": req.ctx.store.metrics(keys, since, until, res), "keys": req.ctx.store.metric_keys()}


@route("GET", "/api/history/hardware")
def history_hardware(req):
    if (r := _no_store(req)):
        return r
    return {"current": req.ctx.store.hardware_latest(), "history": req.ctx.store.hardware_history(100)}


@route("GET", "/api/history/events")
def history_events(req):
    if (r := _no_store(req)):
        return r
    import time as _t
    try:
        since = float(_q(req, "since", _t.time() - 86400))
        limit = int(_q(req, "limit", 300))
    except ValueError:
        return Response({"error": "Parâmetros inválidos."}, 400)
    return {"events": req.ctx.store.events(since=since, family=_q(req, "family"), min_severity=_q(req, "min_severity"),
                                           limit=limit)}


# ---------- incidentes ----------
def _no_inv(req):
    return None if req.ctx.investigator else Response({"error": "Investigador de incidentes desativado."}, 404)


@route("GET", "/api/incidents")
def incidents_list(req):
    if (r := _no_inv(req)):
        return r
    try:
        before = float(_q(req, "before")) if _q(req, "before") else None
        limit = int(_q(req, "limit", 100))
    except ValueError:
        return Response({"error": "Parâmetros inválidos."}, 400)
    items = req.ctx.store.incidents(limit=limit, status=_q(req, "status"), before=before)
    return {"items": items, "watcher": req.ctx.journal.status() if req.ctx.journal else None}


@route("POST", "/api/incidents/investigate")
def incident_investigate(req):
    if (r := _no_inv(req)):
        return r
    import time as _t
    b = req.json() or {}
    try:
        at = float(b.get("at") or _t.time())
        before = float(b.get("before") or 300)
        after = float(b.get("after") if b.get("after") is not None else 60)
    except (TypeError, ValueError):
        return Response({"error": "Parâmetros inválidos."}, 400)
    if at < _t.time() - 90 * 86400:
        return Response({"error": "Momento muito antigo."}, 400)
    iid = req.ctx.investigator.investigate(at, before, after, str(b.get("note") or "")[:2000] or None)
    return {"id": iid}


@route("GET", "/api/incidents/<iid>")
def incident_get(req, iid):
    if (r := _no_inv(req)):
        return r
    try:
        inc = req.ctx.store.incident(int(iid))
    except ValueError:
        inc = None
    return inc or Response({"error": "Incidente não encontrado."}, 404)


@route("POST", "/api/incidents/<iid>")
def incident_update(req, iid):
    if (r := _no_inv(req)):
        return r
    b = req.json() or {}
    fields = {}
    if b.get("status") in ("open", "resolved", "ignored"):
        fields["status"] = b["status"]
    if "note" in b:
        fields["note"] = str(b["note"] or "")[:4000] or None
    try:
        iid = int(iid)
    except ValueError:
        return Response({"error": "Incidente inválido."}, 400)
    if not req.ctx.store.incident(iid):
        return Response({"error": "Incidente não encontrado."}, 404)
    req.ctx.store.update_incident(iid, **fields)
    return req.ctx.store.incident(iid)


@route("POST", "/api/incidents/<iid>/delete")
def incident_delete(req, iid):
    if (r := _no_inv(req)):
        return r
    try:
        ok = req.ctx.store.delete_incident(int(iid))
    except ValueError:
        ok = False
    return {"ok": True} if ok else Response({"error": "Incidente não encontrado."}, 404)


# ---------- serviços ----------
def _user_scope(req, body=None) -> bool:
    return ((body or {}).get("scope") or _q(req, "scope", "system")) == "user"


@route("GET", "/api/services")
def services_list(req):
    from .. import services
    return services.list_units(user=_user_scope(req))


@route("GET", "/api/services/<unit>")
def service_detail(req, unit):
    from .. import services
    try:
        return services.detail(unit, user=_user_scope(req))
    except ValueError as e:
        return Response({"error": str(e)}, 400)


@route("POST", "/api/services/<unit>/<act>")
def service_action(req, unit, act):
    from .. import services
    if not req.ctx.config.allow_services:
        return Response({"error": "Controlar serviços está desativado na configuração (security.allow_services)."}, 403)
    try:
        return services.action(unit, act, user=_user_scope(req, req.json()))
    except ValueError as e:
        return Response({"error": str(e)}, 400)
    except PermissionError as e:
        return Response({"error": str(e)}, 403)
    except RuntimeError as e:
        return Response({"error": str(e)}, 500)


# ---------- logs do sistema ----------
def _logs_off(req):
    return None if req.ctx.config.allow_logs else Response({"error": "Aba Logs desativada (security.allow_logs)."}, 403)


@route("GET", "/api/logs")
def logs_query(req):
    from .. import journal
    if (r := _logs_off(req)):
        return r
    q = {k: _q(req, k) for k in ("grep", "unit", "user_unit", "ident", "priority", "boot", "since", "until", "limit",
                                 "before", "after")}
    q["kernel"] = _q(req, "kernel") in ("1", "true")
    try:
        return journal.query(q)
    except ValueError as e:
        return Response({"error": str(e)}, 400)


@route("GET", "/api/logs/entry")
def logs_entry(req):
    from .. import journal
    if (r := _logs_off(req)):
        return r
    try:
        e = journal.entry(_q(req, "cursor", ""))
    except ValueError as e:
        return Response({"error": str(e)}, 400)
    return e or Response({"error": "Linha não encontrada."}, 404)


@route("GET", "/api/logs/boots")
def logs_boots(req):
    from .. import journal
    if (r := _logs_off(req)):
        return r
    return {"boots": journal.boots()}
