"""Black-box integration tests for design.md's reply-context feature,
driven strictly through the documented entrypoints
(``TelegramBotCore._handle_message``) and the documented response-side
surfaces (``policy_snapshot.read_snapshot``, ``SessionRegistry.
get_session_by_msg``).

Unlike the developer's own unit tests (``tests/test_session_ops_reply_
context.py``), which call ``bot._build_reply_context``/``bot._resolve_
reply_target`` directly (explicitly flagged there as something "the
Tester must not do"), every test here goes through the full handler
so it also exercises the WIRING: chat-id derivation from the real
``update.effective_chat``, ``ctx.bot.id`` threading, and the
``_inject_prompt`` -> ``write_snapshot`` chain -- none of which the
unit-level tests can prove.

``policy_snapshot.snapshot_path``/``reply_context_path`` are always
monkeypatched to ``tmp_path`` -- a live daemon runs on this machine and
must never see a test write to its real ``/tmp/claude-policy-*``/
``/tmp/claude-reply-*`` files.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.state import Status, TrackedSession

CHAT_ID = -1001  # matches mk_update's default chat_id
BOT_ID = 87654321


@pytest.fixture(autouse=True)
def _isolate_snapshot_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")


def _idle_sess(name="claude-jim", label="jim", **kw):
    s = TrackedSession(name=name, label=label, status=Status.IDLE)
    s.scope_chat_id = kw.pop("scope_chat_id", CHAT_ID)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _ctx():
    c = MagicMock()
    c.bot.id = BOT_ID
    return c


def _wire_happy_dtach(monkeypatch, bot):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()


def _reply_target(*, message_id, text="(old text)", caption=None, from_user=None):
    m = MagicMock()
    m.message_id = message_id
    m.text = text
    m.caption = caption
    m.from_user = from_user
    return m


# ===== Criterion 1: reply to the session's own latest message ==============

def test_reply_to_latest_message_no_highlight_snapshot_reply_context_empty(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _idle_sess(last_msg_id=300)
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(300, sess.name, CHAT_ID)
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("thanks", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=300)
    run_async(bot._handle_message(update, _ctx()))

    assert sess.status == Status.BUSY  # routing still happened
    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] == ""


# ===== Criterion 3: highlight produces context EVEN on the latest message ==

def test_highlight_on_latest_message_still_produces_context(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _idle_sess(last_msg_id=300)
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(300, sess.name, CHAT_ID)
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("what about this part?", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=300, text="the source text")
    update.message.quote = MagicMock(text="the exact fragment", is_manual=True)
    run_async(bot._handle_message(update, _ctx()))

    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] != ""
    assert "the exact fragment" in snap["reply_context"]
    # No fallback file for a small, non-oversized highlight.
    assert not ps.reply_context_path(sess.name).exists()


# ===== Criterion 11: absent reply_to_message, no quote -> no context =======

def test_plain_message_no_reply_no_quote_produces_no_context_block(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _idle_sess()
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("just a normal message", chat_id=CHAT_ID)
    assert update.message.reply_to_message is None
    assert update.message.quote is None
    run_async(bot._handle_message(update, _ctx()))

    # Routes exactly as a plain message: to last_active_session, injected.
    assert sess.status == Status.BUSY
    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] == ""


# ===== Criterion 7: reply to your own earlier message routes to that =======
# ===== message's session, not last_active_session ===========================

def test_reply_to_own_earlier_message_routes_to_that_messages_session(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess_a = _idle_sess(name="claude-a", label="a")
    sess_b = _idle_sess(name="claude-b", label="b")
    bot.registry._sessions[sess_a.name] = sess_a
    bot.registry._sessions[sess_b.name] = sess_b
    _wire_happy_dtach(monkeypatch, bot)

    # Turn 1: the user's own prompt to A (routed via last_active, which is
    # A for this one call), message_id=100 -- Part 1 requires this be
    # tracked automatically as a side effect of routing.
    bot.registry.last_active_session = sess_a.name
    first = mk_update("hello A", chat_id=CHAT_ID, message_id=100)
    run_async(bot._handle_message(first, _ctx()))
    assert sess_a.status == Status.BUSY
    # Confirms Part 1: the user's own message got tracked, independent of
    # any bot-sent message.
    tracked = bot.registry.get_session_by_msg(100, CHAT_ID)
    assert tracked is not None and tracked.name == sess_a.name

    # Reset for turn 2's routing assertion.
    sess_a.status = Status.IDLE
    bot.registry.last_active_session = sess_b.name

    # Turn 2: a DIFFERENT message replies to msg 100 (the user's own
    # earlier prompt). It must route to A, not to last_active (B).
    second = mk_update("following up on my own earlier message",
                       chat_id=CHAT_ID, message_id=101)
    second.message.reply_to_message = _reply_target(message_id=100, text="hello A")
    run_async(bot._handle_message(second, _ctx()))

    assert sess_a.status == Status.BUSY
    assert sess_b.status == Status.IDLE  # never touched


# ===== Criterion 12: busy_msg_id sentinels never false-positive "latest" ===

@pytest.mark.parametrize("sentinel_value", [0, -1])
def test_busy_msg_id_sentinel_never_false_positives_the_latest_test(
    mk_bot, mk_update, run_async, monkeypatch, sentinel_value,
):
    """Boundary-value probe: a contrived reply_to.message_id equal to the
    SENTINEL value itself (never a real Telegram id, which is always a
    positive integer) must not be treated as "the latest message" just
    because it happens to numerically match a sentinel stored on the
    session. Per design.md, None/0/-1 are excluded from the "is this the
    latest" candidate set entirely."""
    bot = mk_bot()
    sess = _idle_sess(last_msg_id=None, trigger_msg_id=None)
    sess.busy_msg_id = sentinel_value
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(sentinel_value, sess.name, CHAT_ID)
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("about that", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(
        message_id=sentinel_value, text="an old real message",
    )
    run_async(bot._handle_message(update, _ctx()))

    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] != "", (
        f"a reply_to.message_id == sentinel {sentinel_value} was wrongly "
        "treated as 'the latest message' and suppressed"
    )


def test_reply_to_a_genuine_positive_busy_msg_id_is_treated_as_latest(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Positive control for the parametrized sentinel test above: a REAL
    (positive) busy_msg_id must still correctly suppress context when
    replied to, proving the exclusion is specific to the sentinels and
    not an accidental "always produce context" bug."""
    bot = mk_bot()
    sess = _idle_sess(last_msg_id=None, trigger_msg_id=None)
    sess.busy_msg_id = 555
    bot.registry._sessions[sess.name] = sess
    bot.registry.track_message(555, sess.name, CHAT_ID)
    _wire_happy_dtach(monkeypatch, bot)

    update = mk_update("ok", chat_id=CHAT_ID)
    update.message.reply_to_message = _reply_target(message_id=555, text="the busy prompt")
    run_async(bot._handle_message(update, _ctx()))

    snap = ps.read_snapshot(sess.name)
    assert snap is not None
    assert snap["reply_context"] == ""
