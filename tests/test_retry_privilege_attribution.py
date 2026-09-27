"""review-1.md rev-iter1-001: a privilege leak on the Retry path.

``_inject_prompt`` used to attribute a note's permissions via
``self._driver_user(sess)`` -> ``sess.last_driver_user_id`` — a mutable
session field that can be updated by an unrelated interaction between a
prompt failing and the operator tapping Retry on its card. Retry has no
live ``Update`` identifying whose content ``sess.last_prompt`` actually
is, so the note written on retry inherited whichever identity the
session field happened to name at tap time — including that identity's
``bypass_safety``.

Sequence reproduced below, matching review-1.md exactly:

1. A member's prompt is picked up by Claude (its note is consumed —
   simulated here via ``delete_notes``, exactly what the
   ``UserPromptSubmit`` hook does at pick-up) and then fails, leaving a
   Retry card. ``sess.last_prompt`` still holds the member's content.
2. An owner — an elevated role with ``bypass_safety=True`` — sends an
   unrelated message. It injects immediately (queue-handoff removed the
   BUSY hold) and leaves an outstanding note with ``bypass_safety=True``.
   ``sess.last_driver_user_id`` is now the owner's id.
3. The owner taps Retry on the member's (still-displayed) card.
4. ``mixed_sender_note_outstanding`` compares the tapper (owner) to the
   only outstanding note's sender (owner) — no mismatch, so the
   mixed-sender hold passes trivially.
5. Old code: the retried note — carrying the member's prompt text — is
   written with the owner's resolved role, ``bypass_safety=True``.
   Fixed code: Retry passes the tapper as ``driver_user_id`` only when
   the tapper sent ``sess.last_prompt`` (``last_prompt_driver_user_id``,
   roadmap 8.50, 2026-09-27 — so an owner's own Retry keeps its rights);
   here the author is the member, so no sender is passed and permission
   resolution fails closed to the floor.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.bot.transport import driver_id_from_update
from aipager.dtach import inject
from aipager.policy import load_policy
from aipager.policy_snapshot import delete_notes, list_outstanding_notes
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession

CHAT_ID = -100
SESSION_NAME = "claude-x__g100"


def _bot(mk_bot):
    scope = Scope(
        chat_id=CHAT_ID, kind="group", label="dev",
        members=(
            Member(id=1, label="mem", role="user"),
            Member(id=2, label="boss", role="owner"),  # bypass_safety=True
        ),
    )
    bot = mk_bot(scopes=[scope])
    bot.policy = load_policy()
    return bot


def _sess():
    sess = TrackedSession(name=SESSION_NAME, label="x", status=Status.IDLE)
    sess.scope_chat_id = CHAT_ID
    sess.scope_kind = "group"
    return sess


def _update_for(user_id: int):
    u = MagicMock()
    u.effective_user = MagicMock(id=user_id)
    return u


def test_retry_does_not_leak_a_later_senders_bypass_safety(
    mk_bot, run_async, monkeypatch,
):
    bot = _bot(mk_bot)
    sess = _sess()
    bot.registry._sessions[sess.name] = sess
    bot.registry.last_active_session = sess.name

    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    injected: list[str] = []
    monkeypatch.setattr(
        inject, "send_text_and_enter",
        AsyncMock(side_effect=lambda n, body: injected.append(body) or True),
    )
    bot._send_busy_and_animate = AsyncMock()

    member_update = _update_for(1)
    owner_update = _update_for(2)

    # ---- 1. Member's prompt is sent, picked up (note consumed), fails ----
    sess.trigger_msg_id = 501
    sess.last_prompt = "member's prompt that failed"
    bot._mark_driver(sess, member_update)
    ok = run_async(bot._inject_prompt(
        sess, sess.last_prompt, msg_id=501, chat_id=CHAT_ID,
        driver_user_id=driver_id_from_update(member_update),
    ))
    assert ok
    member_notes = [
        n for n in list_outstanding_notes(sess.name)
        if n["sender_key"] == [CHAT_ID, 1]
    ]
    assert member_notes, "member's note was never written"
    # Simulate the UserPromptSubmit hook consuming it at pick-up — Claude
    # started working on it, then the turn errored (API failure). The
    # member's note is no longer outstanding by the time the error/Retry
    # card appears; only its text (sess.last_prompt) survives.
    delete_notes(sess.name, member_notes)
    assert not [
        n for n in list_outstanding_notes(sess.name)
        if n["sender_key"] == [CHAT_ID, 1]
    ]
    injected.clear()

    # ---- 2. An owner sends an unrelated message; it injects immediately ----
    bot._mark_driver(sess, owner_update)
    ok = run_async(bot._inject_prompt(
        sess, "owner's unrelated message", msg_id=502, chat_id=CHAT_ID,
        driver_user_id=driver_id_from_update(owner_update),
    ))
    assert ok
    assert sess.last_driver_user_id == 2
    # sess.last_prompt is untouched by _inject_prompt itself (only the
    # handler wrapper sets it) — the member's failed prompt is still
    # what a Retry tap would resend.
    assert sess.last_prompt == "member's prompt that failed"

    owner_notes = [
        n for n in list_outstanding_notes(sess.name)
        if n["sender_key"] == [CHAT_ID, 2]
    ]
    assert len(owner_notes) == 1
    assert owner_notes[0]["bypass_safety"] is True  # sanity: owner IS elevated

    # ---- 3. Owner taps Retry on the member's still-outstanding card ----
    query = MagicMock()
    query.data = f"{sess.name}:retry"
    query.message = MagicMock(text="member's card", message_id=700)
    query.message.edit_text = AsyncMock()
    query.message.chat = MagicMock(id=CHAT_ID)
    query.from_user = MagicMock(id=2)
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=CHAT_ID)
    update.effective_user = MagicMock(id=2)

    run_async(bot._handle_callback(update, MagicMock()))

    assert injected, (
        "Retry never re-injected — the mixed-sender hold fired for the "
        "wrong reason, or the callback was refused before reaching "
        "_inject_prompt"
    )
    # The identity *marker* (cosmetic display text, not a permission
    # grant — untouched by this fix, out of scope per rev-iter1-001)
    # still reflects whoever sess.last_driver_user_id names, so it may
    # be prefixed with the owner's marker. The underlying content is
    # still the member's, unmarked, in raw_text — checked below.
    assert injected[-1].endswith("member's prompt that failed")

    retried_notes = [
        n for n in list_outstanding_notes(sess.name)
        if n["raw_text"] == "member's prompt that failed"
    ]
    assert retried_notes, "no note was written for the retried content"
    assert not any(n["bypass_safety"] for n in retried_notes), (
        "the retried note carries the owner's bypass_safety even though "
        "the content is the member's prompt, not the owner's — rev-iter1-001"
    )


def test_a_prompt_sent_with_no_sender_records_no_author(mk_bot, run_async, monkeypatch):
    """review rev-iter4-002: a Retry (or /compact) sends with no explicit
    sender while the owner is the session's last driver. Its note must not
    name the owner as the prompt's author, or the owner's next Retry of it
    would count as "the tapper's own prompt" and run with owner rights."""
    from aipager.policy_snapshot import note_driver_id
    bot = _bot(mk_bot)
    sess = _sess()
    bot.registry._sessions[sess.name] = sess
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()

    bot._mark_driver(sess, _update_for(2))          # the owner drove last
    assert run_async(bot._inject_prompt(sess, "member's prompt", msg_id=601,
                                        chat_id=CHAT_ID, driver_user_id=None))
    notes = [n for n in list_outstanding_notes(sess.name) if n.get("msg_id") == 601]
    assert notes and all(note_driver_id(n) is None for n in notes)

    # ...while an explicit sender is still recorded as the author.
    assert run_async(bot._inject_prompt(sess, "owner's own", msg_id=602,
                                        chat_id=CHAT_ID, driver_user_id=2))
    own = [n for n in list_outstanding_notes(sess.name) if n.get("msg_id") == 602]
    assert own and note_driver_id(own[0]) == 2


def test_the_pickup_datagram_carries_the_real_author():
    """The daemon records the prompt's author from the queue_pickup wire
    note; without author_user_id there it would always be unknown."""
    from aipager.dtach.notify_hook import _note_wire
    wire = _note_wire({"msg_id": 1, "chat_id": CHAT_ID, "raw_text": "t",
                       "sender_key": [CHAT_ID, 2], "author_user_id": 2})
    assert wire["author_user_id"] == 2
    assert "bypass_safety" not in wire          # identity only, no permissions
