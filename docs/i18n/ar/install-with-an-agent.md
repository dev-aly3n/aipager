<div dir="rtl">

# التثبيت بواسطة وكيل

<!-- i18n-source: docs/install-with-an-agent.md @ 6fc084f -->

> هذه الصفحة مترجمة عن الإنجليزية؛ إذا اختلفت النسختان، فالنسخة الإنجليزية هي الصحيحة. ([English](../../install-with-an-agent.md))

هذه الصفحة موجهة إلى وكيل برمجة بالذكاء الاصطناعي (Claude Code، أو أي وكيل يستطيع استخدام الطرفية) طُلب منه تثبيت aipager لصاحبه وإعداده. المطلوب اتباعها خطوة بخطوة. كل أمر هنا يعمل دون أي سؤال ولا يحتاج أبدًا إلى طرفية تفاعلية.

يتيح aipager لصاحبه التحكم في جلسات Claude Code من Telegram. عند انتهاء عملك، يكون لدى الشخص بوت Telegram لا يتحدث إلا معه، وaipager يعمل في الخلفية على هذا الكمبيوتر.

## <a id="1-what-only-the-person-can-do"></a>١. ما لا يستطيع فعله إلا الشخص نفسه

هذه أمور يُطلب من الشخص فعلها؛ لا يمكنك فعلها نيابة عنه.

