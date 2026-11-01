# Changelog

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
