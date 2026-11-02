# Changelog

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
