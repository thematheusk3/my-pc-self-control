# Changelog

## 0.4.7
Trazido da versão Windows (tudo da seção 1 do [PARIDADE.md](PARIDADE.md) que se aplica ao Linux):
- Controles > Tela: cada monitor aparece com o **nome real** (EDID, ex.: "LG ULTRAGEAR (DP-4)"), o **tipo de conexão**
  (DisplayPort, HDMI, tela embutida), a **escala em %** e o chip **principal**.
- Visão geral > GPU: **uso por engine** (3D, compute, vídeo, cópia), somando os processos (fdinfo do DRM) ou, na
  NVIDIA, pelo driver.
- NVIDIA lida pelo **NVML** (`libnvidia-ml.so.1`, via ctypes) em vez de abrir o `nvidia-smi` a cada segundo;
  sem a biblioteca, continua usando o `nvidia-smi`. Inclui uso do codificador/decodificador de vídeo.
- Processos > **Finalizar** agora fecha as janelas do processo como clicar no X (script de uma vez só no KWin; no X11
  com `wmctrl`): o app pode perguntar se quer salvar. Sem janela, SIGTERM como antes. "Forçar" continua SIGKILL.
- **Processos essenciais** (systemd, logind, D-Bus, KWin, Xwayland, plasmashell, PipeWire, WirePlumber, SDDM/plasmalogin,
  NetworkManager...): aviso do que acontece se pararem e confirmação extra (`confirm_critical`); não é bloqueio total.
- Histórico de processos: **PID reaproveitado** é detectado (horário de início diferente) e o processo antigo vai
  para os encerrados em vez de ser confundido com o novo.
- Sistema > Atualizações: aviso de que o **PC precisa reiniciar** (kernel novo instalado com o antigo rodando;
  `/run/reboot-required` no Debian/Ubuntu; `needs-restarting -r` no Fedora). API: campo `reboot` em `GET /api/updates`.
- Instalador: sem `ufw`/`firewalld`, avisa se um firewall `nftables`/`iptables` bloqueia a entrada e a porta do
  painel não está liberada (o celular não conseguiria abrir), com o comando para liberar.
- Processos > Analisar: confere se o executável **ainda é igual ao que o pacote instalou** (SHA-256 do banco do
  `pacman`, `md5sums` do `dpkg` ou `rpm --dump`); se foi alterado, vira sinal **alto** com o comando para reinstalar.
  É o equivalente Linux à verificação de assinatura digital do Windows.

## 0.4.6
- `./atualizar.sh`: baixa a versão nova do GitHub, lista o que mudou e reinstala do mesmo jeito que o serviço já
  está (root ou seu usuário); `--verificar` só consulta, `--forcar` reinstala mesmo sem versão nova. Funciona também
  pela cópia instalada (`/opt/applications/self-monitoring/atualizar.sh`): o instalador anota a pasta de origem.
- [ATUALIZAR.md](ATUALIZAR.md): como atualizar, conferir a versão, resolver problemas e voltar à versão anterior.

## 0.4.5
Trazido da versão Windows (0.5.2-win e 0.5.3-win):
- Sistema > **Quem está acessando**: sessões abertas com aparelho, IP, origem (este PC, rede local, VPN, internet),
  nome na rede (DNS reverso) e MAC (tabela ARP do kernel), última atividade e botão para encerrar a sessão de outro
  aparelho (ou todas as outras). Histórico dos últimos acessos: logins, saídas, senhas erradas, bloqueios, sessões
  encerradas (`data/access-log.json`).
- **Todo login no painel mostra uma notificação na tela do PC** (e todo IP bloqueado por senha errada), sempre,
  independente dos alertas. O alerta "Novo acesso ao painel" continua indo para o painel/ntfy e não duplica no PC.
- Sistema > **Ações feitas pelo painel**: registro de tudo que muda algo no PC ou no painel (processos, energia,
  sessão, apps, áudio, mídia, tela, captura, ventoinha, FPS, senha, sessões, alertas), com aparelho, IP, sessão, hora
  e resultado, inclusive tentativas recusadas. Ajustes seguidos (arrastar volume, brilho, velocidade da ventoinha)
  viram uma linha. Filtros por categoria e por aparelho; "Ver ações" em cada sessão (`data/audit-log.json`).
- API: `GET /api/auth/sessions`, `POST /api/auth/sessions/end`, `GET /api/audit`.

## 0.4.4
- **Serviço como root** (`install.sh --root`): sessão, áudio, tela, captura, mídia, janela em foco e abrir apps
  não funcionavam ("Ninguém está logado", "Servidor de áudio indisponível", `kscreen-doctor: -6`), porque o painel
  procurava a sessão do próprio root. Agora esses comandos rodam como o dono da área de trabalho (gravado pelo
  instalador em `SM_DESKTOP_USER`; senão o do login automático ou da sessão aberta).
