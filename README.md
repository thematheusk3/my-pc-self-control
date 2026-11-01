# self-monitoring

Painel web para monitorar a máquina local pelo celular ou PC na mesma rede.
Backend em Python (só biblioteca padrão + `psutil`), frontend em HTML/JS puro — sem build, sem npm.

Funciona em PCs comuns (AMD, Intel, NVIDIA) e tem tratamento específico para a **AMD BC-250**.

## O que mostra

- **Visão geral**: CPU (uso total, por thread, clock, temperatura, load), GPU (uso, temperatura,
  VRAM/GTT, clocks, potência, fan), memória, rede, discos, e os maiores consumidores de CPU/RAM/GPU.
- **Processos**: lista completa com busca, filtro por usuário, ordenação, uso de GPU por processo
  e opção de **finalizar** (SIGTERM) ou **forçar** (SIGKILL), com confirmação.
  - **Encerrados**: histórico dos processos que já terminaram (quando, quanto duraram, quem os iniciou).
  - **Suspeitos**: processos vivos ou encerrados com sinais comuns em malware.
  - **Analisar**: o que é o processo (pacote e descrição), executável, SHA-256 + link do VirusTotal,
    cadeia de processos pais, serviço systemd, portas e conexões, LD_PRELOAD.
- **Apps**: abre pelo celular, na tela do PC, os aplicativos e jogos instalados.
- **Rede**: conexões por programa (para onde, quanto envia/recebe), conexões encerradas e portas abertas.
- **Sensores**: todos os sensores hwmon (temperaturas, ventoinhas, tensões), agrupados por chip.
- **Ventoinha**: controle da ventoinha por PWM: **automático** (curva temperatura × velocidade editável,
  com perfis prontos), **manual** (velocidade fixa) ou **placa** (devolve o controle ao firmware).
- **Sistema**: informações do host e do próprio monitor; **desligar/reiniciar** a máquina (com confirmação e opção de forçar).

Gráficos com os últimos 5 minutos, atualização em tempo real (SSE, 1 s), tema claro/escuro,
layout próprio para celular (abas embaixo) e PC (grade de 3 colunas). Pode ser adicionado à tela
inicial do celular ("Adicionar à tela inicial").

## Instalar

Requisitos: Linux com systemd, Python ≥ 3.11 e `psutil`. O instalador instala as dependências
(pacman, apt, dnf ou zypper) se faltarem.

```bash
git clone https://github.com/thematheusk3/my-pc-self-control ~/Projetos/self-monitoring
cd ~/Projetos/self-monitoring
sudo ./install.sh              # roda como seu usuário (recomendado)
sudo ./install.sh --root       # roda como root: vê/finaliza qualquer processo e vê o dono de todas as conexões
```

O código é copiado para `/opt/applications/self-monitoring`, o serviço `self-monitoring` é criado e,
na primeira vez, o instalador pede a senha de acesso (também dá para criar abrindo `http://localhost:8765`
na própria máquina). Depois acesse de qualquer aparelho da rede (ex.: `http://192.168.1.50:8765`).
O login dura 30 dias; a senha pode ser trocada na aba **Sistema**. Esqueceu? `python3 server.py --set-password` na máquina.

**Configuração**: `/opt/applications/self-monitoring/config.toml`, criado a partir de `config.example.toml`
na primeira instalação e **nunca sobrescrito** por atualizações. Depois de editar: `sudo systemctl restart self-monitoring`.

**Atualizar**: `git pull && sudo ./install.sh`. Configuração e dados (`data/`) são preservados.

**Desinstalar**: `sudo ./install.sh --uninstall` (mantém config e dados) ou `--purge` (apaga tudo).

Comandos úteis: `systemctl status self-monitoring`, `journalctl -u self-monitoring -f`.

## Desenvolver

