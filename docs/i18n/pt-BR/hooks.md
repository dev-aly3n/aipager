# Eventos de hook

<!-- i18n-source: docs/hooks.md @ 7228205 -->

> Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../hooks.md))

O Claude Code roda comandos de hook em momentos fixos de uma tarefa: uma mensagem é enviada, uma ferramenta está para rodar, uma ferramenta terminou, a tarefa terminou, e assim por diante. O aipager se conecta a todos eles e os transforma em atividade no Telegram: o cartão de atividade, os pedidos de permissão, as respostas, os avisos e o registro de auditoria.

## <a id="how-it-is-wired"></a>Como é ligado

O `aipager config` (ou o `aipager setup`) adiciona as entradas do aipager em `~/.claude/settings.json`, e o aipager as confere a cada início:

- um comando `aipager-hook` para cada um dos 16 eventos abaixo, com o matcher `*` nos eventos de ferramenta (`PreToolUse`, `PostToolUse`, `PermissionRequest`);
- um tempo limite de 5 segundos no `PreModelSwitch` e, pelo `aipager config` e pelo `aipager setup`, um de 30 segundos no `PermissionRequest` (o próprio hook espera no máximo 20 segundos, veja abaixo); as outras entradas usam o padrão do Claude Code;
- o comando `statusLine`, `aipager-statusline`.

