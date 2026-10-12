# Portuguese (Brazil) (pt-BR) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way a good Brazilian app talks to people (Telegram, WhatsApp, Nubank, iFood). Address the reader as você. Everyday Brazilian Portuguese, not European Portuguese and not a word-for-word copy of the English.

## Address
você

## Punctuation and quotation marks
Use “ ” for quotations.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.

## Digits
Latin digits 0-9.

## Never translated
Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App, GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob, WebFetch, Agent, AskUserQuestion), model names and aliases (opus, sonnet, haiku, fable, opusplan), commands (/new, /status, /x1, /mode auto), session names, @usernames, paths, code and anything inside a placeholder. These reach the text only as placeholders or verbatim; CI fails a translation that spells one differently.

## Placeholders and tags
Keep every {placeholder} and every HTML tag of the English, exactly once each. Never translate inside <code> or <pre>.

## Markers, keyboard words and modes
- Keep `**`, `_`, every HTML tag and every {placeholder} exactly as in the English: the same number of `**` and `_`, never a backtick.
- The four main-keyboard words (status, stop, new, kill) stay as the English words, exactly like the commands /status, /stop, /new and /kill they stand for. In a Latin-script language a translated word (for example "novo") is also a valid session name, and check 10 forbids a keyboard word that could hide a session called that.
- A navigation button that opens another keyboard ends with " ›" ("Templates ›", "Comandos ›"; the model list is "Modelo ›"), like "Model ›": the arrow keeps the label from ever being a session name.
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Automático, Perguntar, Total) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Modo Automático, 💬 Modo Perguntar, 🔓 Modo Acesso total); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | sessão | |
| background agent | agente em segundo plano | |
| background shell command | comando em segundo plano (the first line under an answer adds "(shell)") | shell alone, ordem |
| your message to Claude (prompt) | mensagem | prompt (only where the text is about prompts as such) |
| compact (Claude Code's /compact) | compactar (compactar a conversa) | |
| context window | contexto | janela de contexto |
| update | atualizar, atualização | |
| restart | reiniciar | |
| load, loading | carregar, carregando… | |
| link | link | vínculo, ligação |
| repository | repo | repositório (fine in a long explanation) |
| hung, frozen | travado (travou) | congelado |
| crashed | caiu, fechou com erro | crashou |
| voice message | áudio | mensagem de voz |
| computer, machine | computador | máquina |
| default | padrão | predefinido |
| private chat (DM) | chat privado | DM |
| idle (a session waiting for a message) | pronta | ociosa, inativa |
| busy, working | trabalhando | ocupada |
| waiting on you | esperando sua resposta | interativa |
| ended (a session no longer running) | encerrada | terminada |
| end a session (Kill, End) | encerrar | matar |

## Vocabulary (one word per idea)
- session: sessão (feminine: "x1 está pronta", "x1 encerrada"); turn: vez where people take turns ("a vez de outra pessoa"), tarefa for Claude's work on one message ("quando esta tarefa terminar"); card: cartão; permission: permissão; a permission request: pedido de permissão; question: pergunta.
- allow: permitir (answered: permitido); deny: negar (answered: negado); stop: parar (stopped: parada); resume: continuar / retomar (the /resume button: Continuar); queue: fila; agent: agente; background: em segundo plano.
- owner: dono; admin: admin; member: membro; chat: chat; group: grupo; settings: configurações; folder: pasta; model: modelo; role: função; "o Mini App" ("pelo Mini App").
- Kept in English: commit, token, diff, log, ctx. A diff is "alterações (diff)" where it is a label.
- Restart = Reiniciar; Delete = Excluir; Rename = Renomear; Cancel = Cancelar; Close = Fechar; Retry = Tentar de novo; End = Encerrar.
- The places in rep.area.* are written with their preposition ("no cartão de atividade", "na mensagem fixada"), because rep.offer.internal puts them straight after {error}: "(TypeError no cartão de atividade)", never "em o".

## Plurals
one, many and other. In Portuguese "one" also covers 0: always write {n}, never the word "um". many is for large round numbers: give it the same text as other.
