#!/usr/bin/env bash
# Coleta o que é preciso para entender por que algo não aparece no painel (sessão, áudio, tela, ventoinhas).
# Não muda nada na máquina. Rode na conta que fica logada na tela e cole a saída:
#
#   /opt/applications/self-monitoring/tools/diagnostico.sh

set -u
sec() { printf '\n===== %s =====\n' "$1"; }

sec "Serviço"
systemctl show self-monitoring -p User -p ActiveState -p Environment 2>&1
echo "terminal: $(id -un) (uid $(id -u)), SUDO_USER=${SUDO_USER:-}"

sec "Sessões (logind)"
loginctl list-sessions --no-legend 2>&1
for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do
  echo "-- $s: $(loginctl show-session "$s" -p Name -p Type -p Class -p Active -p State -p LockedHint -p Desktop 2>&1 | tr '\n' ' ')"
done

SVC_USER=$(systemctl show self-monitoring -p User --value 2>/dev/null)
SVC_USER=${SVC_USER:-root}
if [[ $EUID -eq 0 && "$SVC_USER" != root ]]; then RUN=(sudo -u "$SVC_USER"); else RUN=(); fi
SVC_UID=$(id -u "$SVC_USER" 2>/dev/null)
ENV=(env XDG_RUNTIME_DIR="/run/user/$SVC_UID" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$SVC_UID/bus")

sec "Ambiente gráfico do systemd --user de $SVC_USER"
mapfile -t GUI < <("${RUN[@]}" "${ENV[@]}" systemctl --user show-environment 2>/dev/null \
  | grep -E '^(WAYLAND_DISPLAY|DISPLAY|XAUTHORITY|XDG_SESSION_TYPE|XDG_CURRENT_DESKTOP|QT_QPA_PLATFORM)=')
printf '%s\n' "${GUI[@]}"
[[ ${#GUI[@]} -gt 0 ]] || echo "(sem WAYLAND_DISPLAY/DISPLAY: a sessão gráfica não exportou o ambiente ou o serviço está na conta errada)"

sec "Áudio (pactl)"
"${RUN[@]}" "${ENV[@]}" pactl info 2>&1 | grep -E 'Server Name|Default Sink|Connection' || echo "pactl falhou"

sec "Tela (kscreen-doctor)"
"${RUN[@]}" "${ENV[@]}" "${GUI[@]}" kscreen-doctor -o 2>&1 | head -15
echo "código de saída: ${PIPESTATUS[0]}"

sec "Placa-mãe"
for f in sys_vendor product_name board_vendor board_name; do printf '%s: %s\n' "$f" "$(cat /sys/class/dmi/id/$f 2>/dev/null)"; done

sec "Sensores hwmon"
for h in /sys/class/hwmon/hwmon*; do
  drv=$(basename "$(readlink -f "$h/device/driver" 2>/dev/null)" 2>/dev/null)
  fans=$(for f in "$h"/fan*_input; do [[ -e $f ]] && printf '%s=%s ' "$(basename "$f" _input)" "$(cat "$f" 2>/dev/null)"; done)
  pwms=$(ls "$h" 2>/dev/null | grep -cE '^pwm[0-9]+$')
  echo "$(basename "$h"): $(cat "$h/name" 2>/dev/null) [${drv:-?}] fans: ${fans:-nenhuma} pwm: $pwms"
done
echo "módulos de Super I/O carregados: $(lsmod 2>/dev/null | awk '{print $1}' | grep -E '^(nct6775|nct6683|it87|f71882fg|w83627ehf|asus_ec_sensors|asus_wmi_sensors|dell_smm_hwmon|thinkpad_acpi)$' | tr '\n' ' ')"
journalctl -k -b 2>/dev/null | grep -iE 'nct67|it87|resource conflict|ACPI: OSL: Resource' | tail -5

