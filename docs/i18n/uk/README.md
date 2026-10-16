# aipager

<!-- i18n-source: README.md @ c91598e -->

> Цю сторінку перекладено з англійської; якщо вони розходяться, правильною є англійська версія. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Пульт керування сесіями [Claude Code](https://claude.com/claude-code) через Telegram. Claude працює на твоєму комп'ютері у від'єднаному терміналі (`dtach`), а ти користуєшся ним з телефона: читаєш відповіді Claude, пишеш йому повідомлення, відповідаєш на його запити на дозвіл і питання та перемикаєшся між сесіями, не тримаючи відкритим SSH-з'єднання.

[aipager.run](https://aipager.run) · [Документація](#docs) · [Історія змін](../../../CHANGELOG.md) · [Issues на GitHub](https://github.com/dev-aly3n/aipager/issues)

**Читати іншою мовою:** [English](../../../README.md) · [Español](../es/README.md) · [Русский](../ru/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Встановлення

Маєш агента для програмування (Claude Code або будь-якого агента з shell)? Створи бота через [@BotFather](https://t.me/BotFather), натисни в ньому Start і перевір, що Claude Code на цьому комп'ютері увійшов у твій акаунт. Збережи токен бота у файл, який агентові ніколи не доведеться читати (встав токен, натисни Enter, потім Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Потім дай агентові таке повідомлення:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Якщо не знаєш свого Telegram ID, так і скажи агентові; він зможе його знайти.

Інструкція, за якою працює агент, зокрема те, як безпечно передати йому токен, це [Встановлення за допомогою агента](install-with-an-agent.md). Щоб встановити самостійно, читай далі.

Linux або macOS, будь-яка архітектура. `dtach` встановлюється автоматично через залежність [`dtach-bin`](https://pypi.org/project/dtach-bin/): окремий системний пакет не потрібен.

### <a id="one-line-install-recommended"></a>Встановлення одним рядком (рекомендовано)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Він використовує `uv`, якщо той є, інакше `pipx`, інакше (лише на macOS) Homebrew. Якщо нічого з цього немає, він спершу встановлює `uv` (менеджер інструментів Python від Astral) і встановлює aipager через нього.

### <a id="uv-recommended-on-macos"></a>uv (рекомендовано на macOS)

```sh
uv tool install aipager     # якщо uv уже встановлено
```

Або, щоб спершу встановити uv:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv приносить власний інтерпретатор Python, тому це працює на будь-якій версії macOS чи Linux, хоч би що відбувалося із системним Python; так ти оминаєш конфлікт між Python із Homebrew і Xcode, описаний нижче в розділі про Homebrew.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Примітка:** на macOS рекомендований шлях це `uv tool install aipager`. Формула brew працює, коли пакет `python@3.12` з Homebrew і твої Xcode / Command Line Tools узгоджені, але час від часу вони розходяться; востаннє так було на **macOS Tahoe (26.x)**, де встановлення падає з помилкою символу `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue в upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Оновлення Xcode і Command Line Tools зазвичай допомагає, але простіше використати uv.

```sh
brew install dev-aly3n/tap/aipager
```

Бере `dtach` зі стандартної формули Homebrew і встановлює aipager у Python venv, яким керує Homebrew.

### <a id="docker"></a>Docker

Готовий образ, у якому вже є python, node, `claude` і `dtach`; зручно для VPS, NAS чи Raspberry Pi, де не хочеться встановлювати Python або Node в систему. Для кількох архітектур (amd64, arm64).

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

Підключи в `/workspace` папки, які має редагувати Claude. Підключення `~/.claude` переносить твої облікові дані Claude та історію розмов; щоб увійти, один раз запусти `claude` у системі або `docker exec -it aipager claude`, щоб увійти всередині контейнера.

Теги: `latest`, `1.3`, `1.3.4` (по одному на точну версію і на мінорну).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Збирає aipager з вихідного коду із зафіксованими залежностями з nixpkgs. `dtach` дає Nix; `claude` **ні**: встанови його окремо (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code` або за документацією Anthropic).

Для декларативної конфігурації NixOS чи Home Manager додай aipager як вхід flake і візьми його пакет у `environment.systemPackages`:

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

Після цього `aipager service install` налаштує користувацьку службу systemd.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Snap із суворою ізоляцією, у якому є python, node, `claude`, `dtach` і aipager. Через пісочницю snap робочі папки мають лежати всередині `~/` (наприклад, `~/projects/foo`). Маніфест лежить у [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Налаштування

```sh
aipager config
```

Інтерактивний майстер: питає токен твого Telegram-бота (від [@BotFather](https://t.me/BotFather)) та ID чату, перевіряє їх і сам додає потрібні hooks у `~/.claude/settings.json`. Тобі ніколи не доведеться редагувати файли вручну.

Налаштовуєш aipager для когось зі скрипта або через агента для програмування? `aipager setup --token-file FILE --chat-id ID` робить те саме без запитань (див. [команду setup](commands.md#command-line-aipager-setup-for-coding-agents) та [інструкцію для агентів](install-with-an-agent.md)).

## <a id="run"></a>Запуск

```sh
aipager start
```

aipager лишається на передньому плані. Запусти сесію Claude в іншому терміналі:

```sh
aipager session dev
```

Це створює сесію dtach з назвою `claude-dev`, у якій працює Claude Code (або під'єднується до неї, якщо вона вже є). aipager знаходить її за кілька секунд, і Telegram починає її показувати. Сесію можна запустити й з Telegram: надішли ботові `/new dev`. Щоб потім знову під'єднатися, запусти ту саму команду; щоб піти, не зупиняючи Claude, закрий термінал або панель tmux (сесія працюватиме далі).

Якщо сесію dtach закрито (наприклад, комп'ютер перезавантажився), а розмову Claude хочеться продовжити з диска, додай `--continue`:

```sh
aipager session dev --continue    # остання розмова claude у цій папці
```

Можна передати в кінці й `--resume <session-id>` (або будь-який інший прапорець claude):

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Запуск як служба (працює й після виходу із системи)

```sh
aipager service install
```

На Linux це записує користувацьку службу systemd у `~/.config/systemd/user/aipager.service` і запускає її. На macOS це записує plist для launchd у `~/Library/LaunchAgents/com.aipager.daemon.plist` і запускає його. В обох випадках aipager працює у твоїй домашній папці, і нова сесія запускається там, якщо ти не вибереш іншу папку. Підкоманди: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Робота в команді (групи та інші люди)

За замовчуванням aipager це особистий бот: ти і бот в одному особистому чаті. Щоб поділитися ним, додай групу Telegram, особисті чати інших людей з ботом або і те, і те. Для групи: додай бота в групу (адміном, щоб він міг закріпити рядок стану групи), знову запусти `aipager config` і вибери **Add a group scope**. Майстер пропонує тебе (власника твого особистого чату) власником групи, додає кожного учасника з роллю (`owner`, `admin`, `user` або `read_only`), питає кореневу папку учасників і перезавантажує налаштування aipager без перезапуску. У групі бот реагує лише на команди, відповіді на свої повідомлення та згадки (`@aipagerbot fix the tests`, `/jim run the tests`), і кожне повідомлення виконується з роллю того, хто його надіслав. Учасник з `user` або `read_only` запускає і тримає сесії лише в кореневій папці свого чату (за замовчуванням `~/aipager-users/<label>`). **Додати учасника означає дати йому право запускати код на твоєму комп'ютері**: повну модель довіри описано в розділі [Робота в команді та групи](groups.md).

## <a id="what-it-does"></a>Що він уміє

- Показує в Telegram, що робить кожна сесія Claude Code: працює вона чи готова, які інструменти використовує, % контексту, вартість і змінені рядки, на одній картці на завдання, а потім відповідь Claude
- Надсилає твої повідомлення Claude **одразу**, навіть поки він працює: надішли кілька, і Claude сам поставить їх у чергу, як під час набору в терміналі. 👀 означає надіслано, 👍 означає, що Claude його забрав
- Притримує повідомлення, поки відкритий запит на дозвіл або питання, щоб його ніколи не прочитали як відповідь на нього, і надсилає його, коли буде відповідь
- Показує запити на дозвіл і питання Claude з кнопками (Дозволити, Відхилити, Дозволяти завжди, по одній на варіант); кнопка, що лишилася від уже завершеного завдання, відмовляє, а не діє на поточну роботу
- Три режими дозволів на сесію, перемикаються через `/mode`: 💬 режим «Питати» (Claude питає перед кожною дією), 🤖 режим Авто, за замовчуванням (власна перевірка безпеки Claude Code пропускає безпечні дії і зупиняє ризиковані, щоб рішення було за тобою) і 🔓 режим «Повний доступ» (усе виконується без жодних перевірок; для адмінів і для тебе у твоєму власному особистому чаті)
- Запускає сесії з чату: `/new x1` одразу запускає одну (`/new x1 fix the tests` ще й надсилає перше повідомлення), з режимом, моделлю та папкою з `/settings` і кнопками, щоб їх змінити
- Приймає фото, файли та голосові повідомлення (голосові перетворюються на текст на твоєму комп'ютері, з додатковим пакетом для голосових)
- Повідомляє, коли контекст майже заповнений, коли розмову стиснуто, коли сесія завершилася і коли якась довго мовчить
- Тримає кілька сесій одночасно через одного бота; за бажанням робота в команді з ролями та правилами для кожного інструмента ([Робота в команді та групи](groups.md))
- Говорить English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی і Tiếng Việt: лише тексти самого aipager, ніколи твої сесії чи відповіді Claude ([Мови](commands.md#languages)). Цей README і документацію теж перекладено (див. **Читати іншою мовою** вище)

### <a id="mini-app"></a>Mini App

`/app` відкриває панель усередині Telegram: твої сесії із Зупинити, Завершити, Перезапустити, Перейменувати та зміною режиму, перегляд diff для змін `Write`/`Edit`, налаштування і, для власника, «Повідомити про проблему». Її віддає сам aipager, і вона **увімкнена за замовчуванням**; кожен запит перевіряється за підписом `initData` від Telegram. Якщо ти є в кількох чатах (твій особистий чат і група), рядок із назвами чатів угорі перемикає між ними. Керуй нею через `aipager miniapp enable|disable|status` або спрямуй її на свою адресу замість керованого тунелю: див. [Модель безпеки](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Про кнопки моделей (для Bedrock і Vertex)

Підменю **Модель** на постійній клавіатурі (і вибір моделі в Mini App) пропонує родинні аліаси Claude Code, `sonnet`, `opus`, `haiku`, `fable` і `opusplan`, та закріплені моделі на кшталт `claude-opus-5-5` і `claude-sonnet-5-5` (повний список у розділі [Постійна клавіатура](commands.md#persistent-keyboard)). В API Anthropic аліас завжди вказує на найновішу модель своєї родини. На **Bedrock** і **Vertex** ті самі аліаси можуть вказувати на старіші версії, залежно від того, що є у твого провайдера. Якщо ти працюєш через ці сервіси і тобі потрібна конкретна модель, надішли з чату `/model <full-id>`.

## <a id="developing-locally"></a>Розробка на своєму комп'ютері

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` приходить із залежністю `dtach-bin`, більше нічого не потрібно. Запускай тести з обмеженням пам'яті, як вище (на macOS без `systemd-run`): інакше тест, що вийшов з-під контролю, може забрати всю пам'ять комп'ютера.

Порядок випуску версій описано в [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Ліцензія

MIT; повний текст у [LICENSE](../../../LICENSE).

## <a id="docs"></a>Документація

| Сторінка | Про що |
|---|---|
| [Встановлення за допомогою агента](install-with-an-agent.md) | Кроки, за якими агент для програмування встановлює і налаштовує aipager для своєї людини |
| [Архітектура](architecture.md) | Як улаштована програма, схема частин, де лежать файли та сокети |
| [Події hooks](hooks.md) | Кожна подія, яку aipager отримує від Claude Code, з полями |
| [Команди бота](commands.md) | Команди, кнопки, Mini App, голосові та файли, усі команди `aipager` і налаштування з оточення |
| [Робота в команді та групи](groups.md) | Налаштування для кількох людей, ролі, правила, хто що зробив |
| [Розв'язання проблем](troubleshooting.md) | Часті збої та таблиця рішень `aipager doctor` |
| [Звіти про проблеми](problem-reports.md) | Що містить звіт про проблему, куди він іде і коли aipager його пропонує |
| [Модель безпеки](security.md) | Межа довіри, секрети, журнал аудиту, мережеві з'єднання |
