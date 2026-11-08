"""Regras que classificam linhas do journal em eventos (sem IA: expressões regulares e campos do journal).

Cada regra diz:
  kind      tipo do evento (ex.: gpu_hang)
  family    grupo usado para juntar eventos num incidente (gpu, crash, memory, service, thermal, storage,
            hardware, kernel, power)
  severity  info / notice / warning / error / critical
  trigger   se o evento sozinho abre um incidente
  label     texto curto em português

Para adicionar uma regra: acrescente um Rule em RULES. A primeira que casar vence, então regras mais
específicas vêm antes. `classify(entry)` recebe o dicionário do `journalctl -o json`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

COREDUMP_ID = "fc2e22bc6ee647b6b90729ab34a250b1"
UNIT_FAILED_ID = "d9b373ed55a64feb8242e02dbe79a49c"      # "Unit X entered failed state" / "Failed with result"
UNIT_RESULT_IDS = {UNIT_FAILED_ID, "98e322203f7a4ed290d09fe03c09fe15"}   # unidade falhou / processo saiu com erro
OOMD_KILL_ID = "1e2d5c33b5cc4f04a0ab36d3c49c8c3b"


@dataclass
class Rule:
    kind: str
    family: str
    severity: str
    label: str
    pattern: str | None = None          # regex no MESSAGE (sem diferenciar maiúsculas)
    ident: str | None = None            # regex no SYSLOG_IDENTIFIER / _COMM
    kernel: bool | None = None          # só mensagens do kernel?
    message_id: set[str] | None = None
    trigger: bool = False
    rx: re.Pattern | None = field(default=None, init=False, repr=False)
    irx: re.Pattern | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self.rx = re.compile(self.pattern, re.I) if self.pattern else None
        self.irx = re.compile(self.ident, re.I) if self.ident else None


RULES: list[Rule] = [
    # ---------- GPU ----------
    Rule("gpu_reset", "gpu", "critical", "Reset da GPU", kernel=True, trigger=True,
         pattern=r"(amdgpu|radeon|i915|xe|nouveau).*(gpu reset|reset (begin|start|succeeded|failed)|GPU recovery|"
                 r"device wedged|resetting)|GPU reset (begin|succeeded|failed)|ring .* reset"),
    Rule("gpu_hang", "gpu", "critical", "GPU travou (timeout de comandos)", kernel=True, trigger=True,
         pattern=r"ring \S+ timeout|job timed? ?out|\*ERROR\* .*timeout|GPU HANG|gpu hang|"
                 r"Xid\b.*\b(8|13|31|32|43|45|48|61|62|63|64|68|69|74|79|92|94|95|109|119|120)\b"),
    # comum em placas com interrupções perdidas (ex.: BC-250): só um aviso, a menos que venha junto de um hang
    Rule("gpu_fence", "gpu", "warning", "GPU perdeu interrupções (fence fallback)", kernel=True,
         pattern=r"fence fallback timer expired"),
    Rule("gpu_fault", "gpu", "error", "Falha de memória da GPU (page fault)", kernel=True, trigger=True,
         pattern=r"\[gfxhub\]|\[mmhub\]|VM_L2_PROTECTION_FAULT|\bpage fault\b.*(amdgpu|vm)|amdgpu.*\bfault\b|GCVM_L2_PROTECTION"),
    Rule("gpu_driver", "gpu", "warning", "Erro do driver de vídeo", kernel=True,
         pattern=r"(amdgpu|radeon|i915|nouveau|nvidia|drm).*(\*ERROR\*|error|failed|fail)"),
    # ---------- travamentos de programas ----------
    Rule("coredump", "crash", "error", "Programa travou (core dump)", message_id={COREDUMP_ID}, trigger=True),
    Rule("segfault", "crash", "error", "Programa travou (segfault)", kernel=True,
         pattern=r"segfault at|general protection fault ip:|traps: .* trap"),
    # ---------- memória ----------
    Rule("oom", "memory", "critical", "Memória esgotada: processo encerrado (OOM)", trigger=True,
         pattern=r"Out of memory: Killed process|oom-kill:|invoked oom-killer|Killed .* due to memory pressure|"
                 r"earlyoom.*(sending SIGTERM|killing)|low memory.*kill", message_id={OOMD_KILL_ID}),
    # ---------- kernel ----------
    Rule("kernel_bug", "kernel", "critical", "Falha no kernel", kernel=True, trigger=True,
         pattern=r"BUG: |Oops:|kernel BUG at|general protection fault, |Kernel panic|watchdog: BUG: soft lockup|"
                 r"rcu_(sched|preempt) detected stalls|hard LOCKUP|blocked for more than \d+ seconds"),
    Rule("kernel_warn", "kernel", "warning", "Aviso do kernel (WARNING com rastreio)", kernel=True,
         pattern=r"^WARNING: .* at "),
    # ---------- hardware ----------
    Rule("mce", "hardware", "critical", "Erro de hardware da CPU/memória (MCE)", kernel=True, trigger=True,
         pattern=r"Machine check|mce: \[Hardware Error\]|\[Hardware Error\]|EDAC .*(CE|UE) "),
    Rule("pcie_aer", "hardware", "warning", "Erro no barramento PCIe", kernel=True,
         pattern=r"AER: .*(error|Error)|PCIe Bus Error"),
    Rule("usb", "hardware", "notice", "Problema com dispositivo USB", kernel=True,
         pattern=r"usb \S+: (device descriptor read|device not accepting address|reset .* USB device|USB disconnect)"),
    # ---------- disco ----------
    Rule("disk_io", "storage", "critical", "Erro de leitura/escrita no disco", kernel=True, trigger=True,
         pattern=r"I/O error|blk_update_request|Buffer I/O error|nvme\S*: .*(timeout|I/O|reset|controller is down)|"
                 r"ata\d+.*(failed command|exception|link is slow|hard resetting)|critical medium error|"
                 r"(EXT4-fs|BTRFS|XFS) (error|critical|warning \(device .*\): .*(corrupt|csum))"),
    # ---------- temperatura / energia ----------
    Rule("thermal", "thermal", "critical", "Temperatura crítica / throttling", kernel=True, trigger=True,
         pattern=r"temperature above threshold|clock throttled|critical temperature|thermal.*(shutdown|critical)|"
                 r"Package temperature|Core temperature"),
    Rule("power_supply", "power", "warning", "Energia / bateria", pattern=r"under-?voltage|power supply .* (fail|lost)"),
    # ---------- serviços ----------
    Rule("unit_failed", "service", "error", "Serviço falhou", message_id=UNIT_RESULT_IDS, trigger=True),
    Rule("unit_failed", "service", "error", "Serviço falhou", ident=r"^systemd$", trigger=True,
         pattern=r"Failed with result|entered failed state|Main process exited, code=(killed|dumped)|"
                 r"Start request repeated too quickly"),
    # ---------- sessão gráfica ----------
    Rule("compositor", "session", "error", "Compositor / sessão gráfica com erro", ident=r"^(kwin_wayland|kwin_x11|gnome-shell|Xorg|plasmashell)$",
         pattern=r"(crash|fatal|segfault|lost connection to|GPU reset|context lost|graphics reset)"),
]

# unidades transitórias e ruidosas que não interessam como "serviço falhou"
IGNORED_UNITS = re.compile(r"^(systemd-coredump@|app-.*\.scope|run-.*\.(scope|service)|session-\d+\.scope|"
                           r"user@\d+\.service|dbus-:.*|plasma-.*-.*\.scope|.*\.mount$)")
BOOT_GRACE = 120    # s depois do boot em que avisos não abrem incidente
PRIO_SEVERITY = {0: "critical", 1: "critical", 2: "critical", 3: "error", 4: "warning", 5: "notice", 6: "info", 7: "info"}


def _boot_age(e: dict) -> float:
    try:
        return int(e.get("__MONOTONIC_TIMESTAMP")) / 1e6
    except (TypeError, ValueError):
        return 1e9


def is_kernel(e: dict) -> bool:
    return e.get("_TRANSPORT") == "kernel" or e.get("SYSLOG_IDENTIFIER") == "kernel"


def text(v) -> str:
    """O journal manda bytes não-UTF-8 como lista de números."""
    if isinstance(v, list):
        try:
            return bytes(v).decode(errors="replace")
        except (TypeError, ValueError):
            return ""
    return str(v or "")


def unit_of(e: dict) -> str | None:
    return text(e.get("UNIT") or e.get("USER_UNIT") or e.get("_SYSTEMD_USER_UNIT") or e.get("_SYSTEMD_UNIT")) or None


def classify(e: dict) -> dict | None:
    """Entrada do journal -> evento classificado, ou None se nenhuma regra casar.
    Mensagens de erro (prioridade <= 3) sem regra viram "error" genérico, sem abrir incidente."""
    msg = text(e.get("MESSAGE"))
    ident = text(e.get("SYSLOG_IDENTIFIER") or e.get("_COMM"))
    mid = text(e.get("MESSAGE_ID"))
    kernel = is_kernel(e)
    for r in RULES:
        if r.kernel is not None and r.kernel != kernel:
            continue
        if r.message_id and mid in r.message_id:
            pass
        elif r.message_id and not r.rx:
            continue
        else:
            if r.irx and not r.irx.search(ident):
                continue
            if r.rx and not r.rx.search(msg):
                continue
            if not r.rx and not r.irx:
                continue
        ev = _event(e, r.kind, r.family, r.severity, r.trigger, r.label, msg, ident)
        if r.kind == "unit_failed":
            unit = ev.get("unit")
            if not unit or IGNORED_UNITS.match(unit):
                return None
            if unit.startswith("app-"):      # app aberto pelo menu: o core dump já abre o incidente
                ev["trigger"], ev["family"] = False, "crash"
        if ev["trigger"] and r.family not in ("storage", "hardware") and _boot_age(e) < BOOT_GRACE:
            ev["trigger"] = False            # drivers iniciando costumam reclamar no boot
            ev["data"]["boot_noise"] = True
        return ev
    try:
        prio = int(e.get("PRIORITY", 6))
    except (TypeError, ValueError):
        prio = 6
    if prio <= 3:
        return _event(e, "error", "other", PRIO_SEVERITY[prio], False, "Erro registrado no sistema", msg, ident)
    return None


def _event(e: dict, kind: str, family: str, severity: str, trigger: bool, label: str, msg: str, ident: str) -> dict:
    try:
        ts = int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6
    except (TypeError, ValueError):
        ts = 0.0
    try:
        pid = int(e.get("_PID") or e.get("SYSLOG_PID") or 0) or None
    except (TypeError, ValueError):
        pid = None
    unit = unit_of(e)
    data: dict = {"label": label, "priority": e.get("PRIORITY"), "cursor": e.get("__CURSOR")}
    if kind == "coredump":
        exe = text(e.get("COREDUMP_EXE"))
        data.update({"exe": exe, "comm": text(e.get("COREDUMP_COMM")), "signal": text(e.get("COREDUMP_SIGNAL_NAME")),
                     "cmdline": text(e.get("COREDUMP_CMDLINE"))[:1000], "core_pid": e.get("COREDUMP_PID"),
                     "core_unit": text(e.get("COREDUMP_USER_UNIT") or e.get("COREDUMP_UNIT")) or None,
                     "uid": e.get("COREDUMP_UID"), "storage": text(e.get("COREDUMP_FILENAME")) or None})
        try:
            pid = int(e.get("COREDUMP_PID")) or pid
        except (TypeError, ValueError):
            pass
        unit = data["core_unit"] or unit
        msg = msg.splitlines()[0] if msg else f"{data['comm']} travou ({data['signal']})"
    if kind == "unit_failed":
        unit = text(e.get("UNIT") or e.get("USER_UNIT")) or _unit_from_msg(msg) or unit
        data["result"] = text(e.get("UNIT_RESULT")) or _result_from_msg(msg)
    return {"ts": ts, "source": "kernel" if is_kernel(e) else "journal", "kind": kind, "family": family,
            "severity": severity, "trigger": trigger, "unit": unit, "ident": ident or None, "pid": pid,
            "boot": text(e.get("_BOOT_ID")) or None, "message": msg[:2000], "data": data}


def _unit_from_msg(msg: str) -> str | None:
    m = re.match(r"^([\w@.\\-]+\.(service|socket|timer|scope|mount|target)):", msg)
    return m.group(1) if m else None


def _result_from_msg(msg: str) -> str | None:
    m = re.search(r"Failed with result '([^']+)'", msg)
    return m.group(1) if m else None
