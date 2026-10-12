# Relatórios de problemas: o que eles contêm

<!-- i18n-source: docs/problem-reports.md @ 6fc084f -->

> Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../problem-reports.md))

Quando o aipager encontra um bug, você pode mandar um relatório de problemas para quem mantém o projeto com um toque. Você vê o relatório inteiro antes de qualquer coisa ser enviada, nada sai do seu computador até você mandar, e um relatório nunca contém seus chats, mensagens, nomes, caminhos nem tokens. Ele é montado só a partir de uma lista fixa de campos: versões, contagens, datas, respostas de sim ou não, opções de listas fixas, lugares do próprio código do aipager e os nomes `ap1-` dos bugs dele, mais uma nota curta só se você escrever uma.

## <a id="how-to-send-one"></a>Como enviar um

Pelo Telegram, só o dono desta instalação do aipager pode enviar um relatório: a pessoa do próprio chat privado da instalação com o bot (quem instalou o aipager; se houver vários chats assim e nenhum dono único, não aparece o botão Relatar um problema nem a sugestão automática, e `aipager report` no terminal continua funcionando). O Mini App tem a própria página de relatório; todos os outros caminhos do Telegram abrem o mesmo cartão de prévia no seu chat privado com o bot:

- **`/help`**: quando o dono manda, o texto de ajuda vem com um botão “🐞 Relatar um problema”. Num grupo só o toque do dono faz alguma coisa, e a prévia abre no chat privado do dono, nunca no grupo.
- **O Mini App**: na aba Configurações, “Relatar um problema” (quando o app está aberto no seu chat privado) abre uma página dentro do app. Sua nota fica no topo (opcional, até 500 caracteres, com contador). Abaixo vem um resumo curto do relatório: versões, configuração, os erros (toque em um para ver os lugares dele no código) e **Mais detalhes**. **Mostrar o relatório exato** mostra o texto exato que vai ser enviado, com sua nota incluída, e acompanha o que você digita. **Enviar relatório** (o botão do próprio Telegram embaixo) envia exatamente isso, uma vez, e depois a página mostra a referência com um botão **Copiar**. Voltar não envia nada. Se o aipager limpar sua nota (espaços no começo ou no fim, caracteres invisíveis, o que passar de 500 caracteres), a página mostra a nota limpa e nada é enviado até você tocar em Enviar relatório de novo.
- **O aviso de limite de memória**: quando o hook do aipager fica sem memória, o aviso no chat vem com um botão “🐞 Relatar isto”.
- **Uma sugestão automática**: de vez em quando, o aipager sugere enviar um relatório de um bug que ele percebeu (veja [Quando o aipager sugere um relatório](#when-aipager-offers-a-report)).
- **No terminal**: `aipager report` imprime um relatório e pergunta `Send this report? [y/N]`. Ele tem o mesmo tipo de campos, mas menos informações do aipager em execução (o modo, os chats, os recursos, o tempo ligado, as sessões e os detalhes do Claude Code ficam desconhecidos ou vazios). `aipager report --note "what you were doing"` adiciona uma nota. Sem terminal (um script, um pipe, o shell de uma sessão do Claude Code) ele imprime o relatório e nunca o envia.

O cartão de prévia mostra o relatório exatamente como ele vai ser enviado: o JSON num bloco recolhido que você pode abrir ou, quando é longo demais para uma mensagem, como um arquivo `report.json` em resposta ao cartão. Os botões dele:

- **📤 Enviar**: envia exatamente esse relatório, uma vez. Depois o cartão diz `Enviado. Referência: ap1-...`.
- **✏️ Adicionar uma nota**: sua próxima mensagem nesse chat, em até 10 minutos, vira a nota do relatório (cortada em 500 caracteres), e o cartão mostra o relatório de novo com a nota antes de você enviar. Se você fizer outra coisa (um comando, outro botão, uma foto, algo no Mini App), a pergunta da nota fecha sem adicionar nota, e depois de 10 minutos sua próxima mensagem vai para o Claude normalmente. A pergunta tem **‹ Voltar**, que volta para a prévia sem nota, e Cancelar.
- **✖️ Cancelar**: nada é enviado.

Uma prévia fica aberta por 24 horas, e as 5 mais recentes ficam abertas (abrir a sexta fecha a mais antiga). Depois que o aipager reinicia, um cartão antigo não pode mais ser enviado: abra Relatar um problema de novo para ter um novo. Um relatório aberto no Mini App pode ser enviado por 24 horas (os 3 mais recentes); depois de um reinício, a página carrega um novo.

## <a id="what-a-report-holds"></a>O que um relatório contém

Cada parte, em palavras simples. Cada valor é conferido com a lista de valores permitidos antes de a prévia aparecer e de novo antes do envio; o que não se encaixa vira `<invalid>` ou é tirado, nunca é enviado do jeito que estava.

**Sobre o relatório**
- `schema`: o formato do relatório (`aipager-report/1`).
- `trigger`: `manual` (você abriu) ou `auto` (uma sugestão automática).
- `day`: a data, em UTC (sem a hora).
- `note`: só se você adicionou uma: suas palavras, exatamente como a prévia mostrou (no máximo 500 caracteres, sem caracteres invisíveis).

**O aipager e o computador**
- `aipager`: a versão; como foi instalado (`pipx`, `uv`, `brew`, `pip`, `editable`, `nix`, `snap`, `docker`, `system`, `foreign` ou `unknown`); de onde (o índice de pacotes, uma pasta local, um checkout do git); e se `aipager update` consegue atualizá-lo.
- `python`: a versão do Python e se é CPython ou PyPy.
- `os`: o sistema (`linux`, `darwin`, `windows`, `freebsd`), o tipo de processador, a distribuição Linux de uma lista fixa e o número da versão dela, a versão major.minor do kernel, o tipo de contêiner, se houver (`docker`, `lxc`, `wsl`, `podman`), e se o aipager está configurado como serviço do systemd ou do launchd.
- `deps`: as versões das bibliotecas de que o aipager depende (python-telegram-bot, httpx, aiohttp, PyYAML, rich, questionary, dtach-bin).
- `claude_code`: a versão do Claude Code, quantas instalações do Claude Code foram encontradas, e como ele faz login (`env`, `file`, `keychain`, ou por que não deu para saber). Nunca o login em si.

**Sua configuração, só como contagens**
- `config`: o tipo de uso (`personal`, `team`, `scope`); quantos chats privados e quantos grupos estão configurados; quantas funções personalizadas existem (sem os nomes); quais recursos estão ligados (o Mini App, o tipo de túnel dele, os observadores, a voz, as prévias de diff, os resumos ricos).
- `runtime`: há quanto tempo o aipager está ligado, como uma faixa (`<10m`, `10m-1h`, `1-24h`, `1-7d`, `>7d`); quantas sessões estão rodando e quantas trabalhando; quantas vezes o aipager parou de forma inesperada nos últimos 7 dias e como terminou da última vez (`clean`, `crash`, `reboot`).
- `doctor`: os resultados (`ok`, `warn`, `fail`) de uma lista fixa de verificações de saúde, quando estão disponíveis sem rodar as verificações de novo (os relatórios do bot e do terminal deixam isso vazio).
- `flood`: o estado dos limites de velocidade do Telegram, somado em todos os chats: bloqueios nos últimos 7 dias, se agora algum chat está silenciado, em modo mínimo ou diminuindo o ritmo, e a parte do orçamento por hora que o chat mais movimentado usa, como uma faixa. Sem IDs de chat.

**O que deu errado**
- `counters_24h`: quantas vezes aconteceu cada coisa de uma lista fixa nas últimas 24 horas (por exemplo o vigia do cartão de atividade reiniciando, uma classe de erro do Telegram, o hook chegando ao limite de memória, um erro de rede ou de disco). Só as contagens.
- `log_digest_24h`: para os 15 lugares mais movimentados do código do aipager que registraram um alerta ou um erro nas últimas 24 horas, o lugar (`aipager/state.py:1234`), o nível e quantas vezes. Nunca o texto do log.
- `errors`: até 10 erros que o aipager registrou, cada um com:
  - `fingerprint`: um nome `ap1-` para o bug (um hash do tipo de erro e dos nomes de até 8 lugares do código do aipager, nunca de algum texto ou dado);
  - onde aconteceu (o aipager, o hook, a linha de status, um comando, o Mini App), como foi capturado, e se é um bug de verdade ou só algo incomum (`tier`);
  - para um erro que o aipager registrou, o nome do logger (um módulo do `aipager`, `asyncio` ou uma parte do `telegram.ext`);
  - o nome do tipo da exceção (por exemplo `builtins.KeyError`, ou `<other>` para um que não seja de uma lista fixa de pacotes), os tipos de até três causas, e o nome de um número de erro como `ENOSPC`;
  - para um erro do hook, o evento do hook e a ferramenta (uma ferramenta própria pelo nome, qualquer ferramenta MCP como `mcp`);
  - quantas vezes, em que dias e em que versões do aipager aconteceu;
  - até 12 lugares do próprio código do aipager por onde passou (arquivo, linha, nome da função), e até 8 nomes de pacotes externos por onde passou, de uma lista fixa.

**Nunca entra**: seus chats, mensagens, as respostas do Claude, os nomes das sessões, caminhos de pastas ou arquivos, tokens ou chaves, IDs de chat ou de usuário, nomes de usuário, o nome do computador, o texto de qualquer mensagem de erro, nem linhas do log. Os campos acima não têm lugar para isso, e o relatório é conferido com essa lista antes de aparecer e antes de ser enviado.

## <a id="where-it-goes"></a>Para onde vai

Um relatório vai, sem seu nome, para a caixa privada de erros de quem mantém o aipager no [Sentry](https://sentry.io): sem conta e sem ID de instalação. Como qualquer pedido na web, ele sai do endereço de internet do seu computador, que o GitHub e o Sentry podem ver; a caixa está configurada para não guardá-lo (a opção “Prevent Storing of IP Addresses” do Sentry). O próprio aipager envia o relatório num único pedido HTTPS (ele não usa a biblioteca do Sentry, que coleta muito mais): o relatório que você viu vai como anexo `report.json`, junto com um resumo curto para agrupar (a versão, como foi instalado e se é uma instalação de desenvolvimento, o sistema, as versões do Python e do Claude Code, como o relatório foi aberto, e o fingerprint, o tipo e os lugares no código do primeiro erro). O pedido se identifica só como `aipager/<version>`.

Depois do envio, o cartão (ou o terminal) mostra uma **referência**: o fingerprint `ap1-` do primeiro erro ou, para um relatório sem erros, o ID do evento, de 32 caracteres. Se você também abrir uma [issue no GitHub](https://github.com/dev-aly3n/aipager/issues), inclua a referência para que quem mantém o aipager encontre seu relatório. Não cole logs nem a saída do `aipager doctor` numa issue: eles têm seu ID de chat, os caminhos da sua pasta pessoal e o começo das suas mensagens.

Logo antes de enviar, o aipager lê o [report-endpoint.json](https://github.com/dev-aly3n/aipager/blob/main/report-endpoint.json) do repo do aipager no GitHub para saber para onde enviar (um aipager em execução guarda uma cópia boa por um dia; uma leitura que falha é tentada de novo no próximo envio, e cada `aipager report` lê de novo). Assim quem mantém o aipager pode trocar a chave do Sentry, desligar os relatórios para todo mundo ou impedir que versões abaixo de um número enviem (uma versão que não sabe o próprio número conta como abaixo), sem lançar uma versão nova. Se o GitHub não responder ou o arquivo não for algo que o aipager entenda, o aipager usa a chave que vem nesta versão; um arquivo legível que diga desligado é sempre respeitado.

## <a id="limits"></a>Limites

- No máximo 5 tentativas de envio por dia (em UTC) de um computador, manuais e automáticas juntas, e uma tentativa que falhou também conta. A próxima diz `Dá para enviar no máximo 5 relatórios por dia de um computador. Tente de novo amanhã.`
- Nada é tentado de novo em segundo plano: se um envio falhar, o cartão diz `Não foi possível enviar agora. Nada foi perdido; tente de novo mais tarde.` e mantém o botão Enviar.

## <a id="when-aipager-offers-a-report"></a>Quando o aipager sugere um relatório

O aipager percebe os próprios bugs e, de vez em quando, sugere no seu chat privado enviar um relatório sobre eles: `O aipager teve um erro interno N vezes (TypeError no cartão de atividade). Um relatório ajuda a corrigir isso mais rápido. ...` com **Ver o relatório**, **Agora não** e **Não perguntar de novo**. Ver o relatório abre o cartão de prévia só com esses erros; nada é enviado até você tocar em Enviar lá.

Uma sugestão só aparece quando tudo isto vale:
- É um bug de verdade do aipager: um erro no próprio código do aipager que aconteceu pelo menos duas vezes, com pelo menos 10 minutos de diferença, ou na hora quando o aipager caiu rodando como serviço (um `aipager start` em primeiro plano que é fechado, por exemplo ao fechar o terminal, só é contado). Os erros que o hook, a linha de status ou um comando avisam (incluindo o hook ficar sem memória) também precisam ter acontecido ao longo de pelo menos um dia, então um só nunca gera uma sugestão; o botão Relatar isto do aviso existe para isso. O que não é culpa do aipager (os limites do Telegram, a rede, um disco cheio) nunca gera uma sugestão; só é contado para um relatório que você mesmo envia.
- No máximo uma sugestão a cada 3 dias, com tudo o que está pendente nessa mesma sugestão.
- Não nas primeiras 48 horas depois que o aipager rodou pela primeira vez neste computador (as atualizações não zeram essa contagem), nem na primeira hora depois que o aipager reinicia.
- Só quando nada está em andamento nem esperando para ser enviado por 2 minutos, e você usou seu chat privado com o bot nos últimos 30 minutos. Nunca num grupo, e nunca enquanto o Telegram estiver limitando esse chat.
- Cada bug é sugerido uma vez por versão do aipager. Depois de qualquer resposta, ele não é sugerido de novo nessa versão; pode voltar depois de você atualizar, só se acontecer de novo.
- **Agora não** e **Não perguntar de novo** contam os dois como um não, e também uma sugestão que você não responde em um dia. **Ver o relatório** zera a contagem, mesmo que depois você cancele. Dois nãos seguidos desligam as sugestões automáticas: quando o segundo é um toque, uma linha avisa; de qualquer jeito o `/settings` mostra.
- Nunca para uma instalação de desenvolvimento (o aipager instalado de uma pasta local, de um checkout do git ou em modo editable).

Para desligar as sugestões automáticas, use `/settings` no seu chat privado: **🐞 Relatórios de problemas: Desligado**. Quando dois nãos as desligaram, a mesma página tem **Reativar as sugestões**. Um `reports.json` que existe mas não pode ser lido também desliga as sugestões automáticas (o aipager não sabe mais o que você respondeu antes); **Reativar as sugestões** resolve isso também. Num computador sem chat para perguntar, defina `AIPAGER_REPORT_PROMPTS=0` no ambiente do aipager. Relatar um problema e `aipager report` sempre funcionam, não importa o que essas configurações digam.

## <a id="what-is-kept-on-your-machine"></a>O que fica guardado no seu computador

- `~/.local/state/aipager/reports.json`: os erros que o aipager registrou (no formato acima: nomes de tipos, versões, lugares no código, no máximo 50), contagens por hora por 8 dias, o resumo do log por 2 dias, como terminaram as paradas recentes, e o estado das sugestões (quando sugeriu pela última vez, os nãos seguidos, quais bugs sugeriu em qual versão). Só você pode ler.
- `~/.local/state/aipager/report-sends.json`: quantos relatórios foram enviados ou tentados hoje.
- `~/.local/share/aipager/install.json`: quando o aipager rodou pela primeira vez neste computador (para as 48 horas acima).
- `~/.local/share/aipager/running.json`: escrito enquanto o aipager roda (o número do processo, a hora de início, se roda como serviço e o ID desta inicialização do sistema), para diferenciar uma queda de uma parada normal.

Nenhum deles tem um chat, uma mensagem, um nome, um caminho nem um token. O `aipager uninstall` os mantém, junto com o resto dos dados do aipager, para que uma reinstalação continue de onde parou; ele lista o que mantém e imprime o comando que apaga tudo isso (veja [`aipager uninstall`](commands.md#command-line-aipager-uninstall)). Para apagar só esses, exclua esses quatro arquivos: apagar a pasta inteira `~/.local/state/aipager` remove os dois primeiros (e junto o estado de idioma, `languages.json`), mas `install.json` e `running.json` precisam ser apagados pelo nome (não apague `~/.local/share/aipager`, que também tem as pastas das sessões do aipager).

## <a id="see-also"></a>Veja também

- [Solução de problemas](troubleshooting.md): falhas comuns e como resolver.
- [Modelo de segurança](security.md): no que o aipager mexe e o que sai pela rede.
- [Comandos do bot](commands.md): `/help`, `/settings` e a linha de comando.
