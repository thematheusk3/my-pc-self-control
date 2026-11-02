#!/usr/bin/env bash
# Instala/atualiza o self-monitoring em /opt/applications/self-monitoring e cria o serviço systemd.
#
#   sudo ./install.sh               instala ou atualiza (serviço roda como o seu usuário; recomendado)
#   sudo ./install.sh --root        serviço roda como root (vê e finaliza qualquer processo, vê todas as conexões)
#   sudo ./install.sh --user NOME   serviço roda como NOME (quando o terminal já é root, ex.: depois de `su -`)
#   sudo ./install.sh --no-service  só copia os arquivos
#   sudo ./install.sh --uninstall   remove serviço, helper e regra do polkit (mantém config e dados)
#   sudo ./install.sh --purge       como --uninstall e apaga também /opt/applications/self-monitoring
#
# Atualizar depois de um `git pull`: rode `sudo ./install.sh` de novo. Config (config.toml) e dados
# (data/: senha, sessões, históricos) são preservados.
set -euo pipefail

DEST="${SM_DEST:-/opt/applications/self-monitoring}"
SRC="$(cd "$(dirname "$0")" && pwd)"
RUN_AS="${SUDO_USER:-${USER:-$(id -un)}}"
EXPLICIT_ROOT=0
SERVICE=1
UNINSTALL=0
PURGE=0
while [[ $# -gt 0 ]]; do
  a="$1"; shift
  case "$a" in
    --root) RUN_AS=root; EXPLICIT_ROOT=1 ;;
    --user) RUN_AS="${1:?--user precisa do nome do usuário}"; shift ;;
    --no-service) SERVICE=0 ;;
    --uninstall) UNINSTALL=1 ;;
    --purge) UNINSTALL=1; PURGE=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $a (use --help)"; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 && ( $SERVICE -eq 1 || $UNINSTALL -eq 1 ) ]]; then
  echo "Rode com sudo: sudo $0 $*"; exit 1
fi

# ---------- desinstalar ----------
if [[ $UNINSTALL -eq 1 ]]; then
  systemctl disable --now self-monitoring.service 2>/dev/null || true
  rm -f /etc/systemd/system/self-monitoring.service
  systemctl daemon-reload
  rm -rf /usr/local/lib/self-monitoring
  rm -f /etc/polkit-1/rules.d/50-self-monitoring.rules
  if [[ $PURGE -eq 1 ]]; then
    rm -rf "$DEST"
    echo "Removido, inclusive $DEST (configuração e dados)."
  else
    echo "Serviço removido. Configuração e dados mantidos em $DEST (use --purge para apagar)."
  fi
  echo "Se você ativou o login pelo celular, desative antes: sudo ./tools/login-pelo-celular.sh desativar"
  exit 0
fi

# ---------- dependências ----------
need_pkgs() {
  python3 -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null || { echo "python3 >= 3.11 é necessário."; return 0; }
  python3 -c "import psutil" 2>/dev/null && return 1 || return 0
}
if ! command -v python3 >/dev/null || need_pkgs; then
  if command -v pacman >/dev/null; then CMD=(pacman -S --needed --noconfirm python python-psutil iproute2)
  elif command -v apt-get >/dev/null; then CMD=(apt-get install -y python3 python3-psutil iproute2)
  elif command -v dnf >/dev/null; then CMD=(dnf install -y python3 python3-psutil iproute)
  elif command -v zypper >/dev/null; then CMD=(zypper --non-interactive install python3 python3-psutil iproute2)
  else echo "Instale python3 (>= 3.11) e psutil para python3, e rode de novo."; exit 1
  fi
  echo "Faltam dependências. Vou rodar: ${CMD[*]}"
  if [[ -t 0 ]]; then read -rp "Continuar? [S/n] " ok; [[ "${ok,,}" =~ ^(s|y|)$ ]] || exit 1; fi
  "${CMD[@]}"
  need_pkgs && { echo "Ainda falta python3 >= 3.11 com psutil."; exit 1; }
fi

# Terminal já como root (su -, login como root) sem --root: o serviço viraria root sem querer e não acharia a
# sessão gráfica, o áudio nem a tela. Usa o dono da sessão gráfica aberta, se houver.
if [[ "$RUN_AS" == root && $EXPLICIT_ROOT -eq 0 && $UNINSTALL -eq 0 ]]; then
  GUI_USER=$(loginctl list-sessions --no-legend 2>/dev/null | awk '$2 >= 1000 {print $3}' | while read -r u; do
    for s in $(loginctl show-user "$u" -p Sessions --value 2>/dev/null); do
      [[ "$(loginctl show-session "$s" -p Type --value 2>/dev/null)" =~ ^(wayland|x11)$ ]] && { echo "$u"; break 2; }
    done; done | head -n1)
  if [[ -n "$GUI_USER" ]]; then
    RUN_AS="$GUI_USER"
    echo "Terminal como root: o serviço vai rodar como $RUN_AS (dono da sessão gráfica). Para root de verdade, use --root."
  else
    echo "Rode a partir da sua conta (sudo ./install.sh), ou informe o usuário: sudo ./install.sh --user NOME"
    echo "(para rodar como root de propósito: --root)"
    exit 1
  fi
