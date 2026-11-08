# Frota: vários computadores num servidor central (planejado)

Objetivo: cada PC continua rodando o self-monitoring sozinho (painel próprio, funciona sem rede) e, se configurado,
**envia** o que importa para um servidor central, que mostra todas as máquinas: estado, incidentes, histórico de
métricas e de hardware.

## O que já está pronto (0.6.0)

- **Identidade da máquina** (`sm/fleet.py`): `machine` = 16 hex do SHA-256 de `self-monitoring:` + `/etc/machine-id`.
  É estável entre reinícios e reinstalações do painel, e não expõe o machine-id original. Aparece em `GET /api/info`
  (`machine`) e `GET /api/fleet`.
- **Banco com o formato do servidor** (`sm/store.py`, `data/store.db`): toda linha de `events`, `incidents`, `metrics`
  e `hardware` leva `machine` e um `seq` global crescente, atualizado também quando a linha muda (ex.: incidente
  resolvido). O servidor pode usar o mesmo esquema com várias máquinas na mesma tabela.
- **Pacote de envio**: `GET /api/fleet/export?since=<seq>&limit=1000` devolve

  ```json
  {"schema": 1, "sent": 1794177451.0, "identity": {"machine": "...", "hostname": "...", "os": "linux", "version": "0.6.0"},
   "events": [...], "incidents": [...], "hardware": [...], "metrics": [...], "next": 1234, "more": false}
  ```

  `next` é o `seq` a usar no próximo pedido; `more = true` indica que há mais páginas.

## O que falta

### No agente (este painel)

1. Configuração:

   ```toml
   [fleet]
   server = "https://central.exemplo:8800"
   token = "..."          # gerado no servidor, por máquina
   interval = 30          # s entre envios
   ```

2. Uma thread que guarda `fleet_sent_seq` na tabela `meta`, chama `fleet.package(store, since)` e faz
   `POST {server}/api/fleet/ingest` com `Authorization: Bearer <token>`. Em erro, tenta de novo com espera crescente;
   como o envio é por `seq`, nada se perde se o servidor ficar fora do ar (o banco local guarda os dias configurados).
3. Um "batimento" leve a cada envio: snapshot atual resumido (CPU, memória, temperaturas, GPU, jogo, alertas não lidos).
4. Opcional: o servidor responde com comandos pendentes (ex.: "investigar momento X"), sempre limitados ao que
   `[security]` desta máquina permite.

### No servidor central (projeto separado ou modo `--server` deste)

- `POST /api/fleet/ingest`: valida o token da máquina e o `schema`, faz upsert por `(machine, id)` em `events`,
  `incidents`, `hardware` e por `(machine, res, key, ts)` em `metrics`. Idempotente: reenviar o mesmo pacote não
  duplica nada.
- Telas: lista de máquinas (online/offline pelo último batimento, incidentes abertos, temperatura), incidentes de
  todas as máquinas, comparação de hardware/versões entre máquinas e histórico de métricas.
- Retenção própria, maior que a de cada PC.

## Decisões

- **Empurrar, não puxar**: o PC é quem conecta no servidor (funciona atrás de NAT, sem abrir portas nos PCs).
- **HTTPS obrigatório** para o servidor central; o token nunca vai na URL.
- **Privacidade**: o pacote não leva linhas de comando completas nem logs inteiros por padrão; incidentes levam o
  relatório (que pode ter trechos de log). Uma opção `[fleet] send_logs = false` deve cortar `logs` e `cmdline`.
