#!/usr/bin/env bash
# Atualiza o self-monitoring: baixa a versão nova do GitHub (git pull) e reinstala do mesmo jeito que o serviço
# já está (como root ou como o seu usuário). Configuração, senha, sessões e históricos são preservados.
#
#   ./atualizar.sh              atualiza (pede a senha do sudo só para reinstalar)
#   ./atualizar.sh --verificar  só diz se há versão nova, sem mudar nada
#   ./atualizar.sh --forcar     reinstala mesmo já estando na versão mais nova (ex.: o serviço ficou estranho)
#
# Também funciona a partir da cópia instalada: /opt/applications/self-monitoring/atualizar.sh
# (ela sabe de qual pasta do projeto foi instalada).
set -euo pipefail

CHECK=0
FORCE=0
for a in "$@"; do
  case "$a" in
    --verificar) CHECK=1 ;;
    --forcar) FORCE=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $a (use --help)"; exit 1 ;;
  esac
done

DEST="${SM_DEST:-/opt/applications/self-monitoring}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$HERE"
if ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  # rodando da cópia instalada: o instalador anota de onde veio
  REPO="$(cat "$DEST/.origem" 2>/dev/null || true)"
  if [[ -z "$REPO" ]] || ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Não achei a pasta do projeto (clone do git). Rode o atualizar.sh de dentro dela, por exemplo:"
    echo "  cd ~/Projetos/self-monitoring && ./atualizar.sh"
    exit 1
  fi
fi

# o git roda como o dono da pasta do projeto (não como root, para não bagunçar as permissões)
OWNER="$(stat -c %U "$REPO")"
git_() { if [[ $EUID -eq 0 && "$OWNER" != root ]]; then sudo -u "$OWNER" git -C "$REPO" "$@"; else git -C "$REPO" "$@"; fi; }
version() { python3 -c "import sys; sys.path.insert(0, '$1'); import sm; print(sm.__version__)" 2>/dev/null || echo "?"; }

echo "Projeto: $REPO"
INSTALLED="$(version "$DEST")"
echo "Instalada: $INSTALLED"

if [[ -n "$(git_ status --porcelain --untracked-files=no)" ]]; then
  echo
  echo "Há alterações locais na pasta do projeto; o git pull não vai sobrescrevê-las:"
  git_ status --short --untracked-files=no
  echo "Para descartá-las e ficar igual ao GitHub: git -C \"$REPO\" checkout -- .   (e rode de novo)"
  [[ $CHECK -eq 1 ]] || exit 1
  echo
fi

echo "Procurando versão nova no GitHub..."
git_ fetch --quiet
BRANCH="$(git_ rev-parse --abbrev-ref HEAD)"
UPSTREAM="$(git_ rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "origin/$BRANCH")"
BEHIND="$(git_ rev-list --count "HEAD..$UPSTREAM")"
REMOTE_VERSION="$(git_ show "$UPSTREAM:sm/__init__.py" 2>/dev/null | sed -n 's/^__version__ = "\(.*\)"/\1/p')"
REPO_VERSION="$(version "$REPO")"

if [[ "$BEHIND" -gt 0 ]]; then
  echo "Versão nova no GitHub: ${REMOTE_VERSION:-?} ($BEHIND commit(s)):"
  git_ log --format='  - %s' "HEAD..$UPSTREAM"
elif [[ "$REPO_VERSION" != "$INSTALLED" ]]; then
  echo "A pasta do projeto já tem a $REPO_VERSION, mas a instalada é a $INSTALLED."
else
  echo "Já está na versão mais nova ($INSTALLED)."
  [[ $FORCE -eq 1 ]] || exit 0
fi
[[ $CHECK -eq 1 ]] && exit 0

if [[ "$BEHIND" -gt 0 ]]; then
  git_ pull --ff-only --quiet
fi

# reinstala do mesmo jeito: serviço como root continua root; como usuário, continua no mesmo usuário
ARGS=()
SVC_USER="$(systemctl show self-monitoring -p User --value 2>/dev/null || true)"
if [[ -f /etc/systemd/system/self-monitoring.service ]]; then
  if [[ -z "$SVC_USER" || "$SVC_USER" == root ]]; then ARGS=(--root); else ARGS=(--user "$SVC_USER"); fi
  echo "Reinstalando (serviço como ${SVC_USER:-root})..."
else
  echo "Serviço ainda não instalado; instalando como $(id -un)..."
fi
if [[ $EUID -eq 0 ]]; then "$REPO/install.sh" "${ARGS[@]}"; else sudo "$REPO/install.sh" "${ARGS[@]}"; fi

echo
echo "Atualizado: $INSTALLED → $(version "$DEST")"
