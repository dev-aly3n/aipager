<div dir="rtl">

# aipager

<!-- i18n-source: README.md @ c91598e -->

> هذه الصفحة مترجمة عن الإنجليزية؛ إذا اختلفت النسختان، فالنسخة الإنجليزية هي الصحيحة. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

التحكم عن بُعد عبر Telegram في جلسات [Claude Code](https://claude.com/claude-code). يعمل Claude على كمبيوترك داخل طرفية منفصلة (`dtach`)، ويمكنك استخدامه من هاتفك: قراءة إجابات Claude، وإرسال الرسائل إليه، والرد على طلبات الإذن وأسئلته، والتنقل بين الجلسات، دون إبقاء اتصال SSH مفتوحًا.

&rlm;[aipager.run](https://aipager.run) · [التوثيق](#docs) · [سجل التغييرات](../../../CHANGELOG.md) · [مشكلات GitHub](https://github.com/dev-aly3n/aipager/issues)

**اقرأ بلغة أخرى:** [English](../../../README.md) ·&rlm; [Español](../es/README.md) ·&rlm; [Русский](../ru/README.md) ·&rlm; [Português (Brasil)](../pt-BR/README.md) ·&rlm; [简体中文](../zh-Hans/README.md) ·&rlm; [Français](../fr/README.md) ·&rlm; [Deutsch](../de/README.md) ·&rlm; [한국어](../ko/README.md) ·&rlm; [Bahasa Indonesia](../id/README.md) ·&rlm; [Українська](../uk/README.md) ·&rlm; [Italiano](../it/README.md) ·&rlm; [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>التثبيت

هل لديك وكيل برمجة (Claude Code، أو أي وكيل يستطيع استخدام الطرفية)؟ يكفي إنشاء بوت عبر &lrm;[@BotFather](https://t.me/BotFather)، والضغط على Start فيه، والتأكد من أن Claude Code على هذا الكمبيوتر مسجّل الدخول بحسابك. بعدها يُحفظ توكن البوت في ملف لا يحتاج الوكيل أبدًا إلى قراءته (لصق التوكن، ثم Enter، ثم Ctrl-D):

<div dir="ltr">

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

</div>

ثم تُعطى هذه الرسالة للوكيل:

<div dir="ltr">

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

</div>

إذا كان معرّفك في Telegram غير معروف لك، يكفي إخبار الوكيل بذلك؛ فهو يستطيع إيجاده.

الإرشادات التي يتبعها الوكيل، ومنها طريقة تسليمه التوكن بأمان، موجودة في [التثبيت بواسطة وكيل](install-with-an-agent.md). للتثبيت دون وكيل، يكفي متابعة القراءة.

يعمل على Linux أو macOS، وعلى أي معالج. يُثبَّت `dtach` تلقائيًا عبر الاعتمادية [`dtach-bin`](https://pypi.org/project/dtach-bin/): لا حاجة إلى حزمة نظام منفصلة.

### <a id="one-line-install-recommended"></a>التثبيت بسطر واحد (موصى به)

<div dir="ltr">

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

</div>

يستخدم `uv` إن وُجد، وإلا `pipx`، وإلا (على macOS فقط) Homebrew. وإذا لم يوجد أيٌّ منها، يثبّت `uv` أولًا (أداة إدارة برامج Python من Astral) ثم يثبّت aipager بها.

### &rlm;<a id="uv-recommended-on-macos"></a>uv (موصى به على macOS)

<div dir="ltr">

```sh
uv tool install aipager     # إن كان uv مثبّتًا
```

</div>

أو، لتثبيت uv أولًا:

<div dir="ltr">

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

</div>

يجلب uv مفسّر Python الخاص به، فيعمل على أي إصدار من macOS أو Linux مهما كانت حالة Python في النظام؛ وبهذا يُتجنَّب التعارض بين Python من Homebrew وXcode الموصوف في قسم Homebrew أدناه.

### <a id="pipx"></a>pipx

<div dir="ltr">

```sh
pipx install aipager
```

</div>

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS، Linuxbrew)

> **ملاحظة:** على macOS، الطريقة الموصى بها هي `uv tool install aipager`. تعمل وصفة brew عندما يتوافق `python@3.12` من Homebrew مع Xcode /&rlm; Command Line Tools لديك، لكنهما يختلفان أحيانًا؛ وآخر مرة كانت على **macOS Tahoe (26.x)**، حيث توقف التثبيت بخطأ الرمز `pyexpat _XML_SetAllocTrackerActivationThreshold` ([المشكلة في المصدر](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). تحديث Xcode وCommand Line Tools يحل المشكلة عادةً، لكن uv أبسط.

<div dir="ltr">

```sh
brew install dev-aly3n/tap/aipager
```

</div>

يجلب `dtach` من وصفة Homebrew الرسمية ويثبّت aipager في بيئة Python افتراضية يديرها Homebrew.

### <a id="docker"></a>Docker

صورة جاهزة فيها python وnode و`claude` و`dtach`؛ مناسبة لخادم VPS أو NAS أو Raspberry Pi لا يُراد فيه تثبيت Python أو Node على النظام. متعددة المعماريات (amd64،&rlm; arm64).

<div dir="ltr">

```sh
# 1. تشغيل معالج الإعداد مرة واحدة (تفاعلي)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. تشغيل aipager (في الخلفية، مع إعادة التشغيل التلقائية)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

</div>

تُربط (mount) في &lrm;`/workspace` المجلدات المطلوب أن يعدّلها Claude. ربط &lrm;`~/.claude` يجلب معه تسجيل دخول Claude وسجل محادثاتك؛ لتسجيل الدخول، يكفي تشغيل `claude` مرة واحدة على الجهاز، أو `docker exec -it aipager claude` لتسجيل الدخول داخل الحاوية.

الوسوم: `latest` و`1.3` و`1.3.4` (واحد للإصدار الدقيق وواحد للإصدار الفرعي).

### <a id="nix-flake"></a>Nix flake

<div dir="ltr">

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

</div>

يبني aipager من المصدر مع اعتماديات مثبّتة من nixpkgs. يوفّر Nix الأداة `dtach`؛ أما `claude` فلا: يلزم تثبيتها وحدها (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`، أو حسب توثيق Anthropic).

لإعداد NixOS أو Home Manager بأسلوب تصريحي، يُضاف aipager مدخلًا في الـ flake وتؤخذ حزمته في `environment.systemPackages`:

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

بعدها يُنشئ `aipager service install` خدمة مستخدم في systemd.

### <a id="snap"></a>Snap

<div dir="ltr">

```sh
snap install aipager
```

</div>

حزمة snap معزولة بإحكام فيها python وnode و`claude` و`dtach` وaipager. بسبب عزل snap، يجب أن تكون مجلدات العمل داخل `~/` (مثل &lrm;`~/projects/foo`). ملف التعريف موجود في [`packaging/snap/`](../../../packaging/snap/)&lrm;.

## <a id="configure"></a>الإعداد

<div dir="ltr">

```sh
aipager config
```

</div>

معالج إعداد تفاعلي: يطلب توكن بوت Telegram (من &lrm;[@BotFather](https://t.me/BotFather)) ومعرّف الدردشة، ويتحقق من كليهما، ثم يضيف بنفسه الهوكات اللازمة إلى &lrm;`~/.claude/settings.json`. لا حاجة أبدًا إلى تعديل أي ملف بيدك.

هل الإعداد لشخص آخر، من سكربت أو عبر وكيل برمجة؟ الأمر `aipager setup --token-file FILE --chat-id ID` يفعل الشيء نفسه دون أي سؤال (يُنظر [أمر setup](commands.md#command-line-aipager-setup-for-coding-agents) و[دليل الوكيل](install-with-an-agent.md)).

## <a id="run"></a>التشغيل

<div dir="ltr">

```sh
aipager start
```

</div>

يعمل aipager في الواجهة. لبدء جلسة Claude، في طرفية أخرى:

<div dir="ltr">

```sh
aipager session dev
```

</div>

ينشئ هذا جلسة dtach باسم `claude-dev` تشغّل Claude Code (أو يتصل بها إن كانت موجودة). يكتشفها aipager خلال ثوانٍ ويبدأ Telegram في عرضها. يمكنك أيضًا بدء جلسة من Telegram: إرسال &lrm;`/new dev` إلى البوت. للعودة إليها لاحقًا، يكفي تشغيل الأمر نفسه مرة أخرى؛ وللخروج دون إيقاف Claude، يكفي إغلاق الطرفية أو لوحة tmux (تستمر الجلسة في العمل).

إذا كانت جلسة dtach قد أُغلقت (أُعيد تشغيل الكمبيوتر مثلًا) والمطلوب متابعة محادثة Claude المحفوظة على القرص، يُضاف &lrm;`--continue`:

<div dir="ltr">

```sh
aipager session dev --continue    # آخر محادثة claude في هذا المجلد
```

</div>

ويمكن أيضًا إضافة &lrm;`--resume <session-id>`&lrm; (أو أي خيار آخر لـ claude) في النهاية:

<div dir="ltr">

```sh
aipager session dev -- --resume abc1234
```

</div>

### <a id="run-as-a-service-survives-logout"></a>التشغيل كخدمة (يستمر بعد تسجيل الخروج)

<div dir="ltr">

```sh
aipager service install
```

</div>

على Linux، يكتب هذا خدمة مستخدم systemd في &lrm;`~/.config/systemd/user/aipager.service` ثم يشغّلها. على macOS، يكتب ملف plist لـ launchd في &lrm;`~/Library/LaunchAgents/com.aipager.daemon.plist` ثم يشغّله. في الحالتين يعمل aipager في مجلدك الرئيسي، وهناك تبدأ الجلسة الجديدة ما لم تختر مجلدًا آخر. الأوامر الفرعية: `start` و`stop` و`status` و`logs` و`uninstall`.

## <a id="team-mode-groups-and-other-people"></a>وضع الفريق (المجموعات وأشخاص آخرون)

افتراضيًا، aipager بوت شخصي: أنت والبوت في دردشة خاصة واحدة. للمشاركة، يمكنك إضافة مجموعة Telegram، أو دردشات خاصة لأشخاص آخرين مع البوت، أو كليهما. لمجموعة: إضافة البوت إلى المجموعة (مشرفًا، ليتمكن من تثبيت شريط الحالة فيها)، ثم تشغيل `aipager config` مرة أخرى واختيار **Add a group scope**. يقترح المعالج حسابك (مالك دردشتك الخاصة) مالكًا للمجموعة، ويضيف كل عضو بدور (`owner` أو `admin` أو `user` أو `read_only`)، ويسأل عن المجلد الجذر للأعضاء، ثم يعيد تحميل إعدادات aipager دون إعادة تشغيل. في المجموعة لا يستجيب البوت إلا للأوامر، والردود على رسائله، والرسائل التي تذكره (&lrm;`@aipagerbot fix the tests`،&rlm; &lrm;`/jim run the tests`)، وكل رسالة تعمل بدور مرسلها. العضو بدور `user` أو `read_only` لا يبدأ جلساته ولا يحتفظ بها إلا داخل المجلد الجذر لدردشته (افتراضيًا &lrm;`~/aipager-users/<label>`&lrm;). **إضافة عضو تعني منحه القدرة على تشغيل شيفرة على كمبيوترك**: نموذج الثقة كاملًا موصوف في [وضع الفريق والمجموعات](groups.md).

## <a id="what-it-does"></a>ماذا يفعل

- يعرض لك في Telegram ما تفعله كل جلسة Claude Code: هل تعمل أم جاهزة، والأداة التي تستخدمها، ونسبة السياق، والتكلفة، والأسطر المتغيرة، في بطاقة واحدة لكل مهمة، ثم إجابة Claude
- يوصل رسائلك إلى Claude **فورًا**، حتى أثناء عمله: يمكنك إرسال عدة رسائل وسيضعها Claude في قائمة انتظاره، تمامًا كما في الطرفية. 👀 تعني أنها أُرسلت، و👍 تعني أن Claude أخذها
- يحتجز الرسائل ما دام هناك طلب إذن أو سؤال مفتوح، حتى لا تُقرأ أبدًا كإجابة عنه، ثم يرسلها بعد ردك
- يعرض طلبات الإذن وأسئلة Claude مع أزرار (سماح، رفض، سماح دائمًا، زر لكل خيار)؛ والزر المتبقي من مهمة انتهت يرفض بدل أن يؤثر في المهمة الجارية
- ثلاثة أوضاع للأذونات لكل جلسة، تتغير عبر &lrm;`/mode`: 💬 وضع «اسأل» (يسأل Claude قبل كل إجراء)، و🤖 الوضع التلقائي، وهو الافتراضي (فحص الأمان في Claude Code نفسه يشغّل الإجراءات الآمنة ويوقف الخطرة ليكون القرار لك)، و🔓 وضع الوصول الكامل (يعمل كل شيء دون أي فحص؛ للمشرفين، ولك في دردشتك الخاصة)
- يبدأ الجلسات من الدردشة: &lrm;`/new x1` يبدأ جلسة فورًا (و&lrm;`/new x1 fix the tests` يرسل أيضًا أول رسالة)، بالوضع والنموذج والمجلد المأخوذة من &lrm;`/settings` ومع أزرار لتغييرها
- يستقبل الصور والملفات والرسائل الصوتية (تُحوَّل الرسائل الصوتية إلى نص على كمبيوترك نفسه، مع ميزة الرسائل الصوتية)
- ينبّهك عندما يقترب السياق من الامتلاء، وعند تلخيص المحادثة، وعند انتهاء جلسة، وعندما تطول فترة صمت جلسة
- يشغّل عدة جلسات في الوقت نفسه عبر بوت واحد؛ ووضع فريق اختياري بأدوار وقواعد لكل أداة ([وضع الفريق والمجموعات](groups.md))
- يتحدث English وEspañol وРусский وPortuguês (Brasil) و简体中文 وFrançais وDeutsch و한국어 والعربية وBahasa Indonesia وУкраїнська وItaliano وTürkçe وفارسی وTiếng Việt: نصوص aipager وحدها، لا جلساتك ولا إجابات Claude أبدًا ([اللغات](commands.md#languages)). هذا الملف والتوثيق مترجمان أيضًا (انظر **اقرأ بلغة أخرى** في الأعلى)

### <a id="mini-app"></a>Mini App

يفتح &lrm;`/app` لوحة تحكم داخل Telegram: جلساتك مع الإيقاف والإنهاء وإعادة التشغيل وإعادة التسمية وتغيير الوضع، وعرض التغييرات (diff) لتعديلات `Write`/`Edit`، والإعدادات، ولمالك البوت «الإبلاغ عن مشكلة». يقدّمها aipager نفسه وهي **مفعّلة افتراضيًا**؛ ويُتحقق من كل طلب بتوقيع `initData` من Telegram. إذا كنت في أكثر من دردشة (دردشتك الخاصة ومجموعة)، يظهر صف بأسماء الدردشات في الأعلى للتنقل بينها. للإدارة: `aipager miniapp enable|disable|status`، أو يمكنك توجيهها إلى عنوانك الخاص بدل النفق المُدار: يُنظر [نموذج الأمان](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>ملاحظة عن أزرار النماذج (لمستخدمي Bedrock وVertex)

قائمة **النموذج** الفرعية في لوحة المفاتيح الدائمة (ومنتقي النموذج في Mini App) تعرض أسماء العائلات المستعارة في Claude Code،&rlm; `sonnet` و`opus` و`haiku` و`fable` و`opusplan`، ونماذج مثبّتة مثل `claude-opus-5-5` و`claude-sonnet-5-5` (القائمة كاملة في [لوحة المفاتيح الدائمة](commands.md#persistent-keyboard)). على واجهة Anthropic البرمجية يشير الاسم المستعار دائمًا إلى أحدث نموذج في عائلته. على **Bedrock** و**Vertex** قد تشير الأسماء نفسها إلى إصدارات أقدم، بحسب ما يوفره مزوّدك. لمن يستخدم هذه الخدمات ويحتاج إلى نموذج محدد: إرسال &lrm;`/model <full-id>`&lrm; من الدردشة.

## <a id="developing-locally"></a>التطوير على كمبيوترك

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

يأتي `dtach` مع الاعتمادية `dtach-bin`، فلا حاجة إلى شيء آخر. يُفضَّل تشغيل الاختبارات مع حد للذاكرة كما في الأعلى (على macOS دون `systemd-run`): وإلا فقد يستهلك اختبار خرج عن السيطرة كل ذاكرة الكمبيوتر.

طريقة إصدار النسخ موصوفة في [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>الرخصة

&rlm;MIT؛ النص الكامل في [LICENSE](../../../LICENSE).

## <a id="docs"></a>التوثيق

| الصفحة | محتواها |
|---|---|
| [التثبيت بواسطة وكيل](install-with-an-agent.md) | الخطوات التي يتبعها وكيل برمجة لتثبيت aipager وإعداده لصاحبه |
| [البنية](architecture.md) | كيف بُني البرنامج، ومخطط أجزائه، وأماكن الملفات والمقابس |
| [أحداث الهوك](hooks.md) | كل حدث يتلقاه aipager من Claude Code، مع حقوله |
| [أوامر البوت](commands.md) | الأوامر والأزرار وMini App والرسائل الصوتية والملفات، وكل أمر `aipager`، والإعدادات من البيئة |
| [وضع الفريق والمجموعات](groups.md) | الإعداد لعدة أشخاص، والأدوار، والقواعد، ومن فعل ماذا |
| [حل المشكلات](troubleshooting.md) | الأعطال الشائعة وجدول إصلاحات `aipager doctor` |
| [تقارير المشكلات](problem-reports.md) | ما يحتويه تقرير المشكلة، وإلى أين يذهب، ومتى يعرض aipager إرساله |
| [نموذج الأمان](security.md) | حدود الثقة، والأسرار، وسجل التدقيق، والاتصالات الشبكية |

</div>
