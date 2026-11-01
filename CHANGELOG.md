# Changelog

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
