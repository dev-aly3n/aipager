# French (fr) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly. Address the reader as vous (decided: French developer tools use vous; tu reads as marketing). Sound like the English: plain words, short sentences, no slang.

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
- A mode word (mode.word.auto, mode.word.ask: Automatique, Demander) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask) is what people see: the emoji, that same word and the word for mode (🤖 Mode Automatique, 💬 Mode Demander); a sentence around {mode} must not add its own word for mode.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Terms
- session: session; turn: tour; card: carte; permission: autorisation; prompt: prompt (the text sent to Claude; "message" where it is just a message; "demande" for a permission prompt or a question waiting for an answer); question: question.
- allow: autoriser; deny: refuser; stop: arrêter; resume: reprendre; queue: file d'attente; agent: agent; background: arrière-plan ("en arrière-plan").
- owner: propriétaire; admin: administrateur; member: membre; chat: discussion (Telegram's own French word); group: groupe; settings: paramètres; update: mise à jour / mettre à jour; folder: dossier; model: modèle.
- Kept in English: commit, token, diff, log, ctx, prompt.
- Other recurring words: Kill (end a session's process) = Terminer; Restart = Redémarrer; Delete = Supprimer; Rename = Renommer; Compact = Compacter; role = rôle; "la Mini App"; a session name stands for a session (feminine: "x1 arrêtée").
- In French the "one" plural form also covers 0: always write {n}, never the word "un".
