# Turkish (tr) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly. Address the reader as sen. Sound like the English: plain words, short sentences, no slang.

## Address
sen

## Punctuation and quotation marks
Use “ ” for quotations. Mind the dotted and dotless i (İ i, I ı).
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
- A keyboard word (status, stop, new, kill) is ONE word with no space: it is typed as a word. A Latin keyboard label must not be a valid session name (check 10: only a-z, 0-9, _ and - would be one), so it is either a reserved command word or carries a Turkish letter: Özet, Stop, Oluştur, Sonlandır; the sub-keyboards are Şablonlar › and Komutlar ›.
- A mode word (Auto, Ask) is ONE word after its emoji: 🤖 Otomatik, 💬 Sor.
- A default button label (Continue, Run tests...) stays short: it is a button.

## Words (the glossary's terms in context)
oturum (session), tur (turn: one prompt and Claude's work on it), kart (card), izin (permission; a permission prompt is an izin isteği), istem (prompt: what you send to Claude), soru (question), İzin ver / izin verildi (allow), Reddet / reddedildi (deny), Durdur / durduruldu (stop), Devam ettir / devam ediyor (resume), kuyruk (queue), ajan (agent), arka planda (background), sahip (owner), yönetici (admin), üye (member), sohbet (chat), grup (group), ayarlar (settings), güncelleme / güncelle (update), klasör (folder), model (model). Buttons are short imperatives (sen form): İzin ver, Reddet, Durdur.
- Suffixes never go on a placeholder directly (its value is not known, so vowel harmony cannot be right): write "{session} oturumu", "{session} için" or put the value after a colon.

## Plurals
one and other; a noun after a number stays singular in Turkish (5 oturum, never 5 oturumlar), so both forms are often the same.
- Sentence-initial İ lowercases to i̇ in software, so the glossary lists both forms (izin and İzin, istem and İstem); the vowel-dropping stem izn (iznin) is listed too.
