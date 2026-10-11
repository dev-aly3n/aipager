# Portuguese (Brazil) (pt-BR) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly, Brazilian usage. Address the reader as você. Sound like the English: plain words, short sentences, no slang.

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
- A mode word (mode.word.auto, mode.word.ask: Automático, Perguntar) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask) is what people see: the emoji, that same word and the word for mode (🤖 Modo Automático, 💬 Modo Perguntar); a sentence around {mode} must not add its own word for mode.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Terms
- session: sessão; turn: turno; card: cartão; permission: permissão; prompt: prompt (the text sent to Claude; "mensagem" where it is just a message; "pedido" for a permission prompt or a question waiting for an answer); question: pergunta.
- allow: permitir; deny: negar (a refused request: recusar); stop: interromper for a running turn, parar elsewhere; resume: retomar; queue: fila; agent: agente; background: segundo plano ("em segundo plano").
- owner: dono; admin: administrador; member: membro; chat: chat (conversa where it reads better); group: grupo; settings: configurações; update: atualizar / atualização; folder: pasta; model: modelo.
- Kept in English: commit, token, diff, log, ctx, prompt.
- Other recurring words: Kill (end a session's process) = Encerrar; Restart = Reiniciar; Delete = Excluir; Rename = Renomear; Compact = Compactar; role = função; "o Mini App" ("pelo Mini App"); a session name stands for a sessão (feminine: "x1 interrompida").
- In Portuguese the "one" plural form also covers 0: always write {n}, never the word "um".
