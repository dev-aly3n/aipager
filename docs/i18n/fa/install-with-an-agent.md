<div dir="rtl">

# نصب با ایجنت

<!-- i18n-source: docs/install-with-an-agent.md @ fc5d6810 -->

> این صفحه ترجمهٔ صفحهٔ انگلیسی است؛ اگر جایی با هم فرق داشتند، نسخهٔ انگلیسی درست است. ([English](../../install-with-an-agent.md))

این صفحه برای یک ایجنت هوش مصنوعی برنامه‌نویسی است (Claude Code یا هر ایجنتی که shell دارد) که از او خواسته‌اند aipager را برای صاحبش نصب و راه‌اندازی کند. قدم‌به‌قدم دنبالش کنید. همهٔ دستورهای این صفحه بدون هیچ سؤالی اجرا می‌شوند و هیچ‌وقت ترمینال تعاملی لازم ندارند.

&rlm;aipager به آن شخص اجازه می‌دهد سشن‌های Claude Code را از تلگرام بگرداند. وقتی کارتان تمام شد، آن شخص یک ربات تلگرام دارد که فقط با خودش حرف می‌زند، و aipager در پس‌زمینه روی این کامپیوتر اجرا می‌شود.

## <a id="1-what-only-the-person-can-do"></a>۱. کارهایی که فقط خود آن شخص می‌تواند انجام دهد

این‌ها را از آن شخص بخواهید. شما نمی‌توانید به جایش انجامشان دهید.

