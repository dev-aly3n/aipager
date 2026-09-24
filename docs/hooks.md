# Hook events

Claude Code emits hook events at every interesting moment of a turn:
prompt submitted, tool about to run, tool finished, session ending,
etc. aipager listens to those hooks and translates them into Telegram
activity (animated busy messages, permission prompts, diff previews,
audit log entries).

The wiring is straightforward: `aipager config` patches
`~/.claude/settings.json` so each event invokes the `aipager-hook`
console script. That script
(`aipager.dtach.notify_hook:main`) reads the hook JSON on stdin and
sends a single UDP datagram to `$XDG_RUNTIME_DIR/aipager.sock` (falling back to
`/tmp/aipager.sock`). Total latency budget:
<5 ms, so claude code keeps moving even on a busy daemon.

The daemon's `HookReceiver` (`aipager/dtach/hook_receiver.py`) decodes the
datagram and dispatches on the `"event"` field. Per-event handling
is summarized below.

## Common payload fields

Every event carries:

| Field | Type | Meaning |
|---|---|---|
| `event` | str | The event type (matches the table below). |
| `session` | str | Claude Code session id, prefixed `claude-`. |
| `transcript_path` | str | Path to the JSONL transcript (Claude side). |
| `sl_tokens`, `cost_usd`, `model_name`, `context_pct`, `total_output` | mixed | Statusline snapshot, set when the event has them. |

Tool events additionally carry `tool_name` and `tool_input`. Subagent
events carry `agent_id` and `agent_type`.

## Event reference

The handlers are in `aipager/dtach/hook_receiver.py`.

### `UserPromptSubmit`