```bash
python3 server.py --port 8799            # roda direto do repositório, sem instalar (dados em ./data)
python3 -m unittest discover -s tests -t .   # testes (só biblioteca padrão + psutil)
```

Rodando da pasta do repositório, o painel usa `./config.toml` se existir (senão, os padrões do código)
e `./data/`, separados da instalação em `/opt`, então dá para testar sem mexer no serviço. Fluxo sugerido:
alterar → testar na porta 8799 → `python3 -m unittest …` → commit → `sudo ./install.sh` para atualizar o serviço.
O GitHub Actions (`.github/workflows/ci.yml`) roda os testes, compila o Python e confere a sintaxe do JavaScript
e dos scripts a cada push. Veja **Estrutura e como estender** abaixo e registre mudanças no `CHANGELOG.md`.

## Segurança

- Acesso por senha. Só o hash (scrypt com salt) é salvo, em `data/auth.json` (permissão 600).
- A primeira senha só pode ser criada na própria máquina (localhost ou terminal), não pela rede.
- Depois de 5 senhas erradas em 5 min, o IP fica bloqueado por 2 min.
- Trocar a senha encerra as sessões dos outros aparelhos. Esqueceu a senha? Rode `--set-password` na máquina.
- PID 1, o próprio servidor e threads do kernel são protegidos.
- A finalização confere o horário de criação do processo, para não matar um PID reaproveitado.
- Rodando como usuário comum, só processos desse usuário podem ser finalizados.
- `security.allow_kill = false` em `config.toml` desliga a função.
- Não exponha a porta para a internet: o tráfego é HTTP sem criptografia (feito para a LAN).

## Histórico e análise de processos

O Linux não guarda histórico de processos encerrados. O painel varre `/proc` a cada
`sampling.track_interval` (0,5 s, ~3 ms por varredura) e registra quem aparece e quem some.
Processos mais curtos que o intervalo podem escapar; para pegar 100% seria preciso root + audit/eBPF.
O histórico (últimos `exited_max`) fica em `data/exited.json` e sobrevive a reinícios.
Para executáveis em pasta temporária, ocultos ou apagados, o SHA-256 é calculado assim que o
processo aparece (malware costuma se apagar logo depois de rodar).

Sinais verificados (`sm/procinspect.py`), em três níveis: **alto**, **médio** e **info**:
executável apagado do disco ou rodando da memória (memfd); executável em `/tmp`, `/var/tmp` ou `/dev/shm`,
ou com nome oculto; nome imitando thread do kernel (`[kworker/...]`); comando de shell reverso,
`curl | sh`, `base64 -d | sh`, minerador (`stratum+tcp`, xmrig); `LD_PRELOAD` desconhecido;
arquivo em pasta do sistema que não é de nenhum pacote; root rodando binário fora dos pacotes;
porta aberta para a rede por binário fora dos pacotes. Processos em container (Flatpak, Steam Runtime)
são reconhecidos e não disparam as regras de pacote. **Não é um antivírus**: programas legítimos
podem disparar algum sinal. Dá para marcar um executável como confiável (fica em `data/trusted.json`).

Rodando como usuário comum, dos processos de outros usuários (root etc.) o painel só vê nome, comando
e pai: não vê o executável real, as conexões nem o ambiente. Para análise completa, use `sudo ./install.sh --root`.

Links diretos: `#processes:exited`, `#processes:susp`.

### Simular um processo suspeito

```bash
./tools/simular-suspeito.sh        # 120 s; ./tools/simular-suspeito.sh 300 para 5 min
```

Cria 4 processos **inofensivos** (cópias do `sleep`) que disparam os sinais de alto risco: executável oculto em `/tmp`,
programa que apaga o próprio arquivo, nome imitando `[kworker]` e linha de comando de minerador.
Tudo é encerrado e apagado no fim (ou com Ctrl+C).

## Monitor de rede

