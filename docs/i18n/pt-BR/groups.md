# Uso em equipe e grupos

<!-- i18n-source: docs/groups.md @ daaa753 -->

> Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../groups.md))

Por padrão o aipager é um bot privado: você e o bot num chat privado, mais ninguém. O **uso em equipe** o abre para mais pessoas: um grupo do Telegram em que várias pessoas que programam controlam sessões, os chats privados de outras pessoas com o bot, ou as duas coisas. Cada chat tem os próprios membros, e cada membro tem uma função que decide o que ele pode fazer ali: mandar mensagens, responder às perguntas e aos pedidos de permissão do Claude, gerenciar sessões, ou só olhar.

O uso em equipe é opcional. Uma instalação só com o seu chat privado funciona como antes.

## <a id="decide-carefully"></a>Decida com cuidado

Adicionar um usuário do Telegram a um chat dá a ele **permissão para rodar código no computador em que o aipager roda**. Dependendo da função, ele pode:

- mandar mensagens que o Claude transforma em comandos de shell, edições de arquivos e pedidos de rede;
- responder aos pedidos de permissão (Permitir / Negar). O seu `~/.claude/settings.json` continua decidindo sobre quais ferramentas o Claude pergunta, mas qualquer pessoa cuja função possa aprovar pode tocar em Permitir;
- iniciar, parar, encerrar e trocar sessões.

