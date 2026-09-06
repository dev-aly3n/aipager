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
| `/resume [label]` | optional | Resume a previously-gone session by name, or open a picker. |
| `/kill [label]` | optional | Destroy a session. With no arg, opens a picker. Always two-tap: shows `[💀 Kill] [Cancel]`. |
| `/restart [label]` | optional | Kill and relaunch a session, keeping its conversation. |
| `/rename [label]` | optional | Give a session a new name. |
| `/delete [label]` | optional | Drop a finished (GONE) session from the list. |
| `/diff [label]` | optional | Show the session's working-directory git diff. |
| `/clearqueue` | — | Drop every not-yet-picked-up message for the active session — both messages aipager is holding and messages already queued inside Claude — without interrupting the running turn. Replies with the count cleared. |
| `/perms [label]` | optional | Switch a session between Ask and Auto permission modes. On a busy session, offers `Stop task & switch` / `Not now`. |
| `/settings` | — | Message layout, diff previews (off by default), formatting and language preferences. Whatever the layout, every busy card ends with its session's status line (`⏳`/`✅ name · …`) and every answer starts with its result line (`💬 name`, plus `· Finished (…)` when no finished card is left to show the stats); the merged layout stacks the two, each line in its own section. |
| `/whoami` | — | Show your Telegram id and (in team mode) your role. |

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
a session's dashboard carries the same actions.

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
- **Models** — quick model switches (`/model sonnet`, `/model opus`,
  `/model haiku`, `/model opusplan`).

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

`🔄 Restart daemon now` always works:

- Service-managed daemons: `systemctl --user restart aipager.service`
  on Linux, `launchctl kickstart -k` on macOS.
- Foreground / editable daemons: spawn a detached replacement that
  waits for the parent PID to die, then `exec aipager start`. The
  current daemon SIGTERMs itself once the spawn is alive.

No SSH required.

## Free messages

### Text

Treated as the next prompt for the **active session** (the one whose
slash command you last sent). Messages reach Claude **immediately**,
even while a turn is running — exactly like typing into the terminal.
Send several and they queue inside Claude itself, which picks each up
at a natural boundary:

- 👀 on your message — sent to the session.
- 👍 — Claude has taken it in. For a message sent while a turn runs
  this happens at once: Claude queues it, and only later either folds
  it into the turn already running or starts a new turn for it.

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
terminal. Only a message that started a turn is judged this way — one
queued behind a running turn just keeps its 👀 reaction, which never
turns into 👍 if Claude did not take it.

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
