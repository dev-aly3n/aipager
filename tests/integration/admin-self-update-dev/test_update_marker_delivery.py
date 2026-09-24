"""After the restart the NEW daemon announces the update once (8.36)."""

from __future__ import annotations

import time

from aipager import self_update
from aipager.bot import update_flow
from aipager.bot.flood import MUTE
from aipager.state import Status


def _marker(env, **over):
    data = {"from": "0.7.13", "to": "0.7.14", "chat_id": env.chat_id,
            "user_id": env.chat_id, "scheduled_at": time.time(),
            "sessions": [{"name": "claude-dev", "label": "dev"},
                         {"name": "claude-web", "label": "web"}]}
    data.update(over)
    self_update.write_marker(data)


def _deliver(env, run):
    async def scenario():
        await update_flow.deliver_update_marker(env.bot, env.registry)
    run(scenario)


def test_new_daemon_posts_updated_a_to_b_with_readopted_count(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    env.add_session("claude-web", "web", Status.BUSY)
    _marker(env)
    _deliver(env, run)
    assert len(env.sent) == 1
    assert env.sent[0]["chat_id"] == env.chat_id
    assert env.sent[0]["text"] == "✅ aipager updated 0.7.13 → 0.7.14, 2 sessions re-adopted"


def test_missing_sessions_are_named(env, run):
    env.running = "0.7.14"
    env.add_session("claude-dev", "dev", Status.IDLE)
    env.add_session("claude-web", "web", Status.UNKNOWN)   # not re-adopted
    _marker(env)
    _deliver(env, run)
    assert env.sent[0]["text"] == ("✅ aipager updated 0.7.13 → 0.7.14, 1 sessions "
                                   "re-adopted\n⚠️ Not back: web")


def test_marker_deleted_after_delivery(env, run):
    env.running = "0.7.14"
    _marker(env)
    _deliver(env, run)
    _deliver(env, run)
    assert not self_update.UPDATE_MARKER_PATH.exists()
    assert len(env.sent) == 1


def test_marker_skipped_when_chat_muted(env, run):
    env.running = "0.7.14"
    MUTE.mute(env.chat_id, 600, source="test")
    _marker(env)
    _deliver(env, run)
    assert env.sent == []
    env.bot._app.bot.send_message.assert_not_awaited()
    # Claimed anyway: never queued into the ban, never re-sent later.
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_stale_marker_is_dropped(env, run):
    env.running = "0.7.14"
    _marker(env, scheduled_at=time.time() - self_update.MARKER_MAX_AGE_SECONDS - 5)
    _deliver(env, run)
    assert env.sent == []
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_version_mismatch_after_restart_is_reported(env, run):
    env.running = "0.7.13"   # the restart brought the old version back
    _marker(env)
    _deliver(env, run)
    assert env.sent[0]["text"] == "⚠️ aipager restarted but is running 0.7.13, not 0.7.14."


def test_no_marker_sends_nothing(env, run):
    _deliver(env, run)
    assert env.sent == []


def test_delivery_never_raises(env, run, monkeypatch):
    env.running = "0.7.14"
    _marker(env)

    async def _boom(*a, **k):
        raise RuntimeError("telegram down")
    env.bot._app.bot.send_message = _boom
    _deliver(env, run)   # must not raise


def test_daemon_schedules_and_cancels_marker_delivery():
    import inspect

    from aipager.cli import daemon
    src = inspect.getsource(daemon._run_daemon)
    assert "update_flow.deliver_update_marker(bot, registry)" in src
    assert src.index("session_monitor.start()") < src.index("deliver_update_marker")
    assert "marker_task.cancel()" in src


def test_interrupted_update_is_announced_with_the_repair(env, run):
    """A shutdown killed the installer: the next daemon says so, once."""
    self_update.write_marker({
        "interrupted": "upgrading", "kind": "aipager", "chat_id": env.chat_id,
        "user_id": env.chat_id, "job_id": 1, "from": "0.7.13",
        "source_kind": "pipx", "scheduled_at": time.time()})
    _deliver(env, run)
    (sent,) = env.sent
    assert sent["chat_id"] == env.chat_id
    assert "interrupted" in sent["text"] and "may be partial" in sent["text"]
    assert "pipx install --force aipager" in sent["text"]
    assert "updated" not in sent["text"]
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_interrupted_claude_update_suggests_claude_update(env, run):
    self_update.write_marker({
        "interrupted": "claude_updating", "kind": "claude", "chat_id": env.chat_id,
        "job_id": 1, "from": "0.7.13", "source_kind": "pipx",
        "scheduled_at": time.time()})
    _deliver(env, run)
    (sent,) = env.sent
    assert "run `claude update` again" in sent["text"]
