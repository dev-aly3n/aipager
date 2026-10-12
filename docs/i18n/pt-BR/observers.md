# Bots observadores

<!-- i18n-source: docs/observers.md @ 6fc084f -->

> Esta página é uma tradução da página em inglês; se as duas não baterem, vale a versão em inglês. ([English](../../observers.md))

Bots do Telegram somente leitura que repetem os avisos do bot principal. Recebem resumos, alertas e erros, mas não podem controlar as sessões.

## <a id="setup"></a>Configuração

1. Crie um bot com o [@BotFather](https://t.me/BotFather).
2. Abra um chat com o bot e mande `/start`.
3. Descubra o ID do chat (mande uma mensagem e depois abra `https://api.telegram.org/bot<TOKEN>/getUpdates`).
4. Defina `OBSERVER_BOTS` no ambiente do aipager. Se você roda o aipager no terminal, exporte antes de `aipager start`; para o serviço do systemd:

```sh
systemctl --user edit aipager    # adicione abaixo de [Service]:
# Environment=OBSERVER_BOTS=<bot_token>:<chat_id>
```

Vários observadores (separados por vírgula):

```
OBSERVER_BOTS=111:AAA_first:12345,222:BBB_second:67890
```

O formato é `token:chat_id`; na leitura, o separador é o **último** dois-pontos (o token do bot já tem dois-pontos no meio).

5. Reinicie o aipager.

## <a id="what-observers-receive"></a>O que os observadores recebem

Os observadores recebem estes avisos de **todos os chats em que o bot está**, de cada chat privado e cada grupo, sem nenhum filtro: configure um só para um chat a quem você confiaria tudo isso. Cada aviso chega no idioma do chat de onde veio.

| Evento | Exemplo |
|-------|---------|
| Tarefa concluída | `💬 x1 · Concluído (2:05, +12 -3)`: só a linha do resultado, não a resposta; e também o registro completo como arquivo `.md` quando a resposta ou o cartão foram cortados |
| Um trabalho em segundo plano terminou | `✅ x1 · Concluído (...)`, ou `⚠️ x1 · Concluído (...)` quando um agente sumiu ou não houve notícia do fim de um comando em segundo plano |
| Erro da API | `⚠️ x1 · Os servidores da Anthropic estão sobrecarregados. Tente de novo em instantes.` (sem botão de tentar de novo) |
| Aviso de contexto | `⚠️ x1 · Contexto em 82% - logo vai ser compactado automaticamente` |
| Compactando | `🔄 x1 · Compactando a conversa` |
| Compactação concluída | `📦 x1 · Conversa compactada: 82% → 4%` |
| Um pedido que ainda está esperando | `⬆️ x1 · ainda esperando sua resposta lá em cima`, com a permissão ou a pergunta que ele espera |
| Uma sessão trabalhando sem dar notícias | `⏳ x1 · ainda trabalhando (10 min sem novidades)` |
| Uma mensagem que o Claude Code não recebeu | `⚠️ x1 · O Claude Code não recebeu a mensagem` |
| A sessão foi encerrada | `🔴 x1 · Sessão encerrada` |

## <a id="what-observers-dont-receive"></a>O que os observadores não recebem

- A animação do cartão de atividade
- As atualizações das ferramentas que rodam
- Os pedidos de permissão e os botões deles (só o lembrete de “ainda esperando” acima diz o que um pedido quer)
- As perguntas do AskUserQuestion
- Nenhum teclado nem botão

Os observadores não guardam nenhum estado: a mensagem é enviada e pronto. Se um observador falhar, isso não afeta o bot principal.
