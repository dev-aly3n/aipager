# Hook events

Claude Code emits hook events at every interesting moment of a turn:
prompt submitted, tool about to run, tool finished, session ending,
etc. aipager listens to those hooks and translates them into Telegram
activity (animated busy messages, permission prompts, diff previews,
audit log entries).

The wiring is straightforward: `aipager config` patches
`~/.claude/settings.json` so each event invokes the `aipager-hook`
console script. That script
(`aipager.notify_hook:main`) reads the hook JSON on stdin and sends
a single UDP datagram to `/tmp/aipager.sock`. Total latency budget:
<5 ms, so claude code keeps moving even on a busy daemon.

The daemon's `HookReceiver` (`aipager/hook_receiver.py`) decodes the
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

The handlers are in `aipager/hook_receiver.py:222-438`.

### `UserPromptSubmit`

Fires the instant the user submits a prompt in claude — whether they
typed it in the dtach terminal or aipager injected it from Telegram.

| Aipager does | User sees |
|---|---|
| Marks the session BUSY. Sends or edits the "🟡 Working…" busy message with cost + agent count. | A live "Working…" pinned reply in Telegram. |

### `PreToolUse`

Fires before every tool call. The most important event because it
drives the **permission flow**: claude's settings tell it `Allow`,
`Ask`, or `Deny` for that tool + input.

- `Allow` (auto-approved): aipager logs the tool to `tool_history` and
  posts a diff preview if the tool is `Write` or `Edit`. No prompt.
- `Ask` (requires confirmation): aipager edits the busy message into
  a permission prompt with inline `[✅ Allow] [❌ Deny] [➡️ Continue]`
  buttons (see [commands → permission prompts](commands.md#permission-prompts)).
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
(`AIPAGER_SUBAGENT_TTL`).

### `SessionStart` / `SessionEnd`

Session lifecycle.

- `SessionStart` registers the session if it wasn't already tracked
  (e.g. a session started outside aipager's `aipager session new`).
- `SessionEnd` marks it GONE in the pinned status. The user can
  recreate via `aipager session <name>` or `/new <name>`.

### `PreCompact`

Claude is about to compact its context window. aipager flushes a
"💬 Compacting context…" message threaded under the busy message so
users see the pause isn't a crash. `trigger` is `auto` or `user`.

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
  |                       | with Allow/Deny/Continue   |
  |                       |--------------------------->|
  |                       |                            |
  |                       |   user taps [✅ Allow]     |
  |                       |<---------------------------|
  |                       | audit.append(action="allow")
  |                       | write hookSpecificOutput=approve
  |                       | to stdout (claude reads it)
  |  resume tool call     |                            |
  |<----------------------|                            |
```

The hookSpecificOutput JSON goes back to claude via the
`aipager-hook` helper's stdout — the daemon writes it back to the
helper over the same datagram socket, the helper relays it to claude.
See `aipager/notify_hook.py` for the bidirectional protocol.

## See also

- [Architecture](architecture.md) — where `HookReceiver` fits.
- [Bot commands → permission prompts](commands.md#permission-prompts) — the user-facing side.
- [Security model](security.md) — why hooks aren't a privilege boundary.