O `aipager uninstall` tira essas entradas de novo (veja [comandos](commands.md#command-line-aipager-uninstall)).

O Claude Code inicia um processo novo do `aipager-hook` (`aipager.dtach.notify_hook:main`) para cada evento. Ele lê o JSON do evento pelo stdin, acrescenta dois campos (abaixo) e o manda ao aipager como um datagrama por um socket do Unix. O socket é, nesta ordem: `$AIPAGER_INSTANCE_DIR/aipager.sock` para uma instância de teste isolada, `$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock`, ou `/tmp/aipager.sock`. O envio nunca espera: se o aipager não estiver escutando, o evento se perde e o vigia de sessões se atualiza a partir dos arquivos da sessão.

A maioria dos eventos custa ao Claude Code uns 20 a 25 ms de início de processo. Alguns também carregam o código de regras do aipager: antes de uma ferramenta rodar (a verificação de segurança, veja [PreToolUse](#pretooluse)) uns 70 ms, no fim de uma tarefa (para apagar a marca de tarefa aberta) uns 55 ms, e o `UserPromptSubmit` (que escreve as regras da tarefa). Um `PermissionRequest` espera de propósito o seu toque, por até 20 segundos.

O hook limita a própria memória a 1 GB. Um hook que chega ao limite sai com um erro (no `PreToolUse`, com o código de saída que o Claude Code lê como recusa) e manda ao aipager um datagrama `hook_memory_cap_hit`, que vira um aviso no chat, com um botão “🐞 Relatar isto” para o dono da instalação (veja [relatórios de problemas](problem-reports.md)). Defina `AIPAGER_DEBUG=1` no ambiente da sessão para o hook e a linha de status imprimirem os diagnósticos deles no stderr.

## <a id="what-the-daemon-receives"></a>O que o aipager recebe

O datagrama é o JSON de hook do próprio Claude Code com os campos que o aipager acrescenta (alguns eventos trazem mais, indicados em cada evento abaixo):

| Campo | Significado |
|---|---|
| `hook_event_name` | O evento (campo do Claude Code; o aipager distribui o trabalho por ele). |
| `session_id`, `transcript_path`, `cwd`, ... | Os campos do próprio Claude Code, do jeito que chegam. `session_id` é o ID da conversa no Claude Code. |
| `session` | Acrescentado pelo aipager: o nome dele para a sessão, `claude-<name>`, da variável `CLAUDE_DTACH_SESSION` que o aipager define ao iniciar uma sessão. Os eventos de uma sessão do Claude Code que o aipager não iniciou não têm nome e o aipager os ignora. |
| `sl_tokens` | Acrescentado pelo aipager, em todo evento menos o `MessageDisplay`, quando o arquivo da linha de status da sessão existe: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added` e `lines_removed`. |

Os eventos de ferramenta também trazem `tool_name` e `tool_input`. Todo evento que acontece dentro de um subagente traz `agent_id` (e os eventos de subagente, `agent_type`); os do loop principal não, e o aipager usa isso para colocar as ferramentas de um agente na linha desse agente.

## <a id="event-reference"></a>Referência dos eventos

Os tratadores do aipager ficam em `aipager/dtach/hook_receiver.py`; o trabalho do próprio hook fica em `aipager/dtach/notify_hook.py`.

### <a id="sessionstart"></a>`SessionStart`

Uma sessão iniciou, foi retomada ou teve a conversa compactada. Uma sessão que o aipager ainda não acompanha é registrada. Depois de compactar, o cartão diz `📦 name · Conversa compactada: 82% → 4%` (veja [PreCompact](#precompact--postcompact)). Para uma conversa nova, o hook também apaga a marca de tarefa aberta da tarefa anterior.

### <a id="sessionend"></a>`SessionEnd`

A sessão terminou. Ela é marcada como GONE: sai da barra de status fixada e vai para as sessões encerradas, de onde o `/resume` pode trazê-la de volta. `aipager session <name>` ou `/new <name>` cria uma nova.

### <a id="userpromptsubmit"></a>`UserPromptSubmit`

O Claude pegou uma mensagem, digitada no terminal ou enviada pelo Telegram.

O hook primeiro casa a mensagem com as notas de permissão da sessão (`/tmp/claude-notes-<session>/`, uma para cada mensagem do Telegram que o aipager digitou) e escreve as regras da tarefa em `/tmp/claude-policy-<session>.json`, que a verificação do [PreToolUse](#pretooluse) lê (veja [Modelo de segurança → como as regras valem no uso em equipe](security.md#team-mode-enforcement)). Ele avisa o aipager quais mensagens o Claude pegou (um datagrama `queue_pickup`) antes de repassar o evento. Depois imprime contexto extra para o Claude quando houver: o estilo de resposta escolhido em `/settings` e, para uma resposta a uma mensagem anterior, o que essa mensagem dizia e quando. Uma mensagem `<task-notification>` (o Claude acordando sozinho quando um agente ou comando em segundo plano termina) é o mesmo trabalho continuando: não casa com nenhuma nota e mantém as regras da tarefa.

O aipager marca a sessão como trabalhando, abre o cartão de atividade se não houver nenhum (uma mensagem enviada pelo Telegram já tem um) e transforma o 👀 das mensagens que o Claude pegou em 👍. Uma mensagem enviada enquanto há uma tarefa em andamento ganha o 👍 quando o Claude a junta a essa tarefa ou começa a próxima com ela.

### <a id="pretooluse"></a>`PreToolUse`

Uma ferramenta está para rodar. É aqui que roda a verificação de segurança do próprio aipager, dentro do hook, antes de o Claude Code olhar as regras de permissão dele:

- Uma tarefa que começou pelo Telegram fica presa às regras da função de quem a mandou e ao piso de segurança (caminhos protegidos, padrões de comandos), do qual só a função `owner` está isenta; uma tarefa que o aipager não consegue atribuir a ninguém recebe o piso; uma mensagem que você digitou no terminal não tem restrições. As regras estão em [uso em equipe](groups.md#how-rules-work) e em [Modelo de segurança](security.md#team-mode-enforcement).
- Quando uma regra nega a execução, o hook imprime uma decisão `deny`, o Claude Code recusa a ferramenta sem perguntar, e o aipager é avisado (`safety_blocked`): a tarefa é interrompida com `🛑 x1 · Bloqueado por uma regra de segurança: ... (interrompida)`, e todas as ferramentas seguintes dessa tarefa também são negadas.
- Uma verificação que falha nega, a não ser que as regras da sessão possam ser lidas e deem a isenção do dono.

Do lado do aipager, a execução vira uma linha do cartão de atividade (a de um subagente vai na linha desse agente), e um `Write` ou `Edit` mostra uma prévia do diff quando `/settings` → Prévias de diff está ligado (por padrão, desligado). Uma chamada `AskUserQuestion` vira a pergunta com um botão por opção; uma que um agente auxiliar do Claude Code faz quando não há tarefa em andamento não aparece (não é o Claude perguntando a você). A chamada `Agent` do próprio Claude deixa `subagent_type` e `description` para o agente que ela inicia, e uma execução de um subagente é a última atividade desse agente: as duas aparecem no status embaixo de uma resposta enquanto o agente trabalha em segundo plano.

### <a id="permissionrequest"></a>`PermissionRequest`

O Claude Code vai pedir permissão. O cartão de atividade vira o pedido, com o comando ou o caminho de verdade embaixo da descrição do próprio Claude (veja [comandos → pedidos de permissão](commands.md#permission-prompts)): Permitir, Negar, Parar, e Sempre permitir só quando o Claude Code oferece uma regra de verdade para lembrar.

O hook responde o pedido sozinho quando consegue. Ele abre um socket de resposta de vida curta, manda o endereço com o evento e espera o seu toque por até 20 segundos. Um toque a tempo volta como uma decisão do `PermissionRequest` (permitir, permitir com a regra, ou negar), então o Claude Code nunca mostra a caixa de diálogo dele. Se nenhum toque chegar a tempo, o hook avisa o aipager que a espera acabou (`permission_reply_timeout`) e não imprime nada; também não imprime nada quando não consegue chegar ao aipager ou quando algo falha nesse caminho. Aí o Claude Code mostra a caixa de diálogo dele no terminal, e um toque depois a responde digitando as teclas. O registro de auditoria (`~/.claude/aipager-audit.jsonl`) guarda por qual caminho cada toque respondeu: `"via": "hook_decision"` ou `"via": "keystroke_fallback"`.

### <a id="posttooluse--posttoolusefailure"></a>`PostToolUse` / `PostToolUseFailure`

Uma ferramenta terminou, ou falhou. A linha dela no cartão de atividade fica fixa (uma falha é marcada na linha), e a resposta a um pedido aberto é percebida: um Permitir ou Negar antigo para ele não digita mais nada depois disso. A sessão guarda as 200 linhas de ferramentas mais recentes.

Um `Bash` que o Claude Code mandou para segundo plano informa um `backgroundTaskId`. O aipager passa a acompanhá-lo como um comando em segundo plano (só os do loop principal: uma chamada com `agent_id` é desse subagente) até ver o fim: um `<task-notification>` com um `<status>` (na mensagem que acorda o Claude, ou na conversa salva quando chega no meio de uma tarefa), um `TaskStop` ou `KillShell` com o ID dele, o fim da sessão, ou duas horas (`AIPAGER_BG_SHELL_MAX_TRACK`, em segundos, padrão 7200). Veja [comandos](commands.md#idle-responses) para a linha dele no cartão.

### <a id="notification"></a>`Notification`

O aipager lê o `notification_type` do Claude Code:

- `permission_prompt`: um sinal mais lento e antigo de um pedido de permissão. Só mostra o pedido quando o `PermissionRequest` ainda não o mostrou.
- `idle_prompt` enquanto um pedido de permissão ou uma pergunta espera: a sessão continua esperando, e o chat recebe `⬆️ jim · ainda esperando sua resposta lá em cima`, uma vez por espera.
- `idle_prompt` nos outros casos: tratado como `Stop` (abaixo). Pega uma tarefa cujo `Stop` se perdeu; uma resposta já entregue nunca é enviada de novo.

### <a id="stop"></a>`Stop`

A tarefa terminou. A sessão fica pronta, o cartão de atividade fica fixo do jeito que o seu formato diz, e a resposta do Claude é enviada (do `last_assistant_message` do evento, ou senão da conversa salva). Depois saem as mensagens que esperavam a tarefa terminar. Agentes e comandos em segundo plano que ainda rodam mantêm o trabalho aberto: o cartão fica como status desse trabalho e a resposta termina com um status ao vivo desse trabalho em segundo plano: o que cada agente está fazendo e quantos comandos em segundo plano deste trabalho estão rodando (veja [comandos → agentes que ainda estão trabalhando](commands.md#agents-still-running-when-the-answer-goes-out)). O hook primeiro apaga a marca de tarefa aberta, a não ser que o Claude ainda tenha na fila uma mensagem que vira a próxima tarefa na hora.

### <a id="stopfailure"></a>`StopFailure`

A tarefa terminou com um erro da API. A sessão fica pronta e a resposta, se houver, é entregue como no `Stop`; um erro conhecido (servidores sobrecarregados, limite de velocidade, ...) aparece em palavras simples.

### <a id="subagentstart--subagentstop"></a>`SubagentStart` / `SubagentStop`

Um subagente começou ou terminou. Cada um tem a própria linha no cartão de atividade, `🤖 <type> · <activity> · <elapsed>`, com as próprias ferramentas recolhidas embaixo, e ao terminar fica fixo como `✅ 🤖 <type> · N ferramentas usadas · <elapsed>`. Um subagente cujo `SubagentStop` nunca chega é descartado quando fica 30 minutos sem dar notícias (`AIPAGER_SUBAGENT_SILENCE`, em segundos; nenhum evento de nenhum tipo com o ID dele). Um agente que trabalha manda eventos de ferramentas a cada poucos segundos, então isso só recolhe um cujo fim se perdeu (um reinício, um datagrama perdido); uma única ferramenta que roda 30 minutos sem nenhum evento com o ID desse agente também o recolhe. `AIPAGER_SUBAGENT_TTL` é aceito como nome antigo da mesma configuração. Chegam com frequência eventos `SubagentStop` sem tipo e com um ID desconhecido, e eles são ignorados.

### <a id="precompact--postcompact"></a>`PreCompact` / `PostCompact`

O Claude está compactando o contexto (`trigger` é `auto` ou `manual`). O cartão de atividade diz `🔄 name · Compactando a conversa` (uma mensagem nova quando não há cartão aberto), para a pausa não parecer uma queda. O `PostCompact` encerra esse estado; a linha `📦 name · Conversa compactada: X% → Y%` vem do `SessionStart` seguinte, ou da linha de status quando ele se perde. A linha de status também dá o aviso `⚠️ name · Contexto em 82% - logo vai ser compactado automaticamente` (com um botão para compactar) quando o contexto chega a 80%, uma vez por conversa: compactar não repete o aviso, e uma conversa nova (depois de `/clear`, por exemplo) recebe de novo.

### <a id="messagedisplay"></a>`MessageDisplay`

O texto do próprio Claude no momento em que chega à tela. A conversa salva só é escrita depois de cada rodada de ferramentas, então esta é a única fonte atual do que o Claude está dizendo, e o que leva os comentários dele para o cartão de atividade. O texto de um subagente (qualquer evento com o `agent_id` dele) nunca é tomado pelo do Claude: nunca chega aos comentários do cartão, ao Mini App nem ao registro da tarefa, e vira a última atividade desse agente no status embaixo de uma resposta. O hook não acrescenta dados da linha de status a este evento: o Claude Code espera por ele enquanto desenha, e ele acontece várias vezes por mensagem.

### <a id="premodelswitch"></a>`PreModelSwitch`

O Claude Code 2.1.251 e posteriores o enviam antes de trocar de modelo. Ele traz `from_model`, `to_model`, `requested_model` e `source` (`command` para um `/model` digitado, ou `picker` ou `sdk`). O Claude Code costuma perguntar “Switch model?” antes de uma troca, porque a próxima resposta precisa ler a conversa inteira de novo. Um hook que responde `permissionDecision: "allow"` deixa a troca seguir sem essa pergunta.

O `aipager-hook` responde `allow` só para uma troca que o próprio aipager digitou, pelo controle de modelo do Mini App ou pelo teclado de modelos. Qualquer outra troca não recebe resposta, então o Claude Code pergunta como sempre. Isso inclui o seu próprio `/model` no terminal, o seletor e uma troca pelo SDK.

O aipager reconhece as próprias trocas por um arquivo de marca. Logo antes de digitar `/model <name>`, o aipager escreve `aipager-modelswitch-<session>.json`:
- ele vai na pasta do socket de controle (normalmente `$XDG_RUNTIME_DIR`);
- é escrito de forma atômica, com permissões 0600;
- tem o nome do modelo e o ID da sessão do Claude;
- vence em 30 segundos.

O hook responde `allow` só quando tudo isto vale:
- a marca é um arquivo seu, e não um link simbólico;
- não venceu;
- é para a mesma sessão do aipager e o mesmo ID de sessão do Claude;
- o modelo dela é igual a `requested_model` ou a `to_model`, sem diferenciar maiúsculas;
- `source` é `command`.

Depois o hook pega a marca com uma renomeação atômica, para ela ser usada exatamente uma vez. A verificação lê um único arquivo local, sem socket e sem espera. O evento não é repassado ao aipager.

O Claude Code 2.1.101 e posteriores ignoram os eventos de hook que não conhecem, então versões mais antigas sem `PreModelSwitch` não são afetadas. Um caso ainda pode mostrar a pergunta: uma troca para o `opusplan` ou saindo dele pode fazer o Claude Code perguntar duas vezes ao hook, e só a primeira encontra a marca, então o Claude Code pergunta “Switch model?” no terminal.

## <a id="the-status-line"></a>A linha de status

Não é um evento de hook: o Claude Code roda o comando `statusLine`, `aipager-statusline` (`aipager.dtach.statusline_notify:main`), sempre que redesenha a linha de status dele. O comando:

- escreve o JSON de status do Claude Code em `/tmp/claude-status-<session>.json` (lido pelo hook para o `sl_tokens`, pelo `aipager status` e pelo aipager);
- manda ao aipager um datagrama `statusline` com o modelo, o % de contexto, o custo, as contagens de tokens e as linhas adicionadas e removidas, que alimentam o cartão de atividade, o `/status`, o Mini App, a verificação de que uma troca de modelo aconteceu, e o aviso de contexto;
- imprime a linha de status do terminal: `[name] Opus 5.5 | 42% ctx | $1.23`.

## <a id="aipagers-own-datagrams"></a>Os datagramas do próprio aipager

Além dos eventos de hook, os ajudantes mandam ao aipager algumas mensagens próprias pelo mesmo socket:

| Mensagem | Quem manda | Quando |
|---|---|---|
| `queue_pickup` | hook | No `UserPromptSubmit`: quais mensagens do Telegram o Claude pegou. |
| `safety_blocked` | hook | No `PreToolUse`, quando a verificação de segurança negou a execução. |
| `permission_reply_timeout` | hook | A espera de um `PermissionRequest` terminou sem nenhum toque. |
| `statusline` | linha de status | A cada redesenho da linha de status. |
| `hook_memory_cap_hit` | hook, linha de status | O processo chegou ao limite de memória. |
| `report_error` | hook, comandos `aipager` | Teve um erro: só o tipo do erro e o lugar dele no código do aipager, nunca o conteúdo do evento, para os [relatórios de problemas](problem-reports.md). |

## <a id="the-permission-flow"></a>O caminho de um pedido de permissão

```
claude              aipager-hook             aipager daemon          Telegram
  |                      |                         |                     |
  | PermissionRequest    |                         |                     |
  |--------------------->| opens a reply socket    |                     |
  |                      |------------------------>| busy card becomes   |
  |                      |                         | the prompt          |
  |                      |                         |-------------------->|
  |                      |                         |   you tap Allow     |
  |                      |                         |<--------------------|
  |                      |   allow (reply socket)  | audit log           |
  |                      |<------------------------|                     |
  |  decision: allow     |                         |                     |
  |<---------------------|                         |                     |
  | tool runs            |                         |                     |
```

Se nenhum toque chegar em 20 segundos, o hook não imprime nada e o Claude Code mostra a caixa de diálogo dele; um toque depois digita a resposta nessa caixa pelo terminal da sessão (`dtach -p`). Nos dois casos o toque fica no registro de auditoria e é publicado embaixo do cartão (`✅ jim · Permitido · @alice · Bash: ls -la /tmp`).

## <a id="see-also"></a>Veja também

- [Arquitetura](architecture.md): onde o receptor de hooks se encaixa.
- [Comandos do bot → pedidos de permissão](commands.md#permission-prompts): o lado que você vê.
- [Modelo de segurança](security.md): o que a verificação de segurança cobre e o que não cobre.
