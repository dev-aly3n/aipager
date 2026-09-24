"""Roadmap 8.39 — an answer that already reached the chat is never re-sent,
even across a daemon restart.

The incident (2026-09-24): a Stop at 16:20:30 delivered the answer; the
daemon restarted at 16:21:09; at 16:21:30 Claude Code's idle Notification
(`notification_type: idle_prompt`, 60 s after the Stop, no
`last_assistant_message`) landed on the already-IDLE session, the late path
read the transcript's newest text — the answer already delivered — and the
fresh daemon's empty delivered-digest ring let it go out a second time.

Every test here drives the real HookReceiver into the real finish path
(`TelegramBot.notify`) with only the Telegram transport mocked, and
simulates the restart as `save()` on one registry and `load()` on a new
one — exactly what the daemon does.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.rich_message import RichMessageFloodBanned
from aipager.dtach import hook_receiver as hr
from aipager.state import DELIVERED_DIGEST_RING, SessionRegistry, Status

SESSION = "claude-boss"
CHAT = 4242
ANSWER = "The daemon restarted cleanly; nothing else changed."
NEXT_ANSWER = "Second turn: the fix is in."


def _iso(wall: float) -> str:
    return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat()


def _write_transcript(path, *entries: tuple[str, float | None]) -> None:
    """One user line, then one assistant text entry per (text, wall); a
    None wall writes the entry without a timestamp."""
    lines = [json.dumps({"type": "user", "message": {"content": "go"}})]
    for text, wall in entries:
        entry = {"type": "assistant",
                 "message": {"content": [{"type": "text", "text": text}]}}
        if wall is not None:
            entry["timestamp"] = _iso(wall)
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def rich(monkeypatch):
    mock = AsyncMock(return_value={"message_id": 777})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", mock)
    return mock


@pytest.fixture
def transcript(tmp_path):
    return tmp_path / "3f1c9b2e-boss.jsonl"


def _daemon(mk_bot, registry=None):
    """One daemon: a registry, the bot's real finish path, a receiver."""
    registry = registry or SessionRegistry()
    bot = mk_bot(registry)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=321))
    bot._maybe_update_bot_name = AsyncMock()
    return registry, bot, hr.HookReceiver(registry, bot.notify)


