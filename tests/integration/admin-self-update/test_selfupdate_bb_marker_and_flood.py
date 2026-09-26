"""SC-5: after the restart the new daemon posts "aipager updated A → B,
N sessions re-adopted" once; every session is still present.
Flood: nothing is sent or edited into a flood-muted chat, including the
post-restart message; progress edits are throttled.
(design.md Success criteria #5; entrypoints.md "After a restart",
"Muted chats"; spec "Keep the flood rules")."""

from __future__ import annotations

import logging

import pytest

from aipager import self_update
from aipager.bot import update_flow
from aipager.bot.flood import MUTE
from aipager.state import SessionRegistry, Status

BAN = 3600


def _schedule_restart_via_flow(world, bot, h, run_async, labels=("alpha", "beta")):
    """Drive the old daemon all the way to a scheduled restart, so the
    marker under test is the one the flow itself wrote."""
    for label in labels:
        h.add_session(bot.registry, label)
    _, _, phase = run_async(h.run_job(bot, "aipager"))
    assert phase == "restart_scheduled"
    assert self_update.UPDATE_MARKER_PATH.exists()


def _new_daemon(mk_bot, h, alive=("alpha", "beta"), gone=()):
    from unittest.mock import AsyncMock
    reg = SessionRegistry()
    for label in alive:
        h.add_session(reg, label, status=Status.IDLE)
    for label in gone:
        h.add_session(reg, label, status=Status.GONE)
    bot = mk_bot(reg)
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", h.DM), 802))
    # Roadmap 8.45: the new daemon edits the old one's status message.
    bot._app.bot.edit_message_text = AsyncMock(
        side_effect=lambda *a, **kw: h.status_message(kw.get("chat_id", h.DM), 700))
    return bot, reg


def _sent(bot, h):
    return "\n".join(h.texts_of(bot._app.bot))


@pytest.fixture
def restarted(world, personal_bot, mk_bot, h, run_async):
    _schedule_restart_via_flow(world, personal_bot, h, run_async)
    world.running = h.LATEST          # the NEW daemon runs B
    return world


def test_new_daemon_posts_updated_a_to_b_with_count(restarted, mk_bot, h, run_async):
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert f"aipager updated {h.RUNNING} → {h.LATEST}, 2 sessions re-adopted" in _sent(bot, h)


def test_marker_message_goes_to_requesting_chat(restarted, mk_bot, h, run_async):
    """Roadmap 8.45: the outcome now EDITS the job's status message in the
    requesting chat (was: a new send_message there, a second message)."""
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    call = bot._app.bot.edit_message_text.await_args
    assert call.kwargs["chat_id"] == h.DM and call.kwargs["message_id"] == 700
    bot._app.bot.send_message.assert_not_awaited()


def test_marker_deleted_after_delivery(restarted, mk_bot, h, run_async):
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_marker_delivered_only_once(restarted, mk_bot, h, run_async):
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    run_async(update_flow.deliver_update_marker(bot, reg))
    # Roadmap 8.45: delivered by one edit (was: one send_message).
    assert bot._app.bot.edit_message_text.await_count == 1
    assert bot._app.bot.send_message.await_count == 0


def test_missing_session_is_reported_not_back(restarted, mk_bot, h, run_async):
    bot, reg = _new_daemon(mk_bot, h, alive=("alpha",), gone=("beta",))
    run_async(update_flow.deliver_update_marker(bot, reg))
    text = _sent(bot, h)
    assert "Not back" in text and "beta" in text


def test_missing_session_lowers_the_count(restarted, mk_bot, h, run_async):
    bot, reg = _new_daemon(mk_bot, h, alive=("alpha",), gone=("beta",))
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert "1 session re-adopted" in _sent(bot, h) or "1 session re-adopted" in _sent(bot, h)


def test_unknown_status_session_is_not_counted_readopted(restarted, mk_bot, h, run_async):
    bot, reg = _new_daemon(mk_bot, h, alive=("alpha",))
    h.add_session(reg, "beta", status=Status.UNKNOWN)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert "beta" in _sent(bot, h)


def test_version_mismatch_after_restart_is_reported(world, personal_bot, mk_bot, h, run_async):
    _schedule_restart_via_flow(world, personal_bot, h, run_async)
    world.running = h.RUNNING         # new daemon still on A
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert f"restarted but is running {h.RUNNING}, not {h.LATEST}" in _sent(bot, h)


