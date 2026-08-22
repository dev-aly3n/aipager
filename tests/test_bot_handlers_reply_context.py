"""Black-box handler-level tests for design.md's "reply context" feature,
driving the three reply-bearing handlers exactly as entrypoints.md
describes (a constructed ``Update``, via ``mk_update``/``MagicMock``) and
observing outcomes only through the documented seams: the policy
snapshot file, ``SessionRegistry.get_session_by_msg``, and outbound
Telegram calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import policy_snapshot as ps
from aipager.state import Status, TrackedSession

BOT_ID = 424242


def _ctx():
    c = MagicMock()
    c.bot.id = BOT_ID
    return c


def _isolate_snapshot(monkeypatch, tmp_path):
    # BOTH paths, always. Patching only snapshot_path let the older-message
    # branch (allow_file=True) call the real write_reply_context_file and
    # drop /tmp/claude-reply-claude-jim.txt on the actual filesystem — on a
    # machine running a live daemon, where a colliding session name would
    # clobber that session's real file.
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")


def _wire_common(monkeypatch, bot):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter", AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()


# ---- criterion 6 / staleness guard -----------------------------------------

def test_plain_message_after_a_reply_clears_the_stale_reply_context(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    """THE staleness-guard test (design.md success criterion 6): a reply
    turn writes a non-empty reply_context; the VERY NEXT plain-message
    turn for the same session must overwrite it with "". Deleting the
    ``reply_context=""`` default on ``_inject_prompt`` (or on
    ``write_snapshot``) must make this test fail — see implementation.md
    for the mutation verification."""
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = mk_bot()
    _wire_common(monkeypatch, bot)
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_msg_id = 1  # so the reply below targets an OLDER message
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"

    update1 = mk_update("pointing at something")
    update1.message.reply_to_message = MagicMock(
        message_id=2, text="an older message", caption=None, from_user=None,
    )
    run_async(bot._handle_message(update1, _ctx()))
    snap1 = ps.read_snapshot("claude-jim")
    assert snap1 is not None
    assert snap1["reply_context"] != ""

    # Simulate the session finishing that turn and going back to IDLE —
    # otherwise the second update would just queue behind the first
    # (never reaching _inject_prompt at all) and this test would prove
    # nothing about the staleness guard.
    sess.status = Status.IDLE

    update2 = mk_update("just a normal follow-up")  # reply_to_message=None (default)
    run_async(bot._handle_message(update2, _ctx()))
    snap2 = ps.read_snapshot("claude-jim")
    assert snap2 is not None
    assert snap2["reply_context"] == ""


# ---- criterion 1 -------------------------------------------------------

def test_reply_to_latest_message_produces_no_context(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = mk_bot()
    _wire_common(monkeypatch, bot)
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.last_msg_id = 300
    bot.registry._sessions["claude-jim"] = sess

    update = mk_update("ok")
    update.message.reply_to_message = MagicMock(
        message_id=300, text="(latest)", caption=None, from_user=None,
    )
    run_async(bot._handle_message(update, _ctx()))
    snap = ps.read_snapshot("claude-jim")
    assert snap is not None
    assert snap["reply_context"] == ""


# ---- criterion 7 --------------------------------------------------------

def test_reply_to_own_earlier_message_routes_to_that_messages_session(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    _wire_common(monkeypatch, bot)
    target = TrackedSession(name="claude-target", label="target", status=Status.IDLE)
    other = TrackedSession(name="claude-other", label="other", status=Status.IDLE)
    bot.registry._sessions["claude-target"] = target
    bot.registry._sessions["claude-other"] = other
    bot.registry.last_active_session = "claude-other"  # would be the wrong answer
    bot.registry.track_message(500, "claude-target", -1001)

    update = mk_update("reply text", chat_id=-1001)
    update.message.reply_to_message = MagicMock(
        message_id=500, text="(target's earlier message)", caption=None, from_user=None,
    )
    run_async(bot._handle_message(update, _ctx()))
    assert target.status == Status.BUSY
    assert other.status == Status.IDLE


# ---- criterion 8 (handler level) -------------------------------------------

def test_cross_chat_message_id_collision_never_crosses_chats(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Both directions checked — a test that only ever drives ONE of the
    two colliding chats can pass by iteration-order coincidence even
    when scoping is completely broken (verified against a real mutation
    while writing this test; see implementation.md)."""
    bot = mk_bot()
    _wire_common(monkeypatch, bot)
    a = TrackedSession(name="claude-a", label="a", status=Status.IDLE)
    a.scope_chat_id = 111
    b = TrackedSession(name="claude-b", label="b", status=Status.IDLE)
    b.scope_chat_id = 222
    bot.registry._sessions["claude-a"] = a
    bot.registry._sessions["claude-b"] = b
    bot.registry.track_message(700, "claude-a", 111)
    bot.registry.track_message(700, "claude-b", 222)

    update_a = mk_update("hi a", chat_id=111)
    update_a.message.reply_to_message = MagicMock(
        message_id=700, text="whatever", caption=None, from_user=None,
    )
    run_async(bot._handle_message(update_a, _ctx()))
    assert a.status == Status.BUSY
    assert b.status == Status.IDLE

    update_b = mk_update("hi b", chat_id=222)
    update_b.message.reply_to_message = MagicMock(
        message_id=700, text="whatever", caption=None, from_user=None,
    )
    run_async(bot._handle_message(update_b, _ctx()))
    assert b.status == Status.BUSY


# ---- criterion 11 -----------------------------------------------------

def test_absent_reply_to_message_and_no_quote_is_not_a_reply(
    mk_bot, mk_update, run_async, monkeypatch, tmp_path,
):
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = mk_bot()
    _wire_common(monkeypatch, bot)
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.last_active_session = "claude-jim"

    update = mk_update("just a plain message")  # reply_to_message=None, quote=None
    run_async(bot._handle_message(update, _ctx()))
    assert sess.status == Status.BUSY  # routed via last_active, exactly as a plain message
    snap = ps.read_snapshot("claude-jim")
    assert snap is not None
    assert snap["reply_context"] == ""


# ---- criterion 14 -----------------------------------------------------

def test_gone_target_shows_not_found_with_a_resume_button(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """The Resume button carries the short indexed form, not
    `{name}:resume` — no `{name}:<verb>` form can fit Telegram's 64-byte
    callback_data cap (design.md), so what must hold is the DESTINATION
    (which session the button reaches), not the literal encoded string."""
    from aipager.bot import session_parity

    bot = mk_bot()
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.GONE)
    sess.last_msg_id = 300
    bot.registry._sessions["claude-jim"] = sess

    update = mk_update("bring it back")  # default chat_id=-1001
    update.message.reply_to_message = MagicMock(
        message_id=300, text="(old)", caption=None, from_user=None,
    )
    run_async(bot._handle_message(update, _ctx()))

    args, kwargs = update.message.reply_text.await_args
    assert args[0] == "⚠️ Session 'claude-jim' not found"
    kb = kwargs["reply_markup"]
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    dests = []
    for cb in cbs:
        _sentinel, kind, idx, verb = cb.split(":", 3)
        assert (_sentinel, kind) == ("_", "sx"), f"unexpected callback form: {cb!r}"
        resolved = session_parity._resolve_pref_index(bot, -1001, idx)
        dests.append((resolved.name if resolved is not None else None, verb))
    assert (sess.name, "resume") in dests