def _send(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def _turn(registry, recv, run_async, transcript, answer: str | None) -> None:
    """A prompt, then its Stop (carrying the answer when given)."""
    sess = registry.get_or_create(SESSION)
    sess.label = "boss"
    sess.scope_chat_id = CHAT
    registry.transition(SESSION, Status.BUSY)
    stop = {"hook_event_name": "Stop", "session": SESSION,
            "transcript_path": str(transcript)}
    if answer is not None:
        stop["last_assistant_message"] = answer
    _send(recv, run_async, **stop)


def _restart(mk_bot, old_registry):
    """Save, then a fresh daemon loads the file and its monitor finds the
    session alive (GONE/UNKNOWN → IDLE, as logged live)."""
    old_registry.save()
    registry, bot, recv = _daemon(mk_bot, SessionRegistry())
    registry.load()
    assert registry.get(SESSION) is not None
    registry.transition(SESSION, Status.IDLE)
    return registry, bot, recv


def _idle_notification(recv, run_async, transcript):
    _send(recv, run_async, hook_event_name="Notification",
          notification_type="idle_prompt", session=SESSION,
          transcript_path=str(transcript),
          message="Claude is waiting for your input")


def _bodies(rich) -> list[str]:
    return [c.args[1] for c in rich.await_args_list]


# ---- the incident -------------------------------------------------------

def test_restart_between_a_delivered_stop_and_the_idle_notification_resends_nothing(
    mk_bot, run_async, rich, transcript, tmp_state_file,
):
    _write_transcript(transcript, (ANSWER, time.time() - 1))
    registry, _bot, recv = _daemon(mk_bot)
    _turn(registry, recv, run_async, transcript, ANSWER)
    assert len(_bodies(rich)) == 1 and _bodies(rich)[0].endswith(ANSWER)

    registry2, bot2, recv2 = _restart(mk_bot, registry)
    _idle_notification(recv2, run_async, transcript)

    assert len(_bodies(rich)) == 1, _bodies(rich)
    bot2._app.bot.send_message.assert_not_awaited()


def test_the_delivery_stamp_alone_refuses_a_transcript_text_that_predates_it(
    mk_bot, run_async, rich, transcript, tmp_state_file,
):
    """R2: the hook's text and the transcript's need not hash alike (the
    transcript joins every text block of the entry; the hook carries what
    Claude Code chose). The persisted delivery stamp still proves the
    transcript's newest answer was out before the restart."""
    _write_transcript(transcript, (ANSWER + "\n\n(and a second block)",
                                   time.time() - 1))
    registry, _bot, recv = _daemon(mk_bot)
    _turn(registry, recv, run_async, transcript, ANSWER)
    assert len(_bodies(rich)) == 1

    registry2, _bot2, recv2 = _restart(mk_bot, registry)
    _idle_notification(recv2, run_async, transcript)

    assert len(_bodies(rich)) == 1, _bodies(rich)


def test_the_persisted_ring_alone_refuses_when_the_transcript_has_no_timestamp(
    mk_bot, run_async, rich, transcript, tmp_state_file,
):
    """R1 on its own: with no entry timestamp the delivery stamp cannot
    speak, so the restored digest ring is the only thing in the way."""
    _write_transcript(transcript, (ANSWER, None))
    registry, _bot, recv = _daemon(mk_bot)
    _turn(registry, recv, run_async, transcript, ANSWER)

    _registry2, _bot2, recv2 = _restart(mk_bot, registry)
    _idle_notification(recv2, run_async, transcript)

    assert len(_bodies(rich)) == 1, _bodies(rich)


def test_an_answer_that_landed_through_the_plain_text_fallback_counts_as_delivered(
    mk_bot, run_async, rich, transcript, tmp_state_file,
):
    _write_transcript(transcript, (ANSWER, None))
    registry, bot, recv = _daemon(mk_bot)
    rich.side_effect = RuntimeError("parse error")
    _turn(registry, recv, run_async, transcript, ANSWER)
    assert bot._app.bot.send_message.await_count == 1  # the fallback landed

    rich.side_effect = None
    _registry2, bot2, recv2 = _restart(mk_bot, registry)
    _idle_notification(recv2, run_async, transcript)

    assert len(_bodies(rich)) == 1  # only the failed rich attempt
    bot2._app.bot.send_message.assert_not_awaited()


# ---- the late path's reason to exist (R3) ------------------------------

def _previous_turn_delivered(mk_bot, run_async, transcript):
    """Turn 1 delivered normally, so the stamp is set and older than turn 2."""
    first_written = time.time() - 1
    _write_transcript(transcript, (ANSWER, first_written))
    registry, bot, recv = _daemon(mk_bot)
    _turn(registry, recv, run_async, transcript, ANSWER)
    delivered_at = registry.get(SESSION).answer_delivered_wall
    assert delivered_at > first_written
    # Turn 2's answer is written after turn 1 went out and before turn 2's
    # own Stop — the real order.
    _write_transcript(transcript, (ANSWER, first_written),
                      (NEXT_ANSWER, delivered_at + 0.001))
    return registry, bot, recv


def test_restart_while_the_send_was_in_flight_still_delivers_exactly_once(
    mk_bot, run_async, rich, transcript, tmp_state_file,
):
    """The daemon stops mid-send: the task is cancelled and the shutdown
    save runs while the answer's digest is only pending. The restarted
    daemon must deliver it — once."""
    registry, _bot, recv = _previous_turn_delivered(mk_bot, run_async, transcript)

    async def dies_mid_send(*_a, **_k):
        registry.save()  # the shutdown save, with the send still open
        raise asyncio.CancelledError
    rich.side_effect = dies_mid_send
    with pytest.raises(asyncio.CancelledError):
        _turn(registry, recv, run_async, transcript, NEXT_ANSWER)

    rich.side_effect = None
    rich.return_value = {"message_id": 778}
    _registry2, _bot2, recv2 = _daemon(mk_bot, SessionRegistry())
    _registry2.load()
    _registry2.transition(SESSION, Status.IDLE)
    _idle_notification(recv2, run_async, transcript)
    _idle_notification(recv2, run_async, transcript)  # a repeat stays quiet

    delivered = [b for b in _bodies(rich) if b.endswith(NEXT_ANSWER)]
    assert len(delivered) == 2, _bodies(rich)  # the cut-off attempt + one


@pytest.mark.parametrize("failure", ["send_failed", "held"])
def test_restart_after_a_send_that_never_landed_delivers_exactly_once(
    mk_bot, run_async, rich, transcript, tmp_state_file, failure,
):
    registry, bot, recv = _previous_turn_delivered(mk_bot, run_async, transcript)
    if failure == "held":
        # Held answers live in memory only (held.py) — a restart loses them.
        rich.side_effect = RichMessageFloodBanned(60, CHAT)
    else:
        rich.side_effect = RuntimeError("telegram 500")
        bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("down"))
    _turn(registry, recv, run_async, transcript, NEXT_ANSWER)
    attempts = len(_bodies(rich))

    rich.side_effect = None
    rich.return_value = {"message_id": 779}
    _registry2, _bot2, recv2 = _restart(mk_bot, registry)
    _idle_notification(recv2, run_async, transcript)
    _idle_notification(recv2, run_async, transcript)

    after = _bodies(rich)[attempts:]
    assert len(after) == 1 and after[0].endswith(NEXT_ANSWER), after


