# Russian (ru) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way Telegram's own Russian interface talks. Address the reader as ты. Everyday Russian, not a word-for-word copy of the English: short sentences, no officialese ("осуществляется", "данный", "в связи с"), no slang.

Russian past tense and short adjectives carry gender ("ты сделал", "готов"). The reader can be anyone, so say it without one: present tense, an impersonal phrase or a noun ("закрыто: было другое действие", not "ты перешёл к другому"). A person named by {person} or {who} gets Telegram's own convention, the "(а)" ending: "{person} остановил(а) {session}". Sessions are feminine (сессия: "готова", "завершена") and that is fine.

## Address
ты

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
- A keyboard word (status, stop, new, kill) is ONE word with no space: it is typed as a word.
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Авто, Спрашивать, Полный) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Режим Авто, 💬 Режим «Спрашивать», 🔓 Режим «Полный доступ»); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button.

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | сессия | сеанс |
| background agent | фоновый агент | |
| background shell command | фоновая команда (the first line under an answer adds "(shell)") | shell alone, оболочка |
| your message to Claude (prompt) | сообщение | промпт, запрос (запрос is a permission or question prompt) |
| compact (Claude Code's /compact) | сжать (сжать переписку) | компактировать |
| context window | контекст | контекстное окно |
| conversation | переписка | беседа, диалог |
| update | обновить, обновление | апдейт |
| restart | перезапустить, перезапуск | рестарт |
| load, loading | загрузить, загрузка… | |
| link | ссылка | линк |
| repository | репозиторий | репа |
| hung, frozen | завис | заморожен |
| crashed | упал, сбой | крашнулся |
| voice message | голосовое сообщение (голосовое where short) | войс |
| computer, machine | компьютер | машина, хост |
| default | по умолчанию | дефолт |
| private chat (DM) | личный чат | ЛС, директ |
| tool (Claude's tools) | инструмент | тул |
| idle (a session waiting for a message) | готова | простаивает |
| busy, working | работает, в работе | занята |
| waiting on you | ждёт твоего ответа | интерактивная |
| ended (a session no longer running) | завершена | закрыта, убита |
| end a session (Kill, End) | завершить | убить |
| start a session (/new) | запустить | |

## Vocabulary (one word per idea)
- session: сессия; turn: очередь where people take turns ("сейчас очередь другого человека"), задача for Claude's work on one message ("когда эта задача закончится"); card: карточка; permission: разрешение; a permission request: запрос разрешения; question: вопрос.
- allow: разрешить (answered: разрешено); deny: отклонить (answered: отклонено); stop: остановить (stopped: остановлена); resume: продолжить; queue: очередь; agent: агент; background: фоновый.
- owner: владелец; admin: админ; member: участник; chat: чат; group: группа; settings: настройки; folder: папка; model: модель; role: роль; mode: режим; "Mini App".
- Kept in English: commit, token, diff, log, ctx. A diff is "изменения (diff)" where it is a label.
- Buttons are infinitives, as Telegram's Russian buttons are: Разрешить, Отклонить, Остановить, Продолжить, Перезапустить, Удалить, Переименовать, Отмена, Закрыть, Повторить, Завершить, Отправить, Отправить сейчас.
- Same situation, same words: "Couldn't …" is "Не удалось …"; "Couldn't reach the server" is "Нет связи с сервером"; a refused restart is "Перезапуска не будет: …"; a stale button or card is "устарела/устарел"; a running session "работает".
- {clock} is a duration: "Готово ({clock})", "Работает ({clock})", never "с {clock}" or "после {clock}" (reads like a time of day).

## Plurals
one, few, many and other: 1 сессия, 2 сессии, 5 сессий, 21 сессия; other is for fractions only and reads like few. A verb after a counted phrase sits outside the plural, so it must agree with any count: "{agents} ещё в работе", not "работают". A one-time line with no {n} (the *_once keys) still writes the number: "1 раз", never "один раз" (operator decision 14).

