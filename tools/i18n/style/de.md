# German (de) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly, the way a good German app talks to people (Telegram, WhatsApp, the German Android and iOS settings). Address the reader as du (decided: modern German developer tools use du; Sie would be stiffer than the English). Everyday German, not a word-for-word copy of the English: short sentences, no officialese (no "Vorgang", "erfolgreich durchgeführt", "seitens"), no slang.

German has no gendered "du" in the past tense, so "du hast etwas anderes gemacht" is safe; say a person's action with them as the subject ("{person} hat {session} beendet").

## Address
du

## Punctuation and quotation marks
Use „ “ for quotations. Capitalise nouns as usual; keep compound words readable.
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
- A keyboard word (status, stop, new, kill) is ONE word with no space: it is typed as a word. A Latin keyboard label must not be a valid session name (check 10: only a-z, 0-9, _ and - would be one), so it is either a reserved command word or carries a character outside that set: Status, Stop, Neu…, Beenden… (not Schließen, which the close buttons use); the sub-keyboards are Vorlagen › and Befehle ›.
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Auto, Fragen, Vollzugriff) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Auto-Modus, 💬 Fragen-Modus, 🔓 Vollzugriff-Modus); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- The "instead" buttons on /new (mode.*_instead) say exactly what the switch buttons say ("💬 Zum Fragen-Modus wechseln"), not a word for "instead" (operator, 2026-10-16: a literal "instead" read oddly on a button).
- A default button label (Continue, Run tests...) stays short: it is a button.

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | Sitzung (Telegram's own German word) | Session |
| background agent | Hintergrund-Agent | |
| background shell command | Hintergrundbefehl, Befehl im Hintergrund (the first line under an answer adds "(shell)") | Shell alone, Kommando |
| your message to Claude (prompt) | Nachricht | Prompt (only where the text is about prompts as such) |
| compact (Claude Code's /compact) | komprimieren (den Kontext komprimieren) | kompaktieren |
| context window | Kontext | Kontextfenster |
| update | Update, aktualisieren | Aktualisierung (fine in a long sentence) |
| restart | neu starten, Neustart | |
| load, loading | laden, wird geladen… | |
| link | Link | Verknüpfung |
| repository | Repo | Repository (fine in a long explanation) |
| hung, frozen | hängt | eingefroren |
| crashed | abgestürzt | gecrasht |
| voice message | Sprachnachricht | Voice-Nachricht |
| computer, machine | Computer | Maschine, Rechner, Host |
| default | Standard | Default, Vorgabe |
| private chat (DM) | privater Chat | DM, Direktnachricht |
| tool (Claude's tools) | Tool | Werkzeug |
| idle (a session waiting for a message) | bereit | inaktiv, untätig |
| busy, working | arbeitet | beschäftigt |
| waiting on you | wartet auf dich | interaktiv |
| ended (a session no longer running) | beendet | geschlossen |
| end a session (Kill, End) | beenden | killen, abschießen |
| start a session (/new) | starten | |

## Vocabulary (one word per idea)
- session: Sitzung; turn: dran sein where people take turns ("gerade ist eine andere Person dran"), Aufgabe for Claude's work on one message ("wenn diese Aufgabe fertig ist"); card: Karte; permission: Berechtigung; a permission request: Berechtigungsanfrage; question: Frage.
- allow: erlauben (answered: erlaubt); deny: ablehnen (answered: abgelehnt); stop: stoppen (stopped: gestoppt); resume: fortsetzen; queue: Warteschlange; agent: Agent; background: im Hintergrund.
- owner: Besitzer; admin: Admin; member: Mitglied; chat: Chat; group: Gruppe; settings: Einstellungen; folder: Ordner; model: Modell; role: Rolle; mode: Modus; "die Mini App".
- Kept in English: commit, token, diff, log, ctx. A diff is "Änderungen (diff)" where it is a label.
- Buttons are infinitives, as German apps write them: Erlauben, Ablehnen, Stoppen, Fortsetzen, Neu starten, Löschen, Umbenennen, Abbrechen, Schließen, Erneut versuchen, Beenden, Senden, Jetzt senden.
- Same situation, same words: "Couldn't …" is "… konnte nicht … werden" ("Die Datei konnte nicht geladen werden"); "Couldn't reach the server" is "Keine Verbindung zum Server"; a refused restart is "Kein Neustart: …"; a stale button or card is "veraltet"; a running session "läuft".
- {clock} is a duration: "Fertig ({clock})", "Arbeitet ({clock})", never "seit {clock}" or "nach {clock}" (reads like a time of day).
- Button names quoted in a sentence go in „ “: Tippe auf „Jetzt senden“.
- The places in rep.area.* are written with their preposition ("in der Arbeitskarte", "beim Senden der Nachrichten an Claude"), because rep.offer.internal puts them straight after {error}: "(TypeError in der Arbeitskarte)", never "in die".

## Plurals
one and other. The count goes first as in the English ({n} Sitzungen).
