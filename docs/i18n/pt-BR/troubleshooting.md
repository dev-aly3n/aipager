# Solução de problemas

<!-- i18n-source: docs/troubleshooting.md @ 7e36944 -->

> Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../troubleshooting.md))

Falhas comuns e como resolver. Na dúvida, rode:

```sh
aipager doctor
```

Ele roda todas as verificações abaixo em ordem e imprime a solução recomendada para cada uma que falhar. Código: `aipager/doctor.py`.

## <a id="another-aipager-daemon-already-owns-the-socket"></a>“Another aipager daemon already owns the socket”

Dois aipager não podem dividir o mesmo socket de controle. Este erro quer dizer que um aipager ainda está rodando e respondendo nele (o socket é `$XDG_RUNTIME_DIR/aipager.sock`, ou `/tmp/aipager.sock` quando `$XDG_RUNTIME_DIR` não está definido). Um arquivo de socket que sobrou de um aipager que caiu não é problema: o próximo início o apaga e abre um novo. Primeiro pare o aipager que está rodando:

```sh
aipager service stop          # se ele roda como serviço
pkill -f 'aipager start'      # senão
```

Se a verificação do socket não o enxergar, o `aipager start` ainda assim se recusa com `aipager is already running (pid=N)` e diz qual é o arquivo de trava dele (`~/.local/share/aipager/daemon.lock`): pare esse processo do mesmo jeito.

## <a id="telegram-bot-doesnt-respond"></a>O bot do Telegram não responde

