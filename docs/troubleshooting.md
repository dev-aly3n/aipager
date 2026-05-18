# Troubleshooting

Common failures and the fix. When in doubt, run:

```sh
aipager doctor
```

It runs every check below in order and prints the canonical fix
hint for each failure. Source: `aipager/doctor.py`.

## "Another aipager daemon already owns the socket"

Two daemons can't share `/tmp/aipager.sock`. If a previous daemon
crashed without unlinking, the new one detects the stale socket and
exits.

```sh
pkill -f 'aipager start'
rm -f /tmp/aipager.sock
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

```sh
aipager session <label>            # new dtach + claude
# or via Telegram:
/new <label>
```

To recover the prior conversation, attach interactively once with
`--resume`:

```sh
aipager session <label> --resume
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

## `aipager doctor` check list

The order matters — each later check assumes earlier ones passed.

| Check | What it verifies | Fix hint |
|---|---|---|
| `check_config` | `~/.config/aipager/config.env` exists and has token + chat ID | `aipager config` |
| `check_token_valid` | Token works against Telegram `getMe` | re-run `aipager config` |
| `check_chat_reachable` | Bot can send to the configured chat | open bot, tap Start |
| `check_dtach` | `dtach` binary on PATH | `uv tool install --reinstall aipager` |
| `check_claude` | `claude` binary on PATH | install Claude Code |
| `check_settings_json` | `~/.claude/settings.json` has the aipager hooks wired up | `aipager config` |
| `check_hook_scripts` | `aipager-hook` and `aipager-statusline` are on PATH | `uv tool install --reinstall aipager` |
| `check_daemon` | Daemon is running and socket is responsive | `aipager start` |
| `check_service_installed` | Optional: service unit is present | `aipager service install` |

## Still stuck?

Open an issue at
[github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues)
and include the output of `aipager doctor` plus the last ~50 lines
of `aipager logs`.

## See also

- [Architecture](architecture.md) — what each component does.
- [Security model](security.md) — what aipager touches and doesn't.
