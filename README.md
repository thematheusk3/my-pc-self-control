# self-monitoring

Painel web para monitorar a máquina local pelo celular ou PC na mesma rede.
Backend em Python (só biblioteca padrão + `psutil`), frontend em HTML/JS puro — sem build, sem npm.

Funciona em PCs comuns (AMD, Intel, NVIDIA) e tem tratamento específico para a **AMD BC-250**.

## Sistemas suportados

O projeto é feito e testado no **[CachyOS](https://cachyos.org) com KDE Plasma 6 (Wayland)**.

| Sistema | Situação |
|---|---|
| **CachyOS** (KDE Plasma) | Suportado: é onde o projeto é desenvolvido e testado. |
| Arch Linux, EndeavourOS, Manjaro e outros baseados em Arch, com KDE Plasma 6 | Deve funcionar por completo (mesmos pacotes, `pacman`/AUR). Não testado. |
| Outras distros com systemd (Debian/Ubuntu, Fedora, openSUSE...) com KDE Plasma 6 | Funciona em grande parte: o instalador usa `apt`/`dnf`/`zypper` e as atualizações pendentes usam `apt`/`dnf`. Não testado. |
| Linux com outro ambiente gráfico (GNOME, XFCE...) | Monitoramento, processos, rede, sensores, ventoinha, áudio, mídia, alertas e desligar funcionam. Ficam de fora o que depende do KDE: resolução da tela (`kscreen-doctor`), captura (Spectacle), abrir apps (`kioclient`), janela em foco no Wayland (KWin; no X11 usa `xprop`) e o login pelo celular (plasmalogin/SDDM). |
| Windows 10/11 | Versão separada: [self-monitoring-win](https://github.com/thematheusk3/self-monitoring-win). O que difere entre as duas está em [PARIDADE.md](PARIDADE.md). |

## O que mostra

- **Visão geral**: CPU (uso total, por thread, clock, temperatura, load), GPU (uso, uso por engine: 3D, vídeo,
  cópia..., temperatura, VRAM/GTT, clocks, potência, fan; NVIDIA lida pelo NVML, sem subprocesso), memória, rede, discos, e os maiores consumidores de CPU/RAM/GPU.
- **Processos**: lista completa com busca, filtro por usuário, ordenação, uso de GPU por processo
  e opção de **finalizar** ou **forçar** (SIGKILL), com confirmação. Finalizar fecha as janelas do processo como
  clicar no X (pelo KWin; no X11 com `wmctrl`), e o app pode perguntar se quer salvar; sem janela, SIGTERM.
  Processos essenciais (KWin, plasmashell, PipeWire, systemd, D-Bus, gerenciador de login...) pedem confirmação extra.
  - **Encerrados**: histórico dos processos que já terminaram (quando, quanto duraram, quem os iniciou).
  - **Suspeitos**: processos vivos ou encerrados com sinais comuns em malware.
  - **Analisar**: o que é o processo (pacote e descrição), executável, SHA-256 + link do VirusTotal,
    se o executável ainda é igual ao que o pacote instalou,
    cadeia de processos pais, serviço systemd, portas e conexões, LD_PRELOAD.
- **Controles**: áudio (mídia tocando com ⏮ ⏯ ⏭ e ±10 s, saída fone/HDMI, volume, por aplicativo, microfone), tela (resolução, brilho,
  apagar/espera) e captura de tela (sempre com aviso no PC).
- **Alertas**: regras configuráveis com aviso no painel, na tela do PC e no celular (ntfy).
- **Jogo/FPS** (na Visão geral): jogo aberto e FPS via MangoHud.
- **Mural**: tela cheia para deixar aberta num monitor, tablet ou celular: relógio, clima, jogo (arte, FPS, CPU, GPU) e música,
  com seis fundos para o Início.
- **Aparência**: três estilos (Placa, Vidro, Osciloscópio) e seis cores de placa, cada uma no tema escuro e no claro, salvos em
  cada aparelho (botão ◐ na barra lateral).
- **Webhook**: outros programas (scripts, backup, Home Assistant...) enviam notificações para o painel.
- **Atualização do painel**: versão instalada × GitHub, changelog e aviso "Nova" na navegação.
- **No PC agora** (na Visão geral): janela em foco e o que está tocando (Spotify, navegador, VLC...), com tocar/pausar/pular.
- **Downloads Steam** (na Visão geral, só quando há algo baixando): progresso, velocidade e tempo restante.
- **Apps**: abre pelo celular, na tela do PC, os aplicativos e jogos instalados.
- **Rede**: conexões por programa (para onde, quanto envia/recebe), conexões encerradas e portas abertas.
- **Sensores**: todos os sensores hwmon (temperaturas, ventoinhas, tensões), agrupados por chip.
- **Ventoinha**: controle da ventoinha por PWM: **automático** (curva temperatura × velocidade editável,
  com perfis prontos), **manual** (velocidade fixa) ou **placa** (devolve o controle ao firmware).
- **Incidentes**: o painel lê o log do sistema o tempo todo e abre um incidente quando a GPU trava/reinicia, um programa ou
  serviço cai, a memória acaba, o disco dá erro ou o PC desliga sem aviso. Cada um traz linha do tempo, métricas em volta,
  processos, mudanças recentes (pacotes, hardware, ações do painel), hipóteses por regras (sem IA) e um texto pronto para colar
  numa IA. Também investiga qualquer momento escolhido.
- **Serviços**: unidades do systemd (sistema e do usuário) com estado, memória, CPU, tarefas e reinícios ao vivo; logs e
  iniciar/parar/reiniciar.
- **Logs**: busca no journal (texto/regex, prioridade, unidade, kernel, boots anteriores), ao vivo e "investigar este momento".
- **Sistema**: informações do host e do próprio monitor; **desligar/reiniciar** a máquina (com confirmação e opção de forçar);
  **quem está acessando** o painel (aparelho, IP, nome na rede, MAC, encerrar sessões, histórico de acessos) e
  **ações feitas pelo painel** (quem fez o quê, quando e se deu certo).

Gráficos com os últimos 5 minutos, atualização em tempo real (SSE, 1 s; o topo mostra o horário da última
atualização e fica vermelho com "Offline · HH:MM:SS há X" se os dados pararem de chegar), tema claro/escuro,
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

**Atualizar**: na pasta do projeto, `./atualizar.sh`. Ele baixa a versão nova do GitHub, mostra o que mudou e reinstala
do mesmo jeito que o serviço já está (root ou seu usuário). `./atualizar.sh --verificar` só diz se há versão nova.
Configuração e dados (`data/`) são preservados. Detalhes, jeito manual e como voltar de versão: [ATUALIZAR.md](ATUALIZAR.md).

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
- Todo login e todo bloqueio por senha errada aparecem como notificação na tela do PC. Sessões abertas e histórico
  de acessos ficam em Sistema > Quem está acessando; tudo o que o painel muda no PC fica em Sistema > Ações feitas pelo painel.
- PID 1, o próprio servidor e threads do kernel são protegidos.
- A finalização confere o horário de criação do processo, para não matar um PID reaproveitado.
- Rodando como usuário comum, só processos desse usuário podem ser finalizados.
- `security.allow_kill = false` em `config.toml` desliga a função.
- Não exponha a porta para a internet: o tráfego é HTTP sem criptografia (feito para a LAN).

## Incidentes, serviços e logs

O painel mantém um banco SQLite em `data/store.db` (configuração `[history]`):

- **Vigia do journal**: acompanha `journalctl -f` (avisos e piores + tudo do kernel), retomando do último cursor depois de
  reinícios; na primeira vez lê as últimas 24 h. As regras ficam em `sm/rules.py` (GPU travou/reiniciou, page fault da GPU,
  core dump, OOM, serviço que falhou, erro de disco, MCE/PCIe, falha do kernel, temperatura crítica). Avisos dos primeiros
  2 minutos do boot não abrem incidente (drivers iniciando costumam reclamar).
- **Investigador** (`sm/incidents.py`): gatilhos a menos de 2 min entram no mesmo incidente (ex.: GPU travou e o jogo caiu 3 s
  depois); o mesmo programa caindo de novo em 15 min vira repetição. O relatório junta a janela de 5 min antes até 1 min
  depois: eventos, ações feitas pelo painel, arquivos de overlay alterados, pacotes atualizados (`/var/log/pacman.log`),
  métricas (1 s ao vivo ou o resumo por minuto), processos no momento, a pilha do core dump e as hipóteses com evidências.
  "Copiar para IA" gera um Markdown com tudo isso.
- **Boot anterior**: na partida, se o boot anterior terminou sem as mensagens de desligamento, abre "Desligamento inesperado".
- **Métricas**: cada série do painel é resumida por minuto (mín/méd/máx) e por hora (`GET /api/history/metrics`).
- **Hardware**: placa, BIOS, CPU (microcode), GPU (VBIOS), memória, discos, kernel e versões (Mesa, firmware, Proton...) são
  gravados quando mudam (`GET /api/history/hardware`), com a lista do que mudou.
- **Permissão**: para ler o journal do sistema, o usuário do serviço precisa estar no grupo `wheel`, `adm` ou `systemd-journal`
  (no Arch/CachyOS o primeiro usuário já está no `wheel`). Sem isso, só aparecem os logs do próprio usuário.
- **Serviços do sistema**: iniciar/parar pedem o polkit; sem uma regra para o usuário do painel o sistema recusa (os do
  usuário funcionam direto). Parar o próprio painel pelo painel é bloqueado.

Vários computadores: veja [FROTA.md](FROTA.md).

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
porta aberta para a rede por binário fora dos pacotes; executável diferente do que o pacote instalou (só na análise
detalhada: compara o SHA-256 com o banco do `pacman`, os `md5sums` do `dpkg` ou o `rpm --dump`). Processos em container (Flatpak, Steam Runtime)
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

## FPS dos jogos

O Linux não tem um contador de FPS global; quem mede é o **MangoHud** dentro do jogo. Ligando "FPS" no
cartão **Jogo** (Visão geral), o painel acrescenta `autostart_log=1` ao `~/.config/MangoHud/MangoHud.conf`
num bloco marcado (removido ao desligar, deixando o arquivo como estava). O MangoHud passa a gravar um CSV
enquanto o jogo roda e o painel lê o final dele a cada segundo: FPS, 1% low (aprox., últimos 60 s), frametime,
uso de CPU/GPU. O jogo precisa rodar com o MangoHud (Steam: `mangohud %command%` nas opções de inicialização).
Desligado, nada é lido nem gravado. Jogos da Steam abertos aparecem mesmo sem MangoHud (sem FPS).
Se o Goverlay regravar o MangoHud.conf, desligue e ligue de novo.

Se um app que não é jogo aparecer como jogo (ex.: Wallpaper Engine pela Steam, ou um programa aberto com o MangoHud), marque-o
em **Mural > Apps que não são jogos**: ele some do jogo atual e do FPS. A lista fica em `data/game.json`.

## Mural

Uma tela cheia para deixar aberta num monitor, tablet ou celular. Abra pela aba *Mural* ou direto em `/#mural:abrir`. Mexa o
mouse ou toque na tela para trocar o modo, entrar em tela cheia ou sair (também com Esc).

| Modo | O que mostra |
|---|---|
| Automático | Jogo aberto; senão, música tocando; senão, Início. |
| Início | Relógio, data, clima e previsão de 3 dias. |
| Jogo | Arte do jogo de fundo, logo e os itens escolhidos: FPS, 1% low, frametime, CPU, GPU, temperaturas, VRAM, RAM, tempo de jogo e player. |
| Música | Capa de fundo, faixa, artista, álbum, progresso, controles, hora e temperatura. |

- **Clima:** escolha a cidade na aba. A previsão vem da [Open-Meteo](https://open-meteo.com/), sem cadastro; só as coordenadas
  da cidade saem do PC, e nada é consultado antes da escolha.
- **Fundos do Início:** Céu da cidade, Serra, Placa de circuito, Aurora, Capas dos jogos (arte da Steam, trocada a cada 2 min)
  e Sua imagem. Escolha em Mural > Fundo do Início ou pelo botão de imagem na barra do Mural.
- **Capas:** cada jogo usa a arte do cache da Steam (`~/.local/share/Steam/appcache/librarycache`, ou a Steam em Flatpak; se
  faltar, a CDN dela) ou uma imagem sua, reduzida no navegador antes do envio.
- **FPS** depende da leitura com o MangoHud (ver [FPS dos jogos](#fps-dos-jogos)).
- **Cores:** vêm da arte do jogo ou da capa da música; no Início, do céu da cidade na hora atual.
- **Eventos:** avisos passam no canto; alertas críticos ocupam a tela inteira e devolvem a cena anterior. Maior prioridade passa
  na frente, e os demais esperam na fila. As opções ficam na aba Mural.
- **Tela apagando:** impedir a tela de apagar exige HTTPS ou localhost. Pelo IP da rede, ajuste o tempo de tela no próprio aparelho.
- **Tela cheia:** o iPhone não oferece tela cheia para páginas. Adicione o painel à Tela de Início para abrir sem as barras do Safari.

## Notificações por webhook

Outros programas avisam pelo painel. Gere o token na aba *Alertas*: ele aparece uma vez, e o painel guarda só o hash. Sem
token, o webhook fica desligado.

```bash
curl -X POST http://SEU-PC:8765/api/webhook/notify \
  -H "Authorization: Bearer SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Backup concluído", "message": "120 GB copiados.", "icon": "check", "level": "info", "source": "Backup"}'
```

| Campo | Tipo | Descrição |
|---|---|---|
| `title` | texto, obrigatório | Até 120 caracteres. |
| `message` | texto | Até 1000 caracteres. |
| `icon` | texto | Um emoji ou nome de ícone (a lista completa está na aba Alertas). Sem ele, usa o sino. |
| `level` | texto | `info` (padrão), `warning` ou `critical`. |
| `source` | texto | Nome do programa que enviou, até 60 caracteres. |
| `url` | texto | Link `http(s)` aberto a partir do alerta. |
| `display` | texto | Como aparece no Mural: `auto` (padrão; `critical` ocupa a tela), `toast` ou `fullscreen`. |
| `duration` | número | Segundos no Mural, de 3 a 300. Padrão: info 5, warning 8, critical 12, tela inteira 20. |
| `silent` | booleano | `true`: só no painel e no Mural, sem tela do PC nem ntfy. |

Respostas: `200` com `{"ok": true, "id": 12}`; `400` campo inválido (com `error`); `401` token errado; `403` webhook desligado;
`429` limite excedido, com `Retry-After`. Limites: 60 notificações por minuto e 5 tokens errados por IP a cada minuto.

O token dá acesso só a esta rota, não ao resto do painel. O painel usa HTTP: na rede local o token viaja sem criptografia, então
não exponha a porta na internet; use uma VPN.

## Áudio e tela

**Áudio** (via `pactl`, PipeWire ou PulseAudio): saída padrão (tocar numa saída leva os apps junto),
volume/mudo de cada saída, volume/mudo/saída **de cada aplicativo**, microfone (mudo, volume, entrada padrão)
e quais apps estão usando o microfone.

**Tela** (KDE): cada monitor com o nome real (lido do EDID, ex.: "LG ULTRAGEAR"), tipo de conexão, escala em % e
qual é o principal. Resolução e taxa de atualização via `kscreen-doctor`. Toda mudança precisa ser confirmada
em 20 s; sem confirmação volta ao modo anterior (proteção contra tela preta). **Brilho/contraste** via DDC/CI
(`ddcutil`, o monitor precisa ter DDC/CI ligado no menu). **Apagar tela** põe brilho e contraste em 0: a tela
fica praticamente preta mas o monitor continua ligado e conectado (ao contrário de desligar no botão, que em
DisplayPort faz o PC "perder" o monitor); **Restaurar** volta aos valores salvos. **Espera** (DPMS) também
existe, com o aviso de que alguns monitores DisplayPort se desconectam em espera profunda.
O `install.sh` adiciona o usuário ao grupo `i2c` para o DDC funcionar mesmo sem sessão aberta.

## Captura de tela

*Controles > Captura de tela*. **Toda captura mostra antes uma notificação crítica na tela do PC**
("📸 Captura de tela pelo painel", com o IP de quem pediu), que aparece mesmo no modo não perturbe.
Se a notificação não puder ser exibida, a captura é recusada. Usa o Spectacle (KDE/Wayland).
As últimas 30 capturas ficam em `data/screenshots/` (só o usuário do serviço lê). Desative com
`security.allow_screenshot = false`.
A lista se atualiza sozinha (inclusive capturas feitas por outro aparelho); o painel só entrega a imagem
depois que o Spectacle termina de gravar o arquivo.

## No PC agora: janela em foco e mídia

**Janela em foco**: no Wayland um programa comum não enxerga as janelas dos outros, então o painel carrega
no KWin um script pequeno (`data/kwin-focus.js`, nome `self-monitoring-focus`) que, a cada troca de janela,
escreve uma linha `SMFOCUS {...}` no log do KWin; o painel acompanha o journal do usuário. Mudanças só de
título (terminal, player) são enviadas no máximo a cada 10 s. Ao parar o serviço o script é descarregado.
Fora do KDE, em sessões X11, usa o `xprop`. Para desligar: `collectors.disabled = ["focus"]`.

**Mídia**: lê os players MPRIS do D-Bus da sessão (`busctl`): título, artista, álbum, capa e posição.
A Visão geral mostra o player principal; *Controles > Áudio* mostra todos, com voltar/avançar 10 s quando o player permite.
Os botões ⏮ ⏯ ⏭ seguem `security.allow_control`. Desligar: `collectors.disabled = ["media"]`.

## Downloads da Steam

A Steam não tem API local de progresso; o painel junta o `logs/content_log.txt` (estado: baixando,
pausado, na fila, instalando...) e os `steamapps/appmanifest_<id>.acf` (bytes baixados/total). A Steam
atualiza esses números em saltos, então a barra anda aos pulos. A velocidade é o tráfego do processo
`steam` no monitor de rede (sem ele, a variação dos bytes baixados). O cartão só aparece com algo na fila.

## Alertas

Aba **Alertas** (e o sino no topo). Cada regra pode ser ligada/desligada e as de limite têm valor ajustável:
temperatura da CPU/GPU (sustentada por 30 s), disco quase cheio, memória, ventoinha parada, processo suspeito
novo, porta nova aberta para a rede, login no painel, IP bloqueado por senha errada, atualizações, FPS baixo.
A mesma condição não repete por 30 min. Canais: o painel (sempre; notificação do navegador só em HTTPS/localhost),
a **tela do PC** e **push no celular via ntfy** (app gratuito; use um tópico com nome longo e aleatório,
pois no ntfy.sh público quem souber o nome lê as mensagens). Há um botão de teste.

## Atualizações

*Sistema > Atualizações pendentes*: `checkupdates` + AUR (`paru`/`yay -Qua`) + Flatpak no Arch,
`apt`/`dnf` em outras distros. Verifica sozinho a cada 6 h. Kernel, drivers DKMS e pacotes de sistema
(mesa, systemd, gerenciador de login...) aparecem destacados. Avisa quando o PC **precisa reiniciar** (kernel novo
instalado e o antigo ainda rodando; `/run/reboot-required` no Debian/Ubuntu; `needs-restarting -r` no Fedora).
**O painel não instala atualizações**.

### Atualização do painel

A aba *Atualização* compara a versão instalada com a do GitHub e mostra o changelog, o link do repositório e o comando para
atualizar (`atualizar.sh`). Quando há versão maior, a navegação mostra **Nova**. O aviso depende do número da versão: um commit
que não aumenta `__version__` não gera aviso.

O painel só lê dois arquivos públicos do repositório (`raw.githubusercontent.com`), com ele aberto e no máximo a cada 6 h. Não
envia dados do PC, não baixa nem instala nada. Para desligar: `check = false` em `[update]` no `config.toml`. A aba continua
mostrando o changelog da versão instalada.

## Desligar / reiniciar

Aba **Sistema > Energia**. Pede confirmação. **Forçar finalização** (marcado por padrão) ignora programas que estão bloqueando o desligamento
(inibidores do systemd: downloads, jogos, diálogos de "salvar?") e outros usuários logados; os programas recebem SIGTERM e,
se não fecharem, SIGKILL. O serviço não roda numa sessão gráfica, então o `install.sh` instala uma regra do polkit
(`/etc/polkit-1/rules.d/50-self-monitoring.rules`) que libera só desligar/reiniciar para o usuário do serviço.
**Timer**: desligar ou reiniciar daqui a X minutos, sempre forçado (nada impede). Avisa na tela do PC
ao agendar, 5 min e 1 min antes; pode ser cancelado. Sobrevive a reinícios do serviço.
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
atualizar.sh              baixa a versão nova (git pull) e reinstala do mesmo jeito (ver ATUALIZAR.md)
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
  desktop.py              sessão gráfica (bloquear/desbloquear), apps, ambiente da sessão, notificação no PC
  audio.py                saídas/entradas/apps de áudio (pactl)
  display.py              resolução (kscreen-doctor) com reversão, brilho DDC/CI, DPMS
  game.py                 jogo atual e FPS (logs do MangoHud)
  alerts.py               regras de alerta, histórico e canais (PC, ntfy)
  screenshot.py           captura de tela com aviso obrigatório no PC
  updates.py              atualizações pendentes
  focus.py                janela em foco (script no KWin + journal; xprop no X11)
  media.py                o que está tocando (MPRIS via busctl) e controles
  steamdl.py              downloads/atualizações da Steam (content_log + appmanifest)
tools/login-pelo-celular.sh  liga/desliga o login automático com a sessão bloqueada
tools/diagnostico.sh        coleta o necessário para entender sessão, áudio, tela e ventoinhas (não muda nada)
tools/simular-suspeito.sh processos inofensivos que imitam malware (para testar a aba Suspeitos)
  auth.py                 senha (hash scrypt), sessões, limite de tentativas e histórico de acessos
  clients.py              quem está acessando: origem do IP, nome (DNS reverso), MAC (ARP), aparelho
  audit.py                registro de ações feitas pelo painel (descritor por rota)
  api/routes.py           endpoints REST
  collectors/             UM ARQUIVO POR COLETOR (carregados automaticamente)
    cpu.py gpu.py memory.py disks.py network.py sensors.py processes.py system.py focus.py media.py steam.py
  hw/
    hwmon.py              leitura genérica de /sys/class/hwmon com filtro de valores absurdos
    gpu.py                backends de GPU: AmdGpu, NvidiaGpu, IntelGpu
    drm_clients.py        uso de GPU por processo (fdinfo)
    quirks.py             correções por placa (BC-250 etc.)
web/                      frontend (sem build; fonte Archivo, licença OFL, e ícones Phosphor, licença MIT, incluídos)
  index.html, login.html, css/app.css, css/styles.css (estilos), css/mural.css
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
| POST | `/api/processes/<pid>/signal` | `{"signal": "TERM"\|"KILL"\|"STOP"\|"CONT", "create_time": ..., "confirm_critical": true}`; TERM fecha as janelas (`closed_windows`) ou manda SIGTERM; processo essencial sem `confirm_critical` → 409 `{"critical": true}` |
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
| GET/POST | `/api/game` | jogo atual e FPS; `{"fps_enabled": true}` liga a leitura |
| POST | `/api/game/not-game` | `{"key": "exe:wallpaper", "name": "Wallpaper", "ignored": true}`; `"ignored": false` desfaz |
| GET/POST | `/api/mural` | preferências do Mural; `GET /api/mural/cities?q=`, `/api/mural/games`, `GET/POST /api/mural/art/<key>` (`POST .../reset`) |
| GET/POST | `/api/mural/background` | imagem de fundo do Início; `POST` com `{"image": "data:image/jpeg;base64,..."}`; `POST /api/mural/background/reset` remove |
| POST | `/api/webhook/notify` | notificação de outro programa (token, ver "Notificações por webhook"); `GET /api/webhook`, `POST /api/webhook/token`, `/revoke`, `/test` |
| GET | `/api/panel-update` | versão instalada × GitHub e changelog; `POST /api/panel-update/check` verifica agora |
| POST | `/api/media` | `{"player": "spotify", "op": "play_pause"\|"next"\|"previous"\|"seek", "offset": 10}` (offset em segundos, só no seek) |
| GET/POST | `/api/audio` | estado do áudio; `{"op": "set_default"\|"volume"\|"mute"\|"move", ...}` |
| GET | `/api/display` | monitores (`label` do EDID, `connector`, `primary`, `scale`) e modos |
| POST | `/api/display/mode` | `{"output","mode"}` aplica (reverte em 20 s); `{"op":"confirm"\|"revert"}` |
| GET/POST | `/api/display/ddc` | brilho/contraste; `{"bus", "brightness"/"contrast"}` ou `{"bus","op":"dim"\|"restore"}` |
| POST | `/api/display/dpms` | `{"on": false}` põe o monitor em espera |
| GET/POST | `/api/power/timer` | `{"action":"poweroff","minutes":60}` agenda (forçado); `{"cancel":true}` |
| GET | `/api/updates` | atualizações pendentes e `reboot` (precisa reiniciar?); `POST /api/updates/refresh` verifica agora |
| GET | `/api/alerts` | histórico; `POST /api/alerts/read`, `/api/alerts/clear`, `/api/alerts/test` |
| GET/POST | `/api/alerts/config` | regras e canais |
| POST | `/api/screenshot` | captura (avisa na tela do PC); `GET /api/screenshots`, `/api/screenshots/<nome>` |
| GET | `/api/fan` | estado da ventoinha (modo, RPM, %, temperatura, curva) |
| POST | `/api/fan` | `{"mode": "auto"\|"manual"\|"firmware", "manual_pct": 40, "curve": [[50,25],...], "preset": "silencioso"}` |

Autenticação: cookie de sessão obtido em `POST /login` (campo `password`). Troca de senha:
`POST /api/auth/password` com `{"current": "...", "new": "..."}`. Sair: `POST /logout`.