- Instalador: rodado de um terminal já como root (`su -`) sem `--root`, usa o dono da sessão gráfica em vez de
  instalar como root sem avisar; nova opção `--user NOME`. O `login-pelo-celular.sh` recusa o mesmo engano.
- Sistema > Sessão: se há sessão aberta de outra conta, avisa que o painel está ligado à conta errada e como corrigir.
- Sensores/Ventoinha: sem nenhuma ventoinha encontrada, mostra qual driver falta para a placa-mãe (ex.: MSI →
  NCT6687D, Gigabyte → it87) e como carregá-lo.
- `tools/diagnostico.sh`: coleta, sem mudar nada, o que é preciso para entender sessão, áudio, tela e ventoinhas.
- `PARIDADE.md`: o que dá para levar entre as versões Linux e Windows (mesmo arquivo nos dois repositórios).

## 0.4.3
- Controles > Áudio: novo cartão **Mídia** no topo, com todos os players abertos (Spotify, navegador, VLC...):
  capa, faixa, progresso e ⏮ ⏯ ⏭, mais **voltar/avançar 10 s** quando o player permite.
- Controles > Áudio: no celular o menu de saída de cada app passava da borda da tela; agora desce para a linha de baixo.
- API: `POST /api/media` aceita `{"op": "seek", "offset": -10}` (segundos).
- README: seção "Sistemas suportados" (feito para o CachyOS; Windows talvez no futuro).

## 0.4.2
- Captura de tela: ao capturar, a imagem abre na hora por cima do painel (com "Tamanho real" e "Baixar").

## 0.4.1
- Correção: depois de tirar uma captura, a lista falhava com 501 `Unsupported method ('{}GET')` até dar F5.
  O servidor não lia o corpo de POSTs cujas rotas não o usam (captura, apagar captura, logout); com keep-alive
  o corpo ficava na conexão e corrompia o pedido seguinte. Agora o corpo é sempre lido antes da rota.

## 0.4.0
- Topo: horário da última atualização; "Offline · HH:MM:SS há X" quando os dados param de chegar
  (e reconecta sozinho quando o celular acorda ou a conexão trava sem erro).
- Captura de tela: a imagem aparece na hora, sem F5 (o painel esperava só o arquivo existir e servia o PNG
  pela metade, que o navegador guardava em cache); a lista se atualiza sozinha.
- Visão geral > No PC agora: janela em foco (KWin/X11) e mídia tocando (MPRIS) com tocar/pausar/pular.
- Visão geral > Downloads Steam: progresso, velocidade e tempo restante de downloads e atualizações.
- Topo: abas rolam em telas médias em vez de sobrepor o nome do PC.

## 0.3.0
- Jogo atual e FPS (MangoHud): FPS, 1% low, frametime; leitura liga/desliga (desligada não custa nada).
- Controles > Áudio: saída padrão (fone/HDMI...), volume e mudo por dispositivo, áudio por aplicativo
  (volume, mudo e por qual saída sai), microfone (mudo, volume, quem está usando).
- Controles > Tela: resolução/taxa com reversão automática em 20 s; brilho/contraste do monitor via DDC/CI;
  "apagar tela" (brilho e contraste em 0, monitor continua conectado); espera (DPMS).
- Controles > Captura de tela: sempre mostra um aviso na tela do PC antes de capturar.
- Energia: timer para desligar/reiniciar (sempre forçado, com avisos na tela do PC); "forçar" marcado por padrão.
- Alertas: regras configuráveis (temperatura, disco, memória, ventoinha, processo suspeito, porta nova,
  login no painel, senha errada, atualizações, FPS baixo); avisos no painel, na tela do PC e no celular (ntfy).
- Sistema: atualizações pendentes (pacman, AUR, flatpak, apt, dnf), destacando kernel e drivers.

## 0.2.0
- Ventoinha: controle automático (curva com histerese), manual e devolução à placa; detecção automática do canal.
- Processos: histórico de encerrados, análise (pacote, hash, VirusTotal, cadeia de pais, conexões) e lista de suspeitos.
- Rede: conexões por programa, bytes enviados/recebidos, conexões encerradas e portas abertas.
- Apps: abrir aplicativos e jogos instalados pelo celular.
- Sessão: bloquear/desbloquear a tela pelo painel; login automático com a sessão bloqueada (`tools/login-pelo-celular.sh`).
- Energia: desligar e reiniciar com confirmação e opção de forçar.
- Instalador: instala dependências, cria `config.toml` a partir do exemplo, `--uninstall`/`--purge`.
- Testes automáticos e CI.

## 0.1.0
- Painel inicial: visão geral, processos, sensores, sistema; senha e sessões.
