"""The Compacted notice never claims the context dropped to 0%.

Right after a compaction Claude Code's status line reads 0 until the next
message is sent, so every compaction reached compact_done with an after
of 0 and the notice read "Compacted: 22% → 0%" (seen as the conversation
being wiped). An after of 0 now says "Compacted from 22%", and a
compaction between turns gets the size added to its message by the first
real status line reading after it.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.notify import COMPACTED, COMPACTED_FROM
from aipager.bot.transport import resolve_chat_id
from aipager.dtach import hook_receiver as hr
from aipager.i18n import core
from aipager.state import SessionRegistry, Status, TrackedSession

SESSION = "claude-alpha"


def _sess(*, status=Status.IDLE, busy_msg_id=None):
    s = TrackedSession(name=SESSION, label="alpha", status=status)
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic()
    s.claude_session_id = "conv-a"
    return s


def _wire(bot, monkeypatch, *, edit_result=True):
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=555))
    bot._app.bot.delete_message = AsyncMock()
    bot._animate_compact = AsyncMock()
    bot._edit_busy_raw = AsyncMock(return_value=edit_result)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._start_animation = MagicMock()
    monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)


def _compact(bot, run_async, sess, before, after):
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))
    run_async(bot.notify(sess, "compact_done", {"before_pct": before, "after_pct": after}))


def _last_text(bot):
    return bot._edit_busy_raw.await_args.args[1]


# ===== the notice's text =====================================================

@pytest.mark.parametrize("before, after, shown, hidden", [
    (22, 0, "Compacted from 22%", "→"),
    (22, 3, "Compacted: 22% → 3%", "from"),
    (0, 0, "Compacted", "%"),
    (0, 4, "Compacted", "%"),
])
def test_the_notice_shows_only_measured_sizes(
    mk_bot, run_async, monkeypatch, before, after, shown, hidden,
):
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch)
    _compact(bot, run_async, sess, before, after)
    text = _last_text(bot)
    assert shown in text
    assert hidden not in text.split("·", 1)[1]


def test_a_compaction_in_a_turn_says_from_on_its_card_and_owes_nothing(
    mk_bot, run_async, monkeypatch,
):
    """The card goes back to the turn's animation, so the size is never
    added to it later."""
    bot = mk_bot()
    sess = _sess(status=Status.BUSY, busy_msg_id=42)
    sess.push_compacting(42, time.monotonic(), deadline_seconds=180.0)
    _wire(bot, monkeypatch)
    run_async(bot.notify(sess, "compact_done", {"before_pct": 97, "after_pct": 0}))
    assert "Compacted from 97%" in _last_text(bot)
    bot._start_animation.assert_called_once()
    assert sess.compacted_size_pending is None


def test_rtl_catalogs_keep_the_mirrored_arrow():
    for code in ("fa", "ar"):
        text = core.get(code).html(COMPACTED, session="x1", before="22%", after="3%")
        assert "←" in text and "→" not in text, (code, text)
        text = core.get(code).html(COMPACTED_FROM, session="x1", before="22%")
        assert "→" not in text and "←" not in text, (code, text)


# ===== between turns: the size is added later ================================

def test_an_idle_compaction_owes_its_message_the_size(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch)
    _compact(bot, run_async, sess, 22, 0)
    assert sess.compacted_size_pending == (555, resolve_chat_id(sess), 22, "conv-a")


def test_a_message_it_had_to_send_is_owed_the_size_too(mk_bot, run_async, monkeypatch):
    """compacting could not send: compact_done's own message gets it."""
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=321))
    run_async(bot.notify(sess, "compact_done", {"before_pct": 22, "after_pct": 0}))
    assert sess.compacted_size_pending == (321, resolve_chat_id(sess), 22, "conv-a")


@pytest.mark.parametrize("before, after", [(0, 0), (22, 3)])
def test_nothing_is_owed_when_there_is_nothing_to_add(
    mk_bot, run_async, monkeypatch, before, after,
):
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch)
    _compact(bot, run_async, sess, before, after)
    assert sess.compacted_size_pending is None


