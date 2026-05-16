# aipager

Telegram remote-control for [Claude Code](https://claude.com/claude-code)
CLI sessions. Run Claude inside a detached terminal (`dtach`), drive it
from your phone — read responses, send prompts, approve permission
requests, switch sessions — without an SSH session staying open.

## Install

Requires Python 3.10+. **`dtach` is installed automatically** via the
[`dtach-bin`](https://pypi.org/project/dtach-bin/) dependency — no
separate system package needed.

```sh
pipx install aipager
```

(or `uv tool install aipager`, or `pip install aipager` into a venv —
all work the same.)

Linux ARM and macOS users on Apple Silicon get the same one-command
install; the dtach binary ships as pre-built wheels for each platform.

> **Homebrew support is coming in v0.3** as `brew install <user>/tap/aipager`,
> which uses the system `dtach` instead of the bundled one.

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
mirroring it. To survive logout, use `screen`, `tmux`, or a systemd-user
unit (template at `scripts/aipager.service.example` — full `aipager
service install` automation lands in v0.4).

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
