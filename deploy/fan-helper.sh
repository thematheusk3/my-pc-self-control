#!/bin/sh
# Helper do self-monitoring para a ventoinha. Instalado pelo install.sh em
# /usr/local/lib/self-monitoring/fan-helper (dono root) e chamado pelo systemd como root:
#   fan-helper grant <usuário>   (ExecStartPre)  dá ao usuário posse dos arquivos pwm* do hwmon
#   fan-helper release <datadir> (ExecStopPost)  se o painel controlava a ventoinha e não
#                                                devolveu (ex.: travou), devolve à placa
set -u
case "${1:-}" in
  grant)
    user="${2:?usuário}"
    for f in /sys/class/hwmon/hwmon*/pwm[0-9] /sys/class/hwmon/hwmon*/pwm[0-9]_enable; do
      [ -e "$f" ] && chown "$user" "$f" 2>/dev/null
    done
    exit 0 ;;
  release)
    marker="${2:?datadir}/fan.active"
    [ -f "$marker" ] || exit 0
    path=$(head -n1 "$marker")
    case "$path" in
      /sys/class/hwmon/hwmon[0-9]/pwm[0-9]_enable|/sys/class/hwmon/hwmon[0-9][0-9]/pwm[0-9]_enable)
        [ -e "$path" ] && echo 2 > "$path" ;;
    esac
    rm -f "$marker"
    exit 0 ;;
  *) echo "uso: $0 grant <usuário> | release <datadir>" >&2; exit 2 ;;
esac
