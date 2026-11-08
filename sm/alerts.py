"""Alertas: regras configuráveis (liga/desliga + limite), histórico e canais de envio.

Canais:
  - painel: sempre (sino com contador; notificação do navegador se a aba estiver aberta e o navegador permitir)
  - pc: notificação na tela do próprio PC (opcional)
  - ntfy: push para o celular via ntfy (app gratuito); URL do tópico configurável, ex. https://ntfy.sh/meu-topico-secreto
         ATENÇÃO: no ntfy.sh público, qualquer um que souber o tópico lê as mensagens; use um nome difícil ou servidor próprio.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections import deque

from .config import ROOT

log = logging.getLogger(__name__)
CONF_FILE = ROOT / "data" / "alerts.json"
LOG_FILE = ROOT / "data" / "alerts-log.json"

# id: (rótulo, ligado por padrão, valor padrão, unidade, descrição)
RULES = {
    "cpu_temp":   ("Temperatura da CPU alta", True, 90, "°C", "Acima do limite por 30 s"),
    "gpu_temp":   ("Temperatura da GPU alta", False, 90, "°C", "Acima do limite por 30 s (na BC-250 é o mesmo sensor da CPU)"),
    "disk_full":  ("Disco quase cheio", True, 90, "%", "Alguma partição acima do limite"),
    "mem_high":   ("Memória quase cheia", False, 92, "%", "Acima do limite por 60 s"),
    "fan_stall":  ("Ventoinha parada", True, None, None, "O painel controla a ventoinha, ela deveria girar e está em 0 RPM"),
    "suspicious": ("Processo suspeito", True, None, None, "Novo processo com sinal de risco alto"),
    "new_port":   ("Nova porta aberta para a rede", True, None, None, "Um programa passou a aceitar conexões de outras máquinas"),
    "panel_login": ("Novo acesso ao painel", True, None, None, "Alguém entrou no painel com a senha"),
    "login_fail": ("Tentativas de senha erradas", True, None, None, "Um IP foi bloqueado por errar a senha várias vezes"),
    "updates":    ("Atualizações disponíveis", False, None, None, "Quando aparecem atualizações novas"),
    "incident":   ("Incidente detectado", True, None, None, "GPU travou, programa ou serviço caiu, memória esgotada, erro de disco..."),
    "low_fps":    ("FPS baixo no jogo", False, 30, "FPS", "FPS médio abaixo do limite por 20 s (precisa da leitura de FPS)"),
}
COOLDOWN = 30 * 60   # a mesma condição contínua só alerta de novo depois disso


class Alerts:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.lock = threading.Lock()
        self.conf = self._load_conf()
        self.history: deque[dict] = deque(maxlen=300)
        try:
            self.history.extend(json.loads(LOG_FILE.read_text()))
        except (OSError, ValueError):
            pass
        self._since: dict[str, float] = {}     # condição -> quando começou
        self._fired: dict[str, float] = {}     # condição -> último alerta
        self._seen_susp: set[str] = set()
        self._ports: set | None = None
        self._last_updates = None
        self._seq = max((a.get("seq", 0) for a in self.history), default=0)
        self.started = time.time()

    # ---------- configuração ----------
    def _load_conf(self) -> dict:
        try:
            c = json.loads(CONF_FILE.read_text())
        except (OSError, ValueError):
            c = {}
        rules = c.get("rules", {})
        for rid, (_, on, val, _, _) in RULES.items():
            r = rules.setdefault(rid, {})
            r.setdefault("enabled", on)
            if val is not None:
                r.setdefault("value", val)
        ch = c.setdefault("channels", {})
        ch.setdefault("pc", False)
        ch.setdefault("ntfy", "")
        return {"rules": rules, "channels": ch}

    def config(self) -> dict:
        with self.lock:
            rules = [{"id": rid, "label": lab, "unit": unit, "help": hlp, **self.conf["rules"][rid]}
                     for rid, (lab, _, _, unit, hlp) in RULES.items()]
            return {"rules": rules, "channels": dict(self.conf["channels"])}

    def update_config(self, body: dict) -> dict:
        with self.lock:
            for rid, r in (body.get("rules") or {}).items():
                if rid not in RULES:
                    continue
                cur = self.conf["rules"][rid]
                if "enabled" in r:
                    cur["enabled"] = bool(r["enabled"])
                if "value" in r and RULES[rid][2] is not None:
                    try:
                        v = float(r["value"])
                    except (TypeError, ValueError):
                        raise ValueError(f"Valor inválido para {RULES[rid][0]}.") from None
                    if not 1 <= v <= 1000:
                        raise ValueError(f"Valor fora da faixa para {RULES[rid][0]}.")
                    cur["value"] = v
            ch = body.get("channels") or {}
            if "pc" in ch:
                self.conf["channels"]["pc"] = bool(ch["pc"])
            if "ntfy" in ch:
                url = str(ch["ntfy"] or "").strip()
                if url and not url.startswith(("https://", "http://")):
                    raise ValueError("O endereço do ntfy deve começar com https://")
                self.conf["channels"]["ntfy"] = url[:300]
            CONF_FILE.write_text(json.dumps(self.conf))
        return self.config()

    def _on(self, rid: str) -> bool:
        return bool(self.conf["rules"].get(rid, {}).get("enabled"))

    def _val(self, rid: str) -> float:
        return float(self.conf["rules"][rid].get("value") or RULES[rid][2])

    # ---------- disparo ----------
    def event(self, rid: str, text: str, level: str = "warning", key: str | None = None, pc: bool = True) -> None:
        """Dispara um alerta (respeitando liga/desliga e o cooldown por chave).
        pc=False: quem chamou já mostrou o aviso na tela do PC (não duplica)."""
        if not self._on(rid):
            return
        k = f"{rid}:{key or ''}"
        now = time.time()
        if now - self._fired.get(k, 0) < COOLDOWN:
            return
        self._fired[k] = now
        with self.lock:
            self._seq += 1
            a = {"seq": self._seq, "rule": rid, "title": RULES[rid][0], "text": text, "level": level,
                 "time": now, "read": False}
            self.history.appendleft(a)
            self._save()
        log.warning("alerta: %s — %s", a["title"], text)
        threading.Thread(target=self._deliver, args=(a, pc), daemon=True).start()

    def external(self, title: str, text: str = "", level: str = "info", icon: str = "", url: str = "", source: str = "",
                 duration: int = 0, display: str = "auto", silent: bool = False) -> dict:
        """Notificação de outro programa (webhook): não passa por regra nem cooldown.
        silent=True: fica só no painel e no Mural, sem PC nem ntfy."""
        with self.lock:
            self._seq += 1
            a = {"seq": self._seq, "rule": "webhook", "title": title, "text": text, "level": level, "icon": icon,
                 "url": url, "source": source, "duration": duration, "display": display, "time": time.time(), "read": False}
            self.history.appendleft(a)
            self._save()
        log.info("webhook: %s (%s)", title, source or "sem origem")
        if not silent:
            threading.Thread(target=self._deliver, args=(a,), daemon=True).start()
        return a

    def _deliver(self, a: dict, pc: bool = True) -> None:
        ch = self.conf["channels"]
        if pc and ch.get("pc"):
            try:
                from .desktop import notify_pc
                webhook = a.get("rule") == "webhook" and a["level"] != "critical"
                head = a["title"] if webhook else f"⚠ {a['title']}"
                notify_pc(head, a["text"], urgency="critical" if a["level"] == "critical" else "normal",
                          icon="dialog-information" if webhook else "dialog-warning")
            except Exception:  # noqa: BLE001
                pass
        if ch.get("ntfy"):
            self.send_ntfy(ch["ntfy"], a["title"], a["text"], a["level"])

    @staticmethod
    def send_ntfy(url: str, title: str, text: str, level: str = "warning") -> str | None:
        req = urllib.request.Request(url, data=text.encode(), method="POST", headers={
            "Title": title.encode("utf-8").decode("latin-1", "replace"),
            "Priority": "high" if level == "critical" else "default",
            "Tags": "warning" if level != "critical" else "rotating_light"})
        try:
            urllib.request.urlopen(req, timeout=8).read()
            return None
        except Exception as e:  # noqa: BLE001
            log.warning("ntfy: falha ao enviar: %s", e)
            return str(e)

    def _save(self) -> None:
        try:
            LOG_FILE.write_text(json.dumps(list(self.history)[:300]))
        except OSError:
            pass

    def list(self) -> dict:
        with self.lock:
            items = list(self.history)
        return {"alerts": items, "unread": sum(1 for a in items if not a["read"])}

    def mark_read(self) -> None:
        with self.lock:
            for a in self.history:
                a["read"] = True
            self._save()

    def clear(self) -> None:
        with self.lock:
            self.history.clear()
            self._save()

    def unread(self) -> int:
        with self.lock:
            return sum(1 for a in self.history if not a["read"])

    # ---------- avaliação periódica ----------
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="alerts").start()

    def _loop(self) -> None:
        time.sleep(15)   # deixa os coletores aquecerem
        n = 0
        while True:
            try:
                self._check(heavy=n % 6 == 0)   # coisas mais caras a cada ~30 s
            except Exception:  # noqa: BLE001
                log.exception("alertas: falha na avaliação")
            n += 1
            time.sleep(5)

    def _sustained(self, key: str, cond: bool, secs: float) -> bool:
        now = time.time()
        if not cond:
            self._since.pop(key, None)
            return False
        start = self._since.setdefault(key, now)
        return now - start >= secs

    def _check(self, heavy: bool) -> None:
        d = self.ctx.data
        cpu = d.get("cpu") or {}
        if self._on("cpu_temp") and cpu.get("temp") is not None:
            lim = self._val("cpu_temp")
            if self._sustained("cpu_temp", cpu["temp"] >= lim, 30):
                self.event("cpu_temp", f"CPU a {cpu['temp']:.0f} °C (limite {lim:.0f} °C).", "critical")
        for i, g in enumerate((d.get("gpu") or {}).get("gpus", [])):
            if self._on("gpu_temp") and g.get("temp") is not None:
                lim = self._val("gpu_temp")
                if self._sustained(f"gpu_temp{i}", g["temp"] >= lim, 30):
                    self.event("gpu_temp", f"GPU a {g['temp']:.0f} °C (limite {lim:.0f} °C).", "critical", key=str(i))
        if self._on("disk_full"):
            lim = self._val("disk_full")
            for p in (d.get("disks") or {}).get("partitions", []):
                if p.get("total") and p["used"] / p["total"] * 100 >= lim:
                    self.event("disk_full", f"{p['mount']} está {p['used'] / p['total'] * 100:.0f}% cheio.", key=p["mount"])
        mem = d.get("memory") or {}
        if self._on("mem_high") and mem.get("percent") is not None:
            lim = self._val("mem_high")
            if self._sustained("mem", mem["percent"] >= lim, 60):
                self.event("mem_high", f"Memória em {mem['percent']:.0f}% (limite {lim:.0f}%).")
        fan = d.get("fan") or {}
        if self._on("fan_stall") and fan.get("mode") in ("auto", "manual"):
            stalled = (fan.get("target_pct") or 0) > 0 and fan.get("rpm") in (0, None)
            if self._sustained("fan", stalled, 15):
                self.event("fan_stall", "A ventoinha deveria estar girando mas marca 0 RPM.", "critical")
        game = d.get("game") or {}
        if self._on("low_fps") and game.get("live") and game.get("fps") is not None:
            lim = self._val("low_fps")
            name = (game.get("game") or {}).get("name") or "jogo"
            if self._sustained("fps", game["fps"] < lim, 20):
                self.event("low_fps", f"{name}: {game['fps']:.0f} FPS (limite {lim:.0f}).", key=name)
        if not heavy:
            return
        if self._on("suspicious") and self.ctx.proctrack:
            from .procinspect import suspicious
            for it in suspicious(self.ctx.proctrack)["items"]:
                old = not it["alive"] and (it.get("end") or 0) < self.started   # já estava no histórico
                if it["score"] >= 50 and it["id"] not in self._seen_susp and not old:
                    self._seen_susp.add(it["id"])
                    reasons = "; ".join(f["text"] for f in it["flags"] if f["level"] == "alto")
                    self.event("suspicious", f"{it['name']} (PID {it['pid']}): {reasons}", "critical", key=it["id"])
        if self.ctx.netmon:
            snap = self.ctx.netmon.snapshot()
            ports = {(x["proto"], x["port"]) for x in snap["listening"] if x["scope"] == "rede"}
            if self._ports is None:
                self._ports = ports                  # linha de base: o que já estava aberto
            elif self._on("new_port"):
                for proto, port in sorted(ports - self._ports):
                    who = next((", ".join({p["name"] for p in x["procs"]}) or "outro usuário"
                                for x in snap["listening"] if (x["proto"], x["port"]) == (proto, port)), "?")
                    self.event("new_port", f"Porta {port}/{proto.upper()} aberta para a rede por {who}.", key=f"{proto}{port}")
                self._ports |= ports
        if self._on("updates") and getattr(self.ctx, "updates", None):
            st = self.ctx.updates.status()
            c = st.get("count")
            if c and c != self._last_updates and self._last_updates is not None:
                self.event("updates", f"{c} atualizações disponíveis" + (" (inclui kernel)" if st.get("kernel") else "") + ".",
                           "info", key=str(c))
            self._last_updates = c