Lê as conexões com `ss` a cada `sampling.net_interval` (2 s, ~45 ms por leitura). Mostra para cada conexão o programa,
o IP remoto e o nome reverso (PTR), se é internet/rede local/esta máquina, porta e serviço, direção (entrada/saída),
bytes enviados/recebidos e velocidade atual. Conexões encerradas ficam em `data/connections.json`.
Portas em que um programa conhecido de malware costuma falar (3333, 4444, 14444...) ganham um aviso.

Limites: **o conteúdo não é visível** (quase tudo é HTTPS); para pacotes use Wireshark (root), para ver dentro do HTTPS
do próprio navegador, um proxy como o mitmproxy. UDP (inclui QUIC/HTTP3) aparece sem contagem de bytes. Sem root, o dono
dos sockets de outros usuários aparece como "outro usuário", e o domínio exato de cada conexão (consultas DNS) não é visível,
só o DNS reverso do IP.

## Abrir aplicativos pelo celular

A aba **Apps** lista os `.desktop` instalados (sistema, `~/.local/share/applications`, Flatpak, jogos da Steam),
com ícones do tema atual (e do cache da Steam para jogos sem ícone). Tocar abre o app **na sessão gráfica do
usuário do serviço** via `systemd-run --user` + `kioclient exec`: o app herda o ambiente da sessão (Wayland/X11)
e roda numa unidade própria, independente do painel. Precisa de alguém logado; se a tela estiver bloqueada,
o app abre por trás do bloqueio. Desative com `security.allow_launch = false`.

## Entrar no PC pelo celular

O gerenciador de login (plasmalogin/SDDM) não tem API para login remoto. A solução é:

1. **Login automático com a sessão já bloqueada**: ao ligar, o PC entra no seu usuário e mostra direto a tela
   de bloqueio (`LockOnStart` do KDE). Ative/desative com:
   ```bash
   sudo ./tools/login-pelo-celular.sh ativar     # ou: desativar | status
   ```
   O script ativa primeiro o bloqueio ao iniciar e só então o login automático, e faz backup de `/etc/plasmalogin.conf`.
2. **Desbloquear pelo painel**: *Sistema > Sessão > Desbloquear* pede a **senha do painel** e manda o
   `systemd-logind` desbloquear a sessão (`loginctl unlock-session`). Também dá para **bloquear** remotamente.

Por que a senha do painel e não a do Linux: o painel é HTTP sem criptografia na rede local, e a senha do Linux
costuma ser também a do `sudo`. O desbloqueio tem o mesmo limite de tentativas do login.

Efeito colateral: sem digitar a senha no login, o **KWallet** não abre sozinho; programas que guardam senhas
nele vão pedir a senha da carteira na primeira vez. Desative com `security.allow_unlock = false`.

## Desligar / reiniciar

Aba **Sistema > Energia**. Pede confirmação. **Forçar finalização** ignora programas que estão bloqueando o desligamento
(inibidores do systemd: downloads, jogos, diálogos de "salvar?") e outros usuários logados; os programas recebem SIGTERM e,
se não fecharem, SIGKILL. O serviço não roda numa sessão gráfica, então o `install.sh` instala uma regra do polkit
(`/etc/polkit-1/rules.d/50-self-monitoring.rules`) que libera só desligar/reiniciar para o usuário do serviço.
Desative com `security.allow_power = false`.

## Controle da ventoinha

Por padrão (`chip = "auto"`, `channel = 0`) o painel usa o primeiro canal PWM da placa-mãe cuja ventoinha
está girando; ventoinhas de placa de vídeo são ignoradas. Para escolher outro, defina `chip` e `channel` em `[fan]`.
Na BC-250 a detecção acha a única controlável, a do conector **Pump Fan** (`nct6686`, `pwm2`); a da fonte
não tem controle. Enquanto você não escolher um modo na aba, o painel não mexe na ventoinha.

