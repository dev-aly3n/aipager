# aipager — working notes for Claude

Telegram remote-control daemon for Claude Code sessions. Claude runs inside a
detached `dtach` PTY; the daemon relays its state to Telegram and injects
prompts back. One asyncio process, no database, no worker pool.

## Layout

- `aipager/bot/` — the Telegram side. `notify.py` (delivery + job lifecycle),
  `animation.py` (the busy card renderer), `handlers.py` (messages/files),
  `callbacks.py` (button taps), `keyboards.py`, `session_ops.py` (prompt
  injection, stop/kill), `transport.py` (Telegram primitives + shared helpers).
- `aipager/dtach/` — the Claude side. `notify_hook.py` is the `aipager-hook`
  binary Claude Code runs on every hook event; `hook_receiver.py` is the
  daemon's UDP datagram listener; `inject.py` drives the PTY.
- `aipager/state.py` — `TrackedSession` + `SessionRegistry`, persisted to
  `~/.claude/aipager-sessions.json`. `session_monitor.py` ticks every 2s.
- `aipager/miniapp/` — the dashboard served on 127.0.0.1:8765.
- `tests/` — unit tests beside `tests/integration/<feature>/` scenario dirs
  and `tests/e2e/` (opt-in, drives real Claude).

## Running the tests

```
systemd-run --user --scope -q -p MemoryMax=2G -p MemorySwapMax=0 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
ruff check aipager tests
```

Always cap the memory: an unbounded mock loop has OOM-killed this machine.
Never patch `asyncio.sleep` or `create_task` through a module path —
`aipager.bot.notify.asyncio` IS the global module, and doing so has hung the
suite twice. No test may reach the real Telegram API, spawn a real `claude`
or `dtach`, touch a socket under `/tmp`, or write to real `~/.claude/` or
`~/.config/aipager/`; `tests/conftest.py`'s `_never_spawn_real_dtach` fixture
enforces the first two and must not be weakened.

## Things that are true and non-obvious

- **Claude Code's transcript is written lazily.** It flushes only after a
  tool-result round, so during a long tool call the file looks finished and
  quiet. Any check that reads it to decide "the turn ended" must first ask
  whether work is in flight (`TrackedSession.work_in_flight`).
- **Background agents end the turn.** The `Agent` tool can launch
  asynchronously; Stop fires while the agent runs, and Claude later wakes
  itself with a `<task-notification>` prompt. A *job* is the prompt plus its
  continuations — see `job_background_open()`. Hook events fired inside a
  subagent carry `agent_id`; the parent's own do not.
- **Phantom `SubagentStop` events** (empty type, unknown id, 0.0s) arrive
  constantly and are tolerated by design.
- **The permission dialog's row order is not stable across Claude Code
  releases.** Answering it by counting arrow keys is fragile: 2.1.247 added
  "switch to auto mode", 2.1.257 added the outside-reads dialog, and a
  `setMode` suggestion turns row 2 into a session-wide mode switch. Gate any
  "always" affordance on `permission_suggestions` carrying a real rule.
- **A message sent while a turn is running** is absorbed by that turn without
  a fresh `UserPromptSubmit`, so anything keyed to prompt submission must not
  assume it will fire.

## Conventions

- Commits: one-line lowercase imperative subject, no body, no co-author
  trailer.
- Every guard gets a test that fails when the guard is removed. Verify it:
  break the guard, watch the named test fail, restore, confirm the tree is
  byte-identical. This project has documented dozens of tests that passed for
  reasons unrelated to their names.
- User-facing behaviour changes go in `CHANGELOG.md` under `[Unreleased]` and,
  where they touch commands or hooks, in `docs/`.
