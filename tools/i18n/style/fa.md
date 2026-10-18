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
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: خودکار, پرسش, کامل) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 مود خودکار, 💬 مود پرسش, 🔓 مود دسترسی کامل); a sentence around {mode} must not add its own word for mode.
- Persian: "ادمین" for admin in new text, never "حالت" for mode (always "مود"), active plain sentences: "💬 تغییر به مود پرسش", "🔓 تغییر به مود دسترسی کامل".
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- The "instead" buttons on /new (mode.*_instead) say exactly what the switch buttons say ("💬 تغییر به مود پرسش"), not a word for "instead" (operator, 2026-10-16: a literal "instead" read oddly on a button).
- A default button label (Continue, Run tests...) stays short: it is a button.

## The voice (Persian rewrite, 2026-10-11)
Write the way a good Persian app talks to people (Telegram, Snapp, Divar), not a translation of the English sentence. Read what the English MEANS in its place (a button, a card line, an error) and say that in natural Persian: everyday words, active voice, short sentences, the verb where Persian puts it. Prefer words people already use over formal or Arabic-heavy ones (کامپیوتر not رایانه, جواب in conversation, دوباره not مجدد). Buttons are the person's commands to the app: short, imperative or a noun ("ادامه بده", "بفرست", "لغو"). Messages address the person as شما.

## Say the word people say, even when it is English (operator rule, 2026-10-11)
Do not translate every word into Persian. Iranians use many English words in everyday Persian, and a "pure" Persian word in their place reads like a dictionary, not like an app. Iranians say «کارت آپدیت می‌شود», not «کارت تازه می‌شود»; «هنگ کرده», not «یخ زده»; «پیام ارسال می‌شود», not «پیام می‌رود». The test for every word: is this what an Iranian says out loud to a friend about their phone or computer? If people say the English word, write it in Persian letters (آپدیت، ری‌استارت، لینک). If people say a Persian word, keep the Persian (ارسال، تنظیمات، نصب، لغو).

Do not overdo it either: no English word that most people would not say (no «سند» for send, no «کنسل» where «لغو» is what apps and people say, no Latin letters except names, commands and the keep_english words).

Spell a loanword the common Persian way, with a zero-width non-joiner where it splits (ری‌استارت، اسکرین‌شات), and conjugate it with کردن / شدن the way people do: آپدیت کنید، آپدیت شد، ری‌استارت می‌شود، لود نشد، کپی شد، هنگ کرده، کرش کرد.

### Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| update (verb, noun, a new version) | آپدیت (آپدیت کنید، آپدیت شد، آپدیت جدید) | به‌روزرسانی، به‌روز |
| refresh (a card or a page redrawn) | آپدیت می‌شود | تازه می‌شود |
| restart | ری‌استارت (ری‌استارت کنید، در حال ری‌استارت) | راه‌اندازی دوباره |
| reset (put back to default) | ریست | بازنشانی |
| frozen, hung (stops moving) | هنگ کرده | یخ زده |
| stuck | گیر کرده | |
| crashed | کرش کرد | از کار افتاد |
| a message goes to / is delivered | ارسال می‌شود، فرستاده می‌شود | می‌رود، برود |
| session (a Claude Code session) | سشن | نشست |
| new (a new session, a new card) | جدید | تازه |
| load, loading, failed to load | لود، در حال لود…، لود نشد | بارگذاری، بارگیری |
| download | دانلود | بارگیری |
| link | لینک | پیوند |
| copy, copied | کپی، کپی شد | رونوشت |
| address (a web address) | آدرس | نشانی |
| check (look whether) | چک کردن (چک کردن آپدیت) | بررسی (fine for a careful review) |
| voice message | ویس | پیام صوتی |
| online, offline | آنلاین، آفلاین | برخط، برون‌خط |
| context window (Claude's) | کانتکست | حافظهٔ گفتگو |
| compact (Claude Code's /compact) | کامپکت (کامپکت کردن گفتگو) | خلاصه‌سازی گفتگو |
| configuration, setup (a summary label) | تنظیمات | پیکربندی |
| screenshot | اسکرین‌شات | تصویر صفحه |
| up to date | آپدیت است | به‌روز است |
| repository (git) | ریپو | مخزن |
| package (to install) | پکیج | |
| hook (Claude Code's) | هوک (in Persian letters, never Latin) | |

«چک» is a verb only (چک کردن، چک کنید، چک نشد). Never use it as a bare noun («این چک»، «چک‌ها»): alone it reads as a bank cheque; say «نتیجه» or «یک مورد چک نشد».

### Persian words people really use (keep these)
ارسال (send), تنظیمات (settings), نصب (install), حذف (delete), لغو (cancel), ذخیره (save), خطا (error), پیش‌فرض (default), اشتراک (subscription), عکس (photo), پوشه (folder), جواب (reply), صف (queue), ابزار (tool), دکمه (button), پیش‌نمایش (preview), گزارش (report), سنجاق (pin: Telegram's own word), ربات (bot), پردازش (processing), نسخه (version).

## Vocabulary (one word per idea)
| idea | word |
|---|---|
| session | سشن |
| idle (a session waiting for a message) | آماده |
| busy / working | در حال کار |
| waiting on you | منتظر شما |
| admin | ادمین |
| root folder (the folder a chat's members are held to) | پوشهٔ root (Persian speakers keep "root" in English: پوشهٔ root چت, پوشه‌های root, پوشهٔ root خودش; site owner, 2026-10-18); never پوشهٔ ریشه. A message to the person may still say پوشهٔ شما {folder}, as the catalog does |
| owner | مالک |
| member | عضو |
| mode (Ask, Auto, Full access) | مود (never حالت) |
| your message to Claude (prompt) | پیام |
| permission request | درخواست اجازه |
| allow / deny | اجازه بده / رد کن (answered: اجازه داده شد / رد شد) |
| compact (Claude Code's /compact) | کامپکت (کامپکت کردن گفتگو) |
| context window | کانتکست |
| the Telegram chat | چت |
| the conversation with Claude | گفتگو |
| restart | ری‌استارت |
| update (a new version) | آپدیت / نسخهٔ جدید |
| background agent | ایجنت پس‌زمینه |
| background shell command | دستور پس‌زمینه (never bare دستور; the first line under an answer says دستور پس‌زمینه (shell)) |
| template (ready-made message) | قالب |
| pinned message | پیام سنجاق‌شده |
