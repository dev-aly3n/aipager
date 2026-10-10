# Indonesian (id) style guide

The translator (Claude) reads this with the glossary before every batch.

## Tone
Plain, direct and friendly. Address the reader as kamu (decided: Anda is more formal). Sound like the English: plain words, short sentences, no slang.

## Address
kamu

## Punctuation and quotation marks
Use “ ” for quotations.
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
- The four main-keyboard words (status, stop, new, kill) stay as the English words, exactly like the commands /status, /stop, /new and /kill they stand for. A translated Indonesian word (for example "baru") is also a valid session name, and check 10 forbids a keyboard word that could hide a session called that.
- A navigation button that opens another keyboard ends with " ›" ("Templat ›", "Perintah ›"; the model list is "Model ›"), like "Model ›": the arrow keeps the label from ever being a session name.
- A mode word (Auto, Tanya) is ONE word after its emoji: people type it after /mode. 🤖 Auto stays as it is and Ask is 💬 Tanya, and every text names the modes Auto and Tanya ("mode Tanya"). "Tanya" is plain ASCII and could also be a session name: /mode treats it as the session when a live session has exactly that name, else as the mode (phase 5, 7b), so it no longer hides a session. The problem-report option "Ask me" is not the mode: it is "Tanya aku".
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Terms
- session sesi; turn giliran; card kartu; permission izin; prompt prompt (a message to Claude: pesan; a request waiting for an answer: permintaan); question pertanyaan; allow izinkan; deny tolak; stop hentikan (berhenti); resume lanjutkan; queue antrean; agent agen (a subagent: subagen); background latar belakang; owner pemilik; admin admin; member anggota; chat chat; group grup; settings pengaturan; update perbarui / pembaruan; folder folder; model model.
- Kill (end a session's process) akhiri; Restart mulai ulang; Delete hapus; Rename ganti nama; Compact ringkas; Retry coba lagi; Cancel batal; Back kembali; role peran; transcript transkrip; daemon daemon; install pasang.
- Kept in English: commit, token, diff, log, ctx, prompt, shell (a background shell command).
- Use "kamu" (never "Anda"), and the active imperative for requests ("Kirim /new lagi").
