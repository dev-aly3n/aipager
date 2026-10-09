# Arabic (ar) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Modern Standard Arabic: plain, direct and friendly. Address the reader as أنت. Sound like the English: plain words, short sentences, no slang.

## Address
أنت

## Punctuation and quotation marks
Use the Arabic comma ، semicolon ؛ and question mark ؟, and « » for quotations. Right to left: write ← where the English has →; « » and › flip on their own and stay as they are.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.

## Digits
Arabic-Indic digits ٠-٩ for numbers written in the text (counts, percentages, sizes). Never in names, versions, paths, commands or code, and never inside a placeholder: aipager converts numeric placeholders itself.

## Never translated
Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App, GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob, WebFetch, Agent, AskUserQuestion), model names and aliases (opus, sonnet, haiku, fable, opusplan), commands (/new, /status, /x1, /mode auto), session names, @usernames, paths, code and anything inside a placeholder. These reach the text only as placeholders or verbatim; CI fails a translation that spells one differently.

## Placeholders and tags
Keep every {placeholder} and every HTML tag of the English, exactly once each. Never translate inside <code> or <pre>.
