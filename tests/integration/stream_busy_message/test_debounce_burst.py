"""Integration tests: debounce / burst safety.

Covers the contract from entrypoints.md "Observable state — hook events and debounce":

- Any of tool_use, tool_done, tool_failed, subagent_start, subagent_stop sets
  stream_dirty = True.
- A burst of five tool events inside one second produces at most one call to
  edit_message_text_rich.
- A successful edit updates last_tool_edit_at and clears stream_dirty.
- When the debounce blocks an event, stream_dirty stays True.

These are BLACK-BOX tests.  We drive the public notify() interface with the HTTP
boundary monkeypatched.  No reading of implementation internals.

Flakiness note: time is monkeypatched via monkeypatch on time.monotonic to eliminate
wall-clock dependence.  Tests are deterministic.
"""

from __future__ import annotations

import time

from aipager.state import Status, TrackedSession


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sess(label="dev", busy_msg_id=42):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    s.scope_kind = "dm"
    s.scope_chat_id = 12345
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic() - 10
    return s


# ── SC-DEB-1: tool_use sets stream_dirty ─────────────────────────────────────

def test_tool_use_sets_stream_dirty(mk_bot, run_async, monkeypatch):
    """entrypoints.md: tool_use must set stream_dirty = True."""
    bot = mk_bot()
    sess = _sess()
    sess.stream_dirty = False
    # Freeze time so debounce window has NOT elapsed (prevents edit, keeps dirty)
    now = time.monotonic()
    sess.last_tool_edit_at = now
    monkeypatch.setattr("aipager.bot.notify.time.monotonic", lambda: now)

    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Read: /x",
        "tool_name": "Read",
        "tool_input_full": None,
    }))

    assert sess.stream_dirty is True, "tool_use must set stream_dirty = True"


# ── SC-DEB-2: tool_done sets stream_dirty ────────────────────────────────────

def test_tool_done_sets_stream_dirty(mk_bot, run_async, monkeypatch):
    """entrypoints.md: tool_done must set stream_dirty = True."""
    bot = mk_bot()
    sess = _sess()
    sess.tool_history = [("Read: /x", False)]
    sess.stream_dirty = False
    now = time.monotonic()
    sess.last_tool_edit_at = now
    monkeypatch.setattr("aipager.bot.notify.time.monotonic", lambda: now)

    run_async(bot.notify(sess, "tool_done", {
        "tool_name": "Read",
        "tool_summary": "Read: /x",
    }))

    assert sess.stream_dirty is True, "tool_done must set stream_dirty = True"


# ── SC-DEB-3: tool_failed sets stream_dirty ──────────────────────────────────

def test_tool_failed_sets_stream_dirty(mk_bot, run_async, monkeypatch):
    """entrypoints.md: tool_failed must set stream_dirty = True."""
    bot = mk_bot()
    sess = _sess()
    sess.tool_history = [("Bash: ls", False)]
    sess.stream_dirty = False
    now = time.monotonic()
    sess.last_tool_edit_at = now
    monkeypatch.setattr("aipager.bot.notify.time.monotonic", lambda: now)

    run_async(bot.notify(sess, "tool_failed", {
        "tool_name": "Bash",
        "tool_summary": "Bash: ls",
    }))

    assert sess.stream_dirty is True, "tool_failed must set stream_dirty = True"


# ── SC-DEB-4: subagent_start sets stream_dirty ───────────────────────────────

def test_subagent_start_sets_stream_dirty(mk_bot, run_async, monkeypatch):
    """entrypoints.md: subagent_start must set stream_dirty = True."""
    bot = mk_bot()
    sess = _sess()
    sess.stream_dirty = False
    now = time.monotonic()
    sess.last_tool_edit_at = now
    monkeypatch.setattr("aipager.bot.notify.time.monotonic", lambda: now)

    run_async(bot.notify(sess, "subagent_start", {
        "agent_id": "a1",
        "agent_type": "explore",
    }))

    assert sess.stream_dirty is True, "subagent_start must set stream_dirty = True"


# ── SC-DEB-5: subagent_stop sets stream_dirty ────────────────────────────────

