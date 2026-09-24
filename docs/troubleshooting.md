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

## Busy cards got slower

Telegram allows roughly one message a second into any one chat (and 20 a
minute into a group). Every message and every edit counts. The daemon
gives each chat its own budget of about 1 call a second, with a small
burst, and the busy cards of that chat **share** it. (The "typing…"
indicator is *not* in that budget — Telegram does not count chat actions
with messages, which is measured, not assumed — so a working session
shows the bubble in your chat list without ever slowing its card. It is
refreshed on its own schedule, every 4.5 seconds per working session,
because Telegram clears a typing status after 5; `TYPING_INDICATOR_INTERVAL`
tunes that, and `0` turns the bubble off.)

So with two sessions working in the same chat, each card refreshes about
every 2.2 seconds instead of every 1.2; with three, about every 3.3. **A
slower card is the budget working, not a bug.** Answers, replies and
button responses are never slowed to make room for a card — they keep a
reserved token, and a card edit that cannot afford a call is simply
skipped and retried on the next tick rather than queued in front of your
answer.

In a **group** the limit is 20 calls a minute however many sessions are
in it, so a card there refreshes every 3.3 seconds whatever else is going
on.

To speed the cards up: run fewer simultaneous sessions per chat, or give
the busiest ones a chat of their own (`aipager config`). You can also
tune `STREAM_EDIT_INTERVAL` (default `1.2`, the cadence while the card is
streaming text) and `BUSY_EDIT_INTERVAL` (default `3.0`, when it is
quiet) in your config. Setting either *below* the per-chat floor is
harmless and changes nothing — the floor wins, which is what keeps the
chat under Telegram's limit whatever you put in the file.

None of this affects the "typing…" bubble. It has a schedule of its own,
one refresh every `TYPING_INDICATOR_INTERVAL` seconds (default 4.5) for as
long as a session is working, however slow that session's card happens to
be — so a chat with three busy sessions refreshes the bubble exactly as
often as a chat with one, while its cards refresh every 3.3 seconds.
Telegram clears a typing status after 5 seconds, which is why the default
sits just under that; setting it higher leaves gaps between refreshes.

## The bot slowed down: a rate limit (429)

If Telegram rate-limits a chat anyway, it answers one call with `429` and
a `retry_after` of a few seconds. The daemon stops calling that chat for
exactly that long, makes the one deferred send again afterwards, and
**doubles that chat's card interval** (up to ×8). Each quiet minute halves
it back to normal.

What you see:

- `aipager status` (and the `aipager daemon` row of `aipager doctor`)
  shows **`Telegram flood backoff ×4 (chat …), last 429 12 s ago`**.
- Exactly one line in `aipager logs`, with no traceback:
  `flood: chat … 429 retry_after=5s → cadence ×2`.
- Nothing is muted, and **no answer is lost** — it is deferred, not
  dropped.
- The chat's **earned send rate halves**, and `aipager status` shows it:
  `Telegram chat 123: rate 0.25/s, 7/30 in the last minute`.

Nothing to do. It clears itself. The rate climbs back by 0.1 calls/s for
every quiet minute, the backoff line disappears once the chat has been
quiet for a minute or two, and `aipager doctor` keeps the daemon row
green throughout: a chat backing off is normal operation.

### The earned rate, and why your cards may be slower than they were

Since 0.7.13 each chat's sustained send rate is **learned rather than
assumed**. Telegram does not publish the limit that actually applies to
your bot — it depends on your account's recent history — so the daemon
starts each chat at 0.5 calls/s, adds 0.1 for every quiet minute up to a
ceiling of 1/s, halves it on a 429 and drops it to 0.05 after a ban. A
bot that has just been banned is therefore paced far more carefully than
one that has not, and it earns its speed back over the following hours.

If a chat's rate falls below 0.2 calls/s it enters **minimal mode**: busy
cards stop animating and show one static `⏳ working — updates paused`
line, the typing bubble stops, and the pinned dashboard stops refreshing.
**Answers, replies and permission prompts keep flowing** — that is the
point. The card is about 95 % of what this bot sends and the answer about
5 %, so under pressure it sheds pixels rather than work. `aipager doctor`
reports minimal mode as a warning so a paused card is never mistaken for
a broken one; it lifts by itself as the rate recovers.

