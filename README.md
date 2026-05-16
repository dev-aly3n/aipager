# aipager

Telegram remote-control for [Claude Code](https://claude.com/claude-code)
CLI sessions. Run Claude inside a detached terminal (`dtach`), and drive it
from your phone — read responses, send prompts, approve permission
requests, switch sessions — without an SSH session staying open.

## Install

Requires Python 3.10+ and the `dtach` binary (see below).

```sh
git clone <repo-url> aipager && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Install `dtach`:

```sh
sudo apt install dtach     # Debian / Ubuntu
brew install dtach          # macOS
sudo dnf install dtach      # Fedora
```

## Configure

```sh
aipager config
```

Interactive wizard — asks for your Telegram bot token (from
[@BotFather](https://t.me/BotFather)) and chat ID, validates them, then
wires the necessary hooks into `~/.claude/settings.json` automatically.
You don't edit any file by hand.

## Run

```sh
aipager start
```

The daemon stays in the foreground. Launch a Claude session in another
terminal:

```sh
claude-dtach dev
```

The daemon discovers it within seconds and Telegram starts mirroring the
session. To survive logout, use `screen`, `tmux`, or a systemd-user unit
(template in `scripts/aipager.service.example`).

## What it does

- Mirrors Claude Code session state to Telegram: busy/idle, tool calls,
  context %, cost, line counts
- Lets you reply to messages to inject prompts back into the session
- Surfaces permission prompts and `AskUserQuestion` dialogs as Telegram
  inline keyboards
- Notifies on context warnings, compaction, session end, and stalls
- Supports multiple concurrent sessions with one bot
- Optional read-only observer bots

## License

MIT — see [LICENSE](LICENSE).