def test_subagent_stop_sets_stream_dirty(mk_bot, run_async, monkeypatch):
    """entrypoints.md: subagent_stop must set stream_dirty = True."""
    bot = mk_bot()
    sess = _sess()
    sess.tool_history = [("🤖 explore", False)]
    sess.stream_dirty = False
    now = time.monotonic()
    sess.last_tool_edit_at = now
    monkeypatch.setattr("aipager.bot.notify.time.monotonic", lambda: now)

    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "a1",
        "agent_type": "explore",
        "elapsed": 3.0,
        "history_idx": 0,
    }))

    assert sess.stream_dirty is True, "subagent_stop must set stream_dirty = True"


# ── SC-DEB-6: Burst of 5 tool_use events → at most 1 HTTP call ───────────────

def test_burst_of_five_tool_events_produces_at_most_one_edit(mk_bot, run_async, monkeypatch):
    """entrypoints.md: A burst of five tool events inside one second produces
    at most one call to edit_message_text_rich.

    Time is monkeypatched so all events appear to arrive at the same instant
    (after the debounce window has elapsed once for the first event).
    """
    import aipager.bot.rich_message as rm_mod

    bot = mk_bot()
    sess = _sess()
    # Reset debounce — first event should trigger an edit
    sess.last_tool_edit_at = 0.0
    sess.stream_last_rendered = ""

    http_call_count = 0
    # After the first call, advance last_tool_edit_at so subsequent calls
    # within the same "second" are debounced
    fixed_time = [time.monotonic()]

    async def _fake_post(method, payload):
        nonlocal http_call_count
        http_call_count += 1
        # Simulate that after the first edit, time has advanced only a tiny bit
        # (still inside the debounce window for subsequent events)
        fixed_time[0] += 0.01
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    # Freeze monotonic so all 5 events arrive "at the same time" after the first edit

    def _controlled_time():
        return fixed_time[0]

    monkeypatch.setattr("aipager.bot.notify.time.monotonic", _controlled_time)

    # First event fires the edit (debounce window was 0, so elapsed >= interval)
    for i in range(5):
        run_async(bot.notify(sess, "tool_use", {
            "tool_summary": f"Read: /file{i}.py",
            "tool_name": "Read",
            "tool_input_full": None,
        }))

    assert http_call_count <= 1, (
        f"Burst of 5 tool events produced {http_call_count} HTTP calls; "
        "at most 1 allowed per the debounce contract"
    )


# ── SC-DEB-7: Debounce blocked → stream_dirty stays True ─────────────────────

def test_debounce_blocked_stream_dirty_stays_true(mk_bot, run_async, monkeypatch):
    """entrypoints.md: When the debounce blocks an event, stream_dirty stays True
    and the next animation tick issues the edit."""
    import aipager.bot.rich_message as rm_mod

    bot = mk_bot()
    sess = _sess()
    sess.stream_dirty = False
    # Freeze time at the point of a recent edit — debounce window NOT elapsed
    fixed_now = time.monotonic()
    sess.last_tool_edit_at = fixed_now  # just edited

    http_calls = []

    async def _fake_post(method, payload):
        http_calls.append(method)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)
    monkeypatch.setattr("aipager.bot.notify.time.monotonic", lambda: fixed_now)

    # Fire a tool_use — debounce should block the edit
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Read: /x.py",
        "tool_name": "Read",
        "tool_input_full": None,
    }))

    # No HTTP call should have been made (debounced)
    assert len(http_calls) == 0, (
        "Expected debounce to block the edit, but an HTTP call was made"
    )
    # But stream_dirty must be True so the next tick will pick it up
    assert sess.stream_dirty is True, (
        "stream_dirty should remain True when debounce blocks an edit"
    )


# ── SC-DEB-8: Successful edit clears stream_dirty ────────────────────────────

def test_successful_edit_clears_stream_dirty(mk_bot, run_async, monkeypatch):
    """entrypoints.md: A successful edit updates last_tool_edit_at and clears
    stream_dirty."""
    import aipager.bot.rich_message as rm_mod

    bot = mk_bot()
    sess = _sess()
    sess.stream_dirty = True
    sess.stream_last_rendered = ""
    # Ensure debounce window has elapsed
    sess.last_tool_edit_at = 0.0

    async def _fake_post(method, payload):
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm_mod, "_post", _fake_post)

    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: ls",
        "tool_name": "Bash",
        "tool_input_full": None,
    }))

    assert sess.stream_dirty is False, (
        "stream_dirty should be cleared after a successful edit"
    )
    assert sess.last_tool_edit_at > 0.0, (
        "last_tool_edit_at should be updated after a successful edit"
    )
