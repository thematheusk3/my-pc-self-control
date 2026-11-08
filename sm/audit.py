"""Registro de ações feitas pelo painel (auditoria): tudo que muda algo no PC ou no próprio painel.

Cada entrada diz o quê (texto legível), quem (aparelho, IP, sessão), quando e se deu certo (ou o erro).
Ajustes seguidos do mesmo controle (ex.: arrastar o volume) viram uma entrada só, com o valor final.
Fica em data/audit-log.json (últimas 1000) e também no log do painel.

Para registrar uma rota nova: adicione um descritor em DESCRIBERS (nome da função da rota -> função que
recebe (req, body, params) e devolve (categoria, texto, chave de agrupamento ou None)).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque

from .auth import _write_private
from .config import ROOT

log = logging.getLogger(__name__)
FILE = ROOT / "data" / "audit-log.json"
KEEP = 1000
MERGE_WINDOW = 20      # s: ajustes seguidos do mesmo controle viram uma entrada
SAVE_DELAY = 2         # s: agrupa gravações em disco (sliders mandam vários pedidos por segundo)
CATEGORIES = {"process": "Processos", "power": "Energia", "fan": "Ventoinha", "session": "Sessão do PC", "apps": "Apps",
              "audio": "Áudio", "media": "Mídia", "display": "Tela", "screenshot": "Captura de tela",
              "game": "Jogo/FPS", "panel": "Painel"}


class Audit:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: deque[dict] = deque(maxlen=KEEP)
        try:
            self.items.extend(json.loads(FILE.read_text())[:KEEP])
        except (OSError, ValueError, TypeError):
            pass
        self._seq = max((e.get("seq", 0) for e in self.items), default=0)
        self._timer: threading.Timer | None = None

    def record(self, cat: str, text: str, who: dict, ok: bool, error: str | None = None,
               merge: str | None = None) -> dict:
        now = time.time()
        with self.lock:
            last = self.items[0] if self.items else None
            if (merge and ok and last and last.get("merge") == merge and last.get("ok")
                    and last.get("session") == who.get("session") and now - last["time"] < MERGE_WINDOW):
                last.update(text=text, time=now, count=last.get("count", 1) + 1)
                e = last
            else:
                self._seq += 1
                e = {"seq": self._seq, "time": now, "cat": cat, "text": text, "ok": ok, "error": error,
                     "merge": merge, **who}
                self.items.appendleft(e)
                log.warning("ação pelo painel [%s] %s — %s %s%s", cat, text, who.get("device"), who.get("ip"),
                            "" if ok else f" — falhou: {error}")
            self._schedule_save()
        return e

    def _schedule_save(self) -> None:
        if self._timer is None:
            self._timer = threading.Timer(SAVE_DELAY, self._save)
            self._timer.daemon = True
            self._timer.start()

    def _save(self) -> None:
        with self.lock:
            self._timer = None
            data = list(self.items)
        try:
            _write_private(FILE, data)
        except OSError as e:
            log.warning("falha ao gravar %s: %s", FILE, e)

    def flush(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._save()

    def list(self, cat: str | None = None, session: str | None = None, limit: int = 200,
             before: int | None = None) -> list[dict]:
        with self.lock:
            out = []
            for e in self.items:
                if before is not None and e["seq"] >= before:
                    continue
                if (cat and e["cat"] != cat) or (session and e.get("session") != session):
                    continue
                out.append({k: v for k, v in e.items() if k != "merge"})
                if len(out) >= limit:
                    break
            return out


# ---------- descrições legíveis por rota ----------
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, fn, ttl: float = 15):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:  # noqa: BLE001  (só para a descrição; a ação em si segue normal)
        val = None
    _cache[key] = (time.time(), val)
    return val


def _audio_names() -> dict:
    def load():
        from .audio import state
        st = state()
        names = {}
        for k in ("sinks", "sources"):
            for d in st.get(k, []):
                names[("dev", d["name"])] = d.get("description") or d["name"]
        for k in ("apps", "recording"):
            for a in st.get(k, []):
                names[(k, str(a["index"]))] = a.get("app") or a.get("binary") or "app"
        return names
    return _cached("audio", load) or {}


def _pct(v) -> str:
    try:
        return f"{round(float(v))}%"
    except (TypeError, ValueError):
        return "?"


def _kill(req, body, p):
    import psutil
    pid = p.get("pid")
    try:
        name = psutil.Process(int(pid)).name()
    except (psutil.Error, ValueError):
        name = "?"
    verb = {"TERM": "Finalizar", "KILL": "Forçar finalização de", "STOP": "Suspender", "CONT": "Retomar"}.get(
        str(body.get("signal", "TERM")).upper(), f"Sinal {body.get('signal')} para")
    return "process", f"{verb} {name} (PID {pid})", None


def _audio(req, body, p):
    op, kind = body.get("op"), body.get("kind", "sink")
    names = _audio_names()
    if op == "move":
        kind = "app"
    if kind in ("sink", "source") or op == "set_default":
        who = names.get(("dev", body.get("name")), body.get("name") or "?")
        what = "saída" if kind == "sink" else "entrada (microfone)"
    else:
        who = names.get(("apps" if kind == "app" else "recording", str(body.get("index"))), "app")
        what = "app" if kind == "app" else "gravação"
    if op == "set_default":
        return "audio", f"Saída padrão de áudio: {who}" if kind == "sink" else f"Microfone padrão: {who}", None
    if op == "volume":
        return "audio", f"Volume da {what} {who}: {_pct(body.get('volume'))}", f"audio-vol:{kind}:{body.get('name') or body.get('index')}"
    if op == "mute":
        return "audio", f"{'Mudo' if body.get('mute') else 'Som ligado'}: {what} {who}", None
    if op == "move":
        dest = names.get(("dev", body.get("sink")), body.get("sink")) if body.get("sink") else "saída padrão"
        return "audio", f"{who} passa a tocar em: {dest}", None
    return "audio", f"Áudio: {op}", None


def _media(req, body, p):
    op = body.get("op")
    off = float(body.get("offset") or 0)
    label = {"play_pause": "Tocar/pausar", "next": "Próxima faixa", "previous": "Faixa anterior",
             "seek": f"Avançar {off:g} s" if off >= 0 else f"Voltar {-off:g} s"}.get(op, op)
    return "media", f"{label} ({body.get('player') or 'player'})", None


def _ddc(req, body, p):
    from .display import ddc_monitors
    mons = _cached("ddc", lambda: ddc_monitors(), 60) or []
    model = next((m["model"] for m in mons if m.get("bus") == body.get("bus")), f"monitor {body.get('bus')}")
    op = body.get("op", "set")
    if op == "dim":
        return "display", f"Escurecer {model}", None
    if op == "restore":
        return "display", f"Restaurar brilho de {model}", None
    keys = [k for k in ("brightness", "contrast") if body.get(k) is not None]
    parts = [f"{'brilho' if k == 'brightness' else 'contraste'} {body[k]}%" for k in keys]
    return "display", f"{model}: {', '.join(parts) or 'ajuste'}", f"ddc:{body.get('bus')}:{','.join(keys)}"


def _mode(req, body, p):
    op = body.get("op", "apply")
    if op == "confirm":
        return "display", "Manter a nova resolução", None
    if op == "revert":
        return "display", "Voltar à resolução anterior", None
    from .display import outputs
    outs = _cached("outputs", outputs, 30) or []
    label = next((o.get("label") or o["name"] for o in outs if o.get("name") == body.get("output")), body.get("output"))
    return "display", f"Resolução de {label}: {str(body.get('mode', '?')).replace('@', ' @ ')} Hz", None


def _power(req, body, p):
    act = {"poweroff": "Desligar", "reboot": "Reiniciar"}.get(body.get("action"), str(body.get("action")))
    return "power", f"{act} o PC{' (forçado)' if body.get('force') else ''}", None


def _timer(req, body, p):
    if body.get("cancel"):
        return "power", "Cancelar o timer de energia", None
    act = {"poweroff": "desligar", "reboot": "reiniciar"}.get(body.get("action", "poweroff"), body.get("action"))
    return "power", f"Agendar para {act} em {body.get('minutes')} min", None


def _fan(req, body, p):
    mode = body.get("mode")
    if "curve" in body:
        return "fan", "Salvar a curva da ventoinha (modo automático)", "fan-curve"
    if "preset" in body:
        return "fan", f"Perfil da ventoinha: {body.get('preset')}", None
    if mode == "manual" or "manual_pct" in body:
        text = f"Ventoinha em velocidade fixa: {_pct(body['manual_pct'])}" if "manual_pct" in body else "Ventoinha em modo manual"
        return "fan", text, "fan-manual"
    if mode == "auto":
        return "fan", "Ventoinha em modo automático (curva)", None
    if mode == "firmware":
        return "fan", "Devolver a ventoinha ao controle da placa", None
    return "fan", "Ajustar a ventoinha", None


def _launch(req, body, p):
    from .desktop import APPS
    app = _cached(f"app:{p.get('aid')}", lambda: APPS.get(p.get("aid")), 300)
    return "apps", f"Abrir {app['name'] if app else p.get('aid')}", None


DESCRIBERS = {
    "kill": _kill,
    "audio_action": _audio,
    "media_control": _media,
    "display_ddc_set": _ddc,
    "display_mode": _mode,
    "display_dpms": lambda r, b, p: ("display", "Ligar a imagem dos monitores" if b.get("on") else "Apagar a imagem dos monitores", None),
    "power_request": _power,
    "power_timer_set": _timer,
    "session_lock": lambda r, b, p: ("session", "Bloquear a tela do PC", None),
    "session_unlock": lambda r, b, p: ("session", "Desbloquear a tela do PC", None),
    "fan_update": _fan,
    "app_launch": _launch,
    "game_set": lambda r, b, p: ("game", "Ligar a leitura de FPS (MangoHud)" if b.get("fps_enabled") else "Desligar a leitura de FPS", None),
    "screenshot_take": lambda r, b, p: ("screenshot", "Tirar captura de tela", None),
    "screenshot_delete": lambda r, b, p: ("screenshot", f"Apagar a captura {p.get('name')}", None),
    "trust": lambda r, b, p: ("panel", f"{'Marcar como confiável' if b.get('trusted', True) else 'Deixar de confiar em'}: {b.get('exe')}", None),
    "exited_clear": lambda r, b, p: ("panel", "Limpar o histórico de processos encerrados", None),
    "net_clear": lambda r, b, p: ("panel", "Limpar o histórico de conexões encerradas", None),
    "alerts_config_set": lambda r, b, p: ("panel", "Alterar as regras/canais de alertas", None),
    "mangohud_update": lambda r, b, p: ("game", (f"Editar o arquivo {b.get('file') or 'MangoHud.conf'} do MangoHud" if "text" in b else
                                                  "MangoHud: " + ", ".join(f"{k}={v}" for k, v in list((b.get("set") or {}).items())[:6])),
                                        f"mangohud:{b.get('file')}:{','.join(sorted(b.get('set') or {}))}"),
    "game_not_game": lambda r, b, p: ("game", f"{'Marcar' if b.get('ignored', True) is not False else 'Desmarcar'} {b.get('name') or b.get('key')} como app que não é jogo", None),
    "mural_set": lambda r, b, p: ("panel", "Alterar as preferências do Mural", "mural"),
    "mural_background_set": lambda r, b, p: ("panel", "Enviar a imagem de fundo do Mural", "mural"),
    "mural_background_reset": lambda r, b, p: ("panel", "Remover a imagem de fundo do Mural", "mural"),
    "mural_art_set": lambda r, b, p: ("panel", f"Trocar a arte do jogo {p.get('key')} no Mural", None),
    "mural_art_reset": lambda r, b, p: ("panel", f"Voltar à arte padrão do jogo {p.get('key')} no Mural", None),
    "webhook_token": lambda r, b, p: ("panel", "Gerar token do webhook", None),
    "webhook_revoke": lambda r, b, p: ("panel", "Desligar o webhook", None),
    "webhook_test": lambda r, b, p: ("panel", "Enviar notificação de teste do webhook", None),
    "alerts_clear": lambda r, b, p: ("panel", "Limpar o histórico de alertas", None),
    "auth_sessions_end": lambda r, b, p: ("panel", "Encerrar todas as outras sessões" if b.get("others") else "Encerrar a sessão de outro aparelho", None),
}


def describe(fn_name: str, req, body: dict, params: dict) -> tuple[str, str, str | None] | None:
    d = DESCRIBERS.get(fn_name)
    if not d:
        return None
    try:
        return d(req, body if isinstance(body, dict) else {}, params)
    except Exception:  # noqa: BLE001
        log.exception("descrição da ação %s", fn_name)
        return "panel", fn_name, None