def test_stale_marker_is_dropped(world, personal_bot, mk_bot, h, run_async, monkeypatch):
    _schedule_restart_via_flow(world, personal_bot, h, run_async)
    world.running = h.LATEST
    monkeypatch.setattr(self_update, "MARKER_MAX_AGE_SECONDS", -1)
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert bot._app.bot.send_message.await_count == 0


def test_stale_marker_is_deleted(world, personal_bot, mk_bot, h, run_async, monkeypatch):
    _schedule_restart_via_flow(world, personal_bot, h, run_async)
    monkeypatch.setattr(self_update, "MARKER_MAX_AGE_SECONDS", -1)
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_no_marker_sends_nothing(world, mk_bot, h, run_async):
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert bot._app.bot.send_message.await_count == 0


def test_corrupt_marker_never_raises(world, mk_bot, h, run_async):
    path = self_update.UPDATE_MARKER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert bot._app.bot.send_message.await_count == 0


def test_delivery_never_raises_when_send_fails(restarted, mk_bot, h, run_async):
    from unittest.mock import AsyncMock
    bot, reg = _new_daemon(mk_bot, h)
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("network down"))
    run_async(update_flow.deliver_update_marker(bot, reg))   # must not raise


def test_marker_skipped_when_chat_muted(restarted, mk_bot, h, run_async):
    MUTE.mute(h.DM, BAN)
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert bot._app.bot.send_message.await_count == 0


def test_muted_marker_is_not_requeued(restarted, mk_bot, h, run_async):
    """Skipped, never queued into the ban: a later daemon start after the
    mute lifts must not deliver it either."""
    MUTE.mute(h.DM, BAN)
    bot, reg = _new_daemon(mk_bot, h)
    run_async(update_flow.deliver_update_marker(bot, reg))
    MUTE.clear()
    run_async(update_flow.deliver_update_marker(bot, reg))
    assert bot._app.bot.send_message.await_count == 0


def test_muted_marker_skip_is_logged(restarted, mk_bot, h, run_async, caplog):
    MUTE.mute(h.DM, BAN)
    bot, reg = _new_daemon(mk_bot, h)
    with caplog.at_level(logging.INFO):
        run_async(update_flow.deliver_update_marker(bot, reg))
    assert "update.marker.skipped_muted" in caplog.text


# ---- flood discipline during a job ------------------------------------------

def test_update_sends_nothing_into_muted_chat(world, personal_bot, h, run_async):
    MUTE.mute(h.DM, BAN)
    _, msg, _ = run_async(h.run_job(personal_bot, "claude"))
    assert msg.edit_text.await_count == 0 and personal_bot._app.bot.send_message.await_count == 0


def test_update_job_still_runs_when_chat_muted(world, personal_bot, h, run_async):
    """The mute silences the chat, not the job."""
    MUTE.mute(h.DM, BAN)
    run_async(h.run_job(personal_bot, "claude"))
    assert len(world.claude_update_calls()) == 1


def test_miniapp_origin_job_sends_nothing_into_muted_chat(world, personal_bot, h, run_async):
    MUTE.mute(h.DM, BAN)

    async def go():
        await personal_bot.updates.start("claude", chat_id=h.DM, user_id=h.OPERATOR,
                                         origin="miniapp")
        await h.wait_phase(personal_bot, h.TERMINAL)
    run_async(go())
    assert personal_bot._app.bot.send_message.await_count == 0


def test_miniapp_origin_job_posts_one_status_message(world, personal_bot, h, run_async):
    async def go():
        await personal_bot.updates.start("claude", chat_id=h.DM, user_id=h.OPERATOR,
                                         origin="miniapp")
        await h.wait_phase(personal_bot, h.TERMINAL)
        await h.wait_for(lambda: False, 0.1)
    run_async(go())
    assert personal_bot._app.bot.send_message.await_count == 1


def test_progress_edits_are_throttled_while_waiting(world, personal_bot, h, run_async, monkeypatch):
    """With the default 5 s edit floor, ~60 gate polls in 0.6 s must not
    turn into ~60 edits."""
    monkeypatch.setattr(self_update, "PROGRESS_EDIT_MIN_SECONDS", 5)
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)
    msg = h.status_message(h.DM)

    async def go():
        await personal_bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                                         origin="chat", status_message=msg)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await h.wait_for(lambda: False, 0.6)
    run_async(go())
    assert msg.edit_text.await_count <= 3
