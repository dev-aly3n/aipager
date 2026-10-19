# Troubleshooting

Common failures and the fix. When in doubt, run:

```sh
aipager doctor
```

It runs every check below in order and prints the canonical fix
hint for each failure. Source: `aipager/doctor.py`.

## "Another aipager daemon already owns the socket"

Two daemons can't share one control socket. This error means a daemon
is still running and answering on it (the socket is
`$XDG_RUNTIME_DIR/aipager.sock`, or `/tmp/aipager.sock` when
`$XDG_RUNTIME_DIR` is unset). A socket file left behind by a daemon that
crashed is not a problem: the next start removes it and binds a new
one. Stop the running daemon first:

```sh
aipager service stop          # if it runs as the service
pkill -f 'aipager start'      # otherwise
```

If the socket check misses it, `aipager start` still refuses with
`aipager is already running (pid=N)` and names its lock file
(`~/.local/share/aipager/daemon.lock`): stop that process the same way.

## Telegram bot doesn't respond

1. `aipager status`: if the daemon row says `not running`, start it.
2. `aipager doctor`: the `token_valid` and `chat_reachable` checks
   ping the Telegram API end-to-end and surface the exact error.
3. Wrong chat: run `aipager config` and pick **Test bot reachability**.
   To fix a wrong DM, add the right one with **Add a DM scope** (see
   [finding ids](groups.md#finding-ids-auto-detect)), then remove the
   wrong one under **Edit a scope**.
4. Bot was never `/start`ed: open the bot in Telegram and tap Start
   once.

## Busy cards got slower

Telegram allows roughly one message a second into any one chat (and 20 a
minute into a group). Every message and every edit counts. The daemon
gives each chat its own budget of about 1 call a second, with a small
burst, and the busy cards of that chat **share** it. The "typing…"
indicator is in that budget too, as the **lowest** thing the chat sends
(see "The typing bubble" below).

The bubble's share is set aside first - one call every 4.5 seconds
while the chat's oldest working turn is under ten minutes old, less as it
ages (see "The typing bubble") - and the cards divide what is left.
So a single card in a DM refreshes about every 4.8 seconds; with two
sessions working in the same chat, each card about every 9.7 seconds;
with three, about every 14.5. **A
slower card is the budget working, not a bug.** Answers, replies and
button responses are never slowed to make room for a card - they keep a
reserved token, and a card edit that cannot afford a call is simply
skipped and retried on the next tick rather than queued in front of your
answer.

In a **group** the limit is 20 calls a minute however many sessions are
in it, and the bubble alone would use two thirds of that. The group's
cards together keep one refresh every 10 seconds (11 s for one card), and
the bubble takes the rest - so in a group it can lapse for a second or
two now and then.

To speed the cards up: run fewer simultaneous sessions per chat, or give
the busiest ones a chat of their own (`aipager config`). You can also
tune `STREAM_EDIT_INTERVAL` (default `1.2`, the cadence while the card is
streaming text) and `BUSY_EDIT_INTERVAL` (default `3.0`, when it is
quiet), as environment variables of the daemon (see
[settings in the environment](commands.md#settings-in-the-environment)).
Setting either *below* the per-chat floor is harmless and changes
nothing: the floor wins, which is what keeps the chat under Telegram's
limit whatever you set.

### A long turn's card refreshes less often

The cadence above is for the first two minutes of a turn. After that the
card **slows down as the turn gets older**, and from 10 minutes its
clock moves in 30 s steps, matching the refresh, so it never looks frozen:

| turn age | card refreshes at most every | clock reads |
|---|---|---|
| 0–2 min | as above (about 4.8 s for one card in a DM) | `0:45` |
| 2–10 min | 10 s | `4:10` |
| 10–60 min | 30 s | `23:00`, `23:30` |
| over 60 min | 30 s | `1:23:00`, `1:23:30` |

The clock is rounded down to its 30 s step, and refreshes are at least
30 s apart, sometimes a little more. So the card skips a step now and
then: a refresh at 23 min 29 s shows `23:00`, and if the next one comes
at 24 min 1 s it shows `24:00`, so `23:30` is skipped.

A new tool row or a new sentence waits for the next refresh; a **state
change** - the session going from working to waiting on a background
agent, and back - is shown at once, at most once every 10 seconds. The
live agent rows and the waiting line use the same steps. The finished
card shows the turn's duration in true seconds (`23:17`).

This is what keeps a four-hour turn from editing one message thousands of
times: on 2026-09-23 one card did exactly that, and the chat was banned
for seven hours. To keep the fast cadence for a whole turn, switch off
**`/settings` → ⏱ Long-turn card updates** (for the chat, or per session
from the Mini App or `👤 Per-session preferences`).

### The typing bubble

The "typing…" bubble is sent by **one** loop per chat, however many
sessions are working in it (the bubble is per chat in every Telegram
client), every `TYPING_INDICATOR_INTERVAL` seconds (default 4.5 -
Telegram clears a typing status after 5; `0` turns the bubble off). It
counts in the chat's budget as the lowest thing the chat sends:

- it **slows as the turn gets older**, on the age of the chat's oldest
  working turn: every 4.5 s for the first ten minutes (a steady bubble),
  every 15 s after that - Telegram shows it for 5 s, so an older turn's
  bubble flickers instead of staying lit. At 4.5 s
  it alone would be 800 calls an hour and the hourly limit (below) would
  switch it off for long stretches; switching off **⏱ Long-turn card
  updates** keeps it at 4.5 s;
- it **shows from the start of every turn**; a young card refreshes
  slightly slower to make room - the bubble's share of the chat is set
  aside before the cards divide the rest (above), so the two together
  never exceed the chat's limits;
- it never takes a token a card edit needs: a bubble due just after a
  card edit waits half a second (`TYPING_RETRY_WAKE`) and tries again, so
  it is at most a second or two late, never missing;
- it is the first thing dropped when the chat's hourly volume gets high
  (below);
- a 429 on the bubble itself blocks only the bubble, for as long as
  Telegram asked - the cards carry on - but it does start the chat's
  six-hour warning regime (the rate is capped at 0.5 calls/s, so the
  cards slow down too);
- a **ban** on the bubble (a `retry_after` past the cap below) mutes the
  chat exactly like a ban on any other call: nothing more goes into it,
  and the turn's answer is held and delivered when the ban lifts.

It used to be free (one loop per working session, outside every budget),
until on 2026-09-23 two sessions in one DM had sent it ~5,400 times in
three hours and Telegram rate-limited the bubble itself.

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
- Nothing is muted, and **no answer is lost** - it is deferred, not
  dropped.
- The chat's **earned send rate halves**, and the chat enters a
  **six-hour warning regime** (`FLOOD_WARNING_HOURS`): its rate may not
  climb past 0.5 calls/s (`FLOOD_WARNED_CEILING`), and it climbs back
  slowly. `aipager status` shows it:
  `Telegram chat 123: rate 0.25/s (ceiling 0.50/s), 212/1200 calls in the
  last hour (as of 18s ago), 7/30 in the last minute - warning regime,
  5h 52m left`.

Nothing to do. It clears itself. The backoff line disappears once the
chat has been quiet for a minute or two; the rate climbs back by 0.1
calls/s per quiet window - a minute normally, 36 minutes during the
warning regime - and the full ceiling returns when the regime ends.
`aipager doctor` keeps the daemon row green throughout: a chat backing off
is normal operation. (A 429 used to be forgiven in about five
minutes. Telegram's one warning before the 2026-09-23 ban came 3 h 23 min
earlier, and the chat was back at full speed within minutes of it.)

### The hourly budget

Every chat also has a rolling **hour**: at most `FLOOD_HOURLY_MAX` (1200)
calls in any 60 minutes - typing bubbles, card edits and answers alike;
only reactions are exempt. The last 120 (`FLOOD_HOURLY_ESSENTIAL_RESERVE`)
are kept for answers, replies and prompts:

- at 810 calls in the hour the typing bubble pauses (it comes back under
  648);
- at 1080 the chat enters **minimal mode** (below) until the hour frees
  to 864;
- answers are never refused by the hour; if they ever go past 1200, the
  log says so once an hour.

`aipager status` shows the chat's calls in the last hour against its
budget. The figure comes from the state file, which the daemon rewrites
at most once a minute while calls flow, so it is labelled with its age.
Two busy sessions streaming into one DM for hours stay well under the
budget (the 2026-09-23 incident, replayed: ~900 calls in the busiest
hour, against a modelled ~3,400 for the same work without it).

### The earned rate, and why your cards may be slower than they were

Since 0.7.13 each chat's sustained send rate is **learned rather than
assumed**. Telegram does not publish the limit that actually applies to
your bot - it depends on your account's recent history - so the daemon
starts each chat at 0.5 calls/s, adds 0.1 for every quiet minute up to a
ceiling of 1/s, halves it on a 429 and drops it to 0.05 after a ban. A
bot that has just been banned is therefore paced far more carefully than
one that has not, and it earns its speed back over the following hours.

If a chat's rate falls below 0.2 calls/s - or its hourly budget's share
for cards and bubbles is spent - it enters **minimal mode**: busy cards
stop animating and show one static `⏳ x1 · working (updates paused)` line,
the typing bubble stops, and the pinned status bar shows
`⏸ card updates paused (hourly limit)` (or `(rate limit)` when the rate
is what put the chat there) and otherwise stops changing until it lifts.
**Answers, replies and permission prompts keep flowing** - that is the
point. The card is about 95 % of what this bot sends and the answer about
5 %, so under pressure it sheds pixels rather than work. `aipager doctor`
reports minimal mode as a warning so a paused card is never mistaken for
a broken one; it lifts by itself as the rate recovers.

## The bot went quiet: flood control

A `retry_after` past `TELEGRAM_MAX_RETRY_AFTER` (default 90 s) is not a
rate limit - it is a **ban**, and it can run to hours. From the chat it
looks like the bot died: prompts still reach Claude, sessions keep
working, but no reply comes back.

What you see:

- `aipager status` (and the `aipager daemon` row of `aipager doctor`)
  shows **`Telegram flood-muted until HH:MM (chat …)`**.
- One line in `aipager logs`: `Telegram flood control — chat … muted
  for Ns (until HH:MM) …`, then silence for that chat. Later, one
  `flood mute on chat … lifted` line.
- `aipager status` also shows the chat's state in full:
  `Telegram chat 123: rate 0.05/s (ceiling 0.50/s), 431/600 calls in the
  last hour (as of 12s ago) - MINIMAL MODE, card updates paused -
  flood-muted until 09:41 - warning regime, 5h 59m left (1 ban(s) in the
  last 7 days)`.

The daemon mutes the chat for exactly the time Telegram asked and makes
**no call of any kind** to it - answers, busy-card edits, attachments,
reactions, typing indicators, button-tap toasts and the replies your
commands would have produced. Each one would be a fresh violation that
extends the ban; a request into an active ban is what turned a 21-minute
ban into a 9.5-hour one on 2026-09-15. A command typed during a mute
therefore answers with nothing at all, and **a button tap is not
acknowledged either** - up to 0.7.12 the toast still fired, on the theory
that Telegram meters it separately. It does, for pacing; it does not for
bans. Other chats are unaffected.

**Messaging the bot during a ban is safe, and nothing is lost.** Nothing
you do in the chat makes the bot call Telegram while the chat is muted,
so it cannot extend the ban:

- A prompt you send (text, voice, a file, a quick template) still
  reaches Claude and the session works on it. Its busy card is held
  back, not dropped: if the turn is still running when the ban lifts,
  the card appears then. A ban leaves the chat in minimal mode for a
  while afterwards, so the card can take a few minutes longer than the
  ban itself. If the turn finished during the ban you get its answer
  and no card.
- **🔄 Retry** does nothing during a ban. The prompt is not re-sent and
  the button stays, so you can tap it again after the ban lifts. Up to
  and including 0.7.14 the prompt went to Claude while the error
  message and its button stayed, so a second tap sent it again.
- The main keyboard is held back too, including the one a restart sends
  (up to and including 0.7.14 it was lost until something else refreshed it). It is
  sent after the ban lifts.

After the ban lifts, things arrive in this order: held answers, then
cards for turns still running, then the keyboard. A card that has to
wait for minimal mode to end comes after the keyboard.

**Answers are not lost.** An answer produced while the chat is muted is
held and delivered once the ban lifts, within a couple of seconds, with
its first line reading `⏳ delivered late (held 42 min during a Telegram
rate limit)`. You do not need to ask again - and if you did ask again,
both answers arrive, oldest first: a ban lasting hours spans several
turns and each one's answer is yours. (Held answers live in memory: if
you restart the daemon while any are waiting, they are lost - the
shutdown log says how many. A chat holds at most 20, and nothing older
than 24 hours; anything dropped for either reason is a warning in the
log naming what it was.)

**A ban makes the chat slower afterwards, not faster.** The moment a ban
is armed the chat's learned rate drops to the floor and the ban is
counted; the muted hours earn nothing back, the climb restarts from the
moment the ban lifts, and **for seven days** (`FLOOD_BAN_MEMORY_DAYS`)
the chat's rate ceiling *and* its hourly budget are divided by one plus
the number of bans in that week: one ban halves both, two third them.
`aipager status` shows all of it. (The memory used to be 24 hours
and only halved the ceiling; a chat banned on 2026-09-19 was back at full
speed - and banned for seven hours - on 2026-09-23.) Five or more bans in
a week keep the chat in minimal mode until they age out: answers still
flow, the cards stay paused.

What NOT to do:

- Don't restart the daemon to "fix" it. The mute self-clears at the time
  shown. Since 0.7.13 a restart no longer forgets the ban - the deadline,
  the chat's earned rate and its ban history are persisted to
  `~/.claude/aipager-flood-state.json` and restored on start, and so are
  the chat's last hour of calls and its warning regime - so
  restarting no longer extends it either. But it will throw away any
  answers still held, and it fixes nothing.
- Don't lower `TELEGRAM_MAX_RETRY_AFTER` below the default 90 s hoping
  to retry sooner - everything past that cap is a ban, not a rate limit.

To avoid it: run fewer simultaneous sessions per chat, or give the
busiest ones a chat of their own (`aipager config`).

## A session dropped off the pinned status bar (it is GONE)

The dtach process for that session exited (machine reboot,
`pkill claude`, user typed `exit` in the dtach attach session).
Recreate from scratch - its `~/.claude/projects/...` directory still
has the conversation:

The easiest path is `/resume <label>` in Telegram (or bare
`/resume` for a picker) - it relaunches the session and picks the
conversation back up. From the CLI:

```sh
aipager resume <label>             # same thing from the terminal
aipager session <label>            # or a fresh session, no history
aipager session <label> --continue # fresh dtach, the folder's last conversation
```

## A permission prompt with no news for 5 minutes

If a session waits on a permission prompt or a question and Claude Code
sends no news for 5 minutes, aipager reads the session's saved
conversation. Only Claude's own news and that of the agent that asked
count: a background agent that keeps working does not keep this check
from running. Only if it shows the prompt is over (answered in the
terminal, and the news of it lost) does aipager stop waiting on it and
count the session as working again. Otherwise the prompt stays open
however long it takes: the card and the pinned bar keep saying the
session needs your answer, no "still working" note is sent, and your
messages are held until you answer (see [text](commands.md#text)). A
Claude Code that quits with the prompt open ends the session. The
prompt's buttons still answer the dialog if it is still open (see
[idle responses](commands.md#idle-responses)).

Change the wait:

```sh
AIPAGER_INTERACTIVE_TIMEOUT=300 aipager start    # in seconds
```

## Voice extra won't install via Telegram

The `[📦 Install voice]` button runs the installer and edits its
message every few seconds while it works (`still working… (25s)`). If
the install fails, the message shows the exit code and the last 500
characters of the installer's output. Common causes:

- Network: pip can't reach PyPI. Check connectivity from the daemon
  host.
- Disk: `~/.cache/huggingface/` needs ~74 MB for the model and
  pip's wheel cache another ~250 MB. Free space first.
- Permission denied: read-only venv. Switch to `uv tool install`
  which always uses a user-owned venv.

If the bot lost connection mid-install, the install itself usually
completed on the host - restart the daemon and try a voice message
again.

## Open App shows "Open this page from the Telegram app to sign in"

The page loaded, but Telegram's Mini App SDK script (the one that
produces `initData`) did not, so the page has nothing to sign in with
and makes no API call at all. The message blames you; the cause is a
network one.

Recent versions of aipager fetch that script themselves and serve it
from the page's own origin (`/telegram-web-app.js`), so the phone only
needs to reach the host that delivered the page - not `telegram.org` as
well. If you see this on an older version, upgrade.

Then, in order:

1. Look at the page source. If its `<script>` tag points at
   `https://telegram.org/js/telegram-web-app.js`, the daemon has not
   managed to fetch the script yet and the page is falling back to
   loading it from Telegram - which is the case that fails on a phone
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
5. You tapped `📱 App` on a reply keyboard from an older aipager.
   Telegram gives a Mini App opened from a keyboard button no
   `initData`, so aipager's keyboard no longer has that button. Send
   `/start` for the current keyboard and open the app from the menu
   button or `/app`.

## `pyexpat _XML_SetAllocTrackerActivationThreshold` on brew install

Homebrew's `python@3.12` bottle was compiled against a newer
libexpat than your system has - almost always because **Xcode and
Command Line Tools are out of date** on macOS Tahoe (26.x). Brew's
own output usually tells you so. Two fixes:

- **Use `uv tool install aipager`** instead. uv bundles its own
  python, dodging the issue entirely. This is the recommended path
  on macOS - see the [README](../README.md#install).
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

## The session list was lost or damaged

aipager keeps its sessions in `~/.claude/aipager-sessions.json`,
rewritten atomically (a new file, then a rename), so an interrupted
write leaves the old file whole. If the file is missing, unreadable or
not valid JSON anyway, the daemon logs that it is starting fresh (a
warning when the file was there but could not be read) and starts with
an empty list; it does not stop.
There are no backups of this file. Nothing is lost that matters:

- live sessions are found again on the first monitor tick, by their
  dtach sockets (`/tmp/claude-dtach-*.sock`);
- every conversation is still in Claude Code's own
  `~/.claude/projects/...` folder, so `aipager session <name> --continue`
  in that session's folder picks it up (`--resume` lets you pick one);
- what is gone is the list of ended sessions (`/resume`), the cards
  that were open, and the rest of what aipager remembered about each
  session.

If the daemon keeps failing at start, `aipager logs` (or `journalctl
--user -u aipager`) shows why; move the file aside
(`mv ~/.claude/aipager-sessions.json ~/aipager-sessions.json.old`) to
rule it out.

## A session launch fails

A launch dtach refuses shows a one-line reason in chat (socket already
exists, shell not executable, no pseudo-terminal, name too long,
permission denied, missing directory); dtach's own message, paths
included, is in `aipager logs`.

## `aipager doctor` check list

The order matters - each later check assumes earlier ones passed.
A check that crashes on an unexpected environment shows as a single
⚠ row naming the check and the error; the remaining checks still run.

| Check (`--json` key) | What it verifies | Fix hint |
|---|---|---|
| `config_parses` | `aipager.yaml` and `policy.yaml` can be read; runs first, since every later check reads the config | the error names the file and line |
| `config` | `~/.config/aipager/aipager.yaml` exists and has a token and a chat | `aipager config` |
| `token_valid` | Token works against Telegram `getMe` | re-run `aipager config` |
| `chat_reachable` | Telegram knows the configured chat (`getChat`; nothing is sent) | open bot, tap Start |
| `team` | The chats and members (or a legacy `team.yaml`): says personal or team mode, and flags a malformed or mismatched old `team.yaml` | `aipager config` |
| `role_shell_access` | Warns once for every role a member holds that can run Bash (its safety rules are best-effort, not a boundary; see [groups](groups.md#roles)) | give that role no Bash in `policy.yaml`, or accept it |
| `peer_messages` | Messages between Claude sessions: where their receipts are kept and whether that folder is private, whether this system can check the sender's start time, and a warning for every role a policy lets use SendMessage (see [security](security.md#messages-between-claude-sessions)) | remove a shared receipt folder (aipager recreates it), or give that role no SendMessage |
| `claude` | Resolves the `claude` binary the way every launch does (`claude_path` in `aipager.yaml` or `$AIPAGER_CLAUDE_BIN` win outright; otherwise the newest of `~/.local/bin`, PATH and Homebrew), and lists every OTHER distinct install found | install Claude Code, or set `claude_path` / `AIPAGER_CLAUDE_BIN` |
| `claude_auth` | Probes `claude auth status` in the same environment a real session gets. **Never FAILs**: "not logged in" and "the probe itself failed" are reported distinctly, and neither stops a session from launching. It also warns when Claude Code was told not to use the sessions' `ANTHROPIC_API_KEY` and nothing else logs them in | `claude auth login`, or set an API key / `CLAUDE_CODE_OAUTH_TOKEN`; with `ANTHROPIC_BASE_URL` set, the key for that address ([providers](#using-an-api-key-a-gateway-or-another-provider)) |
| `dtach` | `dtach` binary found (aipager's own `dtach-bin`, or one on PATH) | `uv tool install --reinstall aipager` |
| `hook_scripts` | `aipager-hook` and `aipager-statusline` are on PATH | `uv tool install --reinstall aipager` |
| `settings_json` | The `settings.json` the daemon's sessions read (`~/.claude/settings.json`, or the one `CLAUDE_CONFIG_DIR` points to) has the aipager hooks wired up; with `CLAUDE_CONFIG_DIR` set it names the file and where the value came from | `aipager config` |
| `daemon` | Daemon is running and its socket answers; also shows Telegram rate-limit state (see [above](#the-bot-slowed-down-a-rate-limit-429)) | `aipager start` |
| `service_installed` | Optional: service unit is present | `aipager service install` |
| `service_unit_path` | Linux only: the *installed* unit's `Environment=PATH=` actually contains the resolved claude binary's directory, parsed as text, since doctor cannot see systemd's own PATH from the operator's interactive shell | `aipager service install --yes` |
| `miniapp` | Whether the Mini App can start when it is on. **Never FAILs**: the Mini App is optional | the fix it names |

`aipager doctor --json` prints the same checks, in this order, with
these keys (see [commands](commands.md#aipager-doctor---json)).

Run `aipager doctor --fix` to interactively discover/copy a Claude
credential into `daemon.env` (also when the file is there but holds
none), or pin `claude_path` when more than one install is found. It only ever acts after asking. For a service unit
whose PATH lacks claude's folder, run `aipager service install --yes`.

## The daemon can't find `claude`, or picks the wrong install

Six places used to resolve `claude` independently and could disagree
with each other. They now all go through one resolver
(`aipager/claude_resolve.py`). `claude_path` in `aipager.yaml`, then
`$AIPAGER_CLAUDE_BIN`, win outright when they work. Otherwise every
`claude` found in `~/.local/bin`, on `$PATH` and in the Homebrew
prefixes is checked, and the newest version wins (so reordering `$PATH`
does not change the pick when the other install is newer). Run
`aipager doctor` to see exactly which one it picked and what else it
found; if the wrong one wins, pin the right one:

```sh
aipager doctor --fix        # interactive picker among discovered installs
# or by hand:
echo 'claude_path: /home/you/.local/bin/claude' >> ~/.config/aipager/aipager.yaml
```

## Sessions can't authenticate under the systemd service, but `claude` works fine in a terminal

`systemctl --user` units never source `~/.bashrc` / `~/.profile`, so
an `export CLAUDE_CODE_OAUTH_TOKEN=…` line there never reaches the
daemon. The service unit instead uses systemd's `LoadCredential=`,
reading `~/.config/aipager/daemon.env` - a plain `KEY=VALUE` file,
0600, created automatically the first time you run
`aipager service install` (copied forward from a legacy `config.env`
if one held a token, otherwise discovered from your login shell once,
otherwise left empty with a warning). If it is still empty,
`aipager doctor --fix` searches the same places again when you say yes
and adds the line it finds, keeping the file's other lines; it never
prints the token. Or add the line yourself:

```sh
cat ~/.config/aipager/daemon.env          # see what's there (or isn't)
echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-...' >> ~/.config/aipager/daemon.env
chmod 600 ~/.config/aipager/daemon.env
aipager service stop && aipager service start
```

See [Security model - The Claude credential](security.md#the-claude-credential-what-actually-protects-it)
for what this file does and doesn't protect against.

## Using an API key, a gateway or another provider

aipager runs Claude Code as you set it up, so a session started from
Telegram logs in the way `claude` does in your terminal: a claude.ai
login, an Anthropic API key, a gateway (LiteLLM, Vercel, Cloudflare and
the like) or another company's Anthropic-format address set in
`ANTHROPIC_BASE_URL`. Anthropic supports Claude Code with Claude models
only; other models can behave differently.

- **Where the settings go.** In the `env` block of
  `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR/settings.json`), which
  every Claude Code session reads, or in
  `~/.config/aipager/daemon.env`, which only the sessions aipager starts
  get: one `KEY=value` a line, with no quotes (they would become part of
  the value). The `env` blocks of a trusted launch folder's
  `.claude/settings.json` and `.claude/settings.local.json` and of managed
  settings (`managed-settings.json` and `managed-settings.d/` in
  `/etc/claude-code` on Linux, `/Library/Application Support/ClaudeCode`
  on macOS) count too, as they do for Claude Code. A service (`aipager service install`) never reads your
  shell's profile, so an `export` there does not reach it. Restart aipager
  after you change `daemon.env`.
- **Which key setting.** For a gateway or another provider, Claude Code's
  docs suggest `ANTHROPIC_AUTH_TOKEN`, which it never asks about. For
  `ANTHROPIC_API_KEY` it asks once "Do you want to use this API key?", and
  a session started from Telegram cannot answer, so aipager answers in
  Claude Code's global config (`~/.claude.json`, or the file
  `CLAUDE_CONFIG_DIR` puts it in) right before the session starts:
  - Yes, when the key is set in one of those settings files or
    `daemon.env`, or when nothing else can log the session in.
  - No, when the key only comes from the environment aipager was started
    in and you are logged in to Claude another way: your sessions stay on
    that login and its plan. To use the key, turn it on in a terminal
    session that has it, with `/config` and "Use custom API key".
  - An answer you gave yourself stays, except a No for a key set in
    `settings.json` or `daemon.env` when nothing else can log a session
    in. `aipager doctor` says when a No leaves sessions with no login;
    turn the key back on in a terminal with `/config` and "Use custom API
    key".
- **First-run screens.** Where Claude Code never finished its first run
  (a new machine or container), aipager marks it done when the session
  has a credential, so a session does not stop at the theme picker. With
  no credential at all, run `claude` once in a terminal and log in.
- **Models.** The Opus, Sonnet and Haiku buttons send Claude Code's
  `opus`, `sonnet` and `haiku`, which it maps to your provider's models
  when you set `ANTHROPIC_DEFAULT_OPUS_MODEL`,
  `ANTHROPIC_DEFAULT_SONNET_MODEL` and `ANTHROPIC_DEFAULT_HAIKU_MODEL`.
  The `claude-...` buttons need a provider that has those models. Type any
  other model on `/new`'s model step or in the Mini App, as your provider
  writes it: up to 128 characters of letters, digits and `. _ - : / @ ~ ,`,
  with Claude Code's `[1m]` ending allowed. `keyboard.json` changes the
  buttons (see [Persistent keyboard](commands.md#persistent-keyboard)).
- **`ANTHROPIC_BASE_URL` with no key.** Claude Code then sends your
  claude.ai login to that address. Set the provider's key too, unless the
  address is a gateway meant to pass your login on to Anthropic.
- **Not supported:** tools that run Claude Code on another company's chat
  subscription (a ChatGPT plan, for example) through a local proxy. That
  company has not approved Claude Code for its plans, and such use can put
  that account at risk.

**Claude Code's config in another folder (`CLAUDE_CONFIG_DIR`).**
Claude Code keeps its settings, login, transcripts and global config in
`~/.claude` and `~/.claude.json`, or in the folder `CLAUDE_CONFIG_DIR`
names. aipager follows the same variable, so its hooks and status line go
in the `settings.json` your sessions read and its answers in the file
Claude Code reads (checked with Claude Code 2.1.303):

- **Set it in `~/.config/aipager/daemon.env`** and restart aipager. A
  service never reads your shell's profile, so an `export` reaches only
  the `aipager start` of that shell. At start the daemon wires its hooks
  into `$CLAUDE_CONFIG_DIR/settings.json` and logs `sessions read Claude
  Code's config from ...`; if you change the value while it runs, the
  next session it starts gets them, as does a settings file that lost
  them since. When the hooks cannot be wired, the
  log says `sessions will start without aipager's hooks` and
  `aipager doctor` warns.
- **Use a full path.** Claude Code takes the value as it is: no `~`, and
  a relative value (an empty one too) means a folder inside each
  session's own folder. aipager writes nothing there: the log and
  `aipager doctor` warn, `aipager setup` stops with the fix, and
  `aipager config` skips its Claude Code step with a warning that shows
  the fix and carries on. The fix names the file that holds the value:
  `daemon.env`, or the settings file whose `env` block sets it.
- **Set in Claude Code's own settings.** A `CLAUDE_CONFIG_DIR` in the
  `env` block of `~/.claude/settings.json` (or of managed settings) moves
  the settings file, the login and the transcripts, but not
  `~/.claude.json`. aipager follows that as well; `aipager doctor` says
  where.
- **`aipager config` and `aipager setup`** patch the file the daemon's
  sessions read: your shell's environment plus `daemon.env`. A `claude`
  you start in a shell with another value reads another file and has no
  aipager hooks; `aipager doctor` names it.
- **The legacy `.config.json`.** When `<config folder>/.config.json` is
  in use (for example, it exists and is not empty), Claude Code reads it
  instead of `.claude.json`, and so does aipager. With an empty
  `CLAUDE_CONFIG_DIR` that file would be in the session's own folder, and
  aipager writes nothing there.
- **What aipager cannot read.** Managed settings delivered by an MDM
  profile (macOS), the Windows registry or Claude's servers, and the
  `--settings` flag. A key or a provider switch set only there is not
  seen when aipager answers the start-up questions; Claude Code then asks,
  as it would without aipager.
- **The deny list.** The default rules that keep restricted roles and
  sent files off Claude's credentials name `~/.claude/**` and
  `~/.claude.json*`. A `CLAUDE_CONFIG_DIR` somewhere else is not covered
  by them, so keep it inside `~/.claude` (the Docker image's is) when
  people who are not admins use your sessions
  ([security](security.md#who-decides-whether-a-tool-call-runs)).

## The daemon's control socket moved

`aipager doctor` / `aipager status` used to always look at
`/tmp/aipager.sock`. Under the systemd service the control socket now
lives at `$XDG_RUNTIME_DIR/aipager.sock` (falling back to `/tmp` only
when `$XDG_RUNTIME_DIR` is unset - containers, WSL1, minimal distros).
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
restarts the daemon - and that restart already runs under the new
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

- an editable (development) checkout - update it with `git pull`;
- a Nix, Snap, Docker or OS-package install - use that system's own
  update;
- an install owned by another user (for example a root-owned venv under
  `/opt`) - update it as that user;
- an install made from a local folder (`pipx install /path/to/aipager`):
  `can't update from here: it was installed from a local folder; update
  it from that folder`. Pull the new version into that folder and
  reinstall from it, or switch to PyPI with `pipx install --force
  aipager`;
- an install whose installer cannot be told (`could not tell which
  installer owns this aipager`): reinstall it with `uv tool install
  aipager` or `pipx install aipager`.

## Update failed: unexpected argument '--refresh'

aipager 1.0.0 and older ran `uv tool upgrade aipager --refresh`, which no
uv release accepts, so every update of a uv install stopped with `error:
unexpected argument '--refresh' found` and restarted nothing. Newer
versions run `uv tool upgrade aipager`. To get past it once, update from
a terminal on that machine (`aipager update` has the same problem on
those versions):

```
uv tool upgrade aipager
systemctl --user restart aipager.service
```

Without the service (or on macOS), restart aipager the way you started it.

## Claude Code update: installed with npm into a folder you can't write to

A Claude Code installed with `sudo npm install -g` lives in a folder
only root can change (such as `/usr/lib/node_modules`), so `claude
update` run as your user fails with `global folder isn't writable`.
`/update` and the Mini App show the newer version with `can't update
from here: installed with npm into a folder your user can't write to`
and no button for it. Run `claude install` once as the user aipager runs
as: it switches to Claude Code's own installer under your home folder,
which updates without sudo. Then restart aipager
(`systemctl --user restart aipager.service`, or the way you started it):
a running daemon keeps the Claude Code it found at start, and after the
restart it takes the newest one it finds. If `claude_path` in
`aipager.yaml` (`aipager doctor --fix` can set it) or `AIPAGER_CLAUDE_BIN`
names the npm one, point it at `~/.local/bin/claude` or remove it first,
since those always win.

## Updated, but the version didn't change

A pipx install made from a git URL upgrades from that git source, not
from PyPI. When the source has nothing newer, `/update` says `already
at A - this pipx install upgrades from its git source, which has
nothing newer`. Push or pick the new version there, or switch to PyPI
with `pipx install --force aipager`.

**Without a service**, a daemon no service manager restarts (`aipager
start` in a terminal or tmux, Docker, launchd) installs an update but
keeps running the old code until it restarts. `/update` then says `aipager B is installed and
waiting for a restart (running A).`, and Claude Code's hooks, which
start fresh on every event, may already run B: the owner gets one notice
that the update is only half done (two minutes after the hooks first ran
B, so a restart that follows the install at once never shows it, and
once no turn is running in the owner's private chat). Once aipager runs
B, that notice turns into `✅ The aipager update is finished: aipager
runs B now.`, without its button. Tap **🔄 Restart now** (in the update
message, the notice, or the Mini App's **Settings → Updates**). aipager
waits until no turn is running, stops cleanly and starts the new version
in place: the same process in the same terminal, tmux window or
container, with the same command and environment.

If the in-place start fails, a detached copy starts aipager once the old
process has gone. Its output then goes to the self-restart log
(`aipager.log` in the instance folder, or `/tmp/aipager.log`) instead of
your terminal, and the daemon log names the command to run if that copy
does not come back. If the interpreter itself is gone (its environment
was removed), or nothing could be started, aipager exits
with code 1, the daemon log ends with `restart.failed:` and the command
to run, and the next start announces the update.

When there is no **Restart now** button, the message gives the steps for
your setup: in a terminal or tmux, press Ctrl+C and run `aipager start`;
on macOS, `launchctl kickstart -k gui/<uid>/com.aipager.daemon`; in
Docker, `docker restart <container>` on the host. Recreating a Docker
container (`docker compose up` after `down`, or `docker run` again)
starts it from its image, which brings back the aipager version in that
image: to keep a newer aipager, use a newer image.

The image sets `CLAUDE_CONFIG_DIR=/home/aipager/.claude`, so Claude
Code's global config (`.claude.json`: folder trust, its first-run state,
the API key answers, your account and user MCP servers) is kept in the
`.claude` volume with the rest and survives a recreate. Images before
this one kept it at `/home/aipager/.claude.json`, in the container
itself. On the first start of the new image the old file is not read:
your login in the volume's `.credentials.json` still works, and aipager
answers the folder trust and first-run questions again when a session
starts, but user MCP servers added with `claude mcp add --scope user`
have to be added again. If you bind-mount your host's `~/.claude` (as the
README's commands do), the file appears there as `~/.claude/.claude.json`;
your host's Claude Code does not read it (it uses `~/.claude.json`). A
named volume keeps the two apart.

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

A Restart now that could not start aipager again (no service) leaves
`restart.failed:` in the daemon log with the command to run; run it, and
the new daemon announces the update.

## A chat is in the wrong language

aipager's language comes from what was picked for the chat, not from the
messages in it. Open `/language` in that chat and pick the language you want:
the choice wins over everything else. In a private chat, **Automatic** follows
your Telegram app's language (Settings → Language in Telegram); a language
aipager does not have reads as English. A private chat that existed before
languages were added stays English until you pick one. A group is English until
an admin picks its language. The language only changes aipager's own text:
Claude answers in the language you write to it, whatever aipager shows. See
[Languages](commands.md#languages).

## The bot description is not the one I wrote

aipager sets the bot's description and short description in each language it
speaks, for people whose Telegram app is in that language, and Telegram shows
that text to them instead of the one you wrote in BotFather. aipager does this
only while you have no description of your own, so a description you write
before installing this release reaches everyone. If you wrote it after aipager
had set its own, people in those languages still see aipager's. To show them
yours, clear aipager's text for each language through the Bot API
(`setMyDescription` and `setMyShortDescription` with an empty text and that
`language_code`); aipager never sets it again while yours is there.

## aipager says languages.json could not be used

aipager keeps what it learned about languages (who was here before languages
were added, the last Telegram language it saw from each person, and whether the
one-time offer and first-contact line went out) in
`~/.local/state/aipager/languages.json`. If that file cannot be read, aipager
moves it aside to `languages.json.bad-<time>` in the same folder, logs a
warning (again at every start until you decide), and keeps every chat on English with no language offer until you decide: put the
old file back if you can repair it, or delete `languages.json` and restart, and
aipager counts every person it knows as existing again (English until they
choose). A language anyone picked is not in this file and is never lost.

## Right-to-left text looks wrong

In Persian and Arabic, aipager puts invisible direction marks in its own text so
each line reads right to left, and keeps session names, commands and code left
to right. If a line still looks jumbled, it is most often a Telegram client that
draws mixed-direction text differently: check it in another client (phone versus
Desktop or Web) before reporting it. Claude's own answers are shown as Claude
wrote them, with no marks added.

## A translated keyboard word does nothing in another chat

Each chat's reply keyboard is in that chat's language. The main keyboard's words
(status, stop, new, kill and the rest) are understood in every chat, in every
language, and the English words always work. A template or command button's
word is different: typed in a chat set to another language, it is a message for
Claude, not a button, so a Russian "Clear" never runs `/clear` in a Persian or
English chat. Use the button itself, or the English command (`/clear`). If a
keyboard looks out of date after the chat's language changed, send `/start` to
get the current one. A word that is also the name of one of your sessions talks
to that session. `/mode` reads `ask`, `auto` and `full` as the mode always, and a
translated mode word as a session only when a live session has exactly that
name.

## Still stuck?

Send a problem report: tap **Report a problem** under `/help` in your private chat with the bot, or run `aipager report` in a terminal. You see the whole report before anything is sent, and it holds no chats, prompts, names, paths or tokens (see [Problem reports](problem-reports.md)). After sending, you get a reference such as `ap1-7c41d09e2b3a`.

Then, if you want to talk about it, open an issue at [github.com/dev-aly3n/aipager/issues](https://github.com/dev-aly3n/aipager/issues) and quote that reference. Please do not paste `aipager doctor` output or `aipager logs` into an issue: they hold your chat id, your home folder's paths, session names and the start of your prompts. If a log line is needed, the maintainer will ask for that one line, and you can remove anything private from it first.

## See also

- [Architecture](architecture.md) - what each component does.
- [Security model](security.md) - what aipager touches and doesn't.
