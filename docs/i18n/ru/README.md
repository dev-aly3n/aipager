# aipager

<!-- i18n-source: README.md @ a175bc5 -->

> Эта страница переведена с английской; если они расходятся, верна английская версия. ([English](../../../README.md))

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](../../../LICENSE)

Пульт управления сессиями [Claude Code](https://claude.com/claude-code) через Telegram. Claude работает на твоём компьютере в отсоединённом терминале (`dtach`), а ты пользуешься им с телефона: читаешь ответы Claude, пишешь ему сообщения, отвечаешь на его запросы разрешения и вопросы и переключаешься между сессиями, не держа открытым SSH-соединение.

[aipager.run](https://aipager.run) · [Документация](#docs) · [История изменений](../../../CHANGELOG.md) · [Issues на GitHub](https://github.com/dev-aly3n/aipager/issues)

**Читать на другом языке:** [English](../../../README.md) · [Español](../es/README.md) · [Português (Brasil)](../pt-BR/README.md) · [简体中文](../zh-Hans/README.md) · [Français](../fr/README.md) · [Deutsch](../de/README.md) · [한국어](../ko/README.md) · [العربية](../ar/README.md) · [Bahasa Indonesia](../id/README.md) · [Українська](../uk/README.md) · [Italiano](../it/README.md) · [Türkçe](../tr/README.md) · [فارسی](../fa/README.md) · [Tiếng Việt](../vi/README.md)

## <a id="install"></a>Установка

Есть агент для программирования (Claude Code или любой агент с shell)? Создай бота через [@BotFather](https://t.me/BotFather), нажми в нём Start и проверь, что Claude Code на этом компьютере вошёл в твой аккаунт. Сохрани токен бота в файл, который агенту никогда не придётся читать (вставь токен, нажми Enter, потом Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Потом дай агенту такое сообщение:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

Если не знаешь свой Telegram ID, так и скажи агенту; он сможет его найти.

Инструкция, по которой работает агент, включая то, как безопасно передать ему токен, это [Установка с помощью агента](install-with-an-agent.md). Чтобы установить самостоятельно, читай дальше.

Linux или macOS, любая архитектура. `dtach` ставится автоматически через зависимость [`dtach-bin`](https://pypi.org/project/dtach-bin/): отдельный системный пакет не нужен.

### <a id="one-line-install-recommended"></a>Установка одной строкой (рекомендуется)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

Она использует `uv`, если он есть, иначе `pipx`, иначе (только на macOS) Homebrew. Если ничего из этого нет, она сначала ставит `uv` (менеджер инструментов Python от Astral) и ставит aipager через него.

### <a id="uv-recommended-on-macos"></a>uv (рекомендуется на macOS)

```sh
uv tool install aipager     # если uv уже установлен
```

Или, чтобы сначала поставить uv:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv приносит свой интерпретатор Python, поэтому это работает на любой версии macOS или Linux, что бы ни происходило с системным Python; так ты обходишь конфликт между Python из Homebrew и Xcode, описанный ниже в разделе про Homebrew.

### <a id="pipx"></a>pipx

```sh
pipx install aipager
```

### <a id="homebrew-tap-macos-linuxbrew"></a>Homebrew tap (macOS, Linuxbrew)

> **Примечание:** на macOS рекомендуемый путь это `uv tool install aipager`. Формула brew работает, когда пакет `python@3.12` из Homebrew и твои Xcode / Command Line Tools согласованы, но время от времени они расходятся; последний раз это было на **macOS Tahoe (26.x)**, где установка падает с ошибкой символа `pyexpat _XML_SetAllocTrackerActivationThreshold` ([issue в upstream](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Обновление Xcode и Command Line Tools обычно помогает, но проще использовать uv.

```sh
brew install dev-aly3n/tap/aipager
```

Берёт `dtach` из стандартной формулы Homebrew и ставит aipager в Python venv, которым управляет Homebrew.

### <a id="docker"></a>Docker

Готовый образ, в котором уже есть python, node, `claude` и `dtach`; удобно для VPS, NAS или Raspberry Pi, где не хочется ставить Python или Node в систему. Под несколько архитектур (amd64, arm64).

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

Подключи в `/workspace` папки, которые Claude должен править. Подключение `~/.claude` переносит твои учётные данные Claude и историю переписок; чтобы войти, один раз запусти `claude` в системе или `docker exec -it aipager claude`, чтобы войти внутри контейнера.

Теги: `latest`, `1.1`, `1.1.0` (по одному на точную версию и на минорную).

### <a id="nix-flake"></a>Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Собирает aipager из исходников с зафиксированными зависимостями из nixpkgs. `dtach` даёт Nix; `claude` **нет**: поставь его отдельно (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code` или по документации Anthropic).

Для декларативной конфигурации NixOS или Home Manager добавь aipager как вход flake и возьми его пакет в `environment.systemPackages`:

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

После этого `aipager service install` настроит пользовательскую службу systemd.

### <a id="snap"></a>Snap

```sh
snap install aipager
```

Snap со строгой изоляцией, в котором есть python, node, `claude`, `dtach` и aipager. Из-за песочницы snap рабочие папки должны лежать внутри `~/` (например, `~/projects/foo`). Манифест лежит в [`packaging/snap/`](../../../packaging/snap/).

## <a id="configure"></a>Настройка

```sh
aipager config
```

Интерактивный мастер: спрашивает токен твоего Telegram-бота (от [@BotFather](https://t.me/BotFather)) и ID чата, проверяет их и сам добавляет нужные hooks в `~/.claude/settings.json`. Тебе никогда не придётся править файлы вручную.

Настраиваешь aipager для кого-то из скрипта или через агента для программирования? `aipager setup --token-file FILE --chat-id ID` делает то же самое без вопросов (см. [команду setup](commands.md#command-line-aipager-setup-for-coding-agents) и [инструкцию для агентов](install-with-an-agent.md)).

## <a id="run"></a>Запуск

```sh
aipager start
```

aipager остаётся на переднем плане. Запусти сессию Claude в другом терминале:

```sh
aipager session dev
```

Это создаёт сессию dtach с именем `claude-dev`, в которой работает Claude Code (или подключается к ней, если она уже есть). aipager находит её за несколько секунд, и Telegram начинает её показывать. Сессию можно запустить и из Telegram: отправь боту `/new dev`. Чтобы потом снова подключиться, запусти ту же команду; чтобы уйти, не останавливая Claude, закрой терминал или панель tmux (сессия продолжит работать).

Если сессию dtach закрыли (например, компьютер перезагрузился), а переписку Claude хочется продолжить с диска, добавь `--continue`:

```sh
aipager session dev --continue    # последняя переписка claude в этой папке
```

Можно передать в конце и `--resume <session-id>` (или любой другой флаг claude):

```sh
aipager session dev -- --resume abc1234
```

### <a id="run-as-a-service-survives-logout"></a>Запуск как служба (работает и после выхода из системы)

```sh
aipager service install
```

На Linux это записывает пользовательскую службу systemd в `~/.config/systemd/user/aipager.service` и запускает её. На macOS это записывает plist для launchd в `~/Library/LaunchAgents/com.aipager.daemon.plist` и запускает его. Подкоманды: `start`, `stop`, `status`, `logs`, `uninstall`.

## <a id="team-mode-groups-and-other-people"></a>Работа в команде (группы и другие люди)

По умолчанию aipager это личный бот: ты и бот в одном личном чате. Чтобы поделиться им, добавь группу Telegram, личные чаты других людей с ботом или и то и другое. Для группы: добавь бота в группу (админом, чтобы он мог закрепить строку состояния группы), снова запусти `aipager config` и выбери **Add a group scope**. Мастер предлагает тебя (владельца твоего собственного личного чата) владельцем группы, добавляет каждого участника с ролью (`owner`, `admin`, `user` или `read_only`), спрашивает корневую папку участников и перезагружает настройки aipager без перезапуска. В группе бот реагирует только на команды, ответы на свои сообщения и упоминания (`@aipagerbot fix the tests`, `/jim run the tests`), и каждое сообщение выполняется с ролью того, кто его отправил. Участник с `user` или `read_only` запускает и держит сессии только в корневой папке своего чата (по умолчанию `~/aipager-users/<label>`). **Добавить участника значит дать ему право запускать код на твоём компьютере**: полная модель доверия описана в [Работа в команде и группы](groups.md).

## <a id="what-it-does"></a>Что он умеет

- Показывает в Telegram, что делает каждая сессия Claude Code: работает она или готова, какие инструменты использует, % контекста, стоимость и изменённые строки, на одной карточке на задачу, а потом ответ Claude
- Отправляет твои сообщения Claude **сразу**, даже пока он работает: отправь несколько, и Claude сам поставит их в очередь, как при наборе в терминале. 👀 значит отправлено, 👍 значит Claude его забрал
- Придерживает сообщение, пока открыт запрос разрешения или вопрос, чтобы оно никогда не прочиталось как ответ на него, и отправляет его, когда ты ответишь
- Показывает запросы разрешения и вопросы Claude с кнопками (Разрешить, Отклонить, Разрешать всегда, по одной на вариант); кнопка, оставшаяся от уже законченной задачи, отказывает, а не действует на текущую работу
- Три режима разрешений на сессию, переключаются через `/mode`: 💬 режим «Спрашивать» (Claude спрашивает перед каждым действием), 🤖 режим Авто, по умолчанию (собственная проверка безопасности Claude Code пропускает безопасные действия и останавливает рискованные, чтобы решил ты) и 🔓 режим «Полный доступ» (всё выполняется без каких-либо проверок; для админов и для тебя в твоём собственном личном чате)
- Запускает сессии из чата: `/new x1` сразу запускает одну (`/new x1 fix the tests` ещё и отправляет первое сообщение), с режимом, моделью и папкой из `/settings` и кнопками, чтобы их сменить
- Принимает фото, файлы и голосовые сообщения (голосовые превращаются в текст на твоём компьютере, с дополнительным пакетом для голосовых)
- Сообщает, когда контекст почти заполнен, когда переписка сжата, когда сессия завершилась и когда какая-то долго молчит
- Держит несколько сессий одновременно через одного бота; по желанию работа в команде с ролями и правилами для каждого инструмента ([Работа в команде и группы](groups.md))
- Боты-наблюдатели только для чтения, по желанию
- Говорит на English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی и Tiếng Việt: только тексты самого aipager, никогда твои сессии или ответы Claude ([Языки](commands.md#languages)). Этот README и документация тоже переведены (см. **Читать на другом языке** выше)

### <a id="mini-app"></a>Mini App

`/app` открывает панель внутри Telegram: твои сессии с Остановить, Завершить, Перезапустить, Переименовать и сменой режима, просмотр diff для изменений `Write`/`Edit`, настройки и, для владельца, «Сообщить о проблеме». Её отдаёт сам aipager, и она **включена по умолчанию**; каждый запрос проверяется по подписи `initData` от Telegram. Если ты состоишь больше чем в одном чате (твой личный чат и группа), строка с названиями чатов вверху переключает между ними. Управляй ею через `aipager miniapp enable|disable|status` или направь её на свой адрес вместо управляемого туннеля: см. [Модель безопасности](security.md#mini-app-tunnel).

### <a id="note-on-model-buttons-bedrock--vertex-users"></a>Про кнопки моделей (для Bedrock и Vertex)

Подменю **Модель** на постоянной клавиатуре (и выбор модели в Mini App) предлагает семейные алиасы Claude Code, `sonnet`, `opus`, `haiku`, `fable` и `opusplan`, и закреплённые модели вроде `claude-opus-5-5` и `claude-sonnet-5-5` (полный список в разделе [Постоянная клавиатура](commands.md#persistent-keyboard)). В API Anthropic алиас всегда указывает на новейшую модель своего семейства. На **Bedrock** и **Vertex** те же алиасы могут указывать на более старые версии, в зависимости от того, что есть у твоего провайдера. Если ты работаешь через эти сервисы и тебе нужна конкретная модель, отправь из чата `/model <full-id>`.

## <a id="developing-locally"></a>Разработка на своём компьютере

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` приходит с зависимостью `dtach-bin`, больше ничего не нужно. Запускай тесты с ограничением памяти, как выше (на macOS без `systemd-run`): иначе вышедший из-под контроля тест может забрать всю память компьютера.

Порядок выпуска версий описан в [CONTRIBUTING.md](../../../CONTRIBUTING.md).

## <a id="license"></a>Лицензия

MIT; полный текст в [LICENSE](../../../LICENSE).

## <a id="docs"></a>Документация

| Страница | О чём |
|---|---|
| [Установка с помощью агента](install-with-an-agent.md) | Шаги, по которым агент для программирования ставит и настраивает aipager для своего человека |
| [Архитектура](architecture.md) | Как устроена программа, схема частей, где лежат файлы и сокеты |
| [События hooks](hooks.md) | Каждое событие, которое aipager получает от Claude Code, с полями |
| [Команды бота](commands.md) | Команды, кнопки, Mini App, голосовые и файлы, все команды `aipager` и настройки из окружения |
| [Работа в команде и группы](groups.md) | Настройка для нескольких людей, роли, правила, кто что сделал |
| [Боты-наблюдатели](observers.md) | Боты только для чтения, которые получают копии уведомлений |
| [Решение проблем](troubleshooting.md) | Частые сбои и таблица решений `aipager doctor` |
| [Отчёты о проблемах](problem-reports.md) | Что содержит отчёт о проблеме, куда он уходит и когда aipager его предлагает |
| [Модель безопасности](security.md) | Граница доверия, секреты, журнал аудита, сетевые соединения |
