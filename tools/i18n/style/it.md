# Italian (it) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly. Address the reader as tu. Sound like the English: plain words, short sentences, no slang.

## Address
tu

## Punctuation and quotation marks
Use « » or “ ” for quotations, consistently.
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
- The four main-keyboard words (status, stop, new, kill) stay as the English words, exactly like the commands /status, /stop, /new and /kill they stand for. In a Latin-script language a translated word (for example "stato") is also a valid session name, and check 10 forbids a keyboard word that could hide a session called that.
- A navigation button that opens another keyboard ends with " ›" ("Template ›", "Comandi ›"; the model list is "Modello ›"), like "Model ›": the arrow keeps the label from ever being a session name.
- A mode word (mode.word.auto, mode.word.ask: Automatico, Chiedi) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask) is what people see: the emoji, that same word and the word for mode (🤖 Modalità «Automatico», 💬 Modalità «Chiedi»); a sentence around {mode} must not add its own word for mode.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Terms
- session: sessione; turn: turno; card: scheda; permission: permesso; prompt: prompt (the text sent to Claude; "messaggio" where it is just a message; "richiesta" for a permission prompt or a question waiting for an answer); question: domanda.
- allow: consentire ("Consenti"); deny: negare ("Nega"); stop: fermare; resume: riprendere; queue: coda; agent: agente; background: in background / in secondo piano.
- owner: proprietario; admin: amministratore; member: membro; chat: chat; group: gruppo; settings: impostazioni; update: aggiornare / aggiornamento; folder: cartella; model: modello.
- Kept in English: commit, token, diff, log, ctx, prompt.
- Other recurring words: Kill (end a session's process) = Termina / terminare; Restart = Riavvia; Delete = Elimina; Rename = Rinomina; Compact = Compatta; role = ruolo; mode = modalità; "la Mini App"; a session name stands for a sessione (feminine: "x1 fermata").
