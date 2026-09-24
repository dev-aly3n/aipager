"""SC-4 (gate half): the aipager update waits while any session is busy,
has a card, a background job, a tool in flight, an open permission, or
held answers; the restart is scheduled once the turn ends; "Restart now"
bypasses; the bounded wait re-asks; Cancel runs nothing; stale job ids are
refused. (design.md Success criteria #4; entrypoints.md callback table and
"aipager, waiting")."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import self_update
from aipager.bot import held
from aipager.state import Status


async def _start(bot, h, msg=None):
    msg = msg or h.status_message(h.DM)
    res = await bot.updates.start("aipager", chat_id=h.DM, user_id=h.OPERATOR,
                                  origin="chat", status_message=msg)
    assert res.ok, res.error
    return res, msg


def _blocker(registry, h, clause):
    """One session (or held answer) per equivalence class of the gate."""
    if clause == "busy":
        return h.add_session(registry, "w", status=Status.BUSY)
    if clause == "interactive":
        return h.add_session(registry, "w", status=Status.INTERACTIVE)
    if clause == "busy_card":
        s = h.add_session(registry, "w", status=Status.IDLE)
        s.busy_msg_id = 4242
        return s
    if clause == "background_job":
        s = h.add_session(registry, "w", status=Status.IDLE)
        s.job_continuation_active = True
        return s
    if clause == "tool_in_flight":
        s = h.add_session(registry, "w", status=Status.IDLE)
        s.pending_tool_started_at = time.monotonic()
        return s
    if clause == "pending_permission":
        s = h.add_session(registry, "w", status=Status.IDLE)
        s.pending_permission = {"tool_name": "Bash"}
        return s
    if clause == "held_answers":
        held.HELD.hold(chat_id=h.DM, session="claude-w", label="w",
                       rich_text="answer", plain_text="answer")
        return None
    raise AssertionError(clause)


def _unblock(sess, clause):
    if clause == "held_answers":
        held.HELD.clear()
        return
    sess.status = Status.IDLE
    if clause == "busy_card":
        sess.busy_msg_id = None
    elif clause == "background_job":
        sess.job_continuation_active = False
    elif clause == "tool_in_flight":
        sess.pending_tool_started_at = None
    elif clause == "pending_permission":
        sess.pending_permission = None


CLAUSES = ["busy", "interactive", "busy_card", "background_job",
           "tool_in_flight", "pending_permission", "held_answers"]


@pytest.mark.parametrize("clause", CLAUSES)
def test_gate_waits_for_each_blocker(world, personal_bot, h, run_async, clause):
    _blocker(personal_bot.registry, h, clause)

    async def go():
        await _start(personal_bot, h)
        return await h.wait_phase(personal_bot, "waiting_for_idle")
    assert run_async(go()) == "waiting_for_idle"


@pytest.mark.parametrize("clause", CLAUSES)
def test_gate_runs_no_installer_while_blocked(world, personal_bot, h, run_async, clause):
    _blocker(personal_bot.registry, h, clause)

    async def go():
        await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert world.upgrade_calls() == [] and world.schedule_calls() == []


@pytest.mark.parametrize("clause", CLAUSES)
def test_restart_waits_for_turn_then_schedules(world, personal_bot, h, run_async, clause):
    sess = _blocker(personal_bot.registry, h, clause)

    async def go():
        await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        _unblock(sess, clause)
        return await h.wait_phase(personal_bot, h.TERMINAL)
    assert run_async(go()) == "restart_scheduled"


@pytest.mark.parametrize("status", [Status.IDLE, Status.GONE, Status.UNKNOWN])
def test_non_blocking_session_states_do_not_hold_the_gate(world, personal_bot, h, run_async, status):
    h.add_session(personal_bot.registry, "calm", status=status)

    async def go():
        await _start(personal_bot, h)
        return await h.wait_phase(personal_bot, h.TERMINAL)
    assert run_async(go()) == "restart_scheduled"


def test_gone_session_with_stale_card_does_not_block(world, personal_bot, h, run_async):
    s = h.add_session(personal_bot.registry, "ghost", status=Status.GONE)
    s.busy_msg_id = 99

    async def go():
        await _start(personal_bot, h)
        return await h.wait_phase(personal_bot, h.TERMINAL)
    assert run_async(go()) == "restart_scheduled"


def test_session_in_another_chat_still_blocks(world, personal_bot, h, run_async):
    """A restart affects every scope, so the gate reads ALL sessions."""
    h.add_session(personal_bot.registry, "far", status=Status.BUSY, chat_id=-5555)

    async def go():
        await _start(personal_bot, h)
        return await h.wait_phase(personal_bot, "waiting_for_idle")
    assert run_async(go()) == "waiting_for_idle"


def test_waiting_message_names_the_busy_session(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "worker7", status=Status.BUSY)

    async def go():
        _, msg = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await h.wait_for(lambda: "worker7" in h.all_text(personal_bot, msg), 2)
        return msg
    msg = run_async(go())
    assert "worker7" in h.all_text(personal_bot, msg)


def test_waiting_message_counts_held_answers(world, personal_bot, h, run_async):
    _blocker(personal_bot.registry, h, "held_answers")

    async def go():
        _, msg = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await h.wait_for(lambda: "held answer" in h.all_text(personal_bot, msg), 2)
        return msg
    msg = run_async(go())
    assert "1 held answer" in h.all_text(personal_bot, msg)


def test_waiting_offers_restart_now_and_cancel(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, msg = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await h.wait_for(lambda: bool(h.buttons_of(msg)), 2)
        return res, msg
    res, msg = run_async(go())
    data = {d for _, d in h.buttons_of(msg)}
    jid = res.job["id"]
    assert {f"_:up:now:{jid}", f"_:up:stop:{jid}"} <= data, data


def test_snapshot_lists_blockers_while_waiting(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        await h.wait_for(lambda: bool(personal_bot.updates.snapshot()["blockers"]), 2)
        return personal_bot.updates.snapshot()
    assert run_async(go())["blockers"]


def test_one_open_reading_is_not_enough(world, personal_bot, h, run_async, monkeypatch):
    """Boundary: GATE_SETTLE_POLLS consecutive empty readings are required.
    A session that flickers idle for a single poll and goes busy again
    (the queued-prompt case) must not let the restart through."""
    monkeypatch.setattr(self_update, "GATE_POLL_SECONDS", 0.05)
    monkeypatch.setattr(self_update, "GATE_SETTLE_POLLS", 50)
    sess = h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        sess.status = Status.IDLE
        await h.wait_for(lambda: False, 0.12)
        sess.status = Status.BUSY
        await h.wait_for(lambda: False, 0.3)
    run_async(go())
    assert world.upgrade_calls() == []


# ---- Restart now / Wait more / Cancel -------------------------------------

def test_restart_now_bypasses_the_gate(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        personal_bot.updates.control("restart-now", res.job["id"])
        return await h.wait_phase(personal_bot, h.TERMINAL)
    assert run_async(go()) == "restart_scheduled"


def test_restart_now_leaves_the_busy_session_alone(world, personal_bot, h, run_async):
    s = h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        personal_bot.updates.control("restart-now", res.job["id"])
        await h.wait_phase(personal_bot, h.TERMINAL)
    run_async(go())
    assert s.status is Status.BUSY


def _tap(data, h, user_id=None):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = h.status_message(h.DM, 700)
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user_id or h.OPERATOR
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = h.DM
    update.effective_chat.type = "private"
    return update, query


def test_restart_now_button_bypasses_the_gate(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        update, _q = _tap(f"_:up:now:{res.job['id']}", h)
        await personal_bot._handle_callback(update, MagicMock())
        return await h.wait_phase(personal_bot, h.TERMINAL)
    assert run_async(go()) == "restart_scheduled"


def test_cancel_during_wait_runs_no_upgrade(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        personal_bot.updates.control("cancel", res.job["id"])
        return await h.wait_phase(personal_bot, h.TERMINAL)
    phase = run_async(go())
    assert phase == "cancelled" and world.upgrade_calls() == []


def test_cancel_button_during_wait_runs_no_upgrade(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        update, _q = _tap(f"_:up:stop:{res.job['id']}", h)
        await personal_bot._handle_callback(update, MagicMock())
        return await h.wait_phase(personal_bot, h.TERMINAL)
    phase = run_async(go())
    assert phase == "cancelled" and world.upgrade_calls() == []


def test_cancel_releases_the_lock(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        personal_bot.updates.control("cancel", res.job["id"])
        await h.wait_phase(personal_bot, h.TERMINAL)
    run_async(go())
    assert h.lock_is_free()


def test_gate_timeout_asks_again(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.1)
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await _start(personal_bot, h)
        return await h.wait_phase(personal_bot, "gate_timeout")
    assert run_async(go()) == "gate_timeout"


def test_gate_timeout_offers_three_buttons(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.1)
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, msg = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "gate_timeout")
        await h.wait_for(lambda: any("Wait" in t for t, _ in h.buttons_of(msg)), 2)
        return res, msg
    res, msg = run_async(go())
    labels = [t for t, _ in h.buttons_of(msg)]
    assert any("Wait 10 more min" in t for t in labels) \
        and any("Restart now" in t for t in labels) \
        and any("Cancel" in t for t in labels), labels


def test_gate_timeout_does_not_upgrade_by_itself(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.1)
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "gate_timeout")
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert world.upgrade_calls() == []


def test_wait_more_goes_back_to_waiting(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.2)
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "gate_timeout")
        personal_bot.updates.control("wait-more", res.job["id"])
        return await h.wait_phase(personal_bot, "waiting_for_idle", 1)
    assert run_async(go()) == "waiting_for_idle"


def test_wait_more_then_idle_schedules(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.2)
    s = h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "gate_timeout")
        personal_bot.updates.control("wait-more", res.job["id"])
        await h.wait_phase(personal_bot, "waiting_for_idle", 1)
        s.status = Status.IDLE
        return await h.wait_phase(personal_bot, h.TERMINAL)
    assert run_async(go()) == "restart_scheduled"


def test_unanswered_decision_auto_cancels(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(self_update, "GATE_DECISION_MAX_SECONDS", 0.2)
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await _start(personal_bot, h)
        return await h.wait_phase(personal_bot, h.TERMINAL, 3)
    assert run_async(go()) == "cancelled"


def test_unanswered_decision_releases_the_lock(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(self_update, "GATE_DECISION_MAX_SECONDS", 0.2)
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        await _start(personal_bot, h)
        await h.wait_phase(personal_bot, h.TERMINAL, 3)
    run_async(go())
    assert h.lock_is_free()


def test_stale_job_id_control_is_refused(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        return personal_bot.updates.control("restart-now", res.job["id"] + 1)
    out = run_async(go())
    assert (out.ok, out.error) == (False, "no_matching_job")


def test_stale_job_id_control_does_nothing(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        personal_bot.updates.control("restart-now", res.job["id"] + 1)
        await h.wait_for(lambda: False, 0.2)
        return personal_bot.updates.snapshot()["phase"]
    assert run_async(go()) == "waiting_for_idle"


def test_stale_job_button_says_update_already_finished(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "w", status=Status.BUSY)

    async def go():
        res, _ = await _start(personal_bot, h)
        await h.wait_phase(personal_bot, "waiting_for_idle")
        update, q = _tap(f"_:up:now:{res.job['id'] + 7}", h)
        await personal_bot._handle_callback(update, MagicMock())
        await h.wait_for(lambda: False, 0.1)
        return q
    q = run_async(go())
    assert "already finished" in "\n".join(h.texts_of(q, personal_bot._app.bot))


def test_control_with_no_job_is_refused(world, personal_bot, h):
    out = personal_bot.updates.control("restart-now", 12345)
    assert out.error == "no_matching_job"


def test_turn_started_during_upgrade_is_waited_out(world, personal_bot, h, run_async):
    s = h.add_session(personal_bot.registry, "w", status=Status.IDLE)
    world.upgrade_hook = lambda: setattr(s, "status", Status.BUSY)

    async def go():
        await _start(personal_bot, h)
        await h.wait_for(lambda: bool(world.upgrade_calls()), 3)
        await h.wait_for(lambda: False, 0.3)
        scheduled_while_busy = bool(world.schedule_calls())
        s.status = Status.IDLE
        phase = await h.wait_phase(personal_bot, h.TERMINAL)
        return scheduled_while_busy, phase
    scheduled_while_busy, phase = run_async(go())
    assert (scheduled_while_busy, phase) == (False, "restart_scheduled")


def test_killmode_reread_before_scheduling(world, personal_bot, h, run_async):
    """KillMode flips to control-group while the installer runs (after the
    plan was made): the restart must not be scheduled."""
    world.upgrade_hook = lambda: world.killmodes.__setitem__(0, "control-group")
    run_async(h.run_job(personal_bot, "aipager"))
    assert world.schedule_calls() == []
