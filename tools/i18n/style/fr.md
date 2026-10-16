# French (fr) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way a good French app talks to people (Telegram, Doctolib, Lydia). Address the reader as vous (decided: French developer tools use vous; tu reads as marketing). Everyday French, not a word-for-word copy of the English.

## Address
vous

## Punctuation and quotation marks
Use « » with a no-break space (U+00A0) inside; a no-break space (U+00A0) before : ; and ?. (The ordinary no-break space, not the narrow U+202F: every Telegram client and font draws it, and the keyboard resolver folds it like a space.)
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
- The four main-keyboard words (status, stop, new, kill) stay as the English words, exactly like the commands /status, /stop, /new and /kill they stand for. In a Latin-script language a translated word (for example "nouvelle") is also a valid session name, and check 10 forbids a keyboard word that could hide a session called that.
- A navigation button that opens another keyboard ends with " ›" ("Modèles ›", "Commandes ›"; the model list is "Modèle IA ›", so the two never read alike), like "Model ›": the arrow keeps the label from ever being a session name.
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Automatique, Demander, Complet) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Mode Automatique, 💬 Mode Demander, 🔓 Mode Accès complet); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- The "instead" buttons on /new (mode.*_instead) say exactly what the switch buttons say ("💬 Passer en Mode Demander"), not a word for "instead" (operator, 2026-10-16: a literal "instead" read oddly on a button).
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | session | |
| background agent | agent en arrière-plan | |
| background shell command | commande en arrière-plan (the first line under an answer adds "(shell)") | shell alone, ordre |
| your message to Claude (prompt) | message | prompt (only where the text is about prompts as such) |
| compact (Claude Code's /compact) | compacter (compacter la conversation) | |
| context window | contexte | fenêtre de contexte |
| update | mise à jour, mettre à jour | update |
| restart | redémarrer | relancer (fine for a session), rebooter |
| load, loading | charger, chargement… | |
| link | lien | |
| repository | dépôt | repository |
| hung, frozen | bloqué | gelé |
| crashed | a planté | a crashé |
| voice message | message vocal | note vocale |
| computer, machine | ordinateur | machine |
| default | par défaut | |
| private chat (DM) | discussion privée | DM, MP |
| idle (a session waiting for a message) | prête | inactive, au repos |
| busy, working | au travail (status line), travaille | occupée |
| waiting on you | attend votre réponse | interactive |
| ended (a session no longer running) | terminée | fermée ("fermer la session" means log out) |
| end a session (Kill, End) | terminer | tuer, fermer |
| start a session (/new) | lancer, démarrer une session | ouvrir une session (it means log in) |

## Vocabulary (one word per idea)
- session: session (feminine: "x1 est prête", "x1 terminée"); turn: tour where people take turns ("le tour d'une autre personne"), tâche for Claude's work on one message ("quand cette tâche sera finie"); card: carte; permission: autorisation; a permission request: demande d'autorisation; question: question.
- allow: autoriser (answered: autorisé); deny: refuser (answered: refusé); stop: arrêter (stopped: arrêtée); resume: reprendre; queue: file d'attente; agent: agent; background: en arrière-plan.
- owner: propriétaire; admin: admin; member: membre; chat: discussion (Telegram's own French word); group: groupe; settings: paramètres; folder: dossier; model: modèle; role: rôle; "la Mini App".
- Kept in English: commit, token, diff, log, ctx. A diff is "modifications (diff)" where it is a label.
- Restart = Redémarrer; Delete = Supprimer; Rename = Renommer; Cancel = Annuler; Close = Fermer; Retry = Réessayer; End = Terminer.

## Plurals
one, many and other. In French "one" also covers 0: always write {n}, never the word "un". many is for large round numbers: give it the same text as other.
