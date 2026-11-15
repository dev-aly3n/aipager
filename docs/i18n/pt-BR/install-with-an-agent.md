# Instalar com um agente

<!-- i18n-source: docs/install-with-an-agent.md @ d63d62ef -->

> Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../install-with-an-agent.md))

Esta página é para um agente de programação com IA (o Claude Code, ou qualquer agente com shell) a quem pediram para instalar e configurar o aipager para a pessoa dele. Siga passo a passo. Todos os comandos daqui rodam sem perguntas e nunca precisam de um terminal interativo.

O aipager deixa essa pessoa usar sessões do Claude Code pelo Telegram. Quando você terminar, a pessoa vai ter um bot do Telegram que só fala com ela, e o aipager rodando em segundo plano neste computador.

## <a id="1-what-only-the-person-can-do"></a>1. O que só a pessoa pode fazer

Peça à pessoa para fazer isto. Você não pode fazer por ela.

1. **Criar o bot.** No Telegram, ela abre o [@BotFather](https://t.me/BotFather), manda `/newbot` e escolhe um nome e um nome de usuário. O BotFather responde com o token do bot (parecido com `123456789:AA...`).
2. **Tocar em Iniciar no bot.** Ela abre o bot novo (o BotFather dá o link `t.me/<bot username>`) e toca em Iniciar. Um bot não pode escrever para quem não fez isso.
3. **Fazer login no Claude Code neste computador.** Se ela já usa o Claude Code aqui, não precisa fazer nada. Num servidor sem tela, ela roda `claude` uma vez no próprio terminal e faz login (aparece um link para abrir em outro aparelho). Se preferir um token de longa duração, ela roda `claude setup-token`, que só imprime o token: ela mesma o salva para as sessões do aipager, no próprio terminal (cola o token, aperta Enter e depois Ctrl-D):

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

   O aipager passa esse token para cada sessão que inicia (veja [Modelo de segurança](security.md#the-claude-credential-what-actually-protects-it)). Você nunca o lê.

## <a id="2-how-the-person-gives-you-the-token"></a>2. Como a pessoa passa o token para você

Quem tem o token do bot controla o bot. Ofereça estas duas opções à pessoa e deixe que ela escolha.

- **A mais segura: um arquivo que você nunca lê.** No próprio terminal, ela roda:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

  depois cola o token, aperta Enter e aperta Ctrl-D. Só ela pode ler o arquivo (permissões 600). Ela pode apagá-lo quando a configuração der certo (o aipager guarda a própria cópia na configuração dele). Ela passa o caminho para você (`~/.config/aipager/bot-token`). O token nunca entra no seu chat, e você passa o caminho para `--token-file`.
- **A mais rápida: colar no chat.** Funciona, mas diga claramente o que isso significa: o token fica na sua conversa e é enviado ao seu provedor de modelos. Se essa conversa puder ser compartilhada algum dia, a pessoa deve depois mandar `/revoke` ao @BotFather e definir o token novo com `aipager setup --force` (passo 3.2, com `--force`). Se ela escolher isso, salve o token no mesmo arquivo sem imprimi-lo: de preferência com a sua própria ferramenta de escrever arquivos e depois `chmod 600 ~/.config/aipager/bot-token`. Se você só tiver um shell, passe-o pelo stdin com um here-document, para ele não ser um argumento do comando que o escreve:

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

  Depois use `--token-file` como abaixo.

O ID de usuário do Telegram da pessoa (o ID do chat dela, um número como `123456789`) não é segredo. Se ela não souber, você pode descobrir (passo 3.3).

## <a id="3-your-steps"></a>3. Seus passos

### <a id="31-install-aipager"></a>3.1 Instalar o aipager

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Usa o uv ou o pipx, o que existir (no macOS também o Homebrew), e instala o uv primeiro se não houver nenhum. Você também pode rodar `uv tool install aipager` ou `pipx install aipager`. Se depois o `aipager` não for encontrado, adicione `~/.local/bin` ao `PATH` do seu shell (`export PATH="$HOME/.local/bin:$PATH"`).

Se o aipager já estava instalado, isso não o atualiza. Confira se `aipager setup --help` funciona; se falhar, o aipager instalado é velho demais: rode `aipager update` e confira de novo. Se `aipager update` disser que é preciso reiniciar o aipager, avise a pessoa; não reinicie você (veja o item 4).

Não rode `aipager config`: ele é o assistente interativo para pessoas.

### <a id="32-set-aipager-up"></a>3.2 Configurar o aipager

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

Use o ID real da pessoa e o caminho do arquivo do token. Em vez de `--token-file`, você pode passar o token por um pipe com `--token-stdin` (por exemplo `--token-stdin < path`); o pipe precisa fechar, e o setup espera por ele no máximo 30 segundos.

O setup confere o token com o Telegram, confere este computador, manda à pessoa uma mensagem de teste ("aipager is set up for you. ...") e só então escreve a configuração, adiciona os hooks do Claude Code em `~/.claude/settings.json` (ou `$CLAUDE_CONFIG_DIR/settings.json`) e, com `--service`, instala e inicia o serviço em segundo plano. Se alguma verificação falhar, nada é escrito.

Adicione `--dry-run` para ver antes o que mudaria. `--role admin` dá à pessoa a função de admin em vez da de dono (o padrão, dono, é o certo para quem configura o próprio computador).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>3.3 Se a pessoa não sabe o ID do Telegram dela

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

Peça à pessoa para tocar em Iniciar no bot (ou mandar qualquer mensagem) enquanto isto espera (até 5 minutos; `--timeout SECONDS` muda isso). Ele imprime o remetente mais recente em `candidate` (`id`, `first_name`, `last_name`, `username`) e não salva nada. Mostre o nome e o nome de usuário à pessoa e pergunte “É você?”. Só quando ela disser que sim, rode o passo 3.2 com `--chat-id` igual a `candidate.id`. Se `other_candidates` não for 0, outras pessoas também escreveram para o bot: confirme bem.

### <a id="34-read-the-result"></a>3.4 Ler o resultado

Com `--json`, o setup imprime exatamente um objeto JSON no stdout. Leia `exit_code` (o mesmo que o código de saída do processo), `status`, `error`, `message`, `fix` e `next_step`. Se o stdout não for JSON (uma opção desconhecida, uma palavra errada depois de `setup`, ou um aipager tão velho que não tem `setup`), o código de saída é 2 e o motivo vem em texto simples no stderr: leia, e para um aipager velho veja o item 3.1.

| Saída | `error` | O que você faz |
|---|---|---|
| 0 | | `installed`, `updated` ou `unchanged`: pronto, vá para o 3.5. `dry_run`: nada foi escrito; rode o mesmo comando sem `--dry-run`. detect-chat com `found`: confirme a pessoa como no 3.3 e depois rode o 3.2. |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Mostre `message` e `fix` à pessoa. `ambiguous_install`: a pessoa resolve no `aipager config`. Não edite arquivos à mão. Um `settings_invalid` que pede um `CLAUDE_CONFIG_DIR` absoluto: o `fix` diz qual arquivo mudar (`daemon.env` ou um arquivo de configurações do Claude Code); pergunte a pasta para a pessoa, mude só esse valor e só se ela pedir, e rode o setup de novo. |
| 2 | `usage`, `token_malformed`, `token_stdin_timeout`, `bad_chat_id`, ... | Sua entrada estava errada; `fix` diz qual opção. `token_malformed`: o arquivo não tem um token de bot; peça à pessoa para conferir. |
| 3 | `deps_missing` | Siga `fix` (cada item que falta também tem o próprio `fix` em `deps.items`) e rode o setup de novo. |
| 4 | `token_rejected` | O Telegram recusou o token. Peça o token de novo à pessoa (ou um novo do @BotFather). |
| 5 | `chat_not_started`, `bot_blocked` | Peça à pessoa para abrir o bot e tocar em Iniciar (ou desbloqueá-lo), e rode o setup de novo. |
| 6 | `existing_install` | O aipager já está configurado com outro token, chat ou função. Diga à pessoa o que é diferente e pergunte antes de adicionar `--force`. |
| 7 | `telegram_unreachable` | Problema de rede ou o Telegram está ocupado. Espere um minuto e tente de novo. |
| 8 | `detect_timeout` | O detect-chat não viu nenhuma mensagem. Peça à pessoa para tocar em Iniciar de novo e rode outra vez. Com o alerta `updates_backlog`, tocar em Iniciar não adianta: pergunte à pessoa o ID numérico dela. |
| 9 | `daemon_running` | detect-chat: um aipager para este bot já está rodando e recebe as mensagens dele. Pergunte à pessoa o ID numérico dela (ou pergunte antes de pará-lo com `aipager service stop`). |
| 10 | `updates_conflict` | detect-chat: outro programa lê as mensagens deste bot. Pergunte à pessoa o ID numérico dela. |
| 130 | `interrupted` | Parado com Ctrl-C. |

Se `daemon.restart_needed` for true, o aipager precisa reiniciar para usar a mudança, ou o setup não conseguiu saber se ele está rodando (o alerta `daemon_unknown`). Siga `next_step`, que diz qual dos dois (se ele sugerir `aipager start`, que fica em primeiro plano, prefira `aipager service install`). Avise a pessoa e reinicie (`aipager service stop` e depois `aipager service start`) só se ela disser para fazer, e nunca de dentro de uma sessão que a pessoa usa pelo aipager (veja o item 4). A lista completa de erros, alertas e chaves JSON está na [referência do comando setup](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>3.5 Conferir a instalação

```sh
aipager doctor --json
```

Ele só lê e não envia nada. A saída 0 quer dizer que nenhuma verificação falhou. Para cada verificação cujo `status` seja `fail` (ou `warn`), mostre à pessoa o `title`, o `detail` e o `fix` dela.

### <a id="36-report-back-to-the-person"></a>3.6 Contar o resultado para a pessoa

Diga em poucas linhas:

- o bot (`@` + `bot_username`), e se a mensagem de teste foi enviada (`test_message`);
- o que foi configurado (`changed`, e `service.result`);
- como iniciar uma sessão do Claude: mandar `/new` para o bot no Telegram, ou rodar `aipager session <name>` num terminal;
- o que o `doctor` apontou, e o `next_step` se ele pedir alguma coisa.

## <a id="4-what-you-must-not-do"></a>4. O que você não deve fazer

- Não imprima, não dê echo nem `cat` no token, e nunca o coloque numa linha de comando (`--token` é recusado de qualquer jeito). Passe-o só como arquivo ou pelo stdin.
- Não edite `~/.config/aipager/` (fora o arquivo do token do passo 2) nem `~/.claude/settings.json` à mão. Quem os escreve são `aipager setup` e `aipager config`. A única exceção é um grupo num aipager mais antigo que este, em que `aipager setup group --help` falha: veja [5.5](#55-older-installs).
- Não adicione `--force` sem perguntar à pessoa: ele substitui o token, o chat ou a função que ela já tem. Com `setup group`, ele muda ou tira funções, ou dá a função `owner`.
- Pelo aipager, só as tarefas do dono podem rodar `aipager setup` (em qualquer forma), `aipager config` ou `aipager uninstall`: as regras de segurança do aipager bloqueiam esses comandos para qualquer outra função. Se a pessoa falar com você pelo aipager sem ser o dono, peça para ela mesma rodar o comando num terminal.
- Não reinicie nem feche a sua própria sessão do Claude Code. Numa primeira instalação, a sessão em que você está não está sob o aipager, e não precisa estar: as sessões novas vêm de `/new` no Telegram ou de `aipager session <name>`. Se o aipager já estiver configurado e a pessoa falar com você por ele (por exemplo para rodar `setup --force` com um token novo), não pare nem reinicie o aipager por conta própria: sua tarefa e a resposta dela ficariam pela metade. Diga à pessoa para reiniciá-lo quando você terminar.

## <a id="5-groups"></a>5. Grupos

O `aipager setup` configura o chat privado da pessoa com o bot. O `aipager setup group` adiciona um grupo do Telegram, os membros e as funções deles a uma instalação que já está configurada, sem perguntas (o `aipager config` faz o mesmo com perguntas). Ele lê o token do bot da instalação, nunca reinicia o aipager e não manda nada para o grupo. Veja [Uso em equipe e grupos](groups.md) e [a referência do comando](commands.md#aipager-setup-group).

### <a id="51-what-only-the-person-can-do"></a>5.1 O que só a pessoa pode fazer

Estes passos você não consegue fazer; peça para a pessoa fazê-los no Telegram:

1. Adicionar o bot ao grupo.
2. Tornar o bot administrador do grupo, com permissão para fixar mensagens (o aipager fixa uma barra de status ali; sem essa permissão o grupo funciona, só que sem barra de status). Só um administrador do grupo consegue fazer isso.
3. Mandar `/start` no grupo, para você achar o ID dele (5.2).
4. Passar para você, de cada membro, o ID numérico de usuário do Telegram, um nome curto (como o aipager mostra a pessoa, como `@label`) e uma função: `owner`, `admin`, `user`, `read_only` ou uma função própria do `policy.yaml`. A função `owner` tem controle total deste computador: ela é da pessoa que cuida dele e de mais ninguém. Se ela não souber algum ID, o `aipager config` consegue achá-lo enquanto essa pessoa escreve no grupo.

### <a id="52-find-the-group"></a>5.2 Achar o grupo

```sh
aipager setup detect-group --json
```

Ele espera (até 300 segundos, `--timeout SECONDS`) uma mensagem num grupo que ainda não está configurado, mandada no máximo 10 minutos antes de você rodá-lo, e imprime o grupo (`candidate.id`, `candidate.title`). Ele não guarda nada. Mostre o título à pessoa e use o ID só depois que ela confirmar que é o grupo certo. Saída 8 quer dizer que nada chegou: peça para ela mandar `/start` no grupo de novo.

### <a id="53-set-it-up"></a>5.3 Configurar o grupo

Rode primeiro com `--dry-run` e depois o mesmo comando sem ele:

```sh
aipager setup group --chat-id -1001234567890 \
  --member 111111111:alice:owner --member 222222222:bob:user \
  --label team --dry-run --json
```

- `--member ID:LABEL:ROLE` uma vez por pessoa. Para adicionar alguém depois, rode de novo só com o `--member` novo: os outros continuam.
- `--remove-member ID` tira uma pessoa.
- `--label NAME` dá nome ao grupo, `--root PATH` define a pasta onde os membros `user` e `read_only` dele trabalham (padrão `~/aipager-users/group-<label>`), e `--block-edits` bloqueia Write e Edit para os membros `user` dele.
- Rodar o mesmo comando de novo não muda nada (`unchanged`).
- Saída 6 (`needs_force`) quer dizer que a mudança precisa de `--force`: `force_reasons` diz por quê (uma mudança de função, um dono dado, rebaixado ou tirado, uma pasta raiz arriscada). Mostre isso à pessoa e acrescente `--force` só se ela concordar.

### <a id="54-read-the-result"></a>5.4 Ler o resultado

| Saída | O que fazer |
|---|---|
| 0 | Pronto (`created`, `updated`, `unchanged` ou `dry_run`). Diga à pessoa o `label` do grupo, os `members` dele e o que `daemon.outcome` diz. |
| 2 | Alguma opção está errada: `error`, `message` e `fix` dizem qual. Corrija e rode de novo. |
| 6 | `needs_force`: pergunte à pessoa sobre cada item de `force_reasons`. |
| 11 | `not_set_up`: configure o aipager primeiro ([3](#3-your-steps)). |
| 1 | Qualquer outra coisa (`config_malformed`, `policy_malformed`, `last_member`, `config_changed`...): mostre `message` e `fix`. Depois de `config_changed`, rode de novo. |

`daemon.outcome` diz o que o aipager em execução fez: `reloaded_live` (o grupo já funciona), `no_daemon` (vai funcionar quando o aipager iniciar), `restart_needed` ou `not_applied` (mostre `next_step` à pessoa; não reinicie o aipager por conta própria). Os alertas nunca mudam o código de saída, mas mostre-os: `bot_not_in_group`, `bot_not_admin`, `bot_cannot_pin` e `group_not_found` querem dizer que falta à pessoa um passo do 5.1, e vale dizer `owner_granted` com todas as letras.

### <a id="55-older-installs"></a>5.5 Instalações mais antigas

Se `aipager setup group --help` falhar, este aipager é mais antigo que o comando. Peça à pessoa para atualizar o aipager (`aipager update`) ou, se ela quiser o grupo já, adicione-o à mão:

1. Adicione um chat de grupo em `~/.config/aipager/aipager.yaml`, seguindo [a estrutura](groups.md#aipageryaml-schema-team-parts). O arquivo guarda o token do bot: nunca o imprima nem dê `cat` nele, e mantenha as permissões em 0600 (`chmod 600 ~/.config/aipager/aipager.yaml`).
2. Confira com `aipager policy validate` e depois se a linha `scope mode: ...` do `aipager doctor` conta o grupo novo.
3. Mande ao aipager uma recarga sem reiniciar, como abaixo.

No Linux, com o serviço em segundo plano:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

No macOS:

```sh
pid=$(launchctl list | awk '$3 == "com.aipager.daemon" {print $1}')
[ "${pid:-0}" -gt 0 ] 2>/dev/null && kill -USR1 "$pid"
```

A verificação `-gt 0` importa: sem aipager rodando, `kill -USR1 0` mandaria o sinal para os processos do seu próprio shell. Nunca reinicie o aipager de dentro de uma sessão do aipager: sua tarefa ficaria pela metade.

## <a id="see-also"></a>Veja também

- [Comandos do bot e o comando setup](commands.md)
- [Solução de problemas](troubleshooting.md)
- [Modelo de segurança](security.md)