1. `aipager status`: se a linha do aipager disser `not running`, inicie-o.
2. `aipager doctor`: as verificações `token_valid` e `chat_reachable` falam com a API do Telegram de ponta a ponta e mostram o erro exato.
3. Chat errado: rode `aipager config` e escolha **Test bot reachability**. Para corrigir um chat privado errado, adicione o certo com **Add a DM scope** (veja [descobrir IDs](groups.md#finding-ids-auto-detect)) e depois tire o errado em **Edit a scope**.
4. Nunca tocaram em `/start` no bot: abra o bot no Telegram e toque em Start uma vez.

## <a id="busy-cards-got-slower"></a>Os cartões de atividade ficaram mais lentos

O Telegram permite mais ou menos uma mensagem por segundo em cada chat (e 20 por minuto num grupo). Cada mensagem e cada edição contam. O aipager dá a cada chat o próprio orçamento, de cerca de 1 chamada por segundo, com uma pequena folga para várias seguidas, e os cartões de atividade desse chat o **dividem**. O indicador “digitando…” também entra nesse orçamento, como a coisa **menos importante** que o chat envia (veja “A bolha de digitando” abaixo).

Primeiro é reservada a parte da bolha (uma chamada a cada 4,5 segundos enquanto a tarefa mais antiga em andamento no chat tem menos de dez minutos, menos à medida que ela se alonga; veja “A bolha de digitando”) e os cartões dividem o resto. Assim, um único cartão num chat privado é atualizado mais ou menos a cada 4,8 segundos; com duas sessões trabalhando no mesmo chat, cada cartão mais ou menos a cada 9,7 segundos; com três, a cada 14,5. **Um cartão mais lento é o orçamento funcionando, não um bug.** Respostas, réplicas e reações aos botões nunca são atrasadas para abrir espaço para um cartão: elas têm uma parte reservada, e uma edição de cartão que não tem orçamento simplesmente é pulada e tentada de novo no próximo tique, em vez de entrar na fila na frente da sua resposta.

Num **grupo** o limite é de 20 chamadas por minuto, não importa quantas sessões haja, e a bolha sozinha usaria dois terços disso. Os cartões do grupo juntos mantêm uma atualização a cada 10 segundos (11 s para um cartão), e a bolha fica com o resto, então num grupo ela pode sumir por um ou dois segundos de vez em quando.

Para os cartões ficarem mais rápidos: rode menos sessões ao mesmo tempo por chat, ou dê às mais movimentadas um chat próprio (`aipager config`). Você também pode ajustar `STREAM_EDIT_INTERVAL` (padrão `1.2`, o ritmo enquanto o cartão mostra texto) e `BUSY_EDIT_INTERVAL` (padrão `3.0`, quando ele está quieto), como variáveis de ambiente do aipager (veja [configurações do ambiente](commands.md#settings-in-the-environment)). Colocar qualquer uma *abaixo* do mínimo de cada chat não faz mal e não muda nada: o mínimo manda, e é ele que mantém o chat dentro do limite do Telegram, não importa o que você defina.

### <a id="a-long-turns-card-refreshes-less-often"></a>O cartão de uma tarefa longa é atualizado com menos frequência

O ritmo acima vale para os dois primeiros minutos de uma tarefa. Depois disso, **o cartão fica mais lento à medida que a tarefa se alonga**, e a partir de 10 minutos o relógio dele anda de 30 em 30 segundos, no ritmo das atualizações, para nunca parecer travado:

| tempo da tarefa | o cartão é atualizado no máximo a cada | o relógio mostra |
|---|---|---|
| 0–2 min | como acima (uns 4,8 s para um cartão num chat privado) | `0:45` |
| 2–10 min | 10 s | `4:10` |
| 10–60 min | 30 s | `23:00`, `23:30` |
| mais de 60 min | 30 s | `1:23:00`, `1:23:30` |

O relógio é arredondado para baixo no passo de 30 s, e as atualizações ficam pelo menos 30 s afastadas, às vezes um pouco mais. Então o cartão pula um passo de vez em quando: uma atualização aos 23 min 29 s mostra `23:00`, e se a próxima vier aos 24 min 1 s ela mostra `24:00`, então `23:30` não aparece.

Uma linha de ferramenta ou uma frase nova espera a próxima atualização; uma **mudança de estado** (a sessão passando de trabalhando para esperando um agente em segundo plano, e o contrário) aparece na hora, no máximo uma vez a cada 10 segundos. As linhas ao vivo dos agentes e a linha de espera usam os mesmos passos. O cartão concluído mostra a duração da tarefa em segundos de verdade (`23:17`).

É isso que impede uma tarefa de quatro horas de editar uma mensagem milhares de vezes: em 2026-09-23 um cartão fez exatamente isso, e o chat ficou bloqueado por sete horas. Para manter o ritmo rápido durante a tarefa toda, desligue **`/settings` → ⏱ Atualizar cartão em tarefas longas** (para o chat, ou para uma sessão pelo Mini App ou por `👤 Configurações de cada sessão`).

### <a id="the-typing-bubble"></a>A bolha de “digitando…”

A bolha de “digitando…” é enviada por **um só** loop por chat, não importa quantas sessões trabalhem nele (a bolha é do chat inteiro em todos os apps do Telegram), a cada `TYPING_INDICATOR_INTERVAL` segundos (padrão 4,5; o Telegram apaga o estado de digitando depois de 5; `0` desliga a bolha). No orçamento do chat, ela é a coisa menos importante que é enviada:

- **fica mais lenta à medida que a tarefa se alonga**, pelo tempo da tarefa mais antiga em andamento no chat: a cada 4,5 s nos primeiros dez minutos (uma bolha fixa), a cada 15 s depois; o Telegram a mostra por 5 s, então a bolha de uma tarefa mais longa pisca em vez de ficar acesa. A 4,5 s ela sozinha seriam 800 chamadas por hora e o limite por hora (abaixo) a desligaria por longos períodos; desligar **⏱ Atualizar cartão em tarefas longas** a mantém em 4,5 s;
- **aparece desde o começo de cada tarefa**; um cartão novo é atualizado um pouco mais devagar para abrir espaço: a parte da bolha no chat é reservada antes de os cartões dividirem o resto (acima), então as duas juntas nunca passam dos limites do chat;
- nunca toma a parte de que uma edição de cartão precisa: uma bolha que caberia logo depois de uma edição espera meio segundo (`TYPING_RETRY_WAKE`) e tenta de novo, então chega no máximo um ou dois segundos atrasada, nunca falta;
- é a primeira coisa a ser cortada quando o volume por hora do chat sobe (abaixo);
- um 429 na própria bolha bloqueia só a bolha, pelo tempo que o Telegram pediu (os cartões continuam), mas começa o período de aviso de seis horas do chat (o ritmo fica limitado a 0,5 chamadas/s, então os cartões também ficam mais lentos);
- um **bloqueio** na bolha (um `retry_after` acima do limite abaixo) silencia o chat igual a um bloqueio em qualquer outra chamada: nada mais é enviado, e a resposta da tarefa fica guardada e é entregue quando o bloqueio acaba.

Antes ela era de graça (um loop por sessão trabalhando, fora de qualquer orçamento), até que em 2026-09-23 duas sessões num chat privado a mandaram cerca de 5.400 vezes em três horas e o Telegram limitou a própria bolha.

## <a id="the-bot-slowed-down-a-rate-limit-429"></a>O bot ficou lento: um limite de velocidade (429)

Se mesmo assim o Telegram limitar a velocidade de um chat, ele responde a uma chamada com `429` e um `retry_after` de alguns segundos. O aipager para de chamar esse chat exatamente por esse tempo, refaz depois o único envio adiado, e **dobra o intervalo dos cartões desse chat** (até ×8). Cada minuto tranquilo o divide por dois até voltar ao normal.

O que você vê:

- `aipager status` (e a linha `aipager daemon` do `aipager doctor`) mostra **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**.
- Exatamente uma linha no `aipager logs`, sem traceback: `flood: chat … 429 retry_after=5s → cadence ×2`.
- Nada é silenciado e **nenhuma resposta se perde**: ela é adiada, não descartada.
- **O ritmo que o chat tinha conquistado cai pela metade**, e o chat entra num **período de aviso de seis horas** (`FLOOD_WARNING_HOURS`): o ritmo dele não pode passar de 0,5 chamadas/s (`FLOOD_WARNED_CEILING`), e sobe devagar. O `aipager status` mostra: `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the last hour (as of 18s ago), 7/30 in the last minute - warning regime, 5h 52m left`.

Você não precisa fazer nada. Resolve sozinho. A linha do backoff some quando o chat fica um ou dois minutos tranquilo; o ritmo sobe 0,1 chamadas/s a cada período tranquilo (um minuto normalmente, 36 minutos durante o período de aviso) e o teto completo volta quando o período acaba. O `aipager doctor` mantém a linha do aipager verde o tempo todo: um chat que diminui o ritmo é funcionamento normal. (Antes, um 429 era perdoado em uns cinco minutos. O único aviso do Telegram antes do bloqueio de 2026-09-23 veio 3 h 23 min antes, e o chat voltou à velocidade total poucos minutos depois.)

### <a id="the-hourly-budget"></a>O orçamento por hora

Cada chat também tem uma **hora** móvel: no máximo `FLOOD_HOURLY_MAX` (1.200) chamadas em qualquer janela de 60 minutos; bolhas de digitando, edições de cartões e respostas, tudo junto; só as reações não contam. As últimas 120 (`FLOOD_HOURLY_ESSENTIAL_RESERVE`) ficam reservadas para respostas, réplicas e pedidos:

- com 810 chamadas na hora, a bolha de digitando pausa (volta abaixo de 648);
- com 1.080, o chat entra em **modo mínimo** (abaixo) até a hora baixar para 864;
- as respostas nunca são recusadas pela hora; se um dia passarem de 1.200, o log diz isso uma vez por hora.

O `aipager status` mostra as chamadas do chat na última hora em relação ao orçamento dele. O número vem do arquivo de estado, que o aipager reescreve no máximo uma vez por minuto enquanto há chamadas, então ele vem com a idade. Duas sessões movimentadas mandando para um chat privado por horas ficam bem abaixo do orçamento (o incidente de 2026-09-23, repetido: cerca de 900 chamadas na hora mais movimentada, contra umas 3.400 calculadas para o mesmo trabalho sem ele).

### <a id="the-earned-rate-and-why-your-cards-may-be-slower-than-they-were"></a>O ritmo conquistado, e por que seus cartões podem estar mais lentos que antes

Desde a 0.7.13, o ritmo contínuo de envio de cada chat **é aprendido em vez de suposto**. O Telegram não publica o limite que de fato vale para o seu bot (depende do histórico recente da sua conta), então o aipager começa cada chat em 0,5 chamadas/s, soma 0,1 a cada minuto tranquilo até um teto de 1/s, divide por dois num 429 e baixa para 0,05 depois de um bloqueio. Assim, um bot que acabou de ser bloqueado vai muito mais devagar do que um que não foi, e recupera a velocidade nas horas seguintes.

Se o ritmo de um chat cair abaixo de 0,2 chamadas/s (ou acabar a parte do orçamento por hora dele para cartões e bolhas), ele entra em **modo mínimo**: os cartões de atividade param de se animar e mostram uma única linha fixa `⏳ x1 · trabalhando (o cartão não está sendo atualizado por enquanto)`, a bolha de digitando para, e a barra de status fixada mostra `⏸ os cartões não estão sendo atualizados por enquanto (limite por hora do Telegram)` (ou `⏸ os cartões não estão sendo atualizados por enquanto (limite do Telegram)` quando foi o ritmo que o levou até lá) e no resto não muda até isso passar. **Respostas, réplicas e pedidos de permissão continuam chegando**: o objetivo é esse. O cartão é cerca de 95% do que este bot envia e a resposta cerca de 5%, então sob pressão ele sacrifica a aparência, não o trabalho. O `aipager doctor` informa o modo mínimo como alerta para um cartão pausado nunca ser confundido com um quebrado; ele passa sozinho quando o ritmo se recupera.

## <a id="the-bot-went-quiet-flood-control"></a>O bot ficou em silêncio: um bloqueio do Telegram (flood control)

Um `retry_after` acima de `TELEGRAM_MAX_RETRY_AFTER` (padrão 90 s) não é um limite de velocidade: é um **bloqueio**, e pode durar horas. Pelo chat parece que o bot morreu: as mensagens continuam chegando ao Claude, as sessões continuam trabalhando, mas nenhuma resposta volta.

O que você vê:

- `aipager status` (e a linha `aipager daemon` do `aipager doctor`) mostra **`Telegram flood-muted until HH:MM (chat …)`**.
- Uma linha no `aipager logs`: `Telegram flood control — chat … muted for Ns (until HH:MM) …`, e depois silêncio para esse chat. Mais tarde, uma linha `flood mute on chat … lifted`.
- O `aipager status` também mostra o estado completo do chat: `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the last hour (as of 12s ago) - MINIMAL MODE, card updates paused - flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the last 7 days)`.

O aipager silencia o chat exatamente pelo tempo que o Telegram pediu e **não faz nenhuma chamada de nenhum tipo** para ele: respostas, edições de cartões, anexos, reações, indicadores de digitando, avisos curtos depois de tocar num botão e as réplicas que seus comandos produziriam. Cada uma seria uma infração nova que aumenta o bloqueio; um pedido no meio de um bloqueio ativo foi o que transformou um bloqueio de 21 minutos num de 9,5 horas em 2026-09-15. Então um comando digitado durante o silêncio não tem resposta nenhuma, e **um toque num botão também não recebe confirmação**: até a 0.7.12 o aviso curto ainda saía, com a ideia de que o Telegram o conta à parte. Para o ritmo ele conta à parte; para os bloqueios, não. Os outros chats não são afetados.

**Mandar mensagem para o bot durante um bloqueio é seguro, e nada se perde.** Nada do que você faz no chat faz o bot chamar o Telegram enquanto o chat está silenciado, então não tem como aumentar o bloqueio:

- Uma mensagem que você manda (texto, áudio, um arquivo, um template) ainda chega ao Claude e a sessão trabalha nela. O cartão de atividade dela fica guardado, não é descartado: se a tarefa ainda estiver em andamento quando o bloqueio acabar, o cartão aparece nessa hora. Um bloqueio deixa o chat em modo mínimo por um tempo depois, então o cartão pode demorar alguns minutos a mais que o próprio bloqueio. Se a tarefa terminou durante o bloqueio, você recebe a resposta e nenhum cartão.
- **🔄 Tentar de novo** não faz nada durante um bloqueio. A mensagem não é reenviada e o botão fica, para você tocar de novo depois que o bloqueio acabar. Até a 0.7.14, inclusive, a mensagem chegava ao Claude enquanto a mensagem de erro e o botão dela ficavam, então um segundo toque a enviava de novo.
- O teclado principal também fica guardado, incluindo o que um reinício envia (até a 0.7.14 ele se perdia até outra coisa atualizá-lo). Ele é enviado depois que o bloqueio acaba.

Depois que o bloqueio acaba, as coisas chegam nesta ordem: as respostas guardadas, depois os cartões das tarefas ainda em andamento, depois o teclado. Um cartão que precisa esperar o fim do modo mínimo chega depois do teclado.

**As respostas não se perdem.** Uma resposta produzida enquanto o chat está silenciado fica guardada e é entregue em um ou dois segundos depois que o bloqueio acaba, com a primeira linha `⏳ chegou atrasada: ficou 42 min presa por um limite do Telegram`. Você não precisa perguntar de novo, e se perguntou, as duas respostas chegam, a mais antiga primeiro: um bloqueio de horas cobre várias tarefas e cada resposta é sua. (As respostas guardadas ficam na memória: se você reiniciar o aipager enquanto alguma estiver esperando, ela se perde; o log de desligamento diz quantas. Um chat guarda no máximo 20, e nada com mais de 24 horas; o que for descartado por qualquer desses motivos vira um alerta no log dizendo o que era.)

**Um bloqueio deixa o chat mais lento depois, não mais rápido.** No momento em que um bloqueio começa, o ritmo aprendido do chat cai para o mínimo e o bloqueio é contado; as horas em silêncio não recuperam nada, a subida recomeça a partir do momento em que o bloqueio acaba, e **por sete dias** (`FLOOD_BAN_MEMORY_DAYS`) o teto de ritmo do chat *e* o orçamento por hora dele são divididos por um mais o número de bloqueios dessa semana: um bloqueio corta os dois pela metade, dois para um terço. O `aipager status` mostra tudo isso. (Antes essa memória durava 24 horas e só cortava o teto pela metade; um chat bloqueado em 2026-09-19 estava de volta à velocidade total, e foi bloqueado por sete horas, em 2026-09-23.) Cinco bloqueios ou mais numa semana deixam o chat em modo mínimo até eles caducarem: as respostas continuam chegando, os cartões continuam pausados.

O que NÃO fazer:

- Não reinicie o aipager para “consertar”. O silêncio acaba sozinho no horário mostrado. Desde a 0.7.13 um reinício não esquece mais o bloqueio (o prazo, o ritmo conquistado do chat e o histórico de bloqueios dele são salvos em `~/.claude/aipager-flood-state.json` e restaurados ao iniciar, assim como as chamadas da última hora do chat e o período de aviso dele), então reiniciar também não aumenta o bloqueio. Mas descarta as respostas ainda guardadas, e não resolve nada.
- Não baixe `TELEGRAM_MAX_RETRY_AFTER` abaixo dos 90 s padrão na esperança de tentar de novo antes: tudo o que passa desse limite é um bloqueio, não um limite de velocidade.

Para evitar: rode menos sessões ao mesmo tempo por chat, ou dê às mais movimentadas um chat próprio (`aipager config`).

## <a id="a-session-dropped-off-the-pinned-status-bar-it-is-gone"></a>Uma sessão sumiu da barra de status fixada (está GONE)

O processo do dtach dessa sessão terminou (reinício do computador, `pkill claude`, alguém digitou `exit` na sessão conectada ao dtach). Crie-a de novo; a pasta `~/.claude/projects/...` dela ainda tem a conversa:

O jeito mais fácil é `/resume <label>` no Telegram (ou `/resume` sozinho, para escolher de uma lista): ele inicia a sessão de novo e retoma a conversa. Pela linha de comando:

```sh
aipager resume <label>             # o mesmo, pelo terminal
aipager session <label>            # ou uma sessão nova, sem histórico
aipager session <label> --continue # dtach novo, a última conversa da pasta
```

## <a id="a-permission-prompt-with-no-news-for-5-minutes"></a>Um pedido de permissão sem notícias por 5 minutos

Se uma sessão espera um pedido de permissão ou uma pergunta e o Claude Code não dá notícias por 5 minutos, o aipager lê a conversa salva da sessão. Só se ela mostrar que o pedido acabou (respondido no terminal, e a notícia se perdeu) o aipager para de esperá-lo e volta a contar a sessão como trabalhando. Senão o pedido continua aberto pelo tempo que for: o cartão e a barra fixada continuam dizendo que a sessão precisa da sua resposta, nenhum aviso de "ainda trabalhando" é enviado, e suas mensagens ficam retidas até você responder (veja [texto](commands.md#text)). Um Claude Code que fecha com o pedido aberto encerra a sessão. Se a caixa de diálogo ainda estiver aberta, os botões do pedido ainda a respondem (veja [respostas](commands.md#idle-responses)).

Mudar a espera:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # em segundos
```

## <a id="voice-extra-wont-install-via-telegram"></a>O recurso de voz não instala pelo Telegram

O botão `[📦 Instalar recurso de voz]` roda o instalador e edita a mensagem a cada poucos segundos enquanto ele trabalha (`📦 Instalando o recurso de voz - ainda em andamento… (25 s)`). Se a instalação falhar, a mensagem mostra o código de saída e os últimos 500 caracteres da saída do instalador. Causas comuns:

- Rede: o pip não chega ao PyPI. Confira a conexão a partir do computador em que o aipager roda.
- Disco: `~/.cache/huggingface/` precisa de uns 74 MB para o modelo e o cache de wheels do pip de mais uns 250 MB. Libere espaço antes.
- Permission denied: um venv somente leitura. Mude para `uv tool install`, que sempre usa um venv do seu usuário.

Se o bot perdeu a conexão no meio da instalação, a instalação em si geralmente terminou no computador: reinicie o aipager e tente um áudio de novo.

## <a id="open-app-shows-open-this-page-from-the-telegram-app-to-sign-in"></a>Abrir o app mostra “Abra esta página pelo app do Telegram para entrar.”

A página carregou, mas o script do Mini App do Telegram (o que produz o `initData`) não, então a página não tem com o que entrar e não faz nenhuma chamada à API. A mensagem põe a culpa em você; a causa é de rede.

As versões recentes do aipager baixam esse script sozinhas e o servem pelo mesmo endereço da página (`/telegram-web-app.js`), então o celular só precisa chegar ao lugar que entregou a página, não também ao `telegram.org`. Se você vê isso numa versão anterior, atualize.

Depois, nesta ordem:

1. Olhe o código-fonte da página. Se a tag `<script>` dela aponta para `https://telegram.org/js/telegram-web-app.js`, o aipager ainda não conseguiu baixar o script e a página está carregando pelo Telegram, que é justamente o caso que falha num celular que não chega ao `telegram.org`. O aipager baixa o script quando o servidor do Mini App inicia e tenta de novo no máximo uma vez por dia, então reiniciar o aipager tenta de novo na hora; `journalctl --user -u aipager | grep "webapp sdk"` diz o que deu errado (normalmente um `telegram.org` bloqueado ou filtrado).
2. Se a tag aponta para `/telegram-web-app.js`, confira se o aipager o serve: `curl -sI http://127.0.0.1:8765/telegram-web-app.js` no computador do aipager deve responder `200` com `Content-Type: application/javascript` (ajuste a porta se você a mudou). A cópia fica em `~/.local/share/aipager/webapp-sdk/`.
3. Você abriu o endereço num navegador comum em vez de pelo botão `/app` ou pelo botão de menu do bot: aí essa é a mensagem esperada. O SDK só produz o `initData` dentro do Telegram.
4. A página ficou aberta por mais de cinco minutos: o `initData` vence e a página pede para ser aberta de novo. Toque em `/app` de novo.
5. Você tocou em `📱 App` num teclado de uma versão antiga do aipager. O Telegram não dá `initData` a um Mini App aberto por um botão do teclado, por isso o teclado do aipager não tem mais esse botão. Mande `/start` para receber o teclado atual e abra o app pelo botão de menu ou pelo `/app`.

## <a id="pyexpat-_xml_setalloctrackeractivationthreshold-on-brew-install"></a>`pyexpat _XML_SetAllocTrackerActivationThreshold` ao instalar pelo brew

O pacote `python@3.12` do Homebrew foi compilado com uma libexpat mais nova que a do seu sistema; quase sempre porque **o Xcode e o Command Line Tools estão desatualizados** no macOS Tahoe (26.x). A própria saída do brew costuma dizer isso. Duas soluções:

- **Use `uv tool install aipager`** em vez disso. O uv traz o próprio python e foge do problema por completo. Este é o caminho recomendado no macOS; veja o [README](README.md#install).
- **Atualize o Xcode e o Command Line Tools**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  Ou abra o Xcode na App Store e atualize para a última versão.

## <a id="modulenotfounderror-aipager"></a>“ModuleNotFoundError: aipager”

O executável do aipager aponta para um Python que não tem mais o aipager instalado (muitas vezes depois de apagar um venv). Reinstale:

```sh
uv tool install --reinstall aipager
# ou com o instalador que você usou no começo: pipx, brew, pip
```

## <a id="the-session-list-was-lost-or-damaged"></a>A lista de sessões se perdeu ou foi danificada

O aipager guarda as sessões em `~/.claude/aipager-sessions.json` e reescreve o arquivo de forma atômica (um arquivo novo e depois uma renomeação), então uma escrita interrompida deixa o arquivo anterior inteiro. Se mesmo assim o arquivo não existir, não puder ser lido ou não for um JSON válido, o aipager escreve no log que está começando do zero (como alerta quando o arquivo estava lá mas não pôde ser lido) e inicia com a lista vazia; ele não para. Não há cópia de segurança desse arquivo. Nada importante se perde:

- as sessões em andamento são encontradas de novo no primeiro tique do vigia, pelos sockets do dtach (`/tmp/claude-dtach-*.sock`);
- todas as conversas continuam na pasta `~/.claude/projects/...` do próprio Claude Code, então `aipager session <name> --continue` na pasta dessa sessão a retoma (`--resume` deixa você escolher uma);
- o que se perde é a lista de sessões encerradas (`/resume`), os cartões que estavam abertos, e o resto do que o aipager lembrava de cada sessão.

Se o aipager continuar falhando ao iniciar, `aipager logs` (ou `journalctl --user -u aipager`) mostra por quê; tire o arquivo do caminho (`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`) para descartá-lo.

## <a id="a-session-launch-fails"></a>O início de uma sessão falha

Um início que o dtach recusa mostra o motivo numa linha no chat (o socket já existe, o shell não pode ser executado, não há pseudoterminal, o nome é longo demais, permissão negada, falta a pasta); a mensagem do próprio dtach, com os caminhos, está no `aipager logs`.

## <a id="aipager-doctor-check-list"></a>Lista de verificações do `aipager doctor`

A ordem importa: cada verificação posterior supõe que as anteriores passaram. Uma verificação que cai num ambiente inesperado aparece como uma única linha ⚠ com o nome da verificação e o erro; as outras verificações continuam rodando.

| Verificação (chave do `--json`) | O que confere | Solução |
|---|---|---|
| `config_parses` | `aipager.yaml` e `policy.yaml` podem ser lidos; roda primeiro, porque todas as outras leem a configuração | o erro diz o arquivo e a linha |
| `config` | `~/.config/aipager/aipager.yaml` existe e tem um token e um chat | `aipager config` |
| `token_valid` | O token funciona com o `getMe` do Telegram | rode `aipager config` de novo |
| `chat_reachable` | O Telegram conhece o chat configurado (`getChat`; nada é enviado) | abra o bot e toque em Start |
| `team` | Os chats e os membros (ou um `team.yaml` antigo): diz se é uso pessoal ou em equipe, e aponta um `team.yaml` antigo quebrado ou que não bate | `aipager config` |
| `role_shell_access` | Alerta uma vez para cada função que um membro tem e que pode rodar Bash (as regras de segurança dela são de melhor esforço, não um limite; veja [uso em equipe](groups.md#roles)) | não dê Bash a essa função no `policy.yaml`, ou aceite |
| `peer_messages` | Mensagens entre sessões do Claude: onde os recibos delas são guardados e se essa pasta é privada, se este sistema consegue conferir a hora de início do remetente, e um alerta para cada função a que uma política deixa usar SendMessage (veja [Modelo de segurança](security.md#messages-between-claude-sessions)) | apague uma pasta de recibos compartilhada (o aipager a recria), ou não dê SendMessage a essa função |
| `claude` | Encontra o executável `claude` do mesmo jeito que cada início (`claude_path` no `aipager.yaml` ou `$AIPAGER_CLAUDE_BIN` mandam sem discussão; senão, a versão mais nova entre `~/.local/bin`, o PATH e o Homebrew), e lista todas as OUTRAS instalações diferentes que encontrar | instale o Claude Code, ou defina `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `claude_auth` | Roda `claude auth status` no mesmo ambiente que uma sessão de verdade recebe. **Nunca dá FAIL**: “sem login” e “a própria verificação falhou” são informados separadamente, e nenhum dos dois impede uma sessão de iniciar | `claude auth login`, ou defina uma chave de API / `CLAUDE_CODE_OAUTH_TOKEN` |
| `dtach` | O executável `dtach` foi encontrado (o `dtach-bin` do próprio aipager, ou um no PATH) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` e `aipager-statusline` estão no PATH | `uv tool install --reinstall aipager` |
| `settings_json` | `~/.claude/settings.json` tem os hooks do aipager ligados | `aipager config` |
| `daemon` | O aipager está rodando e o socket dele responde; também mostra o estado dos limites de velocidade do Telegram (veja [acima](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | Opcional: o arquivo do serviço existe | `aipager service install` |
| `service_unit_path` | Só Linux: o `Environment=PATH=` do serviço *instalado* tem mesmo a pasta do executável do claude encontrado; é lido como texto, porque o doctor não consegue ver o PATH do próprio systemd a partir do seu shell interativo | `aipager service install --yes` |
| `miniapp` | Se o Mini App consegue iniciar quando está ligado. **Nunca dá FAIL**: o Mini App é opcional | a solução que ele indicar |

O `aipager doctor --json` imprime as mesmas verificações, nesta ordem, com estas chaves (veja [comandos](commands.md#aipager-doctor---json)).

Rode `aipager doctor --fix` para procurar de forma interativa uma credencial do Claude e copiá-la para o `daemon.env` (também quando o arquivo existe mas não tem nenhuma), ou para fixar o `claude_path` quando há mais de uma instalação. Ele sempre pergunta antes de fazer qualquer coisa. Para um serviço cujo PATH não tem a pasta do claude, rode `aipager service install --yes`.

## <a id="the-daemon-cant-find-claude-or-picks-the-wrong-install"></a>O aipager não encontra o `claude`, ou escolhe a instalação errada

Antes, seis lugares procuravam o `claude` por conta própria e podiam não concordar entre si. Agora todos passam por um único buscador (`aipager/claude_resolve.py`). `claude_path` no `aipager.yaml`, e depois `$AIPAGER_CLAUDE_BIN`, mandam sem discussão quando funcionam. Senão, cada `claude` encontrado em `~/.local/bin`, no `$PATH` e nas pastas do Homebrew é conferido, e ganha a versão mais nova (então mudar a ordem do `$PATH` não muda a escolha quando a outra instalação é mais nova). Rode `aipager doctor` para ver exatamente qual ele escolheu e o que mais encontrou; se a errada ganhar, fixe a certa:

```sh
aipager doctor --fix        # escolher de forma interativa entre as instalações encontradas
# ou à mão:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## <a id="sessions-cant-authenticate-under-the-systemd-service-but-claude-works-fine-in-a-terminal"></a>As sessões não conseguem fazer login sob o serviço do systemd, mas o `claude` funciona bem num terminal

Os serviços `systemctl --user` nunca leem `~/.bashrc` nem `~/.profile`, então uma linha `export CLAUDE_CODE_OAUTH_TOKEN=…` neles nunca chega ao aipager. Em vez disso, o serviço usa o `LoadCredential=` do systemd e lê `~/.config/aipager/daemon.env`: um arquivo simples `KEY=VALUE`, com permissões 0600, criado sozinho na primeira vez que você roda `aipager service install` (copiado de um `config.env` antigo se ele tinha um token, senão procurado uma vez no seu shell de login, senão deixado vazio com um alerta). Se continuar vazio, o `aipager doctor --fix` procura de novo nos mesmos lugares quando você diz sim e adiciona a linha que encontrar, mantendo as outras linhas do arquivo; ele nunca imprime o token. Ou adicione a linha você mesmo:

```sh
cat ~/.config/aipager/daemon.env          # veja o que tem (ou não tem)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

Veja [Modelo de segurança → a credencial do Claude](security.md#the-claude-credential-what-actually-protects-it) para saber do que este arquivo protege e do que não protege.

## <a id="the-daemons-control-socket-moved"></a>O socket de controle do aipager mudou de lugar

O `aipager doctor` e o `aipager status` antes sempre olhavam `/tmp/aipager.sock`. Sob o serviço do systemd, o socket de controle agora fica em `$XDG_RUNTIME_DIR/aipager.sock` (e só volta para `/tmp` quando `$XDG_RUNTIME_DIR` não está definido: contêineres, WSL1, distribuições mínimas). Só este socket mudou; os sockets do dtach de cada sessão (`/tmp/claude-dtach-*.sock`) não mudaram. Se precisar de um lugar específico, mude com `AIPAGER_SOCKET_PATH`.

## <a id="update-says-the-restart-would-kill-sessions-killmode"></a>O `/update` diz que o reinício encerraria as sessões (KillMode)

Os serviços escritos antes desta versão não têm a linha `KillMode=`, então o systemd usa `control-group`: parar ou reiniciar o `aipager.service` encerra todas as sessões do Claude que o aipager iniciou. Por isso o `/update` instala a versão nova mas não reinicia o aipager, e lista as sessões que um reinício encerraria. Resolva uma vez:

```
aipager service install
```

Ele reescreve o serviço com `KillMode=process`, recarrega o systemd e reinicia o aipager, e esse reinício já roda com a configuração nova. Confira com `systemctl --user show -p KillMode aipager.service` (`KillMode=process`). Com isso, o systemd escreve no log linhas “left-over process” para as suas sessões a cada reinício; é o esperado.

## <a id="aipager-update-cant-find-uv--pipx"></a>O `aipager update` não encontra o uv ou o pipx

O `aipager update` procura o instalador pelo caminho completo no seu `PATH` mais `~/.local/bin`, `~/.cargo/bin`, as pastas do Homebrew, `/usr/bin` e `/bin`. Se mesmo assim ele disser que não encontrou o `pipx`, o instalador que criou este aipager não existe mais ou está em outro lugar; reinstale com a ferramenta que você tiver (`pipx install --force aipager`, `uv tool install --reinstall aipager`).

## <a id="update-refused-editable--not-your-install"></a>Atualização recusada: editable ou uma instalação que não é sua

O `/update` e o `aipager update` só atualizam uma instalação que é do seu usuário e em que ele pode escrever, pelo instalador que a criou. Eles se recusam, e dizem o motivo, para:

- um checkout editable (de desenvolvimento): atualize com `git pull`;
- uma instalação do Nix, do Snap, do Docker ou de um pacote do sistema operacional: use a atualização desse sistema;
- uma instalação de outro usuário (por exemplo um venv do root em `/opt`): atualize com esse usuário;
- uma instalação feita a partir de uma pasta local (`pipx install /path/to/aipager`): `can't update from here: it was installed from a local folder; update it from that folder`. Traga a versão nova para essa pasta e reinstale a partir dela, ou mude para o PyPI com `pipx install --force aipager`;
- uma instalação cujo instalador não dá para saber (`could not tell which installer owns this aipager`): reinstale com `uv tool install aipager` ou `pipx install aipager`.

## <a id="update-failed-unexpected-argument---refresh"></a>A atualização falhou: unexpected argument '--refresh'

O aipager 1.0.0 e anteriores rodavam `uv tool upgrade aipager --refresh`, que nenhuma versão do uv aceita, então toda atualização de uma instalação pelo uv parava com `error: unexpected argument '--refresh' found` e não reiniciava nada. As versões mais novas rodam `uv tool upgrade aipager`. Para passar disso uma vez, atualize por um terminal nesse computador (o `aipager update` tem o mesmo problema nessas versões):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Sem o serviço (ou no macOS), reinicie o aipager do jeito que você o iniciou.

## <a id="claude-code-update-installed-with-npm-into-a-folder-you-cant-write-to"></a>Atualização do Claude Code: instalado com npm numa pasta onde você não pode escrever

Um Claude Code instalado com `sudo npm install -g` fica numa pasta que só o root pode mudar (como `/usr/lib/node_modules`), então `claude update` rodado com o seu usuário falha com `global folder isn't writable`. O `/update` e o Mini App mostram a versão mais nova com `não pode ser atualizado daqui: installed with npm into a folder your user can't write to` e sem botão para ela. Rode `claude install` uma vez com o usuário que roda o aipager: ele passa para o instalador próprio do Claude Code na sua pasta pessoal, que atualiza sem sudo. Depois reinicie o aipager (`systemctl --user restart aipager.service`, ou do jeito que você o iniciou): um aipager rodando fica com o Claude Code que encontrou ao iniciar, e depois do reinício pega o mais novo que encontrar. Se o `claude_path` no `aipager.yaml` (o `aipager doctor --fix` pode defini-lo) ou o `AIPAGER_CLAUDE_BIN` apontar para o do npm, aponte primeiro para `~/.local/bin/claude` ou tire, porque esses dois sempre mandam.

## <a id="updated-but-the-version-didnt-change"></a>Atualizou, mas a versão não mudou

Uma instalação do pipx feita a partir de uma URL do git se atualiza por essa fonte do git, não pelo PyPI. Quando a fonte não tem nada mais novo, o `/update` diz `already at A - this pipx install upgrades from its git source, which has nothing newer`. Publique ou escolha a versão nova lá, ou mude para o PyPI com `pipx install --force aipager`.

## <a id="the-daemon-didnt-come-back-after-an-update"></a>O aipager não voltou depois de uma atualização

A atualização confere se a versão nova é importada antes de reiniciar qualquer coisa, mas se mesmo assim o aipager novo não iniciar, o systemd tenta de novo a cada 5 s. Veja por quê:

```
journalctl --user -u aipager.service -n 50
```

Reinstale a versão anterior com o mesmo instalador e depois reinicie:

```
pipx install --force aipager==<previous>        # ou:
uv tool install --force aipager==<previous>     # ou:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

Uma instalação do pipx a partir de um caminho local: `pipx install --force /path/to/aipager` no commit anterior. Se um anúncio antigo ficou pendente, apague com `rm -f ~/.local/share/aipager/update-restart.json` (de qualquer jeito ele é ignorado depois de 24 h); `~/.local/share/aipager/update.lock` é só um arquivo de trava e pode ser apagado sem problema quando não há nenhuma atualização em andamento.

## <a id="an-update-was-interrupted-when-aipager-shut-down"></a>“An update was interrupted when aipager shut down”

O aipager parou (ou reiniciou) enquanto o `/update` estava instalando, e o instalador parou junto, então a instalação pode ter ficado pela metade. Se o aipager ou o Claude Code não funcionarem direito, conserte: para o aipager, rode o comando de reinstalação da mensagem (por exemplo `pipx install --force aipager`) e depois `systemctl --user restart aipager.service`; para o Claude Code, rode `claude update` de novo.

## <a id="a-chat-is-in-the-wrong-language"></a>Um chat está no idioma errado

O idioma do aipager vem do que foi escolhido para o chat, não das mensagens dele. Abra `/language` nesse chat e escolha o idioma que você quer: essa escolha manda em tudo. Num chat privado, **Automático (seu idioma do Telegram)** acompanha o idioma do seu app do Telegram (Configurações → Idioma no Telegram); um idioma que o aipager não tem aparece em inglês. Um chat privado que já existia antes de existirem idiomas continua em inglês até você escolher um. Um grupo fica em inglês até um admin escolher o idioma dele. O idioma só muda os textos do aipager: o Claude responde no idioma em que você escreve, não importa o que o aipager mostre. Veja [Idiomas](commands.md#languages).

## <a id="the-bot-description-is-not-the-one-i-wrote"></a>A descrição do bot não é a que eu escrevi

O aipager define a descrição e a descrição curta do bot em cada idioma que fala, para as pessoas cujo app do Telegram está nesse idioma, e o Telegram mostra esse texto a elas em vez do que você escreveu no BotFather. O aipager só faz isso enquanto você não tiver uma descrição própria, então uma descrição que você escrever antes de instalar esta versão chega a todo mundo. Se você a escreveu depois que o aipager definiu a dele, as pessoas nesses idiomas continuam vendo a do aipager. Para elas verem a sua, apague o texto do aipager para cada idioma pela Bot API (`setMyDescription` e `setMyShortDescription` com um texto vazio e esse `language_code`); o aipager não volta a defini-lo enquanto o seu estiver lá.

## <a id="aipager-says-languagesjson-could-not-be-used"></a>O aipager diz que não conseguiu usar o languages.json

O aipager guarda o que aprendeu sobre os idiomas (quem estava aqui antes de existirem idiomas, o último idioma do Telegram que viu de cada pessoa, e se a oferta única e a linha de primeiro contato já foram enviadas) em `~/.local/state/aipager/languages.json`. Se esse arquivo não puder ser lido, o aipager o tira do caminho como `languages.json.bad-<time>` na mesma pasta, escreve um alerta no log (de novo a cada início até você decidir) e deixa todos os chats em inglês e sem oferta de idioma até você decidir: coloque o arquivo antigo de volta se conseguir consertá-lo, ou apague `languages.json` e reinicie, e o aipager volta a contar todas as pessoas que conhece como antigas (em inglês até escolherem). Um idioma que alguém escolheu não fica nesse arquivo e nunca se perde.

## <a id="right-to-left-text-looks-wrong"></a>O texto da direita para a esquerda aparece errado

Em persa e árabe, o aipager coloca marcas invisíveis de direção no próprio texto para cada linha ser lida da direita para a esquerda, e mantém os nomes das sessões, os comandos e o código da esquerda para a direita. Se uma linha ainda aparecer bagunçada, quase sempre é um app do Telegram que desenha de outro jeito o texto com direções misturadas: confira em outro app (celular contra Desktop ou Web) antes de relatar. As respostas do próprio Claude aparecem do jeito que o Claude escreveu, sem marcas acrescentadas.

## <a id="a-translated-keyboard-word-does-nothing-in-another-chat"></a>Uma palavra traduzida do teclado não faz nada em outro chat

O teclado de cada chat está no idioma desse chat. As palavras do teclado principal (status, stop, new, kill e as outras) são entendidas em todos os chats, em todos os idiomas, e as palavras em inglês sempre funcionam. A palavra de um botão de template ou de comando é diferente: digitada num chat com outro idioma, é uma mensagem para o Claude, não um botão, então um “Clear” em russo nunca roda `/clear` num chat em persa ou em inglês. Use o próprio botão, ou o comando em inglês (`/clear`). Se um teclado parecer desatualizado depois que o idioma do chat mudou, mande `/start` para receber o atual. Uma palavra que também é o nome de uma das suas sessões fala com essa sessão. O `/mode` sempre lê `ask`, `auto` e `full` como o modo, e uma palavra de modo traduzida como uma sessão só quando uma sessão em andamento tem exatamente esse nome.

## <a id="still-stuck"></a>Ainda travado?

Mande um relatório de problemas: toque em **Relatar um problema** no `/help` do seu chat privado com o bot, ou rode `aipager report` num terminal. Você vê o relatório inteiro antes de qualquer coisa ser enviada, e ele não tem chats, mensagens, nomes, caminhos nem tokens (veja [Relatórios de problemas](problem-reports.md)). Depois do envio, você recebe uma referência como `ap1-7c41d09e2b3a`.

Depois, se quiser conversar sobre isso, abra uma issue em [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) e inclua essa referência. Por favor, não cole a saída do `aipager doctor` nem do `aipager logs` numa issue: elas têm o seu ID de chat, os caminhos da sua pasta pessoal, os nomes das sessões e o começo das suas mensagens. Se for preciso uma linha do log, quem mantém o aipager vai pedir essa linha, e você pode tirar dela antes qualquer coisa privada.

## <a id="see-also"></a>Veja também

- [Arquitetura](architecture.md): o que cada parte faz.
- [Modelo de segurança](security.md): no que o aipager mexe e no que não mexe.
