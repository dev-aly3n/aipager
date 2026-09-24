"""Update aipager: upgrade, probe, marker, detached restart (roadmap 8.36)."""

from __future__ import annotations

import json

from aipager import install_source, self_update
from aipager.install_source import InstallSource
from aipager.self_update import UpdateLock


def test_happy_path_schedules_one_detached_restart(env, run):
    env.add_session("claude-dev", "dev")

    async def scenario():
        res = await env.start("aipager")
        assert res.ok
        await env.finish()
    run(scenario)
    snap = env.manager.snapshot()
    assert snap["phase"] == "restart_scheduled"
    assert len(env.upgrade_calls()) == 1
    assert env.upgrade_calls()[0] == ["/abs/bin/pipx", "upgrade", "aipager"]
    assert len(env.schedule_calls()) == 1
    assert "0.7.13 → 0.7.14 installed. Restarting in 5 s" in env.last_text()
    marker = json.loads(self_update.UPDATE_MARKER_PATH.read_text())
    assert marker["from"] == "0.7.13" and marker["to"] == "0.7.14"
    assert marker["chat_id"] == env.chat_id
    assert marker["sessions"] == [{"name": "claude-dev", "label": "dev"}]


def test_lock_stays_held_after_a_restart_is_scheduled(env, run):
    async def scenario():
        await env.start("aipager")
        await env.finish()
        # Until this process exits, nothing else may start an update.
        assert UpdateLock().try_acquire() is False
    run(scenario)


def test_failed_upgrade_writes_no_marker_and_schedules_nothing(env, run):
    env.upgrade_rc = 1
    env.upgrade_output = "ERROR: No matching distribution found for aipager"

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "failed"
    assert env.schedule_calls() == []
    assert not self_update.UPDATE_MARKER_PATH.exists()
    assert "upgrade failed (exit 1)" in env.last_text()
    assert "No matching distribution" in env.last_text()


def test_upgrade_timeout_schedules_no_restart(env, run):
    env.upgrade_timeout = True

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "failed"
    assert "timed out" in env.last_text()
    assert env.schedule_calls() == []
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_same_version_does_not_restart_and_explains_local_source(env, run):
    env.source = InstallSource(kind="pipx", prefix=env.source.prefix,
                               python=env.source.python, origin="local",
                               origin_detail="/home/op/aipager", upgradable=True)
    env.probe_version = "0.7.13"

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "done"
    text = env.last_text()
    assert "already at 0.7.13" in text
    assert "local path" in text and "/home/op/aipager" in text
    assert env.schedule_calls() == []
    # A local-path install is never pre-checked against PyPI.
    assert self_update.PYPI_URL not in env.fetches


def test_new_version_import_failure_blocks_restart(env, run):
    env.probe_ok = False

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "failed"
    text = env.last_text()
    assert "failed to import" in text and "still running 0.7.13" in text
    assert "pipx install --force aipager" in text
    assert env.schedule_calls() == []
    assert not self_update.UPDATE_MARKER_PATH.exists()


def test_changed_version_comes_from_subprocess_probe(env, run):
    # In-process metadata still says the OLD version; only the fresh
    # interpreter sees what the installer put on disk.
    env.probe_version = "0.8.0"
    env.pypi_latest = "0.8.0"

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    probe = [c for c in env.calls if "-I" in c]
    assert probe and probe[0][0] == env.source.python
    assert json.loads(self_update.UPDATE_MARKER_PATH.read_text())["to"] == "0.8.0"
    assert "0.7.13 → 0.8.0" in env.last_text()


def test_index_install_already_current_skips_gate(env, run):
    from aipager.state import Status

    env.pypi_latest = "0.7.13"
    env.add_session(status=Status.BUSY)

    async def scenario():
        await env.start("aipager")
        await env.finish(timeout=2)
    run(scenario)
    assert env.manager.snapshot()["phase"] == "done"
    assert "already up to date" in env.last_text()
    assert env.upgrade_calls() == []


def test_refused_source_is_not_upgradable(env, run):
    env.source = InstallSource(kind="editable", prefix="/src", python="/src/python",
                               reason="this is an editable (development) install")

    async def scenario():
        res = await env.start("aipager")
        assert (res.ok, res.error) == (False, "not_upgradable")
        res = await env.start("both")
        assert (res.ok, res.error) == (False, "not_upgradable")
    run(scenario)
    assert env.calls == []


def test_missing_installer_is_reported(env, run, monkeypatch):
    monkeypatch.setattr(install_source, "resolve_tool", lambda n: None)

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "failed"
    assert "couldn't find" in env.last_text()


def test_marker_written_before_restart_is_scheduled(env, run):
    seen = []
    env.schedule_hook = lambda argv: seen.append(self_update.UPDATE_MARKER_PATH.exists())

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert seen == [True]


def test_schedule_failure_removes_marker(env, run):
    env.schedule_rc = 1

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "failed"
    assert len(env.schedule_calls()) == 1
    assert not self_update.UPDATE_MARKER_PATH.exists()
    assert "scheduling the restart failed" in env.last_text()
    # And the lock is back.
    lock = UpdateLock()
    assert lock.try_acquire()
    lock.release()


def test_second_update_refused_while_first_runs(env, run):
    import threading

    env.upgrade_release = threading.Event()

    async def scenario():
        first = await env.start("aipager")
        assert first.ok
        await env.until(lambda: env.upgrade_calls())
        second = await env.start("claude")
        assert (second.ok, second.error) == (False, "update_in_progress")
        assert second.job["id"] == first.job["id"]
        env.upgrade_release.set()
        await env.finish()
    run(scenario)


def _lock_free() -> bool:
    lock = UpdateLock()
    free = lock.try_acquire()
    lock.release()
    return free


def test_lock_released_after_success(env, run):
    env.set_under_unit(False)  # manual restart: the job simply ends

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "done"
    assert _lock_free()


def test_lock_released_after_failure(env, run):
    env.upgrade_rc = 2

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert _lock_free()


def test_lock_released_after_cancel(env, run):
    from aipager.state import Status

    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start("aipager")
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        assert env.manager.control("cancel", res.job["id"]).ok
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "cancelled"
    assert _lock_free()


def test_lock_released_after_exception(env, run, monkeypatch):
    def _boom(python):
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(self_update, "probe_installed_version", _boom)

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["phase"] == "failed"
    assert "unexpected error" in env.last_text()
    assert _lock_free()


def test_both_runs_claude_first_then_aipager(env, run):
    async def scenario():
        await env.start("both")
        await env.finish()
    run(scenario)
    claude_at = env.calls.index([env.claude_path, "update"])
    upgrade_at = env.calls.index(["/abs/bin/pipx", "upgrade", "aipager"])
    assert claude_at < upgrade_at
    text = env.last_text()
    assert "Claude Code</b> 2.1.281 → 2.1.290" in text
    assert "Restarting in 5 s" in text