# ---- the state file ----------------------------------------------------

def test_an_old_state_file_without_the_new_fields_loads(tmp_state_file):
    tmp_state_file.write_text(json.dumps({"version": 1, "sessions": {
        SESSION: {"name": SESSION, "label": "boss", "scope_chat_id": CHAT},
        "claude-odd": {"name": "claude-odd", "label": "odd",
                       "delivered_digests": "not-a-list",
                       "answer_delivered_wall": "yesterday"},
    }}))
    registry = SessionRegistry()
    registry.load()
    for name in (SESSION, "claude-odd"):
        sess = registry.get(name)
        assert sess is not None
        assert list(sess.delivered_digests) == []
        assert sess.answer_delivered_wall == 0.0


def test_the_digest_ring_stays_bounded_through_save_and_load(tmp_state_file):
    registry = SessionRegistry()
    sess = registry.get_or_create(SESSION)
    digests = [hashlib.md5(str(i).encode()).hexdigest() for i in range(30)]
    for d in digests:
        sess.remember_delivered(d)
    for d in digests:  # pending marks do not outlive their ring entry
        sess.remember_delivered(d + "-p", pending=True)
    assert len(sess.pending_digests) == DELIVERED_DIGEST_RING
    registry.save()
    saved = json.loads(tmp_state_file.read_text())["sessions"][SESSION]
    assert saved["delivered_digests"] == []  # every ring entry is pending
    sess.delivered_digests.clear()
    sess.pending_digests.clear()
    for d in digests:
        sess.remember_delivered(d)
    registry.save()
    saved = json.loads(tmp_state_file.read_text())["sessions"][SESSION]
    assert saved["delivered_digests"] == digests[-DELIVERED_DIGEST_RING:]

    # A hand-grown file is cut back to the ring on load, newest kept.
    saved["delivered_digests"] = digests
    tmp_state_file.write_text(json.dumps({"version": 1,
                                          "sessions": {SESSION: saved}}))
    registry2 = SessionRegistry()
    registry2.load()
    ring = registry2.get(SESSION).delivered_digests
    assert list(ring) == digests[-DELIVERED_DIGEST_RING:]
    assert ring.maxlen == DELIVERED_DIGEST_RING


