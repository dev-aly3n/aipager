# Italian (it) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way a good Italian app talks to people (Telegram, Satispay, Subito). Address the reader as tu. Everyday Italian, not a word-for-word copy of the English.

The reader can be anyone: never give them a gender. A past participle about "tu" with essere ("sei passato", "sei stato") picks one, so say it with avere or another verb ("hai fatto altro", not "sei passato ad altro"). Sessions are feminine ("è pronta") and that is fine.

## Address
tu

## Punctuation and quotation marks
Use « » for quotations and for the names of buttons quoted in a sentence.
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
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Automatico, Chiedi, Completo) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Modalità «Automatico», 💬 Modalità «Chiedi», 🔓 Modalità «Completo»); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | sessione | |
| background agent | agente in background | agente in secondo piano |
| background shell command | comando in background (the first line under an answer adds "(shell)") | shell alone, ordine |
| your message to Claude (prompt) | messaggio | prompt (only where the text is about prompts as such) |
| compact (Claude Code's /compact) | compattare (compattare la conversazione) | |
| context window | contesto | finestra di contesto |
| update | aggiornare, aggiornamento | |
| restart | riavviare | |
| load, loading | caricare, caricamento… | |
| link | link | collegamento |
| repository | repo | repository (fine in a long explanation) |
| hung, frozen | bloccato | congelato |
| crashed | è andato in crash | |
| voice message | vocale | nota vocale |
| computer, machine | computer | macchina |
| default | predefinito | |
| private chat (DM) | chat privata | DM |
| idle (a session waiting for a message) | pronta | inattiva |
| busy, working | al lavoro | occupata |
| waiting on you | aspetta la tua risposta | interattiva |
| ended (a session no longer running) | terminata | chiusa |
| end a session (Kill, End) | terminare | uccidere |
| start a session (/new) | avviare una sessione | |

## Vocabulary (one word per idea)
- session: sessione (feminine: "x1 è pronta", "x1 terminata"); turn: turno where people take turns ("il turno di un'altra persona"), attività for Claude's work on one message ("quando questa attività finisce"); card: scheda; permission: permesso; a permission request: richiesta di permesso; question: domanda.
- allow: consentire (answered: consentito); deny: negare (answered: negato); stop: fermare (stopped: fermata); resume: riprendere; queue: coda; agent: agente; background: in background.
- owner: proprietario; admin: admin; member: membro; chat: chat; group: gruppo; settings: impostazioni; folder: cartella; model: modello; role: ruolo; mode: modalità; "la Mini App".
- Kept in English: commit, token, diff, log, ctx, background. A diff is "modifiche (diff)" where it is a label.
- Restart = Riavvia; Delete = Elimina; Rename = Rinomina; Cancel = Annulla; Close = Chiudi; Retry = Riprova; End = Termina.
- Same situation, same words: "Couldn't …" is "Non è stato possibile …" (not "Impossibile …"); "Couldn't reach the server" is "Non è stato possibile collegarsi al server"; a refused restart is "Il riavvio non parte: …"; a stale button or card is "è vecchio/vecchia"; a running session is "in esecuzione".
- {clock} is a duration: "Finito ({clock})", "Al lavoro ({clock})", never "da {clock}" (reads like a time of day).

## Plurals
one, many and other. many is for large round numbers: give it the same text as other. The count goes first as in the English ({n} sessioni).
