<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<!-- i18n-source: README.md @ ec7afc2 -->

<h3>Claude теперь просто ещё один чат.</h3>

Твои сессии [Claude Code](https://claude.com/claude-code) в Telegram, рядом с семьёй, друзьями и командой.<br>
Они работают на твоём собственном компьютере. Ты следишь за ними, отвечаешь на их вопросы и даёшь следующую задачу, где бы ты ни был.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="../../../LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Сайт](https://aipager.run)** · **[Установка](#install)** · **[Документация](#documentation)** · **[Безопасность](security.md)** · **[Изменения](../../../CHANGELOG.md)**

**Читать на другом языке:** [English](../../../README.md) · [Español](../es/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

<sub>Эта страница переведена с английской; если они расходятся, верна английская версия. ([English](../../../README.md))</sub>

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="Сообщение, набранное в Telegram, доходит до Claude Code в терминале, а карточка в чате обновляется сама, пока Claude работает">

<sub>Ты пишешь в Telegram. aipager набирает это в Claude Code на твоём компьютере, а одна карточка обновляется сама, пока идёт задача.</sub>

</div>

<br>

## <a id="why-aipager"></a>Зачем aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 Одна живая карточка на задачу**<br>
Что Claude делает прямо сейчас: какие инструменты запускает, контекст, стоимость и изменённые строки, а потом ответ. Карточка обновляется сама, и в чате не становится шумно.

</td>
<td width="33%" valign="top">

**⚡ Пиши ему прямо во время работы**<br>
Твоё сообщение сразу набирается в сессии, даже пока Claude работает, точно как при наборе в терминале. 👀 значит отправлено, 👍 значит Claude его забрал.

</td>
<td width="33%" valign="top">

**✅ Отвечай одним касанием**<br>
Запросы разрешения и вопросы Claude приходят с кнопкой на каждый ответ. Кнопка, оставшаяся от законченной задачи, отказывает, а не действует на следующую.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Три режима разрешений**<br>
💬 Режим «Спрашивать», 🤖 режим Авто (по умолчанию для новых сессий) и 🔓 режим «Полный доступ»: задаются для каждой сессии и переключаются в любой момент через `/mode`.

</td>
<td width="33%" valign="top">

**👥 Сделано для команд**<br>
Добавь бота в группу. Каждое сообщение выполняется с ролью того, кто его отправил, участников можно ограничить их собственной папкой, а каждое нажатие попадает в журнал аудита.

</td>
<td width="33%" valign="top">

**🔒 Ничего посередине**<br>
Ни сервера aipager, ни аккаунта, ни открытого порта. Твой токен бота, твой компьютер, твои чаты.

</td>
</tr>
</table>

## <a id="a-quick-tour"></a>Короткая экскурсия

### <a id="questions-come-to-you"></a>Вопросы приходят к тебе

Когда Claude о чём-то спрашивает или просит разрешения, вопрос приходит в твой чат с кнопкой на каждый ответ, а закреплённая строка показывает, что ждёт тебя. Твоё нажатие набирается в сессии, будто ты сидишь за клавиатурой, и терминал показывает тот же ответ.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude спрашивает, увеличить таймаут или подождать cookie; ответ, нажатый в Telegram, выбирается в терминале">

### <a id="three-permission-modes"></a>Три режима разрешений

Каждая сессия работает в одном из трёх режимов, которые переключаются в любой момент через `/mode`, даже пока Claude работает.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="Одна и та же задача в режимах «Спрашивать», Авто и «Полный доступ»">

| Режим | Что происходит |
|---|---|
| 💬 **Режим «Спрашивать»**<br>`/mode ask` | Claude спрашивает перед каждым действием. |
| 🤖 **Режим Авто**, по умолчанию<br>`/mode auto` | Собственная проверка безопасности Claude Code пропускает безопасные действия и останавливает рискованные, чтобы решал ты. |
| 🔓 **Режим «Полный доступ»**<br>`/mode full` | Всё выполняется без каких-либо проверок. Для админов и для тебя в твоём собственном личном чате с ботом. |

Между режимами «Спрашивать» и Авто переключение происходит на месте, без перезапуска. Вход в режим «Полный доступ» или выход из него перезапускает сессию и сохраняет разговор. Вопросы Claude приходят к тебе в любом режиме.

### <a id="your-team-in-one-group"></a>Твоя команда в одной группе

Добавь бота в группу и дай каждому участнику роль: `owner`, `admin`, `user` или `read_only`. В группе бот реагирует только на команды, ответы на его сообщения и упоминания (`@aipagerbot fix the tests`, `/web run the tests`), и каждое сообщение выполняется с ролью отправителя: инструмент, который роли нельзя использовать, отклоняется до запуска. Участник с ролью `user` или `read_only` работает только внутри своей корневой папки (по умолчанию `~/aipager-users/<label>`). У других людей тоже может быть свой личный чат с ботом.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="Командная группа, где команду отклоняют из-за роли участника, а тесты проходят">

> **⚠️ Важно:** Добавляя участника, ты даёшь ему право запускать код на твоём компьютере. Прочитай [модель доверия](groups.md), прежде чем делиться ботом.

Чтобы всё настроить, добавь бота в группу как админа (чтобы он мог закрепить строку состояния группы), снова запусти `aipager config` и выбери **Add a group scope**. Мастер добавит каждого участника с ролью, спросит корневую папку участников и перечитает настройки работающего aipager.

### <a id="a-dashboard-inside-telegram"></a>Панель прямо в Telegram

`/app` открывает Mini App: твои сессии с кнопками Остановить, Завершить, Перезапустить, Переименовать и сменой режима, просмотр diff для каждого `Write` и `Edit`, настройки и, для владельца, «Сообщить о проблеме». Её отдаёт сам aipager, она включена по умолчанию, и каждый запрос проверяется по подписи Telegram. Если ты в нескольких чатах, строка с их названиями вверху переключает между ними. Выключи её через `aipager miniapp disable` или направь на свой адрес ([как](security.md#mini-app-tunnel)).

### <a id="fifteen-languages"></a>Пятнадцать языков

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="Запрос разрешения на персидском, справа налево, с персидскими цифрами">

- Говорит на English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی и Tiếng Việt.
- Выбери язык через `/language` или позволь личному чату следовать языку твоего приложения Telegram.
- Персидский и арабский читаются справа налево, со своими цифрами.
- Меняются только тексты самого aipager. Твои сессии нет: Claude отвечает на том языке, на котором ты ему пишешь.
- Этот README и [документация](#documentation) тоже переведены.

<br clear="right">

### <a id="and-the-small-things"></a>И мелочи

- **Много сессий, один бот.** Обычное сообщение уходит в сессию, с которой ты говорил последней; ответь на сообщение нужной сессии, чтобы писать именно ей, или отправь `/x1 <message>`.
- **Ничего не путается.** Сообщение, отправленное, пока открыт запрос разрешения или вопрос, ждёт твоего ответа, так что его никогда не примут за ответ.
- **Фото, файлы и голосовые.** Голосовые превращаются в текст на твоём компьютере, с дополнительным пакетом `voice`.
- **Он сообщит.** Почти полный контекст, сжатый разговор, сессия, которая завершилась, и сессия, которая давно молчит.
- **Сессии переживут всё.** Claude работает внутри `dtach`, поэтому продолжает работу, когда ты закрываешь терминал, теряешь SSH или перезапускаешь aipager.

## <a id="how-it-works"></a>Как это работает

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, его hook, aipager, Telegram и твои чаты, в один ряд">

Claude Code работает в настоящем терминале, внутри `dtach`, так что никому не нужно держать окно открытым. Каждый шаг запускает [hook](hooks.md), который отправляет одну датаграмму в aipager через локальный сокет, а aipager правит сообщение в твоём чате. Твои ответы возвращаются тем же путём: решение для hook, который держит вызов, или нажатия клавиш в сессию. Источником истины остаётся терминал. Подробнее в [архитектуре](architecture.md).

## <a id="install"></a>Установка

Linux или macOS, любая архитектура. Нужен Claude Code со входом в аккаунт на этом компьютере и бот Telegram: создай его через [@BotFather](https://t.me/BotFather) и нажми в нём Start.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx или Homebrew: что у тебя есть
aipager config                                # токен бота, твой чат, hooks Claude Code
aipager service install                       # держит aipager запущенным после выхода из системы
aipager session api                           # Claude Code в этой папке и в твоём чате
```

Установщик берёт `uv`, если он есть, иначе `pipx`, иначе (на macOS) Homebrew, а если нет ничего, ставит `uv`. `dtach` идёт вместе с aipager (пакет [`dtach-bin`](https://pypi.org/project/dtach-bin/)), так что больше ничего ставить не нужно. `aipager config` это мастер: он проверяет токен бота и твой чат, а потом сам добавляет hooks aipager в `~/.claude/settings.json`. Тебе никогда не придётся править файл вручную.

<details>
<summary><b>Пусть установку сделает агент для программирования</b></summary>

<br>

Есть Claude Code или любой агент с доступом к shell? Создай бота, нажми в нём Start и сохрани токен бота в файл, который агенту никогда не придётся читать (вставь токен, нажми Enter, потом Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Потом дай агенту этот промпт:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Если не знаешь свой ID пользователя Telegram, так и скажи; агент сможет его найти. Инструкция, по которой он работает, включая то, как безопасно передать токен, это [Установка с помощью агента](install-with-an-agent.md). Для скриптов `aipager setup --token-file FILE --chat-id ID` настраивает aipager без единого вопроса ([команда setup](commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### <a id="other-ways-to-install"></a>Другие способы установки

```sh
uv tool install aipager                        # uv: рекомендуется на macOS, приносит свой Python
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, на macOS и Linuxbrew (см. заметку ниже)
nix profile install github:dev-aly3n/aipager   # Nix: claude не входит (см. ниже)
snap install aipager                           # Snap: строгая изоляция, claude внутри, рабочие папки внутри ~/
```

Или запусти его в [Docker](#docker), с Python, Node, `claude` и `dtach` внутри.

<details>
<summary><b>Homebrew на macOS 26 (Tahoe)</b></summary>

<br>

Формула работает, когда bottle `python@3.12` из Homebrew и твои Command Line Tools от Xcode совпадают, но время от времени они расходятся. На macOS 26 установка падает с ошибкой символа `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue в upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Обычно помогает обновить Xcode и Command Line Tools, но с `uv tool install aipager` этой проблемы нет вовсе. Формула берёт `dtach` из Homebrew и ставит aipager в окружение Python, которым управляет Homebrew.

</details>

### <a id="docker"></a>Docker

Самодостаточный образ с Python, Node, `claude` и `dtach`, хорош для VPS, NAS или Raspberry Pi, где не хочется ставить Python или Node на сам хост. Для нескольких архитектур (amd64, arm64).

```sh
# 1. Один раз запусти мастер настройки (интерактивно)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Запусти aipager (в фоне, с автоматическим перезапуском)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Смонтируй в `/workspace` папки, которые Claude должен править. Монтирование `~/.claude` переносит твои учётные данные Claude и историю разговоров: один раз запусти `claude` на хосте, чтобы войти, или `docker exec -it aipager claude`, чтобы войти внутри контейнера.

Теги: `latest`, `1.3`, `1.3.4` (по одному на точную версию и на минорную).

### <a id="nix"></a>Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Собирает aipager из исходников на зафиксированных nixpkgs. `dtach` даёт Nix, а `claude` нет, так что поставь его отдельно (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code` или по документации Anthropic).

<details>
<summary><b>NixOS и Home Manager</b></summary>

<br>

Добавь aipager как вход flake и положи его пакет в `environment.systemPackages`:

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

Потом `aipager service install` создаст пользовательский юнит systemd.

</details>

## <a id="everyday-use"></a>Каждый день

### <a id="in-telegram"></a>В Telegram

| Отправь | Чтобы |
|---|---|
| `/status` | Увидеть сессии этого чата, каждую с её состоянием и кнопками. |
| `/new x1` | Запустить сессию с именем x1. `/new x1 fix the tests` заодно отправит её первое сообщение. |
| `/x1` | Сделать x1 сессией, куда уходят твои сообщения. `/x1 <message>` отправляет ей сообщение напрямую. |
| `/stop` | Прервать текущую задачу и выбросить то, что ждёт в очереди. |
| `/mode` | Переключиться между режимами «Спрашивать», Авто и «Полный доступ». |
| `/app` | Открыть Mini App. |
| `/language` | Выбрать язык, на котором aipager говорит в этом чате. |
| `/update` | Обновить aipager и Claude Code и перезапустить их на месте (для админов). |
| `/help` | Короткая инструкция по задачам. |

Все команды, кнопки и настройки есть в [Командах бота](commands.md).

### <a id="on-the-command-line"></a>В командной строке

```sh
aipager start                             # сам aipager, в текущем терминале
aipager session dev                       # запустить здесь сессию dev или подключиться к ней
aipager session dev --continue            # продолжить последний разговор Claude в этой папке
aipager session dev -- --resume abc1234   # любой другой флаг claude передаётся как есть
aipager doctor                            # проверить установку
```

Чтобы выйти из сессии, не останавливая Claude, закрой терминал или панель tmux: сессия продолжит работу, а та же команда подключит тебя снова.

`aipager service install` держит aipager запущенным в фоне: как пользовательский юнит systemd на Linux (`~/.config/systemd/user/aipager.service`) или как агент launchd на macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). В обоих случаях aipager работает в твоей домашней папке, где и стартует новая сессия, если не выбрать другую папку. Управлять им можно через `aipager service start`, `stop`, `status`, `logs` и `uninstall`.

## <a id="security"></a>Безопасность

- **Никакого сервера aipager.** aipager общается с Bot API Telegram через твой собственный токен бота. Нет ни аккаунта aipager, ни сервиса aipager.
- **Никакого открытого порта.** aipager слушает только loopback. Mini App выходит наружу через туннель Cloudflare, где каждый запрос должен нести подпись Telegram, а `aipager miniapp disable` выключает его.
- **Каждый ответ записан.** Каждое «Разрешить», «Отклонить» и каждый ответ дописываются в `~/.claude/aipager-audit.jsonl`, а в группе вместе с тем, кто нажал.
- **Решает по-прежнему Claude Code.** Что нужно спрашивать, задают твои настройки Claude Code. aipager только передаёт вопрос и никогда не повышает права: никакого sudo, всё работает от твоего пользователя.

Полная картина, включая то, что защищает твои учётные данные Claude, в [модели безопасности](security.md).

## <a id="faq"></a>Частые вопросы

<details>
<summary><b>Чем это отличается от мобильных приложений Claude?</b></summary>

<br>

Приложения Claude общаются с ассистентом, который работает на серверах Anthropic. aipager соединяет тебя с сессией Claude Code на твоём собственном компьютере: твой репозиторий, твои инструменты, твоё окружение.

</details>

<details>
<summary><b>Мой код покидает мой компьютер?</b></summary>

<br>

aipager отправляет в Telegram только то, что ты видишь в своих чатах, через твой собственный токен бота; никакого сервера aipager посередине нет. Сам Claude Code общается с Anthropic, как и всегда.

</details>

<details>
<summary><b>Что такое dtach?</b></summary>

<br>

Крошечная программа, которая отсоединяет терминал. Она сохраняет Claude Code живым, когда терминал закрывается или обрывается SSH, так что ответ, отправленный через час, всё равно дойдёт до живой сессии. Она идёт вместе с aipager.

</details>

<details>
<summary><b>А если Claude понесёт не туда?</b></summary>

<br>

Запросы ждут твоего нажатия, `/stop` прерывает задачу и выбрасывает всё из очереди, `/kill` завершает сессию, а кнопки законченной задачи отказывают.

</details>

<details>
<summary><b>Как его обновить?</b></summary>

<br>

Отправь `/update`: он проверит aipager и Claude Code и поставит то, что вышло, а 🔄 Перезапустить сейчас поднимет aipager заново, как сервис или нет. Твои сессии всё это время продолжают работать. В командной строке `aipager update` обновляет через тот установщик, которым ставился aipager.

</details>

<details>
<summary><b>Я пользуюсь Bedrock или Vertex. Кнопки моделей работают?</b></summary>

<br>

Меню **Модель** на клавиатуре и Mini App предлагают семейные псевдонимы Claude Code (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) и закреплённые модели вроде `claude-opus-5-5` и `claude-sonnet-5-5`. В API Anthropic псевдоним указывает на самую новую модель семейства. На Bedrock и Vertex тот же псевдоним может указывать на более старую версию, в зависимости от того, что предлагает твой провайдер; отправь `/model <full-id>`, чтобы выбрать точную модель.

</details>

<details>
<summary><b>Сколько это стоит?</b></summary>

<br>

Нисколько. aipager распространяется под лицензией MIT, бот твой, а Claude Code продолжает пользоваться тем тарифом или ключом, что и раньше.

</details>

## <a id="documentation"></a>Документация

| Страница | О чём она |
|---|---|
| [Команды бота](commands.md) | Каждая команда, кнопка, настройка и параметр командной строки |
| [Работа в команде и группы](groups.md) | Роли, корневые папки, кто что может нажимать, модель доверия |
| [Модель безопасности](security.md) | Граница доверия, секреты, журнал аудита, сеть |
| [События hooks](hooks.md) | Что делает в aipager каждый hook Claude Code |
| [Архитектура](architecture.md) | Части aipager, его файлы и сокеты |
| [Решение проблем](troubleshooting.md) | `aipager doctor` и решения частых проблем |
| [Отчёты о проблемах](problem-reports.md) | Что содержит отчёт и когда aipager предлагает его отправить |
| [Установка с помощью агента](install-with-an-agent.md) | Инструкция, по которой работает агент для программирования |

## <a id="contributing"></a>Участие в разработке

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` приходит с зависимостью `dtach-bin`, больше ничего не нужно. Запускай тесты с ограничением памяти, как выше (на macOS без `systemd-run`): иначе вышедший из-под контроля тест может занять всю память машины. Как выпускаются версии, описано в [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Лицензия

MIT. См. [LICENSE](../../../LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
