<div dir="rtl">

# aipager

<!-- i18n-source: README.md @ 6fc084f -->

> این صفحه ترجمهٔ صفحهٔ انگلیسی است؛ اگر جایی با هم فرق داشتند، نسخهٔ انگلیسی درست است. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

کنترل از راه دور سشن‌های [Claude Code](https://claude.com/claude-code) با Telegram. Claude روی کامپیوتر شما داخل یک ترمینال جدا (`dtach`) اجرا می‌شود و شما از گوشی با آن کار می‌کنید: جواب‌های Claude را می‌خوانید، برایش پیام می‌فرستید، به درخواست‌های اجازه و سؤال‌هایش جواب می‌دهید و بین سشن‌ها جابه‌جا می‌شوید، بدون اینکه یک اتصال SSH باز بماند.

[aipager.run](https://aipager.run) · [مستندات](#docs) · [تغییرات نسخه‌ها](../../../CHANGELOG.md) · [گزارش مشکل در GitHub](https://github.com/dev-aly3n/aipager/issues)

## <a id="install"></a>نصب

یک ایجنت برنامه‌نویسی دارید (Claude Code یا هر ایجنتی که shell دارد)؟ با [@BotFather](https://t.me/BotFather) یک ربات بسازید، در آن Start را بزنید و مطمئن شوید روی این کامپیوتر در Claude Code لاگین هستید. توکن ربات را در فایلی ذخیره کنید که ایجنت هیچ‌وقت لازم نباشد آن را بخواند (توکن را پیست کنید، Enter بزنید و بعد Ctrl-D):

<div dir="ltr">

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

</div>

بعد این پیام را به ایجنت بدهید:

<div dir="ltr">

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

</div>

اگر آیدی عددی تلگرامتان را نمی‌دانید، همین را به ایجنت بگویید؛ خودش پیدایش می‌کند.

راهنمایی که ایجنت دنبال می‌کند، از جمله اینکه توکن را چطور امن تحویل بگیرد، [نصب با ایجنت](install-with-an-agent.md) است. برای نصب دستی، ادامه را بخوانید.

روی Linux یا macOS، با هر معماری پردازنده. `dtach` خودکار از طریق وابستگی [`dtach-bin`](https://pypi.org/project/dtach-bin/) نصب می‌شود و پکیج سیستمی جداگانه‌ای لازم نیست.

### <a id="one-line-install-recommended"></a>نصب با یک خط (پیشنهادی)

<div dir="ltr">

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

</div>

اگر `uv` نصب باشد از آن استفاده می‌کند، وگرنه از `pipx`، وگرنه (فقط روی macOS) از Homebrew. اگر هیچ‌کدام نباشد، اول `uv` (ابزار مدیریت برنامه‌های Python از Astral) را نصب می‌کند و aipager را با آن نصب می‌کند.

### <a id="uv-recommended-on-macos"></a>uv (پیشنهادی روی macOS)

<div dir="ltr">

```sh
uv tool install aipager     # اگر uv از قبل نصب است
```

</div>

یا برای اینکه اول خود uv نصب شود:

<div dir="ltr">

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

</div>

uv مفسر Python خودش را همراه دارد، پس روی هر نسخه‌ای از macOS یا Linux کار می‌کند، فرقی نمی‌کند Python سیستم در چه وضعی باشد. این‌طوری از ناسازگاری Python هوم‌برو با Xcode که در بخش Homebrew پایین‌تر آمده، دور می‌مانید.

### <a id="pipx"></a>pipx

<div dir="ltr">

```sh
pipx install aipager
```

</div>

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS، Linuxbrew)

> **نکته:** روی macOS راه پیشنهادی `uv tool install aipager` است. فرمول brew وقتی کار می‌کند که بستهٔ `python@3.12` هوم‌برو با Xcode و Command Line Tools شما هماهنگ باشد، اما هر از گاهی این دو از هم جدا می‌افتند؛ آخرین بار روی **macOS Tahoe (26.x)** که نصب با خطای `pyexpat _XML_SetAllocTrackerActivationThreshold` می‌خوابد ([issue در upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). معمولاً آپدیت Xcode و Command Line Tools درستش می‌کند، ولی ساده‌تر این است که همان uv را به کار ببرید.

<div dir="ltr">

```sh
brew install dev-aly3n/tap/aipager
```

</div>

`dtach` را از فرمول استاندارد Homebrew می‌گیرد و aipager را در یک venv پایتونی نصب می‌کند که Homebrew مدیریتش می‌کند.

### <a id="docker"></a>Docker

ایمیجی کامل که python، node، `claude` و `dtach` داخلش هست؛ مناسب VPS، NAS یا Raspberry Pi، جایی که نمی‌خواهید Python یا Node روی خود سیستم نصب باشد. برای چند معماری (amd64، arm64).

<div dir="ltr">

```sh
# 1. ویزارد راه‌اندازی را یک بار اجرا کنید (با سؤال و جواب)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. aipager را اجرا کنید (در پس‌زمینه، با اجرای دوبارهٔ خودکار)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

</div>

پوشه‌هایی را که می‌خواهید Claude ویرایششان کند زیر `/workspace` مونت کنید. مونت `~/.claude` اطلاعات ورود Claude و تاریخچهٔ گفتگوهایتان را به کانتینر می‌برد؛ برای لاگین، یک بار `claude` را روی خود سیستم اجرا کنید، یا با `docker exec -it aipager claude` داخل کانتینر لاگین کنید.

تگ‌ها: `latest`، `1.0`، `1.0.1` (یکی برای هر نسخهٔ دقیق و یکی برای هر نسخهٔ minor).

### <a id="nix-flake"></a>Nix flake

<div dir="ltr">

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

</div>

aipager را از سورس، با وابستگی‌های قفل‌شدهٔ nixpkgs، می‌سازد. `dtach` را Nix می‌دهد، ولی `claude` را **نه**؛ آن را جدا نصب کنید (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`، یا طبق مستندات Anthropic).

برای تنظیمات declarative در NixOS یا Home Manager، aipager را به‌عنوان یک ورودی flake اضافه کنید و پکیجش را در `environment.systemPackages` بگذارید:

<div dir="ltr">

```nix
{
  inputs.aipager.url = "github:dev-aly3n/aipager";

  outputs = { self, nixpkgs, aipager, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [({ pkgs, ... }: {
        environment.systemPackages = [
          aipager.packages.${pkgs.system}.default
        ];
      })];
    };
  };
}
```

</div>

بعد `aipager service install` یک سرویس systemd کاربری برایش راه می‌اندازد.

### <a id="snap"></a>Snap

<div dir="ltr">

```sh
snap install aipager
```

</div>

یک snap با محدودیت کامل (strict confinement) که python، node، `claude`، `dtach` و aipager را با هم دارد. به خاطر سندباکس snap، پوشه‌های کاری باید زیر `~/` باشند (مثلاً `~/projects/foo`). فایل تعریفش در [`packaging/snap/`](../../../packaging/snap/) است.

## <a id="configure"></a>تنظیم

<div dir="ltr">

```sh
aipager config
```

</div>

ویزاردی با سؤال و جواب: توکن ربات تلگرام (از [@BotFather](https://t.me/BotFather)) و آیدی چت را می‌پرسد، چکشان می‌کند و بعد هوک‌های لازم را خودش در `~/.claude/settings.json` اضافه می‌کند. لازم نیست هیچ فایلی را دستی ویرایش کنید.

می‌خواهید aipager را با یک اسکریپت یا ایجنت برنامه‌نویسی برای کسی راه بیندازید؟ `aipager setup --token-file FILE --chat-id ID` همین کار را بدون هیچ سؤالی انجام می‌دهد (به [دستور setup](commands.md#command-line-aipager-setup-for-coding-agents) و [راهنمای ایجنت‌ها](install-with-an-agent.md) نگاه کنید).

## <a id="run"></a>اجرا

<div dir="ltr">

```sh
aipager start
```

</div>

aipager در همان ترمینال باز می‌ماند. در یک ترمینال دیگر یک سشن Claude باز کنید:

<div dir="ltr">

```sh
aipager session dev
```

</div>

این دستور یک سشن dtach به اسم `claude-dev` می‌سازد که Claude Code در آن اجرا می‌شود (یا اگر از قبل باشد، دوباره به آن وصل می‌شود). aipager در چند ثانیه پیدایش می‌کند و تلگرام شروع به نشان دادنش می‌کند. از خود تلگرام هم می‌توانید سشن شروع کنید: `/new dev` را برای ربات بفرستید. برای وصل شدن دوباره، همین دستور را باز اجرا کنید؛ برای بیرون آمدن بدون متوقف کردن Claude، ترمینال یا پنل tmux را ببندید (سشن به کارش ادامه می‌دهد).

اگر سشن dtach بسته شده (مثلاً کامپیوتر ری‌استارت شده) ولی می‌خواهید گفتگوی Claude را از روی دیسک ادامه دهید، `--continue` را اضافه کنید:

<div dir="ltr">

```sh
aipager session dev --continue    # آخرین گفتگوی claude در همین پوشه
```

</div>

می‌توانید `--resume <session-id>` (یا هر فلگ دیگر claude) را هم آخر دستور بدهید تا به claude برسد:

<div dir="ltr">

```sh
aipager session dev -- --resume abc1234
```

</div>

### <a id="run-as-a-service-survives-logout"></a>اجرا به‌شکل سرویس (بعد از خروج از سیستم هم می‌ماند)

<div dir="ltr">

```sh
aipager service install
```

</div>

روی Linux یک سرویس systemd کاربری در `~/.config/systemd/user/aipager.service` می‌نویسد و اجرایش می‌کند. روی macOS یک plist برای launchd در `~/Library/LaunchAgents/com.aipager.daemon.plist` می‌نویسد و راهش می‌اندازد. زیردستورها: `start`، `stop`، `status`، `logs`، `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>استفادهٔ تیمی (گروه‌ها و آدم‌های دیگر)

aipager به‌طور پیش‌فرض یک ربات خصوصی است: شما و ربات در یک چت خصوصی. برای اینکه با دیگران شریکش شوید، یک گروه تلگرام، چت خصوصی آدم‌های دیگر با ربات، یا هر دو را اضافه کنید. برای گروه: ربات را به گروه اضافه کنید (به‌عنوان ادمین، تا بتواند نوار وضعیت گروه را سنجاق کند)، دوباره `aipager config` را اجرا کنید و **Add a group scope** را انتخاب کنید. ویزارد شما را (مالک چت خصوصی خودتان) به‌عنوان مالک گروه پیشنهاد می‌کند، هر عضو را با یک نقش (`owner`، `admin`، `user` یا `read_only`) اضافه می‌کند، پوشهٔ ریشهٔ اعضا را می‌پرسد و aipager در حال اجرا را بدون ری‌استارت با تنظیمات جدید به‌روز می‌کند. ربات در گروه فقط به فرمان‌ها، جواب به پیام‌های خودش و منشن‌ها واکنش نشان می‌دهد (`@aipagerbot fix the tests`، `/jim run the tests`) و هر پیام با نقش فرستنده‌اش اجرا می‌شود. عضوی با نقش `user` یا `read_only` فقط داخل پوشهٔ ریشهٔ چت خودش سشن شروع می‌کند و نگه می‌دارد (پیش‌فرض: `~/aipager-users/<label>`). **اضافه کردن یک عضو یعنی به او اجازهٔ اجرای کد روی کامپیوترتان را می‌دهید**: کل مدل اعتماد در [استفادهٔ تیمی و گروه](groups.md) آمده است.

## <a id="what-it-does"></a>چه کار می‌کند

- کاری را که هر سشن Claude Code انجام می‌دهد در تلگرام نشان می‌دهد: در حال کار یا آماده، ابزارهایی که اجرا می‌کند، درصد کانتکست، هزینه و خط‌های تغییرکرده، روی یک کارت برای هر کار، و بعد جواب Claude را
- پیام‌هایتان را **فوراً** به Claude می‌فرستد، حتی وقتی در حال کار است: چند پیام پشت هم بفرستید و Claude خودش آن‌ها را در صف می‌گذارد، درست مثل تایپ کردن در ترمینال. 👀 یعنی ارسال شد، 👍 یعنی Claude آن را برداشت
- وقتی یک درخواست اجازه یا سؤال باز است، پیام را نگه می‌دارد تا جواب آن حساب نشود، و بعد از اینکه جواب دادید ارسالش می‌کند
- درخواست‌های اجازه و سؤال‌های Claude را با دکمه نشان می‌دهد (اجازه بده، رد کن، همیشه اجازه بده، یک دکمه برای هر گزینه)؛ دکمه‌ای که از یک کار تمام‌شده جا مانده، به جای اینکه روی کار فعلی اثر بگذارد، رد می‌شود
- برای هر سشن سه مود اجازه دارد که با `/mode` عوض می‌شود: 💬 مود پرسش (Claude قبل از هر کاری می‌پرسد)، 🤖 مود خودکار که پیش‌فرض است (بررسی ایمنی خود Claude Code کارهای بی‌خطر را اجرا می‌کند و کارهای پرریسک را نگه می‌دارد تا شما تصمیم بگیرید) و 🔓 مود دسترسی کامل (همه‌چیز بدون هیچ بررسی‌ای اجرا می‌شود؛ برای ادمین‌ها، و برای خودتان در چت خصوصی خودتان)
- از داخل چت سشن شروع می‌کند: `/new x1` فوراً یکی شروع می‌کند (`/new x1 fix the tests` اولین پیامش را هم می‌فرستد)، با مود، مدل و پوشه‌ای که در `/settings` تنظیم شده، و دکمه‌هایی برای عوض کردنشان
- عکس، فایل و ویس می‌گیرد (ویس روی کامپیوتر خودتان به متن تبدیل می‌شود، با افزونهٔ اختیاری voice)
- دربارهٔ هشدار کانتکست، کامپکت شدن گفتگو، سشنی که بسته شد و سشنی که مدتی بی‌خبر مانده خبرتان می‌کند
- چند سشن را همزمان با یک ربات اجرا می‌کند؛ استفادهٔ تیمی اختیاری با نقش‌ها و قانون برای هر ابزار ([استفادهٔ تیمی و گروه](groups.md))
- ربات‌های ناظر فقط‌خواندنی، به‌شکل اختیاری
- به English، Español، Русский، Português (Brasil)، 简体中文، Français، Deutsch، 한국어، العربية، Bahasa Indonesia، Українська، Italiano، Türkçe، فارسی و Tiếng Việt حرف می‌زند: فقط متن‌های خود aipager، نه سشن‌ها و نه جواب‌های Claude ([زبان‌ها](commands.md#languages)). این صفحه و مستندات هم ترجمه شده‌اند

### <a id="mini-app"></a>Mini App

فرمان `/app` داشبوردی را داخل تلگرام باز می‌کند: سشن‌هایتان با دکمه‌های توقف، بستن، ری‌استارت، تغییر اسم و تغییر مود، نمایشگر diff برای تغییرات `Write`/`Edit`، تنظیمات و، برای مالک، «گزارش مشکل». خود aipager آن را اجرا می‌کند و **به‌طور پیش‌فرض روشن است**؛ هر درخواست با امضای `initData` تلگرام چک می‌شود. اگر در بیش از یک چت عضو باشید (چت خصوصی‌تان و یک گروه)، ردیفی از اسم چت‌ها در بالا جابه‌جا شدن بینشان را ممکن می‌کند. با `aipager miniapp enable|disable|status` مدیریتش کنید، یا به جای تونل مدیریت‌شده آن را روی آدرس خودتان بگذارید؛ به [مدل امنیتی](security.md#mini-app-tunnel) نگاه کنید.

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>نکته دربارهٔ دکمه‌های مدل (کاربران Bedrock و Vertex)

زیرمنوی **مدل** در کیبورد ثابت (و انتخاب‌گرهای مدل در Mini App) اسم‌های کوتاه خانواده‌های مدل در Claude Code، یعنی `sonnet`، `opus`، `haiku`، `fable` و `opusplan`، و مدل‌های ثابتی مثل `claude-opus-5-5` و `claude-sonnet-5-5` را پیشنهاد می‌کند (فهرست کامل در [کیبورد ثابت](commands.md#persistent-keyboard)). روی API خود Anthropic، اسم کوتاه همیشه به جدیدترین مدل آن خانواده می‌رسد. روی **Bedrock** و **Vertex** همین اسم‌ها بسته به نسخه‌هایی که ارائه‌دهنده‌تان دارد ممکن است به نسخه‌های قدیمی‌تر برسند. اگر از این‌ها استفاده می‌کنید و یک مدل مشخص را می‌خواهید، `/model <full-id>` را از چت بفرستید.

## <a id="developing-locally"></a>توسعه روی کامپیوتر خودتان

<div dir="ltr">

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

</div>

`dtach` با وابستگی `dtach-bin` می‌آید، پس چیز دیگری لازم نیست. تست‌ها را مثل بالا با سقف حافظه اجرا کنید (روی macOS بدون `systemd-run`): یک تست از کنترل خارج‌شده وگرنه ممکن است همهٔ حافظهٔ کامپیوتر را بگیرد.

روند انتشار نسخه در [CONTRIBUTING.md](../../../CONTRIBUTING.md) آمده است.

## <a id="license"></a>مجوز

MIT؛ متن کامل در [LICENSE](../../../LICENSE).

## <a id="docs"></a>مستندات

| صفحه | دربارهٔ چه |
|---|---|
| [نصب با ایجنت](install-with-an-agent.md) | مراحلی که یک ایجنت برنامه‌نویسی برای نصب و راه‌اندازی aipager برای صاحبش دنبال می‌کند |
| [معماری](architecture.md) | ساختار برنامه، نمودار اجزا، جای فایل‌ها و سوکت‌ها |
| [رویدادهای هوک](hooks.md) | همهٔ رویدادهایی که aipager از Claude Code می‌گیرد، با فیلدهایشان |
| [فرمان‌های ربات](commands.md) | فرمان‌ها، دکمه‌ها، Mini App، ویس و فایل، همهٔ دستورهای `aipager` و تنظیماتی که از محیط خوانده می‌شود |
| [استفادهٔ تیمی و گروه](groups.md) | راه‌اندازی چندنفره، نقش‌ها، قانون‌ها، ثبت اینکه چه کسی چه کرد |
| [ربات‌های ناظر](observers.md) | ربات‌های فقط‌خواندنی که یک کپی از اعلان‌ها را می‌گیرند |
| [رفع مشکل](troubleshooting.md) | خطاهای رایج و جدول راه‌حل `aipager doctor` |
| [گزارش مشکل](problem-reports.md) | گزارش مشکل چه چیزهایی دارد، کجا می‌رود و aipager کی پیشنهادش را می‌دهد |
| [مدل امنیتی](security.md) | مرز اعتماد، رازها، لاگ ممیزی، ارتباطات شبکه |

</div>
