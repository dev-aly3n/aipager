# Hook events

Claude Code runs hook commands at fixed moments of a turn: a prompt is
submitted, a tool is about to run, a tool finished, the turn ended, and
so on. aipager hooks every one of them and turns them into Telegram
activity: the busy card, permission prompts, answers, notices and the
audit log.

## How it is wired

`aipager config` (or `aipager setup`) adds aipager's entries to
`~/.claude/settings.json`, and the daemon checks them at every start:

- one `aipager-hook` command for each of the 16 events below, with the
  matcher `*` on the tool events (`PreToolUse`, `PostToolUse`,
  `PermissionRequest`);
- a 5-second timeout on `PreModelSwitch`, and from `aipager config`
  and `aipager setup` a 30-second one on `PermissionRequest` (the hook
  itself waits at most 20 seconds, see below); the other entries use
  Claude Code's default;
- the `statusLine` command, `aipager-statusline`.

`aipager uninstall` takes these entries out again (see
[commands](commands.md#command-line-aipager-uninstall)).

Claude Code starts a fresh `aipager-hook` process
(`aipager.dtach.notify_hook:main`) for every event. It reads the event's
JSON on stdin, adds two fields (below) and sends it to the daemon as one
datagram on a Unix socket. The socket is, in this order:
`$AIPAGER_INSTANCE_DIR/aipager.sock` for an isolated test instance,
`$AIPAGER_SOCKET_PATH`, `$XDG_RUNTIME_DIR/aipager.sock`, or
`/tmp/aipager.sock`. The send never waits: if no daemon is listening the
event is dropped, and the session monitor catches up from the session's
files.

Most events cost Claude Code about 20 to 25 ms of process start. A few
also load aipager's rule code: before a tool call (the safety check, see
[PreToolUse](#pretooluse)) about 70 ms, at the end of a turn (to clear
the turn's open mark) about 55 ms, and `UserPromptSubmit` (which writes
the turn's rules). A `PermissionRequest` deliberately waits for your
tap, up to 20 seconds.

The hook caps its own memory at 1 GB. A hook that hits the cap exits
with an error (for `PreToolUse`, the exit code Claude Code reads as a
deny) and sends the daemon a `hook_memory_cap_hit` datagram, which
becomes a notice in the chat, with a "🐞 Report this" button for the
install's owner (see [problem reports](problem-reports.md)). Set `AIPAGER_DEBUG=1` in the
session's environment to have the hook and the status line print their
diagnostics on stderr.

## What the daemon receives

The datagram is Claude Code's own hook JSON with aipager's fields added
(a few events carry more, named under each event below):

| Field | Meaning |
|---|---|
| `hook_event_name` | The event (Claude Code's field; the daemon dispatches on it). |
| `session_id`, `transcript_path`, `cwd`, ... | Claude Code's own fields, as sent. `session_id` is Claude Code's conversation id. |
| `session` | Added by aipager: its name for the session, `claude-<name>`, from the `CLAUDE_DTACH_SESSION` variable aipager sets when it starts a session. Events from a Claude Code session aipager did not start carry no name and are ignored by the daemon. |
| `sl_tokens` | Added by aipager, on every event except `MessageDisplay`, when the session's status-line file exists: `context_pct`, `total_output`, `total_input`, `current_output`, `lines_added` and `lines_removed`. |

Tool events also carry `tool_name` and `tool_input`. Every event fired
inside a subagent carries `agent_id` (and the subagent events
`agent_type`); the main loop's own events do not, and aipager uses that
to put an agent's tool calls on the agent's own row.

## Event reference

The daemon's handlers are in `aipager/dtach/hook_receiver.py`; the
hook's own work is in `aipager/dtach/notify_hook.py`.

### `SessionStart`

A session started, resumed or was compacted. A session the daemon does
not track yet is registered. After a compaction the card reads
`📦 name · Compacted: 82% → 4%` (see [PreCompact](#precompact--postcompact)). For
a new conversation the hook also clears the previous turn's open mark.

### `SessionEnd`

The session ended. It is marked GONE: it leaves the pinned status bar
and moves to the ended sessions, where `/resume` can bring it back.
`aipager session <name>` or `/new <name>` starts a new one.

### `UserPromptSubmit`

Claude took a prompt, typed in the terminal or sent from Telegram.

The hook first matches the prompt against the session's permission
notes (`/tmp/claude-notes-<session>/`, one per Telegram message aipager
typed in) and writes the turn's rules to `/tmp/claude-policy-<session>.json`,
which the [PreToolUse](#pretooluse) check reads (see
[security → team-mode enforcement](security.md#team-mode-enforcement)).
It tells the daemon which messages Claude took (a `queue_pickup`
datagram) before it forwards the event. It then prints extra context for
Claude when there is any: the reply style chosen in `/settings`, and,
for a reply to an older message, what that message said and when. A
`<task-notification>` prompt (Claude waking itself when a background
agent or command finishes) is the same job going on: it matches no
note and keeps the turn's rules.

The daemon marks the session working, opens the busy card if none is up
(a message sent from Telegram has one already), and turns the 👀 on the
messages Claude took into 👍. A message sent while a turn runs gets its
👍 when Claude folds it into that turn or starts the next turn with it.

### `PreToolUse`

A tool is about to run. This is where aipager's own safety check runs,
in the hook, before Claude Code looks at its permission rules:

- A turn started from Telegram is held to its sender's role rules and to
  the safety floor (protected paths, command patterns), which only the
  `owner` role bypasses; a turn aipager cannot attribute gets the floor;
  a prompt you typed in the terminal is not restricted. The rules are in [groups](groups.md#how-rules-work) and
  [security](security.md#team-mode-enforcement).
- When a rule denies the call, the hook prints a `deny` decision,
  Claude Code refuses the tool without asking, and the daemon is told
  (`safety_blocked`): the turn stops with `🛑 x1 · Blocked by safety
  policy: ... (stopped)`, and every later tool call in that turn is
  denied too.
- A check that fails denies, unless the session's rules could be read
  and grant the owner's bypass.

On the daemon side, the call becomes a row on the busy card (a
subagent's call goes on that agent's row), and a `Write` or `Edit`
shows a diff preview when `/settings` → Diff previews is on (off by
default). An `AskUserQuestion` call becomes the question with one button
per option; one asked by a Claude Code helper agent while no turn runs
is not shown (it is not Claude asking you).

### `PermissionRequest`

Claude Code is about to ask for permission. The busy card becomes the
prompt, with the real command or path under Claude's description of it
(see [commands → permission prompts](commands.md#permission-prompts)):
Allow, Deny, Stop, and Allow always only when Claude Code offers a
real rule to remember.

The hook answers the prompt itself when it can. It opens a short-lived
reply socket, sends its address with the event, and waits up to 20
seconds for your tap. A tap in time comes back as a `PermissionRequest`
decision (allow, allow with the rule, or deny), so Claude Code never
shows its own dialog. If no tap comes in time, the hook tells the
daemon the wait is over (`permission_reply_timeout`) and prints
nothing; it also prints nothing when the daemon cannot be reached or
anything on that path fails. Claude Code then shows its own dialog in
the terminal, and a later tap answers it by typing the keys. The audit log
(`~/.claude/aipager-audit.jsonl`) records which way each tap answered,
`"via": "hook_decision"` or `"via": "keystroke_fallback"`.

### `PostToolUse` / `PostToolUseFailure`

A tool finished, or failed. Its row on the busy card settles (a failure
is marked on the row), and an answer to an open prompt is noticed: an
old Allow or Deny for it types nothing afterwards. The session keeps the
newest 200 tool rows.

A `Bash` call Claude Code moved to the background reports a
`backgroundTaskId`. aipager then tracks it as a background shell (the
main loop's only: a call carrying `agent_id` belongs to that subagent)
until its end is seen: a `<task-notification>` with a `<status>` (in the
prompt that wakes Claude, or in the transcript when it arrives
mid-turn), a `TaskStop` or `KillShell` of its id, the session ending, or
two hours (`AIPAGER_BG_SHELL_MAX_TRACK`, seconds, default 7200). See
[commands](commands.md#idle-responses) for its row on the card.

### `Notification`

The daemon reads Claude Code's `notification_type`:

- `permission_prompt`: a slower, older signal for a permission ask. It
  shows the prompt only when `PermissionRequest` did not already.
- `idle_prompt` while a permission prompt or a question waits: the
  session stays waiting, and the chat gets `⬆️ jim · still waiting for
  your answer above`, once per wait.
- `idle_prompt` otherwise: handled like `Stop` (below). It catches a
  turn whose `Stop` was lost; an answer already delivered is never sent
  again.

### `Stop`

The turn ended. The session becomes idle, the busy card settles as your
layout says, and Claude's answer is sent (from the event's
`last_assistant_message`, or else the transcript). Messages that were
held for the turn to end go out next. Background agents and commands
that are still running keep the job open: the card stays as the job's
status and the answer says what is still running (see
[commands → agents still running](commands.md#agents-still-running-when-the-answer-goes-out)).
The hook clears the turn's open mark first, unless Claude still holds a
queued message that becomes the next turn at once.

### `StopFailure`

The turn ended on an API error. The session becomes idle and the
answer, if any, is delivered as for `Stop`; a known error (overloaded,
rate limit, ...) is shown in plain words.

### `SubagentStart` / `SubagentStop`

A subagent started or ended. Each one gets its own row on the busy card,
`🤖 <type> · <activity> · <elapsed>`, with its own tool calls folded
under it, and settles to `✅ 🤖 <type> · N tool calls · <elapsed>` when it
stops. A subagent whose `SubagentStop` never arrives is dropped once it
has been silent for 30 minutes (`AIPAGER_SUBAGENT_SILENCE`, seconds; no
event of any kind carrying its id). A working agent sends tool events
every few seconds, so this only collects one whose stop was missed (a
restart, a lost datagram); a single tool call that runs 30 minutes
without any event under that agent's id collects it too.
`AIPAGER_SUBAGENT_TTL` is accepted as an old name for the same setting.
`SubagentStop` events with no type and an unknown id arrive often and
are ignored.

### `PreCompact` / `PostCompact`

Claude is compacting its context (`trigger` is `auto` or `manual`). The
busy card reads `🔄 name · Compacting` (a new message when no card is
up), so the pause does not look like a crash. `PostCompact` ends that
state; the `📦 name · Compacted: X% → Y%` line comes from the
`SessionStart` that follows, or from the status line when that is
missed. The status line also gives the warning `⚠️ name · Context at
82% - auto-compact soon` (with a Compact button) once the context
reaches 80%, and again only after it has dropped below 30%.

### `MessageDisplay`

Claude's own text as it reaches the screen. The transcript is written
only after each round of tool calls, so this is the one current source
of what Claude is saying, and what streams its commentary into the busy
card. A subagent's text stays under its own row. The hook adds no
status-line data to this event: Claude Code waits for it while it
draws, and it fires several times per message.

### `PreModelSwitch`

Claude Code 2.1.251 and later fires this before it switches model. The
payload carries `from_model`, `to_model`, `requested_model` and `source`
(`command` for a typed `/model`, or `picker` or `sdk`). Claude Code
usually asks "Switch model?" before a switch, because the next reply has
to re-read the whole conversation. A hook that answers
`permissionDecision: "allow"` lets the switch go ahead without that
question.

`aipager-hook` answers `allow` only for a switch that aipager typed
itself, from the Mini App's Model control or the Models keyboard.
Every other switch gets no answer, so Claude Code asks as it always
has. That covers the operator's own `/model` in the terminal, the
picker, and an SDK switch.

aipager recognises its own switches with a marker file. Just before it
types `/model <name>`, the daemon writes
`aipager-modelswitch-<session>.json`:
- it goes in the directory that holds the control socket (normally
  `$XDG_RUNTIME_DIR`);
- it is written atomically, with mode 0600;
- it names the model and the Claude session id;
- it expires after 30 seconds.

The hook answers `allow` only when all of these hold:
- the marker is a file you own, and not a symlink;
- it has not expired;
- it is for the same aipager session and the same Claude session id;
- its model equals `requested_model` or `to_model`, ignoring case;
- `source` is `command`.

The hook then claims the marker with an atomic rename, so it is used
exactly once. The check reads one local file, with no socket and no
wait. The event is not forwarded to the daemon.

Claude Code 2.1.101 and later ignore hook events they don't know, so
older versions without `PreModelSwitch` are unaffected. One case can
still show the question: a switch to or from `opusplan` can make Claude
Code ask the hook twice, and only the first ask finds the marker, so
Claude Code asks "Switch model?" in the terminal.

## The status line

Not a hook event: Claude Code runs the `statusLine` command,
`aipager-statusline` (`aipager.dtach.statusline_notify:main`), each time
it redraws its status line. The command:

- writes Claude Code's status JSON to `/tmp/claude-status-<session>.json`
  (read by the hook for `sl_tokens`, by `aipager status` and by the
  daemon);
- sends the daemon a `statusline` datagram with the model, context %,
  cost, token counts and lines added and removed, which feed the busy
  card, `/status`, the Mini App, the check that a model switch went
  through, and the context warning;
- prints the terminal's status line: `[name] Opus 5.5 | 42% ctx | $1.23`.

## aipager's own datagrams

Besides the hook events, the helpers send the daemon a few messages of
their own on the same socket:

| Message | Sent by | When |
|---|---|---|
| `queue_pickup` | hook | At `UserPromptSubmit`: which Telegram messages Claude took. |
| `safety_blocked` | hook | At `PreToolUse`, when the safety check denied the call. |
| `permission_reply_timeout` | hook | A `PermissionRequest` wait ended with no tap. |
| `statusline` | status line | At each status-line redraw. |
| `hook_memory_cap_hit` | hook, status line | The process hit its memory cap. |
| `report_error` | hook, `aipager` commands | It hit an error: the error's type and its place in aipager's code only, never the event's content, for [problem reports](problem-reports.md). |

## The permission flow

```
claude              aipager-hook             aipager daemon          Telegram
  |                      |                         |                     |
  | PermissionRequest    |                         |                     |
  |--------------------->| opens a reply socket    |                     |
  |                      |------------------------>| busy card becomes   |
  |                      |                         | the prompt          |
  |                      |                         |-------------------->|
  |                      |                         |   you tap Allow     |
  |                      |                         |<--------------------|
  |                      |   allow (reply socket)  | audit log           |
  |                      |<------------------------|                     |
  |  decision: allow     |                         |                     |
  |<---------------------|                         |                     |
  | tool runs            |                         |                     |
```

With no tap within 20 seconds the hook prints nothing and Claude Code
shows its own dialog; a later tap types the answer into that dialog
through the session's terminal (`dtach -p`). Either way the tap is
recorded in the audit log and posted under the card (`✅ jim · Allowed
by @alice · Bash: ls -la /tmp`).

## See also

- [Architecture](architecture.md): where the hook receiver fits.
- [Bot commands → permission prompts](commands.md#permission-prompts): the user-facing side.
- [Security model](security.md): what the safety check does and does not cover.