def test_nothing_is_owed_to_a_message_that_is_gone(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch, edit_result=None)
    _compact(bot, run_async, sess, 22, 0)
    assert sess.compacted_size_pending is None


def test_a_refused_edit_still_owes_the_size(mk_bot, run_async, monkeypatch):
    """A transient refusal (False, a flood mute) leaves the message as it
    was: the size added later still completes it."""
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch, edit_result=False)
    _compact(bot, run_async, sess, 22, 0)
    assert sess.compacted_size_pending == (555, resolve_chat_id(sess), 22, "conv-a")


@pytest.mark.parametrize("change", ["cleared", "ended"])
def test_nothing_is_owed_when_the_conversation_moved_on_during_the_edit(
    mk_bot, run_async, monkeypatch, change,
):
    """/clear's hooks (or the session's end) landing while the edit is
    out: the new conversation's first reading must not size the old
    message."""
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch)
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))

    async def _edit(*args, **kwargs):
        if change == "cleared":
            sess.claude_session_id = "conv-b"
        else:
            sess.status = Status.GONE
        return True

    bot._edit_busy_raw = AsyncMock(side_effect=_edit)
    run_async(bot.notify(sess, "compact_done", {"before_pct": 22, "after_pct": 0}))
    assert sess.compacted_size_pending is None


def test_nothing_is_owed_when_a_new_compaction_began_during_the_edit(
    mk_bot, run_async, monkeypatch,
):
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch)
    run_async(bot.notify(sess, "compacting", {"trigger": "auto"}))

    async def _edit(*args, **kwargs):
        sess.pre_compact_pct = 30
        sess.compact_started_at = time.monotonic()
        return True

    bot._edit_busy_raw = AsyncMock(side_effect=_edit)
    run_async(bot.notify(sess, "compact_done", {"before_pct": 22, "after_pct": 0}))
    assert sess.compacted_size_pending is None


def _size(bot, run_async, sess, after, *, conversation="conv-a", pending=None):
    pending = pending or (555, resolve_chat_id(sess), 22, "conv-a")
    run_async(bot.notify(sess, "compact_size", {
        "pending": pending, "after_pct": after, "session_id": conversation}))


def test_the_size_completes_that_message(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch)
    _size(bot, run_async, sess, 18)
    bot._edit_busy_raw.assert_awaited_once()
    msg_id, text = bot._edit_busy_raw.await_args.args[:2]
    assert msg_id == 555
    assert "Compacted: 22% → 18%" in text
    assert bot._edit_busy_raw.await_args.kwargs["chat_id"] == resolve_chat_id(sess)
    bot._app.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize("case", ["another_conversation", "compacting_again",
                                  "compaction_started", "live_card", "no_size"])
def test_the_size_is_not_added_when_it_no_longer_belongs(
    mk_bot, run_async, monkeypatch, case,
):
    bot = mk_bot()
    sess = _sess()
    _wire(bot, monkeypatch)
    after, conversation = 18, "conv-a"
    if case == "another_conversation":
        conversation = "conv-b"
    elif case == "compacting_again":
        sess.pre_compact_pct = 40
    elif case == "compaction_started":
        sess.compact_started_at = time.monotonic()
    elif case == "live_card":
        sess.busy_msg_id = 555
    else:
        after = 0
    _size(bot, run_async, sess, after, conversation=conversation)
    bot._edit_busy_raw.assert_not_awaited()


# ===== the hook receiver: which reading adds it ==============================

@pytest.fixture
def receiver(tmp_state_file):
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    return registry, hr.HookReceiver(registry, notify_fn), notify_fn


_seq = iter(range(1, 1_000_000))


def _send(recv, run_async, **fields):
    fields.setdefault("session", SESSION)
    fields["seq"] = next(_seq)  # past the receiver's duplicate filter
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def _statusline(recv, run_async, pct, conversation="conv-a"):
    _send(recv, run_async, type="statusline", context_pct=pct, session_id=conversation)


def _sizes(notify_fn):
    return [c.args[2] for c in notify_fn.await_args_list if c.args[1] == "compact_size"]


