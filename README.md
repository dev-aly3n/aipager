# aipager

Telegram remote-control for [Claude Code](https://claude.com/claude-code)
CLI sessions. Run Claude inside a detached terminal (`dtach`), drive it
from your phone — read responses, send prompts, approve permission
requests, switch sessions — without an SSH session staying open.

[Docs](docs/) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/dev-aly3n/aipager/issues)

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

### uv (recommended on macOS)

```sh
uv tool install aipager     # if uv is already installed
```

— or to install uv first:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install aipager
```

uv bundles its own Python interpreter, so this works on any macOS /
Linux version regardless of what system Python is doing — it
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
> periodically drift apart — most recently on **macOS Tahoe
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

Self-contained image with python, node, `claude` and `dtach` baked in
— good for VPS / NAS / Pi deployments where you don't want a Python
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
conversation history — run `claude` on the host once to authenticate,
or `docker exec -it aipager claude` for an interactive login in the
container.

Tags: `latest`, `0.3`, `0.3.12` (semver track + minor track).

### Nix flake

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Builds aipager from source against pinned nixpkgs deps. `dtach` is
provided by Nix; `claude` is **not** — install it separately
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

### AUR (Arch Linux)

```sh
yay -S aipager           # or paru, pikaur — any AUR helper
```

System `dtach` and `python-telegram-bot` come from pacman; install
the Anthropic `claude` CLI separately
(`sudo pacman -S npm && sudo npm install -g @anthropic-ai/claude-code`).
PKGBUILD lives at [`packaging/aur/`](packaging/aur/) for review.

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
aipager session dev
```

This creates (or reattaches to) a dtach session named `claude-dev`
running Claude Code. The aipager daemon discovers it within seconds
and Telegram starts mirroring it. Re-run the same command to reattach
later; detach with `Ctrl-\`.

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
- Lets you reply to messages to inject prompts back into the session
- Surfaces permission prompts and `AskUserQuestion` dialogs as Telegram
  inline keyboards
- Notifies on context warnings, compaction, session end, and stalls
- Supports multiple concurrent sessions with one bot
- Optional read-only observer bots

### Note on model buttons (Bedrock / Vertex users)

The persistent keyboard's **Model** submenu sends `/model sonnet`,
`/model opus`, `/model haiku`, and `/model opusplan` — Claude Code's
aliases. On the Anthropic API these resolve to the latest in each
family (currently Opus 4.7, Sonnet 4.6, Haiku 4.5). On **Bedrock** and
**Vertex** the same aliases may resolve to older snapshots depending on
your provider's available versions. If you target those backends and
want a specific model, tap the alias as a starting point, then `/model
<full-id>` from chat.

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
