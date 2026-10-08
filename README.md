# aipager

[![PyPI](https://img.shields.io/pypi/v/aipager?label=pypi&color=3775A9)](https://pypi.org/project/aipager/)
[![Python](https://img.shields.io/pypi/pyversions/aipager?color=3775A9)](https://pypi.org/project/aipager/)
[![License](https://img.shields.io/badge/license-MIT-black)](LICENSE)

Telegram remote-control for [Claude Code](https://claude.com/claude-code)
CLI sessions. Run Claude inside a detached terminal (`dtach`), drive it
from your phone - read responses, send prompts, approve permission
requests, switch sessions - without an SSH session staying open.

[aipager.run](https://aipager.run) · [Docs](docs/) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/dev-aly3n/aipager/issues)

## Install

Have a coding agent (Claude Code, or any agent with a shell)? Create a
bot with [@BotFather](https://t.me/BotFather), press Start in it, and
make sure you are logged in to Claude Code on this machine. Save the
bot token to a file your agent never has to read (paste the token,
press Enter, then Ctrl-D):

```sh
mkdir -p ~/.config/aipager
rm -f ~/.config/aipager/bot-token
(umask 077 && cat > ~/.config/aipager/bot-token)
chmod 600 ~/.config/aipager/bot-token
```

Then give your agent this prompt:

```text
Install aipager for me by following https://aipager.run/agent.md. My Telegram bot token is in ~/.config/aipager/bot-token and my Telegram user id is <your id>.
```

If you do not know your Telegram user id, say so instead; the agent
can find it.

The guide it follows, including how to hand over the token safely, is
[docs/install-with-an-agent.md](docs/install-with-an-agent.md). To
install it yourself, read on.

Linux or macOS, any architecture. `dtach` is installed automatically
via the [`dtach-bin`](https://pypi.org/project/dtach-bin/) dependency -
no separate system package needed.

### One-line install (recommended)

```sh
curl -fsSL https://raw.githubusercontent.com/dev-aly3n/aipager/main/install.sh | sh
```

This auto-detects `uv` / `pipx` / `brew` and uses whichever is already on
your system. If none is present, it bootstraps `uv` (Astral's Python tool
manager) and installs through it.

### uv (recommended on macOS)

```sh
uv tool install aipager     # if uv is already installed
```

Or, to install uv first:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv bundles its own Python interpreter, so this works on any macOS /
Linux version regardless of what system Python is doing - it
sidesteps the Homebrew-Python-vs-Xcode breakage described under the
Homebrew section below.

### pipx

```sh
pipx install aipager
```

### Homebrew tap (macOS, Linuxbrew)

> **Note:** `uv tool install aipager` is the recommended path on
> macOS. The brew formula works when Homebrew's `python@3.12` bottle
> and your Xcode / Command Line Tools are in sync, but they
> periodically drift apart - most recently on **macOS Tahoe
> (26.x)**, where install fails with a
> `pyexpat _XML_SetAllocTrackerActivationThreshold` symbol error
> ([upstream issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)).
> Updating Xcode + Command Line Tools usually fixes it, but it's
> easier to just use uv.

```sh
brew install dev-aly3n/tap/aipager
```

Pulls `dtach` from Homebrew's standard formula and installs aipager into
a Homebrew-managed Python venv.

### Docker

Self-contained image with python, node, `claude` and `dtach` baked in,
good for VPS / NAS / Pi deployments where you don't want a Python
or Node toolchain on the host. Multi-arch (amd64, arm64).

```sh
# 1. Run the setup wizard once (interactive)
docker run --rm -it \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  ghcr.io/dev-aly3n/aipager:latest config

# 2. Start the daemon (background, auto-restart)
docker run -d --restart=unless-stopped --name aipager \
  -v "$HOME/.claude:/home/aipager/.claude" \
  -v aipager-config:/home/aipager/.config/aipager \
  -v "$PWD:/workspace" \
  ghcr.io/dev-aly3n/aipager:latest
```

Mount the directories you want claude to edit under `/workspace`. The
`~/.claude` mount carries over your claude credentials and
conversation history - run `claude` on the host once to authenticate,
or `docker exec -it aipager claude` for an interactive login in the
container.

Tags: `latest`, `0.7`, `0.7.20` (semver track + minor track).

### Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Builds aipager from source against pinned nixpkgs deps. `dtach` is
provided by Nix; `claude` is **not** - install it separately
(`nix profile install nixpkgs#nodejs && npm install -g
@anthropic-ai/claude-code`, or follow Anthropic's docs).

For declarative NixOS / Home Manager configs, add aipager as a flake
input and pick its package up from `environment.systemPackages`:

```nix
{
  inputs.aipager.url = "github:dev-aly3n/aipager";

  outputs = { self, nixpkgs, aipager, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [{
        environment.systemPackages = [
          aipager.packages.${pkgs.system}.default
        ];
      }];
    };
  };
}
```

`aipager service install` will then wire up a systemd-user unit.

### Group mode (multi-user)

aipager runs by default as a 1:1 DM bot. To use it in a Telegram
group with several developers, add the bot to the group (as an admin,
so it can pin the group's status bar), re-run `aipager config` and
pick **Add a group scope**. The wizard offers you (the owner of your
own DM) as the group's owner, then adds each member with a role (`owner` / `admin` / `user` /
`read_only`), and reloads the running daemon. In the group the bot
acts only on commands, replies to its messages and mentions
(`@aipagerbot fix the tests`, `/jim run the tests`); each message runs
with its sender's role. **Adding a user grants them code-execution
rights on the host**: see [docs/groups.md](docs/groups.md) for the
full trust model.

### Snap

```sh
snap install aipager
```

Strict-confinement snap that bundles python + node + `claude` +
`dtach` + aipager. Because of snap's sandbox model, workspaces must
live under `~/` (e.g. `~/projects/foo`). Manifest at
[`packaging/snap/`](packaging/snap/).

## Configure

```sh
aipager config
```

Interactive wizard - asks for your Telegram bot token (from
[@BotFather](https://t.me/BotFather)) and chat ID, validates them, then
patches `~/.claude/settings.json` to wire the necessary hooks
automatically. You never edit any file by hand.

Setting aipager up for someone from a script or a coding agent?
`aipager setup --token-file FILE --chat-id ID` does the same with no
prompts (see [the setup command](docs/commands.md#command-line-aipager-setup-for-coding-agents)
and [the guide for agents](docs/install-with-an-agent.md)).

## Run

```sh
aipager start
```

The daemon stays in the foreground. Launch a Claude session in another
terminal:

```sh
aipager session dev
```

This creates (or reattaches to) a dtach session named `claude-dev`
running Claude Code. The aipager daemon discovers it within seconds
and Telegram starts mirroring it. Re-run the same command to reattach
later; to leave without stopping Claude, close the terminal or tmux
pane (the session keeps running).

If the dtach session was killed (machine reboot, etc.) but you want
to pick up the Claude conversation from disk, add `--resume`:

```sh
aipager session dev --resume    # resume the last claude conversation in this cwd
```

You can also pass `--resume <session-id>` (or any other claude flag)
through as trailing args:

```sh
aipager session dev -- --resume abc1234
```

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
- Sends your messages to Claude **immediately**, even mid-turn - send
  several and Claude queues them itself, exactly like typing in the
  terminal. 👀 means sent, 👍 means Claude picked it up
- Holds a message while a permission or question prompt is open, so it
  can never be swallowed as an answer to that dialog - then delivers it
  once you respond
- Surfaces permission prompts and `AskUserQuestion` dialogs as Telegram
  inline keyboards; buttons from an already-finished task refuse
  instead of acting on your current work
- Creates sessions from chat: `/new x1` starts one at once (`/new x1 fix
  the tests` also sends its first message), with the mode, model and
  folder set in `/settings`, and buttons to change them
- Notifies on context warnings, compaction, session end, and stalls
- Supports multiple concurrent sessions with one bot; optional
  multi-user team mode with roles and per-tool rules
  ([docs/groups.md](docs/groups.md))
- Optional read-only observer bots

### Mini App

`/app` opens a dashboard inside Telegram - live session list with
stop / kill / restart / rename / permission controls, a diff viewer
for `Write`/`Edit` changes, and settings. It is served by the daemon
itself and is **on by default**; every request is verified against
Telegram's `initData` signature. If you belong to more than one chat
(your DM and a group), a row of chat names at the top switches between
them. Manage it with
`aipager miniapp enable|disable|status`, or point it at your own URL
instead of the managed tunnel - see
[docs/security.md](docs/security.md#mini-app-tunnel).

### Note on model buttons (Bedrock / Vertex users)

The persistent keyboard's **Model** submenu (and the Mini App's model
pickers) offers Claude Code's family aliases, `sonnet`, `opus`, `haiku`,
`fable` and `opusplan`, and pinned models such as `claude-opus-5-5` and
`claude-sonnet-5-5` (the full list is in
[docs/commands.md](docs/commands.md#persistent-keyboard)). On the
Anthropic API an alias resolves to the latest model in its family. On
**Bedrock** and **Vertex** the same aliases may resolve to older
snapshots depending on your provider's available versions. If you
target those backends and want a specific model, send `/model
<full-id>` from chat.

## Developing locally

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` comes with the `dtach-bin` dependency, so nothing else is
needed. Run the tests under a memory cap as above (on macOS, without
`systemd-run`): a runaway test can otherwise take the machine's memory.

Release process is in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT - see [LICENSE](LICENSE).
