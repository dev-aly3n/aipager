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

Emitted by claude when a tool needs user approval at a moment when
no `PreToolUse` hook is in flight (e.g. headless flows). aipager
treats it identically to a `PreToolUse: Ask` and shows the same
keyboard.

### `SubagentStart` / `SubagentStop`

Claude spawned a Task subagent (or it returned). aipager increments
`active_subagents`, edits the busy message to append
`(N agents)`, and rolls subagent cost into the parent session's
`cost_usd` total.

`SubagentStop` decrements the counter. Subagents whose `Stop` never
arrives are garbage-collected after 1 h
(`AIPAGER_SUBAGENT_TTL`, seconds).

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