## The bot went quiet: flood control

A `retry_after` past `TELEGRAM_MAX_RETRY_AFTER` (default 90 s) is not a
rate limit — it is a **ban**, and it can run to hours. From the chat it
looks like the bot died: prompts still reach Claude, sessions keep
working, but no reply comes back.

What you see:

- `aipager status` (and the `aipager daemon` row of `aipager doctor`)
  shows **`Telegram flood-muted until HH:MM (chat …)`**.
- One line in `aipager logs`: `Telegram flood control — chat … muted
  for Ns (until HH:MM) …`, then silence for that chat. Later, one
  `flood mute on chat … lifted` line.
- `aipager status` also shows the chat's state in full:
  `Telegram chat 123: rate 0.05/s — MINIMAL MODE, card updates paused —
  flood-muted until 09:41 (1 ban(s) in the last 24 h)`.

The daemon mutes the chat for exactly the time Telegram asked and makes
**no call of any kind** to it — answers, busy-card edits, attachments,
reactions, typing indicators, button-tap toasts and the replies your
commands would have produced. Each one would be a fresh violation that
extends the ban; a request into an active ban is what turned a 21-minute
ban into a 9.5-hour one on 2026-09-15. A command typed during a mute
therefore answers with nothing at all, and **a button tap is not
acknowledged either** — up to 0.7.12 the toast still fired, on the theory
that Telegram meters it separately. It does, for pacing; it does not for
bans. Other chats are unaffected.

**Answers are not lost.** An answer produced while the chat is muted is
held and delivered once the ban lifts, within a couple of seconds, with
its first line reading `⏳ delivered late (held 42 min during a Telegram
rate limit)`. You do not need to ask again — and if you did ask again,
both answers arrive, oldest first: a ban lasting hours spans several
turns and each one's answer is yours. (Held answers live in memory: if
you restart the daemon while any are waiting, they are lost — the
shutdown log says how many. A chat holds at most 20, and nothing older
than 24 hours; anything dropped for either reason is a warning in the
log naming what it was.)

**A ban makes the chat slower afterwards, not faster.** The moment a ban
is armed the chat's learned rate drops to the floor and the ban is
counted; the muted hours earn nothing back, the climb restarts from the
moment the ban lifts, and for 24 hours after a ban the chat may climb to
only half its normal ceiling. `aipager status` shows all of it.

What NOT to do:

- Don't restart the daemon to "fix" it. The mute self-clears at the time
  shown. Since 0.7.13 a restart no longer forgets the ban — the deadline,
  the chat's earned rate and its ban history are persisted to
  `~/.claude/aipager-flood-state.json` and restored on start — so
  restarting no longer extends it either. But it will throw away any
  answers still held, and it fixes nothing.
- Don't lower `TELEGRAM_MAX_RETRY_AFTER` below the default 90 s hoping
  to retry sooner — everything past that cap is a ban, not a rate limit.

To avoid it: run fewer simultaneous sessions per chat, or give the
busiest ones a chat of their own (`aipager config`).

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

## Open App shows "Open this page from the Telegram app to sign in"

The page loaded, but Telegram's Mini App SDK script (the one that
produces `initData`) did not, so the page has nothing to sign in with
and makes no API call at all. The message blames you; the cause is a
network one.

Recent versions of aipager fetch that script themselves and serve it
from the page's own origin (`/telegram-web-app.js`), so the phone only
needs to reach the host that delivered the page — not `telegram.org` as
well. If you see this on an older version, upgrade.

Then, in order:

1. Look at the page source. If its `<script>` tag points at
   `https://telegram.org/js/telegram-web-app.js`, the daemon has not
   managed to fetch the script yet and the page is falling back to
   loading it from Telegram — which is the case that fails on a phone
   that cannot reach `telegram.org`. The daemon fetches it when the Mini
   App server starts and retries at most once a day, so restarting the
   daemon retries immediately; `journalctl --user -u aipager | grep
   "webapp sdk"` says what went wrong (a blocked or filtered
   `telegram.org`, usually).
