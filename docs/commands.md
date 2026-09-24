# Bot commands and interface

How you drive aipager from Telegram. Four input channels: slash
commands, keyboard buttons, the Mini App dashboard, and free
messages (text / files / voice).

The bot only accepts input from the configured chat(s) — see
[security](security.md) for the trust boundary.

## Slash commands

Registered in `aipager/bot/lifecycle.py`. Telegram autocomplete
shows these in its slash-command menu, refreshed at daemon startup
and on every session change.

| Command | Args | What it does |
|---|---|---|
| `/start`, `/help` | — | Print the welcome panel and persistent keyboard. |
| `/app` | — | Open the Mini App dashboard (sessions, diff viewer, settings). |
| `/status` | — | One-message snapshot of every live session: model, context %, cost, queue depth. |
| `/stop` | — | Interrupt the active session's current turn. Also discards queued messages and replies with how many were discarded. |
| `/new [label] [prompt]` | all optional | Create a session. With no args, an interactive wizard walks name → mode → model → folder. With a label (and optional first prompt), creates `claude-<label>` directly. |
| `/resume [label]` | optional | Resume a previously-gone session by name, or open a picker. A session that ended stays listed for `GONE_SESSION_MAX_AGE_DAYS` (default 14) and then leaves the registry; its Claude transcript is untouched. |
| `/kill [label]` | optional | Destroy a session. With no arg, opens a picker. Always two-tap: shows `[💀 Kill] [Cancel]`. |
| `/restart [label]` | optional | Kill and relaunch a session, keeping its conversation. |
| `/rename [label]` | optional | Give a session a new name. |
| `/delete [label]` | optional | Drop a finished (GONE) session from the list. |
| `/diff [label]` | optional | Show the session's working-directory git diff. |
| `/clearqueue` | — | Drop every not-yet-picked-up message for the active session — both messages aipager is holding and messages already queued inside Claude — without interrupting the running turn. Replies with the count cleared. |
| `/perms [label]` | optional | Switch a session between Ask and Auto permission modes. On a busy session, offers `Stop task & switch` / `Not now`. |
| `/settings` | — | Message layout, diff previews (off by default), long-turn card updates (on by default: a busy card refreshes every 10 s after 2 minutes of a turn, 30 s after 10, once a minute after an hour, counting in minutes then hours — switch off to keep the first-minutes pace for the whole turn; see [troubleshooting](troubleshooting.md#a-long-turns-card-refreshes-less-often)), formatting and language preferences. Whatever the layout, every busy card ends with its session's status line (`⏳`/`✅ name · …`) and every answer starts with its result line (`💬 name`, plus `· Finished (…)` when no finished card is left to show the stats); the merged layout stacks the two, each line in its own section. In the card layout the answer deliberately follows the finished card by a moment, so the card is seen to say Finished before the answer lands under it — tune or disable that head start with `FINISH_CARD_GRACE_SECONDS` (seconds, default 0.8; 0 sends both at once). A card-layout turn that ran no tools keeps no card: its card would only repeat `✅ name · Done · Ns`, so the answer arrives alone with the stats in its `💬` line (see [Idle responses](#idle-responses)). |
| `/whoami` | — | Show your Telegram id and (in team mode) your role. |
| `/update` | — | Admin only. Show the running and latest aipager and Claude Code versions and update either or both — see [Update](#update). |

### Per-session dynamic commands

One command per live session, registered from its label:

| Form | What it does |
|---|---|
| `/<label>` | Switch the active session to `<label>` and show its dashboard. |
| `/<label> <prompt>` | Send `<prompt>` straight to that session without switching. |
| `/<label> stop` | Interrupt that session's current turn. |

`/status` results come from the same data `aipager status` shows on
the CLI; no Telegram round-trip for the session list itself.

## The Mini App

`/app` (and the Telegram menu button) opens a dashboard served by the
daemon itself: live session list with per-session actions (stop,
kill, restart, rename, perms, clear queue), a diff viewer for
`Write`/`Edit` changes, and settings. It is on by default; manage it
with `aipager miniapp enable|disable|status`. Every request is
verified against Telegram's `initData` signature — see
[security → Mini App tunnel](security.md#mini-app-tunnel).

Everything in the Mini App is also reachable from chat: the ⋮ menu on
a session's dashboard carries the same actions. For the admin,
**Settings → Updates** mirrors [`/update`](#update).

### Switching a running session's model

A live session's page has a **Model** control showing the model the
session reports (from Claude Code's statusline) and the same list the
launch picker and the chat's Models keyboard offer. Picking one types
exactly `/model <name>` into that session — the same injection the
chat's Models keyboard uses, with the same rule on who may do it
(anyone who can prompt the session). The control reads **switching…**
until the statusline reports a different model, then shows it; if
nothing changes within 15 seconds it reads **not confirmed — check the
session**.

- **Only while the session is idle, not while Claude is working or a
  prompt is open.**
  Claude Code runs `/model` straight away, even mid-turn, rather than
  after the turn, so the switch is refused until the turn ends. The chat
  keyboard refuses in the same states, with the same words.
- **Claude Code's "Switch model?" question.** Claude Code usually asks
  this before a switch, because the next reply has to re-read the whole
  conversation. With Claude Code 2.1.251 or later, aipager's
  `PreModelSwitch` hook approves the switch aipager just typed, so the
  question is not asked (see [hooks](hooks.md#premodelswitch)). Every
  other switch still asks. With an older Claude Code, or on a switch to
  or from `opusplan`, the question can still appear. The switch then
  shows as *not confirmed* and the question waits in the terminal.
  Answer it there before you send the session anything else, or your
  next message would be typed into it. For the same reason, aipager
  refuses another switch of that session for a minute after an
  unconfirmed one, or until the session reports a new model.

## The pinned status bar

Each chat aipager talks in (your DM, and every group scope) gets one
pinned message: the status bar. Telegram's bar at the top of the chat
shows the message from its **first line** on, with the lines run
together, so that line says what most needs you:

| First line | When |
|---|---|
| `⏳ jim needs you — Bash: make deploy` | a session is waiting on a permission prompt, a question or an interactive prompt; `(+2 more)` when others are waiting too |
| `⚙️ jim — working` / `💤 jim — idle` | the chat has one live session and it is working / idle (`🔄 jim — starting` while it starts): this line is the whole bar, apart from a flood line |
| `⚙️ 2 working · 1 idle` | several sessions, none waiting: how many are in each state (`working`, `idle`, `starting`); `🔄` when none is working and one is starting, `💤` when all are idle |
| `💤 all idle` | no live session |

Below it, only while it applies, a flood line — `🐢 slow mode after a
Telegram warning` (the six hours after a 429) or `⏸ card updates paused
— hourly limit` / `— rate limit` (minimal mode, see
[troubleshooting](troubleshooting.md#the-hourly-budget)) — and then,
when the chat has more than one live session, one line per session with
its state: `working`, `needs you`, `idle` or `starting`. The bar names
each session once: the waiting session the first line names gets no
line of its own, and the first line never lists names the lines below
repeat.
Tap the bar to jump to the message.

Buttons on the pinned message:

- **Answer &lt;label&gt;** (one per waiting session, three at most) sends
  that session's prompt again, with its answer buttons, at the bottom of
  the chat, so you can answer it without scrolling. If it was answered in
  the meantime you get an "already answered" toast instead, and a copy
  (or the original prompt) tapped after its prompt was answered elsewhere
  is refused the same way — it never answers a later prompt. After a
  daemon restart aipager no longer knows which prompt an old copy showed,
  so while a prompt is waiting, a tap on anything but its own message is
  refused with "this prompt has expired". Anyone who
  may answer the prompt may use it; nobody else.
- **📱 App** opens the Mini App, in your DM, while the Mini App is up.

The bar carries no clock, cost, context % or model, so it changes only
when a session's state does. aipager edits it (silently, no
notification) only when what it shows changed, at most once every 30 s
per chat (`PINNED_MIN_EDIT_GAP`); a change inside that gap is shown when
the gap ends, never lost. It is never edited while the chat is
flood-muted, and catches up when the mute lifts.

In a group the bot needs admin rights to pin. If the pin is refused,
aipager deletes the status message it just sent (so it never sits
unpinned in the group's history) and shows no bar in that group until
the daemon restarts; the same goes for a chat that refuses the bot
altogether (kicked, blocked). If you delete the pinned message, aipager sends and
pins a new one, at most once an hour per chat.

## Persistent keyboard

A persistent keyboard sits below the chat input. Rows, top to bottom:

| Row | Buttons | Notes |
|---|---|---|
| Sessions | one button per live session label | auto-built from the registry |
| Actions | `status`, `stop`, `kill` | plain-text shortcuts |
| Nav | `Templates`, `Commands`, `📱 App` | App appears in private chats while the Mini App is up, and opens it directly |

`Model ›` lives inside the Commands submenu. Tapping a submenu
entry sends a canned prompt or slash command:

- **Templates** — bulk prompts you find yourself typing repeatedly,
  e.g. `Write tests for the changes`, `Explain your plan before
  making changes`, `Update CLAUDE.md with what you learned`.
- **Commands** — slash commands claude code natively handles
  (`/compact`, `/clear`, etc.), injected instantly.
- **Models** — quick model switches for the active session. There
  are the family aliases (`sonnet`, `opus`, `haiku`, `fable`,
  `opusplan`), which always mean the latest model in that family, and
  pinned models (`claude-opus-5-5`, `claude-opus-5-5[1m]` with the 1M
  context window, `claude-sonnet-5`, `claude-fable-5-1`,
  `claude-haiku-4-5`). The Mini App's launch and session pickers use
  the same list. A switch is refused while the session is working or a
  prompt is open. The `🔄` reply then changes to show the model the
  session reports, or says the switch was not confirmed (see
  [Switching a running session's model](#switching-a-running-sessions-model)).

Override the default layout by writing
`~/.config/aipager/keyboard.json`:

```json
{
  "templates": [{"label": "Deploy",  "prompt": "Deploy to staging"}],
  "commands":  [{"label": "Compact", "send": "/compact"}],
  "models":    [{"label": "Sonnet",  "send": "/model sonnet"}]
}
```

Each section is independent — missing sections fall through to the
built-in defaults so you can override one without specifying the
others. Malformed JSON fails open with a logged warning. Changes
require a daemon restart.

## Per-message inline buttons

Most bot replies carry context-specific buttons:

### Permission prompts

When claude asks to run a tool that needs approval, the busy message
becomes a permission prompt:

```
🔐 Bash: List the temp directory
ls -la /tmp

  [✅ Allow]  [❌ Deny]
  [🟢 Allow always]  [⏹ Stop]
```

The card shows the real command (or file path) claude is asking to
run, under its own description of it — approve what you can read.

- **Allow** — approve this one call.
- **Deny** — refuse it; claude blocks the tool call.
- **Allow always** — approve and add the standing rule claude offers
  ("don't ask again for …"). The button appears **only when claude
  offers such a rule**; for a command it cannot derive one for (most
  compound commands) the card carries Allow / Deny / Stop instead — as
  it does for a read-only file access (`Read`, `Grep`, `Glob`) outside
  the session's working directory.
  Claude Code 2.1.259+ puts a "switch to auto mode" row in that slot of
  its own Bash dialog, a "block reads outside the working directories
  from now on" row in the outside-read one, and — for a Write/Edit it
  can't derive a per-file rule for — a permission-mode switch such as
  auto-accepting all file edits (`acceptEdits`); aipager never selects
  any of these; change modes deliberately with `/perms`.
- **Stop** — interrupt the turn instead of answering.

Every tap is recorded in `~/.claude/aipager-audit.jsonl` and mirrored
as a one-line reply threaded under the busy message:
`✅ [jim] · Allowed · Bash: ls -la /tmp`.

While a prompt — or an AskUserQuestion — waits for you, the session
shows as waiting, never idle. If Claude Code nudges about idle input
during that wait, the chat gets `⬆️ jim · still waiting for your answer
above` as a reply to the prompt, once per wait.

`AskUserQuestion` dialogs render the same way, with one button per
option (and checkbox-style multi-select where the question allows it).

### Stale buttons

Buttons that act on a running turn — Stop, Kill, Restart, `/new`'s
Replace, `/perms`' Stop-and-switch — are tied to the task they were
shown for. Tapping one left over from an earlier task answers
`That task already finished — …` (with a hint to re-run the command)
and changes nothing, instead of acting on whatever is running now.

### Idle responses

Once a turn ends, the busy message becomes the IDLE response. If
claude's last message is long enough to spill past Telegram's 4 KB
limit it's sent as a `.txt` attachment with a `📎 Full response
attached below ↓` footer. Buttons:

- **🔄 Retry** — re-send the last prompt to the same session.

In the card layout the finished card stays above the answer as the
record of how it was reached — its tool rows, agent rows and what Claude
said between them. A turn with none of those (no tool call, no agent,
no commentary besides the answer itself) has nothing to record, and a
card left behind would say only `✅ name · Done · Ns` right above an
answer saying the same. Such a turn ends as **one** message instead: the
answer, opening with `💬 name · Finished (Ns)`, sent as a normal
(notifying) message threaded to the prompt that started the turn, if
any; the busy card is deleted once the answer is out. A tool-less turn with no new answer text (none at all,
or only text already delivered) keeps its card, as the one sign it
ended. The merged and replace layouts are
unchanged.

A turn Claude starts **by itself** — a background agent reporting back
with a `<task-notification>` when no job is open — gets its busy card
only once it does something: at its first tool call, or after 15 s,
whichever comes first. Most such wake-ups are a few seconds of "nothing
new"; those now show just the answer.
The "typing…" indicator still shows while it runs, and the answer itself
is always delivered — nothing is filtered as trivial.
Turns you start, from Telegram or the terminal, still get their card at
once.

While a session is busy, each background agent Claude launches (via
`Task`) gets its own line on the busy card: `🤖 <type> · <activity> ·
<elapsed>`, showing the agent's type and what it's currently doing,
refreshed as its own tool calls come in. Once that agent has made three
or more tool calls, they fold into their own `▸ N tool calls` tap
directly beneath its row — never appearing in the parent's timeline or
its `Bash ×N` tallies. When the agent finishes, its row settles to `✅
🤖 <type> · N tool calls · <elapsed>` and keeps the same tap. The full
play-by-play `.txt` attachment above gains an AGENTS section listing
every agent that ran the turn, its elapsed time, tool count, and the
tools it called.

Once a turn's timeline grows long, each older run of tool calls (three
or more in a row, and not the run currently in progress) folds into its
own `▸ N tool calls` tap right where it happened, instead of piling up
in full or being cut off by Telegram's own message-length limit — tap
any one to read it in place. Commentary never folds; the newest activity
and the status line are always visible without tapping anything. A
still-running (or just-settled) background agent's own row is never
folded into a tap itself, only its tool calls, and never while it's the
one thing standing between the timeline and the ceiling. Only if the
timeline is so large that even every fold together still can't fit does
content get genuinely dropped from the card — in that case the `.txt`
attachment above carries the complete record.

### Kill confirmation

`/kill <label>` and the `💀 Kill` button always confirm:

```
⚠️ Kill session [jim]?
This will terminate the running claude process.

  [💀 Kill]  [Cancel]
```

### Voice install (when extra isn't installed)

When you send a voice message and `aipager[voice]` isn't installed:

```
⚠️ Voice messages need the optional voice extra
   (~200 MB install · ~74 MB model on first use).

  [📦 Install voice]  [Cancel]
```

Tapping Install runs the right install command for your installer
(`uv tool install --reinstall aipager[voice]`, `pipx install
--force`, or a `pip install faster-whisper` fallback) with a 5 s
heartbeat edit, then offers a `[🔄 Restart daemon now]` button on
success.

### Restart

`🔄 Restart daemon now`:

- A daemon running as the systemd-user service schedules a detached
  `systemctl --user restart aipager.service` 5 s later, in a transient
  unit outside the daemon's own cgroup, so it survives the daemon's
  exit. It refuses while the service unit would kill your sessions
  (`KillMode` other than `process`), and tells you to run
  `aipager service install` first.
- macOS: `launchctl kickstart -k gui/<uid>/com.aipager.daemon`.
- A daemon you started yourself (`aipager start`), even on a machine
  that also has the service installed: spawn a detached replacement
  that waits for the parent PID to die, then `exec aipager start`. The
  current daemon SIGTERMs itself once the spawn is alive.

No SSH required.

## Update

`/update` (admin only; in personal mode, only the operator) replies
`🔎 Checking versions…` and then edits that message to show:

- **aipager** — the running version, the latest on PyPI, and how it was
  installed (e.g. `pipx, from PyPI` or `pipx, from local path …`; group
  chats never show paths);
- **Claude Code** — the installed version, the latest on its own update
  channel (`autoUpdatesChannel`: latest, stable or rc), and the install
  method;
- **Restart** — `automatic (systemd)`, or `manual` with the reason.

A version that cannot be looked up (network down, 5 s timeout) shows as
`unknown`.

Buttons: **⬆️ Update Claude Code**, **⬆️ Update aipager**, **Both**,
**Cancel**. The aipager and Both buttons are missing when this install
cannot be updated from here (editable, Nix, Snap, a system package, a
container, or another user's install); the Claude Code buttons are
missing when `claude` is not found. Every tap re-checks the admin rule.

**Update Claude Code** runs `claude update` (by absolute path, 5 min
timeout) and reports `Claude Code A → B`, "already up to date", or the
failure with the tail of its output. Running sessions keep the old
version until you restart them (`/restart`); the reply lists them. No
session is restarted for you. New sessions use the new version.

**Update aipager**:

1. On a PyPI install that is already current, it says so and stops.
2. If the daemon can restart itself, it first **waits until nothing is
   in flight**: no session running a turn, waiting on a question,
   running a background agent, showing a live busy card, running a
   tool, or holding an open permission prompt, and no held answers
   waiting for a rate limit to lift. The message lists what it is
   waiting for, with **Restart now** (skip the wait) and **Cancel**.
   After 10 minutes it asks again: **Wait 10 more min**, **Restart
   now**, **Cancel**. Unanswered for an hour, it cancels itself.
3. It upgrades through the installer that owns the running daemon
   (`pipx upgrade aipager`, `uv tool upgrade aipager --refresh`,
   `brew upgrade aipager`, or `<venv>/bin/python -m pip install
   --upgrade aipager`), by absolute path, with a 10 min timeout. There
   is no Cancel while the installer runs.
4. It checks the new version imports in a fresh interpreter. A failed,
   timed-out or unimportable upgrade restarts nothing. A timed-out
   upgrade was stopped part-way, so the message warns the install may be
   partial and gives the reinstall command to run before the next
   restart. Installer output is shown only in a private chat; a group
   gets "output in the daemon log".
5. If a turn started during the upgrade, it waits again.
6. It schedules a detached `systemctl --user restart aipager.service`
   5 s later: `aipager A → B installed. Restarting in 5 s…`. The new
   daemon then posts `✅ aipager updated A → B, N sessions re-adopted`
   (and `⚠️ Not back: …` for any session that did not come back) to the
   chat that asked.

The daemon restarts itself only when it runs as the systemd-user
service **and** that unit has `KillMode=process`. Otherwise aipager is
still upgraded, and the message tells you how to restart: run
`aipager service install` first (it lists the sessions a restart would
kill), the `launchctl kickstart` command on macOS, or "restart your
`aipager start`" for a daemon you started yourself.

Only one update runs at a time, across `/update`, the Mini App's
**Settings → Updates** block (same data, same buttons, same job) and
`aipager update` on the command line. That includes the seconds between
"Restarting in 5 s…" and the restart itself: `/update` answers that
aipager is about to restart, the Mini App offers no buttons, and the
voice extra's **Restart daemon now** refuses while an update runs or
waits to restart. If the daemon is still alive two minutes after
scheduling its restart, it stops the pending restart timer, frees the
update lock, and tells you to restart it yourself.

If the daemon shuts down while an installer is running, the installer
gets 3 s to finish and is then stopped (it would otherwise keep writing
the install while the next daemon starts). The status message and,
after the restart, a new message say the install may be partial and how
to repair it.

Once a shutdown has begun, nothing is restarted and nothing new starts:
`/update` buttons answer that aipager is shutting down, the Mini App
answers 503, and no installer or version check is spawned. An installer
that finishes within its 3 s still counts. The status message says the new
version is installed and nothing was restarted, and the next start
announces `aipager updated A → B`. A daemon you stopped with
`aipager service stop` stays stopped. The update's part of the shutdown
takes at most 8 s in total.

## Free messages

### Text

Treated as the next prompt for the **active session** (the one whose
slash command you last sent). Messages reach Claude **immediately**,
even while a turn is running — exactly like typing into the terminal.
Send several and they queue inside Claude itself, which picks each up
at a natural boundary:

The reaction on your message follows it, the way Claude Code's own
queued prompt turns from grey to white:

- 👀 — handed to the session (or held, see below), but Claude has not
  taken it yet. A message sent while a turn runs stays 👀 while it
  waits in Claude's queue.
- 👍 — Claude took it: it started a turn, or Claude folded it into the
  turn already running, or handed it to a running background agent.
- 🤷 — it will never be taken: a held message dropped by `/stop`,
  `/clearqueue` or `/kill`, or one that could not be sent on release; a
  message or command still waiting when `/stop`, `/clearqueue`, `/kill`
  or the session ending (not `/clear` or `/resume`) dropped it; or a
  prompt Claude Code refused (see below). Messages Claude had already
  queued are only marked while aipager can see that queue — the live
  transcript scan is running and no background job is waiting; otherwise
  they keep 👀. Escape in the
  terminal pulls Claude's queue back into its input box, where it may be
  sent again, so a message dropped that way keeps 👀 too.
- 👌 — a Claude Code command that has run (see below), or aipager
  acknowledging `/stop`.

A reaction only ever moves forward, so a message gets at most three,
each once. A reaction that falls inside a Telegram rate-limit ban is
skipped, not replayed later, and one teardown marks at most the ten
newest messages it drops.

Command buttons (`Compact`, `/model …`): tapped while the session is
idle, the command is 👌 at once — Claude Code runs it on Enter, and a
local command such as `/model` fires no hook that could say so later.
Tapped while a turn runs (not `/model`: it is refused until the turn ends,
see [switching a running session's model](#switching-a-running-sessions-model)),
it is 👀 until that turn ends — normally, as
a background job's interim stop, or on an API error — and then 👌;
dropped before that by `/stop`, `/clearqueue`, `/kill` or the session
ending, it never ran: 🤷. When Claude Code queues it as a prompt instead,
it follows the 👀 → 👍 lifecycle. A voice note gets the same reactions as
text once its transcript is sent.

The busy card and the eventual answer follow whichever message Claude
actually consumed for a turn — the one it started on if the session
was idle, or the one it absorbed into the turn already running — never
simply the last message you sent. Sending a follow-up mid-turn does
not "jump the reply" to itself: if Claude folds it into the answer
already forming, the card jumps to it the moment that happens; if
Claude instead finishes first and then picks it up, the first answer
stays under the first message and the follow-up gets its own turn,
with its own card and answer under it.

If Claude Code refuses a message outright — an unknown slash command,
or a built-in that only opens a dialog in the terminal — no hook fires,
so nothing would ever end the turn the daemon just announced. After
8 s without any hook (`PROMPT_HOOK_GRACE_SECONDS`) the busy card
becomes `⚠️ name · Not taken by Claude Code` with a one-line
explanation and the session is idle again; the reason is on the
terminal, and the message gets 🤷 (a slash command gets 👌 instead: a
built-in that opens a dialog fires no hook either). Only a message that
started a turn
is judged this way — one queued behind a running turn keeps its 👀
until Claude takes it or it is dropped.

Two cases are held back instead of sent, and delivered automatically
once resolved:

- A permission or question prompt is open — your text would otherwise
  be read as an answer to that dialog.
- (Team mode) a different user's message is still waiting to be
  picked up — messages from different people are never merged into
  one turn.

Held messages are capped at 50 per session and expire after 24 h;
`/clearqueue` drops them along with anything Claude is holding.

### Files

Uploaded files are downloaded into the active session's workspace
and the path is offered to claude: with a caption, the prompt is the
caption followed by the path(s); without one it is just
`check this: <path>` (or `check these: <paths>` for an album), so
claude is pointed at the file without being told what to do with it.
A caption that starts with `/<label>` picks the session, exactly as
`/<label> <prompt>` does for text, and the label is dropped from the
prompt: `/api` alone sends `check this: <path>` to `api`, and
`/api compare these` sends `compare these <paths>` there. Only a
leading `/<label>` routes; a slash later in the caption is just text.
A label no session answers to is refused with `⚠️ Unknown session`
and nothing is sent.
The 20 MB Telegram bot file
download cap is enforced up-front; oversized files get a clear
rejection before any download attempt.
A download that hits a transient network error is retried up to
three times with a short backoff before you see an error, and that
error names the file. An album — several photos or documents sent as
one message — is handed to claude as a single prompt (the caption,
then every file path in order) once its last item has landed; if one
item cannot be downloaded the rest still go out, with one note naming
the missing one.

### Voice

Voice messages route through `faster-whisper` (the `aipager[voice]`
extra). The audio is transcribed locally and the transcript is
injected as if you had typed it — including as an answer to the
`/new` wizard or a pending rename. See
[hooks → UserPromptSubmit](hooks.md#userpromptsubmit) for what
happens next.

## See also

- [Architecture](architecture.md) — where the bot fits.
- [Hook events](hooks.md) — what aipager hears back from claude.
- [Troubleshooting](troubleshooting.md) — when commands misbehave.