def _owe(registry):
    sess = registry.get_or_create(SESSION)
    sess.compacted_size_pending = (555, 1, 22, "conv-a")
    return sess


def test_the_first_real_reading_adds_the_size_once(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = _owe(registry)
    _statusline(recv, run_async, 0)
    assert _sizes(notify_fn) == []
    assert sess.compacted_size_pending is not None
    _statusline(recv, run_async, 18)
    assert _sizes(notify_fn) == [{"pending": (555, 1, 22, "conv-a"),
                                  "after_pct": 18, "session_id": "conv-a"}]
    assert sess.compacted_size_pending is None
    _statusline(recv, run_async, 19)
    assert len(_sizes(notify_fn)) == 1


def test_a_stale_reading_after_precompact_does_not_end_the_compaction(receiver, run_async):
    """A reading sent just before PreCompact but handled after it still
    carries the old value; under 30% it must not pass for the small one
    after a compaction. The 0 that follows the compaction does."""
    registry, recv, notify_fn = receiver
    _statusline(recv, run_async, 22)
    _send(recv, run_async, hook_event_name="PreCompact", trigger="manual")
    _statusline(recv, run_async, 22)
    assert [c.args[1] for c in notify_fn.await_args_list].count("compact_done") == 0
    _statusline(recv, run_async, 0)
    done = [c.args[2] for c in notify_fn.await_args_list if c.args[1] == "compact_done"]
    assert done == [{"before_pct": 22, "after_pct": 0}]


def test_a_reading_during_a_compaction_adds_nothing(receiver, run_async):
    registry, recv, notify_fn = receiver
    sess = _owe(registry)
    sess.pre_compact_pct = 40
    _statusline(recv, run_async, 35)
    assert _sizes(notify_fn) == []


@pytest.mark.parametrize("event", [
    {"hook_event_name": "PreCompact", "trigger": "auto"},
    {"hook_event_name": "SessionStart", "source": "clear", "session_id": "conv-b"},
    {"hook_event_name": "SessionStart", "source": "resume", "session_id": "conv-c"},
    {"hook_event_name": "SessionEnd", "reason": "clear"},
], ids=["precompact", "clear", "resume", "session_end"])
def test_a_new_compaction_or_conversation_drops_the_owed_size(receiver, run_async, event):
    registry, recv, notify_fn = receiver
    sess = _owe(registry)
    _send(recv, run_async, **event)
    assert sess.compacted_size_pending is None


def test_a_compaction_between_turns_end_to_end(mk_bot, run_async, monkeypatch, tmp_state_file):
    """PreCompact at 22%, SessionStart(compact) with the stale reading,
    the status line's 0, then the next message's real reading: one
    message, "Compacted from 22%" and then "Compacted: 22% → 18%"."""
    registry = SessionRegistry()
    bot = mk_bot(registry)
    _wire(bot, monkeypatch)
    recv = hr.HookReceiver(registry, bot.notify)
    sess = registry.get_or_create(SESSION)
    sess.status = Status.IDLE
    sess.claude_session_id = "conv-a"
    _statusline(recv, run_async, 22)
    _send(recv, run_async, hook_event_name="PreCompact", trigger="auto")
    _send(recv, run_async, hook_event_name="PostCompact")
    _send(recv, run_async, hook_event_name="SessionStart", source="compact",
          session_id="conv-a", sl_tokens={"context_pct": 22})
    _statusline(recv, run_async, 0)
    assert "Compacted from 22%" in _last_text(bot)
    _statusline(recv, run_async, 18)
    _statusline(recv, run_async, 19)
    compacted = [(c.args[0], c.args[1]) for c in bot._edit_busy_raw.await_args_list
                 if "Compacted" in c.args[1]]
    assert [msg_id for msg_id, _ in compacted] == [555, 555]
    assert "Compacted from 22%" in compacted[0][1]
    assert "Compacted: 22% → 18%" in compacted[1][1]
    bot._app.bot.send_message.assert_awaited_once()  # only the Compacting message