- **Automático**: segue a curva usando a temperatura `Tctl` (média de 5 s). Sobe na hora quando esquenta;
  só desce quando a temperatura cai `hysteresis` °C abaixo do ponto, no máximo `step_down` %/s.
- **Manual**: velocidade fixa (mínimo `min_pct`).
- **Placa**: grava `pwm2_enable = 2` e o firmware volta a decidir.
- **Segurança**: em automático e manual, acima de `emergency_temp` (92 °C) ou sem leitura de temperatura
  por 5 s a ventoinha vai para 100%. Ao parar o serviço ela volta para a placa (`on_exit`), inclusive se o
  processo travar (o `ExecStopPost` do systemd faz isso).
- **Permissão**: o serviço roda como usuário comum; o `install.sh` instala `/usr/local/lib/self-monitoring/fan-helper`
  (dono root), que o systemd executa como root antes de iniciar para dar ao usuário posse dos arquivos `pwm*`.
- **Não use junto com o CoolerControl/fancontrol** controlando a mesma ventoinha: os dois brigam.
  O painel avisa quando detecta o `coolercontrold` rodando.

O modo escolhido e a curva ficam em `data/fan.json` e são reaplicados ao reiniciar.

## Notas da BC-250

A BC-250 é uma APU (Cyan Skillfish, CPU Zen 2 + GPU RDNA no mesmo die) com 16 GB GDDR6 compartilhados.
O driver `amdgpu` reporta valores inválidos nela, que o painel filtra (`sm/hw/quirks.py`):

| Leitura | O que o driver dá | O que o painel faz |
|---|---|---|
| Temperatura da GPU (`edge`) | 0 °C | Usa `Tctl` da CPU (mesmo die) |
| Potência (`PPT`) | ~1.700.000 W | Oculta |
| Clock (`sclk`) | Valores aleatórios até 4+ GHz | Oculta |
| Tensões (`vddgfx`, `vddnb`) | 8–36 V | Oculta |
| `gpu_busy_percent` | Instantâneo, oscila 0↔30 | Média de 5 leituras |

A memória aparece como "VRAM (reservada)" (carve-out de 512 MB) + "GTT" (memória compartilhada que a GPU usa de fato).

## Estrutura e como estender

```
server.py                 ponto de entrada
install.sh                instalar / atualizar / desinstalar
config.example.toml       modelo de configuração (vira config.toml na instalação; config.toml não vai pro git)
CHANGELOG.md              histórico de versões
tests/                    testes (python3 -m unittest discover -s tests -t .)
.github/workflows/ci.yml  testes automáticos no GitHub
sm/
  config.py               leitura da configuração e token
  sampler.py              roda os coletores periodicamente; Context compartilhado
  history.py              séries temporais em memória (gráficos)
  server.py               HTTP: estáticos, API JSON, SSE (/api/stream), autenticação
  actions.py              ações que alteram o sistema (finalizar processo)
  fan.py                  controle da ventoinha (thread própria, curva, segurança)
  proctrack.py            histórico de processos encerrados (varredura de /proc)
  procinspect.py          análise de processo, pacote dono, hash, heurísticas de suspeita
  netmon.py               monitor de conexões de rede (ss), DNS reverso, histórico
  power.py                desligar/reiniciar via systemd-logind
  desktop.py              sessão gráfica (bloquear/desbloquear) e aplicativos (listar, ícones, abrir)
tools/login-pelo-celular.sh  liga/desliga o login automático com a sessão bloqueada
tools/simular-suspeito.sh processos inofensivos que imitam malware (para testar a aba Suspeitos)
  auth.py                 senha (hash scrypt), sessões e limite de tentativas
  api/routes.py           endpoints REST
  collectors/             UM ARQUIVO POR COLETOR (carregados automaticamente)
    cpu.py gpu.py memory.py disks.py network.py sensors.py processes.py system.py
  hw/
    hwmon.py              leitura genérica de /sys/class/hwmon com filtro de valores absurdos
    gpu.py                backends de GPU: AmdGpu, NvidiaGpu, IntelGpu
    drm_clients.py        uso de GPU por processo (fdinfo)
    quirks.py             correções por placa (BC-250 etc.)
web/
  index.html, login.html, css/app.css
  js/app.js               conexão SSE, histórico, abas
  js/ui.js, spark.js      utilidades e gráfico
  js/modules/             UMA PÁGINA/ABA POR ARQUIVO (registradas em modules/index.js)
```

