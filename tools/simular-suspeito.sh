#!/usr/bin/env bash
# Cria processos INOFENSIVOS que imitam sinais de malware, para ver a aba Processos > Suspeitos.
# Todos são cópias do comando `sleep` (só esperam) e são encerrados e apagados no final.
#   ./tools/simular-suspeito.sh        # roda por 120 s
#   ./tools/simular-suspeito.sh 300    # roda por 300 s
set -u
DUR="${1:-120}"
SLEEP="$(command -v sleep)"
PIDS=()
cleanup() {
  kill "${PIDS[@]}" 2>/dev/null
  rm -f /tmp/.sm-teste-oculto /dev/shm/sm-teste-apagado
  echo; echo "Simulação encerrada. Os processos vão aparecer em Processos > Encerrados (e em Suspeitos como 'encerrado')."
}
trap cleanup EXIT INT TERM

echo "Iniciando processos de teste por ${DUR}s (Ctrl+C para parar antes)…"

# 1) executável oculto rodando de pasta temporária  -> Alto: pasta temporária + arquivo oculto
cp "$SLEEP" /tmp/.sm-teste-oculto && /tmp/.sm-teste-oculto "$DUR" & PIDS+=($!)
echo "  1. /tmp/.sm-teste-oculto            → executável oculto em /tmp"

# 2) programa que se apaga do disco depois de iniciar  -> Alto: executável apagado
cp "$SLEEP" /dev/shm/sm-teste-apagado && /dev/shm/sm-teste-apagado "$DUR" & PIDS+=($!)
sleep 0.5; rm -f /dev/shm/sm-teste-apagado
echo "  2. /dev/shm/sm-teste-apagado        → apagou o próprio arquivo e continua rodando"

# 3) processo disfarçado de thread do kernel  -> Alto: imita [kworker]
bash -c "exec -a '[kworker/7:7-sm-teste]' '$SLEEP' '$DUR'" & PIDS+=($!)
echo "  3. [kworker/7:7-sm-teste]           → nome imitando thread do kernel"

# 4) linha de comando de minerador (o endereço não existe e nada é baixado)  -> Alto: minerador
bash -c "'$SLEEP' '$DUR'; : --url stratum+tcp://pool.exemplo.invalid:3333 --donate-level 1" & PIDS+=($!)
echo "  4. bash … stratum+tcp://…           → parece minerador de criptomoeda"

echo
echo "Abra o painel em Processos > Suspeitos (ou /#processes:susp)."
wait
