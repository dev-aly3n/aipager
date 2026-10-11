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

## Markers, keyboard words and modes
- Keep `**`, `_`, every HTML tag and every {placeholder} exactly as in the English: the same number of `**` and `_`, never a backtick.
- A keyboard word (status, stop, new, kill) is ONE word with no space: it is typed as a word (الحالة، إيقاف، جديدة، إنهاء).
- A mode word (mode.word.auto, mode.word.ask, mode.word.full: تلقائي, اسأل, كامل) is ONE word with no emoji: people type it after /mode, so it never changes once chosen. A mode label (mode.auto, mode.ask, mode.full) is what people see: the emoji, that same word and the word for mode (🤖 الوضع التلقائي, 💬 وضع «اسأل», 🔓 وضع الوصول الكامل); a sentence around {mode} must not add its own word for mode.
- A button or sentence that names a permission mode names it as a mode (operator, 2026-10-11): the switch buttons (modecard.btn.to_*, ui.btn.switch_to_*), the "instead" buttons (mode.*_instead) and the busy confirms (ui.confirm.perms_busy_*) say the mode word and the word for mode, as mode.auto and mode.ask do. The typed words stay one word.
- A default button label (Continue, Run tests...) stays short: it is a button.

## Words (the glossary's terms in context)
جلسة (session), جولة (turn: one prompt and Claude's work on it; دور means role here), بطاقة (card), إذن (permission; a permission prompt is طلب إذن), طلب (prompt: what you send to Claude), سؤال (question), اسمح / مسموح (allow), ارفض / مرفوض (deny), إيقاف / أوقف / متوقف (stop), استئناف / استأنف (resume), قائمة الانتظار (queue), وكيل / وكلاء (agent), في الخلفية (background), المالك (owner), مشرف (admin, as Telegram's Arabic says), عضو (member), محادثة (chat), مجموعة (group), الإعدادات (settings), تحديث (update), مجلد (folder), نموذج (model).

## Plurals
All six CLDR forms: zero (٠: "لا ..." without the number is fine), one (the singular, the number may be left out), two (the dual, the number may be left out), few (3-10: plural noun), many (11-99: singular accusative noun, e.g. ١١ جلسةً or ١١ جلسة), other (100, 101, 102...: singular noun). {n} may be left out of zero, one and two only.