Trate a lista de membros como você trata o acesso SSH ao computador. O registro de auditoria ([`~/.claude/aipager-audit.jsonl`](security.md#audit-log)) guarda quem fez o quê, mas só depois que aconteceu.

## <a id="setup"></a>Configuração

Tudo é configurado com o `aipager config`. Você nunca precisa editar um arquivo à mão.

**Primeira vez** (ainda não há configuração). O assistente pergunta, em quatro passos:

1. o token do bot;
2. o seu próprio chat privado com o bot (detectado sozinho, ou colado);
3. se a sua conta é a `owner` (o padrão; se disser não, você vira `admin`, o que mantém o piso de segurança ligado para você);
4. confere as dependências e instala os hooks do Claude Code.

Ele escreve o seu chat privado como o primeiro chat. Não pergunta um modo padrão: o resumo dele diz “New sessions start in Auto mode; owners and admins can choose Full access in Telegram under /settings → New sessions.” (as sessões novas começam no Modo Automático; donos e admins podem escolher o Acesso total no Telegram em /settings → Novas sessões). Depois pergunta “Connected. Add a team group or other people now?” com Add a group, Add a person (DM) e I'm done.

**As vezes seguintes** abrem o menu de edição. Ele mostra os chats que você tem e oferece:

- **Add a group scope**: um grupo novo, com os membros dele (abaixo).
- **Add a DM scope**: o chat privado de outra pessoa com o bot. Um chat privado tem um membro; você escolhe a função dele (padrão `user`; escolha `owner` só para uma instalação de uma pessoa só, como o contêiner próprio de um amigo). Para uma função sem `can_manage` (como `user`) ele depois pergunta a [pasta raiz](#root-folders) dele, com o valor padrão já preenchido (`~/aipager-users/<label>`). Para uma pessoa que já tem um chat privado configurado, o assistente diz isso e não muda nada: use Edit a member para mudar a função dela.
- **Edit a scope**: para um grupo, **Add a member**; para qualquer chat, Rename, Edit scope deny_tools, **Set the members' root folder** (veja [Pastas raiz](#root-folders)) e Remove this scope (o último chat não pode ser tirado). Encerre as sessões de um chat antes de tirá-lo: as sessões dele em andamento continuam publicando ali, e nenhum chat consegue mais chegar a elas pelo Telegram (pelo computador, `aipager session ls` e `aipager session kill <name>` ainda conseguem).
- **Edit a member**: Set role, Edit member deny_tools, Remove member (não num chat privado, nem o último membro de um grupo). Para um dono ou admin (ou uma função sua com `bypass_role_denies`), Edit member deny_tools só diz que as ferramentas bloqueadas não valem para essa função.
- Test bot reachability, View policy, Re-install Claude Code hooks e Refresh bot token.

### <a id="adding-a-group"></a>Adicionar um grupo

1. Adicione o bot ao grupo e escolha Add a group scope (ou Add a group na primeira vez).
2. **O ID do grupo.** Escolha a detecção automática e mande `/start` no grupo, ou cole o ID (um número negativo). O assistente manda uma mensagem de teste e pergunta se ela chegou. Um grupo que já está configurado para aqui com “This group is already set up. Use Edit a scope → Add a member.” e nada muda.
3. **Um rótulo** para o grupo (aparece no assistente e no Mini App).
4. **Você primeiro.** Quando você é o dono do seu próprio chat privado, o assistente pergunta se adiciona você como primeiro membro do grupo com a função `owner` (padrão sim; você pode pular). Sem um dono ou admin no grupo, ninguém ali poderia usar o Acesso total, `/settings` nem `/update`.
5. **Os outros membros**, um por um: como encontrá-los (detecção automática, ou colar um ID ou um `@handle`), um rótulo (aparece como `@label` no chat) e uma função (padrão `user`). A lista de funções inclui `owner`, com um alerta: um dono tem controle total deste computador, incluindo a configuração do aipager, o token do bot e as credenciais do Claude. Dê só a você. Dê `admin` às pessoas que devem usar o Acesso total, `/settings` e `/update` no grupo. (No próprio chat privado com o bot, qualquer pessoa que pode mandar mensagens e responder ao Claude pode usar o Acesso total nas próprias sessões, seja qual for a função dela.)
6. **Uma regra opcional:** “Block file edits (Write, Edit) for `user` members in this group? Admins and the owner are not affected.” (Bloquear a edição de arquivos para os membros `user` deste grupo? Admins e o dono não são afetados.) O padrão é não: um `user` já só escreve dentro da pasta de projeto da sessão. Com sim, cada Write e Edit numa tarefa de um membro cuja função não está isenta das regras de função (`user`, `read_only`, e as funções que você define sem `bypass_role_denies`) é negado sem perguntar e a tarefa para. Aí um admin ou o dono precisa mandar esse trabalho.
7. **A pasta raiz dos membros**, com `~/aipager-users/group-<label>` já preenchido: onde os membros `user` e `read_only` do grupo criam as sessões deles e trabalham (veja [Pastas raiz](#root-folders)).

Para adicionar alguém a um grupo que já está configurado, use Edit a scope → o grupo → Add a member. Todo mundo que já está no grupo mantém o lugar e a função.

### <a id="finding-ids-auto-detect"></a>Descobrir IDs (detecção automática)

A detecção automática de um membro pega a mensagem mais recente de alguém que ainda não foi adicionado e pergunta “Is this the person to add?” (É esta a pessoa a adicionar?) antes de usá-la (responda não e ela procura mais para trás). Ela sugere o nome de usuário do Telegram da pessoa como rótulo. Num grupo, a pessoa precisa se dirigir ao bot: mencioná-lo ou mandar `/start` (veja [o que o bot lê](#what-the-bot-reads-in-a-group)). O assistente diz onde está olhando:

- **“Watching through the running daemon”** (pelo aipager em execução): enquanto o `aipager start` roda, o aipager recebe todas as mensagens do bot, então o assistente lê o que o aipager anotou em `~/.claude/aipager-pending-users.json`. O aipager anota quem se dirige ao bot num grupo em que ele ainda não trabalha (o ID, o título e o tipo do grupo, e o ID, o nome de usuário e o nome da pessoa), e qualquer pessoa que não é membro de um grupo em que ele trabalha. Ele nunca responde num grupo em que não trabalha. Uma mensagem privada de alguém que não está configurado recebe resposta e não é anotada, então para adicionar o chat privado de uma pessoa enquanto o aipager roda, cole o ID dela (ou pare o aipager antes).
- **“Watching Telegram directly”** (direto no Telegram): quando o aipager está parado, o próprio assistente pede ao Telegram as mensagens recentes do bot.

O seu próprio chat privado, na primeira vez, sempre é lido do Telegram. Uma mensagem de um admin anônimo do grupo, ou uma publicada como o grupo ou como um canal, não conta: o ID de remetente dela (1087968824, 136817688 ou 777000) é o mesmo de todo mundo que publica assim, então o assistente a recusa, digitada ou detectada. Peça à pessoa para publicar com a própria conta.

### <a id="where-the-config-lives"></a>Onde fica a configuração

O assistente escreve `~/.config/aipager/aipager.yaml` (permissões 0600): o token do bot, os chats em que o bot trabalha, e os membros de cada chat com as funções deles. O que cada função pode fazer fica em `~/.config/aipager/policy.yaml`, que é seu: o assistente nunca o escreve (veja [Como as regras funcionam](#how-rules-work)). As instalações configuradas com o formato antigo `team.yaml` são migradas sozinhas, pelo aipager ao iniciar ou pelo assistente na próxima vez que ele rodar.

### <a id="live-reload"></a>Recarregar a configuração sem reiniciar

Depois de cada mudança de chats ou de membros feita no menu de edição (adicionar ou tirar um grupo ou um chat privado, adicionar ou tirar um membro, definir uma função, editar `deny_tools`, renomear um chat), e depois de um grupo ou de uma pessoa adicionados no fim da primeira vez ou de um grupo pela metade que você retoma (uma única recarga para tudo o que você adicionou ali), o `aipager config` manda ao aipager em execução uma recarga ao vivo (**SIGUSR1**) e diz “Scopes reloaded live (no daemon restart needed)”. A recarga lê de novo o `aipager.yaml` e o `policy.yaml`. Se a configuração no disco fosse recusada, o assistente diz “Not applied” com o motivo, e o aipager fica com a configuração anterior até você consertar. Se o assistente não tiver certeza de qual processo é o aipager, ele manda você reiniciá-lo.

O que uma recarga aplica na hora:

- quem pode usar o bot em cada chat, com qual função e quais regras;
- um grupo ou chat privado novo começa a receber mensagens e ganha o menu `/` dele; um que foi tirado para (as sessões dele continuam rodando até você encerrá-las);
- uma mensagem guardada (uma que espera um pedido ser respondido ou a tarefa de outra pessoa terminar) de alguém que não pode mais mandar nesse chat é descartada: recebe um 🤷 e o bot responde “Não foi enviada ao Claude: @bob não pode mais enviar mensagens aqui.”;
- uma tarefa em andamento de uma pessoa que foi tirada ou perdeu permissões fica, no resto dessa tarefa, só com o mais rígido entre as regras antigas e as novas (o piso restrito para uma pessoa tirada). Uma tarefa digitada no terminal não muda, a não ser que uma mensagem do Telegram tenha entrado nela: o aipager não registra de quem, então uma tarefa assim é restringida quando qualquer dono desse chat (ou, num chat privado, o membro dele) perde permissões. Uma tarefa que já estava em andamento quando o aipager reiniciou (ainda trabalhando, ou esperando uma pergunta ou uma permissão) é restringida para cada membro do chat dela que perdeu permissões. Uma promoção nunca amplia uma tarefa em andamento: as permissões novas valem a partir da próxima tarefa dessa pessoa.

Só um **token de bot** novo precisa de reinício (o `aipager config` diz isso depois do Refresh bot token). Quando nenhum aipager está rodando, nenhuma recarga é enviada: o próximo `aipager start` lê a configuração nova.

Para recarregar à mão (por exemplo depois de editar o `policy.yaml`), mande o sinal só para o processo do aipager. Sob o serviço em segundo plano:

```sh
pid=$(systemctl --user show -p MainPID --value aipager.service)
[ "${pid:-0}" -gt 0 ] && kill -USR1 "$pid"
```

(A verificação importa: quando o serviço não está rodando, `MainPID` é `0`, e `kill -USR1 0` mandaria o sinal para os processos do seu próprio shell.)

Para um aipager que você iniciou com `aipager start`, mande `kill -USR1 <pid>` para esse processo. Não mande o sinal para tudo o que casar com `pgrep -f 'aipager start'` (um shell ou um contêiner que roda o aipager também casa, e o SIGUSR1 para um processo que não o trata), e não use `systemctl --user kill` sem `--kill-whom=main` (as sessões do serviço estão no mesmo grupo dele e seriam paradas).

Se o `aipager.yaml` ou o `policy.yaml` estiverem quebrados na hora da recarga, ou o `aipager.yaml` não existir, o aipager escreve um alerta no log e fica com a configuração anterior na memória. Um erro de digitação ou um arquivo tirado do caminho não conseguem deixar você de fora nem deixar alguém entrar: uma recarga nunca volta para o uso pessoal um aipager que tem chats configurados (só um reinício faz isso).

### <a id="bot-admin-rights-and-privacy-mode"></a>Permissões de admin do bot e modo de privacidade

**Faça do bot um admin do grupo, com permissão para fixar mensagens.** Ele precisa disso para fixar a barra de status do grupo (veja [O que todo mundo vê](#what-everyone-sees)). Sem isso, o grupo funciona mas não tem barra de status; se você der a permissão depois, reinicie o aipager para a barra aparecer.

Um bot que é admin do grupo recebe todas as mensagens do grupo, seja qual for o modo de privacidade dele no BotFather. Isso é seguro: o aipager só atende as mensagens listadas em [o que o bot lê num grupo](#what-the-bot-reads-in-a-group) e ignora o resto, então a conversa do grupo nunca chega a uma sessão. Um bot que não é admin fica no modo de privacidade por padrão (deixe ligado), e aí o Telegram só passa a ele algumas mensagens do grupo; se uma sua não chegar, responda a uma das mensagens do bot ou use um comando. A configuração de privacidade não muda nada do que o aipager atende.

Ser admin do grupo no Telegram não dá nada a ninguém no aipager. O que cada pessoa pode fazer vem só da função dela no `aipager.yaml`.

### <a id="when-telegram-upgrades-the-group"></a>Quando o Telegram atualiza o grupo

O Telegram transforma um grupo básico em supergrupo quando você o torna público, liga os tópicos, ele passa de 200 membros ou você muda algumas permissões de admin, e essa atualização dá ao grupo um ID de chat novo. O aipager acompanha sozinho: quando vê a atualização (o aviso do Telegram no grupo, ou o primeiro envio para o ID antigo), ele move a entrada do grupo no `aipager.yaml` para o ID novo, leva junto as sessões, os destinos das mensagens e os `/settings` do grupo, e publica uma linha no grupo: “O Telegram atualizou este grupo. O aipager foi junto; você não precisa fazer nada.” A barra de status fixada recomeça no grupo atualizado. O log do aipager tem um alerta com os dois IDs.

Os nomes das sessões dentro do aipager mantêm o ID antigo (você nunca os vê), e o `aipager.yaml` guarda um registro curto `chat_migrations` da mudança, para uma sessão iniciada antes da atualização continuar sendo do grupo atualizado, mesmo depois de um reinício. O nome interno de uma sessão com o ID antigo (`/name__g<old id>`) funciona no grupo atualizado e em nenhum outro lugar. Se o aipager estava parado durante a atualização, ele percebe no próximo início. Uma tarefa que está em andamento no momento da atualização termina e a resposta dela chega ao grupo atualizado, mas o cartão de progresso dela não aparece de novo no resto dessa tarefa. Se o ID novo já for um chat separado na configuração, nada muda e o log diz isso; tire um dos dois com o `aipager config`. O “Test bot reachability” no `aipager config` mostra o ID novo quando encontra um grupo atualizado.

A cada início, o aipager consulta cada grupo uma vez. Se o Telegram responder que o grupo não existe ou que o bot não pode usá-lo (o grupo foi apagado, o bot foi tirado, ou o grupo foi atualizado enquanto o aipager estava parado e o Telegram não disse para qual ID), o log do aipager tem um alerta com o nome do chat: confira se o bot ainda está no grupo e, se ele foi atualizado, adicione o grupo de novo no `aipager config` com Add a group scope (ele recebe o ID novo; adicione os membros dele de novo) e depois tire o antigo com Edit a scope → Remove this scope (o ID antigo não funciona mais). O aipager não publica nada e não tenta de novo.

### <a id="forum-topics-are-not-supported"></a>Os tópicos de fórum não são suportados

Use um grupo comum (sem tópicos) por equipe. Num grupo com os tópicos ligados, o aipager continua funcionando, mas não fica num tópico:

- a resposta a um comando ou a uma mensagem (por exemplo `/status`, ou a resposta a “Qual sessão?”) fica no tópico em que foi mandada;
- os avisos, os cartões de atividade, as respostas, os pedidos de permissão e a barra de status fixada são publicados no grupo sem tópico, então podem cair em General;
- cada pessoa tem uma sessão atual para o grupo todo, não uma por tópico.

Ligar os tópicos também atualiza um grupo básico para supergrupo, o que o aipager acompanha (acima).

## <a id="roles"></a>Funções

Quatro funções próprias (veja `aipager/safety.py`). A função que conta é a da pessoa **no chat em que ela age**. Ninguém pode mudar membros nem funções pelo Telegram ou pelo Mini App: só o `aipager config` no computador faz isso, e isso deixa o assunto nas mãos do dono.

| | `owner` | `admin` | `user` | `read_only` |
|---|---|---|---|---|
| Mandar mensagens (`can_prompt`) | ✅ | ✅ | ✅ | ❌ |
| Responder ao Claude: Permitir, Negar, perguntas (`can_approve`) | ✅ | ✅ | ✅ | ❌ |
| Gerenciar: Acesso total, `/settings` de um grupo, `/update`, instalar a voz (`can_manage`) | ✅ | ✅ | ❌ (Acesso total: ✅ no próprio chat privado) | ❌ |
| Mudar membros e funções pelo Telegram ou pelo Mini App | ❌ | ❌ | ❌ | ❌ |
| Isento das regras de negação de função (`bypass_role_denies`) | ✅ | ✅ | ❌ | ❌ |
| Isento do piso de segurança (`bypass_safety`) | ✅ | ❌ | ❌ | ❌ |
| Bash e outras ferramentas que rodam código | ✅ | ✅ (regras de melhor esforço) | ❌ | ❌ |
| Qualquer comando de barra | ✅ | ✅ | só os do teclado de Comandos, `/compact` e as trocas de modelo | igual a `user` |
| Onde as sessões são criadas (`/new`, Continuar, Reiniciar, a troca de modo) | em qualquer lugar | em qualquer lugar | só dentro da [pasta raiz](#root-folders) do chat | igual a `user` |
| Onde os arquivos são escritos | em qualquer lugar | em qualquer lugar que o piso de segurança permita | a pasta de projeto da sessão (quando está dentro da pasta raiz) e o scratchpad dela, nunca a pasta pessoal | igual a `user` |
| Leituras na sua pasta pessoal | em qualquer lugar que o piso de segurança permita | em qualquer lugar que o piso de segurança permita | só dentro da pasta raiz (outras pastas do computador como antes, as pastas raiz de outros membros nunca) | igual a `user` |
| Arquivos de credenciais (`~/.ssh`, `~/.git-credentials`, ...) | legíveis | legíveis | nunca (por padrão) | nunca (por padrão) |
| `Grep`/`Glob` | em qualquer lugar | em qualquer lugar menos as pastas protegidas do aipager (nem uma pasta que seja uma delas, esteja dentro de uma ou contenha uma) | a pasta de projeto da sessão e o scratchpad dela | igual a `user` |

- **owner**: controle total, incluindo o piso de segurança próprio. Deve haver exatamente um: a pessoa que administra o computador.
- **admin**: tudo o que um dono pode fazer no Telegram e no Mini App: mudar sessões para o Modo Acesso total (e iniciá-las nele), retomar uma sessão no Acesso total, mudar os `/settings` de um grupo, rodar `/update`, instalar o recurso de voz, mandar qualquer comando de barra. Um admin também está isento das regras de negação de função (os `deny_tools` do chat e do membro não valem para ele). Duas coisas ficam com o dono:
  - mudar membros e funções: só o `aipager config` no computador faz isso, e nada no Telegram nem no Mini App consegue;
  - estar isento do piso de segurança (a configuração e o token do bot do aipager, as credenciais do Claude Code, os arquivos de controle do aipager). O `Grep` e o `Glob` de um admin podem procurar em qualquer outro lugar, mas não numa pasta que seja uma dessas, esteja dentro de uma ou contenha uma: `Grep` em `~`, `/` ou `/tmp` (ou sem pasta, a partir de uma sessão na pasta pessoal) é negado, e também um `Glob` cujo padrão comece numa delas (como `/home/you/.config/aipager/*`). Procure dentro de uma pasta de projeto. Em versões do Claude Code sem a ferramenta `Grep`, o Claude procura pelo Bash; ali também é negada uma busca ou listagem recursiva da pasta pessoal inteira ou de `/` (`grep -rn x ~`, `rg x $HOME`, `find / -name x`), em melhor esforço (uma busca sem pasta, ou com `..`, rodada a partir da pasta pessoal ou abaixo dela não é vista), e as buscas em projetos não, incluindo uma busca POR um caminho pessoal (`grep -rn "$HOME" src`) ([Modelo de segurança](security.md#team-mode-enforcement)).

  Um admin mantém o Bash, e com um shell os padrões de comandos do piso são de melhor esforço, não um limite: só faça de alguém admin se você daria a ele um shell no computador.
- **user**: as regras de negação valem. Sem Bash, sem nenhuma outra ferramenta que rode código (PowerShell, Monitor, mensagens agendadas, workflows; a lista é `safety.CODE_EXECUTION_TOOLS`), sem `SendMessage` para outras sessões, sem trocar de worktree. Os arquivos só são escritos dentro da pasta da sessão e do scratchpad do Claude Code dela, e nunca em `.claude/`, `.git/` nem `.mcp.json` dessa pasta (cada um roda comandos). `Grep` e `Glob` só procuram dentro dessas pastas, com globs relativos simples. Um `user` cria, retoma, reinicia e troca sessões só dentro da [pasta raiz](#root-folders) do chat, e não lê nada da sua pasta pessoal fora dela. Uma pasta de sessão que seja a sua pasta pessoal, `/` ou uma pasta acima, ou que esteja fora da pasta raiz (uma sessão iniciada antes das pastas raiz, ou por outra pessoa), não conta: ali um `user` só pode escrever e procurar no scratchpad. As leituras seguem as regras dos caminhos protegidos, e os seus arquivos de credenciais nunca podem ser lidos: `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.password-store`, `~/.pgpass`, `~/.npmrc` e `~/.pypirc`. Comandos de barra: só os do teclado de Comandos, `/compact` e as trocas de modelo (veja [Com as permissões de quem uma mensagem roda](#who-a-message-runs-as)).
- **read_only**: observadores. Veem tudo no grupo, e podem usar `/status`, `/diff`, `/whoami`, `/help`, `/start` e `/app`, olhar o `/settings` e passar as páginas de uma lista do `/resume` que outra pessoa abriu. As próprias mensagens de texto, áudio e arquivo deles não são enviadas para lugar nenhum. A primeira que eles mandam num chat recebe uma resposta dizendo que a função deles é `read_only`; depois disso o bot fica calado para eles nesse chat (nem resposta, nem reação) até o aipager reiniciar. Eles têm os mesmos limites de ferramentas e de escrita que `user`, caso alguma mensagem deles chegue a uma sessão.

### <a id="custom-roles"></a>Funções personalizadas

O `policy.yaml` pode mudar qualquer campo de uma função própria, ou definir funções novas. Um campo que você define substitui o valor próprio. Os campos:

- `can_prompt`, `can_approve`: mandar mensagens; responder ao Claude.
- `can_manage`: usar o Acesso total, mudar os `/settings` de um grupo, rodar `/update` e instalar o recurso de voz (próprios: `owner` e `admin` true, `user` e `read_only` false). Ele nunca deixa ninguém mudar membros nem funções, e nunca isenta do piso de segurança.
- `bypass_role_denies`, `bypass_safety`: veja a tabela.
- `deny_tools`, `allow_tools`, `deny_bash_patterns`, `deny_paths_no_access`, `deny_paths_no_write`: veja [Como as regras funcionam](#how-rules-work).

Uma função que você define do zero começa com `can_prompt` e `can_approve` ligados, `can_manage` e as duas isenções desligados, os arquivos de credenciais no `deny_paths_no_access` dela, e **nenhuma ferramenta negada, então ela tem Bash** até você definir `deny_tools` (o `aipager doctor` alerta uma vez quando um membro a tem). O limite de escrita e de busca vale para toda função sem `bypass_safety` nem `bypass_role_denies`. A [pasta raiz](#root-folders) vale para toda função sem `can_manage`, sejam quais forem as isenções dela: uma função que você define com `bypass_role_denies` ou `bypass_safety` mas sem `can_manage` escreve e procura em qualquer lugar dentro da pasta raiz dela (e do scratchpad), não lê nada da sua pasta pessoal fora dela, e em nenhum outro lugar (com `bypass_safety`, numa sessão que roda fora da pasta raiz dela, só escreve no scratchpad). Só donos e admins não têm restrições.

### <a id="root-folders"></a>Pastas raiz

Cada chat tem uma pasta raiz para os membros: a pasta em que os membros cuja função ali não tem `can_manage` (os `user` e `read_only` próprios, e as funções que você define sem ele) criam e mantêm as sessões deles. Donos e admins não ficam presos a ela. A pasta raiz de um chat privado é dessa pessoa; a de um grupo é dividida entre os membros restritos do grupo. Dois chats nunca dividem uma, a não ser que você dê a mesma pasta aos dois de propósito (o assistente pede para você confirmar).

- **Definir.** O `aipager config` a pergunta quando você adiciona um grupo, ou um chat privado para uma função sem `can_manage`, com o valor padrão já preenchido, e Edit a scope → **Set the members' root folder** a muda (vale na hora, com uma recarga ao vivo). A configuração atual mostra a pasta raiz de cada chat, com `(default)` quando nenhuma foi definida.
- **O padrão** é `~/aipager-users/<label>` para um chat privado (o rótulo da pessoa) e `~/aipager-users/group-<label>` para um grupo. O rótulo vai para minúsculas e tudo o que não for letra, número, `.`, `_` ou `-` vira `-`; quando dois chats cairiam na mesma pasta, cada um ganha o ID do chat no fim (`sara-123456`). A pasta (e `~/aipager-users`, quando é nova) é criada, com permissões 0700 para só você poder abri-la, na primeira vez que um membro abre o `/new` ou o formulário de Nova sessão do Mini App. Antes de uma sessão começar nela, o aipager a marca como confiável para o Claude Code, para a pergunta “Do you trust this folder?” do Claude Code não encerrar a primeira sessão do membro. Um padrão nunca muda de lugar: quando uma edição no `aipager config` (a pasta raiz de outro chat, um chat tirado, um rótulo novo) mudaria a pasta padrão de um chat, o assistente escreve a pasta que ele tinha como pasta raiz desse chat.
- **O que o assistente recusa**: um caminho relativo, caracteres curinga, `/`, a sua pasta pessoal ou uma pasta acima dela, e uma pasta que seja, esteja dentro de ou contenha uma das pastas protegidas do aipager ou dos arquivos de credenciais (`~/.claude`, `~/.config/aipager`, `~/.config`, `~/.ssh`, ...), e a pasta temporária do Claude Code (`/tmp/claude-*`, onde fica o scratchpad de cada sessão). Uma pasta que ainda não existe está ok. Ele pede confirmação para uma pasta de onde o seu sistema roda programas (`~/bin`, `~/.local/bin`, `~/.config/systemd`, `~/.config/autostart`, ou uma dentro delas).
- **Criar sessões.** A página de Pasta do `/new` e o Mini App listam a pasta raiz e as pastas logo dentro dela (não as ocultas, nem um link que leve para fora dela), sem a linha “Pasta padrão”; uma sessão nova começa na pasta raiz, a não ser que você escolha uma dessas. Nova pasta cria a pasta dentro da que você escolheu. Qualquer outra coisa recebe “Sua função só pode iniciar sessões na sua pasta ~/... ou em uma pasta dentro dela.”
- **Sessões fora dela.** Continuar, reiniciar, Substituir e mudar o modo de uma sessão cuja pasta está fora da pasta raiz recebem “Esta sessão trabalha fora da sua pasta ~/..., então sua função não pode continuá-la, reiniciá-la nem mudar o modo dela. Peça a um admin.” As mensagens para uma sessão assim que esteja rodando continuam sendo enviadas, e o hook de segurança limita a tarefa: ali ela só escreve no scratchpad.
- **A que o hook limita uma tarefa.** A tarefa de um membro restrito só escreve na pasta da sessão (quando ela está dentro da pasta raiz) e no scratchpad dela, não lê, não faz `Grep` nem `Glob` de nada da sua pasta pessoal fora da pasta raiz (fora o scratchpad e o próprio arquivo de resposta), e nunca mexe na pasta raiz de outro chat, esteja onde estiver. As pastas fora da sua pasta pessoal (`/usr`, `/etc`, ...) continuam legíveis, as pastas protegidas continuam negadas, e o `Bash` continua negado para `user`. Quando uma tarefa mistura dois membros com pastas raiz diferentes, ganha a mais estreita; duas que não estão uma dentro da outra deixam só a pasta da própria sessão.

Para devolver o Bash a `user` (só para pessoas a quem você daria um shell), cite-o em `allow_tools` (que aí vira também a lista de permitidos dessa função) ou substitua o `deny_tools` dela:

```yaml
roles:
  user:
    # a lista própria, sem o Bash
    deny_tools: [PowerShell, Monitor, REPL, Workflow, CronCreate,
                 RemoteTrigger, AppifactRepl, self_hosted_runner_spawn_local,
                 self_hosted_runner_requeue_session, SendMessage,
                 EnterWorktree, ExitWorktree]
```

Aí o `aipager doctor` alerta: `role user has Bash: its safety rules are best-effort, not a boundary`. Se o seu `policy.yaml` já definia `deny_tools` para `user` antes de existir este padrão, a sua lista continua substituindo-o, então essa função tem Bash até você acrescentá-lo de volta; o alerta do doctor diz isso. Veja [Modelo de segurança → como as regras valem no uso em equipe](security.md#team-mode-enforcement) para o que esses limites não cobrem.

## <a id="talking-to-the-bot-in-a-group"></a>Falar com o bot num grupo

### <a id="what-the-bot-reads-in-a-group"></a>O que o bot lê num grupo

Num grupo, o aipager só atende uma mensagem que seja:

- **um comando**: `/status`, `/x1 fix the tests`, ou o mesmo com `@aipagerbot` depois do comando (`/x1@aipagerbot`), que o Telegram acrescenta quando você o escolhe no menu. Um comando para outro bot (`/x1@otherbot`) é ignorado;
- **uma resposta a uma das mensagens do bot**;
- **uma mensagem que menciona o bot**: `@aipagerbot fix the tests`;
- **um toque no teclado do bot** no grupo. Num grupo, todo botão do teclado começa com um quadradinho (`▫️ stop`, `▫️ Limpar conversa`), e só esse texto marcado conta como toque. Digitar a palavra sozinha é conversa normal: um membro que digita `stop`, `Limpar conversa`, `Opus` ou o nome de uma sessão não muda nada. Para usar de propósito uma palavra do teclado sem o teclado, dirija-se ao bot: `@aipagerbot stop`, ou `stop` como resposta a uma das mensagens dele.

Todo o resto (os membros conversando entre si, uma foto ou um áudio que não é resposta ao bot) não recebe nem reação nem resposta. Isso vale seja o bot admin do grupo ou não. As mensagens editadas são ignoradas em todo lugar: edite uma mensagem e nada acontece; mande de novo.

Uma foto, um arquivo ou um áudio funcionam do mesmo jeito: mande como resposta ao bot, ou mencione o bot na legenda. Num álbum, ponha a legenda no primeiro item: os itens do álbum que chegam depois do que tem a legenda, a menção ou a resposta são pegos, e os de antes não.

**A menção é tirada.** `@aipagerbot fix the tests` chega ao Claude como `fix the tests` (o Claude Code leria `@word` como um arquivo). `@aipagerbot status` e `@aipagerbot x2` funcionam como digitar `status` ou `x2` num chat privado, e `@aipagerbot` sozinho mostra para onde vão as suas mensagens. Um comando de sessão depois da menção funciona (`@aipagerbot /x1 fix it`), mas um dos comandos do próprio aipager não: `@aipagerbot /stop` recebe “Envie /stop sozinho, sem mencionar o bot.”

**Só membros.** Uma pessoa que não é membro do grupo recebe uma resposta (“🚫 Você não está entre as pessoas permitidas neste chat.”) com o ID do Telegram dela para passar a você, e é anotada para o `aipager config`; depois disso o bot a ignora até o aipager reiniciar. Um membro `read_only` fica sabendo da função dele uma vez (veja [Funções](#roles)).

**Os admins anônimos precisam publicar com a própria conta.** Um admin do grupo com “Remain anonymous” ligado, ou uma mensagem publicada como o grupo ou como um canal, não diz quem a escreveu: o Telegram mostra o mesmo remetente para todos os admins anônimos. O aipager não consegue dar a uma mensagem assim a função de ninguém, então responde “Escreva com sua própria conta para usar o bot.” (uma vez por chat até o aipager reiniciar) e não faz mais nada. Ela nunca é anotada como usuário pendente, e o assistente recusa esse ID compartilhado. Desligue o “Remain anonymous” para você nas configurações de admin do grupo, ou publique com a sua própria conta.

### <a id="talking-to-a-session-in-a-group"></a>Falar com uma sessão num grupo

Responda a uma das mensagens de uma sessão (a resposta dela, o cartão dela) e a sua mensagem vai para essa sessão. Ou mande `/x1 sua mensagem`, ou uma legenda que comece com `/x1`.

**Cada pessoa tem a própria sessão atual**: a última para a qual mudou (`/x1`), para a qual mandou uma mensagem, a que respondeu ou que criou. A resposta da sessão de outro membro não a muda, e as mensagens de outras pessoas também não. Uma menção, um áudio, um template ou um comando do teclado vão para a sua própria sessão atual, e um comando do teclado ou um template a citam na resposta (“🧹 /clear enviado para x1”). Um `/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete` ou `/diff` sem nome só age sobre a sua própria sessão atual (com a confirmação de sempre), e só quando o comando faz sentido para ela (trabalhando para o `/stop`, encerrada para o `/delete`); senão, sempre mostra a lista para escolher, mesmo quando só sobra uma sessão, porque ela pode ser de outra pessoa. O `/clearqueue` e o `/now` agem sobre a sua sessão atual. `/kill x1` e os outros comandos com nome agem sobre essa sessão, como antes.

- Sem uma sessão atual sua e com mais de uma sessão rodando, o bot pergunta “Qual sessão? Responda a uma das mensagens dela ou escreva /x1 seguido da sua mensagem.” com um botão por sessão, e não manda nada. Com uma sessão só rodando, vai para ela.
- Se a sua sessão atual foi encerrada, a mensagem não é mandada para a única sessão que ainda roda (ela pode ser de outra pessoa): o bot responde “x1 foi encerrada. Qual sessão?” com um botão para cada sessão rodando e, quando x1 pode ser continuada, “▶️ Continuar x1”, e não manda nada.
- Tocar no botão de uma sessão em qualquer uma das duas perguntas também manda para essa sessão a mensagem sobre a qual se perguntava (um texto, o texto de um áudio ou um arquivo), como se você a tivesse mandado ali, e a pergunta muda para “Enviado para x2.”. Só quem mandou a mensagem pode usar os botões, e só por dez minutos e até o aipager reiniciar: depois disso o botão só faz dessa a sua sessão atual (“Essa mensagem ficou antiga e não foi enviada; envie de novo.”). ▶️ Continuar traz x1 de volta mas não manda nada: mande a sua mensagem de novo.
- A barra fixada não tem a linha “As mensagens vão para” num grupo, porque ela é diferente para cada pessoa.

**Os cartões que esperam uma resposta são de uma pessoa.** O cartão de sessão nova diz “Responda a esta mensagem com um nome e, se quiser, o que fazer primeiro:”, e a pergunta de renomear “... Responda a esta mensagem com o novo nome.”. Num grupo cada um mostra de quem é (“🆕 Nova sessão (@alice)”), só essa pessoa pode respondê-lo, e duas pessoas podem criar sessões ou renomear ao mesmo tempo. A resposta de outro membro ao cartão de sessão nova de alguém recebe “Este cartão é de @alice. Envie /new para abrir o seu.” e não vai para lugar nenhum. Uma pergunta de renomear para de esperar depois de 10 minutos, ou assim que a pessoa dela fizer outra coisa. O cartão “x1 está pronta”, a resposta ao `/x1`, o `/start` e o `/help` dizem para responder à mensagem de uma sessão ou mencionar o bot (“Responda a uma mensagem de x1 (ou mencione @aipagerbot) para falar com ela.”), porque uma mensagem comum não chega ao bot.

**O teclado é de cada pessoa.** Templates, Comandos, Modelo › e « Voltar mudam só o teclado de quem os toca: o bot responde como resposta ao toque dessa pessoa, e o teclado de mais ninguém se mexe. « Voltar sobe um nível de onde você está, não importa o que os outros membros estejam olhando. O teclado principal que o `/start` manda é do grupo todo, e leva todo mundo de volta ao teclado principal. Quando uma sessão do grupo começa ou termina, o aipager manda de novo esse teclado principal ao grupo, uma vez, para os botões de sessão dele ficarem atualizados. Depois de atualizar de uma versão sem os botões marcados, um toque num botão do teclado antigo pode ser ignorado: mande `/start` uma vez para dar ao grupo o teclado novo. O teclado de um chat privado não tem marca e funciona como antes.

### <a id="the-mini-app"></a>O Mini App

O `/app` só abre o Mini App num chat privado. Num grupo ele não está no menu `/`; se for digitado ali, ele diz a um membro cujo chat próprio com o bot está configurado “📱 O Mini App só funciona em um chat privado: fale com o bot no privado e envie /app lá.”, e a qualquer outra pessoa “O Mini App abre pelo seu chat privado com o bot. Peça ao dono do bot para adicionar você.”

O Mini App mostra um chat de cada vez. Quem está em mais de um chat (por exemplo o próprio chat privado e um grupo) ganha uma linha com os nomes dos chats no topo para trocar entre eles; ele abre no próprio chat privado da pessoa, senão no primeiro chat que a inclui, e lembra a última escolha nesse celular. Tudo o que tem dentro (as sessões, os botões delas, as configurações e os avisos que ele publica) é do chat escolhido, com a função que a pessoa tem nesse chat. Com um chat só, nada a mais aparece.

## <a id="who-a-message-runs-as"></a>Com as permissões de quem uma mensagem roda

Cada mensagem roda com **as permissões de quem a mandou**, no chat de onde ela foi mandada. Alguém que é `user` no grupo e `owner` do próprio chat privado manda, toca e roda comandos no grupo como `user`, seja qual for a ordem dos chats no `aipager.yaml`.

- Cada mensagem do Telegram leva à sessão a identidade e as regras de quem a mandou (veja [Modelo de segurança → como as regras valem no uso em equipe](security.md#team-mode-enforcement)). O Claude vê quem mandou cada mensagem (`[via Telegram · @bob · role:user]` num grupo), nunca quem controlava a sessão antes. Quem manda uma mensagem passa a controlar a sessão, tenha digitado, usado `/x1`, um template ou um comando do teclado.
- **As mensagens guardadas mantêm o remetente.** Enquanto a tarefa de outra pessoa está em andamento (incluindo um agente em segundo plano que ela iniciou), ou enquanto a mensagem de outra pessoa ainda espera para ser pega, ou enquanto um pedido de permissão ou uma pergunta está aberto, uma mensagem nova é guardada (👀) e mandada quando isso passar, como uma tarefa própria, **com as permissões do próprio remetente**. Uma tarefa digitada no terminal conta como do dono: só a mensagem de um dono (ou, num chat privado, a do próprio membro dele) entra nela. As suas próprias mensagens nunca esperam a sua própria tarefa. Se a tarefa que elas esperam for parada, ou parar porque uma ferramenta foi bloqueada, as mensagens guardadas são descartadas (🤷): mande de novo.
- **Ganha a regra mais rígida.** Se mesmo assim mensagens de pessoas diferentes acabarem numa mesma tarefa, ela roda com a combinação mais restritiva de todos que estão nela. Uma mensagem que entra numa tarefa em andamento nunca a amplia: o resto da tarefa mantém as regras mais rígidas de todos que entraram.
- **Comandos de barra das funções restritas.** Uma função que não está isenta das regras de função (`user`, `read_only`, as funções que você define sem `bypass_role_denies`, e um remetente que o aipager não conhece) só pode mandar os comandos de barra do teclado de Comandos (o seu, se o `keyboard.json` o mudar), `/compact` e as trocas de modelo (`/model <name>`). Qualquer outro, digitado, mandado com `/x1 /deliver ...`, como legenda de um arquivo, como primeira mensagem de `/new x1 /review ...` ou como template, recebe “Esse comando é só para admins.” e um 🤷, e nada chega ao Claude. Donos e admins mandam qualquer um.
- **As funções restritas ficam fora da pasta pessoal e dos arquivos de credenciais.** Uma tarefa de `user` ou `read_only` nunca escreve numa pasta de sessão que seja a sua pasta pessoal, `/` ou acima dela (só no scratchpad dela), e nunca lê os arquivos de credenciais listados em [Funções](#roles).
- **As funções restritas ficam na pasta raiz delas.** A tarefa de uma função sem `can_manage` não lê nada da sua pasta pessoal fora da [pasta raiz](#root-folders) do chat, e não escreve nada numa pasta de sessão fora dela. A pasta raiz é a do chat de onde veio a mensagem do membro, vá para a sessão que for.
- **Um membro tirado perde as permissões na hora, não a tarefa em andamento.** Um remetente que o aipager não conhece mais (tirado enquanto a mensagem dele esperava) roda com o piso restrito. Depois de uma recarga ao vivo, as mensagens guardadas dele são descartadas, e uma tarefa dele que ainda roda continua rodando, presa ao piso restrito até terminar; mande `/stop` para encerrá-la agora (veja [Recarregar a configuração sem reiniciar](#live-reload)).
- **Uma mensagem de outra sessão do Claude leva as permissões de quem a manda.** Uma mensagem que uma sessão manda a outra (o `SendMessage` do Claude Code) roda com as permissões da tarefa que a mandou, do jeito que o aipager as verificou, nunca com as anteriores da sessão que a recebe. Uma mensagem que o aipager não consegue verificar roda com as regras mais rígidas e sem mudanças de arquivos nem acesso à web (veja [Modelo de segurança → mensagens entre sessões do Claude](security.md#messages-between-claude-sessions)).
- **Uma ferramenta bloqueada para a tarefa.** Quando uma tarefa tenta uma ferramenta que as regras dela negam, a tarefa para na hora com “🛑 x1 · Bloqueado por uma regra de segurança: ... (interrompida)”, e toda ferramenta seguinte dessa tarefa também é negada.

## <a id="who-can-tap-what"></a>Quem pode tocar em quê

Cada botão confere a função de quem o toca, no chat em que o botão está, no momento do toque. A função de quem mandou o comando que criou o botão não conta. (Alguns cartões também conferem quem os pediu: os cartões de confirmação e os cartões do `/new`, abaixo.)

- **Olhar é aberto a todos os membros** (VIEW). Os membros `read_only` podem passar as páginas do `/resume`, abrir a visão Encerradas do `/status`, o menu ⋮ de uma sessão, o modo ou o diff dela, percorrer o `/settings` e fechar um menu. Eles não podem agir: qualquer outro botão responde “Sua função neste chat não permite isso.”
- **Responder ao Claude precisa de `can_approve`** (APPROVE): Permitir, Sempre permitir, Negar, as opções de uma pergunta, Enviar, Continuar e o Responder da barra fixada. O `read_only` o tem desligado.
- **Agir sobre uma sessão precisa de `can_prompt`** (PROMPT): Parar, Tentar de novo, Compactar, Enviar agora, Encerrar, Reiniciar, Renomear, Excluir, Continuar, a troca de modo, ✍️ falar, Limpar todas, as configurações próprias de uma sessão e os cartões do `/new`. Um botão que o aipager não conhece também precisa disso.
- **Ir para o Acesso total precisa de um admin** (MANAGE: `owner`, `admin`, ou uma função com `can_manage`, a mesma regra do `/mode full`): Sim, mudar e Parar e mudar de modo num cartão que leva ao Acesso total, e Continuar no Acesso total. Mudar entre Perguntar e Automático é de qualquer pessoa que pode agir sobre a sessão. Continuar uma sessão que estava no Acesso total (digitando `/resume x1`, pela lista, por Continuar no cartão de nome do `/new`, ou pelo Mini App) a traz de volta no Modo Automático para qualquer outra pessoa, e a resposta diz isso: “♻️ x1 está rodando de novo no Modo Automático: o Modo Acesso total é só para admins.”
- **No próprio chat privado, uma pessoa pode usar o Acesso total seja qual for a função** (um `user` incluído; não o `read_only`, que não manda mensagens; uma função personalizada precisa de `can_prompt` e `can_approve`, porque o Acesso total responde a todos os pedidos de permissão do Claude Code). Todos os caminhos para o Acesso total acima funcionam ali para ela: `/mode full`, o cartão de modo, `/new !x1`, os cartões do `/new`, `/resume` e o Mini App. O próprio chat dela é o único lugar em que isso vale: num grupo, e no chat privado de qualquer outra pessoa, o Acesso total continua precisando de um admin. As sessões novas dela começam no Modo Automático, como as de todo mundo, até ela escolher o Acesso total em `/settings` → Novas sessões. Nada mais do que um admin faz se abre para ela (`/update`, o botão de instalar a voz e os `/settings` de um grupo continuam só para admins). Distribuir o Acesso total assim é seguro: ele inicia o Claude Code com `--dangerously-skip-permissions`, que só pula os pedidos de permissão do próprio Claude Code. O hook PreToolUse do aipager continua rodando em cada ferramenta e aplica as regras da função do membro (o piso de segurança, o limite de pastas, as regras de negação, os arquivos de credenciais) exatamente como no Perguntar e no Automático.
- **Instalar precisa de um admin** (UPDATE): os botões de instalar e reiniciar do recurso de voz, como os do `/update`.
- **Mudar os `/settings` de um grupo precisa de um admin.** Olhar, qualquer um pode.
- **Um cartão de confirmação é de quem o pediu.** Num grupo, os cartões de confirmação de Encerrar (`/kill`, ⋮ Encerrar sessão), Reiniciar, Excluir e a troca de modo, as listas de sessões que `/kill`, `/restart`, `/delete`, `/mode`, `/stop`, `/rename` e `/diff` mostram, e o cartão “Continuar x1 em:” que um toque em Continuar abre (os botões de modo dele) só respondem a quem mandou o comando ou tocou no botão que os abriu. O toque de qualquer outra pessoa, Cancelar incluído, recebe “Este cartão é de @alice. Envie /kill x2 para ter o seu.” (com o comando desse cartão) e não muda nada. O próprio cartão do `/mode` (em que modo uma sessão está) é de todos: uma troca tocada nele abre uma confirmação que é de quem tocou. Os cartões do `/new` só respondem a quem mandou o `/new`. O dono de um cartão é lembrado até ele ser usado ou cancelado, também depois de um reinício do aipager (os 4.096 cartões mais recentes). O cartão de nome do `/new` e a pergunta de nome do `/rename` são a exceção: eles vencem em 10 minutos de qualquer jeito, então depois de um reinício mande o comando de novo.
- **Um botão só funciona no chat da própria sessão.** Um tocado em qualquer outro lugar responde “Este botão pertence a outro chat.” e não faz nada, e Continuar sempre continua a sessão que o botão cita, nunca uma sessão de outro chat com o mesmo nome.
- **Os botões antigos não mudam nada.** Um botão que sobrou de uma tarefa anterior, ou de antes de a sessão terminar e voltar, não age sobre o que está rodando agora; ele diz isso (veja [comandos → botões antigos](commands.md#stale-buttons)).

## <a id="what-everyone-sees"></a>O que todo mundo vê

**Respostas ao Claude.** Cada Permitir, Negar e resposta é publicado no chat, embaixo do cartão da sessão, com quem fez:

```
✅ x1 · Permitido · @alice · Bash: ls -la /tmp
🚫 x1 · Negado · @bob · WebFetch: https://example.com
✓ x1 · Respondido · @carol · Which database? → Postgres
```

**Quem mudou uma sessão.** Um toque num botão só é visto por quem toca, então num grupo cada resultado que muda uma sessão diz quem fez:

```
⚠️ x1 · @bob interrompeu
⏹ @bob encerrou x2
🔄 @alice reiniciou [x1].
🗑️ @carol excluiu [x3].
⚙️ @alice mudou a sessão para 🤖 Modo Automático.
@alice mudou isto.          (embaixo do /settings)
```

O cartão “x1 está pronta” diz quem mudou o modo ou o modelo dele (“@bob mudou o modelo para Opus.”). Num chat privado essas linhas de resultado não citam ninguém. Uma mudança feita no Mini App é publicada no chat como “... pelo Mini App” (uma parada feita por lá também cita quem, no cartão da sessão).

**Uma ferramenta bloqueada por uma regra.** Uma tarefa para com “🛑 x1 · Bloqueado por uma regra de segurança: Write is in this role's deny_tools (interrompida)”. Se o Claude Code pedir permissão para uma ferramenta que a pessoa cuja mensagem começou a tarefa em andamento não pode usar, o bot responde Negar sozinho, não mostra nenhum pedido, e publica `⛔ x1 · Edit bloqueado para @bob (função user)` com o resumo da chamada embaixo, citando essa pessoa. Uma tarefa que as mensagens de várias pessoas começaram é bloqueada quando qualquer uma delas não pode usar a ferramenta, e cita essa. Uma tarefa digitada no terminal é sua e nunca é bloqueada pelas regras de um membro, a não ser que uma mensagem do Telegram tenha entrado nela: aí ela é conferida com as regras de quem mandou a última mensagem da sessão (a lista própria do grupo quando isso não se sabe). Quando o aipager não sabe de quem é a tarefa (ela já estava em andamento quando o aipager reiniciou), ele usa as regras de quem mandou a última mensagem da sessão, e a lista própria do grupo quando isso também não se sabe.

**Estado das mensagens.** 👀 numa mensagem quer dizer que ela foi enviada para a sessão (ou está guardada até poder ser); 👍 quer dizer que o Claude a pegou; 🤷 quer dizer que ela foi descartada antes de o Claude pegá-la. O `/whoami` mostra o seu rótulo, a sua função e as suas regras neste chat.

**A barra de status fixada.** O grupo tem a própria barra de status fixada (veja [comandos → a barra de status fixada](commands.md#the-pinned-status-bar)), que só lista as sessões deste grupo, se o bot for um admin com permissão para fixar. Sem essa permissão, a barra é pulada para o grupo: a mensagem é apagada em vez de ficar sem fixar no histórico.

**Auditoria no disco.** `~/.claude/aipager-audit.jsonl` registra cada decisão com `user_id`, `username` e o chat, para você poder reconstruir depois o que cada pessoa fez. Ele é sempre escrito em inglês, seja qual for o idioma do chat.

**Idioma.** Um grupo tem um idioma para as mensagens, os cartões e os teclados do próprio aipager, definido por um admin com `/language`; até um admin escolher, ele fica em inglês. Ele nunca segue o app do Telegram de ninguém. O que só uma pessoa vê, o aviso curto depois de um toque, fica no idioma dessa pessoa: o que ela escolheu no chat privado dela com o bot, senão o idioma do app do Telegram dela para alguém que chegou depois desta versão, senão o idioma do grupo. Só os textos do aipager mudam; o Claude responde no idioma em que cada pessoa escreve. Veja [Idiomas](commands.md#languages).

## <a id="where-messages-go"></a>Para onde vão as mensagens

- **Cada sessão é de um chat, e tudo dela vai para lá**: os cartões, as respostas, os pedidos de permissão, os arquivos e os avisos. Uma mensagem num chat nunca vai para uma sessão de outro chat, e o nome de uma sessão só funciona no próprio chat: digitar o nome de uma sessão de outro chat responde “Não há nenhuma sessão com esse nome”. Uma sessão iniciada enquanto o aipager rodava sem chats configurados ganha o chat dela assim que o `aipager config` os adiciona; até uma sessão ter um chat, nenhum chat a lista nem consegue agir sobre ela.
- **As sessões iniciadas no terminal** (`aipager session <name>`) são do chat privado do dono: o chat privado cujo membro tem a função `owner` (uma função com `bypass_safety`). Com vários chats privados e nenhum do dono, é o primeiro chat privado da lista; sem nenhum chat privado, o primeiro chat. Elas nunca vão para um grupo numa instalação que tem um chat privado.
- **A atualização para supergrupo é acompanhada sozinha** (veja [Quando o Telegram atualiza o grupo](#when-telegram-upgrades-the-group)).

## <a id="aipageryaml-schema-team-parts"></a>Estrutura do `aipager.yaml` (partes da equipe)

```yaml
schema_version: 3
bot_token: "…"

scopes:
  - kind: dm
    chat_id: 12345       # o seu próprio chat privado: o ID dele é o seu ID de usuário
    label: owner DM
    members:
      - id: 12345
        label: alice
        role: owner
  - kind: group
    chat_id: -100123456789
    label: team
    deny_tools: [Write, Edit]   # opcional: a resposta a “Block file edits”
    root: /home/alice/aipager-users/group-team   # a pasta raiz dos membros
    members:
      - id: 12345        # ID de usuário do Telegram (NÃO um rótulo, NÃO um ID de chat)
        label: alice     # como a pessoa aparece no chat (@alice)
        role: owner
      - id: 67890
        label: bob
        role: user
        deny_tools: [WebFetch]  # opcional, por membro
      - id: 11111
        label: charlie
        role: read_only
```

Um chat privado e os chats de grupo convivem: o bot trabalha em cada chat da lista, com as sessões de cada chat separadas. O `aipager config` escreve este arquivo; você nunca precisa editá-lo.

## <a id="how-rules-work"></a>Como as regras funcionam

`~/.config/aipager/policy.yaml` define as regras de cada função. Confira com `aipager policy validate`. Os campos que cada função aceita (além dos de [Funções personalizadas](#custom-roles)):

- `deny_tools`: nomes de ferramentas negadas sem perguntar (por exemplo `Write`, `Edit`, `Bash`, `WebFetch`).
- `allow_tools`: se não estiver vazio, uma lista de permitidos: todo o resto é negado para essa função.
- `deny_bash_patterns`: padrões comparados com as entradas do `Bash`.
- `deny_paths_no_access` / `deny_paths_no_write`: regras de caminhos. Para `user`, `read_only` e toda função que você define sem `bypass_role_denies`, o `deny_paths_no_access` começa com os arquivos de credenciais listados em **user** acima; definí-lo substitui essa lista, então copie para a sua os que quiser manter (uma tarefa em andamento em que outra mensagem entra volta a ficar presa à lista até terminar). Uma regra sem `/` nem `~` no começo (`**/.env`) casa em qualquer lugar, então enquanto houver uma, todo `Grep`/`Glob` de uma função restrita é negado (qualquer busca poderia ler um arquivo assim), o que para essa tarefa.

Os `deny_tools` de um chat e de um membro no `aipager.yaml` se somam aos da função, e os `allow_tools` de um membro se somam à lista de permitidos da função. Tudo isso só vale para as funções sem `bypass_role_denies`: não afeta um dono nem um admin.

Os arquivos em `~/.config/aipager/policy.d/*.yaml` são lidos depois do `policy.yaml`, pela ordem dos nomes, no mesmo formato: um campo de função definido num arquivo posterior substitui o valor anterior, e uma lista `safety:` só soma. O `aipager policy validate` confere todos.

Por baixo de todas as funções há um **piso de segurança** (caminhos protegidos e padrões de comandos) do qual só o `owner` está isento: os admins também ficam presos a ele. Ele é a lista própria do aipager mais a seção `safety:` do `policy.yaml`, que só pode somar:

```yaml
safety:
  deny_paths_no_access: [/srv/secrets/**]   # nem ler, nem escrever, nem procurar
  deny_paths_no_write: [~/notes/**]         # ler sim, escrever não
  deny_bash_patterns: ['\bcurl\b']          # comandos do Bash que são bloqueados
```

Toda tarefa de toda função menos `owner` o recebe inteiro, assim como as tarefas que o aipager não consegue atribuir a ninguém; o `aipager doctor --safety-check` lista o que está valendo. Depois de uma recarga ao vivo, uma mudança nele vale para as mensagens enviadas depois da recarga; uma tarefa já em andamento, ou uma mensagem que já espera na fila, fica com a lista antiga (pare essa tarefa para aplicar a mudança na hora). Um caminho no `deny_paths_no_access` dali sem `/` nem `~` no começo casa em qualquer lugar, então enquanto houver um, todo `Grep`/`Glob` de toda função menos `owner` é negado (o `aipager doctor` alerta sobre isso). Os caminhos protegidos valem para as ferramentas de arquivo do Claude Code (`Read`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `LSP`, `Grep`, `Glob`); o `Grep`/`Glob` de uma função restrita também precisa ficar dentro da pasta da sessão (uma busca por `aipager/**` em `~/.config` é negada). `WebFetch` não é conferido por caminho; `SendUserFile` (os arquivos que o Claude envia para o chat) é, como um Read, e um membro só envia da pasta raiz do chat ([segurança](security.md#files-claude-sends)). Uma tarefa do Telegram que o aipager não consegue atribuir a um remetente recebe o piso, que é pelo menos tão rígido quanto `user`. O Tentar de novo roda com a função de quem o toca se essa pessoa mandou a mensagem que está sendo repetida, e com o piso senão. Os padrões de comandos só importam para uma função que tem Bash, e ali são um filtro, não um muro; veja [Modelo de segurança → como as regras valem no uso em equipe](security.md#team-mode-enforcement).

## <a id="privacy-considerations"></a>Questões de privacidade

- O bot só escuta **os chats configurados**. Adicionar o bot a outro grupo não o ativa ali: ele nunca responde ali. Ele anota quem se dirige a ele ali (o ID, o nome de usuário e o nome da pessoa, e o ID e o título do grupo) em `~/.claude/aipager-pending-users.json`, para o `aipager config` poder adicionar esse grupo. Só você pode ler esse arquivo, e ele guarda as 200 pessoas mais recentes vistas nos últimos 30 dias.
- Os membros `read_only` **podem ler** as mensagens, as respostas e as entradas das ferramentas. Eles não podem agir, mas veem tudo no grupo. Se algum trabalho precisa ficar escondido de alguém, essa pessoa não deveria estar no grupo.
- Numa instalação com chat privado, as suas sessões de terminal e as do seu chat privado nunca aparecem num grupo (veja [Para onde vão as mensagens](#where-messages-go)). Uma sessão de terminal que um aipager mais antigo já levou para o grupo continua sendo do grupo: encerre-a, exclua-a das sessões encerradas do grupo e inicie-a de novo.
- O `aipager.yaml` tem permissões 0600 (só o dono). O aipager não define as permissões do `policy.yaml`: mantenha-o só para o dono você mesmo (`chmod 600 ~/.config/aipager/policy.yaml`).
- O registro de auditoria (`~/.claude/aipager-audit.jsonl`) não tem permissões próprias: ele é tão privado quanto a sua pasta `~/.claude`.

## <a id="revoking-a-user"></a>Tirar o acesso de um usuário

1. Rode `aipager config` → Edit a member → o chat → a pessoa → Remove member. Para tirar a permissão de mandar mensagens mas deixá-la continuar lendo, use Set role e escolha `read_only`. (Para adicioná-la de volta depois: Edit a scope → o grupo → Add a member.) Para o chat privado de uma pessoa, encerre as sessões dele e depois Edit a scope → o chat privado dela → Remove this scope.
2. O assistente recarrega o aipager ao vivo; ele diz “Scopes reloaded live”. Na hora, as mensagens guardadas dela são descartadas (🤷 e uma resposta “Não foi enviada ao Claude”) e uma tarefa dela que ainda roda fica presa ao piso restrito até terminar. Mande `/stop` para encerrar essa tarefa agora.
3. Tire-a também do grupo do Telegram. Enquanto ela continuar ali, ainda pode ler tudo o que é publicado: cartões, respostas, entradas das ferramentas e pedidos de permissão.

Confira o que o assistente diz depois do passo 1. Se ele disser para reiniciar o aipager, não conseguiu chegar a ele: reinicie-o (`aipager service stop` e depois `aipager service start`), porque até lá a lista anterior de membros continua na memória. Se ele disser “Not applied”, conserte o problema que ele cita: o aipager mantém a configuração anterior até lá. Depois de editar o `aipager.yaml` à mão, mande você mesmo a recarga (veja [Recarregar a configuração sem reiniciar](#live-reload)).

## <a id="related-docs"></a>Páginas relacionadas

- [Arquitetura](architecture.md): como o processo funciona.
- [Comandos do bot](commands.md): referência da interface.
- [Modelo de segurança](security.md): limite de confiança, lista de ameaças.
- [Solução de problemas](troubleshooting.md): referência do `aipager doctor`.
