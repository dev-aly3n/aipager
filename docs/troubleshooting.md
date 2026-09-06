# Troubleshooting

Common failures and the fix. When in doubt, run:

```sh
aipager doctor
```

It runs every check below in order and prints the canonical fix
hint for each failure. Source: `aipager/doctor.py`.

## "Another aipager daemon already owns the socket"

Two daemons can't share one control socket. It lives at
`$XDG_RUNTIME_DIR/aipager.sock` under the systemd service (falls back
to `/tmp/aipager.sock` when `$XDG_RUNTIME_DIR` is unset). If a
previous daemon crashed without unlinking, the new one detects the
stale socket and exits.

```sh
pkill -f 'aipager start'
rm -f "${XDG_RUNTIME_DIR:-/tmp}/aipager.sock"
aipager start
```

Or, if you're using the service unit:

```sh
aipager service stop
aipager service start
```

## Telegram bot doesn't respond

1. `aipager status` — is the daemon up? If "daemon down" → start it.
2. `aipager doctor` — the `check_token_valid` and
   `check_chat_reachable` checks ping the Telegram API end-to-end
   and surface the exact error.
3. Wrong chat ID: re-run `aipager config` and re-enter the chat ID
   (open the bot in Telegram, send any message, then check
   `https://api.telegram.org/bot<TOKEN>/getUpdates`).
4. Bot was never `/start`ed: open the bot in Telegram and tap Start
   once.

## Session shows GONE in pinned status

The dtach process for that session exited (machine reboot,
`pkill claude`, user typed `exit` in the dtach attach session).
Recreate from scratch — its `~/.claude/projects/...` directory still
has the conversation:

The easiest path is `/resume <label>` in Telegram (or bare
`/resume` for a picker) — it relaunches the session and picks the
conversation back up. From the CLI:

```sh
aipager resume <label>             # same thing from the terminal
aipager session <label>            # or a fresh session, no history
aipager session <label> --resume   # fresh dtach, resumed conversation
```

## Permission prompt stuck on INTERACTIVE

If a session sits in INTERACTIVE with no hook activity for >5 min,
the session monitor auto-demotes it to BUSY and clears the pending
permission. This catches the case where claude code crashed
mid-prompt and the user can never respond.

Tune the timeout:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # in seconds
```

## Voice extra won't install via Telegram

The `[📦 Install voice]` button runs an installer subprocess and
streams the result. If the install fails, the bot replies with the
last 500 chars of stderr. Common causes:

- Network: pip can't reach PyPI. Check connectivity from the daemon
  host.
- Disk: `~/.cache/huggingface/` needs ~74 MB for the model and
  pip's wheel cache another ~250 MB. Free space first.
- Permission denied: read-only venv. Switch to `uv tool install`
  which always uses a user-owned venv.

If the bot lost connection mid-install, the install itself usually
completed on the host — restart the daemon and try a voice message
again.

## `pyexpat _XML_SetAllocTrackerActivationThreshold` on brew install

Homebrew's `python@3.12` bottle was compiled against a newer
libexpat than your system has — almost always because **Xcode and
Command Line Tools are out of date** on macOS Tahoe (26.x). Brew's
own output usually tells you so. Two fixes:

- **Use `uv tool install aipager`** instead. uv bundles its own
  python, dodging the issue entirely. This is the recommended path
  on macOS — see the [README](../README.md#install).
- **Update Xcode + Command Line Tools**:
  ```sh
  sudo rm -rf /Library/Developer/CommandLineTools
  sudo xcode-select --install
  ```
  Or open Xcode in the App Store and update to the latest.

## "ModuleNotFoundError: aipager"

Your daemon binary references a Python that no longer has aipager
installed (often after a venv wipe). Reinstall:

```sh
uv tool install --reinstall aipager
# or whichever installer you started with: pipx, brew, pip
```

## Daemon crashes on boot with `KeyError` in `state.py`

State file got corrupted (interrupted write). The daemon doesn't
auto-recover destructive corruption — restore the latest backup:

```sh
ls -la ~/.claude/aipager-sessions.json.bak.*
# pick the most recent, then:
cp ~/.claude/aipager-sessions.json.bak.<timestamp> \
   ~/.claude/aipager-sessions.json
