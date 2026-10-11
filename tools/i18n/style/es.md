# Spanish (es) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly, like the English. Address the reader as tú. Sound like the English: plain words, short sentences, no slang.

## Address
tú

## Punctuation and quotation marks
Use « » for quotations (“ ” inside them). Questions open with ¿. Never use ¡ or !.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.

## Digits
Latin digits 0-9; decimal comma in prose numbers only where a placeholder does not already format them.

## Never translated
Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App, GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob, WebFetch, Agent, AskUserQuestion), model names and aliases (opus, sonnet, haiku, fable, opusplan), commands (/new, /status, /x1, /mode auto), session names, @usernames, paths, code and anything inside a placeholder. These reach the text only as placeholders or verbatim; CI fails a translation that spells one differently.

## Placeholders and tags
Keep every {placeholder} and every HTML tag of the English, exactly once each. Never translate inside <code> or <pre>.

## Markers, keyboard words and modes
- Keep `**`, `_`, every HTML tag and every {placeholder} exactly as in the English: the same number of `**` and `_`, never a backtick.
- The four main-keyboard words (status, stop, new, kill) stay as the English words, exactly like the commands /status, /stop, /new and /kill they stand for. In a Latin-script language a translated word (for example "estado") is also a valid session name, and check 10 forbids a keyboard word that could hide a session called that.
- A navigation button that opens another keyboard ends with " ›" ("Plantillas ›", "Comandos ›"; the model list is "Modelo ›"), like "Model ›": the arrow keeps the label from ever being a session name.
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Automático, Preguntar, Total) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Modo Automático, 💬 Modo Preguntar, 🔓 Modo Acceso total); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Terms
- session: sesión; turn: turno; card: tarjeta; permission: permiso; prompt: prompt (the text sent to Claude; "mensaje" where it is just a message; "solicitud" for a permission prompt or a question waiting for an answer); question: pregunta.
- allow: permitir; deny: denegar (a refused request: rechazar); stop: detener; resume: reanudar; queue: cola; agent: agente; background: segundo plano ("en segundo plano").
- owner: propietario; admin: administrador; member: miembro; chat: chat; group: grupo; settings: ajustes; update: actualizar / actualización; folder: carpeta; model: modelo.
- Kept in English: commit, token, diff, log, ctx, prompt.
- Other recurring words: Kill (end a session's process) = Terminar; Restart = Reiniciar; Delete = Eliminar; Rename = Renombrar; Compact = Compactar; role = rol; the Mini App is feminine ("la Mini App"); a session name stands for a sesión (feminine: "x1 detenida").
