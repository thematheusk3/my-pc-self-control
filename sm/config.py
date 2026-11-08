"""Carrega configuração de config.toml (com defaults)."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8765
    sample_interval: float = 1.0      # segundos entre amostras de métricas
    process_interval: float = 2.0     # segundos entre varreduras de processos
    history_seconds: int = 300        # quanto histórico manter para os gráficos
    auth_enabled: bool = True
    allow_kill: bool = True
    allow_power: bool = True             # desligar/reiniciar pelo painel
    allow_unlock: bool = True            # bloquear/desbloquear a sessão gráfica pelo painel
    allow_launch: bool = True            # abrir aplicativos instalados pelo painel
    allow_screenshot: bool = True        # captura de tela (sempre avisa na tela do PC)
    allow_control: bool = True           # áudio e tela (volume, saída, resolução, brilho)
    disabled_collectors: list[str] = field(default_factory=list)
    # histórico de processos
    track_exited: bool = True
    track_interval: float = 0.5          # s entre varreduras de /proc
    exited_max: int = 3000               # quantos encerrados guardar
    track_persist: bool = True           # salva o histórico em data/exited.json
    net_monitor: bool = True             # monitor de conexões (aba Rede)
    net_interval: float = 2.0
    net_closed_max: int = 3000
    # controle da ventoinha
    fan_enabled: bool = True
    fan_chip: str = "auto"               # nome do chip hwmon (ou do driver); "auto" = detecta
    fan_channel: int = 0                 # pwmN / fanN; 0 = o primeiro que estiver girando
    fan_sensor: str = "k10temp/Tctl"     # chip/label da temperatura usada na curva
    fan_interval: float = 1.0
    fan_min_pct: float = 20.0
    fan_emergency_temp: float = 92.0
    fan_hysteresis: float = 3.0          # °C: só desce se a temperatura cair isso abaixo do ponto
    fan_step_down: float = 2.0           # %/s máximos de queda
    fan_on_exit: str = "firmware"        # firmware | full | keep
    # aviso de versão nova (consulta o GitHub; não instala nada)
    update_check: bool = True
    update_repo: str = "thematheusk3/my-pc-self-control"
    # histórico persistente, incidentes, serviços e logs
    history_enabled: bool = True         # banco data/store.db: eventos, incidentes, métricas, hardware
    history_days: int = 30               # retenção de eventos e métricas por minuto (por hora: 1 ano)
    incidents: bool = True               # vigia do journal + investigador de incidentes
    allow_services: bool = True          # iniciar/parar/reiniciar serviços pelo painel (os do sistema dependem do polkit)
    allow_logs: bool = True              # aba Logs (lê o journal inteiro do sistema)


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("SM_CONFIG", DEFAULT_CONFIG))
    raw: dict = {}
    if path.exists():
        with path.open("rb") as f:
            raw = tomllib.load(f)
    server = raw.get("server", {})
    sampling = raw.get("sampling", {})
    security = raw.get("security", {})
    collectors = raw.get("collectors", {})
    fan = raw.get("fan", {})
    update = raw.get("update", {})
    history = raw.get("history", {})
    repo = str(update.get("repo", Config.update_repo))
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError(f'[update] repo inválido: "{repo}" (use o formato dono/repositorio)')

    cfg = Config(
        host=server.get("host", Config.host),
        port=int(os.environ.get("SM_PORT", server.get("port", Config.port))),
        sample_interval=float(sampling.get("interval", Config.sample_interval)),
        process_interval=float(sampling.get("process_interval", Config.process_interval)),
        history_seconds=int(sampling.get("history_seconds", Config.history_seconds)),
        auth_enabled=bool(security.get("auth", True)),
        allow_kill=bool(security.get("allow_kill", True)),
        allow_power=bool(security.get("allow_power", True)),
        allow_unlock=bool(security.get("allow_unlock", True)),
        allow_launch=bool(security.get("allow_launch", True)),
        allow_screenshot=bool(security.get("allow_screenshot", True)),
        allow_control=bool(security.get("allow_control", True)),
        disabled_collectors=list(collectors.get("disabled", [])),
        track_exited=bool(sampling.get("track_exited", True)),
        track_interval=max(0.2, float(sampling.get("track_interval", Config.track_interval))),
        exited_max=max(100, int(sampling.get("exited_max", Config.exited_max))),
        track_persist=bool(sampling.get("track_persist", True)),
        net_monitor=bool(sampling.get("net_monitor", True)),
        net_interval=max(1.0, float(sampling.get("net_interval", Config.net_interval))),
        net_closed_max=max(100, int(sampling.get("net_closed_max", Config.net_closed_max))),
        fan_enabled=bool(fan.get("enabled", Config.fan_enabled)),
        fan_chip=str(fan.get("chip", Config.fan_chip)),
        fan_channel=int(fan.get("channel", Config.fan_channel)),
        fan_sensor=str(fan.get("sensor", Config.fan_sensor)),
        fan_interval=max(0.5, float(fan.get("interval", Config.fan_interval))),
        fan_min_pct=max(0.0, min(100.0, float(fan.get("min_pct", Config.fan_min_pct)))),
        fan_emergency_temp=float(fan.get("emergency_temp", Config.fan_emergency_temp)),
        fan_hysteresis=max(0.0, float(fan.get("hysteresis", Config.fan_hysteresis))),
        fan_step_down=max(0.1, float(fan.get("step_down", Config.fan_step_down))),
        fan_on_exit=fan.get("on_exit", Config.fan_on_exit) if fan.get("on_exit") in ("firmware", "full", "keep") else Config.fan_on_exit,
        update_check=bool(update.get("check", True)),
        update_repo=repo,
        history_enabled=bool(history.get("enabled", True)),
        history_days=max(1, min(3650, int(history.get("days", Config.history_days)))),
        incidents=bool(history.get("incidents", True)),
        allow_services=bool(security.get("allow_services", True)),
        allow_logs=bool(security.get("allow_logs", True)),
    )
    return cfg
