# Comandos e interface do bot

<!-- i18n-source: docs/commands.md @ 4ce4791d -->

> Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../commands.md))

Como você controla o aipager pelo Telegram. Quatro formas de entrada: os comandos de barra, os botões do teclado, o painel do Mini App e as mensagens livres (texto, arquivos, áudio).

O bot só aceita entradas dos chats configurados; veja [Modelo de segurança](security.md) para o limite de confiança.

## <a id="slash-commands"></a>Comandos de barra

São registrados em `aipager/bot/lifecycle.py`. O menu de comandos do Telegram os mostra, e ele é atualizado quando o aipager inicia e a cada mudança nas sessões.

| Comando | Argumentos | O que faz |
|---|---|---|
| `/start` | - | A tela inicial: as sessões deste chat, para onde vai uma mensagem comum (`✍️ As mensagens vão para x1.`), e `🆕 Nova sessão`, `⚙️ Configurações` e, quando este chat tem uma sessão encerrada para trazer de volta, `↩️ Continuar`. Também mostra o teclado fixo. |
| `/help` | - | Um guia curto por tarefa (começar, falar, controlar, gerenciar, configurações e, para admins, `/update`), com a dica de que tocar num comando o envia na hora: toque e segure (celular) ou pressione Tab (computador) para acrescentar texto antes. Para o dono da instalação (a pessoa do chat privado da própria instalação com o bot) ele também tem um botão `🐞 Relatar um problema`: ele abre nesse chat privado uma prévia do relatório exato, com Enviar, Adicionar uma nota e Cancelar. Nada é enviado até você tocar em Enviar ([o que um relatório contém](problem-reports.md)). |
| `/app` | - | Abre o painel do Mini App (sessões, visualizador de diff, configurações). |
| `/status` | - | As sessões deste chat como uma lista em que você pode agir. Cada uma diz o estado em palavras (`⚙️ trabalhando 3:12 · Bash: run tests`, `⏳ precisa de você · Bash: make deploy`, `💤 pronta`), com `✍️` na que recebe as mensagens comuns, e uma segunda linha com o modelo, o contexto, o custo, o que está na fila (`fila 3 (1 na fila, 2 notas)`), e os agentes e comandos em segundo plano rodando. Cada uma tem uma fileira de botões: `✍️ x1` manda para lá as suas próximas mensagens, `⏹ Parar` enquanto ela trabalha, `Responder` enquanto ela espera por você, e `⋮` para o resto. Embaixo: `🆕 Nova`, e `⚫ Encerradas (n)` para as sessões que terminaram (continuar, excluir, ou tirar todas da lista; elas continuam em `/resume`). |
| `/stop [label]` | opcional | Interrompe a tarefa atual de uma sessão. Sem nome, para a única sessão deste chat que está trabalhando (mesmo que as suas mensagens vão para outra; uma sessão cujo agente ou comando em segundo plano ainda roda conta como trabalhando), pergunta qual com uma lista quando há várias, e diz `Nada rodando agora.` quando não há nenhuma. `/stop x1` (ou `/x1 stop`) para x1 (ou responde `x1 não está fazendo nada agora.` quando ela não está trabalhando). Também descarta as mensagens na fila e diz quantas descartou. |
| `/now` | - | Manda agora mesmo as mensagens que o Claude guarda na fila da sessão ativa, em vez de depois da etapa atual (as próprias teclas de enviar agora do Claude Code, Ctrl+X Ctrl+S). Funciona a qualquer momento, também com o chat silenciado ou no modo mínimo; responde `Não há nada esperando na fila` quando o Claude não guarda nada. Veja [Enviar agora uma mensagem da fila](#send-a-queued-message-now). |
| `/new [name] [first message]` | tudo opcional | Cria uma sessão. `/new x1` a cria na hora, e `/new x1 fix the tests` também manda “fix the tests” como primeira mensagem dela. Enviado sozinho (por exemplo tocado no menu), o `/new` pergunta o nome, e a próxima mensagem que você mandar é usada exatamente como o argumento, então `/new` e depois `x1 fix the tests` dá o mesmo resultado. Num grupo, responda ao cartão com o nome (ou mencione o bot: `@aipagerbot x1`), porque uma mensagem comum ali não chega ao bot. Só essa próxima mensagem: se você fizer outra coisa antes (uma foto, outro comando, uma resposta a outra mensagem, um template do teclado, outro botão), ela é tratada como sempre e o cartão fecha; `/status`, `/settings` e os menus do teclado o deixam aberto. O cartão que pergunta também mostra o modo, o modelo e a pasta que a sessão vai ter, com botões para mudá-los antes. Todos os caminhos terminam no mesmo cartão “x1 está pronta”: o que a sessão recebeu, que a sua próxima mensagem vai para ela, e botões de um toque para passar para outro modo ou trocar o modelo. As sessões novas começam no Modo Automático para todo mundo; mude o que o /new usa em `/settings` → Novas sessões. Um `!` antes do nome inicia a sessão no Modo Acesso total, para um admin (as funções `owner` e `admin`) ou para uma pessoa no próprio chat privado com o bot cuja função pode mandar mensagens e responder ao Claude (`user`, não `read_only`); para qualquer outra pessoa ela começa no Modo Automático e o cartão diz por quê. Um nome que já está em uso oferece `↩️ Ir para ela` (quando essa sessão está rodando) ou `♻️ Continuar` (quando ela terminou), `🆕 Substituir por uma nova` e `↩️ Cancelar`, só para quem mandou o `/new`. O cartão que pede um nome expira depois de 10 minutos sem uso (`⏱ Este cartão de nova sessão expirou. Envie /new de novo.`). Um membro cuja função não tem `can_manage` (`user`, `read_only`) só cria sessões dentro da [pasta raiz](groups.md#root-folders) do chat: a pasta do cartão é a pasta raiz, a página de Pasta lista ela e as pastas logo dentro dela (sem a linha “Pasta padrão”), Nova pasta cria dentro da escolhida, e Ir para ela, Continuar e Substituir numa sessão que trabalha fora da pasta raiz são recusados (“Esta sessão trabalha fora da sua pasta ~/..., então sua função não pode continuá-la, reiniciá-la nem mudar o modo dela. Peça a um admin.”). |
| `/resume [label]` | opcional | Continua pelo nome uma sessão que já terminou, ou abre uma lista para escolher. Uma sessão volta no modo em que estava. Uma sessão que estava no Modo Acesso total só volta no Modo Acesso total para um admin ou para uma pessoa no próprio chat privado; para qualquer outra pessoa ela volta no Modo Automático e a resposta diz isso (`♻️ x1 está rodando de novo no Modo Automático: o Modo Acesso total é só para admins.`). Uma sessão que um aipager mais antigo guardou no Auto antigo dele (que rodava sem verificações) volta no Modo Acesso total, e uma guardada no Ask volta no Modo Perguntar. Uma sessão encerrada fica na lista por `GONE_SESSION_MAX_AGE_DAYS` dias (padrão 14) e depois sai da lista de sessões; a conversa guardada do Claude não é tocada. Um membro cuja função não tem `can_manage` não pode continuar uma sessão cuja pasta está fora da [pasta raiz](groups.md#root-folders) do chat (donos e admins podem); o mesmo vale para Reiniciar e para toda troca de modo, incluindo a troca sem reinício entre o Modo Perguntar e o Modo Automático. |
| `/kill [label]` | opcional | Encerra uma sessão, sempre depois de uma confirmação: `⏹ Encerrar x1? O Claude é interrompido e a sessão é encerrada.` com `[⏹ Encerrar] [Cancelar]`. Sem nome, vai para a confirmação quando este chat tem uma só sessão rodando, senão para uma lista das sessões rodando deste chat. Também está em `/status` → `⋮` → `⏹ Encerrar sessão`. |
| `/restart [label]` | opcional | Reinicia uma sessão depois de uma confirmação: o Claude para e começa de novo, mantendo a conversa, no modo em que o aipager a iniciou pela última vez ou para o qual a mudou. Uma sessão iniciada no terminal com `aipager session` volta no Modo Perguntar, a não ser que tenha sido mudada pelo Telegram desde então (o aipager não sabe em que modo o terminal a iniciou, e nunca supõe um mais aberto); o mesmo vale para continuá-la pelo Telegram. |
| `/rename [label] [new]` | opcional | Dá um nome novo a uma sessão (também às encerradas). `/rename x1 newname` renomeia na hora. Com menos palavras, ele pergunta: direto o nome novo quando o comando só pode se referir a uma sessão, senão uma lista (uma palavra só depois de `/rename` não escolhe a sessão, porque poderia ser o nome antigo ou o novo). Num grupo, responda à pergunta com o nome. |
| `/delete [label]` | opcional | Tira uma sessão encerrada da lista, depois de uma confirmação. |
| `/diff [label]` | opcional | Mostra o git diff da pasta de trabalho da sessão. Sem nome, mostra a sessão para a qual vão as suas mensagens. |
| `/clearqueue` | - | Descarta todas as mensagens que a sessão ativa ainda não pegou (as que o aipager guarda e as que já estão na fila dentro do Claude), sem interromper a tarefa em andamento. Responde com quantas apagou. |
| `/mode [label] [ask\|auto\|full]` | opcional | Mostra o modo de permissão de uma sessão com um botão para passar para cada um dos outros: `✍️ x1 está no 🤖 Modo Automático.` `[💬 Mudar para Modo Perguntar]` `[🔓 Mudar para Modo Acesso total]`. Há três modos. **💬 Modo Perguntar**: o Claude pergunta antes de cada ação. **🤖 Modo Automático** (o padrão): a verificação de segurança do próprio Claude Code deixa rodar as ações seguras e para as arriscadas para você decidir. **🔓 Modo Acesso total**: tudo roda sem nenhuma verificação (`--dangerously-skip-permissions`); só um admin, ou uma pessoa no próprio chat privado com o bot cuja função pode mandar mensagens e responder ao Claude (`user`, não `read_only`), pode escolhê-lo. O Modo Automático e o Modo Acesso total só mudam os pedidos de permissão do próprio Claude Code: o hook do aipager continua aplicando as regras da função do membro. `/mode ask`, `/mode auto`, `/mode full` ou `/mode x1 ask` mudam na hora; a palavra do próprio modo no idioma do chat também funciona, sem a palavra “modo” (`/mode x1 Automático` para 🤖 Modo Automático, `/mode x1 Total` para 🔓 Modo Acesso total). Quando uma sessão rodando neste chat tem exatamente esse nome (uma sessão chamada `demander`, por exemplo), a palavra nomeia a sessão: `/mode demander` mostra o modo dessa sessão, e `/mode x1 demander` continua mudando x1. `ask`, `auto` e `full` sempre querem dizer o modo, em qualquer ordem (`/mode x1 ask` ou `/mode ask x1`); só quando as duas palavras são palavras de modo e só uma delas é o nome de uma sessão rodando, essa nomeia a sessão, então com uma sessão chamada `full`, `/mode full ask` e `/mode ask full` a passam as duas para o Modo Perguntar, enquanto `/mode full` sozinho continua sendo o modo. Passar entre o Modo Perguntar e o Modo Automático acontece sem reiniciar e a qualquer momento (também enquanto o Claude trabalha): o aipager aperta Shift+Tab na sessão como você faria, o cartão diz `⚙️ x1 está mudando para 🤖 Modo Automático sem reiniciar.` até o Claude Code informar o modo novo na etapa seguinte, e depois mostra o resultado. Se o Modo Automático não estiver disponível para a sessão (pelo modelo ou pelo plano), o cartão diz isso e a sessão fica no Modo Perguntar. Não enquanto a sessão mostra uma pergunta ou um pedido de permissão: responda primeiro. Ir para o Modo Acesso total precisa de um admin (ou de uma pessoa no próprio chat privado) e de uma confirmação, e reinicia a sessão (a conversa é mantida); sair do Modo Acesso total também a reinicia, na hora. Uma sessão iniciada no Modo Acesso total reinicia a cada troca, também depois de ter sido passada para o Modo Perguntar ou o Modo Automático no terminal, e o mesmo vale para uma que informou o Modo Acesso total desde que começou (uma sessão de terminal iniciada com `--dangerously-skip-permissions`): o Shift+Tab ali poderia levá-la de volta, no ciclo, ao Modo Acesso total. Numa sessão que está trabalhando, um reinício oferece `🛑 Parar e mudar de modo` / `⏳ Agora não`. Um membro cuja função não tem `can_manage` não pode mudar uma sessão cuja pasta está fora da [pasta raiz](groups.md#root-folders) do chat, nem sem reiniciar nem reiniciando. Tocar numa troca muda o próprio cartão em vez de mandar uma segunda mensagem. `/perms` é o nome antigo e continua funcionando igual. |
| `/settings` | - | Como as sessões deste chat aparecem e respondem: o idioma que o aipager usa, como a resposta aparece, as prévias de diff, a atualização do cartão em tarefas longas, a formatação, o tamanho da resposta, o nível de vocabulário; com o que o `/new` inicia uma sessão; as configurações de cada sessão; e, no chat privado do dono, as sugestões de relatórios de problemas. Veja [Configurações](#settings). Num grupo qualquer pessoa pode olhar, e mudar uma configuração precisa de um admin. |
| `/language` | - | O idioma que o aipager usa nas próprias mensagens e botões neste chat (a mesma lista da primeira linha do `/settings`). Cada idioma aparece com o próprio nome: English, depois Español, Русский (russo), Português (Brasil), 简体中文 (chinês simplificado), Français, Deutsch (alemão), 한국어 (coreano), العربية (árabe), Bahasa Indonesia, Українська (ucraniano), Italiano, Türkçe (turco), فارسی (persa) e Tiếng Việt (vietnamita). Um chat privado que começa depois desta versão segue o idioma do seu app do Telegram; um que já existia continua em inglês até você escolher (veja [Idiomas](#languages)). Em espanhol, português, francês, italiano e indonésio, as palavras do teclado de baixo continuam `status`, `stop`, `new` e `kill`, iguais aos comandos. O alemão e o turco usam `Status`, `Stop`, `Neu…` e `Beenden…`, e `Özet`, `Stop`, `Oluştur` e `Sonlandır`; as palavras em inglês continuam funcionando quando digitadas. Em persa e árabe, o texto do aipager é lido da direita para a esquerda, com algarismos persas ou árabes; os nomes das sessões, os comandos, os nomes dos modelos e o código ficam como estão. A lista diz o que muda: só os textos do aipager, aqui e no Mini App; as suas sessões do Claude não mudam, e o Claude responde no idioma em que você escreve para ele. Num grupo qualquer pessoa pode olhar, e mudar precisa de um admin. `language` é um nome de sessão reservado. |
| `/whoami` | - | Mostra quem você é aqui: no uso em equipe, o seu rótulo, a sua função neste chat e as regras que ela dá a você. |
| `/update` | - | Só para admins (as funções `owner` e `admin`, ou uma função com `can_manage`). Procura versões mais novas do aipager e do Claude Code e depois atualiza, com um botão só, o que tiver uma. Veja [Atualizar](#update). |

O menu `/` mostra primeiro as suas sessões (`/x1 · Falar com x1`) e depois os comandos mais usados: `new`, `status`, `stop`, `now`, `resume`, `mode`, `settings`, `language`, `help` (e `app` com o Mini App ligado, só num chat privado). `kill`, `restart`, `rename`, `delete`, `diff`, `clearqueue`, `perms`, `whoami` e `update` não estão no menu, mas funcionam quando digitados; as ações sobre sessões também estão em `/status` → `⋮`.

**Os comandos que agem sobre uma sessão** (`/stop`, `/mode`, `/kill`, `/restart`, `/rename`, `/delete`, `/diff`) seguem uma regra:

- Com um nome (`/restart x1`) eles agem sobre essa sessão deste chat (o `/rename` precisa para isso do nome antigo e do novo, veja acima).
- Sem nome, eles agem sobre a única sessão a que o comando pode se referir (a única trabalhando para o `/stop`, a única rodando para o `/kill` e o `/restart`, a única encerrada para o `/delete`), passando pela confirmação de sempre quando há uma.
- Senão, eles mostram uma lista só com as sessões deste chat para as quais o comando faz sentido: primeiro a que recebe as suas mensagens, marcada com `✍️`, depois as outras por nome, e `✖️ Cancelar`. Escolher uma leva ao mesmo cartão que digitar o nome dela.
- Num grupo, sem nome, eles só agem sobre a sua própria sessão atual, quando o comando faz sentido para ela; senão, sempre mostram a lista, mesmo com uma sessão só, porque ela pode ser de outro membro (veja [uso em equipe](groups.md#talking-to-a-session-in-a-group)).

O `/mode` e o `/diff` olham primeiro a sessão para a qual vão as suas mensagens.

### <a id="settings"></a>Configurações

O `/settings` (e a aba de Configurações do Mini App, que mostra as mesmas opções) define como as sessões deste chat aparecem e respondem. Cada chat tem as suas; num grupo qualquer pessoa pode olhar, e mudar uma configuração precisa de um admin. No seu próprio chat privado, quem as muda é você, seja qual for a sua função, no Telegram e no Mini App. Uma linha marcada com ✅ está com um valor diferente do padrão (Como a resposta aparece sempre mostra a opção escolhida).

| Configuração | Opções (o padrão primeiro) | O que faz |
|---|---|---|
| 🌐 Idioma | Automático (seu idioma do Telegram), num chat privado; English | O idioma das mensagens e dos botões do próprio aipager neste chat e no Mini App, com o próprio nome (a mesma lista do `/language`). Não muda as suas sessões do Claude: o Claude responde no idioma em que você escreve para ele. São oferecidos English, Español, Русский (russo), Português (Brasil), 简体中文 (chinês simplificado), Français, Deutsch (alemão), 한국어 (coreano), العربية (árabe), Bahasa Indonesia, Українська (ucraniano), Italiano, Türkçe (turco), فارسی (persa) e Tiếng Việt (vietnamita); Automático segue o idioma do seu app do Telegram (veja [Idiomas](#languages)). |
| 🖼 Como a resposta aparece | Cartão + resposta separada; Resposta dentro do cartão; Só a resposta (sem cartão) | Como aparece uma tarefa terminada. Cartão + resposta separada deixa o cartão em cima da resposta, como registro de como se chegou a ela; Resposta dentro do cartão põe a resposta no cartão; Só a resposta tira o cartão e deixa a resposta sozinha. O padrão é Cartão + resposta separada (`KEEP_FINISHED_CARD=0` muda para Só a resposta nos chats que nunca escolheram). Veja [respostas](#idle-responses). |
| 📝 Prévias de diff | Desligado; Ligado | Ligado publica cada `Write`/`Edit` como uma mensagem de diff própria embaixo do cartão de atividade. Desligado deixa um cartão e uma resposta; o cartão continua listando cada edição, e o Mini App tem um visualizador de diff. |
| ⏱ Atualizar cartão em tarefas longas | Ligado; Desligado | Ligado, o cartão de atividade é atualizado a cada 10 s depois de 2 minutos de uma tarefa e a cada 30 s depois de 10 (nunca mais devagar, por mais longa que seja a tarefa), e o relógio dele passa então a andar de 30 em 30 s; Desligado mantém o ritmo dos primeiros minutos durante a tarefa toda (veja [solução de problemas](troubleshooting.md#a-long-turns-card-refreshes-less-often)). |
| ✏️ Formatação simples | Desligado; Ligado | Ligado pede ao Claude só texto simples e listas com traços: sem tabelas, blocos de código, títulos nem negrito. |
| 📏 Tamanho da resposta | Sem nenhuma regra; Extra curta; Curta; Média; Longa | Pede ao Claude respostas desse tamanho. |
| 🎓 Nível de vocabulário | Sem nenhuma regra; Palavras simples; Normal; Avançado | Pede ao Claude esse vocabulário. |
| 🎨 Aparência | Botão Parar, Botões Permitir e Negar, Botão App, Menu de configurações | Como os botões do aipager aparecem neste chat; tudo começa em Normal (a aparência do próprio Telegram). O Telegram deixa um bot pintar um botão de azul, verde ou vermelho e nada mais, então: o botão Parar (o do cartão de atividade e o que fica embaixo de uma pergunta ou de um pedido de permissão) Normal, Azul, Verde ou Vermelho; Permitir e Negar Normal ou Cor (Permitir e Sempre permitir em verde, Negar em vermelho); o botão App (na mensagem fixada e embaixo do `/app`) Normal, Azul, Verde ou Vermelho (o botão de menu ao lado da caixa de mensagem não pode ser pintado); o menu de configurações Normal (✅ marca a opção atual) ou a opção atual também em azul. Vale para o chat todo, não por sessão; num grupo, só um admin muda. Os apps do Telegram que não aceitam cores mostram todos os botões normais. A aba Configurações do Mini App tem as mesmas opções numa seção própria, Aparência, depois de Configurações do chat, cada uma com um ponto colorido. |
| 🆕 Novas sessões | Permissões (o modo), Modelo, Pasta | Com o que o `/new` inicia uma sessão quando você não escolhe: Modo Perguntar, Modo Automático (o padrão) ou Modo Acesso total, um modelo da lista de Modelos ou o padrão do Claude Code, e uma pasta. Só um admin, ou uma pessoa no próprio chat privado com o bot, pode escolher o Modo Acesso total aqui (a tela de um chat privado não o mostra a mais ninguém), e ele nunca vale para alguém que não pode usá-lo: as sessões dessa pessoa começam no Modo Automático. |
| 👤 Configurações de cada sessão | uma sessão, depois qualquer configuração acima menos Novas sessões | Valores só para uma sessão (também em `/status` → `⋮` → `👤 Configurações da sessão`); ⭐ marca uma configuração que ela muda, e “Usar o padrão do chat” tira essa mudança. A página da sessão no Mini App define os mesmos valores. |
| 🐞 Relatórios de problemas | Me perguntar; Desligado | Só no chat privado do dono da instalação. Me perguntar deixa o aipager sugerir de vez em quando mandar um relatório de um erro interno que aconteceu mais de uma vez (no máximo uma vez a cada 3 dias); Desligado nunca sugere (o botão Relatar um problema do `/help` continua funcionando). Duas sugestões recusadas seguidas desligam as sugestões, e então esta linha diz isso, com um botão para religá-las. Veja [relatórios de problemas](problem-reports.md#when-aipager-offers-a-report). |

O tamanho da resposta, o nível de vocabulário e a formatação simples chegam ao Claude como uma instrução curta acrescentada a cada mensagem mandada pelo Telegram (pelo hook, veja [hooks](hooks.md#userpromptsubmit)), usando os valores próprios da sessão quando ela os tem.

Seja qual for a aparência escolhida, todo cartão de atividade termina com a linha de estado da sessão (`⏳`/`✅ nome · …`) e toda resposta começa com a linha de resultado (`💬 nome`, mais `· Concluído (…)` quando não sobra nenhum cartão terminado para mostrar os números); a aparência Resposta dentro do cartão empilha as duas, cada linha na própria seção. Acima da linha de estado do cartão de atividade fica uma linha divisória (em letra pequena), e na aparência Resposta dentro do cartão uma segunda separa o cartão terminado da resposta. Na aparência com cartão, a resposta vem de propósito um instante depois do cartão terminado, para dar para ver o cartão dizer Concluído antes de a resposta chegar embaixo dele; ajuste ou desligue essa vantagem com `FINISH_CARD_GRACE_SECONDS` (segundos, padrão 0,8; 0 manda os dois juntos). Na aparência com cartão, uma tarefa que não chamou nenhuma ferramenta não deixa cartão: o cartão é tirado antes de dizer Concluído, e a primeira linha da resposta traz os números, como na aparência “Só a resposta (sem cartão)”.

### <a id="per-session-dynamic-commands"></a>Comandos de cada sessão

Um comando por sessão rodando, registrado a partir do rótulo dela:

| Forma | O que faz |
|---|---|
| `/<label>` | Faz de `<label>` a sessão para a qual vão as suas mensagens. A resposta diz isso com o estado e o modo dela (`✍️ Agora você está falando com x1 · 💤 pronta · 🤖 Modo Automático`, depois “Envie uma mensagem e ela chega em x1.”), oferece **Responder** quando ela espera por você, e **⋮ Mais** para o menu dela. Os números completos estão no `/status`. Num grupo ela diz “Responda a uma mensagem de x1 (ou mencione @aipagerbot) para falar com ela.”, porque uma mensagem comum ali não chega ao bot (veja [uso em equipe](groups.md#talking-to-a-session-in-a-group)). `/x1@aipagerbot`, que o Telegram manda quando você escolhe `/x1` no menu de um grupo, funciona igual. |
| `/<label> <prompt>` | Manda `<prompt>` direto para essa sessão. Ela também passa a ser a sessão para a qual vão as suas próximas mensagens comuns. |
| `/<label> stop` | Interrompe a tarefa atual dessa sessão. |

Os resultados do `/status` vêm dos mesmos dados que o `aipager status` mostra no terminal; a lista de sessões em si não precisa de uma ida e volta ao Telegram.

No computador, `aipager status` e `aipager session ls` dão a cada sessão um estado: `IDLE`, `BUSY`, `WAITING` (um pedido de permissão ou uma pergunta espera por você, com o que é quando se sabe, por exemplo `permission: Bash: make deploy`) ou `GONE`. Com `--json`, cada sessão também tem `waiting_kind` (`permission` ou `question`) e `waiting_summary`, os dois `null` a não ser que ela esteja esperando.

## <a id="languages"></a>Idiomas

O aipager fala English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی e Tiếng Việt, cada um com o próprio nome.

- **Só os textos do aipager mudam.** O idioma vale para as mensagens, os botões, os cartões e o Mini App do aipager. As suas sessões do Claude não mudam, e o Claude responde no idioma em que você escreve para ele. Os nomes das sessões, os comandos, os nomes dos modelos e o código ficam como estão.
- **`/language`** (ou a primeira linha do `/settings`) escolhe o idioma do chat em que você está. Num chat privado ele também é o seu próprio idioma: os avisos que você vê num grupo e o seu Mini App o usam.
- **Automático** (só nos chats privados) segue o idioma do seu app do Telegram, e o segue de novo se você o mudar. Um idioma que o aipager não tem aparece em inglês.
- **Chats novos e antigos.** Um chat privado que começa depois desta versão segue o idioma do seu app do Telegram até você escolher um, e a primeira mensagem dele num idioma que não seja o inglês vem seguida de uma linha curta dizendo isso e como mudar. Um chat que já existia continua em inglês. Se o seu app do Telegram está num idioma que o aipager fala, você recebe um cartão oferecendo esse idioma, com um botão “Continuar em inglês”; ele vem uma vez só, seja qual for a sua resposta.
- **Os grupos** têm um idioma, definido por um admin com o `/language`; até lá eles ficam em inglês. O aviso depois de um toque fica no idioma de quem tocou: o que a pessoa escolheu no chat privado dela com o bot, senão o idioma do app do Telegram dela para alguém que chegou depois desta versão, senão o idioma do grupo. Veja [uso em equipe](groups.md).
- **Da direita para a esquerda.** Em persa e árabe, o texto do aipager é lido da direita para a esquerda, com algarismos persas ou árabes. O cartão de atividade segue o idioma do chat, seja qual for o idioma em que o Claude escreve: num chat em inglês ele é lido da esquerda para a direita, num chat em persa ou árabe da direita para a esquerda, e uma frase do Claude nele escrita no outro sentido continua sendo lida no seu próprio. A resposta segue o texto do Claude: quando o Claude responde em persa ou árabe num chat cujo idioma é escrito da esquerda para a direita, a resposta inteira é uma mensagem da direita para a esquerda, e as linhas do próprio aipager nela (os agentes embaixo da resposta e, em Resposta dentro do cartão, o cartão terminado acima dela) ficam à direita como o resto e continuam sendo lidas da esquerda para a direita.
- **Palavras de modo.** O `/mode` aceita a palavra do modo no seu idioma (`/mode Perguntar`), e `ask`, `auto` e `full` sempre funcionam. Se uma sessão rodando tem exatamente esse nome, a palavra nomeia a sessão (veja o `/mode` acima).
- **Descrição do bot.** Ao iniciar, o aipager escreve a descrição do bot (o texto na tela do chat vazio) e a descrição curta em cada um destes idiomas, para as pessoas com o Telegram nesse idioma. O Telegram mostra esse texto no lugar do que você escreveu no BotFather, então o aipager não define nenhum enquanto você tem o seu (a descrição e a descrição curta são conferidas separadamente). Ele nunca sobrescreve um texto seu e nunca muda o texto em inglês nem o padrão. Quando o texto atual dele já está definido em todos os idiomas, um início não pergunta nada ao Telegram. Senão, ele primeiro lê o seu texto do BotFather (uma leitura para a descrição e uma para a descrição curta): se você tem um, ele para ali, então com um texto seu cada início custa essas leituras e nada mais. Se você não tem, ele lê cada idioma que ainda não tem o texto atual dele, então um idioma em que você escreveu um texto seu é lido de novo a cada início. Se você escrever uma descrição no BotFather depois de o aipager ter definido a dele, quem lê nesses idiomas continua vendo a do aipager (veja [solução de problemas](troubleshooting.md#the-bot-description-is-not-the-one-i-wrote)).

## <a id="the-mini-app"></a>O Mini App

O `/app` (e o botão de menu do Telegram) abre um painel servido pelo próprio aipager. Ele vem ligado por padrão; gerencie com `aipager miniapp enable|disable|status`. Cada pedido é conferido com a assinatura `initData` do Telegram (veja [segurança → túnel do Mini App](security.md#mini-app-tunnel)). A página não carrega nada de nenhum outro lugar: nem fontes, nem imagens, nem scripts de outros sites.

- **Chats.** O app mostra um chat de cada vez. Se você está em mais de um chat (o seu chat privado e um grupo, por exemplo), uma linha com os nomes dos chats no topo troca entre eles; o app abre no seu próprio chat privado e lembra a sua última escolha nesse celular. Tudo o que tem embaixo é do chat escolhido, com a sua função nele. Com um chat só, a linha não aparece. Veja [uso em equipe](groups.md).
- **Sessões.** Uma frase resume o chat, por exemplo “1 precisa de você, 2 trabalhando, 3 prontas”, com o que foi gasto embaixo. Cada sessão rodando é um bloco com um anel de contexto, a pasta, o modelo e o custo dela; as que estão trabalhando brilham na cor de destaque. As sessões que terminaram ficam numa prateleira **Encerradas** que pode ser recolhida. O botão **+** cria uma sessão nova.
- **Precisam de você.** Uma sessão que espera por um pedido de permissão ou por uma pergunta fica numa faixa âmbar no topo, mostrando o que ela pede. **Responder no chat** manda de novo esse pedido, com os botões dele, para o fim do chat, igual ao botão **Responder** da barra fixada, e você responde ali. Se ela era a única sessão esperando, o app fecha para o pedido ficar bem na sua frente; senão ele continua aberto e diz “Enviado para o chat”. Isso precisa da permissão de mandar mensagens para a sessão, e uma cópia tocada depois de o pedido ter sido respondido ou substituído diz “já respondido”.
- **A página de uma sessão.** O topo mostra o anel de contexto, o estado (“Trabalhando (3:12)”, “Precisa de você (permissão)”, “Pronta, última atividade há 5 min”, “Encerrada”) e um **Parar** de um toque (enquanto ela trabalha ou espera) ou **Continuar** (depois que ela terminou). Embaixo: o pedido que espera, com **Responder no chat**, as últimas chamadas de ferramentas numa faixa (toque nela para a linha do tempo completa), a última resposta, os dados, o controle de modelo, as configurações próprias da sessão, e os arquivos alterados e a linha do tempo. O menu ⋮ tem todas as ações, com as mesmas confirmações de antes.
- **Nova sessão.** Um cartão guiado: o nome, depois onde (com atalhos **Recentes** para as pastas que as suas sessões usam, as mais novas primeiro), depois o modelo (com atalhos **Sugeridos**), depois **Mais opções** para o modo de permissão e o estilo das respostas. O próprio botão do Telegram embaixo diz **Criar sessão**. Para um membro cuja função não tem `can_manage`, o “onde” só oferece a [pasta raiz](groups.md#root-folders) do chat e as pastas logo dentro dela (a pasta raiz é o padrão, e Nova pasta cria dentro da escolhida); uma pasta fora dela é recusada com o motivo, e o Continuar, o Reiniciar e a troca de modo de uma sessão que trabalha fora dela respondem “Esta sessão trabalha fora da sua pasta ~/..., então sua função não pode continuá-la, reiniciá-la nem mudar o modo dela. Peça a um admin.”
- **Configurações.** Primeiro o **Idioma do app**: o seu próprio idioma para os textos do aipager no app e no seu chat privado (inglês, espanhol, russo, português (Brasil), chinês (simplificado), francês, alemão, coreano, árabe, indonésio, ucraniano, italiano, turco, persa ou vietnamita; Automático segue o idioma do seu app do Telegram, veja [Idiomas](#languages)). Qualquer pessoa pode mudar o próprio, ele nunca é publicado num chat, e não muda as suas sessões do Claude. Depois, as configurações do chat (num grupo só um admin pode mudá-las; no seu próprio chat privado você pode, seja qual for a sua função, igual ao `/settings` ali) e, para um admin, **Atualizações**, que espelha o [`/update`](#update). Para o dono no chat privado dele, **Relatar um problema** abre uma página própria: escreva uma nota, confira o relatório, envie e veja a referência dele sem sair do app (veja [relatórios de problemas](problem-reports.md#how-to-send-one)).

O app segue o tema claro ou escuro do Telegram quando você o troca, e para as animações suaves quando o seu celular pede menos movimento. Tudo no Mini App também pode ser feito pelo chat: o menu ⋮ no painel de uma sessão tem as mesmas ações.

### <a id="switching-a-running-sessions-model"></a>Trocar o modelo de uma sessão rodando

A página de uma sessão rodando tem um controle de **Modelo** que mostra o modelo que a sessão informa (pela statusline do Claude Code) e a mesma lista que o seletor de criação e o teclado de Modelos do chat oferecem. Escolher um digita exatamente `/model <name>` nessa sessão, a mesma digitação que o teclado de Modelos do chat usa, com a mesma regra sobre quem pode fazer isso (qualquer pessoa que pode mandar mensagens para a sessão). O controle diz **trocando…** até a statusline informar outro modelo, e então o mostra; se nada mudar em 15 segundos, ele diz **não confirmado (verifique a sessão)**.

- **Só enquanto a sessão está pronta, não enquanto o Claude trabalha ou um pedido está aberto.** O Claude Code roda o `/model` na hora, mesmo no meio de uma tarefa, em vez de depois dela, então a troca é recusada até a tarefa terminar. O teclado do chat recusa nos mesmos estados, com as mesmas palavras.
- **A pergunta “Switch model?” do Claude Code.** O Claude Code costuma fazer essa pergunta antes de uma troca, porque a próxima resposta precisa ler de novo a conversa inteira. Com o Claude Code 2.1.251 ou mais novo, o hook `PreModelSwitch` do aipager aprova a troca que o aipager acabou de digitar, então a pergunta não aparece (veja [hooks](hooks.md#premodelswitch)). Toda outra troca continua perguntando. Com um Claude Code mais antigo, ou numa troca de ou para `opusplan`, a pergunta ainda pode aparecer. A troca então aparece como *não confirmada* e a pergunta espera no terminal. Responda ali antes de mandar qualquer outra coisa para a sessão, senão a sua próxima mensagem seria digitada nela. Pelo mesmo motivo, o aipager recusa outra troca dessa sessão por um minuto depois de uma não confirmada, ou até a sessão informar um modelo novo.
- **Uma mensagem enviada logo depois de uma troca espera.** O Claude Code primeiro confere o modelo com uma requisição (até 15 segundos) e perde o que for digitado nesse meio-tempo, então uma mensagem que você mandar nesse momento fica retida (👀) e vai para o Claude assim que a sessão informar o novo modelo, ou depois de 17 segundos.
- **O modelo também vira o padrão do Claude Code para as novas sessões.** Como com `/model` no terminal, o Claude Code salva o modelo para o qual você trocou no `settings.json` dele, e toda sessão nova sem modelo próprio começa com ele. A resposta no chat, o cartão da sessão nova e o Mini App avisam quando isso acontece. Uma sessão iniciada com modelo próprio (na etapa de modelo do `/new` ou pelos padrões do `/settings`) continua com ele.

## <a id="the-pinned-status-bar"></a>A barra de status fixada

Cada chat em que o aipager fala (o seu chat privado, e cada grupo) tem uma mensagem fixada: a barra de status. A barra do Telegram no topo do chat mostra a mensagem a partir da **primeira linha**, com as linhas emendadas, então essa linha diz o que mais precisa de você:

| Primeira linha | Quando |
|---|---|
| `⏳ jim precisa de você - Bash: make deploy` | uma sessão espera um pedido de permissão, uma pergunta ou outra caixa de diálogo do terminal; `(+2 outras sessões)` quando outras também esperam |
| `⚙️ jim (trabalhando)` / `💤 jim (pronta)` | o chat tem uma só sessão rodando e ela está trabalhando / pronta (`🔄 jim (iniciando)` enquanto ela começa): esta linha é a barra inteira, fora uma linha de limites |
| `⚙️ 2 trabalhando · 1 pronta` | várias sessões, nenhuma esperando: quantas há em cada estado (`trabalhando`, `pronta`, `iniciando`); `🔄` quando nenhuma trabalha e uma está começando, `💤` quando todas estão prontas |
| `💤 todas prontas` | nenhuma sessão rodando |

Embaixo dela, só enquanto vale, uma linha de limites (`🐢 enviando mais devagar depois de um aviso do Telegram` (as seis horas depois de um 429) ou `⏸ os cartões não estão sendo atualizados por enquanto (limite por hora do Telegram)` / `(limite do Telegram)` (modo mínimo, veja [solução de problemas](troubleshooting.md#the-hourly-budget))) e depois, quando o chat tem mais de uma sessão rodando, `✍️ As mensagens vão para jim` (a sessão para a qual vai uma mensagem comum neste chat, veja abaixo; não num grupo, onde cada pessoa tem a sua) e uma linha por sessão com o estado dela: `trabalhando`, `precisa de você`, `pronta` ou `iniciando`. A barra cita cada sessão uma vez: a sessão que espera e que a primeira linha cita não ganha linha própria, e a primeira linha nunca lista nomes que as linhas de baixo repetem. Uma sessão que ainda tem agentes trabalhando em segundo plano diz quantos na própria linha (ou na linha única, com uma sessão só): `💤 jim (pronta, 1 agente trabalhando)`. Os comandos que o Claude roda em segundo plano são contados do mesmo jeito: `(pronta, 1 comando em segundo plano rodando)`, ou `(pronta, 2 agentes e 1 comando em segundo plano rodando)` com os dois. É uma contagem, nunca os nomes deles, então a barra só se mexe quando a contagem muda. Toque na barra para pular para a mensagem.

**Para onde vai uma mensagem comum.** Uma mensagem que não é uma resposta vai para a sessão com que este chat falou por último: a última para a qual você mudou com `/<label>`, para a qual mandou uma mensagem, que criou, ou a que respondeu a você aqui por último. Um `/mode`, `/clearqueue`, `/diff` ou `/now` sem nome age sobre essa mesma sessão (o `/stop` para a que estiver trabalhando). Cada chat tem a sua: uma mensagem num chat nunca vai para uma sessão de outro chat. Num grupo só contam uma resposta ao bot, uma mensagem que o menciona, um comando ou um toque no teclado do grupo (veja [uso em equipe](groups.md#what-the-bot-reads-in-a-group)), e cada pessoa tem o próprio destino, que a resposta de uma sessão não muda (veja [uso em equipe](groups.md#talking-to-a-session-in-a-group)).

Botões da mensagem fixada:

- **Responder &lt;label&gt;** (um para cada sessão que espera, no máximo três) manda de novo o pedido dessa sessão, com os botões de resposta, para o fim do chat, para você responder sem rolar a tela. Se ele foi respondido nesse meio-tempo, você recebe um aviso curto “já respondido” no lugar, e uma cópia (ou o pedido original) tocada depois de o pedido ter sido respondido em outro lugar é recusada do mesmo jeito: ela nunca responde a um pedido posterior. Depois de um reinício do aipager, o pedido que esperava continua funcionando pelo cartão de atividade ou pela própria mensagem, mas o aipager não sabe mais qual pedido uma cópia antiga mostrava, então um toque numa cópia que a barra mandou antes do reinício é recusado com “este pedido não está mais aberto”. Qualquer pessoa que pode responder ao pedido pode usá-lo; mais ninguém.
- **📱 App** abre o Mini App, no seu chat privado, enquanto o Mini App está ligado.

A barra não tem relógio, custo, % de contexto nem modelo, então ela só muda quando o estado de uma sessão muda. O aipager a edita (em silêncio, sem notificação) só quando o que ela mostra mudou, no máximo uma vez a cada 30 s por chat (`PINNED_MIN_EDIT_GAP`); uma mudança dentro desse intervalo aparece quando o intervalo acaba, nunca se perde. Ela nunca é editada enquanto o chat está silenciado por um bloqueio, e se atualiza quando o bloqueio acaba.

Num grupo o bot precisa de permissões de admin para fixar. Se a fixação for recusada, o aipager apaga a mensagem de status que acabou de mandar (para ela nunca ficar sem fixar no histórico do grupo) e não mostra barra nesse grupo até o aipager reiniciar; o mesmo vale para um chat que recusa o bot de vez (o expulsou, o bloqueou). Se você apagar a mensagem fixada, o aipager manda e fixa uma nova, no máximo uma vez por hora por chat.

## <a id="persistent-keyboard"></a>Teclado fixo

Embaixo do campo de digitação do chat fica um teclado fixo. Fileiras, de cima para baixo:

| Fileira | Botões | Observações |
|---|---|---|
| Sessões | um botão para cada rótulo de sessão rodando | montada sozinha a partir da lista de sessões |
| Ações | `status`, `stop`, `new` | atalhos de texto (`kill` continua funcionando quando digitado; encerrar uma sessão fica em `/status` → `⋮`) |
| Navegação | `Templates ›`, `Comandos ›` | O Mini App abre pelo botão de menu ao lado do campo de mensagem ou pelo `/app`, não por este teclado |

`Modelo ›` fica dentro do submenu de Comandos. Tocar num item de um submenu manda uma mensagem pronta ou um comando de barra:

- **Templates**: mensagens que você se pega digitando várias vezes, por exemplo “Escreva testes”, “Explique o plano”, “Atualize a memória”.
- **Comandos**: comandos de barra que o próprio Claude Code entende (`/compact`, `/clear` etc.), digitados na hora. Um comando ou um template cita a sessão para a qual foi (`🧹 /clear enviado para x1`). Limpar a conversa é recusado enquanto a sessão trabalha (`⚠️ Não dá para limpar a conversa enquanto a sessão trabalha`).
- **Modelos**: trocas rápidas de modelo para a sessão ativa. Há os apelidos de família (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`), que sempre querem dizer o modelo mais novo dessa família, e os modelos fixos (`claude-opus-5-5`, `claude-opus-5-5[1m]` com o contexto de 1M, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5`). Os seletores de criação e de sessão do Mini App usam a mesma lista. Uma troca é recusada enquanto a sessão trabalha ou um pedido está aberto. A resposta `🔄` muda depois para mostrar o modelo que a sessão informa, ou diz que a troca não foi confirmada (veja [Trocar o modelo de uma sessão rodando](#switching-a-running-sessions-model)). Qualquer outro modelo se digita pelo nome na etapa de modelo do `/new` ou no Mini App, do jeito que o Claude Code ou o seu provedor escreve: até 128 caracteres entre letras, números e `. _ - : / @ ~ ,`, com `[1m]` permitido no final ([provedores](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider)).

Para mudar o layout padrão, escreva `~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Cada seção é independente: as seções que faltam usam os valores próprios, então você pode mudar uma sem informar as outras. Um JSON quebrado é ignorado com um alerta no log. As mudanças precisam de um reinício do aipager.

## <a id="per-message-inline-buttons"></a>Botões embaixo de cada mensagem

A maioria das respostas do bot traz botões próprios da situação:

### <a id="permission-prompts"></a>Pedidos de permissão

Quando o Claude quer rodar uma ferramenta que precisa de aprovação, o cartão de atividade vira um pedido de permissão:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Permitir]  [❌ Negar]
  [🟢 Sempre permitir]  [⏹ Parar]
```

O cartão mostra o comando (ou o caminho do arquivo) real que o Claude quer rodar, embaixo da descrição que o próprio Claude deu: aprove o que você consegue ler. Um caractere que mudaria a ordem em que o texto é desenhado (um controle de direção do Unicode como o U+202E, que pode fazer um comando parecer outro) aparece como uma marca do tipo `⟨U+202E⟩`, e o cartão então acrescenta `⚠️ Este texto tem caracteres ocultos que mudam a direção da escrita (aparecem como ⟨U+...⟩). Pode executar algo diferente do que parece.`

- **Permitir**: aprova esta única chamada.
- **Negar**: recusa; o Claude não roda a ferramenta.
- **Sempre permitir**: aprova e acrescenta a regra permanente que o Claude oferece (“don't ask again for …”). O botão aparece **só quando o Claude oferece uma regra assim**; para um comando para o qual ele não consegue tirar uma (a maioria dos comandos compostos) o cartão traz Permitir / Negar / Parar no lugar, como faz para uma leitura de arquivo que só lê (`Read`, `Grep`, `Glob`) fora da pasta de trabalho da sessão. O Claude Code 2.1.259 e mais novos põem nesse lugar do próprio diálogo do Bash uma linha “switch to auto mode”, no diálogo de leitura fora da pasta uma linha “block reads outside the working directories from now on”, e, para um Write/Edit para o qual ele não consegue tirar uma regra por arquivo, uma troca de modo de permissão como aceitar sozinho todas as edições de arquivos (`acceptEdits`); o aipager nunca escolhe nenhuma dessas; mude de modo de propósito com o `/mode`.
- **Parar**: interrompe a tarefa em vez de responder.

A resposta chega ao Claude Code pelo hook de permissão do aipager, que espera por ela até 20 segundos. Um pedido que veio antes do cartão de atividade (ele é então mandado como uma mensagem própria, só com Permitir e Negar) é respondido do mesmo jeito. Quando essa espera acaba, os botões digitam a resposta no próprio diálogo do Claude Code; o Negar escolhe então a última linha do diálogo (“No, and tell Claude what to do differently”), que encerra a tarefa do Claude como o Parar, e o cartão diz `🚫 jim · Negado`.

Cada toque é guardado em `~/.claude/aipager-audit.jsonl` e repetido como uma resposta de uma linha embaixo do cartão de atividade: `✅ jim · Permitido · @alice · Bash: List the temp directory` (a descrição da chamada, ou o comando quando ela não tem; com um chat configurado pelo `aipager config`; no uso pessoal só `Permitido`).

Enquanto um pedido (ou um AskUserQuestion) espera por você, a sessão aparece esperando, nunca pronta. Se o Claude Code avisar durante essa espera que a entrada está parada, o chat recebe `⬆️ jim · ainda esperando sua resposta lá em cima` como resposta ao pedido, uma vez por espera.

Os diálogos de `AskUserQuestion` aparecem do mesmo jeito, com um botão por opção (as quatro primeiras), e, para uma pergunta que aceita várias respostas, caixas ⬜/☑ e um botão `✅ Enviar`. A pergunta e a descrição de cada opção aparecem inteiras, como no terminal. Só um formulário longo demais para uma mensagem do Telegram é encurtado: o cartão de atividade primeiro mostra menos linhas de ferramentas, depois os textos mais longos entre a pergunta e as descrições terminam em `…` numa palavra inteira.

**Mais de um pedido.** O Claude Code pode esperar dois pedidos ao mesmo tempo: um agente em segundo plano pede uma permissão enquanto o Claude faz uma pergunta a você, ou o contrário. O aipager mostra cada um deles: o primeiro no cartão de atividade (ou como uma mensagem própria) e cada um dos seguintes como uma mensagem própria abaixo do cartão, com seus próprios botões. A sessão aparece esperando até que o último seja respondido, e uma mensagem que você mandar nesse meio-tempo fica retida até lá. Responda na ordem que quiser. Permitir e Negar passam pelo hook de permissão enquanto ele espera, e ele chega a esse mesmo pedido, onde quer que esteja. Um toque que precisa digitar teclas (uma opção de uma pergunta, ou Permitir e Negar depois que o hook parou de esperar) só as digita no pedido que o Claude Code mostra primeiro: o Claude Code mostra um diálogo por vez, e as teclas chegam ao de cima. Caso contrário, o toque não digita nada, mantém seus botões e diz `O Claude Code está mostrando outro pedido primeiro: responda a esse antes deste.` ou, quando o aipager não sabe a ordem (um agente cujo início ele não viu, por exemplo), `O aipager não consegue saber qual pedido o Claude Code mostra primeiro: responda a este no terminal.` Se o pedido do cartão for respondido primeiro, o cartão mostra que está esperando, só com Parar, e os outros pedidos mantêm seus botões. Se o Claude terminar a tarefa enquanto o pedido de um agente ainda estiver aberto, a resposta chega como sempre e a sessão continua esperando esse pedido, mandado de novo como uma mensagem própria. Um reinício com dois ou mais pedidos abertos não traz nenhum de volta: os botões deles respondem `já respondido` e não digitam nada, e os pedidos ainda podem ser respondidos no terminal. Um único pedido aberto volta como antes.

Com 80% do contexto, o chat recebe `⚠️ name · Contexto em 82% - logo vai ser compactado automaticamente` com **📦 Compactar agora**, que manda `/compact` para essa sessão. O aviso vem uma vez por conversa: compactar não o traz de volta, e uma conversa nova (depois de `/clear`, por exemplo) recebe de novo.

### <a id="stale-buttons"></a>Botões antigos

Os botões que agem sobre uma tarefa em andamento (Parar, Encerrar, Reiniciar, a troca do `/mode` e o Parar e mudar de modo dela, o Substituir do `/new`) ficam presos à tarefa para a qual foram mostrados. Um que sobrou de uma tarefa anterior não muda nada, em vez de agir sobre o que estiver rodando agora, e o mesmo vale para um mostrado antes de a sessão terminar e ser continuada ou reiniciada. Encerrar, Reiniciar, a troca do `/mode`, e o Parar do `/status` ou da lista do `/stop` respondem `x1 começou outra tarefa: …` com o que tocar ou mandar de novo (ou `Essa sessão foi encerrada.`); o Parar do cartão de atividade responde `Essa tarefa já terminou: …`, e o Parar e mudar de modo do `/mode` volta o cartão dele para o cartão do `/mode`, embaixo de `⚠️ x1 começou outra tarefa, então nada mudou.` O Substituir do `/new` diz `x1 começou a trabalhar nesse meio-tempo. Envie /new de novo para substituí-la.` quando a sessão começou uma tarefa depois de o cartão dela ser mostrado.

Os botões de um pedido ficam ligados a esse pedido. Depois que ele é respondido, no Telegram ou no terminal, eles respondem `já respondido` e não digitam nada, também enquanto outro pedido da sessão ainda está aberto.

Um botão `⚡ Enviar agora` só age sobre a mensagem que está em cima dele: se o Claude já pegou essa mensagem, o toque responde `O Claude já pegou`, não manda nada e tira as linhas da fila.

### <a id="idle-responses"></a>Respostas

Quando uma tarefa termina, a resposta chega do jeito que a sua configuração Como a resposta aparece diz (veja abaixo e [Configurações](#settings)). Se a resposta do Claude for maior do que cabe numa mensagem (uns 32 KB), a mensagem mostra o que couber embaixo de uma nota `📎 Resposta completa no arquivo abaixo ↓`, e a tarefa inteira (as etapas e a resposta completa) chega como `<name>_full_log.md`, que o Telegram mostra sem outro app. O mesmo arquivo é anexado quando o cartão de atividade precisou deixar linhas de fora.

Uma tarefa que termina num erro da API da Anthropic (crédito, servidores sobrecarregados, limite de velocidade, rede) mostra `⚠️ name · <what went wrong>` no lugar de uma resposta, com **🔄 Tentar de novo**, que manda de novo a última mensagem para a mesma sessão. Enquanto o chat está [silenciado por um bloqueio](troubleshooting.md#the-bot-went-quiet-flood-control) um toque não faz nada (a mensagem não é mandada de novo e o botão fica), então toque de novo quando o bloqueio acabar.

Na aparência com cartão (“Cartão + resposta separada”) o cartão terminado fica em cima da resposta como registro de como se chegou a ela: as linhas de ferramentas, as linhas de agentes e o que o Claude disse entre elas. A resposta chega como uma mensagem própria (com notificação). Uma tarefa que não chamou nenhuma ferramenta (nenhuma ferramenta, agente, comando em segundo plano, pergunta ou pedido de permissão) deixaria só `✅ name · Concluído · Ns`, então o cartão dela é tirado enquanto ainda diz Trabalhando, sem nunca virar Concluído, e a primeira linha da resposta traz o tempo no lugar dele (`💬 name · Concluído (…)`). Mandar um arquivo para você não conta aqui como chamar uma ferramenta: os arquivos vêm depois da resposta. Uma tarefa sem resposta para mostrar, ou que terminou com um erro da API, fica com o cartão. Para ter uma mensagem só por tarefa, escolha “Resposta dentro do cartão” (a resposta entra no cartão) ou “Só a resposta (sem cartão)” (o cartão é tirado e a resposta fica sozinha).

Cada tarefa tem exatamente um cartão de atividade. Uma mensagem que você manda enquanto uma tarefa roda nunca acrescenta um segundo cartão para essa tarefa, e um cartão cuja tarefa terminou não mantém mais o botão Parar: se nada o fechou (um Stop perdido, um reinício), o aipager o fecha depois de 30 segundos, do jeito que a sua configuração diz. Depois de um reinício do aipager, um cartão cuja tarefa ainda roda é mantido e volta a andar na hora, com o tempo contado desde que a tarefa começou, e a sessão aparece trabalhando até a tarefa terminar; qualquer outro cartão que sobrou de antes do reinício é fechado (“O aipager reiniciou”, sem Parar). Uma sessão que esperava um pedido de permissão ou uma pergunta única do Claude volta esperando, não trabalhando: o cartão dela e a barra fixada dizem que ela precisa de você e os botões do pedido continuam respondendo a ele, a não ser que a conversa guardada mostre que ele foi respondido no terminal nesse meio-tempo. As perguntas com várias partes ou várias opções, e os pedidos feitos por um subagente, ficam para o terminal. Quando se sabe que um pedido acabou (respondido com um toque ou no terminal, a tarefa dele terminou, ou um reinício não o trouxe de volta porque ele foi respondido nesse meio-tempo ou não deu para guardá-lo e lê-lo de volta), os botões de resposta antigos dele respondem “já respondido” e não digitam nada, também depois de um reinício. Um pedido que ninguém responde continua aberto enquanto o Claude estiver esperando por ele. O aipager só para de esperá-lo quando o Claude fica 5 minutos sem dar notícias e a conversa salva mostra que o pedido já passou (a notícia de uma resposta se perdeu); um pedido assim não é dado como acabado, então os botões dele continuam respondendo ao diálogo. Essas notícias são as do próprio Claude e as do agente que pediu: um agente em segundo plano trabalhando por conta própria não conta, e a conversa salva não diz nada sobre o pedido de um agente, que espera as notícias desse próprio agente.

Uma tarefa que o Claude começa **sozinho** (um agente em segundo plano que dá notícias com um `<task-notification>` quando nenhum trabalho está aberto) só ganha o cartão de atividade quando faz alguma coisa: na primeira ferramenta, ou depois de 15 s, o que vier primeiro. A maioria desses despertares são uns segundos de “nada de novo”; esses agora só mostram a resposta. O indicador “digitando…” continua aparecendo enquanto ela roda, e a resposta em si sempre é entregue: nada é filtrado por ser pouco importante. As tarefas que você começa, pelo Telegram ou pelo terminal, continuam ganhando o cartão na hora.

Enquanto uma sessão trabalha, cada agente em segundo plano que o Claude lança (com a ferramenta `Agent` do Claude Code, `Task` nas versões mais antigas) ganha uma linha própria no cartão de atividade: `🤖 <type> · <activity> · <elapsed>`, que mostra o tipo do agente e o que ele está fazendo, atualizada conforme chegam as ferramentas dele. Quando esse agente já usou três ferramentas ou mais, elas se dobram num toque próprio `▸ N ferramentas usadas` logo embaixo da linha dele; elas nunca aparecem na linha do tempo da tarefa principal nem nas contagens `Bash ×N` dela. Quando o agente termina, a linha dele vira `✅ 🤖 <type> · N ferramentas usadas · <elapsed>` e mantém o mesmo toque. Um agente lançado por outro agente (um ajudante) mostra, antes do próprio tipo, o agente do topo da cadeia dele, `🤖 pipeline-runner › ship-tester`, quando o aipager sabe quem o lançou no momento em que ele começa; as linhas continuam na ordem em que os agentes começaram. O anexo `.md` acima ganha uma seção de Agentes com cada agente que rodou na tarefa, o tempo dele, quantas ferramentas usou e quais.

Enquanto os agentes em segundo plano ainda rodam depois de a tarefa deles terminar, o cartão fica como o status do trabalho. Quando o Claude pega uma mensagem nova nesse meio-tempo, o cartão ao vivo é o que está embaixo dessa mensagem, ainda mostrando os agentes rodando. Uma mensagem que você manda para a sessão pronta começa uma tarefa nova: o cartão da tarefa anterior fica parado onde está, com as etapas mantidas, e a tarefa nova ganha o próprio cartão. Uma mensagem que estava na fila e é pega continua a tarefa do trabalho: o cartão dela é mandado de novo embaixo dessa mensagem e a cópia antiga é apagada. As ferramentas usadas dentro de um agente nunca aparecem como linhas da própria tarefa principal, e nunca fazem o aipager achar que uma tarefa nova começou.

Um comando que o Claude roda em segundo plano (`run_in_background`, Ctrl+B, ou um que o Claude Code manda para lá quando ele demora demais) ganha uma linha própria enquanto roda: `⏳ comando em segundo plano: <description> (3:12)`, com o nome tirado da descrição da chamada ou, senão, da primeira linha do comando. Quando ele termina, a linha vira `✅ comando em segundo plano: <description> - concluído (6:12)`, `❌ ... - terminou com erro (código de saída 1)`, ou `⏹ ... - parado (6:12)` (o Claude o parou com TaskStop, ou o Claude Code o parou por falta de memória); as linhas com erro e paradas mantêm essa marca no cartão terminado. Uma tarefa que termina enquanto um comando assim ainda roda não está terminada: o cartão espera como o status do trabalho (`🔄 name · 1 comando em segundo plano ainda em andamento · 4:10`, ou `1 agente (ship-reviewer) e 1 comando em segundo plano ainda em andamento` com um agente também), e quando o comando termina, a continuação do Claude segue nesse mesmo cartão. Os comandos que um agente começou são desse agente e não aparecem. Um comando cujo fim o aipager nunca vê (um reinício, um evento perdido) para de contar depois de duas horas (`AIPAGER_BG_SHELL_MAX_TRACK`, em segundos): a linha dele diz `⏹ ... - sem notícia do fim`, e um trabalho que só esperava por ele fecha com `⚠️ name · Concluído (2:00:04, sem notícias do comando em segundo plano)`.

Quando a linha do tempo de uma tarefa fica longa, cada trecho anterior de ferramentas (três ou mais seguidas, e não o trecho em andamento) se dobra num toque próprio `▸ N ferramentas usadas` bem onde aconteceu, em vez de se acumular inteiro ou ser cortado pelo limite de tamanho de mensagem do próprio Telegram; toque em qualquer um para lê-lo ali mesmo. Os comentários nunca se dobram; a atividade mais recente e a linha de estado sempre aparecem sem precisar tocar em nada. A linha própria de um agente em segundo plano que ainda roda (ou que acabou de parar) nunca se dobra ela mesma, só as ferramentas dela, e nunca enquanto ela for a única coisa entre a linha do tempo e o limite. Só se a linha do tempo for tão grande que nem com tudo dobrado ela caiba, algum conteúdo sai de fato do cartão; nesse caso o anexo `.md` acima tem o registro completo.

#### <a id="agents-still-running-when-the-answer-goes-out"></a>Agentes ainda trabalhando quando a resposta sai

O Claude Code pode rodar agentes em segundo plano, e a tarefa que os lançou pode terminar enquanto eles trabalham. A resposta então termina com uma linha divisória e um status curto desse trabalho em segundo plano, no estilo do próprio painel de agentes do Claude Code, mantido atualizado enquanto ele roda. Tudo embaixo da linha fica em letra pequena (o rodapé do Telegram), para a própria resposta se destacar:

```
⏳ 5 agentes trabalhando · 🖥 1 comando em segundo plano (shell) rodando

○ 🤖 pipeline-runner · Writing final.md for the pipeline

- 🤖 ship-tester · Write and run criteria tests
- 🤖 ship-developer · Editing state.py
- 🤖 ship-developer › ship-reviewer · Collect line numbers

‖ 🤖 r2-runner · esperando os próprios agentes

Quando os agentes terminarem, o resultado vai chegar aqui mesmo.
```

- **A primeira linha conta.** Os agentes trabalhando (ou que terminaram), e os comandos em segundo plano deste trabalho rodando, terminados ou parados. Um comando em segundo plano é sempre só uma contagem, nunca o comando dele.
- **Uma linha por agente,** um 🤖 e o tipo em negrito, e depois o que ele está fazendo agora: a última linha de texto dele (as palavras do Claude, como foram escritas, nunca traduzidas), senão a última ferramenta como uma frase curta que diz em que ele trabalha (`Reading state.py`, `Writing final.md`, `Searching for …`, ou a descrição do próprio comando), senão a tarefa que o Claude deu ao agente quando o lançou, senão só a última ferramenta (`Running a command`, `Using TodoWrite`), senão só o tipo. Assim, dois agentes do mesmo tipo rodando comandos sem descrição ainda mostram cada um a própria tarefa. Um agente que parou para esperar agentes que ele mesmo lançou diz `‖ … esperando os próprios agentes` no lugar.
- **Agentes em árvore.** Os agentes que a sessão lançou são o primeiro nível. Cada agente que um deles lançou, em qualquer profundidade, aparece logo embaixo dele, cada um logo depois do agente que o lançou; um lançado por um agente do segundo nível mostra esse agente primeiro (`ship-developer › ship-reviewer`). O cartão de atividade mostra o mesmo agente depois do agente do topo (`pipeline-runner › ship-reviewer`). Nunca há mais de dois níveis. Um agente trabalhando no segundo nível não tem ○ próprio: o Telegram desenha um marcador ali. Um agente cujo lançador o aipager ainda não conhece aparece no primeiro nível, nunca embaixo de um palpite, e passa para baixo do lançador assim que ele é conhecido.
- **No máximo seis linhas,** primeiro os agentes que ainda estão trabalhando, depois `+N outros agentes` para os agentes sem linha. Um agente nunca aparece sem os agentes acima dele.
- **A promessa** “Quando os agentes terminarem, o resultado vai chegar aqui mesmo.” só fica enquanto algum agente ainda trabalha. Os comandos em segundo plano nunca a trazem: um servidor de desenvolvimento pode nunca terminar.

A linha de cada agente para sozinha quando esse agente para, como `✓ 🤖 Explore · concluído (0:42)` (quanto tempo esse agente rodou), e a primeira linha desconta. Um agente que um agente da lista lança depois de a resposta sair entra no status embaixo dele na próxima atualização; um que a própria sessão lança mais tarde aparece embaixo de uma resposta seguinte. Quando todos os agentes terminam, as linhas dão lugar a uma linha só, com o tempo da resposta até o fim do último agente:

```
✅ 2 agentes terminaram (6:12) · 🖥 1 comando em segundo plano (shell) rodando
```

e quando esse comando termina, `✅ 2 agentes terminaram (6:12) · 1 comando em segundo plano (shell) terminou (2:13:05)` (quanto tempo o comando rodou; `parado` quando foi encerrado ou parado pelo Claude, `terminou` quando acabou sozinho, com erro ou não; a linha dele no cartão diz como). Uma resposta só com um comando em segundo plano do trabalho dela mostra `🖥 1 comando em segundo plano (shell) rodando`, que vira `1 comando em segundo plano (shell) terminou (…)` ou `1 comando em segundo plano (shell) parado (…)`.

Só os comandos em segundo plano do próprio trabalho da resposta são contados: um servidor de desenvolvimento que uma tarefa anterior deixou rodando não se repete embaixo de cada resposta (a barra fixada continua contando todos). O `/stop` encerra o trabalho, mas o Escape não encerra um comando em segundo plano: um que ainda roda fica na barra fixada até terminar, e para de ser contado embaixo das respostas seguintes; a continuação dele, mais tarde, começa então uma tarefa própria.

Essa resposta sai no momento em que a tarefa termina, como uma mensagem normal (com notificação) em resposta à sua mensagem: o mesmo texto que o terminal mostra. O cartão de atividade fica em cima dela como o status ao vivo do trabalho, em todas as aparências: `🔄 name · 1 agente (general-purpose) ainda em andamento · 1:18`, com o botão **Parar**. Quando os agentes dão notícias, a resposta do Claude a isso chega como uma mensagem própria; a resposta anterior nunca é mandada de novo, nem com um reinício do aipager. Um trabalho cujos agentes dão notícias mais de uma vez produz uma resposta por notícia, cada uma mandada conforme é escrita.

Um agente que parou enquanto um trabalho dele em segundo plano ainda roda conta como trabalhando, porque ele continua mais tarde. Um agente que começou um agente próprio e depois parou para esperar por ele é reconhecido por esse começo (a própria chamada dele que iniciou o outro agente): ele fica no status como trabalhando, e mantém o trabalho aberto, até parar sem nenhum agente próprio ainda rodando. Se ele não continuou dois minutos depois que o último deles terminou, deixa de contar como rodando e o status fica como foi mostrado por último. O `<task-notification>` do Claude para um agente diz o mesmo numa nota; o aipager a lê quando essa notificação começa uma tarefa, e também quando o Claude a pega no meio de uma tarefa em andamento enquanto o cartão de atividade está na tela. Uma linha espera alguns segundos antes de aparecer como concluída, para essa nota poder desfazer isso antes. O status é acrescentado em todas as aparências, no idioma do chat.

**Como ele é mantido atualizado.** O aipager edita a resposta em silêncio, só quando o que o status mostra mudou, e no máximo uma vez a cada 30 segundos por resposta (a própria resposta conta como a primeira). Cada resposta com status é mantida atualizada assim (até cinco pendentes por sessão). As edições têm prioridade baixa: uma que a cota de envios recusa, ou que cairia enquanto o chat está silenciado por um bloqueio, não é feita, e o status espera a próxima mudança. A edição final, quando tudo terminou, é tentada mais uma vez depois que o bloqueio acaba ou que a cota a recusa, e depois é abandonada. Nenhuma edição é feita numa resposta apagada, para uma sessão que terminou ou foi encerrada, ou quando ela deixaria a resposta comprida demais para o Telegram (quando uma resposta longa é cortada, sobra espaço para o status).

O status fica como foi mostrado por último, sem virar concluído, quando o aipager não tem como saber se o trabalho terminou:

- um agente de quem o aipager não ouve nada por 30 minutos (`AIPAGER_SUBAGENT_SILENCE`) deixa de ser contado como rodando, mas silêncio não é conclusão;
- um comando em segundo plano que roda há mais de duas horas (`AIPAGER_BG_SHELL_MAX_TRACK`) sem que o fim dele seja visto deixa de ser contado;
- um reinício do aipager esquece quais respostas estão pendentes.

Um reinício do aipager guarda os agentes que ainda estão rodando, inclusive os que esperam pelos próprios agentes: uma resposta mandada depois do reinício lista esses agentes, um cartão de atividade que o reinício retoma os mostra de novo, e o fim deles é reconhecido como sempre. Agentes salvos mais de 30 minutos antes do reinício não voltam.

Uma resposta segurada por um bloqueio, ou entregue como texto simples depois de o envio com formatação falhar, sai sem o status, assim como uma tarefa que termina sem texto de resposta (só um cabeçalho).

### <a id="end-confirmation"></a>Confirmação de encerrar

O `/kill`, a lista dele e `⋮` → `⏹ Encerrar sessão` sempre pedem confirmação:

```
⏹ Encerrar jim? O Claude é interrompido e a sessão é encerrada.

  [⏹ Encerrar]  [Cancelar]
```

Depois de `⏹ Encerrar`, a mensagem diz `⏹ jim encerrada` em cima das sessões que sobram neste chat. Uma confirmação tocada depois de a sessão começar outra tarefa é recusada (`jim começou outra tarefa: …`), e o mesmo vale para uma de uma sessão com o mesmo nome criada depois.

### <a id="voice-install-when-extra-isnt-installed"></a>Instalar a voz (quando o pacote não está instalado)

Quando você manda um áudio e o `aipager[voice]` não está instalado:

```
⚠️ Para áudios é preciso um pacote opcional
   (uns 200 MB de instalação e uns 74 MB de modelo no primeiro uso).

  [📦 Instalar recurso de voz]  [Cancelar]
```

Tocar em Instalar roda o comando de instalação certo para o seu instalador (`uv tool install --reinstall aipager[voice]`, `pipx install --force`, ou, como último recurso, `pip install faster-whisper`) com uma edição da mensagem a cada 5 s, e quando dá certo oferece um botão `[🔄 Reiniciar o aipager]`.

### <a id="restart"></a>Reiniciar

`🔄 Reiniciar o aipager`:

- Um aipager que roda como serviço de usuário do systemd agenda um `systemctl --user restart aipager.service` separado para 5 s depois (com um temporizador de 1 s de precisão), numa unidade temporária fora do grupo de processos do próprio aipager, para ela sobreviver ao fim do aipager. Ele recusa enquanto o serviço encerraria as suas sessões (`KillMode` diferente de `process` ou `none`), e diz para você rodar antes `aipager service install`.
- macOS, quando o agente do launchd está instalado (`~/Library/LaunchAgents/com.aipager.daemon.plist`): `launchctl kickstart -k gui/<uid>/com.aipager.daemon`, mesmo para um aipager que você iniciou por conta própria.
- Senão, um aipager que você iniciou por conta própria (`aipager start`), no Linux mesmo quando o serviço também está instalado: ele lança um substituto separado que espera o processo pai morrer e depois roda o mesmo comando com que o aipager foi iniciado (a saída dele vai para o log do reinício próprio). O aipager atual para de forma limpa assim que o substituto está vivo.

Não precisa de SSH. Para concluir uma atualização do aipager, o **🔄 Reiniciar agora** do cartão de atualização reinicia o aipager no lugar, no mesmo terminal (veja [Atualizar](#update)).

## <a id="update"></a>Atualizar

O `/update` (só para admins; no uso pessoal, só quem instalou o aipager) responde com um botão, **🔄 Verificar atualizações**. Nada é procurado até você tocar nele. O toque transforma essa mensagem numa linha por produto:

- `aipager 1.0.1 → 1.1.0` quando existe uma versão mais nova (a mais recente do PyPI);
- `aipager 1.0.1 (atualizado)` quando não existe;
- `Claude Code 2.1.282 (não foi possível verificar)` quando a busca falhou (rede fora do ar, limite de 5 s, ou `claude` não encontrado).

O Claude Code é comparado com a versão mais recente do próprio canal de atualização dele (`autoUpdatesChannel`: latest, stable ou rc). Embaixo das linhas, em letra pequena, vem como o aipager foi instalado (por exemplo `pipx, from PyPI` ou `pipx, from local path …`; os chats de grupo nunca mostram caminhos).

Se houver algo mais novo, há UM botão que atualiza só os produtos que têm atualização: **Atualizar aipager**, **Atualizar Claude Code** ou **Atualizar ambos**, mais **Cancelar**. Quando o aipager é oferecido, a mensagem também diz como vai ser o reinício: `Reinício: automático, quando nenhuma tarefa estiver em andamento.`, `Reinício: toque em “Reiniciar agora” quando estiver instalado.` (um aipager sem serviço, veja abaixo) ou `Reinício: manual (motivo)`.

Se um aipager mais novo já estiver instalado, mas o aipager rodando ainda for o antigo (uma atualização que terminou sem reinício, ou um `pip install -U` feito à mão), a linha diz `O aipager 1.1.0 está instalado e esperando um reinício (rodando agora: 1.0.1).` e a mensagem oferece **🔄 Reiniciar agora** no lugar de uma atualização: Atualizar aipager nunca é oferecido para uma versão que só precisa de um reinício. A versão instalada é verificada iniciando o interpretador em que o aipager reiniciaria, então uma versão instalada que não carrega diz isso (`O aipager 1.1.0 está instalado, mas não carrega. Reinstale com …`) e não oferece nada. Uma versão publicada mais nova que a instalada continua sendo oferecida como atualização. Um aipager mais novo numa instalação que não pode ser atualizada daqui (editável, Nix, Snap, um pacote do sistema, um contêiner, ou a instalação de outro usuário) aparece com o motivo e não é oferecido. O mesmo vale para um Claude Code mais novo instalado com npm numa pasta em que o seu usuário não pode escrever (por exemplo `sudo npm install -g` em `/usr/lib/node_modules`), onde o `claude update` só poderia falhar: a linha diz `não pode ser atualizado daqui: installed with npm into a folder your user can't write to; run "claude install" once to switch to Claude Code's own installer, then restart aipager`. Se nada puder ser atualizado, a mensagem diz “Tudo está atualizado.”, diz que uma verificação falhou, ou, quando existe algo mais novo que não pode ser instalado daqui, “Há uma atualização nova, mas ela não pode ser instalada daqui.”, com um botão **Verificar de novo** e nenhum botão de atualizar.

O botão de atualizar só começa o que a verificação ofereceu. Se a verificação tiver mais de 10 minutos, outra atualização tiver rodado desde então, ou as versões não baterem mais com o botão, ele pede para você verificar de novo. Os botões de um menu `/update` mais antigo (os **Atualizar Claude Code** / **Atualizar aipager** / **Ambos** por produto) respondem “Este menu está velho, envie /update de novo” e não fazem nada. Cada toque confere de novo a regra de admin.

Atualizar o Claude Code roda `claude update` (pelo caminho completo, com limite de 5 min) e informa `Claude Code A → B`, “já está atualizado”, ou a falha com o fim da saída dele. As sessões rodando continuam com a versão antiga até você reiniciá-las (`/restart`); a resposta as lista. Nenhuma sessão é reiniciada por você. As sessões novas usam a versão nova.

Atualizar o aipager (**Atualizar aipager**, ou a segunda metade de **Atualizar ambos**, que atualiza primeiro o Claude Code):

1. Numa instalação pelo PyPI que já está atualizada, ele diz isso e para.
2. Se o aipager consegue reiniciar sozinho, ele primeiro **espera até não haver nada em andamento**: nenhuma sessão rodando uma tarefa, esperando uma pergunta, rodando um agente em segundo plano, mostrando um cartão de atividade ao vivo, rodando uma ferramenta ou com um pedido de permissão aberto, e nenhuma resposta segurada esperando um limite de velocidade passar. A mensagem lista o que ele está esperando, com **Reiniciar agora** (pular a espera) e **Cancelar**. Depois de 10 minutos ele pergunta de novo: **Aguardar mais 10 min**, **Reiniciar agora**, **Cancelar**. Sem resposta por uma hora, ele se cancela sozinho.
3. Ele atualiza pelo instalador que é dono do aipager rodando (`pipx upgrade aipager`, `uv tool upgrade aipager`, `brew upgrade aipager`, ou `<venv>/bin/python -m pip install --upgrade aipager`), pelo caminho completo, com limite de 10 min. Não há Cancelar enquanto o instalador roda.
4. Ele confere se a versão nova carrega num interpretador novo. Uma atualização que falhou, passou do tempo ou não carrega não reinicia nada. Uma atualização que passou do tempo foi parada no meio, então a mensagem avisa que a instalação pode estar incompleta e dá o comando de reinstalação para rodar antes do próximo reinício. A saída do instalador só aparece num chat privado; um grupo recebe “(a saída está no log do aipager)”.
5. Se uma tarefa começou durante a atualização, ele espera de novo.
6. Ele agenda um `systemctl --user restart aipager.service` separado para 5 s depois (com um temporizador de 1 s de precisão), e a mensagem de status diz `⏳ aipager A → B instalado, reiniciando…`. Enquanto o passo 5 espera, ela diz `⏳ aipager A → B instalado; reinicia quando a tarefa atual terminar`. Depois do reinício, o aipager novo transforma essa mesma mensagem em `✅ aipager atualizado A → B; N sessões se reconectaram` (e `⚠️ Não voltaram: …` para cada sessão que não voltou). Se a mensagem foi apagada ou não pode mais ser editada, ele publica o resultado como uma mensagem nova no chat que pediu.

No Mini App, **Configurações → Atualizações** mostra “Reiniciando o aipager…” com uma lanterna girando até o aipager novo responder, e depois “Atualizado para B; N sessões se reconectaram.”. Ele nunca mostra uma contagem regressiva. Depois de uns 2 minutos sem resposta (nunca antes de o aipager antigo poder ter informado um reinício que não aconteceu) ele diz “Ainda reiniciando; abra o app de novo em instantes.”. Com o túnel gerenciado do aipager, o reinício de uma atualização mantém o endereço do app (o aipager novo assume o túnel), então a página aberta segue de pé e mostra “Atualizado para B”. Se no fim o túnel não pôde ser mantido, a página diz “Ainda reiniciando; abra o app de novo em instantes.” depois de uns 2 minutos. Uma atualização iniciada por um aipager sem isso (ou com `MINIAPP_KEEP_TUNNEL=0`) ainda muda o endereço: quando o aipager antigo para de responder, a página aberta diz “O aipager reiniciou; abra o app de novo quando o chat disser que ele foi atualizado”; feche e abra de novo pelo botão de menu do chat ou pelo `/app`. O login do app dura 5 minutos, então uma página aberta há mais tempo diz “Abra o app de novo para ver o resultado da atualização.” no lugar.

Depois da atualização, o aipager só reinicia sozinho quando roda como serviço de usuário do systemd **e** esse serviço tem `KillMode=process` (ou `none`). Com um `KillMode` inseguro, o aipager é atualizado mesmo assim, e a mensagem diz `É preciso reiniciar: …`: rode antes `aipager service install` (ele lista as sessões que um reinício encerraria).

**Reiniciar agora, sem serviço.** Um aipager que nenhum gerenciador de serviços reinicia (`aipager start` num terminal ou tmux, Docker, ou o agente do launchd no macOS) instala a atualização e depois mostra `Toque em “Reiniciar agora” para rodá-la (o aipager espera até nenhuma tarefa estar em andamento).` com um botão **🔄 Reiniciar agora**. Tocar nele (só admins, como todo botão de atualização):

1. verifica se o aipager instalado carrega (`⏳ Verificando o aipager instalado…`); uma instalação quebrada não reinicia nada, e a mesma versão diz que não há nada para reiniciar;
2. espera, como o reinício do systemd, até não haver nada em andamento, com **Reiniciar agora** (pular a espera) e **Cancelar**;
3. verifica a instalação de novo, depois diz `⏳ aipager A → B instalado, reiniciando…` e para o aipager de forma limpa (como o Ctrl+C);
4. inicia o aipager novo no lugar: o mesmo processo, o mesmo comando, o mesmo ambiente e a mesma pasta, na mesma janela do terminal ou do tmux (e, no Docker, o mesmo processo do contêiner). As sessões continuam rodando. O aipager novo edita a mensagem para `✅ aipager atualizado A → B; N sessões se reconectaram`.

Toques enviados durante os poucos segundos do reinício se perdem, como em qualquer reinício. Um Ctrl+C ou um `docker stop` durante o reinício ganha: o aipager para e não começa de novo. Se o aipager novo não puder ser iniciado no lugar, uma cópia separada o inicia quando o antigo já tiver saído (a saída dela vai para o log do reinício próprio, `aipager.log` na pasta da instância ou `/tmp/aipager.log`, e o log do aipager diz o comando a rodar caso essa cópia não volte); se nem isso der certo, ou se o próprio interpretador sumiu, o aipager sai com o código 1 e o log do aipager diz `restart.failed:` com o comando a rodar.

Quando o aipager não consegue se reiniciar de jeito nenhum, a mensagem dá os passos exatos para a sua instalação no lugar de um botão:

- um terminal ou tmux: `Na janela do terminal ou do tmux onde o aipager roda, aperte Ctrl+C e depois execute aipager start.`
- macOS: `No Mac, execute launchctl kickstart -k gui/<uid>/com.aipager.daemon.`
- Docker: `No computador onde o Docker roda, execute docker restart <container>.` (o id curto do contêiner quando o nome do host dele é um), com um lembrete de que recriar o contêiner a partir da imagem traz de volta a versão do aipager dessa imagem.

**Reiniciar agora** também aparece no resultado de **Verificar atualizações** quando um aipager mais novo está instalado e esperando, e num aviso de uma vez só: quando os hooks do Claude Code (que começam do zero a cada evento, a partir do aipager instalado) já rodam um aipager mais novo que o que está rodando, o dono recebe uma mensagem silenciosa por versão mais nova e por execução do aipager, `⚠️ A atualização do aipager está pela metade: …`, com o botão. Ela espera dois minutos a partir de quando os hooks rodaram a versão mais nova pela primeira vez (um reinício logo depois de uma instalação nunca a mostra), e depois até nenhuma tarefa estar em andamento no chat privado do dono. Depois do reinício nessa versão, o aviso é editado para `✅ A atualização do aipager terminou: o aipager agora está no B.`, sem o botão. No Mini App, **Configurações → Atualizações** mostra o mesmo estado como uma linha acima do cartão, com **Reiniciar agora** (ele pergunta antes).

No Mini App, **Atualizações** é o primeiro cartão da aba Configurações: o nome, a versão em execução e um pequeno **Verificar atualizações** no fim da linha. Abrir a aba Configurações verifica sozinho (no máximo uma vez a cada dez minutos, nunca enquanto uma atualização roda ou o aipager reinicia; uma verificação que falha ali não diz nada), e um toque sempre verifica. O cartão fica numa linha discreta até haver algo a fazer: uma versão mais nova traz as linhas dela e o botão único de atualizar, “Tudo está atualizado.” é uma linha só sem botão, e uma atualização em andamento, um reinício ou **Reiniciar agora** mantêm os próprios botões.

Só uma atualização roda de cada vez, entre o `/update`, o cartão **Configurações → Atualizações** do Mini App (a mesma verificação, o mesmo botão único de atualizar, o mesmo trabalho) e o `aipager update` no terminal. Isso inclui os segundos entre “instalado, reiniciando…” e o reinício em si: o `/update` responde que o aipager está prestes a reiniciar, o Mini App não oferece botões, e o **Reiniciar o aipager** do recurso de voz recusa enquanto uma atualização roda ou espera para reiniciar. Se o aipager ainda estiver vivo dois minutos depois de agendar o reinício, ele para o temporizador de reinício pendente, libera a trava de atualização e diz para você reiniciá-lo por conta própria.

Se o aipager desligar enquanto um instalador roda, o instalador tem 3 s para terminar e depois é parado (senão ele continuaria escrevendo a instalação enquanto o próximo aipager inicia). A mensagem de status e, depois do reinício, uma mensagem nova dizem que a instalação pode estar incompleta e como consertá-la.

Depois que um desligamento começou, nada é reiniciado e nada novo começa: os botões do `/update` respondem que o aipager está desligando, o Mini App responde 503, e nenhum instalador nem verificação de versão é lançado. Um instalador que termina dentro dos 3 s dele ainda conta. A mensagem de status diz que a versão nova está instalada e que nada foi reiniciado, e o próximo início anuncia `aipager atualizado A → B`. Um aipager que você parou com `aipager service stop` continua parado. A parte da atualização no desligamento leva no máximo 8 s no total.

## <a id="free-messages"></a>Mensagens livres

### <a id="text"></a>Texto

É tratado como a próxima mensagem para a **sessão ativa**: o destino deste chat, a sessão para a qual você mudou por último, para a qual mandou uma mensagem, que criou, ou a que respondeu a você aqui por último (veja [a barra de status fixada](#the-pinned-status-bar)). Num grupo cada pessoa tem o próprio destino, que a resposta de uma sessão não muda (veja [uso em equipe](groups.md#talking-to-a-session-in-a-group)). As mensagens chegam ao Claude **na hora**, mesmo enquanto a sua própria tarefa roda, exatamente como digitar no terminal (num grupo, a mensagem de outra pessoa espera essa tarefa terminar; veja abaixo). Mande várias e elas entram na fila dentro do próprio Claude, que pega cada uma num ponto natural:

A reação na sua mensagem acompanha o caminho dela, como no Claude Code uma mensagem na fila passa de cinza para branca:

- 👀: entregue à sessão (ou guardada, veja abaixo), mas o Claude ainda não a pegou. Uma mensagem mandada enquanto uma tarefa roda fica em 👀 enquanto espera na fila do Claude.
- 👍: o Claude a pegou: começou uma tarefa com ela, a juntou à tarefa em andamento, ou a passou para um agente em segundo plano que está rodando.
- 🤷: ela nunca vai ser pega: uma mensagem guardada que `/stop`, `/clearqueue` ou `/kill` descartaram, ou que não pôde ser mandada quando foi liberada; uma mensagem ou um comando que ainda esperava quando `/stop`, `/clearqueue`, `/kill` ou o fim da sessão (não `/clear` nem `/resume`) a descartaram; ou uma mensagem que o Claude Code recusou (veja abaixo). As mensagens que o Claude já tinha na fila só são marcadas enquanto o aipager consegue ver essa fila (a leitura ao vivo da conversa guardada está rodando e nenhum trabalho em segundo plano está esperando); senão, elas ficam em 👀. O Escape no terminal devolve a fila do Claude para o campo de digitação, de onde ela pode ser mandada de novo, então uma mensagem descartada assim também fica em 👀.
- 👌: um comando do Claude Code que rodou (veja abaixo), ou o aipager confirmando um `/stop`.

Uma reação só anda para a frente, então uma mensagem recebe no máximo três, cada uma uma vez. Uma reação que cai dentro de um bloqueio por limite de velocidade do Telegram é pulada, não repetida depois, e um encerramento marca no máximo as dez mensagens mais recentes que descarta.

Botões de comando (`Compactar`, `/model …`): tocados com a sessão pronta, o comando recebe 👌 na hora; o Claude Code o roda com Enter, e um comando local como o `/model` não dispara nenhum hook que pudesse dizer isso depois. Tocados enquanto uma tarefa roda (não o `/model`: ele é recusado até a tarefa terminar, veja [trocar o modelo de uma sessão rodando](#switching-a-running-sessions-model)), ele fica em 👀 até essa tarefa terminar (normalmente, como pausa de um trabalho em segundo plano, ou com um erro da API) e depois 👌; descartado antes disso por `/stop`, `/clearqueue`, `/kill` ou pelo fim da sessão, ele nunca rodou: 🤷. Quando o Claude Code o põe na fila como mensagem, ele segue o caminho 👀 → 👍. Um áudio recebe as mesmas reações que o texto depois que o texto dele é mandado.

O cartão de atividade e a resposta final acompanham a mensagem que o Claude de fato pegou para uma tarefa (a que começou a tarefa se a sessão estava pronta, ou a que ele juntou à tarefa em andamento), nunca simplesmente a última mensagem que você mandou. Mandar outra mensagem no meio de uma tarefa não “puxa a resposta” para ela: se o Claude a junta à resposta que já está formando, o cartão pula para ela no momento em que isso acontece; se o Claude termina primeiro e depois a pega, a primeira resposta fica embaixo da primeira mensagem e a seguinte ganha uma tarefa própria, com cartão e resposta próprios embaixo dela.

Se o Claude Code recusar uma mensagem de vez (um comando de barra desconhecido, ou um comando próprio que só abre um diálogo no terminal), nenhum hook dispara, então nada encerraria a tarefa que o aipager acabou de anunciar. Depois de 8 s sem nenhum hook (`PROMPT_HOOK_GRACE_SECONDS`), o cartão de atividade vira `⚠️ name · O Claude Code não recebeu a mensagem` com uma explicação de uma linha, e a sessão fica pronta de novo; o motivo está no terminal, e a mensagem recebe 🤷 (um comando de barra recebe 👌 no lugar: um comando próprio que abre um diálogo também não dispara nenhum hook). Só uma mensagem que começou uma tarefa é julgada assim; uma que está na fila atrás de uma tarefa em andamento mantém o 👀 até o Claude pegá-la ou ela ser descartada.

Três casos são guardados em vez de mandados, e entregues sozinhos quando se resolvem:

- Um pedido de permissão ou uma pergunta está aberto, por todo o tempo em que continuar aberto (não há limite de tempo): senão o seu texto seria lido como resposta a esse diálogo. Sua primeira mensagem recebe então uma resposta, embaixo do pedido, dizendo que o Claude ainda está esperando sua resposta (as seguintes só recebem o 👀). Assim que você responde, no Telegram ou no terminal, as mensagens retidas vão para o Claude na ordem em que você as enviou e essa resposta é apagada. Se a sessão terminar com o pedido ainda aberto, elas são descartadas com um 🤷.
- (Uso em equipe) a mensagem de outra pessoa ainda espera para ser pega.
- (Uso em equipe) a tarefa de outra pessoa está em andamento, incluindo um agente em segundo plano que ela começou. Uma tarefa digitada no terminal conta como do dono, então só a mensagem de um dono (ou, num chat privado, a do próprio membro dele) entra nela.

Os dois últimos mantêm as mensagens de pessoas diferentes fora de uma mesma tarefa, para cada uma rodar com as permissões de quem a mandou. Se mesmo assim uma entrar (por exemplo de um remetente que o aipager não conhece, que nunca é guardado), a tarefa roda com as regras mais rígidas de todos que estão nela (veja [uso em equipe](groups.md#who-a-message-runs-as)). Se a tarefa que uma mensagem espera for parada, ou parar por causa de uma ferramenta bloqueada, as mensagens guardadas são descartadas com um 🤷.

As mensagens guardadas têm um limite de 50 por sessão e expiram depois de 24 h; o `/clearqueue` as descarta junto com o que o Claude estiver guardando.

#### <a id="send-a-queued-message-now"></a>Enviar agora uma mensagem da fila

Uma mensagem que você manda enquanto o Claude trabalha na sua própria tarefa vai para o Claude na hora (o aipager não a segura) e espera na fila do Claude até a etapa atual terminar. Assim que a fila do Claude a mostra, o aipager responde embaixo dela:

```
⏳ Na fila - o Claude vai ler depois da etapa atual
[⚡ Enviar agora]
```

Cada mensagem na fila ganha uma linha própria, e cada linha some, com o 👍 dela, no momento em que o Claude pega essa mensagem. Uma mensagem que o Claude pega antes de o registro dela na fila ser visto não ganha linha, só 👀 e depois 👍.

Tocar em `⚡ Enviar agora` (ou mandar `/now`) aperta nessa sessão as próprias teclas de enviar agora do Claude Code (Ctrl+X, depois Ctrl+S). O Claude então lê de uma vez tudo o que tem na fila, não só a mensagem em que você tocou. O que acontece com o trabalho em andamento é o Claude Code que decide: um comando ou um agente rodando continua em segundo plano; se o Claude estava no meio de escrever uma resposta, essa resposta é cortada e recomeçada com a sua mensagem. Qualquer outro tipo de etapa (outra ferramenta, por exemplo) também pode ser interrompida, depois de uma pequena folga. Em alguns casos (por exemplo com as tarefas em segundo plano do Claude Code desligadas) o enviar agora vira uma simples interrupção da etapa em andamento. Tudo isso é o comportamento próprio de enviar agora do Claude Code, não algo que o aipager decide.

O toque responde `Enviado ao Claude agora` e todas as linhas somem de uma vez, porque o enviar agora entrega ao Claude tudo o que ele guarda; o `/now` as tira do mesmo jeito, e nenhuma linha vem para as mensagens que o Enviar agora mandou (uma mensagem que você mandar depois ganha a sua). Se o Claude cancelar a etapa em andamento em vez de passá-la para o segundo plano, ele pega na hora só a mensagem mais antiga da fila e lê o resto na etapa seguinte; a resposta vai embaixo da mensagem que ele pegou, e essa mensagem não roda duas vezes. Se as teclas demorarem mais que um instante para chegar ao Claude (um computador ocupado), o toque responde `Enviando ao Claude agora` no lugar; a linha então some quando elas chegam, ou, se não deu para entregá-las, fica e diz `⏳ Na fila: não foi possível alcançar o Claude, toque em “Enviar agora” para tentar de novo`, com o botão dela, para você tocar de novo. As teclas só são apertadas quando a conversa guardada do Claude mostra que ele ainda guarda a mensagem; senão o toque responde `O Claude já pegou` e tira a linha. Ele é recusado, sem apertar nada, enquanto um pedido de permissão ou uma pergunta está aberto (`Primeiro responda à pergunta aberta`), enquanto o Claude pode ter acabado de abrir um que o aipager ainda não mostrou (os eventos de uma tarefa nova são segurados enquanto a resposta da tarefa anterior ainda está sendo entregue: `Ocupado, tente de novo daqui a pouco`), e enquanto o aipager está no meio de digitar outra mensagem na sessão (`O aipager está digitando uma mensagem; tente de novo daqui a pouco`). Qualquer pessoa que pode mandar mensagens para a sessão pode usar o botão e o `/now`; um membro `read_only` não.

A linha some sozinha quando a mensagem dela sai da fila do Claude: o Claude a pega (junta à tarefa em andamento, passa para um agente em segundo plano, ou começa como a próxima tarefa), o Escape no terminal devolve a fila para o campo de digitação, ou ela é descartada por `/stop`, `/clearqueue`, `/kill`, `/restart`, uma parada de segurança ou o fim da sessão. Um reinício do aipager tira qualquer linha que ainda esteja aparecendo.

As linhas nunca esperam pelo controle de limites: elas saem e são tiradas na hora, mesmo num chat ocupado ou lento, e no modo mínimo. Só enquanto o próprio Telegram tem o chat silenciado nenhuma linha é mandada (o `/now` continua funcionando então), e se o Telegram acabou de pedir ao aipager para ir mais devagar, uma linha espera os segundos que ele pediu. Cada mensagem que espera custa uma chamada ao chat para a linha dela; as linhas que saem juntas são tiradas numa chamada só.

### <a id="files"></a>Arquivos

Os arquivos enviados são baixados numa pasta privada do chat de onde vieram, `/tmp/aipager-files/<chat>/` (com o nome `<time>_<file name>`; `<chat>` é um nome feito a partir do ID do chat e do token do bot, que não revela o ID do chat). Só o usuário do sistema com que o aipager roda pode ler essas pastas (`0700`, arquivos `0600`), e a tarefa de um membro num chat não pode ler, buscar nem escrever os arquivos enviados em outro chat (veja [segurança](security.md#team-mode-enforcement)). Os arquivos de antes desta mudança ficam onde estavam, direto em `/tmp/aipager-files/`; nada é movido nem apagado, e o aipager restringe essa pasta a `0700` quando inicia, então só esse usuário chega até eles. Um token de bot novo dá a cada chat uma pasta nova; os arquivos enviados antes dele ficam nas pastas antigas, que a tarefa de um membro não consegue mais ler. Uma pasta de downloads que é um link simbólico, ou que pertence a outro usuário, é recusada e o envio falha. O caminho é oferecido ao Claude: com uma legenda, a mensagem é a legenda seguida do caminho (ou dos caminhos); sem legenda, é só `check this: <path>` (ou `check these: <paths>` para um álbum), então o Claude é apontado para o arquivo sem receber nenhuma instrução sobre o que fazer com ele. Uma legenda que começa com `/<label>` escolhe a sessão, exatamente como `/<label> <prompt>` faz para o texto, e o rótulo sai da mensagem: `/api` sozinho manda `check this: <path>` para `api`, e `/api compare these` manda `compare these <paths>` para lá. Só um `/<label>` no começo escolhe o destino; uma barra mais adiante na legenda é só texto. Um rótulo a que nenhuma sessão responde é recusado com `⚠️ Não há nenhuma sessão com esse nome` e nada é mandado. O limite de 20 MB de download de arquivos dos bots do Telegram é aplicado antes; os arquivos maiores recebem uma recusa clara antes de qualquer tentativa de download. Um download que esbarra num erro de rede passageiro é tentado até três vezes no total (esperando 1 s, depois 2 s) antes de você ver um erro, e esse erro cita o arquivo. Um álbum (várias fotos ou documentos mandados como uma mensagem só) é passado ao Claude como uma mensagem só (a legenda, depois cada caminho de arquivo em ordem) assim que o último item chega; se um item não puder ser baixado, o resto sai mesmo assim, com uma nota citando o que faltou.

### <a id="files-claude-sends"></a>Arquivos que o Claude envia

O Claude pode te enviar arquivos da sessão dele: peça com suas próprias palavras ("me manda o print", "me manda a imagem em ~/proj/chart.png"), ou ele envia um quando acha que você quer. Ele usa a ferramenta SendUserFile do Claude Code (Claude Code 2.1.301 ou mais novo), que toda sessão que o aipager inicia recebe: o aipager a inicia com `CLAUDE_CODE_ATTACHMENTS_BY_PATH=1` e avisa o Claude de que a ferramenta existe. Uma sessão iniciada antes de o seu aipager ter isso precisa ser reiniciada para ganhar a ferramenta: `/restart`, uma troca de modo que a reinicia, ou Retomar.

- Os arquivos chegam depois da resposta da tarefa, como resposta a ela (à sua mensagem quando a tarefa não tem resposta), sem som de notificação e só no chat da própria sessão. Enquanto a tarefa roda, o cartão de trabalho mostra `📎 Enviando 2 arquivos`.
- Cada arquivo vai pelo conteúdo, não pelo nome: uma imagem como foto quando cabe nos limites do Telegram para fotos (no máximo 10 MB, largura mais altura no máximo 10.000, o lado maior no máximo 20 vezes o menor), senão como arquivo; um GIF como animação; um MP4 como vídeo; um MP3 ou M4A como áudio; todo o resto como arquivo. Quando o Claude pede um anexo simples, tudo vai como arquivo.
- De 2 a 10 fotos e vídeos vão num só álbum; arquivos vão com arquivos e áudio com áudio; uma animação vai sozinha. Se forem mais, são divididos por igual (11 vão como 6 e 5), e nenhum álbum passa de 50 MB.
- A legenda é `💬 <session>`, com a legenda do próprio Claude na linha seguinte da primeira mensagem, no máximo 1.024 caracteres.
- Cada arquivo é verificado antes, para todos, o owner incluído ([segurança](security.md#files-claude-sends)): as configurações do aipager, as credenciais do Claude, chaves e arquivos com cara de segredo nunca são enviados; um arquivo precisa ser um arquivo comum, não vazio e de no máximo 50 MB; um membro só envia arquivos dentro da [pasta raiz](groups.md#root-folders) do chat. Se um arquivo de um pedido é recusado, nada desse pedido é enviado: o Claude fica sabendo o motivo e te conta, e a tarefa continua.
- O aipager envia uma cópia particular que fez quando o Claude Code leu o arquivo, guardada em `~/.local/state/aipager/outbox/` e apagada assim que é enviada, ou depois de um dia.
- O aipager não põe limite de quantidade próprio: os envios seguem o ritmo dos limites do Telegram, e um álbum conta um por arquivo. Enquanto o chat está silenciado, os arquivos esperam e vão depois da resposta.
- Um arquivo que não pode ser enviado ganha uma linha no chat, por exemplo `⚠️ Não foi possível enviar report.pdf: o Telegram recusou o arquivo.` Um arquivo cujo pedido o aipager não encontra na conversa salva do próprio Claude não ganha nenhuma linha ([segurança](security.md#files-claude-sends)). Um arquivo cujo pedido está lá, mas que o aipager não consegue ligar à tarefa em andamento, recebe `⚠️ Não foi possível enviar report.pdf: o aipager não conseguiu confirmar que o Claude o enviou nesta tarefa.` Também chegam arquivos de uma mensagem que você manda enquanto o Claude trabalha e que ele pega em seguida, e de uma tarefa que um hook de Stop mantém rodando (`/goal`). Cada arquivo enviado, recusado ou que falhou tem seu próprio registro no [registro de auditoria](security.md#audit-log).

### <a id="voice"></a>Voz

Os áudios passam pelo `faster-whisper` (o pacote `aipager[voice]`). O áudio vira texto neste computador, é repetido como `🎙️ Ouvi: …`, e é digitado como se você o tivesse escrito, também como o nome para o `/new` ou para uma renomeação pendente. Um áudio acima do limite de 20 MB de download dos bots do Telegram é recusado. Veja [hooks → UserPromptSubmit](hooks.md#userpromptsubmit) para o que acontece depois.

---

## <a id="command-line-every-command"></a>Terminal: todos os comandos

Rode `aipager <command> --help` para ver a mesma lista no seu computador.

| Comando | O que faz |
|---|---|
| `aipager config` | O assistente de configuração interativo: token do bot, chats, membros e funções, os hooks do Claude Code. Rode de novo para mudar qualquer coisa (veja [uso em equipe](groups.md#setup)). |
| `aipager setup` | A mesma primeira configuração sem perguntas, para scripts e agentes de programação (veja [abaixo](#command-line-aipager-setup-for-coding-agents)). |
| `aipager start` | Roda o aipager em primeiro plano. Ele se recusa a iniciar enquanto outro aipager roda. |
| `aipager service install [--yes]` | Instala o aipager como serviço em segundo plano (serviço de usuário do systemd no Linux, agente do launchd no macOS) e o inicia. `--yes` pula a pergunta quando o serviço instalado é diferente. |
| `aipager service start\|stop\|status\|logs\|uninstall` | Inicia, para ou mostra o serviço, acompanha o log dele, ou o remove. |
| `aipager status [--json]` | O aipager e todas as sessões de uma olhada: estado, modelo, contexto, custo, e o estado dos limites de velocidade do Telegram. |
| `aipager logs [-f] [-n LINES]` | O log do aipager (journald no Linux, o log do launchd no macOS): as últimas 100 linhas, ou `-n` linhas; `-f` acompanha. |
| `aipager doctor [--fix] [--safety-check] [--json]` | Verificações de saúde (veja [solução de problemas](troubleshooting.md#aipager-doctor-check-list)). `--fix` se oferece para consertar a credencial do Claude e uma instalação ambígua do `claude`, perguntando antes; `--safety-check` imprime as regras de segurança em vigor; `--json` é para scripts (veja [abaixo](#aipager-doctor---json)). |
| `aipager session <name> [claude args]` | Inicia uma sessão do Claude Code chamada `<name>` sob o dtach na pasta atual, ou se conecta a ela se estiver rodando. Os argumentos depois do nome (ou depois de `--`) vão como estão para o `claude`, por exemplo `--resume`. Precisa de um aipager rodando. |
| `aipager session ls [-a] [--json]` | Lista as sessões; `-a` inclui as encerradas. Também `aipager session list`. |
| `aipager session kill <name> [-y]` | Encerra uma sessão, depois de uma pergunta a não ser com `-y`. |
| `aipager resume [name]` | Traz de volta uma sessão encerrada com a conversa dela; sem nome, uma lista para escolher. |
| `aipager miniapp enable [--port PORT] [--url URL]` | Liga o Mini App, em `127.0.0.1:PORT` (padrão 8765), atrás do túnel gerenciado ou da sua própria URL `https://`. Vale no próximo reinício do aipager. |
| `aipager miniapp disable\|status` | Desliga (no próximo reinício), ou mostra as configurações e o endereço dele. |
| `aipager policy validate` | Confere o `policy.yaml` (e o `policy.d`); sai com um código diferente de zero se houver um problema. |
| `aipager update` | Atualiza o aipager pelo instalador que é dono dele (uv, pipx, Homebrew, pip). Ele nunca reinicia o aipager; reinicie-o por conta própria, ou use o `/update` no Telegram, que consegue (veja [Atualizar](#update)). |
| `aipager report [--note TEXT]` | Mostra um relatório de problema e o envia se você disser que sim (veja [abaixo](#command-line-aipager-report)). |
| `aipager uninstall [-y]` | Para e remove o aipager e a configuração dele, mantendo os dados dele (veja [abaixo](#command-line-aipager-uninstall)). |
| `aipager version`, `aipager --version` | Imprime a versão. |
| `aipager help [command]` | Ajuda do aipager ou de um comando. |

## <a id="command-line-aipager-setup-for-coding-agents"></a>Terminal: `aipager setup` (para agentes de programação)

As seções acima descrevem o bot do Telegram. Esta descreve um comando que você roda num terminal, ou que um script ou o seu agente de programação roda por você: o `aipager setup` configura o aipager sem perguntas e dá o mesmo resultado que a primeira vez do `aipager config`.

```sh
# 1. descobrir o ID do Telegram da pessoa (ela abre o bot e aperta Start)
aipager setup detect-chat --token-file ~/bot-token.txt --json
# 2. quando ela confirmar que é ela, configurar o aipager
aipager setup --token-file ~/bot-token.txt --chat-id 123456789 --service --json
# 3. conferir a instalação
aipager doctor --json
```

O que ele faz, nesta ordem: lê o token do bot, confere o token com o Telegram (getMe), confere tudo neste computador sem escrever nada (a configuração existente, as dependências, o `~/.claude/settings.json` (ou `$CLAUDE_CONFIG_DIR/settings.json`), se há um aipager rodando), manda uma mensagem de teste para a pessoa ("aipager is set up for you. ..."), e só então escreve. Os arquivos escritos, do mesmo jeito que o assistente os escreve:

- `~/.config/aipager/aipager.yaml` (permissões 0600): `bot_token` e um chat privado cujo único membro é a pessoa (`label: owner`, função owner ou admin).
- `~/.claude/settings.json` (ou `$CLAUDE_CONFIG_DIR/settings.json`, o arquivo que as sessões do daemon leem: o ambiente deste shell mais o `daemon.env`): os hooks do aipager e a statusLine (quando o arquivo existente muda, uma cópia `settings.json.bak.<time>` é guardada). `settings_json.path` na saída JSON informa qual é.
- O registro de auditoria ganha um registro `grant-owner` quando alguém vira dono.
- Com `--service`, o serviço em segundo plano (o mesmo que `aipager service install --yes`).
- Só numa instalação antiga com `config.env` (ainda sem `aipager.yaml`), a migração que o assistente faz: uma cópia `config.env.bak.<time>` (e `team.yaml.bak.<time>` quando há um `team.yaml`), e um `~/.config/aipager/policy.yaml` inicial quando não existe nenhum. O `config.env` em si fica como está.

Se alguma verificação falhar, nada é mandado e nada é escrito. Quando um aipager já está rodando, uma mudança de chat ou de função é recarregada ao vivo; um token de bot novo precisa de um reinício (`aipager service stop`, depois `aipager service start`), que o setup nunca faz sozinho, e o `--service` é pulado. Se o setup não conseguir saber se um aipager roda, ele age como se rodasse (alerta `daemon_unknown`): aí ele nunca instala o serviço, então nunca pode reiniciar um aipager vivo, e depois de qualquer mudança não manda nenhuma recarga e informa `restart_needed: true` no lugar.

### <a id="aipager-setup-flags"></a>Opções do `aipager setup`

| Opção | Significado |
|---|---|
| `--token-file PATH` | Lê o token do bot deste arquivo (no máximo 4096 bytes; texto em volta de um token não tem problema). Um arquivo que outros usuários podem ler funciona, mas dá o alerta `token_file_shared`: faça `chmod 600` nele, ou apague-o quando o setup der certo. |
| `--token-stdin` | Lê o token do bot do stdin (um pipe ou um arquivo redirecionado, nunca um terminal). O pipe precisa fechar: o setup espera no máximo 30 segundos o fim do stdin e depois sai com 2 e `token_stdin_timeout`. `printf '%s\n' "$TOKEN" \| aipager setup --token-stdin ...` e `< token.txt` fecham os dois. |
| `--chat-id N` | Obrigatória. O ID numérico de usuário do Telegram da pessoa, que também é o ID do chat privado dela. Os grupos são adicionados com o `aipager config`. |
| `--role owner\|admin` | A função da pessoa (padrão `owner`). |
| `--service` | Também instala e inicia o serviço em segundo plano (pulado enquanto um aipager roda). |
| `--force` | Permite substituir o token do bot, o chat privado do dono ou a função de uma instalação existente. Os chats de grupo e os outros chats privados são mantidos. |
| `--dry-run` | Roda todas as verificações, não manda nada, não escreve nada, e informa o que mudaria. |
| `--json` | Imprime exatamente um objeto JSON no stdout; todo o resto vai para o stderr. |

Dê exatamente uma entre `--token-file` e `--token-stdin`. O token nunca é aceito como valor de uma opção: `--token` e `--bot-token` são recusadas (`token_on_command_line`) e o valor não é mostrado. Opções abreviadas (`--token-f`, `--dry`) não são aceitas. O setup nunca faz perguntas, nunca precisa de um terminal e nunca lê o token de uma variável de ambiente.

Rodar de novo com o mesmo token, chat e função não muda nada e diz isso (`unchanged`, sem mensagem de teste). Um token, chat ou função diferentes numa instalação existente saem com 6 até você acrescentar `--force`. Uma instalação antiga com `config.env` é migrada primeiro, como o assistente faz (ele dá a função `admin`, então `--role owner` nela precisa de `--force`).

### <a id="aipager-setup-detect-chat"></a>`aipager setup detect-chat`

```sh
aipager setup detect-chat (--token-file PATH | --token-stdin) [--timeout SECONDS] [--json]
```

Espera a mensagem privada mais recente para o bot (tocar em Iniciar manda uma) e imprime quem a mandou: ID, nome e nome de usuário. Ele não guarda nada, então mostre a pessoa ao usuário e rode `aipager setup --chat-id` só depois que ele confirmar. Só contam as mensagens mandadas no máximo 10 minutos antes de o comando começar. `--timeout` vai de 0 a 3600 segundos (padrão 300; 0 quer dizer olhar uma vez só). Ele lê o Telegram sem confirmar nenhuma mensagem, então o `/start` continua disponível para o assistente e para o aipager.

Enquanto um aipager do mesmo bot roda, o aipager recebe as mensagens do bot, e não anota as mensagens privadas de pessoas que ainda não estão configuradas. O detect-chat então sai com 9 (`source: "daemon"`): pare o aipager (`aipager service stop`) e rode de novo, ou peça à pessoa o ID numérico dela. Com um aipager de outro bot, ele lê o Telegram direto (`source: "telegram"`).

Limite: como o detect-chat nunca confirma uma mensagem, o Telegram só mostra a ele as 100 atualizações não lidas mais antigas das últimas 24 horas. Quando o bot tem tantas esperando, um `/start` novo não pode ser visto: o detect-chat então passa do tempo (saída 8) com o alerta `updates_backlog`, e tocar em Iniciar de novo não ajuda. Peça à pessoa o ID numérico do Telegram dela no lugar (ou inicie o aipager uma vez, que lê e esvazia essas atualizações).

### <a id="exit-codes"></a>Códigos de saída

| Código | `error` | Significado |
|---|---|---|
| 0 | | Sucesso (`installed`, `updated`, `unchanged`, `dry_run`, ou detect-chat com `found`). |
| 1 | `config_malformed`, `ambiguous_install`, `settings_invalid`, `test_send_failed`, `write_failed`, `service_failed`, `internal_error` | Falha. `ambiguous_install`: mais de um chat privado poderia ser o do dono; conserte no `aipager config`. |
| 2 | `usage`, `missing_token_source`, `token_source_conflict`, `token_file_unreadable`, `token_stdin_is_tty`, `token_stdin_timeout`, `token_malformed`, `token_on_command_line`, `bad_chat_id`, `bad_role`, `bad_timeout` | Entrada errada ou faltando; `fix` cita a opção. Uma opção desconhecida ou uma palavra errada depois de `setup` é informada pelo analisador de argumentos (texto simples, sem JSON), também com 2. |
| 3 | `deps_missing` | Falta o dtach, o claude, o aipager-hook ou o aipager-statusline; `fix` tem os comandos. Nada foi escrito. |
| 4 | `token_rejected` | O Telegram recusou o token. |
| 5 | `chat_not_started`, `bot_blocked` | A pessoa não tocou em Iniciar no bot ("Open t.me/&lt;bot&gt; and press Start, then run this again."), ou o bloqueou. Nada foi escrito. |
| 6 | `existing_install` | Já há outro token, chat ou função configurados; acrescente `--force`. |
| 7 | `telegram_unreachable` | Erro de rede, ou o Telegram respondeu 429 ou 5xx. |
| 8 | `detect_timeout` | O detect-chat não viu nenhuma mensagem privada antes do `--timeout` (com o alerta `updates_backlog` quando 100 atualizações antigas não lidas escondem as novas). |
| 9 | `daemon_running` | detect-chat: um aipager rodando recebe as mensagens do bot. |
| 10 | `updates_conflict` | detect-chat: outro programa lê as mensagens deste bot. |
| 130 | `interrupted` | Ctrl-C. |

Quando várias verificações falham de uma vez, o código segue esta ordem: `config_malformed`, `ambiguous_install`, `existing_install`, `settings_invalid`, `deps_missing`. O `--dry-run` sai com o código que a execução real teria por essas verificações.

### <a id="json-aipager-setup---json"></a>JSON: `aipager setup --json`

Todas as chaves estão sempre presentes; um valor que ainda não se sabe é `null`.

```json
{
  "command": "setup",
  "status": "installed",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "message": "aipager is set up for @example_bot (chat 123456789, role owner). ...",
  "fix": null,
  "dry_run": false,
  "bot_username": "example_bot",
  "chat_id": 123456789,
  "role": "owner",
  "changed": ["bot_token", "owner_dm", "settings_json"],
  "test_message": "sent",
  "deps": {"ok": true, "items": [{"name": "dtach", "found": true, "path": "/usr/bin/dtach", "fix": null}]},
  "settings_json": {"path": "/home/you/.claude/settings.json", "status": "created", "backup": null, "repointed": 0},
  "daemon": {"running": false, "reload": "not_needed", "restart_needed": false},
  "service": {"requested": false, "result": "not_requested"},
  "warnings": [],
  "next_step": "Start aipager with `aipager service install` ..."
}
```

- `status`: `installed`, `updated`, `unchanged`, `dry_run` ou `error`.
- `changed`: o que mudou (ou mudaria, numa execução de teste), nesta ordem: `migrated_v1`, `bot_token`, `owner_dm`, `role`, `settings_json`, `service`.
- `test_message`: `sent`, `not_needed`, `skipped_dry_run`, `failed` ou `not_attempted`.
- `settings_json.status`: `created`, `patched`, `unchanged`, `would_change` ou `not_checked`.
- `daemon.reload`: `reloaded`, `refused`, `not_reloaded` ou `not_needed`. `restart_needed` é true depois de um token novo com um aipager rodando, e depois de qualquer mudança quando o aipager não pôde ser recarregado ou o setup não conseguiu saber se um roda.
- `service.result`: `not_requested`, `installed`, `already_installed`, `skipped_daemon_running`, `would_install`, `failed`, ou `null` quando o setup parou antes.
- `warnings`: objetos com `code` (`token_file_shared`, `reload_refused`, `daemon_unknown`, `audit_write_failed`) e `message`. `audit_write_failed`: a permissão de dono está escrita, mas o registro `grant-owner` dela não pôde ser acrescentado ao registro de auditoria; esse registro é de melhor esforço, então o código de saída fica 0.
- `next_step` depois de uma mudança com um aipager rodando que não pôde ser recarregado diz para reiniciá-lo (`aipager service stop`, depois `aipager service start`); ele cita o token do bot só quando o token mudou.
- Com o alerta `daemon_unknown`, `next_step` nunca diz que o aipager está rodando. Ele diz que o setup não conseguiu saber, aponta para `aipager doctor --json`, e diz para iniciar o aipager com `aipager service install` ou `aipager start` se ele não estiver rodando. Quando um reinício seria preciso, ele só pede um se um aipager estiver rodando, e senão diz para iniciá-lo do mesmo jeito.
- Num erro, `status` é `error` e `error`, `message` e `fix` dizem o que deu errado e o que fazer; `next_step` repete o `fix`. Quando o erro veio depois de uma escrita, `changed` lista o que foi escrito, e a saída normal acrescenta uma linha no stderr `Already written: aipager.yaml, ... (changed: ...)`.

### <a id="json-aipager-setup-detect-chat---json"></a>JSON: `aipager setup detect-chat --json`

```json
{
  "command": "detect-chat",
  "status": "found",
  "ok": true,
  "exit_code": 0,
  "error": null,
  "message": "Found Ada L (@ada), Telegram id 123456789.",
  "fix": null,
  "bot_username": "example_bot",
  "source": "telegram",
  "candidate": {"id": 123456789, "first_name": "Ada", "last_name": "L", "username": "ada", "date": 1700000000},
  "other_candidates": 0,
  "warnings": [],
  "next_step": "Show this person to the user; once they confirm, run: aipager setup --token-file <path> --chat-id 123456789"
}
```

`candidate` é `null` a não ser que `status` seja `found`. `warnings` tem objetos com `code` (`token_file_shared`, `updates_backlog`) e `message`. `other_candidates` conta outras pessoas que mandaram mensagem para o bot nos mesmos 10 minutos: quando não for 0, confira se você tem a pessoa certa. `next_step` repete a opção de token que você usou, nunca o token.

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

As verificações do `aipager doctor` como um objeto JSON no stdout. Como o `aipager doctor` normal, ele só lê e não manda nenhuma mensagem no Telegram. Sai com 1 quando alguma verificação falha, senão com 0. Não pode ser combinado com `--fix` nem com `--safety-check` (sai com 2 e um objeto de erro).

```json
{
  "command": "doctor",
  "version": "1.0.1",
  "ok": true,
  "summary": {"ok": 14, "warn": 1, "fail": 0},
  "checks": [{"key": "token_valid", "status": "ok", "title": "Telegram bot token", "detail": ["@example_bot"], "fix": null}]
}
```

`checks` vêm nesta ordem, com estas chaves: `config_parses`, `config`, `token_valid`, `chat_reachable`, `team`, `role_shell_access`, `peer_messages`, `claude`, `claude_auth`, `dtach`, `hook_scripts`, `settings_json`, `daemon`, `service_installed`, `service_unit_path`, `miniapp`. Uma verificação que quebra mantém a chave com o estado `warn`. Os textos são simples (sem marcação), e tudo o que tiver formato de token de bot é trocado por `<redacted>`.

## <a id="command-line-aipager-uninstall"></a>Terminal: `aipager uninstall`

O `aipager uninstall` para o aipager e remove o serviço dele, apaga a pasta de configuração (`~/.config/aipager`: o token do bot, os chats e as configurações) e a lista de sessões (`~/.claude/aipager-sessions.json`), limpa os arquivos do aipager em `/tmp`, e desinstala o próprio aipager. Ele pergunta antes (`-y` pula a pergunta).

Ele mantém os dados do aipager, para uma reinstalação continuar de onde parou: `~/.local/share/aipager` (as pastas das sessões, as ferramentas baixadas, os arquivos de trava e de marcação), `~/.local/state/aipager` (os registros dos relatórios de problemas e o estado do idioma), `~/.claude/aipager-audit.jsonl` (o registro das respostas aos pedidos de permissão), `~/.claude/aipager-flood-state.json` (o histórico de limites de velocidade do Telegram), `~/.claude/aipager-pending-users.json` (pessoas que tentaram usar o bot sem acesso) e `~/aipager-users` (as [pastas raiz](groups.md#root-folders) dos membros, com o trabalho deles). Antes de perguntar, ele lista os que existem no seu computador, e no fim imprime o comando `rm -rf` que também os apaga.

Ele também tira as entradas do próprio aipager do `~/.claude/settings.json` (e do que as sessões do daemon leem, quando `CLAUDE_CONFIG_DIR` aponta para outro) do Claude Code: os hooks que rodam `aipager-hook` e a linha de status que roda `aipager-statusline` (uma linha de status sua fica). Todo o resto desse arquivo fica como estava, e o arquivo como estava é guardado ao lado dele como `settings.json.bak.<time>`. Se o arquivo for um link para outro arquivo, não puder ser lido, não for JSON válido, ou não estiver no formato que o Claude Code lê, ele fica exatamente como está e o uninstall diz quais entradas tirar à mão. Um hook seu que ainda cita `aipager-hook` ou `aipager-statusline` (por exemplo um script `aipager-hook-capped.sh` em volta dele) também fica, e o uninstall o cita. As sessões do Claude Code já abertas continuam rodando os hooks que carregaram até você reiniciá-las. O `aipager config` acrescenta as entradas de novo depois de uma reinstalação. O seu bot do Telegram e as cópias `settings.json.bak.*` mais antigas não são tocados, nem o `skipDangerousModePermissionPrompt` (a configuração que pula o aviso do Claude Code antes de ele rodar com as verificações de permissão desligadas), que o aipager define mas que você também pode ter definido.

## <a id="command-line-aipager-report"></a>Terminal: `aipager report`

O `aipager report` monta um relatório de problema do mesmo tipo do botão Relatar um problema do bot, a partir do que o aipager registrou neste computador (com menos dados do aipager rodando: o modo, os chats, os recursos, o tempo ligado, as sessões e os detalhes do Claude Code ficam desconhecidos ou vazios), imprime tudo, e pergunta `Send this report? [y/N]`. Só `y` ou `yes` o enviam; qualquer outra coisa, ou nenhum terminal (um script, um pipe, o shell de uma sessão do Claude Code), não envia nada. `--note "text"` acrescenta uma nota de no máximo 500 caracteres, que aparece no relatório impresso. Ele sai com 0 quando o relatório foi enviado, não foi enviado por escolha sua, ou só foi impresso, e com 1 quando um envio que você confirmou não saiu. Veja [Relatórios de problemas](problem-reports.md) para o que um relatório contém e para onde vai.

## <a id="settings-in-the-environment"></a>Configurações do ambiente

Algumas configurações são lidas do ambiente do aipager em vez do `aipager.yaml`. Para o serviço do systemd, acrescente-as com `systemctl --user edit aipager` embaixo de `[Service]` (`Environment=STALE_BUSY_TIMEOUT=900`), depois `aipager service stop` e `aipager service start`; para o serviço do launchd no macOS, acrescente-as ao `EnvironmentVariables` do plist (`~/Library/LaunchAgents/com.aipager.daemon.plist`; o `aipager service install` escreve esse arquivo de novo, então acrescente-as de novo depois); para um aipager que você inicia por conta própria, defina-as antes do `aipager start`. Nenhuma delas é necessária no uso normal.

| Configuração | Padrão | O que muda |
|---|---|---|
| `AIPAGER_WHISPER_MODEL` | `base` | O modelo de fala para os áudios (`tiny`, `base`, `small`, `medium`, ...): maior é mais preciso e mais lento. |
| `MINIAPP_PORT` | `8765` | A porta do Mini App em `127.0.0.1` (`aipager miniapp enable --port` a guarda no lugar). |
| `MINIAPP_ENABLED` | conforme o `aipager.yaml` | `0` desliga o Mini App e `1` o liga, diga o que disser o `aipager.yaml` (`aipager miniapp enable\|disable` guarda a escolha no lugar). |
| `MINIAPP_PUBLIC_URL` | nenhum | Serve o Mini App na sua própria URL `https://` em vez do túnel gerenciado ([Modelo de segurança](security.md#mini-app-tunnel)). |
| `KEEP_FINISHED_CARD` | `1` | `0` faz de “Só a resposta (sem cartão)” a aparência dos chats que nunca escolheram uma no `/settings`. |
| `STALE_BUSY_TIMEOUT` | `7200` (2 horas) | Segundos que uma sessão trabalhando pode ficar sem notícias antes de o chat receber o aviso “ainda trabalhando (120 min sem novidades)”. |
| `PROMPT_HOOK_GRACE_SECONDS` | `8` | Segundos até uma mensagem que o Claude Code nunca pegou ser marcada como “O Claude Code não recebeu a mensagem” ([mensagens livres](#text)). |
| `FINISH_CARD_GRACE_SECONDS` | `0.8` | Quanto o cartão terminado vem antes da resposta na aparência com cartão; `0` manda os dois juntos. |
| `GONE_SESSION_MAX_AGE_DAYS` | `14` | Dias que uma sessão encerrada fica no `/resume`; `0` mantém todas. |
| `STREAM_EDIT_INTERVAL`, `BUSY_EDIT_INTERVAL` | `1.2`, `3.0` | A frequência de atualização do cartão de atividade enquanto mostra texto e enquanto está quieto, em segundos; o limite por chat do Telegram continua valendo mais ([solução de problemas](troubleshooting.md#busy-cards-got-slower)). |
| `TYPING_INDICATOR_INTERVAL` | `4.5` | Segundos entre os balões de “digitando…”; `0` os desliga. |
| `TELEGRAM_MAX_RETRY_AFTER` | `90` | A espera do Telegram, em segundos, a partir da qual o aipager trata um limite de velocidade como um bloqueio (não diminua; veja [solução de problemas](troubleshooting.md#the-bot-went-quiet-flood-control)). |
| `CLAUDE_RICH_SUMMARIES` | `1` | `0` desliga a formatação rica das respostas que têm blocos de código (uma alternativa que sobrou de versões antigas). |
| `AIPAGER_INTERACTIVE_TIMEOUT` | `300` | Segundos que um pedido de permissão ou uma pergunta pode esperar sem notícias do Claude Code antes de o aipager olhar a conversa salva; só se ela mostrar que o pedido acabou a sessão volta a contar como trabalhando ([solução de problemas](troubleshooting.md#a-permission-prompt-with-no-news-for-5-minutes)). |
| `AIPAGER_SUBAGENT_SILENCE` | `1800` | Segundos de silêncio depois dos quais um subagente cujo fim não foi visto é descartado ([hooks](hooks.md#subagentstart--subagentstop)). |
| `AIPAGER_BG_SHELL_MAX_TRACK` | `7200` | Segundos depois dos quais um comando em segundo plano cujo fim não foi visto para de contar. |
| `AIPAGER_REPORT_PROMPTS` | ligado | `0` desliga as sugestões automáticas de relatórios de problemas ([relatórios de problemas](problem-reports.md#when-aipager-offers-a-report)). |
| `AIPAGER_CLAUDE_BIN` | nenhum | O `claude` que deve rodar, quando há vários instalados (o `claude_path` do `aipager.yaml` vale mais). |
| `AIPAGER_SOCKET_PATH` | `$XDG_RUNTIME_DIR/aipager.sock` | O socket de controle do aipager. As sessões que o aipager inicia o passam para os hooks delas. |
| `AIPAGER_DEBUG` | desligado | `1` no ambiente de uma sessão faz o `aipager-hook` e o `aipager-statusline` imprimirem os diagnósticos deles no stderr. |
| `AIPAGER_INSTANCE_DIR`, `AIPAGER_TELEGRAM_API_BASE` | nenhum | Um segundo aipager isolado para testes ([arquitetura](architecture.md#isolated-instance-for-testing)). |

## <a id="see-also"></a>Veja também

- [Arquitetura](architecture.md): onde o bot se encaixa.
- [Eventos de hook](hooks.md): o que o aipager ouve do claude.
- [Solução de problemas](troubleshooting.md): quando os comandos não se comportam.
