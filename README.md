# aipager

Telegram remote-control for [Claude Code](https://claude.com/claude-code)
CLI sessions. Run Claude inside a detached terminal (`dtach`), drive it
from your phone — read responses, send prompts, approve permission
requests, switch sessions — without an SSH session staying open.

## Install

Linux or macOS, any architecture. `dtach` is installed automatically
via the [`dtach-bin`](https://pypi.org/project/dtach-bin/) dependency —
no separate system package needed.

### One-line install (recommended)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

This auto-detects `uv` / `pipx` / `brew` and uses whichever is already on
your system. If none is present, it bootstraps `uv` (Astral's Python tool
manager) and installs through it.

### uv

```sh
uv tool install aipager     # if uv is already installed
```

— or to install uv first:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv bundles its own Python interpreter, so this works on any macOS /
Linux version regardless of what system Python is doing.

### pipx

```sh
pipx install aipager
```

### Homebrew tap (macOS, Linuxbrew)

```sh
brew install dev-aly3n/tap/aipager
```

Pulls `dtach` from Homebrew's standard formula and installs aipager into
a Homebrew-managed Python venv.

> **Heads-up:** if you're on **macOS Tahoe (26.x)**, the brew path may
> fail at `pip install` time with a `pyexpat _XML_SetAllocTrackerActivationThreshold`
> symbol error — that's a [known Homebrew Python bottle
> issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)
> unrelated to aipager. Use the uv path instead (it's not affected) until
> Homebrew rebuilds the bottles for Tahoe.

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
aipager new dev
```

This creates (or reattaches to) a dtach session named `claude-dev`
running Claude Code. The aipager daemon discovers it within seconds
and Telegram starts mirroring it. Re-run the same command to reattach
later; detach with `Ctrl-\`.

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