2. If the tag points at `/telegram-web-app.js`, check the daemon serves
   it: `curl -sI http://127.0.0.1:8765/telegram-web-app.js` on the
   daemon host (adjust the port if you changed it) should answer `200`
   with `Content-Type: application/javascript`. The copy lives under
   `~/.local/share/aipager/webapp-sdk/`.
3. You opened the URL in a regular browser rather than from the bot's
   `/app` button or menu button: that is the expected message. The SDK
   only produces `initData` inside Telegram.
4. The page was left open for more than five minutes: `initData`
   expires and the page asks to be reopened. Tap `/app` again.

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

## `/update` says the restart would kill sessions (KillMode)

Service units written before this version have no `KillMode=` line, so
systemd uses `control-group`: stopping or restarting `aipager.service`
kills every Claude session the daemon launched. `/update` therefore
installs the new version but does not restart the daemon, and lists the
sessions a restart would take down. Fix it once:

```
aipager service install
```

It rewrites the unit with `KillMode=process`, reloads systemd, and
restarts the daemon — and that restart already runs under the new
setting. Check with
`systemctl --user show -p KillMode aipager.service` (`KillMode=process`).
With it, systemd logs "left-over process" lines for your sessions on
every restart; that is expected.

## `aipager update` can't find uv / pipx

`aipager update` finds the installer by absolute path in your `PATH`
plus `~/.local/bin`, `~/.cargo/bin`, the Homebrew prefixes, `/usr/bin`
and `/bin`. If it still says it could not find `pipx`, the installer
that created this aipager is gone or lives elsewhere; reinstall with the
tool you have (`pipx install --force aipager`,
`uv tool install --reinstall aipager`).

## Update refused: editable / not your install

`/update` and `aipager update` only upgrade an install owned and
writable by your own user, through the installer that created it. They
refuse, and name the reason, for:

- an editable (development) checkout — update it with `git pull`;
- a Nix, Snap, Docker or OS-package install — use that system's own
  update;
- an install owned by another user (for example a root-owned venv under
  `/opt`) — update it as that user.

## Updated, but the version didn't change

A pipx install made from a local path (`pipx install /path/to/aipager`)
upgrades from that same path, not from PyPI. `/update` says
`already at A — this pipx install upgrades from the local path …`. Pull
or check out the new version there first, or switch to PyPI with
`pipx install --force aipager`. (The voice extra's install button uses
`pipx install --force aipager[voice]`, which also switches a local-path
install to PyPI.)

## The daemon didn't come back after an update

The upgrade checks that the new version imports before any restart, but
if the new daemon still fails to start, systemd keeps retrying every
5 s. Look at why:

```
journalctl --user -u aipager.service -n 50
```

Reinstall the previous version with the same installer, then restart:

```
pipx install --force aipager==<previous>        # or:
uv tool install --force aipager==<previous>     # or:
<venv>/bin/python -m pip install aipager==<previous>
systemctl --user restart aipager.service
```

A local-path pipx install: `pipx install --force /path/to/aipager` at
the old commit. If a stale announcement is pending, remove it with
`rm -f ~/.local/share/aipager/update-restart.json` (it is ignored after
24 h anyway); `~/.local/share/aipager/update.lock` is only a lock file
and is safe to delete when no update is running.

## "An update was interrupted when aipager shut down"

The daemon stopped (or was restarted) while `/update` was installing, and
the installer was stopped with it, so the install may be half-written. If
aipager or Claude Code misbehaves, repair it: for aipager, run the
reinstall command from the message (for example
`pipx install --force aipager`) and then
`systemctl --user restart aipager.service`; for Claude Code, run
`claude update` again.

## Still stuck?

Open an issue at
[github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues)
and include the output of `aipager doctor` plus the last ~50 lines
of `aipager logs`.

## See also

- [Architecture](architecture.md) — what each component does.
- [Security model](security.md) — what aipager touches and doesn't.