1. **إنشاء البوت.** في Telegram: فتح [@BotFather](https://t.me/BotFather)، وإرسال `/newbot`، واختيار اسم واسم مستخدم. يرد BotFather بتوكن البوت (يشبه `123456789:AA...`).
2. **الضغط على Start في البوت.** فتح البوت الجديد (يعطي BotFather رابطه بالشكل `t.me/<bot username>`) والضغط على Start. لا يستطيع البوت مراسلة من لم يفعل ذلك.
3. **تسجيل الدخول إلى Claude Code على هذا الكمبيوتر.** إذا كان الشخص يستخدم Claude Code هنا من قبل، فلا شيء مطلوب. على خادم بلا شاشة، يُشغَّل `claude` مرة واحدة في طرفيته هو ويُسجَّل الدخول (يظهر رابط يُفتح على جهاز آخر). وإذا كان التوكن طويل الأمد مفضّلًا، يُشغَّل `claude setup-token`، الذي يطبع التوكن فقط: ويحفظه الشخص بنفسه لجلسات aipager، في طرفيته (لصق التوكن، ثم Enter، ثم Ctrl-D):

<div dir="ltr">

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

</div>

   يسلّم aipager ذلك التوكن إلى كل جلسة يبدؤها (يُنظر [الأمان](security.md#the-claude-credential-what-actually-protects-it)). ولا حاجة لك أبدًا إلى قراءته.

## <a id="2-how-the-person-gives-you-the-token"></a>٢. كيف يسلّمك الشخص التوكن

من يملك توكن البوت يتحكم في البوت. عليك عرض هاتين الطريقتين على الشخص وترك الاختيار له.

- **الأكثر أمانًا: ملف لا يُقرأ من جهتك أبدًا.** في طرفيته هو، يشغّل:

<div dir="ltr">

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

</div>

  ثم يلصق التوكن، ويضغط Enter، ثم Ctrl-D. لا يستطيع قراءة الملف غيره (الصلاحية 600). ويمكنه حذفه بعد نجاح الإعداد (يحتفظ aipager بنسخته الخاصة في إعداداته). يخبرك بالمسار (`~/.config/aipager/bot-token`). لا يدخل التوكن محادثتك أبدًا، ويكون تمرير المسار إلى `--token-file` من جهتك.
- **الأسرع: لصقه في المحادثة.** هذا ينجح، لكن عليك قول ما يعنيه بوضوح: يبقى التوكن في سجل محادثتك ويُرسل إلى مزوّد النموذج الخاص بك. إذا كان هذا السجل قد يُشارك يومًا، فعلى الشخص لاحقًا إرسال `/revoke` إلى @BotFather وضبط التوكن الجديد بالأمر `aipager setup --force` (الخطوة ٣.٢، مع `--force`). إذا اختار هذه الطريقة، فعليك حفظ التوكن في الملف نفسه دون طباعته: والأفضل بأداة كتابة الملفات الخاصة بك، ثم `chmod 600 ~/.config/aipager/bot-token`. وإذا لم يكن لديك سوى الطرفية، فيمكنك تمريره على stdin عبر here-document، حتى لا يكون وسيطًا في الأمر الذي يكتبه:

<div dir="ltr">

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token) <<'EOF'
  <the token>
  EOF
  chmod 600 ~/.config/aipager/bot-token
  ```

</div>

  ثم استخدام `--token-file` كما في الأسفل.

معرّف الشخص في Telegram (وهو معرّف دردشته، رقم مثل `123456789`) ليس سرًا. إذا كان لا يعرفه، يمكنك إيجاده (الخطوة ٣.٣).

## <a id="3-your-steps"></a>٣. خطواتك

### <a id="31-install-aipager"></a>٣.١ تثبيت aipager

<div dir="ltr">

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

</div>

يستخدم uv أو pipx، أيهما وُجد (وHomebrew أيضًا على macOS)، ويثبّت uv أولًا إن لم يوجد أي منها. يمكنك أيضًا تشغيل `uv tool install aipager` أو `pipx install aipager` بنفسك. إذا لم يُعثر بعدها على `aipager`، يُضاف `~/.local/bin` إلى `PATH` في الطرفية (`export PATH="$HOME/.local/bin:$PATH"`).

إذا كان aipager مثبّتًا من قبل، فهذه الأوامر لا تحدّثه. عليك التحقق من أن `aipager setup --help` يعمل؛ وإن فشل، فالنسخة المثبّتة قديمة جدًا: تشغيل `aipager update` ثم التحقق مرة أخرى. وإذا قال `aipager update` إن aipager يحتاج إلى إعادة تشغيل، فعليك إخبار الشخص، دون إعادة تشغيله من جهتك (يُنظر ٤).

لا يُشغَّل `aipager config`: إنه معالج الإعداد التفاعلي المخصص للبشر.

### <a id="32-set-aipager-up"></a>٣.٢ إعداد aipager

<div dir="ltr">

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

</div>

يُستخدم المعرّف الحقيقي للشخص ومسار ملف التوكن. بدل `--token-file`، يمكنك تمرير التوكن عبر أنبوب مع `--token-stdin` (مثلًا `--token-stdin < path`)؛ ويجب أن يُغلق الأنبوب، إذ ينتظره setup ٣٠ ثانية على الأكثر.

يتحقق setup من التوكن لدى Telegram، ويفحص هذا الكمبيوتر، ويرسل إلى الشخص رسالة اختبار واحدة («aipager is set up for you. ...»)، ولا يكتب إعداداته إلا بعد ذلك، ويضيف هوكات Claude Code إلى `~/.claude/settings.json`، ومع `--service` يثبّت خدمة الخلفية ويشغّلها. إذا فشل أي فحص، لا يُكتب شيء.

إضافة `--dry-run` تعرض ما سيتغير أولًا. يجعل `--role admin` الشخص مشرفًا بدل المالك (الافتراضي، المالك، هو الصحيح لشخص يُعدّ كمبيوتره بنفسه).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>٣.٣ إذا كان الشخص لا يعرف معرّفه في Telegram

<div dir="ltr">

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

</div>

يُطلب من الشخص الضغط على Start في البوت (أو إرسال أي رسالة إليه) بينما ينتظر هذا الأمر (حتى ٥ دقائق؛ و`--timeout SECONDS` يغيّر ذلك). يطبع الأمر أحدث مرسِل في `candidate` (`id` و`first_name` و`last_name` و`username`) ولا يحفظ شيئًا. عليك عرض الاسم واسم المستخدم على الشخص وسؤاله «هل هذا أنت؟». لا يُشغَّل ٣.٢ مع `--chat-id` مساويًا لـ `candidate.id` إلا بعد تأكيده. وإذا لم تكن `other_candidates` صفرًا، فقد راسل البوتَ أشخاص آخرون أيضًا: يلزم التأكد جيدًا.

### <a id="34-read-the-result"></a>٣.٤ قراءة النتيجة

مع `--json`، يطبع setup كائن JSON واحدًا بالضبط على stdout. المطلوب قراءة `exit_code` (يساوي رمز خروج العملية) و`status` و`error` و`message` و`fix` و`next_step`. إذا لم يكن stdout بصيغة JSON (خيار غير معروف، أو كلمة خاطئة بعد `setup`، أو نسخة aipager أقدم من أن يكون فيها `setup`)، فرمز الخروج 2 والسبب مكتوب نصًا عاديًا على stderr: يلزم قراءته، والرجوع إلى ٣.١ في حالة النسخة القديمة.

| الخروج | `error` | الإجراء |
|---|---|---|
| 0 | | `installed` أو `updated` أو `unchanged`: انتهى، والانتقال إلى ٣.٥. `dry_run`: لم يُكتب شيء؛ تشغيل الأمر نفسه دون `--dry-run`. detect-chat `found`: تأكيد الشخص كما في ٣.٣، ثم تشغيل ٣.٢. |
| 1 | `config_malformed`، `ambiguous_install`، `settings_invalid`، `test_send_failed`، `write_failed`، `service_failed`، `internal_error` | عرض `message` و`fix` على الشخص. `ambiguous_install`: يصلحه الشخص في `aipager config`. دون تعديل الملفات يدويًا. |
| 2 | `usage`، `token_malformed`، `token_stdin_timeout`، `bad_chat_id`، ... | كانت مدخلاتك خاطئة؛ و`fix` يسمّي الخيار. `token_malformed`: الملف لا يحتوي على توكن بوت، يُطلب من الشخص التحقق منه. |
| 3 | `deps_missing` | اتباع `fix` (لكل عنصر ناقص أيضًا `fix` خاص به في `deps.items`)، ثم تشغيل setup مرة أخرى. |
| 4 | `token_rejected` | رفض Telegram التوكن. طلب التوكن من الشخص مرة أخرى (أو توكن جديد من @BotFather). |
| 5 | `chat_not_started`، `bot_blocked` | يُطلب من الشخص فتح البوت والضغط على Start (أو إلغاء حظره)، ثم تشغيل setup مرة أخرى. |
| 6 | `existing_install` | aipager مُعدّ من قبل بتوكن أو دردشة أو دور مختلف. إخبار الشخص بما يختلف وسؤاله قبل إضافة `--force`. |
| 7 | `telegram_unreachable` | مشكلة في الشبكة أو Telegram مشغول. الانتظار دقيقة ثم المحاولة مرة أخرى. |
| 8 | `detect_timeout` | لم يرَ detect-chat أي رسالة. يُطلب من الشخص الضغط على Start مرة أخرى ثم إعادة التشغيل. مع التحذير `updates_backlog` لا يفيد الضغط على Start: طلب المعرّف الرقمي من الشخص. |
| 9 | `daemon_running` | detect-chat: يعمل aipager لهذا البوت ويستقبل رسائله. طلب المعرّف الرقمي من الشخص (أو سؤاله قبل إيقافه بالأمر `aipager service stop`). |
| 10 | `updates_conflict` | detect-chat: برنامج آخر يقرأ رسائل هذا البوت. طلب المعرّف الرقمي من الشخص. |
| 130 | `interrupted` | أُوقف بـ Ctrl-C. |

إذا كانت قيمة `daemon.restart_needed` هي true، فإن aipager يحتاج إلى إعادة تشغيل لاستخدام التغيير، أو لم يستطع setup معرفة ما إذا كان يعمل (التحذير `daemon_unknown`). عليك اتباع `next_step`، الذي يحدد أي الحالتين (وإن اقترح `aipager start`، الذي يبقى في الواجهة، فالأفضل `aipager service install`). عليك إخبار الشخص، وعدم إعادة التشغيل (`aipager service stop` ثم `aipager service start`) إلا إذا وافق، وأبدًا من داخل جلسة يتحكم فيها الشخص عبر aipager (يُنظر ٤). القائمة الكاملة للأخطاء والتحذيرات ومفاتيح JSON موجودة في [مرجع أمر setup](commands.md#command-line-aipager-setup-for-coding-agents).

### <a id="35-check-the-install"></a>٣.٥ فحص التثبيت

<div dir="ltr">

```sh
aipager doctor --json
```

</div>

يقرأ فقط ولا يرسل شيئًا. الخروج بالرمز 0 يعني أنه لم يفشل أي فحص. لكل فحص قيمة `status` فيه `fail` (أو `warn`)، عليك عرض `title` و`detail` و`fix` الخاصة به على الشخص.

### <a id="36-report-back-to-the-person"></a>٣.٦ إبلاغ الشخص بالنتيجة

في بضعة أسطر:

- البوت (`@` + `bot_username`)، وهل أُرسلت رسالة الاختبار (`test_message`)؛
- ما الذي أُعدّ (`changed`، و`service.result`)؛
- طريقة بدء جلسة Claude: إرسال `/new` إلى البوت في Telegram، أو تشغيل `aipager session <name>` في طرفية؛
- أي شيء نبّه إليه `doctor`، و`next_step` إن كان يطلب شيئًا.

## <a id="4-what-you-must-not-do"></a>٤. ما لا يجوز لك فعله

- لا طباعة للتوكن ولا `echo` ولا `cat` له، ولا وضعه أبدًا في سطر الأوامر (`--token` مرفوض على أي حال). يُمرَّر فقط كملف أو على stdin.
- لا تعديل يدويًا لـ `~/.config/aipager/` (باستثناء ملف التوكن في الخطوة ٢) أو لـ `~/.claude/settings.json`. يكتبهما `aipager setup` و`aipager config`.
- لا إضافة لـ `--force` دون سؤال الشخص: فهو يستبدل التوكن أو الدردشة أو الدور الموجود لديه.
- لا إعادة تشغيل ولا إنهاء لجلستك في Claude Code. في أول تثبيت، جلستك الحالية ليست تحت aipager، ولا حاجة إلى ذلك: الجلسات الجديدة تأتي من `/new` في Telegram أو من `aipager session <name>`. إذا كان aipager مُعدًّا من قبل والشخص يتواصل معك عبره (مثلًا لتشغيل `setup --force` بتوكن جديد)، فلا إيقاف ولا إعادة تشغيل لـ aipager من جهتك: فستنقطع مهمتك وإجابتها. عليك طلب إعادة تشغيله من الشخص بعد انتهائك.

## <a id="5-groups"></a>٥. المجموعات

يُعدّ `aipager setup` الدردشة الخاصة بين الشخص والبوت. لإضافة مجموعة Telegram فيها أشخاص آخرون وأدوارهم، يشغّل الشخص `aipager config` (إعداد المجموعات لا يمكن أتمتته بسكربت بعد). يُنظر [وضع الفريق والمجموعات](groups.md).

## <a id="see-also"></a>صفحات ذات صلة

- [أوامر البوت وأمر setup](commands.md)
- [حل المشكلات](troubleshooting.md)
- [نموذج الأمان](security.md)

</div>
