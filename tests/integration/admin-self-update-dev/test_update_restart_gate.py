"""The restart gate: wait for idle, Restart now, Wait more, Cancel (8.36)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import self_update
from aipager.state import Status


def test_restart_waits_for_turn_then_schedules(env, run):
    sess = env.add_session(status=Status.BUSY)

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        await env.until(lambda: "dev: running a turn" in env.last_text())
        # Gate first: nothing is installed while a turn runs.
        assert env.upgrade_calls() == []
        assert env.manager.snapshot()["blockers"] == ["dev: running a turn"]
        assert env.last_markup_data() == [
            f"_:up:now:{env.manager.snapshot()['id']}",
            f"_:up:stop:{env.manager.snapshot()['id']}"]
        sess.status = Status.IDLE
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "restart_scheduled"
    assert len(env.schedule_calls()) == 1


def test_turn_started_during_upgrade_is_waited_out(env, run):
    sess = env.add_session()
    env.upgrade_hook = lambda: setattr(sess, "status", Status.BUSY)

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls()
                        and env.manager.snapshot()["phase"] == "waiting_for_idle")
        assert env.schedule_calls() == []
        assert not self_update.UPDATE_MARKER_PATH.exists()
        sess.status = Status.IDLE
        await env.finish()
    run(scenario)
    assert len(env.schedule_calls()) == 1


def test_restart_now_bypasses_gate(env, run):
    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start("aipager")
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        assert env.manager.control("restart-now", res.job["id"]).ok
        await env.finish()
    run(scenario)
    # Still BUSY, but the admin said so.
    assert len(env.upgrade_calls()) == 1
    assert len(env.schedule_calls()) == 1


def test_gate_timeout_asks_with_three_buttons(env, run, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)
    env.add_session(status=Status.BUSY)
    env.add_session("claude-x", "x")
    from aipager.bot import held
    held.HELD.hold(chat_id=1, session="claude-x", label="x",
                   rich_text="a", plain_text="a")

    async def scenario():
        res = await env.start("aipager")
        await env.until(lambda: env.manager.snapshot()["phase"] == "gate_timeout")
        await env.until(lambda: len(env.last_markup_data()) == 3)
        jid = res.job["id"]
        assert env.last_markup_data() == [f"_:up:wait:{jid}", f"_:up:now:{jid}",
                                          f"_:up:stop:{jid}"]
        text = env.last_text()
        assert "Wait" in text and "Restart now interrupts" in text
        assert "held answers" in text
        assert env.upgrade_calls() == []
        env.manager.control("cancel", jid)
        await env.finish()
    run(scenario)


def test_wait_more_extends_deadline(env, run, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.05)
    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start("aipager")
        jid = res.job["id"]
        await env.until(lambda: env.manager.snapshot()["phase"] == "gate_timeout")
        # Only meaningful at the prompt; refused before it.
        monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 30)
        assert env.manager.control("wait-more", jid).ok
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        assert env.manager.control("wait-more", jid).ok is False
        env.manager.control("cancel", jid)
        await env.finish()
    run(scenario)


def test_cancel_during_wait_runs_no_upgrade(env, run):
    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start("aipager")
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        assert env.manager.control("cancel", res.job["id"]).ok
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert env.upgrade_calls() == []
    assert "nothing was installed" in env.last_text()


def test_cancel_is_refused_while_the_installer_runs(env, run):
    import threading

    env.upgrade_release = threading.Event()

    async def scenario():
        res = await env.start("aipager")
        await env.until(lambda: env.upgrade_calls())
        assert env.manager.snapshot()["phase"] == "upgrading"
        assert env.manager.control("cancel", res.job["id"]).ok is False
        assert env.last_markup_data() == []  # no Cancel button mid-install
        env.upgrade_release.set()
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "restart_scheduled"


def test_unanswered_gate_prompt_auto_cancels_and_releases_lock(env, run, monkeypatch):
    monkeypatch.setattr(self_update, "GATE_MAX_WAIT_SECONDS", 0.02)
    monkeypatch.setattr(self_update, "GATE_DECISION_MAX_SECONDS", 0.05)
    env.add_session(status=Status.BUSY)

    async def scenario():
        await env.start("aipager")
        await env.finish(timeout=5)
    run(scenario)
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert env.upgrade_calls() == []
    lock = self_update.UpdateLock()
    assert lock.try_acquire()
    lock.release()


def test_gate_needs_two_consecutive_open_readings(env, run, monkeypatch):
    readings = [[], ["dev: running a turn"], [], []]
    seen: list = []

    def _blockers(registry, **kw):
        value = readings.pop(0) if readings else []
        seen.append(value)
        return value
    monkeypatch.setattr(self_update, "restart_blockers", _blockers)

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls())
        # The upgrade may only start after [], [busy], [], [] — never
        # after the single open reading that preceded the busy one.
        assert seen[:4] == [[], ["dev: running a turn"], [], []]
        await env.finish()
    run(scenario)


def test_control_tap_with_stale_job_id_is_refused(env, run):
    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start("aipager")
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        stale = res.job["id"] - 1
        query = MagicMock()
        query.data = f"_:up:now:{stale}"
        query.answer = AsyncMock()
        query.message = env.message
        query.from_user = MagicMock()
        query.from_user.id = env.chat_id  # the operator's DM id
        update = MagicMock()
        update.callback_query = query
        update.effective_chat = MagicMock()
        update.effective_chat.id = env.chat_id
        update.effective_user = query.from_user
        await env.bot._handle_callback(update, MagicMock())
        texts = [c.args[0] for c in query.answer.await_args_list if c.args]
        assert "That update already finished" in texts
        # Nothing happened to the running job.
        assert env.manager.snapshot()["phase"] == "waiting_for_idle"
        assert env.upgrade_calls() == []
        env.manager.control("cancel", res.job["id"])
        await env.finish()
    run(scenario)
