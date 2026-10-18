<div align="center">

<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="96" height="96" alt="aipager"></a>

# aipager

<h3>Claude is one more chat.</h3>

Your [Claude Code](https://claude.com/claude-code) sessions in Telegram, next to your family, your friends and your team.<br>
They run on your own computer. You follow them, answer them and steer them from wherever you are.

<p>
<a href="https://pypi.org/project/aipager/"><img alt="PyPI" src="https://img.shields.io/pypi/v/aipager?style=flat-square&label=pypi&labelColor=181c20&color=2aabee"></a>
<a href="https://pypi.org/project/aipager/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/aipager?style=flat-square&labelColor=181c20&color=2aabee"></a>
<a href="https://github.com/dev-aly3n/aipager/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/dev-aly3n/aipager/test.yml?branch=main&style=flat-square&label=tests&labelColor=181c20"></a>
<img alt="Linux and macOS" src="https://img.shields.io/badge/runs%20on-Linux%20%7C%20macOS-2aabee?style=flat-square&labelColor=181c20">
<a href="https://github.com/dev-aly3n/aipager/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2aabee?style=flat-square&labelColor=181c20"></a>
</p>

**[Website](https://aipager.run)** · **[Install](#install)** · **[Docs](https://github.com/dev-aly3n/aipager/tree/main/docs)** · **[Security](https://github.com/dev-aly3n/aipager/blob/main/docs/security.md)** · **[Changelog](https://github.com/dev-aly3n/aipager/blob/main/CHANGELOG.md)**

**Read this in:** [Español](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/es) · [Русский](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/ru) · [Português (Brasil)](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/pt-BR) · [简体中文](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/zh-Hans) · [Français](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/fr) · [Deutsch](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/de) · [한국어](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/ko) · [العربية](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/ar) · [Bahasa Indonesia](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/id) · [Українська](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/uk) · [Italiano](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/it) · [Türkçe](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/tr) · [فارسی](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/fa) · [Tiếng Việt](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n/vi)

<br>

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/hero.webp" width="100%" alt="A message typed in Telegram reaches Claude Code in a terminal, and one card in the chat edits itself as Claude works">

<sub>You write in Telegram. aipager types it into Claude Code on your computer, and one card edits itself as the turn runs.</sub>

</div>

<br>

## Why aipager

<table>
<tr>
<td width="33%" valign="top">

**📟 One live card per turn**<br>
What Claude is doing right now: its tool calls, context, cost and changed lines, then the answer. The card edits itself, so the chat stays calm.

</td>
<td width="33%" valign="top">

**⚡ Talk to it mid-turn**<br>
Your message is typed into the session at once, even while Claude works, exactly like typing in the terminal. 👀 means sent, 👍 means Claude took it.

</td>
<td width="33%" valign="top">

**✅ Answer with a tap**<br>
Permission prompts and Claude's questions arrive with a button for each answer. A button left over from a finished task refuses instead of acting on the next one.

</td>
</tr>
<tr>
<td width="33%" valign="top">

**🛡️ Three permission modes**<br>
💬 Ask, 🤖 Auto (the default for new sessions) and 🔓 Full access, set per session and switched any time with `/mode`.

</td>
<td width="33%" valign="top">

**👥 Made for teams**<br>
Put the bot in a group. Every message runs with its sender's role, members can be kept to their own folder, and every tap is in the audit log.

</td>
<td width="33%" valign="top">

**🔒 Nothing in the middle**<br>
No aipager server, no account, no open port. Your bot token, your computer, your chats.

</td>
</tr>
</table>

## A quick tour

### Questions come to you

When Claude asks you something or wants permission, the question lands in your chat with a button for each answer, and the pinned bar says what needs you. Your tap is typed into the session as if you were at the keyboard, and the terminal shows the same answer.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/question.webp" width="100%" alt="Claude asks whether to raise the timeout or wait for the cookie; the answer tapped in Telegram is picked in the terminal">

### Three permission modes

Every session is in one of three modes, changed any time with `/mode`, even while Claude works.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/modes.webp" width="100%" alt="The same task in Ask mode, Auto mode and Full access mode">

| Mode | What happens |
|---|---|
| 💬 **Ask**<br>`/mode ask` | Claude asks before each action. |
| 🤖 **Auto**, the default<br>`/mode auto` | Claude Code's own safety check runs the safe actions and stops the risky ones for you to decide. |
| 🔓 **Full access**<br>`/mode full` | Everything runs with no checks. For admins, and for you in your own private chat with the bot. |

Ask and Auto switch in place, with no restart. Going to or from Full access restarts the session and keeps the conversation. Questions from Claude still come to you in every mode.

### Your team, in one group

Add the bot to a group and give each member a role: `owner`, `admin`, `user` or `read_only`. In the group the bot acts only on commands, replies to its messages and mentions (`@aipagerbot fix the tests`, `/web run the tests`), and each message runs with its sender's role: a tool the role may not use is refused before it runs. A `user` or `read_only` member works only inside their own root folder (`~/aipager-users/<label>` by default). Other people can also have their own private chat with the bot.

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/team.webp" width="100%" alt="A team group where a command is refused for a member's role while the tests pass">

> [!IMPORTANT]
> Adding a member gives them the right to run code on your computer. Read [the trust model](https://github.com/dev-aly3n/aipager/blob/main/docs/groups.md) before you share the bot.

To set it up, add the bot to the group as an admin (so it can pin the group's status bar), run `aipager config` again and pick **Add a group scope**. The wizard adds each member with a role, asks for the members' root folder and reloads the running daemon.

### A dashboard inside Telegram

`/app` opens the Mini App: your sessions with Stop, End, Restart, Rename and the mode switch, a diff viewer for every `Write` and `Edit`, the settings and, for the owner, Report a problem. It is served by the daemon itself and is on by default, and every request is checked against Telegram's signature. If you are in more than one chat, a row of chat names at the top switches between them. Turn it off with `aipager miniapp disable`, or point it at your own URL ([how](https://github.com/dev-aly3n/aipager/blob/main/docs/security.md#mini-app-tunnel)).

### Fifteen languages

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/languages.webp" width="420" align="right" alt="A permission prompt in Persian, right to left, with Persian digits">

- Speaks English, Español, Русский, Português (Brasil), 简体中文, Français, Deutsch, 한국어, العربية, Bahasa Indonesia, Українська, Italiano, Türkçe, فارسی and Tiếng Việt.
- Pick one with `/language`, or let a private chat follow your Telegram app.
- Persian and Arabic read right to left, with their own digits.
- Only aipager's own words change. Your sessions do not: Claude answers in the language you write to it.
- This README and [the docs](https://github.com/dev-aly3n/aipager/tree/main/docs/i18n) are translated too.

<br clear="right">

### And the small things

- **Many sessions, one bot.** A plain message goes to the session you last talked with; reply to one of a session's messages to talk to that one, or send `/x1 <message>`.
- **Nothing gets mixed up.** A message sent while a prompt or a question is open waits until you answer, so it is never read as the answer.
- **Photos, files and voice.** Voice messages are turned into text on your computer, with the optional `voice` extra.
- **It tells you.** Context warnings, compaction, a session that ended and one that went quiet.
- **Sessions outlive everything.** Claude runs inside `dtach`, so it keeps working after you close the terminal, lose SSH or restart the daemon.

## How it works

<img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/how-it-works.webp" width="100%" alt="Claude Code, its hook, the aipager daemon, Telegram and your chats, in a row">

Claude Code runs in a real terminal, inside `dtach`, so nobody has to keep a window open. Each step fires a [hook](https://github.com/dev-aly3n/aipager/blob/main/docs/hooks.md) that sends one datagram to the aipager daemon over a local socket, and the daemon edits a message in your chat. Your answers travel back the same way: a verdict to the hook holding the call, or keystrokes into the session. The terminal stays the source of truth. More in [the architecture](https://github.com/dev-aly3n/aipager/blob/main/docs/architecture.md).

## Install

Linux or macOS, any architecture. You need Claude Code, logged in on this computer, and a Telegram bot: create one with [@BotFather](https://t.me/BotFather) and press Start in it.

```sh
curl -fsSL https://aipager.run/install | sh   # uv, pipx or Homebrew: whichever you have
aipager config                                # your bot token, your chat, Claude Code's hooks
aipager service install                       # keeps the daemon running after you log out
aipager session api                           # Claude Code in this folder, and in your chat
```

The installer uses `uv` if you have it, else `pipx`, else (on macOS) Homebrew, and installs `uv` when none is there. `dtach` comes with aipager (the [`dtach-bin`](https://pypi.org/project/dtach-bin/) package), so there is nothing else to install. `aipager config` is a wizard: it checks the bot token and your chat, then adds aipager's hooks to `~/.claude/settings.json` for you. You never edit a file by hand.

<details>
<summary><b>Let a coding agent install it for you</b></summary>

<br>

Have Claude Code, or any agent with a shell? Create the bot, press Start in it, and save the bot token to a file your agent never has to read (paste the token, press Enter, then Ctrl-D):

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

If you do not know your Telegram user id, say so instead; the agent can find it. The guide it follows, including how to hand over the token safely, is [docs/install-with-an-agent.md](https://github.com/dev-aly3n/aipager/blob/main/docs/install-with-an-agent.md). For scripts, `aipager setup --token-file FILE --chat-id ID` sets aipager up with no questions ([the setup command](https://github.com/dev-aly3n/aipager/blob/main/docs/commands.md#command-line-aipager-setup-for-coding-agents)).

</details>

### Other ways to install

```sh
uv tool install aipager                        # uv: recommended on macOS, it brings its own Python
pipx install aipager                           # pipx
brew install dev-aly3n/tap/aipager             # Homebrew, on macOS and Linuxbrew (see the note below)
nix profile install github:dev-aly3n/aipager   # Nix: claude is not included (see below)
snap install aipager                           # Snap: strict confinement, claude inside, workspaces under ~/
```

Or run it in [Docker](#docker), with Python, Node, `claude` and `dtach` inside.

<details>
<summary><b>Homebrew on macOS 26 (Tahoe)</b></summary>

<br>

The formula works when Homebrew's `python@3.12` bottle and your Xcode Command Line Tools are in sync, but they drift apart from time to time. On macOS 26 the install fails with a `pyexpat _XML_SetAllocTrackerActivationThreshold` symbol error ([upstream issue](https://github.com/Homebrew/homebrew-core/issues?q=_XML_SetAllocTrackerActivationThreshold)). Updating Xcode and the Command Line Tools usually fixes it, but `uv tool install aipager` avoids it altogether. The formula takes `dtach` from Homebrew and installs aipager into a Homebrew-managed Python environment.

</details>

### Docker

A self-contained image with Python, Node, `claude` and `dtach`, good for a VPS, a NAS or a Raspberry Pi where you do not want a Python or Node toolchain on the host. Multi-arch (amd64, arm64).

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

Mount the folders you want Claude to edit under `/workspace`. The `~/.claude` mount carries your Claude credentials and conversation history: run `claude` on the host once to log in, or `docker exec -it aipager claude` to log in inside the container.

Tags: `latest`, `1.3`, `1.3.6` (semver track + minor track).

### Nix

```sh
nix run github:dev-aly3n/aipager -- --version
nix profile install github:dev-aly3n/aipager
```

Builds aipager from source against pinned nixpkgs. `dtach` comes from Nix; `claude` does not, so install it separately (`nix profile install nixpkgs#nodejs && npm install -g @anthropic-ai/claude-code`, or follow Anthropic's docs).

<details>
<summary><b>NixOS and Home Manager</b></summary>

<br>

Add aipager as a flake input and take its package into `environment.systemPackages`:

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

`aipager service install` then sets up a systemd user unit.

</details>

## Everyday use

### In Telegram

| Send | To |
|---|---|
| `/status` | See this chat's sessions, each with its state and buttons. |
| `/new x1` | Start a session named x1. `/new x1 fix the tests` also sends its first message. |
| `/x1` | Make x1 the session your messages go to. `/x1 <message>` sends a message straight to it. |
| `/stop` | Interrupt the running turn and drop what is queued. |
| `/mode` | Switch between Ask, Auto and Full access. |
| `/app` | Open the Mini App. |
| `/language` | Pick the language aipager speaks in this chat. |
| `/update` | Update aipager and Claude Code, then restart in place (admins). |
| `/help` | A short guide by task. |

Every command, button and setting is in [docs/commands.md](https://github.com/dev-aly3n/aipager/blob/main/docs/commands.md).

### On the command line

```sh
aipager start                             # the daemon, in the foreground
aipager session dev                       # start a session named dev here, or attach to it
aipager session dev --continue            # pick up the last Claude conversation in this folder
aipager session dev -- --resume abc1234   # any other claude flag goes through as it is
aipager doctor                            # check the install
```

To leave a session without stopping Claude, close the terminal or the tmux pane: the session keeps running, and the same command attaches to it again.

`aipager service install` keeps the daemon running in the background: a systemd user unit on Linux (`~/.config/systemd/user/aipager.service`) or a launchd agent on macOS (`~/Library/LaunchAgents/com.aipager.daemon.plist`). Either way the daemon runs in your home folder, where a new session starts unless you pick another folder. `aipager service start`, `stop`, `status`, `logs` and `uninstall` manage it.

## Security

- **No aipager server.** The daemon talks to the Telegram Bot API with your own bot token. There is no aipager account and no aipager service.
- **No open port.** The daemon listens on loopback only. The Mini App goes out through a Cloudflare tunnel where every request must carry Telegram's signature, and `aipager miniapp disable` turns it off.
- **Every answer on the record.** Each Allow, Deny and answer is appended to `~/.claude/aipager-audit.jsonl`, with who tapped it in a group.
- **Claude Code still decides.** Your Claude Code settings say what needs asking. aipager relays the question and never elevates: no sudo, and everything runs as your user.

The full picture, including what protects your Claude credential, is in [the security model](https://github.com/dev-aly3n/aipager/blob/main/docs/security.md).

## FAQ

<details>
<summary><b>How is this different from Claude's mobile apps?</b></summary>

<br>

Claude's apps talk to Anthropic's hosted assistant. aipager connects you to a Claude Code session running on your own computer: your repository, your tools, your environment.

</details>

<details>
<summary><b>Does my code leave my computer?</b></summary>

<br>

aipager sends to Telegram only what you see in your chats, with your own bot token; there is no aipager server in between. Claude Code itself talks to Anthropic as it always does.

</details>

<details>
<summary><b>What is dtach?</b></summary>

<br>

A tiny terminal detacher. It keeps Claude Code alive after the terminal closes or SSH drops, so an answer you send an hour later still reaches a live session. It comes with aipager.

</details>

<details>
<summary><b>What if Claude goes off the rails?</b></summary>

<br>

Prompts wait for your tap, `/stop` interrupts the turn and drops anything queued, `/kill` ends the session, and buttons from a finished turn refuse.

</details>

<details>
<summary><b>How do I update it?</b></summary>

<br>

Send `/update`: it checks aipager and Claude Code and installs what is new, and 🔄 Restart now brings the daemon back, under a service or not. Your sessions keep running through it. On the command line, `aipager update` upgrades through the installer that owns aipager.

</details>

<details>
<summary><b>I use Bedrock or Vertex. Do the model buttons work?</b></summary>

<br>

The keyboard's **Model** menu and the Mini App offer Claude Code's family aliases (`sonnet`, `opus`, `haiku`, `fable`, `opusplan`) and pinned models such as `claude-opus-5-5` and `claude-sonnet-5-5`. On the Anthropic API an alias resolves to the latest model in its family. On Bedrock and Vertex the same alias may resolve to an older snapshot, depending on what your provider offers; send `/model <full-id>` to pick an exact model.

</details>

<details>
<summary><b>What does it cost?</b></summary>

<br>

Nothing. aipager is MIT licensed, the bot is yours, and Claude Code keeps using whatever plan or key it already uses.

</details>

## Documentation

| Page | What is in it |
|---|---|
| [Commands](https://github.com/dev-aly3n/aipager/blob/main/docs/commands.md) | Every command, button, setting and command-line option |
| [Groups and team mode](https://github.com/dev-aly3n/aipager/blob/main/docs/groups.md) | Roles, root folders, who can tap what, the trust model |
| [Security](https://github.com/dev-aly3n/aipager/blob/main/docs/security.md) | The trust boundary, secrets, the audit log, the network |
| [Hooks](https://github.com/dev-aly3n/aipager/blob/main/docs/hooks.md) | What each Claude Code hook does in aipager |
| [Architecture](https://github.com/dev-aly3n/aipager/blob/main/docs/architecture.md) | The daemon's parts, files and sockets |
| [Troubleshooting](https://github.com/dev-aly3n/aipager/blob/main/docs/troubleshooting.md) | `aipager doctor` and fixes for common problems |
| [Problem reports](https://github.com/dev-aly3n/aipager/blob/main/docs/problem-reports.md) | What a report contains, and when one is offered |
| [Install with an agent](https://github.com/dev-aly3n/aipager/blob/main/docs/install-with-an-agent.md) | The guide a coding agent follows |

## Contributing

```sh
git clone https://github.com/dev-aly3n/aipager.git && cd aipager
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

`dtach` comes with the `dtach-bin` dependency, so nothing else is needed. Run the tests under a memory cap as above (on macOS, without `systemd-run`): a runaway test can otherwise take the machine's memory. The release process is in [CONTRIBUTING.md](https://github.com/dev-aly3n/aipager/blob/main/CONTRIBUTING.md).

## License

MIT. See [LICENSE](https://github.com/dev-aly3n/aipager/blob/main/LICENSE).

<br>

<div align="center">
<a href="https://aipager.run"><img src="https://raw.githubusercontent.com/dev-aly3n/aipager/main/docs/assets/logo.png" width="32" height="32" alt=""></a>
<br>
<sub><a href="https://aipager.run">aipager.run</a></sub>
</div>
