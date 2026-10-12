# Ukrainian (uk) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way Telegram's own Ukrainian interface talks. Address the reader as ти. Everyday Ukrainian, not a word-for-word copy of the English and never a copy of the Russian: Ukrainian words and Ukrainian word order (і, ї, є, ґ; "будь ласка", not calques). Short sentences, no officialese ("здійснюється", "даний"), no slang.

Ukrainian past tense carries gender ("ти зробив"). The reader can be anyone, so say it without one: present tense, an impersonal phrase or a noun ("закрито: була інша дія", not "ти перейшов до іншого"). A person named by {person} or {who} gets the "(-ла)" ending: "{person} зупинив(-ла) {session}". Sessions are feminine (сесія: "готова", "завершена") and that is fine.

## Address
ти

## Punctuation and quotation marks
Use « » for quotations („ “ inside them).
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
- A keyboard word (status, stop, new, kill) is ONE word with no space: it is typed as a word (статус, стоп, нова, завершити).
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Авто, Питати, Повний) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Режим Авто, 💬 Режим «Питати», 🔓 Режим «Повний доступ»); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button.

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | сесія | сеанс |
| background agent | фоновий агент | |
| background shell command | фонова команда (the first line under an answer adds "(shell)") | shell alone, оболонка |
| your message to Claude (prompt) | повідомлення | промпт, запит (запит is a permission or question prompt) |
| compact (Claude Code's /compact) | стиснути (стиснути розмову) | компактувати |
| context window | контекст | контекстне вікно |
| conversation | розмова | діалог, бесіда |
| update | оновити, оновлення | апдейт |
| restart | перезапустити, перезапуск | рестарт |
| load, loading | завантажити, завантаження… | |
| link | посилання | лінк |
| repository | репозиторій | репа |
| hung, frozen | зависла | заморожена |
| crashed | впала, збій | крашнулась |
| voice message | голосове повідомлення (голосове where short) | войс |
| computer, machine | комп'ютер | машина, хост |
| default | за замовчуванням ("типово" on a tight button: "{mode} (типово)") | дефолт |
| private chat (DM) | особистий чат | ЛС, приват |
| tool (Claude's tools) | інструмент | тул |
| idle (a session waiting for a message) | готова | простоює |
| busy, working | працює, у роботі | зайнята |
| waiting on you | чекає на твою відповідь | інтерактивна |
| ended (a session no longer running) | завершена | закрита, вбита |
| end a session (Kill, End) | завершити | вбити |
| start a session (/new) | запустити | |

## Vocabulary (one word per idea)
- session: сесія; turn: черга where people take turns ("зараз черга іншої людини"), завдання for Claude's work on one message ("коли це завдання закінчиться"); card: картка; permission: дозвіл; a permission request: запит на дозвіл; question: питання.
- allow: дозволити (answered: дозволено); deny: відхилити (answered: відхилено); stop: зупинити (stopped: зупинена); resume: продовжити; queue: черга; agent: агент; background: фоновий.
- owner: власник; admin: адмін; member: учасник; chat: чат; group: група; settings: налаштування; folder: папка; model: модель; role: роль; mode: режим; "Mini App".
- Kept in English: commit, token, diff, log, ctx. A diff is "зміни (diff)" where it is a label.
- Buttons are infinitives, as Telegram's Ukrainian buttons are: Дозволити, Відхилити, Зупинити, Продовжити, Перезапустити, Видалити, Перейменувати, Скасувати, Закрити, Повторити, Завершити, Надіслати, Надіслати зараз.
- Same situation, same words: "Couldn't …" is "Не вдалося …"; "Couldn't reach the server" is "Немає зв'язку з сервером"; a refused restart is "Перезапуску не буде: …"; a stale button or card is "застаріла/застарів"; a running session "працює". The apostrophe is the plain ' (комп'ютер, зв'язок).
- {clock} is a duration: "Готово ({clock})", "Працює ({clock})", never "з {clock}" or "після {clock}" (reads like a time of day).

## Plurals
one, few, many and other: 1 сесія, 2 сесії, 5 сесій, 21 сесія; other is for fractions only and reads like few. A verb after a counted phrase sits outside the plural, so it must agree with any count: "{agents} ще в роботі".
