<div dir="rtl">

<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ ec7afc2 -->

<h3>أصبح Claude مجرد دردشة أخرى.</h3>

جلسات [Claude Code](https://claude.com/claude-code) الخاصة بك في Telegram، بجانب عائلتك وأصدقائك وفريقك.<br>
تعمل على كمبيوترك أنت. أينما كنت، تتابعها وتجيب عن أسئلتها وتعطيها المهمة التالية.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[الموقع](https://aipager.run)** · **[التثبيت](#install)** · **[التوثيق](#documentation)** · **[الأمان](security.md)** · **[سجل التغييرات](../../../CHANGELOG.md)**

**اقرأ بلغة أخرى:** [English](../../../README.md) ·&rlm; [Español](../es/README.md) ·&rlm; [Русский](../ru/README.md) ·&rlm; [Português (Brasil)](../pt-BR/README.md) ·&rlm; [简体中文](../zh-Hans/README.md) ·&rlm; [Français](../fr/README.md) ·&rlm; [Deutsch](../de/README.md) ·&rlm; [한국어](../ko/README.md) ·&rlm; [Bahasa Indonesia](../id/README.md) ·&rlm; [Українська](../uk/README.md) ·&rlm; [Italiano](../it/README.md) ·&rlm; [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>هذه الصفحة مترجمة عن الإنجليزية؛ إذا اختلفت النسختان، فالنسخة الإنجليزية هي الصحيحة. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="رسالة مكتوبة في Telegram تصل إلى Claude Code في الطرفية، وبطاقة في الدردشة تحدّث نفسها بينما يعمل Claude">

<sub>تكتب في Telegram، فيكتبه aipager في Claude Code على كمبيوترك، وتحدّث بطاقة واحدة نفسها طوال المهمة.</sub>

</div>

<br>

## <a id="why-aipager"></a>لماذا aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 بطاقة حيّة لكل مهمة**<br>
ما يفعله Claude الآن: الأدوات التي يشغّلها، والسياق، والتكلفة، والأسطر المتغيرة، ثم الإجابة. البطاقة تحدّث نفسها، فتبقى الدردشة هادئة.

</td>
<td width="33%" valign="top">

**⚡ كلّمه وهو يعمل**<br>
تُكتب رسالتك في الجلسة فورًا، حتى أثناء عمل Claude، تمامًا كما لو كتبتها في الطرفية. 👀 تعني أنها أُرسلت، و👍 تعني أن Claude أخذها.

</td>
<td width="33%" valign="top">

**✅ أجب بلمسة واحدة**<br>
تصل طلبات الإذن وأسئلة Claude مع زر لكل إجابة. والزر المتبقي من مهمة انتهت يرفض بدل أن يؤثر في المهمة التالية.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ ثلاثة أوضاع للأذونات**<br>
💬 وضع «اسأل»، و🤖 الوضع التلقائي (الافتراضي للجلسات الجديدة)، و🔓 وضع الوصول الكامل، لكل جلسة على حدة، وتغيّره متى شئت عبر &lrm;`/mode`.

</td>
<td width="33%" valign="top">

**👥 مصمَّم للفرق**<br>
أضف البوت إلى مجموعة. كل رسالة تعمل بدور مرسلها، ويمكن حصر الأعضاء في مجلداتهم، وكل لمسة تُسجَّل في سجل التدقيق.

</td>
<td width="33%" valign="top">

**🔒 لا وسيط بينك وبينه**<br>
لا خادم لـ aipager، ولا حساب، ولا منفذ مفتوح. رمز البوت الخاص بك، وكمبيوترك، ودردشاتك.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>جولة سريعة

### <a id="questions-come-to-you"></a>الأسئلة تأتي إليك

عندما يسألك Claude عن شيء أو يحتاج إلى إذن، يصل السؤال إلى دردشتك مع زر لكل إجابة، ويخبرك الشريط المثبّت بما ينتظرك. وتُكتب لمستك في الجلسة كأنك جالس أمام لوحة المفاتيح، وتعرض الطرفية الإجابة نفسها.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="يسأل Claude هل يرفع مهلة الانتظار أم ينتظر ملف تعريف الارتباط؛ والإجابة التي لُمست في Telegram تُختار في الطرفية">

### <a id="three-permission-modes"></a>ثلاثة أوضاع للأذونات

كل جلسة في واحد من ثلاثة أوضاع، تغيّره متى شئت عبر &lrm;`/mode`، حتى أثناء عمل Claude.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="المهمة نفسها في وضع «اسأل» والوضع التلقائي ووضع الوصول الكامل">

| الوضع | ما الذي يحدث |
|---|---|
| 💬 **وضع «اسأل»**<br>&lrm;`/mode ask` | يسأل Claude قبل كل إجراء. |
| 🤖 **الوضع التلقائي**، الافتراضي<br>&lrm;`/mode auto` | فحص الأمان في Claude Code نفسه يشغّل الإجراءات الآمنة ويوقف الخطرة لتقرر أنت. |
| 🔓 **وضع الوصول الكامل**<br>&lrm;`/mode full` | كل شيء يعمل دون أي فحص. للمشرفين، ولك أنت في دردشتك الخاصة مع البوت. |

التبديل بين وضع «اسأل» والوضع التلقائي يتم في مكانه، دون إعادة تشغيل. الدخول إلى وضع الوصول الكامل أو الخروج منه يعيد تشغيل الجلسة ويحتفظ بالمحادثة. وتصلك أسئلة Claude في كل الأوضاع.

### <a id="your-team-in-one-group"></a>فريقك في مجموعة واحدة

أضف البوت إلى مجموعة وامنح كل عضو دورًا: `owner` أو `admin` أو `user` أو `read_only`. في المجموعة لا يستجيب البوت إلا للأوامر والردود على رسائله والإشارات إليه (&lrm;`@aipagerbot fix the tests`،&rlm; &lrm;`/web run the tests`)، وكل رسالة تعمل بدور مرسلها: الأداة التي لا يحق للدور استخدامها تُرفض قبل أن تعمل. العضو ذو الدور `user` أو `read_only` لا يعمل إلا داخل مجلده الجذر (افتراضيًا &lrm;`~/aipager-users/<label>`&lrm;). ويمكن لأشخاص آخرين أيضًا أن تكون لهم دردشتهم الخاصة مع البوت.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="مجموعة فريق يُرفض فيها أمر بسبب دور أحد الأعضاء بينما تنجح الاختبارات">

> **⚠️ مهم:** إضافة عضو تمنحه حق تشغيل الشيفرة على كمبيوترك. اقرأ [نموذج الثقة](groups.md) قبل أن تشارك البوت.

للإعداد، أضف البوت إلى المجموعة مشرفًا (ليتمكن من تثبيت شريط حالة المجموعة)، ثم شغّل `aipager config` مرة أخرى واختر **Add a group scope**. يضيف المعالج كل عضو بدور، ويسأل عن المجلد الجذر للأعضاء، ويعيد تحميل إعدادات aipager العامل.

### <a id="a-dashboard-inside-telegram"></a>لوحة تحكم داخل Telegram

الأمر &lrm;`/app` يفتح Mini App: جلساتك مع إيقاف وإنهاء وإعادة التشغيل وإعادة التسمية وتبديل الوضع، وعارض diff لكل `Write` و`Edit`، والإعدادات، ولصاحب البوت «الإبلاغ عن مشكلة». يقدّمها aipager نفسه، وهي مفعّلة افتراضيًا، ويُتحقق من كل طلب بتوقيع Telegram. وإن كنت في أكثر من دردشة، فصفّ أسماء الدردشات في الأعلى ينقلك بينها. أوقفها عبر `aipager miniapp disable`، أو وجّهها إلى عنوانك الخاص ([الطريقة](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>خمس عشرة لغة

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="left" alt="طلب إذن بالفارسية، من اليمين إلى اليسار، بأرقام فارسية">

- يتحدث English وEspañol وРусский وPortuguês (Brasil) و简体中文 وFrançais وDeutsch و한국어 والعربية وBahasa Indonesia وУкраїнська وItaliano وTürkçe وفارسی وTiếng Việt.
- اختر لغة عبر &lrm;`/language`، أو دع الدردشة الخاصة تتبع لغة تطبيق Telegram لديك.
- الفارسية والعربية تُقرآن من اليمين إلى اليسار، بأرقامهما الخاصة.
- لا يتغير إلا نص aipager نفسه. أما جلساتك فلا: يجيب Claude باللغة التي تكتب له بها.
- هذه الصفحة و[التوثيق](#documentation) مترجمان أيضًا.

<br clear="left">

### <a id="and-the-small-things"></a>والتفاصيل الصغيرة

- **جلسات كثيرة، وبوت واحد.** الرسالة العادية تذهب إلى الجلسة التي تحدثت معها آخر مرة؛ ولتكلّم جلسة أخرى، ردّ على إحدى رسائلها أو أرسل &lrm;`/x1 <message>`&lrm;.
- **لا شيء يختلط.** الرسالة التي ترسلها بينما طلب إذن أو سؤال مفتوح تنتظر حتى تجيب، فلا تُقرأ أبدًا على أنها الإجابة.
- **صور وملفات ورسائل صوتية.** تُحوَّل الرسائل الصوتية إلى نص على كمبيوترك، مع الإضافة الاختيارية `voice`.
- **يُعلمك.** حين يقترب السياق من الامتلاء، وحين تُضغط المحادثة، وحين تنتهي جلسة، وحين تصمت جلسة طويلًا.
- **الجلسات تصمد أمام كل شيء.** يعمل Claude داخل `dtach`، فيواصل عمله عندما تغلق الطرفية أو ينقطع SSH أو تعيد تشغيل aipager.

## <a id="how-it-works"></a>كيف يعمل

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code وهوكه وaipager وTelegram ودردشاتك، في صف واحد">

يعمل Claude Code في طرفية حقيقية داخل `dtach`، فلا يحتاج أحد إلى إبقاء نافذة مفتوحة. كل خطوة تطلق [هوك](hooks.md) يرسل حزمة بيانات واحدة إلى aipager عبر مقبس محلي، فيعدّل aipager رسالة في دردشتك. وتعود إجاباتك بالطريق نفسه: قرار للهوك الذي يحتجز الاستدعاء، أو ضغطات مفاتيح في الجلسة. تبقى الطرفية مصدر الحقيقة. المزيد في [البنية](architecture.md).

## <a id="install"></a>التثبيت

على Linux أو macOS، بأي معمارية. تحتاج إلى Claude Code مسجَّل الدخول على هذا الكمبيوتر، وإلى بوت Telegram: أنشئ واحدًا عبر &lrm;[@BotFather](https://t.me/BotFather) واضغط Start فيه.

<div dir="ltr">

```sh
curl -fsSL https://aipager.run/install | sh   # uv أو pipx أو Homebrew: أيّها لديك
aipager config                                # رمز البوت، ودردشتك، وهوكات Claude Code
aipager service install                       # يُبقي aipager يعمل بعد تسجيل خروجك
aipager session api                           # Claude Code في هذا المجلد، وفي دردشتك
```

</div>

يستخدم المثبّت `uv` إن كان لديك، وإلا `pipx`، وإلا (على macOS) Homebrew، ويثبّت `uv` إن لم يجد أيًّا منها. يأتي `dtach` مع aipager (الحزمة [`dtach-bin`](https://pypi.org/project/dtach-bin/))، فلا شيء آخر لتثبيته. والأمر `aipager config` معالج إعداد: يتحقق من رمز البوت ومن دردشتك، ثم يضيف هوكات aipager إلى &lrm;`~/.claude/settings.json` بدلًا منك. لن تعدّل أي ملف بيدك.

<details>
<summary><b>دع وكيل برمجة يثبّته لك</b></summary>

<br>

هل لديك وكيل برمجة (Claude Code أو أي وكيل لديه shell)؟ أنشئ البوت واضغط Start فيه، ثم احفظ رمز البوت في ملف لا يحتاج وكيلك إلى قراءته أبدًا (الصق الرمز، واضغط Enter، ثم Ctrl-D):

<div dir="ltr">

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

</div>

ثم أعطِ وكيلك هذه التعليمات:

<div dir="ltr">

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

</div>

إن كنت لا تعرف معرّف مستخدمك في Telegram، فقل ذلك بدلًا منه؛ فالوكيل يستطيع العثور عليه. الدليل الذي يتبعه، بما في ذلك طريقة تسليم الرمز بأمان، هو [التثبيت بواسطة وكيل](install-with-an-agent.md). وللسكربتات، يُعدّ الأمر `aipager setup --token-file FILE --chat-id ID` الإعداد دون أي سؤال ([أمر setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>طرق أخرى للتثبيت

<div dir="ltr">

```sh
uv tool install aipager                        # uv: يُنصح به على macOS، ويأتي بـ Python الخاص به
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew، على macOS وLinuxbrew (انظر الملاحظة أدناه)
nix profile install github:dev-aly3n/aipager   # Nix: لا يتضمن claude (انظر أدناه)
snap install aipager                           # Snap: عزل صارم، وclaude بداخله، ومجلدات العمل تحت ~/
```

</div>

أو شغّله في [Docker](#docker)، ومعه Python وNode و`claude` و`dtach` في الداخل.

<details>
<summary><b>ملاحظة عن Homebrew على macOS 26 (Tahoe)</b></summary>

<br>

تعمل الوصفة عندما تتوافق حزمة `python@3.12` من Homebrew مع Command Line Tools الخاصة بـ Xcode لديك، لكنهما تفترقان من حين لآخر. على macOS 26 يفشل التثبيت بخطأ في الرمز `pyexpat _XML_SetAllocTrackerActivationThreshold` ([المشكلة في المصدر](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). تحديث Xcode وCommand Line Tools يحل المشكلة عادة، لكن مع `uv tool install aipager` لا تظهر أصلًا. تأخذ الوصفة `dtach` من Homebrew وتثبّت aipager في بيئة Python يديرها Homebrew.

</details>

### <a id="docker"></a>Docker

صورة مكتفية بذاتها فيها Python وNode و`claude` و`dtach`، مناسبة لخادم VPS أو جهاز NAS أو Raspberry Pi حين لا تريد Python أو Node على المضيف. تدعم عدة معماريات (amd64،&rlm; arm64).

<div dir="ltr">

```sh
# 1. شغّل معالج الإعداد مرة واحدة (تفاعلي)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. شغّل aipager (في الخلفية، مع إعادة تشغيل تلقائية)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

</div>

اربط تحت &lrm;`/workspace` المجلدات التي تريد أن يعدّلها Claude. ربط &lrm;`~/.claude` ينقل بيانات دخولك إلى Claude وسجل محادثاتك: شغّل `claude` مرة واحدة على المضيف لتسجيل الدخول، أو `docker exec -it aipager claude` لتسجيل الدخول داخل الحاوية.

الوسوم: `latest`،&rlm; `1.3`،&rlm; `1.3.4` (واحد لكل إصدار بعينه وواحد لكل إصدار فرعي).

### <a id="nix"></a>Nix

<div dir="ltr">

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

</div>

يبني aipager من المصدر مع nixpkgs مثبّتة الإصدار. يوفّر Nix الأداة `dtach`، أما `claude` فلا، لذا ثبّته وحده (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`، أو اتبع توثيق Anthropic).

<details>
<summary><b>الإعداد في NixOS وHome Manager</b></summary>

<br>

أضف aipager مدخلًا في flake وضع حزمته في `environment.systemPackages`:

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

بعدها ينشئ الأمر `aipager service install` وحدة مستخدم في systemd.

</details>

## <a id="everyday-use"></a>الاستخدام اليومي

### <a id="in-telegram"></a>في Telegram

| أرسل | لكي |
|---|---|
| &lrm;`/status` | ترى جلسات هذه الدردشة، كل واحدة بحالتها وأزرارها. |
| &lrm;`/new x1` | تبدأ جلسة باسم x1. والأمر &lrm;`/new x1 fix the tests` يرسل أول رسالة أيضًا. |
| &lrm;`/x1` | تجعل x1 الجلسة التي تذهب إليها رسائلك. والأمر &lrm;`/x1 <message>`&lrm; يرسل إليها رسالة مباشرة. |
| &lrm;`/stop` | تقطع المهمة الجارية وتتخلص مما في قائمة الانتظار. |
| &lrm;`/mode` | تبدّل بين وضع «اسأل» والوضع التلقائي ووضع الوصول الكامل. |
| &lrm;`/app` | تفتح Mini App. |
| &lrm;`/language` | تختار اللغة التي يتحدث بها aipager في هذه الدردشة. |
| &lrm;`/update` | تحدّث aipager وClaude Code ثم تعيد التشغيل في مكانه (للمشرفين). |
| &lrm;`/help` | ترى دليلًا قصيرًا حسب المهمة. |

كل أمر وزر وإعداد موجود في [أوامر البوت](commands.md).

### <a id="on-the-command-line"></a>في سطر الأوامر

<div dir="ltr">

```sh
aipager start                             # aipager نفسه، في الواجهة
aipager session dev                       # ابدأ هنا جلسة باسم dev، أو اتصل بها
aipager session dev --continue            # أكمل آخر محادثة لـ Claude في هذا المجلد
aipager session dev -- --resume abc1234   # أي خيار آخر لـ claude يمرّ كما هو
aipager doctor                            # تحقّق من التثبيت
```

</div>

لتغادر جلسة دون إيقاف Claude، أغلق الطرفية أو لوح tmux: تبقى الجلسة تعمل، والأمر نفسه يعيد وصلك بها.

الأمر `aipager service install` يُبقي aipager يعمل في الخلفية: وحدة مستخدم في systemd على Linux (`~/.config/systemd/user/aipager.service`) أو وكيل launchd على macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). وفي الحالتين يعمل aipager في مجلدك الرئيسي، حيث تبدأ الجلسة الجديدة ما لم تختر مجلدًا آخر. وتديره الأوامر `aipager service start` و`stop` و`status` و`logs` و`uninstall`.

## <a id="security"></a>الأمان

- **لا خادم لـ aipager.** يتحدث aipager مع Bot API في Telegram برمز البوت الخاص بك. لا يوجد حساب في aipager ولا خدمة لـ aipager.
- **لا منفذ مفتوح.** لا يستمع aipager إلا على واجهة loopback. وتخرج Mini App عبر نفق Cloudflare يجب أن يحمل فيه كل طلب توقيع Telegram، والأمر `aipager miniapp disable` يوقفها.
- **كل إجابة مسجّلة.** كل «سماح» و«رفض» وكل إجابة تُضاف إلى &lrm;`~/.claude/aipager-audit.jsonl`، ومعها في المجموعة اسم من لمسها.
- **القرار ما زال لـ Claude Code.** إعدادات Claude Code لديك تحدد ما يجب السؤال عنه. أما aipager فينقل السؤال فقط ولا يرفع صلاحياته أبدًا: لا sudo، وكل شيء يعمل بمستخدمك أنت.

الصورة الكاملة، بما في ذلك ما يحمي بيانات دخولك إلى Claude، في [نموذج الأمان](security.md).

## <a id="faq"></a>أسئلة شائعة

<details>
<summary><b>ما الفرق بينه وبين تطبيقات Claude على الهاتف؟</b></summary>

<br>

تطبيقات Claude تتحدث مع المساعد الذي تستضيفه Anthropic. أما aipager فيوصلك بجلسة Claude Code تعمل على كمبيوترك أنت: مستودعك، وأدواتك، وبيئتك.

</details>

<details>
<summary><b>هل تغادر شيفرتي كمبيوتري؟</b></summary>

<br>

لا يرسل aipager إلى Telegram إلا ما تراه في دردشاتك، برمز البوت الخاص بك؛ ولا يوجد أي خادم لـ aipager في الوسط. أما Claude Code نفسه فيتحدث مع Anthropic كعادته.

</details>

<details>
<summary><b>ما هو dtach؟</b></summary>

<br>

برنامج صغير يفصل البرنامج عن الطرفية. يُبقي Claude Code حيًّا عندما تُغلق الطرفية أو ينقطع SSH، فتصل الإجابة التي ترسلها بعد ساعة إلى جلسة ما زالت حيّة. ويأتي مع aipager.

</details>

<details>
<summary><b>وماذا لو انحرف Claude عن المسار؟</b></summary>

<br>

الطلبات تنتظر لمستك، والأمر &lrm;`/stop` يقطع المهمة ويتخلص من كل ما في قائمة الانتظار، والأمر &lrm;`/kill` ينهي الجلسة، وأزرار المهمة المنتهية ترفض.

</details>

<details>
<summary><b>كيف أحدّثه؟</b></summary>

<br>

أرسل &lrm;`/update`: يتحقق من aipager وClaude Code ويثبّت الجديد، ثم يعيد «🔄 إعادة التشغيل الآن» تشغيل aipager، سواء كان يعمل خدمةً أم لا. وتبقى جلساتك تعمل طوال ذلك. وفي سطر الأوامر، يحدّث `aipager update` عبر المثبّت الذي ثبّت aipager.

</details>

<details>
<summary><b>أستخدم Bedrock أو Vertex. هل تعمل أزرار النموذج؟</b></summary>

<br>

قائمة **النموذج** في لوحة المفاتيح وMini App تعرضان الأسماء المستعارة لعائلات Claude Code (`sonnet` و`opus` و`haiku` و`fable` و`opusplan`) ونماذج ثابتة مثل `claude-opus-5-5` و`claude-sonnet-5-5`. في Anthropic API يشير الاسم المستعار إلى أحدث نموذج في عائلته. وفي Bedrock وVertex قد يشير الاسم نفسه إلى إصدار أقدم، بحسب ما يقدمه مزوّدك؛ أرسل &lrm;`/model <full-id>`&lrm; لتختار نموذجًا بعينه.

</details>

<details>
<summary><b>كم يكلّف؟</b></summary>

<br>

لا شيء. aipager مرخّص بترخيص MIT، والبوت ملكك، ويواصل Claude Code استخدام الخطة أو المفتاح الذي يستخدمه أصلًا.

</details>

## <a id="documentation"></a>التوثيق

| الصفحة | محتواها |
|---|---|
| [أوامر البوت](commands.md) | كل أمر وزر وإعداد وخيار في سطر الأوامر |
| [وضع الفريق والمجموعات](groups.md) | الأدوار، والمجلدات الجذرية، ومن يحق له لمس ماذا، ونموذج الثقة |
| [نموذج الأمان](security.md) | حدود الثقة، والأسرار، وسجل التدقيق، والشبكة |
| [أحداث الهوك](hooks.md) | ما يفعله كل هوك من هوكات Claude Code في aipager |
| [البنية](architecture.md) | أجزاء aipager وملفاته ومقابسه |
| [حل المشكلات](troubleshooting.md) | الأمر `aipager doctor` وحلول المشكلات الشائعة |
| [تقارير المشكلات](problem-reports.md) | ما يحتويه التقرير، ومتى يعرض aipager إرساله |
| [التثبيت بواسطة وكيل](install-with-an-agent.md) | الدليل الذي يتبعه وكيل البرمجة |

## <a id="contributing"></a>المساهمة

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

تأتي الأداة `dtach` مع الاعتمادية `dtach-bin`، فلا حاجة إلى شيء آخر. شغّل الاختبارات مع حد للذاكرة كما في الأعلى (على macOS دون `systemd-run`): وإلا فقد يستولي اختبار خرج عن السيطرة على ذاكرة الجهاز كلها. وطريقة إصدار النسخ مشروحة في [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>الترخيص

ترخيص MIT؛ النص الكامل في [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>

</div>