fi
id "$RUN_AS" >/dev/null 2>&1 || { echo "Usuário não existe: $RUN_AS"; exit 1; }

# ---------- arquivos ----------
OWNER="${SUDO_USER:-$RUN_AS}"
mkdir -p "$DEST"
if [[ "$SRC" != "$DEST" ]]; then
  # copia o código; nunca sobrescreve config.toml nem data/ da máquina
  tar -C "$SRC" --exclude=./data --exclude='__pycache__' --exclude=./.git --exclude=./.github \
      --exclude=./tests --exclude=./config.toml --exclude='./watch_*' -cf - . | tar -C "$DEST" -xf -
fi
if [[ ! -f "$DEST/config.toml" ]]; then
  cp "$DEST/config.example.toml" "$DEST/config.toml"
  echo "Criado $DEST/config.toml a partir do exemplo (edite à vontade; atualizações não o sobrescrevem)."
fi
mkdir -p "$DEST/data"
chown -R "$OWNER:" "$DEST"
[[ "$RUN_AS" == root ]] || chown -R "$RUN_AS:" "$DEST/data"
chmod 700 "$DEST/data"
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$DEST'); import sm; print(sm.__version__)")
echo "self-monitoring $VERSION copiado para $DEST"

if [[ $SERVICE -eq 1 ]]; then
  # helper da ventoinha: cópia de dono root (o serviço o executa como root no start/stop)
  install -D -o root -g root -m 755 "$DEST/deploy/fan-helper.sh" /usr/local/lib/self-monitoring/fan-helper
  # polkit: permite ao usuário do serviço desligar/reiniciar pelo painel (security.allow_power)
  if [[ "$RUN_AS" != root && -d /etc/polkit-1/rules.d ]]; then
    sed "s|@USER@|$RUN_AS|" "$DEST/deploy/50-self-monitoring.rules" > /etc/polkit-1/rules.d/50-self-monitoring.rules
    chmod 644 /etc/polkit-1/rules.d/50-self-monitoring.rules
  fi
  # brilho/contraste do monitor (DDC/CI): acesso ao barramento i2c mesmo sem sessão aberta
  if [[ "$RUN_AS" != root ]] && getent group i2c >/dev/null && ! id -nG "$RUN_AS" | grep -qw i2c; then
    usermod -aG i2c "$RUN_AS" && echo "Usuário $RUN_AS adicionado ao grupo i2c (controle de brilho do monitor)."
  fi
  # como root, os comandos da sessão (áudio, tela, notificações) rodam como o dono da área de trabalho
  DESKTOP_USER="$OWNER"; [[ "$DESKTOP_USER" == root ]] && DESKTOP_USER=""
  sed -e "s|@USER@|$RUN_AS|" -e "s|@DIR@|$DEST|g" -e "s|@DESKTOP_USER@|$DESKTOP_USER|" \
      "$DEST/deploy/self-monitoring.service" > /etc/systemd/system/self-monitoring.service
  systemctl daemon-reload
  systemctl enable self-monitoring.service >/dev/null 2>&1
  systemctl restart self-monitoring.service
  PORT=$(python3 -c "import tomllib;print(tomllib.load(open('$DEST/config.toml','rb')).get('server',{}).get('port',8765))")
  if command -v ufw >/dev/null && systemctl is-active -q ufw; then
    ufw allow from 192.168.0.0/16 to any port "$PORT" proto tcp comment self-monitoring >/dev/null || true
    ufw allow from 10.0.0.0/8 to any port "$PORT" proto tcp comment self-monitoring >/dev/null || true
    echo "ufw: porta $PORT liberada para a rede local."
  elif command -v firewall-cmd >/dev/null && systemctl is-active -q firewalld; then
    firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null || true
    echo "firewalld: porta $PORT liberada."
  fi
  if ! [[ -f "$DEST/data/auth.json" ]] && [[ -t 0 ]]; then
    echo; echo "Nenhuma senha cadastrada ainda."
    if [[ "$RUN_AS" == root ]]; then python3 "$DEST/server.py" --set-password
    else sudo -u "$RUN_AS" python3 "$DEST/server.py" --set-password; fi
    systemctl restart self-monitoring.service
  fi
  sleep 2
  echo
  echo "Serviço instalado (usuário: $RUN_AS). Status: $(systemctl is-active self-monitoring)"
  for ip in $(hostname -I 2>/dev/null || ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1); do
    [[ "$ip" == *:* ]] && continue
    echo "Acesse: http://$ip:$PORT"
  done
fi
