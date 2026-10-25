<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ bd8a636 -->

<h3>Claude тепер - просто ще один чат.</h3>

Твої сесії [Claude Code](https://claude.com/claude-code) у Telegram, поруч із родиною, друзями та командою.<br>
Вони працюють на твоєму власному комп'ютері. Ти стежиш за ними, відповідаєш на їхні питання і даєш наступне завдання, хоч де ти є.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Сайт](https://aipager.run)** · **[Встановлення](#install)** · **[Документація](#documentation)** · **[Безпека](security.md)** · **[Зміни](../../../CHANGELOG.md)**

**Читати іншою мовою:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Цю сторінку перекладено з англійської; якщо вони розходяться, правильною є англійська версія. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Повідомлення, набране в Telegram, доходить до Claude Code у терміналі, а картка в чаті оновлюється сама, поки Claude працює">

<sub>Ти пишеш у Telegram. aipager набирає це в Claude Code на твоєму комп'ютері, а одна картка оновлюється сама, поки триває завдання.</sub>

</div>

<br>

## <a id="why-aipager"></a>Навіщо aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Одна жива картка на завдання**<br>
Що Claude робить просто зараз: які інструменти запускає, контекст, вартість і змінені рядки, а потім відповідь. Картка оновлюється сама, тож у чаті не стає галасливо.

</td>
<td width="33%" valign="top">

**⚡ Пиши йому просто під час роботи**<br>
Твоє повідомлення одразу набирається в сесії, навіть поки Claude працює, точнісінько як під час набору в терміналі. 👀 означає, що його надіслано, 👍 - що Claude його забрав.

</td>
<td width="33%" valign="top">

**✅ Відповідай одним дотиком**<br>
Запити на дозвіл і питання Claude приходять із кнопкою на кожну відповідь. Кнопка, що лишилася від завершеного завдання, нічого не зробить із наступним.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Три режими дозволів**<br>
💬 Режим «Питати», 🤖 режим Авто (за замовчуванням для нових сесій) і 🔓 режим «Повний доступ»: задаються для кожної сесії й перемикаються будь-коли через `/mode`.

</td>
<td width="33%" valign="top">

**👥 Зроблено для команд**<br>
Додай бота в групу. Кожне повідомлення виконується з роллю того, хто його надіслав, учасників можна обмежити їхньою власною папкою, а кожне натискання потрапляє в журнал аудиту.

</td>
<td width="33%" valign="top">

**🔒 Нічого посередині**<br>
Ні сервера aipager, ні акаунта, ні відкритого порту. Твій токен бота, твій комп'ютер, твої чати.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Коротка екскурсія

### <a id="questions-come-to-you"></a>Питання приходять до тебе

Коли Claude про щось питає чи просить дозволу, питання приходить у твій чат із кнопкою на кожну відповідь, а закріплений рядок показує, що чекає на тебе. Твоє натискання набирається в сесії, ніби ти сидиш за клавіатурою, і термінал показує ту саму відповідь.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude питає, чи збільшити тайм-аут, чи дочекатися cookie; відповідь, натиснута в Telegram, обирається в терміналі">

### <a id="three-permission-modes"></a>Три режими дозволів

Кожна сесія працює в одному з трьох режимів, які перемикаються будь-коли через `/mode`, навіть поки Claude працює.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="Те саме завдання в режимах «Питати», Авто і «Повний доступ»">

| Режим | Що відбувається |
|---|---|
| 💬 **Режим «Питати»**<br>`/mode ask` | Claude питає перед кожною дією. |
| 🤖 **Режим Авто**, за замовчуванням<br>`/mode auto` | Власна перевірка безпеки Claude Code пропускає безпечні дії й зупиняє ризиковані, щоб вирішував ти. |
| 🔓 **Режим «Повний доступ»**<br>`/mode full` | Усе виконується без жодних перевірок. Для адмінів і для тебе у твоєму власному особистому чаті з ботом. |

Між режимами «Питати» і Авто перемикання відбувається на місці, без перезапуску. Вхід у режим «Повний доступ» чи вихід із нього перезапускає сесію й зберігає розмову. Питання Claude приходять до тебе в будь-якому режимі.

### <a id="your-team-in-one-group"></a>Твоя команда в одній групі

Додай бота в групу й дай кожному учасникові роль: `owner`, `admin`, `user` або `read_only`. У групі бот реагує лише на команди, відповіді на його повідомлення та згадки (`@aipagerbot fix the tests`, `/web run the tests`), і кожне повідомлення виконується з роллю відправника: інструмент, яким роль не може користуватися, відхиляється ще до запуску. Учасник із роллю `user` або `read_only` працює лише всередині своєї кореневої папки (за замовчуванням `~/aipager-users/<label>`). Інші люди теж можуть мати свій особистий чат із ботом.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Командна група, де команду відхиляють через роль учасника, а тести проходять">

> **⚠️ Важливо:** Додаючи учасника, ти даєш йому право запускати код на твоєму комп'ютері. Прочитай [модель довіри](groups.md), перш ніж ділитися ботом.

Щоб усе налаштувати, додай бота в групу як адміна (щоб він міг закріпити рядок стану групи), знову запусти `aipager config` і вибери **Add a group scope**. Майстер додасть кожного учасника з роллю, запитає кореневу папку учасників і перечитає налаштування запущеного aipager.

### <a id="a-dashboard-inside-telegram"></a>Панель просто в Telegram

`/app` відкриває Mini App: твої сесії з кнопками Зупинити, Завершити, Перезапустити, Перейменувати й перемиканням режиму, перегляд diff для кожного `Write` і `Edit`, налаштування та, для власника, «Повідомити про проблему». Її віддає сам aipager, вона ввімкнена за замовчуванням, і кожен запит перевіряється за підписом Telegram. Якщо ти в кількох чатах, рядок із їхніми назвами вгорі перемикає між ними. Вимкни її через `aipager miniapp disable` або спрямуй на свою адресу ([як](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>П'ятнадцять мов

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Запит на дозвіл перською, справа наліво, з перськими цифрами">

- Говорить English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی і Tiếng Việt.
- Вибери мову через `/language` або хай особистий чат бере мову з твого застосунку Telegram.
- Перська й арабська читаються справа наліво, зі своїми цифрами.
- Змінюються лише тексти самого aipager. Твої сесії - ні: Claude відповідає тією мовою, якою ти йому пишеш.
- Цей README і [документацію](#documentation) теж перекладено.

<br clear="right">

### <a id="and-the-small-things"></a>І дрібниці

- **Багато сесій, один бот.** Звичайне повідомлення йде в сесію, з якою була остання розмова; відповідай на повідомлення потрібної сесії, щоб писати саме їй, або надішли `/x1 <message>`.
- **Нічого не плутається.** Повідомлення, надіслане, поки відкритий запит на дозвіл чи питання, чекає на твою відповідь, тож його ніколи не сприймуть як відповідь.
- **Фото, файли й голосові.** Голосові перетворюються на текст на твоєму комп'ютері, з додатковим пакетом `voice`.
- **Він повідомить.** Майже повний контекст, стиснута розмова, сесія, що завершилася, і сесія, що давно мовчить.
- **Сесії переживуть усе.** Claude працює всередині `dtach`, тож продовжує роботу, коли ти закриваєш термінал, втрачаєш SSH чи перезапускаєш aipager.

## <a id="how-it-works"></a>Як це працює

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, його hook, aipager, Telegram і твої чати, в один ряд">

Claude Code працює в справжньому терміналі, всередині `dtach`, тож нікому не треба тримати вікно відкритим. Кожен крок запускає [hook](hooks.md), який надсилає одну датаграму в aipager через локальний сокет, а aipager редагує повідомлення у твоєму чаті. Твої відповіді повертаються тим самим шляхом: рішення для hook, який тримає виклик, або натискання клавіш у сесію. Джерелом істини лишається термінал. Детальніше в [архітектурі](architecture.md).

## <a id="install"></a>Встановлення

Linux або macOS, будь-яка архітектура. Потрібен Claude Code з виконаним входом на цьому комп'ютері та бот Telegram: створи його через [@BotFather](https://t.me/BotFather) і натисни в ньому Start.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx або Homebrew: що в тебе є
aipager config                                # токен бота, твій чат, hooks Claude Code
aipager service install                       # тримає aipager запущеним після виходу з системи
aipager session api                           # Claude Code у цій папці та у твоєму чаті
```

Інсталятор бере `uv`, якщо він є, інакше `pipx`, інакше (на macOS) Homebrew, а якщо немає нічого, ставить `uv`. `dtach` іде разом з aipager (пакет [`dtach-bin`](https://pypi.org/project/dtach-bin/)), тож більше нічого встановлювати не треба. `aipager config` - це майстер: він перевіряє токен бота й твій чат, а потім сам додає hooks aipager у `~/.claude/settings.json`. Тобі ніколи не доведеться правити файл вручну.

<details>
<summary><b>Хай встановлення зробить агент для програмування</b></summary>

<br>

Маєш Claude Code чи будь-який агент із доступом до shell? Створи бота, натисни в ньому Start і збережи токен бота у файл, який агентові ніколи не доведеться читати (встав токен, натисни Enter, потім Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Потім надішли агентові це повідомлення:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Якщо не знаєш свій ID користувача Telegram, так і скажи; агент зможе його знайти. Інструкція, за якою він працює, зокрема як безпечно передати токен, - це [Встановлення за допомогою агента](install-with-an-agent.md). Для скриптів `aipager setup --token-file FILE --chat-id ID` налаштовує aipager без жодного питання ([команда setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Інші способи встановлення

```sh
uv tool install aipager                        # uv: рекомендовано на macOS, приносить свій Python
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, на macOS і Linuxbrew (див. примітку нижче)
nix profile install github:dev-aly3n/aipager   # Nix: claude не входить (див. нижче)
snap install aipager                           # Snap: суворе ізолювання, claude всередині, робочі папки всередині ~/
```

Або запусти його в [Docker](#docker), з Python, Node, `claude` і `dtach` всередині.

<details>
<summary><b>Homebrew на macOS 26 (Tahoe)</b></summary>

<br>

Формула працює, коли bottle `python@3.12` з Homebrew і твої Command Line Tools від Xcode збігаються, але час від часу вони розходяться. На macOS 26 встановлення падає з помилкою символу `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue в upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Зазвичай допомагає оновити Xcode і Command Line Tools, але з `uv tool install aipager` цієї проблеми немає взагалі. Формула бере `dtach` із Homebrew і ставить aipager у середовище Python, яким керує Homebrew.

</details>

### <a id="docker"></a>Docker

Самодостатній образ із Python, Node, `claude` і `dtach`, добрий для VPS, NAS чи Raspberry Pi, де не хочеться ставити Python або Node на сам хост. Для кількох архітектур (amd64, arm64).

```sh
# 1. Один раз запусти майстер налаштування (інтерактивно)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Запусти aipager (у фоні, з автоматичним перезапуском)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Змонтуй у `/workspace` папки, які Claude має правити. Монтування `~/.claude` переносить твої облікові дані Claude та історію розмов: один раз запусти `claude` на хості, щоб увійти, або `docker exec -it aipager claude`, щоб увійти всередині контейнера.

Теги: `latest`, `1.3`, `1.3.10` (по одному на точну версію та на мінорну).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Збирає aipager з вихідного коду на зафіксованих nixpkgs. `dtach` дає Nix, а `claude` - ні, тож встанови його окремо (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code` або за документацією Anthropic).

<details>
<summary><b>NixOS і Home Manager</b></summary>

<br>

Додай aipager як вхід flake і поклади його пакет у `environment.systemPackages`:

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

Потім `aipager service install` створить користувацьку службу systemd.

</details>

## <a id="everyday-use"></a>Щодня

### <a id="in-telegram"></a>У Telegram

| Надішли | Щоб |
|---|---|
| `/status` | Побачити сесії цього чату, кожну з її станом і кнопками. |
| `/new x1` | Запустити сесію з назвою x1. `/new x1 fix the tests` заразом надішле її перше повідомлення. |
| `/x1` | Зробити x1 сесією, куди йдуть твої повідомлення. `/x1 <message>` надсилає їй повідомлення напряму. |
| `/stop` | Перервати поточне завдання й викинути те, що чекає в черзі. |
| `/mode` | Перемкнутися між режимами «Питати», Авто і «Повний доступ». |
| `/app` | Відкрити Mini App. |
| `/language` | Вибрати мову, якою aipager говорить у цьому чаті. |
| `/update` | Оновити aipager і Claude Code та перезапустити їх на місці (для адмінів). |
| `/help` | Коротка інструкція за завданнями. |

Усі команди, кнопки й налаштування є в [Командах бота](commands.md).

### <a id="on-the-command-line"></a>У командному рядку

```sh
aipager start                             # сам aipager, у поточному терміналі
aipager session dev                       # запустити тут сесію dev або під'єднатися до неї
aipager session dev --continue            # продовжити останню розмову Claude у цій папці
aipager session dev -- --resume abc1234   # будь-який інший прапорець claude передається як є
aipager doctor                            # перевірити встановлення
```

Щоб вийти із сесії, не зупиняючи Claude, закрий термінал або панель tmux: сесія працюватиме далі, а та сама команда знову під'єднає тебе.

`aipager service install` тримає aipager запущеним у фоні: як користувацька служба systemd на Linux (`~/.config/systemd/user/aipager.service`) або як агент launchd на macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). В обох випадках aipager працює у твоїй домашній папці, де й запускається нова сесія, якщо не вибрати іншу папку. Керувати ним можна через `aipager service start`, `stop`, `status`, `logs` і `uninstall`.

## <a id="security"></a>Безпека

- **Жодного сервера aipager.** aipager спілкується з Bot API Telegram через твій власний токен бота. Немає ні акаунта aipager, ні сервісу aipager.
- **Жодного відкритого порту.** aipager слухає лише loopback. Mini App виходить назовні через тунель Cloudflare, де кожен запит має нести підпис Telegram, а `aipager miniapp disable` вимикає його.
- **Кожна відповідь записана.** Кожне «Дозволити», «Відхилити» й кожна відповідь дописуються в `~/.claude/aipager-audit.jsonl`, а в групі разом із тим, хто натиснув.
- **Вирішує, як і раніше, Claude Code.** Що треба питати, визначають твої налаштування Claude Code. aipager лише передає питання й ніколи не підвищує права: жодного sudo, усе працює від твого користувача.

Повна картина, зокрема те, що захищає твої облікові дані Claude, - у [моделі безпеки](security.md).

## <a id="faq"></a>Часті питання

<details>
<summary><b>Чим це відрізняється від мобільних застосунків Claude?</b></summary>

<br>

Застосунки Claude спілкуються з асистентом, який працює на серверах Anthropic. aipager з'єднує тебе із сесією Claude Code на твоєму власному комп'ютері: твій репозиторій, твої інструменти, твоє середовище.

</details>

<details>
<summary><b>Мій код залишає мій комп'ютер?</b></summary>

<br>

aipager надсилає в Telegram лише те, що ти бачиш у своїх чатах, через твій власний токен бота; жодного сервера aipager посередині немає. Сам Claude Code спілкується з Anthropic, як і завжди.

</details>

<details>
<summary><b>Що таке dtach?</b></summary>

<br>

Крихітна програма, що від'єднує термінал. Вона не дає Claude Code завершитися, коли термінал закривається чи обривається SSH, тож відповідь, надіслана за годину, все одно дійде до живої сесії. Вона йде разом з aipager.

</details>

<details>
<summary><b>А якщо Claude понесе не туди?</b></summary>

<br>

Запити чекають на твоє натискання, `/stop` перериває завдання й викидає все з черги, `/kill` завершує сесію, а кнопки завершеного завдання не спрацьовують.

</details>

<details>
<summary><b>Як його оновити?</b></summary>

<br>

Надішли `/update`: він перевірить aipager і Claude Code та встановить те, що вийшло, а «🔄 Перезапустити зараз» знову запускає aipager, хоч як він працює: як служба чи ні. Твої сесії весь цей час працюють далі. У командному рядку `aipager update` оновлює через той інсталятор, яким встановлено aipager.

</details>

<details>
<summary><b>Я користуюся Bedrock або Vertex. Кнопки моделей працюють?</b></summary>

<br>

Меню **Модель** на клавіатурі й Mini App пропонують родинні аліаси Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) і закріплені моделі на кшталт `claude-opus-5-5` і `claude-sonnet-5-5`. В API Anthropic аліас вказує на найновішу модель родини. На Bedrock і Vertex той самий аліас може вказувати на старішу версію, залежно від того, що пропонує твій провайдер; надішли `/model <full-id>`, щоб вибрати точну модель.

</details>

<details>
<summary><b>У мене API-ключ, шлюз або інший провайдер. aipager працюватиме?</b></summary>

<br>

Так. aipager запускає Claude Code так, як він у тебе налаштований, і відповідає на запитання, які Claude Code ставить на старті (тека, API-ключ, екрани першого запуску), щоб сесія, запущена з Telegram, на них не зупинялася. Налаштування поклади в блок `env` файлу `~/.claude/settings.json` або в `~/.config/aipager/daemon.env` (один рядок `KEY=value` на налаштування, без лапок). Кнопки Opus, Sonnet і Haiku йдуть за `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL` і `ANTHROPIC_DEFAULT_HAIKU_MODEL`, а будь-яку іншу модель можна ввести за назвою. Anthropic підтримує Claude Code лише з моделями Claude. Докладніше в [Розв'язанні проблем](troubleshooting.md#using-an-api-key-a-gateway-or-another-provider).

</details>

<details>
<summary><b>Скільки це коштує?</b></summary>

<br>

Нічого. aipager поширюється під ліцензією MIT, бот твій, а Claude Code далі користується тим тарифом чи ключем, що й раніше.

</details>

## <a id="documentation"></a>Документація

| Сторінка | Про що вона |
|---|---|
| [Команди бота](commands.md) | Кожна команда, кнопка, налаштування й параметр командного рядка |
| [Робота в команді та групи](groups.md) | Ролі, кореневі папки, хто що може натискати, модель довіри |
| [Модель безпеки](security.md) | Межа довіри, секрети, журнал аудиту, мережа |
| [Події hooks](hooks.md) | Що робить в aipager кожен hook Claude Code |
| [Архітектура](architecture.md) | Частини aipager, його файли й сокети |
| [Розв'язання проблем](troubleshooting.md) | `aipager doctor` і розв'язання частих проблем |
| [Звіти про проблеми](problem-reports.md) | Що містить звіт і коли aipager пропонує його надіслати |
| [Встановлення за допомогою агента](install-with-an-agent.md) | Інструкція, за якою працює агент для програмування |

## <a id="contributing"></a>Участь у розробці

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` приходить із залежністю `dtach-bin`, більше нічого не треба. Запускай тести з обмеженням пам'яті, як вище (на macOS без `systemd-run`): інакше тест, що вийшов з-під контролю, може зайняти всю пам'ять комп'ютера. Як випускаються версії, описано в [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Ліцензія

MIT. Див. [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
