# Como atualizar o self-monitoring (Linux)

O painel que está rodando **não é** a pasta do projeto: o instalador copia tudo para
`/opt/applications/self-monitoring` e é de lá que o serviço `self-monitoring` roda. Por isso, baixar código novo
não muda nada sozinho: é preciso baixar **e** reinstalar. O `atualizar.sh` faz as duas coisas.

**O que é preservado:** a configuração (`config.toml`), a senha, as sessões abertas (ninguém precisa entrar de
novo), os históricos, os alertas, o registro de ações, as capturas e o estado da ventoinha (`data/`).
**O que é substituído:** o código do painel (`sm/`, `web/`, `server.py`, `tools/`).

## Jeito rápido: `atualizar.sh`

Na pasta do projeto (onde você fez o `git clone`):

```bash
cd ~/Projetos/self-monitoring
./atualizar.sh
```

Ele:
1. mostra a versão instalada;
2. procura versão nova no GitHub e lista o que mudou;
3. baixa (`git pull`);
4. reinstala **do mesmo jeito que já estava**: serviço como root continua root (`--root`), serviço no seu usuário
   continua no seu usuário. Pede a senha do `sudo` só nesse passo;
5. mostra `Atualizado: 0.4.4 → 0.4.5`.

Se já estiver na versão mais nova, ele avisa e não faz nada.

| Comando | O que faz |
|---|---|
| `./atualizar.sh` | Atualiza, se houver versão nova |
| `./atualizar.sh --verificar` | Só diz se há versão nova e o que mudou, sem baixar nem instalar |
| `./atualizar.sh --forcar` | Reinstala mesmo já estando na versão mais nova (ex.: algo ficou estranho no serviço) |
| `/opt/applications/self-monitoring/atualizar.sh` | O mesmo, de qualquer pasta (a cópia instalada sabe de onde veio) |

**Primeira vez numa máquina com versão antiga** (que ainda não tem o `atualizar.sh`): rode o `git pull` uma vez à mão
e depois o script:

```bash
cd ~/Projetos/self-monitoring
git pull
./atualizar.sh --forcar
```

## Jeito manual

```bash
cd ~/Projetos/self-monitoring
git pull
systemctl show self-monitoring -p User   # User=root → use --root; seu usuário → sem opção
sudo ./install.sh            # ou: sudo ./install.sh --root
```

## Conferir a versão

- No painel: aba **Sistema**, cartão **Monitor**, linha **Versão**.
- No terminal: `python3 -c "import sys; sys.path.insert(0,'/opt/applications/self-monitoring'); import sm; print(sm.__version__)"`

O que mudou em cada versão está no [CHANGELOG.md](CHANGELOG.md).

## Problemas comuns

**"Há alterações locais na pasta do projeto"**: algum arquivo do projeto foi modificado nessa máquina, e o
`git pull` não sobrescreve. Veja quais com `git status`. Se não for nada seu que queira guardar, descarte e rode de novo:

```bash
git checkout -- .
./atualizar.sh
```

A configuração **não** fica nessa pasta, e sim em `/opt/applications/self-monitoring/config.toml`, então não se perde.

**"Não achei a pasta do projeto"**: rodou a cópia de `/opt` numa instalação antiga, que ainda não anotava a pasta de origem.
Rode uma vez de dentro da pasta do projeto (`cd ~/Projetos/self-monitoring && ./atualizar.sh`); daí em diante a cópia de
`/opt` também funciona.

**O painel não voltou depois de atualizar**: veja o motivo com `journalctl -u self-monitoring -n 50`.
Para entender sessão, áudio, tela e ventoinhas: `/opt/applications/self-monitoring/tools/diagnostico.sh`.

## Voltar para a versão anterior

Cada versão é um commit no git. Para voltar:

```bash
cd ~/Projetos/self-monitoring
git log --oneline | head           # ache o commit da versão desejada (ex.: "0.4.4: ...")
git checkout <commit>
sudo ./install.sh                  # com --root se o serviço for root
```

Para voltar a receber atualizações depois: `git checkout main` e `./atualizar.sh`.
