# Turkish (tr) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Plain, direct and friendly, the way Telegram's own Turkish interface talks. Address the reader as sen. Everyday Turkish, not a word-for-word copy of the English: short sentences, no officialese ("gerçekleştirilmektedir", "söz konusu"), no slang. Turkish has no grammatical gender, so "sen" and {person} need no care there.

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
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: Otomatik, Sor, Tam) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 Otomatik mod, 💬 Sor modu, 🔓 Tam erişim modu); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- The "instead" buttons on /new (mode.*_instead) say exactly what the switch buttons say ("💬 Sor moduna geç"), not a word for "instead" (operator, 2026-10-16: a literal "instead" read oddly on a button).
- A default button label (Continue, Run tests...) stays short: it is a button.

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | oturum | seans |
| background agent | arka plan ajanı | |
| background shell command | arka plan komutu (the first line under an answer adds "(shell)") | shell alone, kabuk |
| your message to Claude (prompt) | mesaj | istem (only where the text is about prompts as such) |
| a permission or question prompt | istek (izin isteği) | |
| compact (Claude Code's /compact) | sıkıştırmak (konuşmayı sıkıştır) | kompakt etmek |
| context window | bağlam | bağlam penceresi |
| conversation | konuşma | diyalog |
| update | güncellemek, güncelleme | |
| restart | yeniden başlatmak | |
| load, loading | yüklemek, yükleniyor… | |
| link | bağlantı | |
| repository | repo | depo |
| hung, frozen | takıldı | dondu |
| crashed | çöktü | |
| voice message | sesli mesaj | ses notu |
| computer, machine | bilgisayar | makine, sunucu |
| default | varsayılan | |
| private chat (DM) | özel sohbet | DM |
| tool (Claude's tools) | araç | tool |
| idle (a session waiting for a message) | hazır | boşta |
| busy, working | çalışıyor | meşgul |
| waiting on you | yanıtını bekliyor | etkileşimli |
| ended (a session no longer running) | sona erdi | öldü |
| end a session (Kill, End) | sonlandırmak | öldürmek |
| start a session (/new) | başlatmak | |

## Vocabulary (one word per idea)
- session: oturum; turn: sıra where people take turns ("şu an başka birinin sırası"), görev for Claude's work on one message ("bu görev bitince"); card: kart; permission: izin; a permission request: izin isteği; question: soru.
- allow: izin vermek (answered: izin verildi); deny: reddetmek (answered: reddedildi); stop: durdurmak (stopped: durduruldu); resume: devam ettirmek; queue: kuyruk; agent: ajan; background: arka plan.
- owner: sahip; admin: yönetici; member: üye; chat: sohbet; group: grup; settings: ayarlar; folder: klasör; model: model; role: rol; mode: mod; "Mini App".
- Kept in English: commit, token, diff, log, ctx. A diff is "değişiklikler (diff)" where it is a label.
- Buttons are short imperatives (sen form): İzin ver, Reddet, Durdur, Devam ettir, Yeniden başlat, Sil, Yeniden adlandır, İptal, Kapat, Tekrar dene, Sonlandır, Gönder, Şimdi gönder.
- Same situation, same words: "Couldn't …" is "… yapılamadı" ("Dosya indirilemedi"); "Couldn't reach the server" is "Sunucuya bağlanılamadı"; a refused restart is "Yeniden başlatma yok: …"; a stale button or card is "eskidi"; a running session "çalışıyor".
- {clock} is a duration: "Bitti ({clock})", "Çalışıyor ({clock})", never "{clock} sonra" (reads like a time of day).
- Suffixes never go on a placeholder directly (its value is not known, so vowel harmony cannot be right): write "{session} oturumu", "{session} için" or put the value after a colon.

## Plurals
one and other; a noun after a number stays singular in Turkish (5 oturum, never 5 oturumlar), so both forms are often the same.
- Sentence-initial İ lowercases to i̇ in software, so the glossary lists both forms (izin and İzin, istem and İstem); the vowel-dropping stem izn (iznin) is listed too.
