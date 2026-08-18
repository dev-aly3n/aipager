"""Fix-iteration coverage: the deadline-based reclaim rule (rev-iter1-001),
the Mini App 0%-context Compact gate (criterion 16), and the independence of
the two compaction knobs (criterion 19).
"""
import time
from unittest.mock import AsyncMock, MagicMock

from aipager.config import (
    COMPACT_CARD_TIMEOUT_SECONDS,
    COMPACT_INFLIGHT_MAX_SECONDS,
)
from aipager.miniapp.sessions import session_actions
from aipager.state import Status, TrackedSession


# ===== rev-iter1-001: a LIVE compaction must survive a new prompt ===========

def _compacting_sess(*, age: float, deadline: float | None = 180.0):
    """Session whose compacting card was pushed `age` seconds ago."""
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 500
    sess.push_compacting(500, time.monotonic() - age, deadline_seconds=deadline)
    return sess


def test_in_progress_compaction_is_not_reclaimed_by_a_new_prompt(mk_bot, run_async):
    """The regression the reviewer caught.

    `/label <text>` and the retry button both call
    registry.transition(name, BUSY) — synchronously — immediately before
    _send_busy_and_animate, so a genuinely in-flight compaction IS
    reachable here. Tearing it down orphans its card and misdirects the
    later compact_done edit onto the replacement.
    """
    bot = mk_bot()
    sess = _compacting_sess(age=5.0)          # 5s old, deadline 180s → live
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))

    run_async(bot._send_busy_and_animate(sess))

    bot._app.bot.send_message.assert_not_called()
    assert sess.stack_top_kind() == "compacting"
    assert sess.busy_msg_id == 500  # compact_done still edits the right card


def test_overdue_compaction_is_reclaimed_so_the_wedged_case_still_self_heals(
    mk_bot, run_async,
):
    """The operator's actual bug: card up far past its deadline."""
    bot = mk_bot()
    sess = _compacting_sess(age=600.0)        # 10 min old, deadline 180s
    sess.status = Status.IDLE                 # exactly the observed live state
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))

    run_async(bot._send_busy_and_animate(sess))

    bot._app.bot.send_message.assert_awaited_once()
    assert sess.stack_top_kind() == "busy"
    assert sess.busy_msg_id == 999


def test_compaction_pushed_without_a_deadline_is_never_reclaimed(mk_bot, run_async):
    bot = mk_bot()
    sess = _compacting_sess(age=99999.0, deadline=None)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))

    run_async(bot._send_busy_and_animate(sess))

    bot._app.bot.send_message.assert_not_called()
    assert sess.stack_top_kind() == "compacting"


def test_compacting_is_overdue_is_the_shared_predicate():
    """The sweeper and the reclaim branch must agree by construction."""
    sess = _compacting_sess(age=0.0)
    now = time.monotonic()
    assert sess.compacting_is_overdue(now) is False
    assert sess.compacting_is_overdue(now + 181.0) is True

    busy = TrackedSession(name="claude-b", label="b", status=Status.BUSY)
    busy.busy_msg_id = 7
    assert busy.compacting_is_overdue(now + 99999) is False   # busy top
    empty = TrackedSession(name="claude-e", label="e", status=Status.IDLE)
    assert empty.compacting_is_overdue(now + 99999) is False  # nothing live


# ===== criterion 16: Mini App greys out Compact at 0% context ===============

def test_compact_unavailable_with_a_reason_at_zero_context():
    for status in ("busy", "idle"):
        actions = session_actions(status, resumable=False, can_act=True,
                                  context_pct=0)
        assert actions["compact"]["available"] is False, status
        assert actions["compact"]["reason"], status  # non-null, displayable


def test_compact_available_once_context_is_nonzero():
    for status in ("busy", "idle"):
        actions = session_actions(status, resumable=False, can_act=True,
                                  context_pct=5)
        assert actions["compact"]["available"] is True, status
        assert actions["compact"]["reason"] is None, status


# ===== criterion 19: the two knobs are independent ==========================

def test_compaction_knobs_are_separate_with_the_chosen_defaults():
    assert COMPACT_CARD_TIMEOUT_SECONDS == 180.0   # operator-chosen 3 min
    assert COMPACT_INFLIGHT_MAX_SECONDS == 1800.0  # unchanged warning window
    assert COMPACT_CARD_TIMEOUT_SECONDS != COMPACT_INFLIGHT_MAX_SECONDS