aipager start
```

If no backup is recoverable, you can safely delete the state file —
the daemon will recover live sessions by scanning
`/tmp/claude-dtach-*.sock` on first monitor tick.

## A session launch fails

A launch dtach refuses shows a one-line reason in chat (socket already
exists, shell not executable, no pseudo-terminal, name too long,
permission denied, missing directory); dtach's own message, paths
included, is in `aipager logs`.

## `aipager doctor` check list

The order matters — each later check assumes earlier ones passed.
A check that crashes on an unexpected environment shows as a single
⚠ row naming the check and the error; the remaining checks still run.

| Check | What it verifies | Fix hint |
|---|---|---|
| `check_config` | `~/.config/aipager/aipager.yaml` exists and has token + chat ID | `aipager config` |
| `check_token_valid` | Token works against Telegram `getMe` | re-run `aipager config` |
| `check_chat_reachable` | Bot can send to the configured chat | open bot, tap Start |
| `check_dtach` | `dtach` binary on PATH | `uv tool install --reinstall aipager` |
| `check_claude` | Resolves the `claude` binary via the same precedence chain every launch uses (`claude_path` config → `$AIPAGER_CLAUDE_BIN` → `~/.local/bin` → PATH → Homebrew), and lists every OTHER distinct install found | install Claude Code, or set `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `check_claude_auth` | Probes `claude auth status` in the same environment a real session gets. **Never FAILs** — "not logged in" and "the probe itself failed" are reported distinctly, and neither stops a session from launching | `claude auth login`, or set an API key / `CLAUDE_CODE_OAUTH_TOKEN` |
| `check_settings_json` | `~/.claude/settings.json` has the aipager hooks wired up | `aipager config` |
| `check_hook_scripts` | `aipager-hook` and `aipager-statusline` are on PATH | `uv tool install --reinstall aipager` |
| `check_daemon` | Daemon is running and socket is responsive | `aipager start` |
| `check_service_installed` | Optional: service unit is present | `aipager service install` |
| `check_service_unit_path` | Linux only: the *installed* unit's `Environment=PATH=` actually contains the resolved claude binary's directory — parsed as text, since doctor cannot see systemd's own PATH from the operator's interactive shell | `aipager service install --yes` |

Run `aipager doctor --fix` to interactively discover/copy a Claude
credential into `daemon.env`, or pin `claude_path` when multiple
installs are found (or the unit's PATH disagrees with the resolved
one). It only ever acts after asking.

## The daemon can't find `claude`, or picks the wrong install

Six places used to resolve `claude` independently and could disagree
with each other. They now all go through one resolver
(`aipager/claude_resolve.py`), in this order: `claude_path` in
`aipager.yaml` → `$AIPAGER_CLAUDE_BIN` → `~/.local/bin/claude` →
every `claude` on `$PATH` → the fixed Homebrew prefixes. Run
`aipager doctor` to see exactly which one it picked and what else it
found; if the wrong one wins, either fix `$PATH` for the process that
launches aipager, or pin the right one explicitly:

```sh
aipager doctor --fix        # interactive picker among discovered installs
# or by hand:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## Sessions can't authenticate under the systemd service, but `claude` works fine in a terminal

`systemctl --user` units never source `~/.bashrc` / `~/.profile`, so
an `export CLAUDE_CODE_OAUTH_TOKEN=…` line there never reaches the
daemon. The service unit instead uses systemd's `LoadCredential=`,
reading `~/.config/aipager/daemon.env` — a plain `KEY=VALUE` file,
0600, created automatically the first time you run
`aipager service install` (copied forward from a legacy `config.env`
if one held a token, otherwise discovered from your login shell once,
otherwise left empty with a warning).

```sh
cat ~/.config/aipager/daemon.env          # see what's there (or isn't)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

See [Security model — The Claude credential](security.md#the-claude-credential--what-actually-protects-it)
for what this file does and doesn't protect against.

## The daemon's control socket moved

`aipager doctor` / `aipager status` used to always look at
`/tmp/aipager.sock`. Under the systemd service the control socket now
lives at `$XDG_RUNTIME_DIR/aipager.sock` (falling back to `/tmp` only
when `$XDG_RUNTIME_DIR` is unset — containers, WSL1, minimal distros).
Only this one socket moved; per-session dtach sockets
(`/tmp/claude-dtach-*.sock`) are unaffected. Override with
`AIPAGER_SOCKET_PATH` if you need a specific location.

## Still stuck?

Open an issue at
[github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues)
and include the output of `aipager doctor` plus the last ~50 lines
of `aipager logs`.

## See also

- [Architecture](architecture.md) — what each component does.
- [Security model](security.md) — what aipager touches and doesn't.
