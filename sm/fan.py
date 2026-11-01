"""Controle da ventoinha por PWM (hwmon).

Modos:
  auto      curva temperatura -> % com histerese (sobe rápido, desce devagar)
  manual    porcentagem fixa
  firmware  devolve o controle para a placa (pwmN_enable = 2)
  None      nunca configurado: o painel não toca na ventoinha

Segurança (valem para auto e manual):
  - temperatura >= emergency_temp  -> 100%
  - sem leitura de temperatura por alguns segundos -> 100%
  - nunca abaixo de min_pct
  - ao parar o serviço, devolve para a placa (on_exit = "firmware")

Para escrever em /sys/class/hwmon/.../pwmN o serviço precisa de permissão:
o install.sh instala um helper que dá posse dos arquivos pwm* ao usuário do serviço.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

import psutil

from .config import ROOT
from .hw import hwmon
from .hw.util import read, read_int

log = logging.getLogger(__name__)

STATE_FILE = ROOT / "data" / "fan.json"
ACTIVE_MARKER = ROOT / "data" / "fan.active"   # lido pelo helper root no ExecStopPost

MODES = ("auto", "manual", "firmware")
PWM_MANUAL, PWM_AUTO = 1, 2

# (°C, %) — pensadas para a BC-250, cujo Tctl fica ~55-65 °C em repouso
PRESETS = {
    "silencioso":  [[55, 20], [65, 30], [75, 45], [82, 65], [88, 85], [92, 100]],
    "equilibrado": [[50, 25], [60, 35], [70, 50], [78, 70], [85, 90], [90, 100]],
    "desempenho":  [[45, 35], [55, 50], [65, 65], [72, 80], [80, 95], [85, 100]],
}
DEFAULT_PRESET = "equilibrado"

TEMP_LOST_AFTER = 5.0      # s sem temperatura -> 100%
EXTERNAL_TOLERANCE = 6     # diferença (0-255) aceitável entre o escrito e o lido
CC_CHECK_EVERY = 10.0      # s entre checagens do CoolerControl


def pct_to_pwm(p: float) -> int:
    return max(0, min(255, round(p * 255 / 100)))


def pwm_to_pct(v: int | None) -> float | None:
    return None if v is None else round(v * 100 / 255, 1)


def interpolate(curve: list[list[float]], t: float) -> float:
    if t <= curve[0][0]:
        return curve[0][1]
    for (t0, p0), (t1, p1) in zip(curve, curve[1:]):
        if t <= t1:
            return p0 + (p1 - p0) * (t - t0) / (t1 - t0)
    return curve[-1][1]


def validate_curve(curve) -> list[list[float]]:
    if not isinstance(curve, list) or not 2 <= len(curve) <= 12:
        raise ValueError("A curva precisa ter de 2 a 12 pontos.")
    pts = []
    for p in curve:
        try:
            t, v = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError("Ponto inválido na curva.") from None
        if not 20 <= t <= 105:
            raise ValueError("Temperaturas da curva devem estar entre 20 e 105 °C.")
        if not 0 <= v <= 100:
            raise ValueError("Velocidades da curva devem estar entre 0 e 100%.")
        pts.append([round(t, 1), round(v, 1)])
    pts.sort(key=lambda p: p[0])
    for a, b in zip(pts, pts[1:]):
        if b[0] - a[0] < 1:
            raise ValueError("Dois pontos da curva estão na mesma temperatura.")
        if b[1] < a[1]:
            raise ValueError("A velocidade não pode diminuir quando a temperatura aumenta.")
    return pts


class FanController:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.cfg = ctx.config
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._temps: deque[float] = deque(maxlen=5)
        self._last_temp_ok = time.monotonic()
        self._auto_pct: float | None = None
        self._written: int | None = None      # último valor PWM que escrevemos
        self._mismatch = 0
        self._fw_set = False
        self._cc_checked = 0.0
        self.cc_running = False
        self.external_at: float | None = None
        self.error: str | None = None
        self.reason = ""
        self.target_pct: float | None = None
        self.temp: float | None = None
        self.temp_avg: float | None = None
        self.rpm: float | None = None
        self.pwm: int | None = None
        self.enable: int | None = None

        self.mode: str | None = None
        self.manual_pct: float = 50.0
        self.curve: list[list[float]] = [list(p) for p in PRESETS[DEFAULT_PRESET]]
        self._load_state()

        self.dir, self.n = self._find_channel()
        self.temp_path, self.temp_src = self._find_temp()
        self.label = (read(self.dir / f"fan{self.n}_label") if self.dir else None) or f"fan{self.n}"

    # ---------- descoberta ----------
    def _find_channel(self) -> tuple[Path | None, int]:
        """chip/canal do config; chip = "auto" e/ou channel = 0 escolhem sozinhos:
        o primeiro canal PWM cuja ventoinha está girando (RPM > 0)."""
        want_chip, want_n = self.cfg.fan_chip, int(self.cfg.fan_channel)
        for chip in hwmon.chips():
            name = read(chip / "name", "") or ""
            driver = ""
            try:
                driver = (chip / "device" / "driver").resolve().name
            except OSError:
                pass
            if want_chip != "auto" and want_chip not in (name, driver):
                continue
            if want_chip == "auto" and name.startswith(("amdgpu", "nouveau", "radeon")):
                continue   # ventoinha de placa de vídeo: deixa com o driver dela
            channels = [want_n] if want_n > 0 else sorted(
                int(f.name[3:]) for f in chip.glob("pwm[0-9]*") if f.name[3:].isdigit())
            for n in channels:
                if not (chip / f"pwm{n}").exists():
                    continue
                if want_n > 0 and want_chip != "auto":
                    return chip, n                     # configurado explicitamente
                if (read_int(chip / f"fan{n}_input") or 0) > 0:
                    return chip, n
        return None, max(want_n, 1)

    def _find_temp(self) -> tuple[Path | None, str | None]:
        want_chip, _, want_label = self.cfg.fan_sensor.partition("/")
        for chip in hwmon.chips():
            if read(chip / "name") != want_chip:
                continue
            for f in sorted(chip.glob("temp*_input")):
                idx = f.name[4:-6]
                label = read(chip / f"temp{idx}_label") or f"temp{idx}"
                if not want_label or label == want_label:
                    return f, f"{want_chip}/{label}"
        return None, None

    @property
    def available(self) -> bool:
        return self.dir is not None

    def _p(self, suffix: str = "") -> Path:
        return self.dir / f"pwm{self.n}{suffix}"

    def writable(self) -> bool:
        return self.available and os.access(self._p(), os.W_OK) and os.access(self._p("_enable"), os.W_OK)

    # ---------- estado persistido ----------
    def _load_state(self) -> None:
        try:
            s = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return
        if s.get("mode") in MODES:
            self.mode = s["mode"]
        try:
            self.manual_pct = max(0.0, min(100.0, float(s.get("manual_pct", self.manual_pct))))
        except (TypeError, ValueError):
            pass
        try:
            self.curve = validate_curve(s.get("curve", self.curve))
        except ValueError:
            log.warning("curva salva inválida; usando a padrão")

    def _save_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mode": self.mode, "manual_pct": self.manual_pct, "curve": self.curve}))
        tmp.replace(STATE_FILE)

    def _mark_active(self, on: bool) -> None:
        try:
            if on:
                if not ACTIVE_MARKER.exists():
                    ACTIVE_MARKER.write_text(f"/sys/class/hwmon/{self.dir.name}/pwm{self.n}_enable\n")
            else:
                ACTIVE_MARKER.unlink(missing_ok=True)
        except OSError:
            pass

    # ---------- API ----------
    def update(self, body: dict) -> dict:
        with self._lock:
            mode = body.get("mode", self.mode)
            if mode not in MODES:
                raise ValueError("Modo inválido.")
            manual = self.manual_pct
            if "manual_pct" in body:
                try:
                    manual = float(body["manual_pct"])
                except (TypeError, ValueError):
                    raise ValueError("Velocidade manual inválida.") from None
                if not 0 <= manual <= 100:
                    raise ValueError("Velocidade manual deve estar entre 0 e 100%.")
                manual = max(manual, self.cfg.fan_min_pct)
            curve = self.curve
            if "preset" in body:
                if body["preset"] not in PRESETS:
                    raise ValueError("Perfil desconhecido.")
                curve = [list(p) for p in PRESETS[body["preset"]]]
            elif "curve" in body:
                curve = validate_curve(body["curve"])
            if not self.writable():
                raise PermissionError(self._perm_msg())
            if mode != self.mode:
                self._auto_pct = None
            self.mode, self.manual_pct, self.curve = mode, round(manual, 1), curve
            self._save_state()
            log.info("ventoinha: modo=%s manual=%s%%", mode, self.manual_pct)
        self._wake.set()
        return {"ok": True}

    def _perm_msg(self) -> str:
        if not self.available:
            return "Ventoinha não encontrada."
        return (f"Sem permissão para escrever em {self._p()}. "
                "Rode  sudo ./install.sh  na pasta do self-monitoring para instalar a permissão.")

    def status(self) -> dict:
        with self._lock:
            ext = self.external_at is not None and time.time() - self.external_at < 30
            return {
                "available": self.available,
                "writable": self.writable() if self.available else False,
                "error": self.error,
                "device": f"{self.cfg.fan_chip}/pwm{self.n}",
                "label": self.label,
                "mode": self.mode,
                "manual_pct": self.manual_pct,
                "curve": self.curve,
                "presets": PRESETS,
                "min_pct": self.cfg.fan_min_pct,
                "emergency_temp": self.cfg.fan_emergency_temp,
                "hysteresis": self.cfg.fan_hysteresis,
                "temp": self.temp, "temp_avg": self.temp_avg, "temp_source": self.temp_src,
                "rpm": self.rpm,
                "duty_pct": pwm_to_pct(self.pwm),
                "pwm_enable": self.enable,
                "target_pct": None if self.target_pct is None else round(self.target_pct, 1),
                "reason": self.reason,
                "external": ext,
                "coolercontrol": self.cc_running,
            }

    # ---------- laço ----------
    def start(self) -> None:
        if not self.available:
            log.warning("ventoinha %s/pwm%d não encontrada; controle desativado", self.cfg.fan_chip, self.n)
            return
        if not self.writable():
            log.warning(self._perm_msg())
        threading.Thread(target=self._loop, daemon=True, name="fan").start()

    def _loop(self) -> None:
        last = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            dt, last = now - last, now
            try:
                with self._lock:
                    self._tick(dt)
                    self.error = None
            except PermissionError:
                self.error = self._perm_msg()
            except OSError as e:
                self.error = f"Erro ao acessar a ventoinha: {e}"
                log.warning("ventoinha: %s", e)
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                log.exception("ventoinha: erro inesperado")
            self._wake.wait(self.cfg.fan_interval)
            self._wake.clear()

    def _read_sensors(self) -> None:
        t = None
        if self.temp_path:
            v = read_int(self.temp_path)
            if v is not None and 1000 < v < 150_000:
                t = v / 1000
        if t is None:  # fallback: mesma lógica do coletor de CPU
            from .collectors.cpu import cpu_temp
            t, src = cpu_temp()
            if t is not None:
                self.temp_src = src
        self.temp = t
        if t is not None:
            self._temps.append(t)
            self._last_temp_ok = time.monotonic()
        self.temp_avg = round(sum(self._temps) / len(self._temps), 1) if self._temps else None
        rpm = read_int(self.dir / f"fan{self.n}_input")
        self.rpm = float(rpm) if rpm is not None else None
        self.pwm = read_int(self._p())
        self.enable = read_int(self._p("_enable"))

        now = time.monotonic()
        if now - self._cc_checked > CC_CHECK_EVERY:
            self._cc_checked = now
            self.cc_running = any(p.info["name"] == "coolercontrold"
                                  for p in psutil.process_iter(["name"]))

    def _tick(self, dt: float) -> None:
        self._read_sensors()
        mode = self.mode
        if mode is None:
            self.reason, self.target_pct = "Não gerenciada pelo painel", None
            return
        if mode == "firmware":
            self.target_pct = None
            self.reason = "Controlada pela placa"
            self._mark_active(False)
            if self.enable != PWM_AUTO:
                if self._fw_set:          # já tínhamos devolvido e alguém mudou de novo
                    self.external_at = time.time()
                self._write(self._p("_enable"), PWM_AUTO)
                self._fw_set = True
            self._written = None
            return
        self._fw_set = False

        # auto / manual
        cfg = self.cfg
        temp_lost = time.monotonic() - self._last_temp_ok > TEMP_LOST_AFTER
        peak = max(self._temps) if self._temps else None
        if temp_lost:
            pct, self.reason = 100.0, "Sem leitura de temperatura: 100% por segurança"
        elif peak is not None and peak >= cfg.fan_emergency_temp:
            pct, self.reason = 100.0, f"Emergência: {peak:.0f} °C ≥ {cfg.fan_emergency_temp:.0f} °C"
            self._auto_pct = 100.0
        elif mode == "manual":
            pct, self.reason = self.manual_pct, "Velocidade fixa"
        else:
            pct, self.reason = self._auto(dt), "Curva automática"
        pct = max(cfg.fan_min_pct, min(100.0, pct))
        self.target_pct = pct
        want = pct_to_pwm(pct)

        need = self.enable != PWM_MANUAL or self._written != want
        if not need and self.pwm is not None and abs(self.pwm - want) > EXTERNAL_TOLERANCE:
            self._mismatch += 1
            if self._mismatch >= 3:     # alguém mexeu (ex.: CoolerControl)
                self.external_at = time.time()
                need = True
        else:
            self._mismatch = 0
        if self.enable != PWM_MANUAL and self._written is not None:
            self.external_at = time.time()
        if need:
            self._write(self._p(), want)   # o driver já põe em modo manual
            if read_int(self._p("_enable")) != PWM_MANUAL:
                self._write(self._p("_enable"), PWM_MANUAL)
                self._write(self._p(), want)
            self._written, self._mismatch = want, 0
            self._mark_active(True)

    def _auto(self, dt: float) -> float:
        t = self.temp_avg
        up = interpolate(self.curve, t)
        cur = self._auto_pct
        if cur is None or up >= cur:
            new = up
        else:
            down = interpolate(self.curve, t + self.cfg.fan_hysteresis)
            new = max(down, cur - self.cfg.fan_step_down * dt) if down < cur else cur
        self._auto_pct = new
        return new

    @staticmethod
    def _write(path: Path, value: int) -> None:
        path.write_text(f"{value}\n")

    # ---------- saída ----------
    def release(self) -> None:
        """Chamado ao encerrar o serviço."""
        self._stop.set()
        if not self.available:
            return
        with self._lock:
            if self.mode not in ("auto", "manual") or self._written is None:
                return
            try:
                action = self.cfg.fan_on_exit
                if action == "full":
                    self._write(self._p(), 255)
                elif action == "firmware":
                    self._write(self._p("_enable"), PWM_AUTO)
                log.info("ventoinha: serviço encerrando, ação de saída = %s", action)
                if action != "keep":
                    self._mark_active(False)
            except OSError as e:
                log.warning("ventoinha: falha ao restaurar na saída: %s", e)

