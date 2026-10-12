# Spanish (es) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way a good app talks to people in Mexico, Colombia, Argentina or Spain (Telegram, WhatsApp, Mercado Libre). Address the reader as tú. Neutral Spanish that every country reads as its own: no vosotros, no voseo, no regional slang. Say what the line means where it appears, not a word-for-word copy of the English.

## Address
tú

## Punctuation and quotation marks
Use « » for quotations (" " inside them). Questions open with ¿. Never use ¡ or !.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.
- Sentence case: "Modo Automático" keeps its capitals because it is a name; other labels capitalise only the first word.

## Digits
Latin digits 0-9.

## Never translated
Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App, GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob, WebFetch, Agent, AskUserQuestion), model names and aliases (opus, sonnet, haiku, fable, opusplan), commands (/new, /status, /x1, /mode auto), session names, @usernames, paths, code and anything inside a placeholder. These reach the text only as placeholders or verbatim; CI fails a translation that spells one differently.

## Placeholders and tags
Keep every {placeholder} and every HTML tag of the English, exactly once each. Never translate inside <code> or <pre>.

## Markers, keyboard words and modes
- Keep `**`, `_`, every HTML tag and every {placeholder} exactly as in the English: the same number of `**` and `_`, never a backtick.
- The four main-keyboard words (status, stop, new, kill) stay as the English words, exactly like the commands /status, /stop, /new and /kill they stand for. In a Latin-script language a translated word (for example "estado") is also a valid session name, and check 10 forbids a keyboard word that could hide a session called that.
- A navigation button that opens another keyboard ends with " ›" ("Plantillas ›", "Comandos ›"; the model list is "Modelo ›"), like "Model ›": the arrow keeps the label from ever being a session name.
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Automático, Preguntar, Total) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Modo Automático, 💬 Modo Preguntar, 🔓 Modo Acceso total); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode: the switch buttons, the "instead" buttons and the busy confirms say "Modo …", as mode.auto does. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | sesión | |
| background agent | agente en segundo plano | |
| background shell command | comando en segundo plano (the first line under an answer adds "(shell)") | shell alone, orden |
| your message to Claude (prompt) | mensaje | prompt (only where the text is about prompts as such) |
| compact (Claude Code's /compact) | compactar (compactar la conversación) | |
| context window | contexto | ventana de contexto |
| update | actualizar, actualización | |
| restart | reiniciar | |
| load, loading | cargar, cargando… | |
| link | enlace | vínculo |
| repository | repo | repositorio (fine in a long explanation) |
| hung, frozen | colgado (se quedó colgado) | congelado |
| crashed | se cerró por un error | crasheó |
| voice message | audio | nota de voz, mensaje de voz |
| screenshot | captura | captura de pantalla (fine where there is room) |
| computer, machine | computadora | ordenador, máquina |
| default | por defecto | predeterminado |
| private chat (DM) | chat privado | DM, MD |
| idle (a session waiting for a message) | lista | inactiva |
| busy, working | trabajando | ocupada |
| waiting on you | esperando tu respuesta | interactiva |
| ended (a session that is no longer running) | terminada | cerrada ("cerrar sesión" means log out in Spanish apps), finalizada |
| end a session (Kill, End) | terminar | matar, cerrar |
| start a session (/new) | crear una sesión, arrancar | iniciar sesión (it means sign in) |

## Vocabulary (one word per idea)
- session: sesión (feminine: "x1 está lista", "x1 cerrada"); turn: turno where people take turns ("el turno de otra persona"), tarea for Claude's work on one message ("cuando termine esta tarea"); card: tarjeta; permission: permiso; a permission request: solicitud de permiso; question: pregunta.
- allow: permitir (answered: permitido); deny: rechazar (answered: rechazado); stop: detener (stopped: detenido); resume: continuar / reanudar (the /resume button: Continuar); queue: cola; agent: agente; background: en segundo plano.
- owner: propietario; admin: admin; member: miembro; chat: chat; group: grupo; settings: ajustes; folder: carpeta; model: modelo; role: rol; the Mini App is feminine ("la Mini App").
- Kept in English: commit, token, diff, log, ctx. A diff is "cambios (diff)" where it is a label.
- Restart = Reiniciar; Delete = Eliminar; Rename = Cambiar nombre; Cancel = Cancelar; Close = Cerrar; Retry = Reintentar.

## Plurals
one, many and other. many is used for large round numbers (1 000 000 sesiones): give it the same text as other. The count goes first as in the English ({n} sesiones).
