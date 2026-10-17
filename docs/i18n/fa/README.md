<div dir="rtl">

<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ e6f0c88 -->

<h3>حالا Claude هم یکی از چت‌های شماست.</h3>

سشن‌های [Claude Code](https://claude.com/claude-code) شما در تلگرام، کنار چت خانواده، دوستان و تیمتان.<br>
روی کامپیوتر خودتان اجرا می‌شوند. هر جا که باشید، کارشان را می‌بینید، جواب سؤال‌هایشان را می‌دهید و کار بعدی را برایشان می‌فرستید.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[وب‌سایت](https://aipager.run)** · **[نصب](#install)** · **[مستندات](#documentation)** · **[امنیت](security.md)** · **[تغییرات نسخه‌ها](../../../CHANGELOG.md)**

**به زبان‌های دیگر:** [English](../../../README.md) ·&rlm; [Español](../es/README.md) ·&rlm; [Русский](../ru/README.md) ·&rlm; [Português (Brasil)](../pt-BR/README.md) ·&rlm; [简体中文](../zh-Hans/README.md) ·&rlm; [Français](../fr/README.md) ·&rlm; [Deutsch](../de/README.md) ·&rlm; [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) ·&rlm; [Українська](../uk/README.md) ·&rlm; [Italiano](../it/README.md) ·&rlm; [Türkçe](../tr/README.md) ·&rlm; [Tiếng Việt](../vi/README.md)

<sub>این صفحه ترجمهٔ صفحهٔ انگلیسی است؛ اگر جایی با هم فرق داشتند، نسخهٔ انگلیسی درست است. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="پیامی که در تلگرام تایپ شده به Claude Code در ترمینال می‌رسد و یک کارت در چت، هم‌زمان با کار Claude، خودش آپدیت می‌شود">

<sub>شما در تلگرام می‌نویسید. aipager آن را در Claude Code روی کامپیوترتان تایپ می‌کند و یک کارت، هم‌زمان با کار Claude، خودش آپدیت می‌شود.</sub>

</div>

<br>

## <a id="why-aipager"></a>چرا aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 یک کارت زنده برای هر کار**<br>
اینکه Claude همین حالا چه می‌کند: ابزارهایی که اجرا می‌کند، کانتکست، هزینه و خط‌های تغییرکرده، و بعد جواب. کارت خودش آپدیت می‌شود، پس چت شلوغ نمی‌شود.

</td>
<td width="33%" valign="top">

**⚡ وسط کار هم پیام بدهید**<br>
پیامتان همان لحظه در سشن تایپ می‌شود، حتی وقتی Claude در حال کار است، درست مثل تایپ کردن در ترمینال. 👀 یعنی ارسال شد، 👍 یعنی Claude آن را برداشت.

</td>
<td width="33%" valign="top">

**✅ جواب با یک دکمه**<br>
درخواست‌های اجازه و سؤال‌های Claude با یک دکمه برای هر جواب می‌رسند. دکمه‌ای که از یک کار تمام‌شده جا مانده، به جای اینکه روی کار بعدی اثر بگذارد، رد می‌شود.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ سه مود اجازه**<br>
💬 مود پرسش، 🤖 مود خودکار (پیش‌فرض سشن‌های جدید) و 🔓 مود دسترسی کامل؛ برای هر سشن جدا تنظیم می‌شود و هر وقت بخواهید با &lrm;`/mode` عوض می‌شود.

</td>
<td width="33%" valign="top">

**👥 برای کار تیمی**<br>
ربات را به یک گروه اضافه کنید. هر پیام با نقش فرستنده‌اش اجرا می‌شود، می‌شود اعضا را به پوشهٔ خودشان محدود کرد و هر دکمه‌ای که کسی بزند در لاگ ممیزی ثبت می‌شود.

</td>
<td width="33%" valign="top">

**🔒 هیچ واسطه‌ای در کار نیست**<br>
نه سرور aipager، نه حساب کاربری، نه پورت باز. توکن ربات خودتان، کامپیوتر خودتان، چت‌های خودتان.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>یک گشت کوتاه

### <a id="questions-come-to-you"></a>سؤال‌ها به دست خودتان می‌رسند

وقتی Claude چیزی از شما می‌پرسد یا اجازه می‌خواهد، سؤال با یک دکمه برای هر جواب در چتتان می‌آید و نوار سنجاق‌شده می‌گوید چه چیزی منتظر شماست. دکمه‌ای که می‌زنید، انگار پشت کیبورد نشسته باشید، در سشن تایپ می‌شود و ترمینال هم همان جواب را نشان می‌دهد.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude می‌پرسد timeout را بیشتر کند یا منتظر کوکی بماند؛ جوابی که در تلگرام زده شده در ترمینال انتخاب می‌شود">

### <a id="three-permission-modes"></a>سه مود اجازه

هر سشن در یکی از سه مود است و هر وقت بخواهید با &lrm;`/mode` عوض می‌شود، حتی وقتی Claude در حال کار است.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="یک کار در مود پرسش، مود خودکار و مود دسترسی کامل">

| مود | چه اتفاقی می‌افتد |
|---|---|
| 💬 **مود پرسش**<br>&lrm;`/mode ask` | Claude قبل از هر کاری می‌پرسد. |
| 🤖 **مود خودکار**، پیش‌فرض<br>&lrm;`/mode auto` | بررسی ایمنی خود Claude Code کارهای بی‌خطر را اجرا می‌کند و کارهای پرریسک را نگه می‌دارد تا شما تصمیم بگیرید. |
| 🔓 **مود دسترسی کامل**<br>&lrm;`/mode full` | همه‌چیز بدون هیچ بررسی‌ای اجرا می‌شود. برای ادمین‌ها، و برای خودتان در چت خصوصی خودتان با ربات. |

جابه‌جایی بین مود پرسش و مود خودکار همان‌جا انجام می‌شود، بدون ری‌استارت. رفتن به مود دسترسی کامل یا بیرون آمدن از آن سشن را ری‌استارت می‌کند و گفتگو سر جایش می‌ماند. سؤال‌های Claude در هر مودی به شما می‌رسند.

### <a id="your-team-in-one-group"></a>تیمتان، در یک گروه

ربات را به یک گروه اضافه کنید و به هر عضو یک نقش بدهید: `owner`،&rlm; `admin`،&rlm; `user` یا `read_only`. ربات در گروه فقط به فرمان‌ها، جواب به پیام‌های خودش و منشن‌ها واکنش نشان می‌دهد (&lrm;`@aipagerbot fix the tests`،&rlm; &lrm;`/web run the tests`) و هر پیام با نقش فرستنده‌اش اجرا می‌شود: ابزاری که نقش اجازه‌اش را ندارد، قبل از اجرا رد می‌شود. عضوی با نقش `user` یا `read_only` فقط داخل پوشهٔ ریشهٔ خودش کار می‌کند (پیش‌فرض: &lrm;`~/aipager-users/<label>`&lrm;). آدم‌های دیگر هم می‌توانند چت خصوصی خودشان را با ربات داشته باشند.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="گروهی تیمی که در آن یک فرمان به خاطر نقش یکی از اعضا رد می‌شود و تست‌ها پاس می‌شوند">

> **⚠️ مهم:** اضافه کردن یک عضو یعنی به او اجازهٔ اجرای کد روی کامپیوترتان را می‌دهید. قبل از اینکه ربات را با کسی شریک شوید، [مدل اعتماد](groups.md) را بخوانید.

برای راه‌اندازی، ربات را به‌عنوان ادمین به گروه اضافه کنید (تا بتواند نوار وضعیت گروه را سنجاق کند)، دوباره `aipager config` را اجرا کنید و **Add a group scope** را انتخاب کنید. ویزارد هر عضو را با یک نقش اضافه می‌کند، پوشهٔ ریشهٔ اعضا را می‌پرسد و بدون ری‌استارت تنظیمات جدید را به aipager در حال اجرا می‌دهد.

### <a id="a-dashboard-inside-telegram"></a>یک داشبورد داخل تلگرام

فرمان &lrm;`/app`،&rlm; Mini App را باز می‌کند: سشن‌هایتان با دکمه‌های توقف، بستن، ری‌استارت، تغییر نام و تغییر مود، نمایشگر diff برای هر `Write` و `Edit`، تنظیمات و، برای مالک، «گزارش مشکل». خود aipager آن را اجرا می‌کند و به‌طور پیش‌فرض روشن است، و هر درخواست با امضای تلگرام چک می‌شود. اگر در بیش از یک چت باشید، ردیفی از اسم چت‌ها در بالا بینشان جابه‌جا می‌کند. با `aipager miniapp disable` خاموشش کنید، یا آن را روی آدرس خودتان بگذارید ([چطور](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>پانزده زبان

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="left" alt="یک درخواست اجازه به فارسی، راست به چپ، با عددهای فارسی">

- به English،&rlm; Español،&rlm; Русский،&rlm; Português (Brasil)،&rlm; 简体中文،&rlm; Français،&rlm; Deutsch،&rlm; 한국어، العربية، Bahasa Indonesia،&rlm; Українська،&rlm; Italiano،&rlm; Türkçe، فارسی و Tiếng Việt حرف می‌زند.
- با &lrm;`/language` یکی را انتخاب کنید، یا بگذارید چت خصوصی از زبان اپ تلگرامتان پیروی کند.
- فارسی و عربی راست به چپ خوانده می‌شوند، با عددهای خودشان.
- فقط متن‌های خود aipager عوض می‌شود. سشن‌هایتان نه: Claude به همان زبانی جواب می‌دهد که با آن برایش می‌نویسید.
- این صفحه و [مستندات](#documentation) هم ترجمه شده‌اند.

<br clear="left">

### <a id="and-the-small-things"></a>و چیزهای کوچک

- **چند سشن، یک ربات.** پیام معمولی به سشنی ارسال می‌شود که آخرین بار با آن حرف زده‌اید؛ برای حرف زدن با سشن دیگر، روی یکی از پیام‌هایش ریپلای کنید یا &lrm;`/x1 <message>`&lrm; را بفرستید.
- **هیچ‌چیز قاطی نمی‌شود.** پیامی که وقتی یک درخواست اجازه یا سؤال باز است می‌فرستید، صبر می‌کند تا جواب بدهید، پس هیچ‌وقت جواب آن حساب نمی‌شود.
- **عکس، فایل و ویس.** ویس‌ها روی کامپیوتر خودتان به متن تبدیل می‌شوند، با افزونهٔ اختیاری `voice`.
- **خبرتان می‌کند.** هشدار کانتکست، کامپکت شدن گفتگو، سشنی که بسته شد و سشنی که مدتی ساکت مانده.
- **سشن‌ها سر جایشان می‌مانند.** Claude داخل `dtach` اجرا می‌شود، پس بعد از بستن ترمینال، قطع شدن SSH یا ری‌استارت aipager هم به کارش ادامه می‌دهد.

## <a id="how-it-works"></a>چطور کار می‌کند

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code، هوکش، aipager، تلگرام و چت‌های شما، پشت سر هم">

&rlm;Claude Code در یک ترمینال واقعی، داخل `dtach`، اجرا می‌شود تا لازم نباشد کسی پنجره‌ای را باز نگه دارد. هر قدم یک [هوک](hooks.md) را اجرا می‌کند که یک پیام کوتاه از طریق یک سوکت محلی برای aipager می‌فرستد، و aipager یک پیام را در چتتان آپدیت می‌کند. جواب‌های شما هم از همین راه برمی‌گردند: یا تصمیم به هوکی که منتظر آن است، یا کلیدها به داخل سشن. ترمینال همیشه مرجع اصلی می‌ماند. جزئیات در [معماری](architecture.md).

## <a id="install"></a>نصب

روی Linux یا macOS، با هر معماری پردازنده. Claude Code باید روی این کامپیوتر نصب و لاگین باشد، و یک ربات تلگرام لازم دارید: با &lrm;[@BotFather](https://t.me/BotFather) یکی بسازید و در آن Start را بزنید.

<div dir="ltr">

```sh
curl -fsSL https://aipager.run/install | sh   # uv، pipx یا Homebrew: هر کدام که دارید
aipager config                                # توکن ربات، چت شما، هوک‌های Claude Code
aipager service install                       # aipager را بعد از خروج از سیستم هم روشن نگه می‌دارد
aipager session api                           # Claude Code در همین پوشه، و در چت شما
```

</div>

اسکریپت نصب اگر `uv` داشته باشید از آن استفاده می‌کند، وگرنه از `pipx`، وگرنه (روی macOS) از Homebrew، و اگر هیچ‌کدام نباشد `uv` را نصب می‌کند. `dtach` همراه aipager می‌آید (پکیج [`dtach-bin`](https://pypi.org/project/dtach-bin/))، پس چیز دیگری لازم نیست نصب کنید. `aipager config` یک ویزارد است: توکن ربات و چتتان را چک می‌کند و بعد هوک‌های aipager را خودش در &lrm;`~/.claude/settings.json` اضافه می‌کند. لازم نیست هیچ فایلی را دستی ویرایش کنید.

<details>
<summary><b>بگذارید یک ایجنت برنامه‌نویسی نصبش کند</b></summary>

<br>

یک ایجنت برنامه‌نویسی دارید (Claude Code یا هر ایجنتی که shell دارد)؟ ربات را بسازید، در آن Start را بزنید و توکن ربات را در فایلی ذخیره کنید که ایجنت هیچ‌وقت لازم نباشد آن را بخواند (توکن را پیست کنید، Enter بزنید و بعد Ctrl-D):

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

اگر آیدی عددی تلگرامتان را نمی‌دانید، همین را به ایجنت بگویید؛ خودش پیدایش می‌کند. راهنمایی که ایجنت دنبال می‌کند، از جمله اینکه توکن را چطور امن تحویل بگیرد، [نصب با ایجنت](install-with-an-agent.md) است. برای اسکریپت‌ها، `aipager setup --token-file FILE --chat-id ID` بدون هیچ سؤالی aipager را راه می‌اندازد ([دستور setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>راه‌های دیگر نصب

<div dir="ltr">

```sh
uv tool install aipager                        # uv: پیشنهادی روی macOS، Python خودش را همراه دارد
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew، روی macOS و Linuxbrew (نکتهٔ پایین را ببینید)
nix profile install github:dev-aly3n/aipager   # Nix: claude همراهش نیست (پایین‌تر را ببینید)
snap install aipager                           # Snap: با محدودیت کامل، claude داخلش، پوشه‌های کاری زیر ~/
```

</div>

یا آن را در [Docker](#docker) اجرا کنید، با Python،&rlm; Node،&rlm; `claude` و `dtach` داخلش.

<details>
<summary><b>نکتهٔ Homebrew روی macOS 26 (Tahoe)</b></summary>

<br>

فرمول brew وقتی کار می‌کند که بستهٔ `python@3.12` هوم‌برو با Xcode و Command Line Tools شما هماهنگ باشد، اما هر از گاهی این دو از هم جدا می‌افتند. روی macOS 26 نصب با خطای `pyexpat _XML_SetAllocTrackerActivationThreshold` می‌خوابد ([issue در upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). معمولاً آپدیت Xcode و Command Line Tools درستش می‌کند، ولی با `uv tool install aipager` کلاً به این مشکل نمی‌خورید. فرمول، `dtach` را از Homebrew می‌گیرد و aipager را در یک محیط Python نصب می‌کند که Homebrew مدیریتش می‌کند.

</details>

### <a id="docker"></a>Docker

ایمیجی کامل با Python،&rlm; Node،&rlm; `claude` و `dtach`؛ مناسب VPS،&rlm; NAS یا Raspberry Pi، جایی که نمی‌خواهید Python یا Node روی خود سیستم نصب باشد. برای چند معماری (amd64،&rlm; arm64).

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

پوشه‌هایی را که می‌خواهید Claude ویرایششان کند زیر &lrm;`/workspace` مونت کنید. مونت &lrm;`~/.claude` اطلاعات ورود Claude و تاریخچهٔ گفتگوهایتان را به کانتینر می‌برد؛ برای لاگین، یک بار `claude` را روی خود سیستم اجرا کنید، یا با `docker exec -it aipager claude` داخل کانتینر لاگین کنید.

تگ‌ها: `latest`،&rlm; `1.3`،&rlm; `1.3.5` (یکی برای هر نسخهٔ دقیق و یکی برای هر نسخهٔ minor).

### <a id="nix"></a>Nix

<div dir="ltr">

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

</div>

این دستورها aipager را از سورس، با وابستگی‌های قفل‌شدهٔ nixpkgs، می‌سازند. `dtach` را Nix می‌دهد ولی `claude` را نه؛ آن را جدا نصب کنید (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`، یا طبق مستندات Anthropic).

<details>
<summary><b>برای NixOS و Home Manager</b></summary>

<br>

پروژهٔ aipager را به‌عنوان یک ورودی flake اضافه کنید و پکیجش را در `environment.systemPackages` بگذارید:

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

</details>

## <a id="everyday-use"></a>استفادهٔ روزمره

### <a id="in-telegram"></a>در تلگرام

| بفرستید | برای |
|---|---|
| &lrm;`/status` | دیدن سشن‌های این چت، هر کدام با وضعیت و دکمه‌هایش. |
| &lrm;`/new x1` | شروع یک سشن به اسم x1.&rlm; &lrm;`/new x1 fix the tests` اولین پیامش را هم می‌فرستد. |
| &lrm;`/x1` | اینکه پیام‌هایتان به x1 ارسال شوند. &lrm;`/x1 <message>`&lrm; یک پیام را مستقیم به آن می‌فرستد. |
| &lrm;`/stop` | قطع کردن کار در حال اجرا و دور ریختن پیام‌های در صف. |
| &lrm;`/mode` | جابه‌جایی بین مود پرسش، مود خودکار و مود دسترسی کامل. |
| &lrm;`/app` | باز کردن Mini App. |
| &lrm;`/language` | انتخاب زبانی که aipager در این چت با آن حرف می‌زند. |
| &lrm;`/update` | آپدیت aipager و Claude Code و بعد ری‌استارت همان‌جا (برای ادمین‌ها). |
| &lrm;`/help` | یک راهنمای کوتاه، کار به کار. |

همهٔ فرمان‌ها، دکمه‌ها و تنظیمات در [فرمان‌های ربات](commands.md) آمده است.

### <a id="on-the-command-line"></a>در خط فرمان

<div dir="ltr">

```sh
aipager start                             # خود aipager، در همین ترمینال
aipager session dev                       # یک سشن به اسم dev همین‌جا شروع کنید، یا به آن وصل شوید
aipager session dev --continue            # آخرین گفتگوی Claude در همین پوشه را ادامه دهید
aipager session dev -- --resume abc1234   # هر فلگ دیگر claude همان‌طور که هست به آن می‌رسد
aipager doctor                            # نصب را چک کنید
```

</div>

برای بیرون آمدن از سشن بدون متوقف کردن Claude، ترمینال یا پنل tmux را ببندید: سشن به کارش ادامه می‌دهد و همان دستور دوباره به آن وصل می‌شود.

دستور `aipager service install` خود aipager را در پس‌زمینه روشن نگه می‌دارد: روی Linux یک سرویس systemd کاربری (&lrm;`~/.config/systemd/user/aipager.service`) و روی macOS یک agent برای launchd (`~/Library/LaunchAgents/com.aipager.daemon.plist`). در هر دو حالت aipager در پوشهٔ خانهٔ شما اجرا می‌شود و سشن جدید هم همان‌جا شروع می‌شود، مگر اینکه پوشهٔ دیگری انتخاب کنید. با `aipager service start`،&rlm; `stop`،&rlm; `status`،&rlm; `logs` و `uninstall` مدیریتش کنید.

## <a id="security"></a>امنیت

- **سرور aipager در کار نیست.** خود aipager با توکن ربات شما مستقیم با Bot API تلگرام حرف می‌زند. نه حساب کاربری aipager وجود دارد، نه سرویس aipager.
- **پورت بازی در کار نیست.** خود aipager فقط روی loopback گوش می‌دهد. Mini App از طریق یک تونل Cloudflare بیرون می‌رود که هر درخواست در آن باید امضای تلگرام را داشته باشد، و `aipager miniapp disable` خاموشش می‌کند.
- **هر جوابی ثبت می‌شود.** هر «اجازه بده»، «رد کن» و جواب، به &lrm;`~/.claude/aipager-audit.jsonl` اضافه می‌شود و در گروه اینکه چه کسی دکمه را زده هم ثبت می‌شود.
- **تصمیم هنوز با Claude Code است.** تنظیمات Claude Code شما تعیین می‌کند چه چیزی باید پرسیده شود. aipager فقط سؤال را می‌رساند و هیچ‌وقت دسترسی بالاتر نمی‌گیرد: نه sudo، و همه‌چیز با کاربر خود شما اجرا می‌شود.

تصویر کامل، از جمله اینکه چه چیزی از اطلاعات ورود Claude شما محافظت می‌کند، در [مدل امنیتی](security.md) آمده است.

## <a id="faq"></a>سؤال‌های رایج

<details>
<summary><b>این با اپ‌های موبایل Claude چه فرقی دارد؟</b></summary>

<br>

اپ‌های Claude با دستیار Anthropic روی سرورهای خودش حرف می‌زنند. aipager شما را به یک سشن Claude Code وصل می‌کند که روی کامپیوتر خودتان اجرا می‌شود: ریپوی شما، ابزارهای شما، محیط شما.

</details>

<details>
<summary><b>کد من از کامپیوترم بیرون می‌رود؟</b></summary>

<br>

خود aipager فقط همان چیزی را که در چت‌هایتان می‌بینید، با توکن ربات خودتان، به تلگرام می‌فرستد؛ هیچ سرور aipager‌ای در این وسط نیست. خود Claude Code هم مثل همیشه با Anthropic حرف می‌زند.

</details>

<details>
<summary><b>اصلاً dtach چیست؟</b></summary>

<br>

یک ابزار کوچک که برنامه را از ترمینال جدا می‌کند. Claude Code را بعد از بسته شدن ترمینال یا قطع شدن SSH زنده نگه می‌دارد، پس جوابی که یک ساعت بعد می‌فرستید هم به یک سشن زنده می‌رسد. همراه aipager می‌آید.

</details>

<details>
<summary><b>اگر Claude از مسیر خارج شود چه؟</b></summary>

<br>

درخواست‌ها منتظر دکمهٔ شما می‌مانند، &lrm;`/stop` کار را قطع می‌کند و هر چیزی را که در صف است دور می‌ریزد، &lrm;`/kill` سشن را می‌بندد، و دکمه‌های یک کار تمام‌شده دیگر عمل نمی‌کنند.

</details>

<details>
<summary><b>چطور آپدیتش کنم؟</b></summary>

<br>

فرمان &lrm;`/update` را بفرستید: aipager و Claude Code را چک می‌کند و نسخه‌های جدید را نصب می‌کند، و «🔄 همین حالا ری‌استارت کن» aipager را دوباره بالا می‌آورد، چه به‌شکل سرویس اجرا شود چه نه. سشن‌هایتان در این مدت به کارشان ادامه می‌دهند. در خط فرمان، `aipager update` با همان ابزاری که aipager را نصب کرده آپدیتش می‌کند.

</details>

<details>
<summary><b>از Bedrock یا Vertex استفاده می‌کنم. دکمه‌های مدل کار می‌کنند؟</b></summary>

<br>

منوی **مدل** در کیبورد و Mini App اسم‌های کوتاه خانواده‌های مدل در Claude Code (`sonnet`،&rlm; `opus`،&rlm; `haiku`،&rlm; `fable`،&rlm; `opusplan`) و مدل‌های ثابتی مثل `claude-opus-5-5` و `claude-sonnet-5-5` را پیشنهاد می‌کنند. روی API خود Anthropic، اسم کوتاه به جدیدترین مدل آن خانواده می‌رسد. روی Bedrock و Vertex همین اسم ممکن است بسته به آنچه ارائه‌دهنده‌تان دارد به نسخهٔ قدیمی‌تری برسد؛ برای انتخاب یک مدل مشخص، &lrm;`/model <full-id>`&lrm; را بفرستید.

</details>

<details>
<summary><b>هزینه‌اش چقدر است؟</b></summary>

<br>

هیچ. aipager با مجوز MIT منتشر شده، ربات مال خودتان است و Claude Code همان اشتراک یا کلیدی را که دارد به کار می‌برد.

</details>

## <a id="documentation"></a>مستندات

| صفحه | دربارهٔ چه |
|---|---|
| [فرمان‌های ربات](commands.md) | همهٔ فرمان‌ها، دکمه‌ها، تنظیمات و گزینه‌های خط فرمان |
| [استفادهٔ تیمی و گروه](groups.md) | نقش‌ها، پوشه‌های ریشه، اینکه چه کسی چه دکمه‌ای را می‌تواند بزند، مدل اعتماد |
| [مدل امنیتی](security.md) | مرز اعتماد، رازها، لاگ ممیزی، شبکه |
| [رویدادهای هوک](hooks.md) | کاری که هر هوک Claude Code در aipager انجام می‌دهد |
| [معماری](architecture.md) | اجزای aipager، فایل‌ها و سوکت‌ها |
| [رفع مشکل](troubleshooting.md) | `aipager doctor` و راه‌حل مشکل‌های رایج |
| [گزارش مشکل](problem-reports.md) | گزارش مشکل چه چیزهایی دارد و aipager کی پیشنهادش را می‌دهد |
| [نصب با ایجنت](install-with-an-agent.md) | راهنمایی که ایجنت برنامه‌نویسی دنبال می‌کند |

## <a id="contributing"></a>مشارکت

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

&rlm;`dtach` با وابستگی `dtach-bin` می‌آید، پس چیز دیگری لازم نیست. تست‌ها را مثل بالا با سقف حافظه اجرا کنید (روی macOS بدون `systemd-run`): وگرنه یک تست از کنترل خارج‌شده ممکن است همهٔ حافظهٔ کامپیوتر را بگیرد. روند انتشار نسخه در [CONTRIBUTING.md](../../../CONTRIBUTING.md) آمده است.

## <a id="license"></a>مجوز

&rlm;MIT؛ متن کامل در [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>

</div>
