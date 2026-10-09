# Spanish (es) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly, like the English. Address the reader as tú. Sound like the English: plain words, short sentences, no slang.

## Address
tú

## Punctuation and quotation marks
Use « » for quotations (“ ” inside them). Questions open with ¿. Never use ¡ or !.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.

## Digits
Latin digits 0-9; decimal comma in prose numbers only where a placeholder does not already format them.

## Never translated
Product names (Claude, Claude Code, Anthropic, aipager, Telegram, Mini App, GitHub, PyPI), Claude Code's tool names (Bash, Read, Write, Edit, Grep, Glob, WebFetch, Agent, AskUserQuestion), model names and aliases (opus, sonnet, haiku, fable, opusplan), commands (/new, /status, /x1, /mode auto), session names, @usernames, paths, code and anything inside a placeholder. These reach the text only as placeholders or verbatim; CI fails a translation that spells one differently.

## Placeholders and tags
Keep every {placeholder} and every HTML tag of the English, exactly once each. Never translate inside <code> or <pre>.