def test_a_pending_digest_is_kept_out_of_the_file_until_confirmed(tmp_state_file):
    registry = SessionRegistry()
    sess = registry.get_or_create(SESSION)
    sess.remember_delivered("aaa")
    sess.remember_delivered("bbb", pending=True)
    assert sess.was_delivered("bbb")  # refused in-process at once
    registry.save()
    saved = json.loads(tmp_state_file.read_text())["sessions"][SESSION]
    assert saved["delivered_digests"] == ["aaa"]
    assert saved["answer_delivered_wall"] == 0.0

    sess.confirm_delivered(["bbb"], 1234.5)
    registry.save()
    saved = json.loads(tmp_state_file.read_text())["sessions"][SESSION]
    assert saved["delivered_digests"] == ["aaa", "bbb"]
    assert saved["answer_delivered_wall"] == 1234.5
    sess.confirm_delivered([], 10.0)  # the stamp never moves backwards
    assert sess.answer_delivered_wall == 1234.5


def test_a_confirmed_resend_clears_an_earlier_pending_mark():
    """The job-buffer flush remembers with `pending=not landed`; a later
    landed send of the same body must make it persistable."""
    registry = SessionRegistry()
    sess = registry.get_or_create(SESSION)
    sess.remember_delivered("ccc", pending=True)
    sess.remember_delivered("ccc")
    assert sess.persisted_digests() == ["ccc"]


# ---- a background job's interim answer (roadmap 8.42) --------------------
#
# Until 8.42 these covered the job buffer's flush and the finish path's
# interim+final composition. 8.42 (operator decision 2026-09-24) sends an
# interim answer the moment its turn ends, so it is that turn's answer: its
# digest persists and it moves the delivery stamp, once it has landed.

@pytest.mark.parametrize("outcome", ["sent", "fallback", "held", "blocked",
                                     "all_failed"])
def test_a_job_interim_answer_persists_only_when_it_landed(
    mk_bot, run_async, rich, outcome,
):
    from aipager.bot.rich_message import RichMessageBlocked
    registry, bot, _recv = _daemon(mk_bot)
    sess = registry.get_or_create(SESSION)
    sess.scope_chat_id = CHAT
    if outcome == "fallback":
        rich.side_effect = RuntimeError("parse error")
    elif outcome == "held":
        rich.side_effect = RichMessageFloodBanned(60, CHAT)
    elif outcome == "blocked":
        rich.side_effect = RichMessageBlocked("403")
    elif outcome == "all_failed":
        rich.side_effect = RuntimeError("parse error")
        bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("down"))
    turn_end = time.time() - 5
    run_async(bot._deliver_job_interim(sess, "an interim answer", turn_end))

    digest = hashlib.md5(b"an interim answer").hexdigest()
    assert sess.was_delivered(digest)  # refused in-process either way
    landed = outcome in ("sent", "fallback")
    assert sess.persisted_digests() == ([digest] if landed else [])
    assert sess.answer_delivered_wall == (turn_end if landed else 0.0)

    if outcome == "held":  # the mute lifts and the held interim lands
        rich.side_effect = None
        assert run_async(bot.flush_held_answers(sess)) == 1
        assert sess.persisted_digests() == [digest]
        assert sess.answer_delivered_wall == turn_end


def test_a_restart_after_a_job_interim_does_not_resend_it(
    mk_bot, run_async, rich, transcript, tmp_state_file,
):
    """The 8.39 incident's shape, mid-job: the interim went out, the daemon
    restarted, and Claude Code's idle Notification read the transcript's
    newest text — the interim."""
    _write_transcript(transcript, (ANSWER, time.time() - 1))
    registry, bot, recv = _daemon(mk_bot)
    sess = registry.get_or_create(SESSION)
    sess.label = "boss"
    sess.scope_chat_id = CHAT
    sess.status = Status.IDLE
    run_async(bot._deliver_job_interim(sess, ANSWER, time.time()))
    assert len(_bodies(rich)) == 1

    registry, bot, recv = _restart(mk_bot, registry)
    _idle_notification(recv, run_async, transcript)
    assert len(_bodies(rich)) == 1


