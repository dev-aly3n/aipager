# Arabic (ar) style guide

The translator (Claude) reads TRANSLATING.md first, then this, then the glossary.

## Tone
Modern Standard Arabic as a good Arabic app writes it: plain, direct and friendly, short sentences, no slang and no heavy formal phrasing (no piles of "يتم" and "قام ب"). Address the reader as أنت, but never give them a gender. Unvocalized Arabic lets most forms serve everyone; use those:
- The pronoun suffix ـك and the second-person past tense are written the same for everyone: "ليس لديك إذن", "قمت بشيء آخر", "رسالتك".
- An instruction is "يمكنك + verbal noun" or a verbal noun after a colon, never a gendered imperative or present tense: "يمكنك إرسال /new مرة أخرى", "للمحاولة مرة أخرى: /rename" (not "أرسل" / "أرسلي", not "تريد" / "تريدين"). A question to the reader avoids a verb that marks gender: "ما الإجراء التالي؟", not "ماذا تريد أن تفعل؟".
- A button is a verbal noun or a noun (إرسال، إيقاف، استئناف، سماح), as Arabic interfaces write buttons.
- {person} and {who} can be anyone: no verb that agrees with them. Use "بواسطة {person}", "من {person}" or a label ("{person}: رفض"): "⏹ أُوقف عمل {session} بواسطة {person}". A verb about a session agrees with جلسة (feminine): "انتهت الجلسة".

## Address
أنت

## Punctuation and quotation marks
Use the Arabic comma ، semicolon ؛ and question mark ؟, and « » for quotations. Right to left: write ← where the English has →; « » and › flip on their own and stay as they are.
- No long dashes (U+2014): use "-" or parentheses, as the English does.
- No exclamation marks.

## Digits
Arabic-Indic digits ٠-٩ for numbers written in the text (counts, percentages, sizes, "٧ أيام"). Never in names, versions, paths, commands or code, and never inside a placeholder: aipager converts numeric placeholders itself.

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

## Words people actually say (use these)
| English idea | write | not |
|---|---|---|
| session | جلسة | |
| agent, background agent | وكيل، وكيل في الخلفية | |
| background shell command | أمر في الخلفية (the first line under an answer adds "(shell)") | أمر alone |
| your message to Claude (prompt) | رسالة | طلب (that is the request waiting for an answer) |
| a permission or question prompt | طلب (طلب إذن) | |
| compact (Claude Code's /compact) | تلخيص المحادثة | ضغط |
| context window | السياق | نافذة السياق |
| Claude's conversation | المحادثة | |
| a Telegram chat | الدردشة (الدردشة الخاصة، المجموعة) | المحادثة (that is Claude's) |
| update | تحديث | |
| restart | إعادة تشغيل | |
| link | رابط | |
| repository | مستودع | |
| hung, frozen | معلّق | متجمد |
| crashed | تعطّل | انهار |
| voice message | رسالة صوتية | |
| computer, machine | الكمبيوتر | الآلة، المضيف |
| default | الافتراضي | |
| tool (Claude's tools) | أداة | |
| idle (a session waiting for a message) | جاهزة | خاملة |
| busy, working | تعمل | مشغولة |
| waiting on you | بانتظارك | تفاعلية |
| ended (a session no longer running) | انتهت | ميتة |
| end a session (Kill, End) | إنهاء | قتل |
| the aipager program (daemon, service) | aipager | الخدمة، العفريت |
| saved transcript | المحادثة المحفوظة | النص المنسوخ |
| working directory | المجلد | دليل العمل |
| terminal | الطرفية | |
| hook | هوك | خطاف |
| log | السجل | |
| the person who makes aipager (maintainer) | مطوّر aipager | المشرف على الصيانة |
| the bot's owner (operator) | مالك البوت | المشغّل |

## Vocabulary (one word per idea)
- session جلسة; turn: المهمة for Claude's work on one message ("عندما تنتهي هذه المهمة"); where people take turns, say what happens ("Claude يعمل الآن لشخص آخر"), since دور means role here; card بطاقة; permission إذن; a permission request طلب إذن; question سؤال.
- allow سماح (answered: مسموح); deny رفض (answered: مرفوض); stop إيقاف (stopped: أُوقف / متوقفة); resume استئناف; queue قائمة الانتظار; agent وكيل / وكلاء; background في الخلفية.
- owner المالك; admin مشرف (Telegram's Arabic word); member عضو; group مجموعة; settings الإعدادات; folder مجلد; model نموذج; role دور; mode وضع; "Mini App".
- Kept in English: commit, token, diff, ctx. A diff is "التغييرات (diff)" where it is a label.
- Buttons: سماح، رفض، إيقاف، استئناف، إعادة تشغيل، حذف، إعادة تسمية، إلغاء، إغلاق، إعادة المحاولة، إنهاء، إرسال، إرسال الآن.
- Same situation, same words: "Couldn't …" is "تعذّر …" ("تعذّر تنزيل الملف"); "Couldn't reach the server" is "تعذّر الاتصال بالخادم"; a refused restart is "لم تتم إعادة التشغيل: …"; a stale button or card is "قديمة"; an expired one "انتهت صلاحيتها"; a running session "تعمل".
- {clock} is a duration: "انتهى ({clock})", "تعمل ({clock})", never "بعد {clock}" (reads like a time of day).

## Plurals
All six CLDR forms: zero (٠: "لا ..." without the number is fine), one (the singular, the number may be left out), two (the dual, the number may be left out), few (3-10: plural noun), many (11-99: singular accusative noun, "١١ وكيلًا"; a noun ending in ة reads the same as other), other (100, 101, 102...: singular noun, "١٠٠ وكيل"). card.fold's many and other are the same text. {n} may be left out of zero, one and two only. A verb before a counted subject stays singular and agrees in gender ("استُخدمت {n} أدوات").