1. **ساختن ربات.** در تلگرام &lrm;[@BotFather](https://t.me/BotFather) را باز کند، &lrm;`/newbot` را بفرستد و یک اسم و یک یوزرنیم انتخاب کند. BotFather توکن ربات را جواب می‌دهد (چیزی شبیه &lrm;`123456789:AA...`&lrm;).
2. **زدن Start در ربات.** ربات جدید را باز کند (BotFather لینکش را به‌شکل `t.me/<bot username>`&lrm; می‌دهد) و Start را بزند. ربات نمی‌تواند به کسی که این کار را نکرده پیام بدهد.
3. **لاگین در Claude Code روی این کامپیوتر.** اگر از قبل روی همین کامپیوتر با Claude Code کار می‌کند، کاری لازم نیست. روی سرور بدون صفحه‌نمایش، یک بار در ترمینال خودش `claude` را اجرا می‌کند و لاگین می‌کند (لینکی نشان می‌دهد که روی دستگاه دیگری باز شود). اگر توکنی با عمر طولانی را ترجیح می‌دهد، `claude setup-token` را اجرا می‌کند که فقط توکن را چاپ می‌کند: خودش آن را برای سشن‌های aipager، در ترمینال خودش، ذخیره می‌کند (توکن را پیست کند، Enter بزند و بعد Ctrl-D):

<div dir="ltr">

   ```sh
   mkdir -p ~/.config/aipager
   (umask 077 && { printf 'CLAUDE_CODE_OAUTH_TOKEN='; cat; } >> ~/.config/aipager/daemon.env)
   ```

</div>

   &rlm;aipager آن توکن را به هر سشنی که شروع می‌کند می‌دهد ([مدل امنیتی](security.md#the-claude-credential-what-actually-protects-it) را ببینید). شما هیچ‌وقت آن را نمی‌خوانید.

## <a id="2-how-the-person-gives-you-the-token"></a>۲. آن شخص توکن را چطور به شما می‌دهد

هر کس توکن ربات را داشته باشد، ربات را کنترل می‌کند. این دو راه را به آن شخص پیشنهاد بدهید و بگذارید خودش انتخاب کند.

- **امن‌ترین: فایلی که شما هیچ‌وقت نمی‌خوانید.** در ترمینال خودش این را اجرا می‌کند:

<div dir="ltr">

  ```sh
  mkdir -p ~/.config/aipager
  rm -f ~/.config/aipager/bot-token
  (umask 077 && cat > ~/.config/aipager/bot-token)
  chmod 600 ~/.config/aipager/bot-token
  ```

</div>

  بعد توکن را پیست می‌کند، Enter و بعد Ctrl-D را می‌زند. فقط خودش می‌تواند فایل را بخواند (حالت دسترسی 600). وقتی راه‌اندازی موفق شد می‌تواند آن را پاک کند (aipager نسخهٔ خودش را در تنظیماتش نگه می‌دارد). مسیر را به شما می‌گوید (&lrm;`~/.config/aipager/bot-token`). توکن هیچ‌وقت وارد چت شما نمی‌شود و شما مسیر را به &lrm;`--token-file` می‌دهید.
- **سریع‌ترین: پیست کردن در چت.** این کار می‌کند، ولی صاف بگویید یعنی چه: توکن در گفتگوی شما می‌ماند و به ارائه‌دهندهٔ مدلتان فرستاده می‌شود. اگر ممکن است این گفتگو روزی با کسی به اشتراک گذاشته شود، آن شخص باید بعداً &lrm;`/revoke` را به &lrm;@BotFather بفرستد و توکن جدید را با `aipager setup --force` تنظیم کند (قدم ۳.۲، با &lrm;`--force`). اگر این راه را انتخاب کرد، توکن را بدون چاپ کردن در همان فایل ذخیره کنید: بهتر از همه با ابزار نوشتن فایل خودتان، و بعد `chmod 600 ~/.config/aipager/bot-token`. اگر فقط shell دارید، آن را با here-document به stdin بدهید تا آرگومان دستوری که آن را می‌نویسد نباشد:

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

  بعد مثل پایین از &lrm;`--token-file` استفاده کنید.

آیدی کاربری تلگرام آن شخص (همان آیدی چتش، عددی مثل `123456789`) راز نیست. اگر آن را نمی‌داند، شما می‌توانید پیدایش کنید (قدم ۳.۳).

## <a id="3-your-steps"></a>۳. قدم‌های شما

### <a id="31-install-aipager"></a>۳.۱ نصب aipager

<div dir="ltr">

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

</div>

از uv یا pipx، هر کدام که باشد، استفاده می‌کند (روی macOS از Homebrew هم)، و اگر هیچ‌کدام نباشد اول uv را نصب می‌کند. می‌توانید خودتان هم `uv tool install aipager` یا `pipx install aipager` را اجرا کنید. اگر بعدش `aipager` پیدا نشد، &lrm;`~/.local/bin` را برای shell خودتان به `PATH` اضافه کنید (`export PATH="$HOME/.local/bin:$PATH"`&lrm;).

اگر aipager از قبل نصب بوده، این دستورها آپدیتش نمی‌کنند. چک کنید `aipager setup --help` کار می‌کند؛ اگر نکرد، aipager نصب‌شده خیلی قدیمی است: `aipager update` را اجرا کنید و دوباره چک کنید. اگر `aipager update` گفت aipager را ری‌استارت کنید، به آن شخص بگویید؛ خودتان ری‌استارتش نکنید (بخش ۴ را ببینید).

&rlm;`aipager config` را اجرا نکنید: آن ویزارد سؤال و جوابی برای آدم‌هاست.

### <a id="32-set-aipager-up"></a>۳.۲ راه‌اندازی aipager

<div dir="ltr">

```sh
aipager setup --token-file ~/.config/aipager/bot-token --chat-id 123456789 --service --json
```

</div>

آیدی واقعی آن شخص و مسیر فایل توکن را بگذارید. به جای &lrm;`--token-file` می‌توانید توکن را با &lrm;`--token-stdin` از طریق pipe بدهید (مثلاً &lrm;`--token-stdin < path`)؛ pipe باید بسته شود و setup حداکثر ۳۰ ثانیه منتظرش می‌ماند.

&rlm;setup توکن را با تلگرام چک می‌کند، این کامپیوتر را چک می‌کند، یک پیام آزمایشی برای آن شخص می‌فرستد ("aipager is set up for you. ...") و فقط بعد از آن تنظیماتش را می‌نویسد، هوک‌های Claude Code را به &lrm;`~/.claude/settings.json` (یا &lrm;`$CLAUDE_CONFIG_DIR/settings.json`) اضافه می‌کند و با &lrm;`--service` سرویس پس‌زمینه را نصب و اجرا می‌کند. اگر هر بررسی‌ای شکست بخورد، هیچ چیزی نوشته نمی‌شود.

برای دیدن اینکه اول چه چیزی عوض می‌شود، &lrm;`--dry-run` را اضافه کنید. &lrm;`--role admin` آن شخص را به جای مالک، ادمین می‌کند (پیش‌فرض، یعنی مالک، برای کسی که کامپیوتر خودش را راه می‌اندازد درست است).

### <a id="33-if-the-person-does-not-know-their-telegram-id"></a>۳.۳ اگر آن شخص آیدی تلگرامش را نمی‌داند

<div dir="ltr">

```sh
aipager setup detect-chat --token-file ~/.config/aipager/bot-token --json
```

</div>

از آن شخص بخواهید وقتی این دستور منتظر است، در ربات Start را بزند (یا هر پیامی بفرستد) (تا ۵ دقیقه؛ &lrm;`--timeout SECONDS` عوضش می‌کند). جدیدترین فرستنده را در `candidate` چاپ می‌کند (`id`،&rlm; `first_name`،&rlm; `last_name`،&rlm; `username`) و چیزی ذخیره نمی‌کند. اسم و یوزرنیم را به آن شخص نشان بدهید و بپرسید «این شما هستید؟». فقط وقتی گفت بله، قدم ۳.۲ را با &lrm;`--chat-id` برابر `candidate.id` اجرا کنید. اگر `other_candidates` صفر نیست، آدم‌های دیگری هم به ربات پیام داده‌اند: بیشتر مطمئن شوید.

### <a id="34-read-the-result"></a>۳.۴ خواندن نتیجه

با &lrm;`--json`،&rlm; setup دقیقاً یک شیء JSON روی stdout چاپ می‌کند. `exit_code` (همان کد خروج پروسه)، `status`،&rlm; `error`،&rlm; `message`،&rlm; `fix` و `next_step` را بخوانید. اگر stdout JSON نبود (یک فلگ ناشناخته، کلمهٔ اشتباه بعد از `setup`، یا aipager آن‌قدر قدیمی که `setup` ندارد)، کد خروج 2 است و دلیلش به‌شکل متن ساده روی stderr می‌آید: آن را بخوانید، و برای aipager قدیمی بخش ۳.۱ را ببینید.

| کد خروج | `error` | شما چه می‌کنید |
|---|---|---|
| 0 | | `installed`،&rlm; `updated` یا `unchanged`: کار تمام است، بروید سراغ ۳.۵. `dry_run`: چیزی نوشته نشد؛ همان دستور را بدون &lrm;`--dry-run` دوباره اجرا کنید. detect-chat با `found`: مثل ۳.۳ از آن شخص تأیید بگیرید و بعد ۳.۲ را اجرا کنید. |
| 1 | `config_malformed`،&rlm; `ambiguous_install`،&rlm; `settings_invalid`،&rlm; `test_send_failed`،&rlm; `write_failed`،&rlm; `service_failed`،&rlm; `internal_error` | `message` و `fix` را به آن شخص نشان بدهید. `ambiguous_install`: خود آن شخص در `aipager config` درستش می‌کند. فایل‌ها را دستی ویرایش نکنید. خطای `settings_invalid` که یک `CLAUDE_CONFIG_DIR` با مسیر کامل می‌خواهد: مقدار `fix` فایلی را که باید عوض شود می‌گوید (`daemon.env` یا یک فایل تنظیمات Claude Code)؛ پوشه را از آن شخص بپرسید، فقط اگر خودش گفت همان یک مقدار را عوض کنید و بعد setup را دوباره اجرا کنید. |
| 2 | `usage`،&rlm; `token_malformed`،&rlm; `token_stdin_timeout`،&rlm; `bad_chat_id`، ... | ورودی شما اشتباه بود؛ `fix` فلگ را نام می‌برد. `token_malformed`: فایل توکن ربات ندارد، از آن شخص بخواهید چکش کند. |
| 3 | `deps_missing` | `fix` را دنبال کنید (هر مورد کم‌شده در `deps.items` هم `fix` خودش را دارد) و بعد setup را دوباره اجرا کنید. |
| 4 | `token_rejected` | تلگرام توکن را رد کرد. دوباره توکن را از آن شخص بخواهید (یا یک توکن جدید از &lrm;@BotFather). |
| 5 | `chat_not_started`،&rlm; `bot_blocked` | از آن شخص بخواهید ربات را باز کند و Start را بزند (یا از بلاک درش بیاورد)، بعد setup را دوباره اجرا کنید. |
| 6 | `existing_install` | aipager از قبل با یک توکن، چت یا نقش دیگر راه‌اندازی شده. به آن شخص بگویید چه چیزی فرق دارد و قبل از اضافه کردن &lrm;`--force` بپرسید. |
| 7 | `telegram_unreachable` | مشکل شبکه یا شلوغی تلگرام. یک دقیقه صبر کنید و دوباره امتحان کنید. |
| 8 | `detect_timeout` | detect-chat هیچ پیامی ندید. از آن شخص بخواهید دوباره Start را بزند و دوباره اجرا کنید. با هشدار `updates_backlog`، زدن Start کمکی نمی‌کند: آیدی عددی‌اش را از خودش بپرسید. |
| 9 | `daemon_running` | detect-chat: یک aipager برای همین ربات از قبل اجرا می‌شود و پیام‌هایش را می‌گیرد. آیدی عددی را از آن شخص بپرسید (یا قبل از اینکه با `aipager service stop` متوقفش کنید، از او اجازه بگیرید). |
| 10 | `updates_conflict` | detect-chat: برنامهٔ دیگری پیام‌های این ربات را می‌خواند. آیدی عددی را از آن شخص بپرسید. |
| 130 | `interrupted` | با Ctrl-C متوقف شد. |

اگر `daemon.restart_needed` برابر true است، aipager برای استفاده از تغییر باید ری‌استارت شود، یا setup نتوانست بفهمد aipager در حال اجراست یا نه (هشدار `daemon_unknown`). `next_step` را دنبال کنید که می‌گوید کدام است (اگر `aipager start` را پیشنهاد کرد که در ترمینال باز می‌ماند، `aipager service install` را ترجیح بدهید). به آن شخص بگویید و فقط اگر خودش گفت ری‌استارت کنید (`aipager service stop` و بعد `aipager service start`)، و هیچ‌وقت از داخل سشنی که آن شخص از طریق aipager می‌گرداند (بخش ۴ را ببینید). فهرست کامل خطاها، هشدارها و کلیدهای JSON در [مرجع دستور setup](commands.md#command-line-aipager-setup-for-coding-agents) است.

### <a id="35-check-the-install"></a>۳.۵ چک کردن نصب

<div dir="ltr">

```sh
aipager doctor --json
```

</div>

فقط می‌خواند و چیزی نمی‌فرستد. کد خروج 0 یعنی هیچ بررسی‌ای شکست نخورد. برای هر بررسی که `status` آن `fail` (یا `warn`) است، `title`،&rlm; `detail` و `fix` آن را به آن شخص نشان بدهید.

### <a id="36-report-back-to-the-person"></a>۳.۶ گزارش به آن شخص

در چند خط به او بگویید:

- ربات (`@` + `bot_username`)، و اینکه پیام آزمایشی فرستاده شد یا نه (`test_message`)؛
- چه چیزهایی راه‌اندازی شد (`changed` و `service.result`)؛
- چطور یک سشن Claude شروع کند: &lrm;`/new` را در تلگرام برای ربات بفرستد، یا `aipager session <name>`&lrm; را در ترمینال اجرا کند؛
- هر چیزی که `doctor` به آن اشاره کرد، و اگر `next_step` چیزی خواست، همان را.

## <a id="4-what-you-must-not-do"></a>۴. کارهایی که نباید بکنید

- توکن را چاپ، echo یا `cat` نکنید و هیچ‌وقت آن را در خط فرمان نگذارید (&lrm;`--token` در هر حال رد می‌شود). فقط به‌شکل فایل یا از stdin بدهیدش.
- &rlm;&lrm;`~/.config/aipager/`&lrm; (جز فایل توکن در قدم ۲) یا &lrm;`~/.claude/settings.json` را دستی ویرایش نکنید. `aipager setup` و `aipager config` آن‌ها را می‌نویسند.
- بدون پرسیدن از آن شخص &lrm;`--force` اضافه نکنید: توکن، چت یا نقش فعلی‌اش را عوض می‌کند.
- سشن Claude Code خودتان را ری‌استارت یا بسته نکنید. در اولین نصب، سشنی که در آن کار می‌کنید زیر aipager نیست و لازم هم نیست باشد: سشن‌های جدید از &lrm;`/new` در تلگرام یا `aipager session <name>`&lrm; می‌آیند. اگر aipager از قبل راه‌اندازی شده و آن شخص از طریق آن با شما حرف می‌زند (مثلاً برای اجرای `setup --force` با یک توکن جدید)، خودتان aipager را متوقف یا ری‌استارت نکنید: کار شما و جوابش نیمه‌کاره می‌ماند. به آن شخص بگویید وقتی کارتان تمام شد ری‌استارتش کند.

## <a id="5-groups"></a>۵. گروه‌ها

&rlm;`aipager setup` چت خصوصی خود آن شخص با ربات را راه می‌اندازد. برای اضافه کردن یک گروه تلگرام با آدم‌های بیشتر و نقش‌هایشان، خود آن شخص `aipager config` را اجرا می‌کند (راه‌اندازی گروه هنوز با اسکریپت ممکن نیست). [استفادهٔ تیمی و گروه](groups.md) را ببینید.

## <a id="see-also"></a>این‌ها را هم ببینید

- [فرمان‌های ربات و دستور setup](commands.md)
- [رفع مشکل](troubleshooting.md)
- [مدل امنیتی](security.md)

</div>
