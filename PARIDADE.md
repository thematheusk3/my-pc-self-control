# Paridade Linux × Windows

Este arquivo existe para as duas versões do self-monitoring ficarem o mais iguais possível:
[Linux](https://github.com/thematheusk3/my-pc-self-control) (CachyOS/Arch com KDE) e
[Windows](https://github.com/thematheusk3/self-monitoring-win). O frontend e a API são os mesmos; o que muda é o
backend de cada sistema.

**O arquivo é o mesmo nos dois repositórios.** Ao mudar em um, copie para o outro.

**Regra:** ao implementar algo novo em uma versão, registre aqui se dá para levar para a outra (e como), ou se é
impossível (e por quê). Quando for portado, mova a linha para "Já equalizado" e anote a versão.

Situação em: versão Linux 0.4.6, versão Windows 0.9.0-win.

## 1. Só no Windows: dá para levar ao Linux

| Recurso | Como foi feito no Windows | Como fazer no Linux | Esforço |
|---|---|---|---|
| **Interface repaginada** (0.6.0-win, **aguardando aprovação**: portar só se agradar no Windows) | Tema "placa do PC", faixa de estado com LEDs por thread, barra lateral, "Mais" no celular, ícones SVG, fonte própria. Mesmas funções e mesma API | Ver "Levar a interface repaginada ao Linux", logo abaixo desta tabela | Baixo a médio |
| **Servidor aceita rajadas de pedidos** (0.6.0-win) | `request_queue_size = 128` na classe `Server` do `sm/server.py`; com o padrão (5), o Windows recusava conexões ao carregar a página | A mesma linha no `sm/server.py`. No Linux o sintoma é mais raro, mas a linha é inofensiva | Baixo |
| **Aba Atualização e aviso de versão nova** (0.7.0-win) | `sm/selfupdate.py` compara `__version__` com o `sm/__init__.py` do GitHub e traz o `CHANGELOG.md`; rotas `GET /api/panel-update` e `POST /api/panel-update/check`; `[update]` no config; `web/js/modules/update.js` marca "Nova" na navegação | Ver "Levar a aba Atualização ao Linux", abaixo desta tabela | Baixo |
| **Mural** (0.8.0-win; cores vivas e motor de eventos na 0.9.0-win) | `sm/mural.py` (preferências em `data/mural.json`, clima Open-Meteo, arte), `web/js/mural-stage.js`, `mural-palette.js`, `mural-events.js`, `web/js/modules/mural.js`, `web/css/mural.css`; `game_key` e `steam_games` em `sm/game.py` | Ver "Levar o Mural e o webhook ao Linux", abaixo desta tabela | Médio |
| **Notificações por webhook** (0.8.0-win) | `sm/webhook.py` (token com hash, limites), `Alerts.external`, rotas `/api/webhook/*`, `/api/webhook/notify` em `PUBLIC`, cartão em `alerts.js` | Portável sem mudança de lógica; ver a mesma seção | Baixo |
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

### Levar o Mural e o webhook ao Linux

Faça depois da interface repaginada: usam `icons.js` e os tokens de cor.

1. **Webhook:** copie `sm/webhook.py` e `tests/test_webhook.py`. Em `sm/alerts.py`, traga `external()` e a mudança em `_deliver`. Em `sm/server.py`, acrescente `/api/webhook/notify` a `PUBLIC` e à lista liberada antes do cadastro da senha. Em `server.py`, `ctx.webhook = Webhook(ctx.alerts)` depois dos alertas. Traga as rotas, o cartão de `alerts.js` e os descritores de `sm/audit.py`. Nada depende do sistema.
2. **Mural, sem mudança:** copie `web/js/mural-stage.js`, `web/js/modules/mural.js`, `web/css/mural.css` e `tests/test_mural.py`, registre em `modules/index.js` e inclua `mural.css` no `index.html`. Traga os ícones novos de `icons.js`. O `sm/mural.py` só depende de `STEAM_ROOTS`, `_write_private` e `steam_games`.
3. **`sm/game.py` do Linux:** `running_game()` já devolve `appid` e o jogo sem Steam vem do MangoHud. Falta só portar `game_key()`, devolver `key` em `status()` (o `"exe"` no jogo do MangoHud é opcional: sem ele, a chave usa o nome) e trazer `steam_games()`, que é igual.
4. **Caminhos da Steam:** `STEAM_ROOTS` já existe em `sm/desktop.py` do Linux, com as pastas nativa e Flatpak. O cache `appcache/librarycache/<appid>` deve ter o mesmo formato: confira numa instalação real.
5. **FPS no Mural:** vem de `d.game.fps`, então funciona com o MangoHud sem mudança. No Linux, `cpu_load` e `gpu_load` por jogo existem: dá para usar no lugar do uso total.
6. **Não muda:** clima e mídia (o frontend usa `d.media`, igual ao MPRIS atual).
7. **Cores e eventos (0.9.0-win):** `web/js/mural-palette.js` e `web/js/mural-events.js` são só frontend e entram sem mudança, assim como `display` em `sm/webhook.py` e `Alerts.external`. `sunrise` e `sunset` estão em `sm/mural.py`.

Limitações nas duas versões: impedir a tela de apagar exige HTTPS ou localhost, e o iPhone não oferece tela cheia para páginas.

### Levar a aba Atualização ao Linux

Só consulta o GitHub; não instala nada. Faça depois da interface repaginada, porque `update.js` usa `icons.js` e os estilos novos.

1. Copie `sm/selfupdate.py`, `web/js/modules/update.js` e `tests/test_selfupdate.py`. Em `update.js` nada muda: repositório e comandos vêm do backend.
2. Em `sm/selfupdate.py`, troque o que é do sistema: `UPDATE_STEPS = ["/opt/applications/self-monitoring/atualizar.sh"]` (a cópia instalada funciona de qualquer pasta) e um `UPDATE_NOTE` próprio.
3. Em `sm/config.py`, acrescente `update_check` e `update_repo` (padrão `thematheusk3/my-pc-self-control`) e a leitura de `[update]`. Em `config.example.toml`, a seção `[update]`.
4. No `server.py`, crie `ctx.selfupdate = SelfUpdate(cfg.update_repo, cfg.update_check)`; em `sm/sampler.py`, `self.selfupdate = None`. Em `sm/api/routes.py`, as duas rotas.
5. Em `web/js/modules/index.js`, registre `update`. Com 9 módulos mais a Ventoinha, o "Mais" do celular fica com duas linhas, o que o CSS já comporta. Em `system.js`, faça a versão apontar para `#update`.
6. Traga o bloco "Atualização do painel" e `.tab-flag` de `app.css` (se o CSS novo for copiado inteiro, já vêm), e o ícone `arrow-circle-up` em `icons.js`.
7. Confira se o `install.sh` copia o `CHANGELOG.md` para a pasta instalada: é dele que sai o changelog quando o GitHub não responde. O formato (`## 0.4.6`) e a versão sem sufixo já funcionam com o código atual.

Limitação nas duas versões: a comparação é pelo número da versão. Um commit que não aumenta `__version__` não gera aviso.

### Levar a interface repaginada ao Linux

Vale fazer depois de aprovar a interface no Windows. Quase tudo é frontend, e os nomes das classes foram mantidos.

1. No Linux, guarde a interface atual antes de mudar: `git tag -a ui-classica-0.4.6 -m "Interface antiga" && git push origin ui-classica-0.4.6`.
2. Copie inteiros do Windows: `web/css/app.css` (tem todas as classes do CSS do Linux), `web/icon.svg` e a pasta `web/fonts/`.
3. Para o restante, gere o diff no repositório do Windows e aplique no do Linux. Testado na 0.4.6: 11 de 17 arquivos entram limpos e 13 trechos ficam em `.rej` (`media.js`, `apps.js`, `controls.js`, `net.js`, `overview.js`, `system.js`), quase todos trocas de emoji por `ic(...)`. Aplique esses à mão e apague os `.rej`. Os dois repositórios não têm histórico comum, então `--3way` não funciona:
   ```bash
   # no repositório do Windows
   git diff ui-classica-0.5.3 v0.6.0-win -- web ':!web/css/app.css' ':!web/fonts' ':!web/icon.svg' > /tmp/ui.patch
   # no repositório do Linux
   git apply --reject --whitespace=nowarn /tmp/ui.patch
   ```
4. No `sm/server.py`, repita as três mudanças: constante `FONT`, somada a `PUBLIC` e à lista liberada antes do cadastro da senha (senão o login fica sem a fonte); cache longo para `.woff2` em `_static`; `request_queue_size = 128`.
5. Só no Linux: em `fan.js`, troque o ícone por `icon: ic("fan")` (já incluído em `icons.js`). A aba Ventoinha entra no "Mais" do celular, e os estilos `.fan-*` já estão no CSS novo.
6. A faixa de estado cita a temperatura da CPU quando existe. No Linux ela vem do hwmon, então "Sem leitura de temperatura" não deve aparecer.
7. Confira nos dois temas, no PC e no celular, e mova esta linha para "Já equalizado".

Não precisa portar: a correção dos ícones de apps em `sm/desktop.py` (`HBITMAP`) é específica do Windows.

Para voltar atrás no Linux: `git restore --source=ui-classica-0.4.6 -- web`, seguido do `atualizar.sh` ou `install.sh`.

## 2. Só no Linux: dá para levar ao Windows

| Recurso | Como é no Linux | Como fazer no Windows | Esforço |
|---|---|---|---|
| **Script de atualização** `atualizar.sh` (0.4.6) | `git fetch`, lista os commits novos, `git pull --ff-only` e reinstala no mesmo modo (root/usuário); `--verificar`, `--forcar`; a cópia instalada acha o clone por `.origem` | `atualizar.ps1`: mesmo fluxo com `git -C`, ler a versão de `sm\__init__.py` no `origin/main`, chamar `install.ps1` elevado e mostrar "Atualizado: X → Y". O `ATUALIZAR.md` do Windows passa a citar o script | Baixo |
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
| Guia de atualização (`ATUALIZAR.md`) | Windows → Linux | 0.5.3-win → 0.4.6 | No Linux vem junto o `atualizar.sh` (ver seção 2) |