Fires the instant claude picks a prompt up — whether it was typed in
the dtach terminal or injected from Telegram. This is also where
Telegram-originated permissions are installed: the hook matches the
prompt against the session's per-message permission notes
(`/tmp/claude-notes-<session>/`, written when each message was sent),
merges what it accounts for, and writes the result to the snapshot
the `PreToolUse` check reads — see
[security → team-mode enforcement](security.md#team-mode-enforcement).
It then tells the daemon which messages were consumed.

| Aipager does | User sees |
|---|---|
| Marks the session BUSY; sends the busy message if one isn't already up (Telegram-injected prompts get theirs at send time). Reacts 👍 on each Telegram message the prompt accounted for. | The 👀 on their message flips to 👍; a live "Working…" reply. |

### `PreToolUse`

Fires before every tool call. The most important event because it
drives the **permission flow**: claude's settings tell it `Allow`,
`Ask`, or `Deny` for that tool + input.

- `Allow` (auto-approved): aipager logs the tool to `tool_history` and,
  if the tool is `Write` or `Edit` and the `/settings` **Diff previews**
  toggle is on (it is off by default), posts a diff preview. No prompt.
- `Ask` (requires confirmation): aipager edits the busy message into
  a permission prompt with inline `[✅ Allow] [❌ Deny]
  [🟢 Allow always] [⏹ Stop]` buttons (see
  [commands → permission prompts](commands.md#permission-prompts)).
- `Deny`: claude blocks the call itself; aipager just records it.

The decision lives in claude's `~/.claude/settings.json`. aipager
never decides; it relays the prompt.

### `PostToolUse` / `PostToolUseFailure`

Tool finished. aipager appends a one-line summary to the session's
`tool_history` (capped at 200 entries). On failure, the busy message
header changes to "⚠️ Tool failed" until the next event.

### `PermissionRequest` (and the legacy `permission_prompt`)

The primary, first-arriving signal for an ordinary interactive
permission ask — not, as an earlier version of this doc claimed, a
moment when no `PreToolUse` hook is in flight. aipager treats it
identically to a `PreToolUse: Ask` and shows the same keyboard.
`permission_prompt` (a `Notification` hook) is a slower fallback that
only fires if `PermissionRequest` didn't already handle it.

On the fast path, `aipager-hook` itself can answer the prompt directly:
it opens a short-lived reply socket, forwards its address alongside the
usual datagram, and waits (~20s by default) for the daemon to deliver a
verdict — allow, allow with a remembered rule ("Allow always"), or deny
— which it then prints back to Claude Code as a `PermissionRequest`
decision object, so the tool call resolves with **no** keystrokes sent
to the terminal at all. If no verdict arrives in time, the daemon can't
be reached, or anything about that path errors, the hook prints nothing
and Claude Code's own interactive dialog appears exactly as it always
has — answered by the existing `Down`/`Enter` keystroke injection when a
Telegram button is tapped after that point. The audit log
(`~/.claude/aipager-audit.jsonl`) records which path actually answered
each tap as `"via": "hook_decision"` or `"via": "keystroke_fallback"`.

### `SubagentStart` / `SubagentStop`

Claude spawned a Task subagent (or it returned). aipager increments
`active_subagents`, edits the busy message to append
`(N agents)`, and rolls subagent cost into the parent session's
`cost_usd` total.

`SubagentStop` decrements the counter. Subagents whose `Stop` never
arrives are garbage-collected once they have gone **silent** — 30 min
with no hook event of any kind carrying that agent's id
(`AIPAGER_SUBAGENT_SILENCE`, seconds). A working agent emits tool hooks
every few seconds, so it survives for as long as it keeps emitting them,
however long it runs; a genuinely missed `SubagentStop` (daemon restart,
crash, dropped datagram) is what ages out. Note the window still bounds
one uninterrupted gap: a single tool call or a nested agent that emits
nothing under this agent's id for 30 minutes collects its row too.

`AIPAGER_SUBAGENT_TTL`, which used to bound an agent's total age at 1 h,
is accepted as a deprecated alias for the silence window.

### `SessionStart` / `SessionEnd`

Session lifecycle.

- `SessionStart` registers the session if it wasn't already tracked
  (e.g. a session started outside aipager's `aipager session new`).
- `SessionEnd` marks it GONE in the pinned status. The user can
  recreate via `aipager session <name>` or `/new <name>`.

### `PreCompact` / `PostCompact`

Claude is about to compact its context window. aipager flushes a
"💬 Compacting context…" message threaded under the busy message so
users see the pause isn't a crash. `trigger` is `auto` or `user`.
`PostCompact` closes the in-flight marker; the "Compacted: X% → Y%"
summary arrives via the post-compact session start or the statusline.

### `MessageDisplay`

Claude's own prose as it reaches the screen, in paragraph-sized
chunks. This is the only *current* source of what claude is saying —
the transcript file lags until each tool round finishes — and is what
streams commentary into the busy message live.

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
- it is for the same dtach session and the same Claude session id;
- its model equals `requested_model` or `to_model`, ignoring case;
- `source` is `command`.

The hook then claims the marker with an atomic rename, so it is used
exactly once. The check reads one local file, with no socket and no
wait. The event is not forwarded to the daemon. The settings entry has
a 5-second timeout.

The entry is added on daemon start (and by `aipager config`) like every
other hook event. Claude Code 2.1.101 and later ignore hook events they
don't know, so older versions without `PreModelSwitch` are unaffected.

One case can still show the question. A switch to or from `opusplan` can
make Claude Code ask the hook twice, and only the first ask finds the
marker, so Claude Code asks "Switch model?" in the terminal.

### `statusline`

Special — not a real hook. It's emitted by the `aipager-statusline`
console script (`aipager.statusline_notify:main`), which claude code
runs every ~2 s. Updates `/tmp/claude-status-<session>.json` with the
latest model, context%, cost, output tokens, lines added/removed,
last assistant message. Read by `aipager status` and the busy-message
animator.

## The Allow / Ask / Deny flow

`PreToolUse: Ask` is the most-touched code path in the daemon. The
sequence:

```
claude               aipager daemon                 Telegram
  |                       |                            |
  |  PreToolUse (Ask)     |                            |
  |---------------------->|                            |
  |                       | edit busy msg → prompt     |
  |                       | with Allow / Deny buttons  |
  |                       |--------------------------->|
  |                       |                            |
  |                       |   user taps [✅ Allow]     |
  |                       |<---------------------------|
  |                       | audit.append(...)
  |                       | inject keystrokes into the
  |                       | session's pty (dtach) to
  |                       | select Yes / No in claude's
  |                       | own dialog
  |  resume tool call     |                            |
  |<----------------------|                            |
```

Two channels, each one-way. The datagram socket only ever carries
events **from** the hook **to** the daemon — it is fire-and-forget,
with no reply path. Your tap travels back to claude as keystrokes
injected into the session's pty, answering the same dialog you would
see in the terminal. When the `aipager-hook` helper does answer
claude directly on stdout (a rule-based deny from
`aipager/dtach/enforce.py`, or per-message context on
`UserPromptSubmit`), it computes that answer itself from local files
— it never waits on the daemon.

## See also

- [Architecture](architecture.md) — where `HookReceiver` fits.
- [Bot commands → permission prompts](commands.md#permission-prompts) — the user-facing side.
- [Security model](security.md) — why hooks aren't a privilege boundary.