**Novo coletor** (ex.: status do Docker): crie `sm/collectors/docker.py`:

```python
from . import Collector, register

@register
class Docker(Collector):
    name = "docker"
    interval = 5            # segundos (None = padrão)
    def collect(self):
        self.record("docker.running", 3)   # opcional: vira série para gráfico
        return {"running": 3}
```

Os dados aparecem em `snapshot.data.docker` no frontend e `app.history["docker.running"]` nos gráficos.

**Novo endpoint**: `@route("POST", "/api/algo/<id>")` em `sm/api/routes.py`.

**Nova aba**: crie `web/js/modules/minha.js` exportando `{ id, title, icon, mount(el, app), show(), hide() }`
e adicione em `web/js/modules/index.js`. Use `app.onSnapshot(fn)` para receber dados ao vivo e
`app.api(url, {method, body})` para chamar a API.

**Nova placa com quirk**: adicione uma função em `sm/hw/quirks.py`.

## API

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/info` | informações estáticas, coletores ativos |
| GET | `/api/snapshot` | última amostra de todos os coletores |
| GET | `/api/stream` | Server-Sent Events com um snapshot por segundo |
| GET | `/api/history?keys=cpu.total,gpu.0.busy` | séries temporais |
| GET | `/api/processes` | lista completa de processos |
| POST | `/api/processes/<pid>/signal` | `{"signal": "TERM"\|"KILL"\|"STOP"\|"CONT", "create_time": ...}` |
| GET | `/api/processes/exited` | histórico de encerrados |
| POST | `/api/processes/exited/clear` | limpa o histórico |
| GET | `/api/processes/suspicious` | processos com sinais de atenção (`?info=1` inclui os informativos) |
| GET | `/api/processes/<pid>/inspect` | análise de processo vivo (`?create_time=` evita PID reaproveitado) |
| GET | `/api/processes/record/<id>/inspect` | análise de processo do histórico |
| POST | `/api/processes/trust` | `{"exe": "/caminho", "trusted": true}` |
| GET | `/api/net` | conexões ao vivo, por programa e portas abertas |
| GET | `/api/net/closed` | conexões encerradas |
| POST | `/api/net/closed/clear` | limpa o histórico de conexões |
| GET | `/api/power` | se o serviço tem permissão para desligar/reiniciar |
| POST | `/api/power` | `{"action": "poweroff"\|"reboot", "force": false, "confirm": true}` |
| GET | `/api/session` | sessão gráfica (bloqueada?) e status do login automático |
| POST | `/api/session/lock` | bloqueia a tela do PC |
| POST | `/api/session/unlock` | `{"password": "<senha do painel>"}` desbloqueia a tela do PC |
| GET | `/api/apps` | aplicativos instalados |
| GET | `/api/apps/<id>/icon` | ícone do aplicativo |
| POST | `/api/apps/<id>/launch` | abre o aplicativo na sessão do usuário |
| GET | `/api/fan` | estado da ventoinha (modo, RPM, %, temperatura, curva) |
| POST | `/api/fan` | `{"mode": "auto"\|"manual"\|"firmware", "manual_pct": 40, "curve": [[50,25],...], "preset": "silencioso"}` |

Autenticação: cookie de sessão obtido em `POST /login` (campo `password`). Troca de senha:
`POST /api/auth/password` com `{"current": "...", "new": "..."}`. Sair: `POST /logout`.
