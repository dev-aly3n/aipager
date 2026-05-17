# aipager

Telegram remote-control for [Claude Code](https://claude.com/claude-code)
CLI sessions. Run Claude inside a detached terminal (`dtach`), drive it
from your phone — read responses, send prompts, approve permission
requests, switch sessions — without an SSH session staying open.

## Install

Requires Python 3.10+ (Linux / macOS).

### One-line install (auto-detects pipx / uv / brew)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

### pipx

```sh
pipx install aipager
```

— or `uv tool install aipager`, or `pip install aipager` into a venv.
All variants work the same. `dtach` is installed automatically via the
[`dtach-bin`](https://pypi.org/project/dtach-bin/) dependency — no
separate system package needed. Linux ARM and macOS Apple Silicon are
supported via pre-built wheels.

### Homebrew (macOS, Linuxbrew)

```sh
brew install dev-aly3n/tap/aipager
```

This pulls `dtach` from Homebrew's standard formula (works on both Intel
and Apple Silicon Macs) and installs aipager into a Homebrew-managed
Python venv.

## Configure

```sh
aipager config
```

Interactive wizard — asks for your Telegram bot token (from
[@BotFather](https://t.me/BotFather)) and chat ID, validates them, then
patches `~/.claude/settings.json` to wire the necessary hooks
automatically. You never edit any file by hand.

## Run

```sh
aipager start
```

The daemon stays in the foreground. Launch a Claude session in another
terminal:

```sh
claude-dtach dev
```

The daemon discovers the session within seconds and Telegram starts
mirroring it.

### Run as a service (survives logout)

```sh
aipager service install
```

On Linux this writes a systemd-user unit at
`~/.config/systemd/user/aipager.service` and starts it. On macOS it
writes a launchd plist at `~/Library/LaunchAgents/com.aipager.daemon.plist`
and bootstraps it. Subcommands: `start`, `stop`, `status`, `logs`,
`uninstall`.

## What it does

- Mirrors Claude Code session state to Telegram: busy/idle, tool calls,
  context %, cost, line counts
- Lets you reply to messages to inject prompts back into the session
- Surfaces permission prompts and `AskUserQuestion` dialogs as Telegram
  inline keyboards
- Notifies on context warnings, compaction, session end, and stalls
- Supports multiple concurrent sessions with one bot
- Optional read-only observer bots

## Developing locally

```sh
git clone <repo-url> aipager && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

When iterating on code changes you'll generally want to also install
`dtach-bin` from a local checkout — or `pip install dtach-bin` — so the
runtime can find `dtach` on PATH.

Release process is in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
