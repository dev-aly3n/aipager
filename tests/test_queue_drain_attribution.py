"""review-2.md rev-iter2-001: the queue-drain path's permission attribution.

``pending_queue`` used to be a 4-tuple ``(text, msg_id, queued_at,
reply_context)`` with no slot for who sent the message, so
``notify.py``'s drain called ``_inject_prompt`` with no
``driver_user_id`` at all — every drained message got floor permissions,
losing the sender's role and ``bypass_safety``, even for the ordinary
"send a prompt, it happens to arrive while BUSY" case.

Fix: ``TrackedSession.queue_prompt`` now captures the sender's id (from
the same ``transport.driver_id_from_update(update)`` every immediate-
inject call site already uses) at queue time and carries it as a 5th
tuple element; the drain reads it back and threads it into
``_inject_prompt(..., driver_user_id=...)``.

No test previously exercised this path's attribution in either
direction (review-2's stated coverage gap) — every test below reads the
drained turn's actual note (``policy_snapshot.list_outstanding_notes``),
never the queue tuple, per Criterion 10's warning that asserting on the
tuple alone proves nothing about what a real turn resolves to.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.dtach import inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT_ID = -100
SESSION_NAME = "claude-x__g100"

# id=1 -> role "user": bypass_safety=False (the built-in default, see
# safety.BUILTIN_ROLE_DEFAULTS). id=2 -> role "owner": bypass_safety=True.
# The two roles' bypass_safety values differ, which is what every
# assertion below keys off to prove WHOSE permissions actually landed on
# the drained note.
MEMBER_ID = 1
OWNER_ID = 2


@pytest.fixture(autouse=True)
def _mock_send_rich_message(monkeypatch):
    """Same isolation test_notify_queue_reply.py's module fixture
    applies — only the PTB send_message path (header) fires, never a
    real Telegram call."""
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message",
        AsyncMock(return_value={}),
    )


def _isolate_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")


def _bot(mk_bot):
    scope = Scope(
        chat_id=CHAT_ID, kind="group", label="dev",
        members=(
            Member(id=MEMBER_ID, label="mem", role="user"),
            Member(id=OWNER_ID, label="boss", role="owner"),
        ),
    )
    bot = mk_bot(scopes=[scope])
    bot.policy = load_policy()
    return bot


def _sess(status=Status.IDLE):
    sess = TrackedSession(name=SESSION_NAME, label="x", status=status)
    sess.scope_chat_id = CHAT_ID
    sess.scope_kind = "group"
    sess.busy_started_at = time.monotonic()
    return sess


def _drive_idle_drain(bot, sess, run_async, monkeypatch):
    bot.registry._sessions[sess.name] = sess
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    monkeypatch.setattr(inject, "send_text_and_enter",
                        AsyncMock(return_value=True))
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))


def _drained_note(session_name: str) -> dict:
    notes = ps.list_outstanding_notes(session_name)
    assert notes, "the drain never wrote a note at all"
    return notes[-1]


# ---- 1. attribution to the captured queuer, not the floor, not the -------
#         mutable sess.last_driver_user_id fallback ------------------------

def test_drained_message_is_attributed_to_the_sender_who_queued_it(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = _bot(mk_bot)
    sess = _sess()

    # Owner queues a message (e.g. it arrived while a dialog was open, or
    # while a different sender's note was outstanding — either hold
    # condition in _hold_for_open_dialog). The id is captured NOW.
    ok = sess.queue_prompt(
        "owner's held prompt", 900, "", OWNER_ID,
    )
    assert ok

    # Between queueing and the drain, an UNRELATED interaction moves
    # sess.last_driver_user_id to someone else entirely — exactly the
    # kind of race review-1/2 warned about. If the drain ever fell back
    # to this mutable field, the note below would resolve to the
    # member's (non-bypass) role instead of the owner's.
    sess.last_driver_user_id = MEMBER_ID

    _drive_idle_drain(bot, sess, run_async, monkeypatch)

    note = _drained_note(sess.name)
    assert note["sender_key"] == [CHAT_ID, OWNER_ID], (
        "drained note attributed to the wrong sender_key — expected the "
        "id captured at queue_prompt() time"
    )
    assert note["bypass_safety"] is True, (
        "drained note lost the queuing sender's role/bypass_safety — "
        "rev-iter2-001"
    )
    assert note["raw_text"] == "owner's held prompt"


# ---- 2. no recoverable sender id -> floor, never a guess ------------------

def test_drained_message_with_no_recoverable_sender_id_floors_permissions(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = _bot(mk_bot)
    sess = _sess()

    # No driver_user_id captured at queue time (e.g. a call site with no
    # live Update — the hook-triggered /compact path).
    ok = sess.queue_prompt("no captured sender", 901)
    assert ok
    assert sess.pending_queue[0][4] is None

    # sess.last_driver_user_id names a real, elevated member — the drain
    # must NOT reach for it as a substitute identity.
    sess.last_driver_user_id = OWNER_ID

    _drive_idle_drain(bot, sess, run_async, monkeypatch)

    note = _drained_note(sess.name)
    assert note["bypass_safety"] is False, (
        "an entry with no captured sender id must floor, not borrow "
        "sess.last_driver_user_id — that is precisely the leak "
        "rev-iter1-001/rev-iter2-001 closed"
    )
    assert note["sender_key"] == [CHAT_ID, OWNER_ID], (
        "sender_key bookkeeping is allowed to use the last-driver "
        "fallback (it only ever widens a hold, never a grant) — but "
        "permission resolution above must not"
    )


# ---- 3. a 4-tuple entry persisted by an older build still loads and ------
#         drains without error ---------------------------------------------

def test_a_legacy_4_tuple_queue_entry_still_loads_and_drains_without_error(
    mk_bot, run_async, monkeypatch, tmp_path, tmp_state_file,
):
    _isolate_snapshot(monkeypatch, tmp_path)
    now = time.time()
    state = {
        "version": 1, "last_active_session": "", "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            SESSION_NAME: {
                "name": SESSION_NAME,
                "label": "x",
                "last_msg_id": None,
                "transcript_path": "",
                "trigger_msg_id": None,
                # pre-rev-iter2-001 shape: no 5th (driver_user_id) slot.
                "pending_queue": [["pre-upgrade prompt", 902, now, ""]],
                "last_prompt": "",
                "model_name": "",
                "busy_msg_id": None,
                "scope_chat_id": CHAT_ID,
                "scope_kind": "group",
            },
        },
    }
    tmp_state_file.write_text(json.dumps(state))

    r = SessionRegistry()
    r.load()
    sess = r.get(SESSION_NAME)
    assert sess is not None
    assert len(sess.pending_queue[0]) == 5, "not widened to the new shape on load"
    assert sess.pending_queue[0][4] is None, "a legacy entry has no sender to invent"
    sess.status = Status.IDLE
    sess.busy_started_at = time.monotonic()

    bot = _bot(mk_bot)
    bot.registry = r
    sess.last_driver_user_id = OWNER_ID  # must still not be borrowed

    _drive_idle_drain(bot, sess, run_async, monkeypatch)  # must not raise

    assert sess.pending_queue == []
    note = _drained_note(sess.name)
    assert note["raw_text"] == "pre-upgrade prompt"
    assert note["bypass_safety"] is False, (
        "a widened legacy entry must floor, not inherit last_driver_user_id"
    )


# ---- 4. regression guard: the drain must never attribute from -----------
#         sess.last_driver_user_id again ------------------------------------

def test_drain_regression_guard_never_attributes_from_last_driver_user_id(
    mk_bot, run_async, monkeypatch, tmp_path,
):
    """Named per the review's own wording: "if the drain ever attributes
    from sess.last_driver_user_id again, a named test must fail."

    Mirrors test 1 but inverted: the LOW-privilege member queues the
    message, while sess.last_driver_user_id (mutated afterward, exactly
    as in the rev-iter1-001 sequence) names the HIGH-privilege owner. If
    a future change reintroduces the fallback — either by dropping the
    captured id at the notify.py call site, or by resolving permissions
    from ``sess.last_driver_user_id`` again — this drained note would
    incorrectly gain ``bypass_safety=True``. Verified load-bearing below.
    """
    _isolate_snapshot(monkeypatch, tmp_path)
    bot = _bot(mk_bot)
    sess = _sess()

    ok = sess.queue_prompt("member's held prompt", 903, "", MEMBER_ID)
    assert ok
    sess.last_driver_user_id = OWNER_ID  # a later, unrelated, elevated sender

    _drive_idle_drain(bot, sess, run_async, monkeypatch)

    note = _drained_note(sess.name)
    assert note["sender_key"] == [CHAT_ID, MEMBER_ID]
    assert note["bypass_safety"] is False, (
        "the drain attributed permissions from sess.last_driver_user_id "
        "(the owner) instead of the id captured at queue time (the "
        "member) — the rev-iter1-001/rev-iter2-001 leak is back"
    )
