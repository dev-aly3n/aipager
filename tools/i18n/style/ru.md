# Russian (ru) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly. Address the reader as ты, as Telegram's own Russian interface does. Sound like the English: plain words, short sentences, no slang.

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
