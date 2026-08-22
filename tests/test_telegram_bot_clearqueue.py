"""Tests for `/clearqueue` (item 3.3) and `/kill` confirmation flow
(item 3.2)."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager.state import SessionRegistry, Status, TrackedSession


# ----- /clearqueue -----

def test_clearqueue_no_active_session(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    bot = mk_bot(registry)
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    assert "No active session" in update.message.reply_text.await_args.args[0]


def test_clearqueue_empty_queue(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = mk_bot(registry)
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args.args[0]
    assert "Nothing to clear" in msg
    assert "jim" in msg


def test_clearqueue_drops_entries(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("a", 100)
    sess.queue_prompt("b", 101)
    sess.queue_prompt("c", 102)
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = mk_bot(registry)
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    assert sess.pending_queue == []
    msg = update.message.reply_text.await_args.args[0]
    assert "Cleared 3" in msg
    assert "messages" in msg  # plural


def test_clearqueue_singular_message(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("only one", 100)
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    bot = mk_bot(registry)
    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Cleared 1 queued message " in msg  # singular, no trailing "s"


def test_clearqueue_also_clears_outstanding_notes_combined_count(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """design.md "queue handoff": /clearqueue's count includes what
    Claude itself is holding, not just aipager's own pending_queue —
    the same combined-count primitive Stop uses."""
    from aipager import policy_snapshot as ps

    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("a", 100)
    registry._sessions["claude-jim"] = sess
    registry.last_active_session = "claude-jim"
    ps.write_note("claude-jim", None, None, None, msg_id=9, chat_id=1,
                  sender_key=(1, 1), body="note text", raw_text="note text")
    bot = mk_bot(registry)
    keys = []
    async def _send_keys(name, k):
        keys.append(k)
        return True
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)

    update = mk_update("/clearqueue")
    run_async(bot._handle_clearqueue_cmd(update, MagicMock()))

    msg = update.message.reply_text.await_args.args[0]
    assert "Cleared 2" in msg  # 1 queued + 1 note
    assert sess.pending_queue == []
    assert ps.list_outstanding_notes("claude-jim") == []
    assert keys == ["Escape", "KillLine"]  # never a second/interrupt Escape


# ----- /kill confirmation flow -----

def test_kill_with_label_shows_confirmation(monkeypatch, mk_bot, mk_update, run_async):
    """The Kill/Cancel buttons carry the short indexed form, not
    `{name}:<verb>` — no `{name}:<verb>` form can fit Telegram's 64-byte
    callback_data cap (design.md), so what must hold is the DESTINATION
    (which session a button reaches), not the literal encoded string."""
    from aipager.bot import session_parity

    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/kill jim")  # default chat_id=-1001
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    call = update.message.reply_text.await_args
    text = call.args[0] if call.args else call.kwargs.get("text", "")
    # Should ask for confirmation, not kill immediately
    assert "Kill session" in text
    keyboard = call.kwargs.get("reply_markup")
    assert keyboard is not None
    # Inspect the inline keyboard buttons
    buttons = keyboard.inline_keyboard[0]
    dests = []
    for b in buttons:
        _sentinel, kind, idx, verb = b.callback_data.split(":", 3)
        assert (_sentinel, kind) == ("_", "sx"), f"unexpected callback form: {b.callback_data!r}"
        resolved = session_parity._resolve_pref_index(bot, -1001, idx)
        dests.append((resolved.name if resolved is not None else None, verb))
    assert (sess.name, "kill-confirm") in dests
    assert (sess.name, "kill-cancel") in dests


def test_kill_unknown_session_friendly_error(monkeypatch, mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    # No sessions registered
    bot = mk_bot(registry)
    update = mk_update("/kill ghost")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Unknown" in msg or "gone" in msg


def test_kill_already_gone_session_friendly_error(mk_bot, mk_update, run_async):
    registry = SessionRegistry()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    registry._sessions["claude-jim"] = sess
    bot = mk_bot(registry)
    update = mk_update("/kill jim")
    run_async(bot._handle_kill_cmd(update, MagicMock()))
    msg = update.message.reply_text.await_args.args[0]
    assert "Unknown" in msg or "gone" in msg
