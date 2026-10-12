# Indonesian (id) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way Telegram's own Indonesian interface talks. Address the reader as kamu (decided: Anda is more formal). Everyday Indonesian, not a word-for-word copy of the English: short sentences, no officialese ("dilakukan", "tersebut" piled up), no slang. Indonesian has no grammatical gender, so "kamu" and {person} need no care there.

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
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Auto, Tanya, Penuh) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Mode Auto, 💬 Mode Tanya, 🔓 Mode Akses penuh); a sentence around {mode} must not add its own word for mode. "Tanya" is plain ASCII and could also be a session name: /mode treats it as the session when a live session has exactly that name, else as the mode (phase 5, 7b), so it no longer hides a session. The problem-report option "Ask me" is not the mode: it is "Tanya aku".
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button. Its payload stays English (Claude receives the English).

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | sesi | |
| background agent | agen latar belakang | |
| background shell command | perintah latar belakang (the first line under an answer adds "(shell)") | shell alone |
| your message to Claude (prompt) | pesan | prompt (only where the text is about prompts as such) |
| a permission or question prompt | permintaan (permintaan izin) | |
| compact (Claude Code's /compact) | ringkas (ringkas percakapan) | kompak |
| context window | konteks | jendela konteks |
| conversation | percakapan | dialog |
| update | perbarui, pembaruan | |
| restart | mulai ulang | |
| link | tautan | |
| repository | repo | repositori (fine in a long explanation) |
| hung, frozen | macet | membeku |
| crashed | crash | |
| voice message | pesan suara | voice note |
| computer, machine | komputer | mesin, host |
| default | bawaan | default |
| private chat (DM) | chat pribadi | DM |
| tool (Claude's tools) | alat | tool |
| idle (a session waiting for a message) | siap | menganggur |
| busy, working | sedang bekerja | sibuk |
| waiting on you | menunggu jawabanmu | interaktif |
| ended (a session no longer running) | sudah berakhir | mati |
| end a session (Kill, End) | akhiri | bunuh |
| start a session (/new) | mulai, jalankan | |

## Vocabulary (one word per idea)
- session sesi; turn: giliran where people take turns ("sekarang giliran orang lain"), tugas for Claude's work on one message ("saat tugas ini selesai"); card kartu; permission izin; a permission request permintaan izin; question pertanyaan.
- allow izinkan (answered: diizinkan); deny tolak (answered: ditolak); stop hentikan (stopped: dihentikan); resume lanjutkan; queue antrean; agent agen; background latar belakang.
- owner pemilik; admin admin; member anggota; chat chat; group grup; settings pengaturan; folder folder; model model; role peran; mode mode; "Mini App".
- Kept in English: commit, token, diff, log, ctx. A diff is "perubahan (diff)" where it is a label.
- Buttons: Izinkan, Tolak, Hentikan, Lanjutkan, Mulai ulang, Hapus, Ganti nama, Batal, Tutup, Coba lagi, Akhiri, Kirim, Kirim sekarang.
- Same situation, same words: "Couldn't …" is "Gagal …" or "… tidak bisa …" ("File tidak bisa diunduh"); "Couldn't reach the server" is "Tidak bisa terhubung ke server"; a refused restart is "Tidak dimulai ulang: …"; a stale button or card is "sudah usang"; a running session "sedang berjalan".
- {clock} is a duration: "Selesai ({clock})", "Bekerja ({clock})", never "setelah {clock}" (reads like a time of day).
- Use "kamu" (never "Anda"), and the active imperative for requests ("Kirim /new lagi").

## Plurals
other only: Indonesian does not mark the plural after a number (2 sesi).
