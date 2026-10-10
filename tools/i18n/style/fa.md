# Persian (fa) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly. Address the reader as شما (decided: the standard form in Persian apps; تو would read as rude). Sound like the English: plain words, short sentences, no slang.

## Address
شما

## Punctuation and quotation marks
Use the Persian comma ، semicolon ؛ and question mark ؟, and « » for quotations. Use the Persian forms ی and ک, and a zero-width non-joiner where Persian spelling needs one. Right to left: write ← where the English has →; « » and › flip on their own and stay as they are.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.

## Digits
Persian digits ۰-۹ for numbers written in the text (counts, percentages, sizes). Never in names, versions, paths, commands or code, and never inside a placeholder: aipager converts numeric placeholders itself.

## Never translated
Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App, GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob, WebFetch, Agent, AskUserQuestion), model names and aliases (opus, sonnet, haiku, fable, opusplan), commands (/new, /status, /x1, /mode auto), session names, @usernames, paths, code and anything inside a placeholder. These reach the text only as placeholders or verbatim; CI fails a translation that spells one differently.

## Placeholders and tags
Keep every {placeholder} and every HTML tag of the English, exactly once each. Never translate inside <code> or <pre>.

## Markers, keyboard words and modes
- Keep `**`, `_`, every HTML tag and every {placeholder} exactly as in the English: the same number of `**` and `_`, never a backtick.
- A keyboard word (status, stop, new, kill) is ONE word with no space: it is typed as a word.
- A mode word (mode.word.auto, mode.word.ask: خودکار, پرسش) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask) is what people see: the emoji, that same word and the word for mode (🤖 مود خودکار, 💬 مود پرسش); a sentence around {mode} must not add its own word for mode.
- A default button label (Continue, Run tests...) stays short: it is a button.
