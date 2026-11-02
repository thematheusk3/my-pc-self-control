# Paridade Linux × Windows

Este arquivo existe para as duas versões do self-monitoring ficarem o mais iguais possível:
[Linux](https://github.com/thematheusk3/my-pc-self-control) (CachyOS/Arch com KDE) e
[Windows](https://github.com/thematheusk3/self-monitoring-win). O frontend e a API são os mesmos; o que muda é o
backend de cada sistema.

**O arquivo é o mesmo nos dois repositórios.** Ao mudar em um, copie para o outro.

**Regra:** ao implementar algo novo em uma versão, registre aqui se dá para levar para a outra (e como), ou se é
impossível (e por quê). Quando for portado, mova a linha para "Já equalizado" e anote a versão.

Situação em: versão Linux 0.4.5, versão Windows 0.5.3-win.

## 1. Só no Windows: dá para levar ao Linux

| Recurso | Como foi feito no Windows | Como fazer no Linux | Esforço |
|---|---|---|---|
| Guia de atualização (`ATUALIZAR.md`: atualizar, conferir a versão, voltar à anterior) | Documento | `git pull && sudo ./install.sh`; conferir em Sistema ou `python3 -c "import sm; print(sm.__version__)"`; voltar: `git checkout <tag>` + `install.sh` | Baixo |
| Nome real do monitor e tipo de conexão (ex.: "LG ULTRAGEAR · DisplayPort") e chip "principal" | DisplayConfig (Win32) | Ler o EDID em `/sys/class/drm/card*-*/edid` (nome no descritor 0xFC); o `kscreen-doctor -j` já informa a saída primária | Baixo |
| Escala da tela em % no cartão do monitor | `GetDpiForMonitor` | O `kscreen-doctor -j` já devolve `scale`; só formatar como % no frontend | Baixo |
| Uso da GPU por tipo de engine (3D, cópia, vídeo) | Contadores "GPU Engine" | O fdinfo do DRM já separa engines (`gfx`, `compute`, `dec`, `enc`); expor em `gpu.engines` | Baixo |
| NVIDIA sem subprocesso (mais leve que chamar `nvidia-smi` a cada segundo) | NVML via ctypes (`nvml.dll`) | O mesmo código com `libnvidia-ml.so.1` | Baixo |
| Finalizar "educado" (como clicar no X; o app pode perguntar se quer salvar) antes do SIGTERM | `WM_CLOSE` nas janelas do processo | Script do KWin (`window.closeWindow()`) para janelas do PID; sem janela, SIGTERM | Médio |
| Processos críticos protegidos (finalizá-los derruba o sistema) | Lista: csrss, lsass, winlogon, dwm... | Proteger `systemd`, `kwin_wayland`, `plasmashell`, `sddm`/`plasmalogin`, `pipewire`, `Xwayland`, `dbus-daemon`, com aviso em vez de bloqueio total | Baixo |
| Detecção de PID reaproveitado no histórico de processos | Compara o horário de início | Já usa `pid-starttime` como id; falta só verificar PIDs conhecidos (raro no Linux, `pid_max` alto) | Baixo |
| Atualizações que pedem reinício | Campo `RebootRequired` do Windows Update | Comparar `uname -r` com o kernel instalado em `/usr/lib/modules`; `needs-restarting` no Fedora; `/var/run/reboot-required` no Debian/Ubuntu | Baixo |
| Aviso de rede "Pública" bloqueando o acesso pelo celular | `Get-NetConnectionProfile` no instalador | O `install.sh` já libera a porta no `ufw`/`firewalld`; falta avisar quando há outro firewall (`nftables`/`iptables` com política DROP) | Baixo |
| Captura de tela sem programa externo | GDI (BitBlt) + encoder PNG próprio | No Wayland só pelo portal (`org.freedesktop.portal.Screenshot`) ou Spectacle; manter o Spectacle | — (não vale) |

## 2. Só no Linux: dá para levar ao Windows

| Recurso | Como é no Linux | Como fazer no Windows | Esforço |
|---|---|---|---|
| **Instalador não instala na conta errada** (0.4.4) | Rodado de um terminal já como root sem `--root`, usa o dono da sessão gráfica; opção `--user NOME` | No `install.ps1`: comparar a conta que vai rodar a tarefa agendada com o dono da sessão do console (`(Get-CimInstance Win32_ComputerSystem).UserName`). Se o instalador foi elevado com **outra** conta de administrador, a tarefa no logon ficaria na conta errada: avisar e oferecer `-User NOME` | Baixo |
| **Aviso de sessão aberta em outra conta** (Sistema > Sessão, 0.4.4) | `GET /api/session` devolve `service_user` e `other_users`; o cartão explica como reinstalar | Listar sessões com `WTSEnumerateSessions` (ou `quser`) e devolver os mesmos campos; o frontend já está pronto | Baixo |
| **Script de diagnóstico** (0.4.4) | `tools/diagnostico.sh`: serviço, sessões, ambiente gráfico, áudio, tela, placa-mãe, sensores; não muda nada | `tools/diagnostico.ps1`: tarefa agendada (usuário, elevada?, última execução), sessão do console, LibreHardwareMonitor rodando, PresentMon, dispositivos Core Audio, monitores, versão | Baixo |
| **Dica quando nenhuma ventoinha aparece** (0.4.4) | `sensors.fan_hint`: fabricante da placa (DMI) → driver que falta (MSI → NCT6687D, Gigabyte → it87...) | Mesmo campo `sensors.fan_hint`: se o LibreHardwareMonitor não responde ou não tem ventoinhas, dizer como instalar/iniciar (`install.ps1 -LHM`) e lembrar do driver PawnIO | Baixo |
| Controle da ventoinha (curva automática, manual, devolver à placa, emergência) | PWM em `/sys/class/hwmon` | Carregar a `LibreHardwareMonitorLib.dll` (via `pythonnet`) e usar `Sensor.Control.SetSoftware(%)` / `SetDefault()`; a lógica de curva/histerese/emergência de `fan.py` pode ser reaproveitada inteira. Exige o driver do LHM (PawnIO nas versões novas). Alternativa: integrar com o FanControl (configuração em JSON) | Alto |
| Entrar pelo celular depois de reiniciar (painel no ar sem ninguém digitar a senha) | Login automático + sessão iniciando bloqueada (`LockOnStart`) | Login automático (Autologon da Sysinternals, guarda a senha cifrada pelo LSA) + tarefa no logon que roda `LockWorkStation` antes de tudo. O painel sobe com a tela já bloqueada. **Desbloquear continua impossível** (ver seção 3) | Médio |
| FPS com uso de CPU/GPU do jogo | Colunas `cpu_load`/`gpu_load` do MangoHud | O PresentMon 2.x tem `GPUBusy`, `CPUBusy`, `DisplayLatency`; ler essas colunas no `game.py` | Baixo |
| Ícone e categoria do app vindos do próprio app (`.desktop`: Jogos, Internet, Escritório...) | `Categories=` dos `.desktop` | O menu Iniciar não tem categorias; dá para usar a pasta do atalho no menu Iniciar (`Get-StartApps` não informa: ler os `.lnk` em `ProgramData\...\Start Menu\Programs`) | Médio |
| Volume acima de 100% | PipeWire amplifica até 150% | Só com APO (Equalizer APO) instalado no dispositivo; não vale a complexidade | — (não vale) |

## 3. Impossível ou não se aplica (e por quê)

| Recurso | Onde existe | Por que não dá no outro |
|---|---|---|
| Desbloquear a tela pelo celular | Linux (`loginctl unlock-session`) | No Windows a tela de bloqueio (LogonUI) só aceita credencial digitada, PIN ou Windows Hello. A única saída seria um *Credential Provider* próprio (DLL no LogonUI), que é complexo e abriria um buraco de segurança. |
| Serviço como root com os comandos da sessão rodando como o dono da área de trabalho (`setpriv`, 0.4.4) | Linux | Não se aplica: no Windows a tarefa já roda elevada **na conta do usuário**, então áudio, tela e notificações já são os da sessão dele. O equivalente útil é a checagem de conta errada (seção 2). |
| FPS sem configurar o jogo | Windows (PresentMon lê os eventos de apresentação do sistema) | O Linux não tem um ponto central equivalente; quem mede é a camada do jogo (MangoHud, gamescope). O mais próximo: detectar se o jogo roda no gamescope e ler as estatísticas dele. |
| Temperatura da CPU sem programa extra | Linux (hwmon) | O Windows exige driver em modo kernel para ler os sensores; o LibreHardwareMonitor é esse driver. |
| Assinatura digital do executável | Windows (Authenticode) | Executáveis do Linux não são assinados. O equivalente é "qual pacote instalou" (já existe) e verificar se o arquivo foi alterado depois de instalado: `pacman -Qkk <pacote>` / `rpm -V` / `debsums`. **Isso dá para levar ao Linux** como sinal "binário alterado em relação ao pacote". |
| Bytes por conexão desde o início | Linux (`ss` mostra o total da conexão) | O Windows só conta depois que alguém liga a coleta naquela conexão; o painel liga ao ver a conexão, então conexões antigas começam do zero. |
| Detalhes do processo de outros usuários sem root | — | Nos dois sistemas exige privilégio (root / administrador). |

## 4. Ideias que nenhum dos dois tem (fazer nos dois)

| Ideia | Windows | Linux |
|---|---|---|
| **Programas que iniciam sozinhos** (onde malware se instala para sobreviver ao reinício): listar e avisar quando surge um novo | Chaves `Run`/`RunOnce` (HKCU/HKLM), pasta Inicializar, tarefas agendadas, serviços novos | `~/.config/autostart`, `/etc/xdg/autostart`, unidades `systemd --user` e de sistema, timers, `crontab` |
| **Saúde dos discos** (SMART: desgaste do SSD, setores ruins, temperatura) | `Get-PhysicalDisk` / `MSFT_StorageReliabilityCounter` ou LibreHardwareMonitor | `smartctl -j` (smartmontools) |
| **Mensagem na tela do PC** enviada do celular ("já vou desligar", "janta!") | Notificação (já existe `notify_pc`) | `notify-send` (já existe) |
| **Prioridade do processo** (baixar a prioridade de algo que está travando o PC) | `psutil.Process.nice()` com classes de prioridade | `renice` / `psutil` |
| **Ligar o PC pelo celular (Wake-on-LAN)** | O painel não pode se ligar sozinho, mas pode mostrar o MAC e checar se o WoL está habilitado na placa de rede; o "acordar" precisa de outro aparelho ligado na rede (roteador, Raspberry Pi) | Idem (`ethtool <iface>` mostra `Wake-on: g`) |
| **Histórico de temperatura/uso maior que 5 min** (ex.: 24 h em resolução menor) | Comum aos dois (`history.py`) | Comum aos dois |

## Já equalizado

Funciona igual nas duas versões (mesma API e mesma tela): visão geral, processos (lista, encerrados, suspeitos,
análise, confiáveis), rede (por programa, conexões, encerradas, portas), apps pelo celular, áudio (saída padrão,
volume por dispositivo e **por app, inclusive a saída de cada app**, microfone), mídia com ±10 s, tela (resolução com
reversão, brilho DDC/CI, apagar, espera), captura de tela com aviso, janela em foco, downloads da Steam, jogo atual,
alertas (PC e ntfy), atualizações pendentes, desligar/reiniciar com timer, bloquear a tela, senha e sessões.

Portados depois (mesma API e mesma tela nas duas versões):

| Recurso | De → para | Versões | Observação |
|---|---|---|---|
| Quem está acessando (sessões, IP, origem, nome, MAC, encerrar, histórico de acessos) | Windows → Linux | 0.5.2-win → 0.4.5 | Só `arp_table()` muda: `GetIpNetTable` no Windows, `/proc/net/arp` no Linux |
| Notificação no PC a cada login e a cada bloqueio por senha errada | Windows → Linux | 0.5.2-win → 0.4.5 | `notify_pc` de cada sistema |
| Registro de ações feitas pelo painel | Windows → Linux | 0.5.3-win → 0.4.5 | No Linux há também a categoria "Ventoinha" (`fan_update`) e "Desbloquear a tela" é real |
