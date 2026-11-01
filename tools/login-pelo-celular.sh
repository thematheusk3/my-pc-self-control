#!/usr/bin/env bash
# Liga/desliga o "entrar pelo celular":
#   o PC liga, entra sozinho no seu usuário e a sessão já começa com a TELA BLOQUEADA;
#   você desbloqueia pelo painel (Sistema > Sessão) digitando a senha do painel.
#
#   sudo ./tools/login-pelo-celular.sh ativar
#   sudo ./tools/login-pelo-celular.sh desativar
#        ./tools/login-pelo-celular.sh status
#
# Funciona com Plasma (plasmalogin ou SDDM). Faz backup da configuração antes de mexer.
set -euo pipefail
ACTION="${1:-status}"
TARGET_USER="${SUDO_USER:-$USER}"
CONF=""
for c in /etc/plasmalogin.conf /etc/sddm.conf; do [[ -f "$c" ]] && { CONF="$c"; break; }; done
[[ -n "$CONF" ]] || { echo "Não encontrei /etc/plasmalogin.conf nem /etc/sddm.conf (gerenciador de login não suportado)."; exit 1; }

as_user() { if [[ $EUID -eq 0 ]]; then sudo -u "$TARGET_USER" "$@"; else "$@"; fi; }
lock_on_start() { as_user kreadconfig6 --file kscreenlockerrc --group Daemon --key LockOnStart --default false; }
autologin_user() { python3 - "$CONF" <<'PY'
import configparser, sys
cp = configparser.ConfigParser(strict=False, interpolation=None); cp.optionxform = str; cp.read(sys.argv[1])
print(cp.get("Autologin", "User", fallback=""))
PY
}
set_autologin() {   # $1 = usuário ou vazio para remover
  cp -n "$CONF" "$CONF.bak-self-monitoring" 2>/dev/null || true
  python3 - "$CONF" "$1" <<'PY'
import configparser, sys
path, user = sys.argv[1], sys.argv[2]
cp = configparser.ConfigParser(strict=False, interpolation=None); cp.optionxform = str; cp.read(path)
if not cp.has_section("Autologin"):
    cp.add_section("Autologin")
if user:
    cp.set("Autologin", "User", user)
    if not cp.get("Autologin", "Session", fallback=""):
        cp.set("Autologin", "Session", "plasma")
    cp.set("Autologin", "Relogin", "false")
else:
    cp.remove_option("Autologin", "User")
with open(path, "w") as f:
    cp.write(f, space_around_delimiters=False)
PY
}

case "$ACTION" in
  status)
    echo "Configuração:        $CONF"
    echo "Login automático:    $(autologin_user || true)"
    echo "Bloquear ao iniciar: $(lock_on_start)"
    ;;
  ativar)
    [[ $EUID -eq 0 ]] || { echo "Rode com sudo: sudo $0 ativar"; exit 1; }
    [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]] || { echo "Rode com sudo a partir do seu usuário (não como root direto)."; exit 1; }
    # 1º o bloqueio ao iniciar (se falhar, NÃO liga o login automático)
    as_user kwriteconfig6 --file kscreenlockerrc --group Daemon --key LockOnStart true
    [[ "$(lock_on_start)" == "true" ]] || { echo "Não consegui ativar o bloqueio ao iniciar; nada foi alterado."; exit 1; }
    set_autologin "$TARGET_USER"
    echo "Ativado para '$TARGET_USER'. Backup da configuração: $CONF.bak-self-monitoring"
    echo
    echo "Na próxima vez que o PC ligar ele entra sozinho e mostra a tela de bloqueio."
    echo "Desbloqueie pelo painel (Sistema > Sessão) ou digitando a senha no próprio PC."
    echo
    echo "ATENÇÃO (KWallet): sem digitar a senha no login, a carteira do KDE não abre sozinha."
    echo "Programas que guardam senhas nela (Discord, navegadores Chromium, Wi-Fi do usuário...)"
    echo "vão pedir a senha da carteira na primeira vez. Teste uma reinicialização estando perto do PC."
    ;;
  desativar)
    [[ $EUID -eq 0 ]] || { echo "Rode com sudo: sudo $0 desativar"; exit 1; }
    set_autologin ""
    as_user kwriteconfig6 --file kscreenlockerrc --group Daemon --key LockOnStart false
    echo "Desativado: o PC volta a mostrar a tela de login normal."
    ;;
  *) echo "uso: $0 ativar | desativar | status"; exit 2 ;;
esac