# ---- held answers delivered after the mute lifts (review rev-iter1-001) --

@pytest.mark.parametrize("delivery", ["rich", "plain"])
def test_a_held_answer_flushed_after_the_mute_lifts_is_not_resent_after_a_restart(
    mk_bot, run_async, rich, transcript, tmp_state_file, delivery,
):
    from aipager.bot.held import HELD, HELD_ANSWER_MAX_ATTEMPTS
    _write_transcript(transcript, (ANSWER, None))
    registry, bot, recv = _daemon(mk_bot)
    rich.side_effect = RichMessageFloodBanned(5, CHAT)
    _turn(registry, recv, run_async, transcript, ANSWER)
    sess = registry.get(SESSION)
    assert sess.persisted_digests() == []  # held: not yet delivered

    if delivery == "rich":
        rich.side_effect = None
    else:  # the last rich attempt fails; the stored plain text lands
        rich.side_effect = RuntimeError("parse error")
        for entry in HELD.pending(CHAT):
            entry.attempts = HELD_ANSWER_MAX_ATTEMPTS - 1
    assert run_async(bot.flush_held_answers(sess)) == 1
    assert len(sess.persisted_digests()) == 1
    assert sess.answer_delivered_wall > 0.0
    sent_before = len(_bodies(rich)) + bot._app.bot.send_message.await_count

    rich.side_effect = None
    _registry2, bot2, recv2 = _restart(mk_bot, registry)
    _idle_notification(recv2, run_async, transcript)

    assert len(_bodies(rich)) + bot2._app.bot.send_message.await_count \
        + bot._app.bot.send_message.await_count == sent_before


# ---- the stamp's moment (review rev-iter1-002) --------------------------

def test_a_next_answer_written_before_the_answer_is_selected_is_not_taken_for_delivered(
    mk_bot, run_async, rich, transcript, tmp_state_file,
):
    """The stamp is taken when the turn end reaches the finish path, not at
    content selection, which follows awaited work (the final card render,
    paced by the outbound gate). A queued message Claude pops as the next
    turn can write its answer in between; if that turn's Stop is lost, the
    restarted daemon's idle notification must still deliver it. The first
    await of the IDLE branch stands in for that gap here."""
    first_written = time.time() - 1
    _write_transcript(transcript, (ANSWER, first_written))
    registry, bot, recv = _daemon(mk_bot)
    written: dict = {}

    async def render_takes_a_moment(_sess):
        # Runs right after the turn end is received, before selection.
        written["at"] = time.time() + 0.001
        while time.time() < written["at"] + 0.002:
            pass
        _write_transcript(transcript, (ANSWER, first_written),
                          (NEXT_ANSWER, written["at"]))
    bot._mark_ran_commands = render_takes_a_moment
    _turn(registry, recv, run_async, transcript, ANSWER)
    assert _bodies(rich)[-1].endswith(ANSWER)
    assert registry.get(SESSION).answer_delivered_wall < written["at"]

    _registry2, _bot2, recv2 = _restart(mk_bot, registry)
    _idle_notification(recv2, run_async, transcript)

    delivered = [b for b in _bodies(rich) if b.endswith(NEXT_ANSWER)]
    assert len(delivered) == 1, _bodies(rich)


def test_a_future_delivery_stamp_is_discarded_on_load(tmp_state_file):
    tmp_state_file.write_text(json.dumps({"version": 1, "sessions": {
        SESSION: {"name": SESSION, "answer_delivered_wall": time.time() + 3600},
        "claude-inf": {"name": "claude-inf", "answer_delivered_wall": "inf"},
    }}))
    registry = SessionRegistry()
    registry.load()
    assert registry.get(SESSION).answer_delivered_wall == 0.0
    assert registry.get("claude-inf").answer_delivered_wall == 0.0
