"""SC-3: "Update Claude Code" runs `<absolute claude> update` with a timeout,
reports A -> B, says running sessions keep A until restarted, lists them,
and restarts none. (design.md Success criteria #3; entrypoints.md
"Observable job messages -- Claude update")."""

from __future__ import annotations

import os

import pytest

from aipager import self_update
from aipager.state import Status


@pytest.fixture
def no_session_ops(monkeypatch):
    """Record any attempt to touch a session's PTY."""
    touched: list = []
    for name in ("kill_session", "launch_session", "send_keys",
                 "send_interrupt", "send_ctrl_c"):
        monkeypatch.setattr(f"aipager.dtach.inject.{name}",
                            lambda *a, _n=name, **k: touched.append(_n),
                            raising=False)
    return touched


def _claude(bot, h, run_async, **kw):
    async def go():
        return await h.run_job(bot, "claude", **kw)
    return run_async(go())


def test_claude_update_argv_is_absolute_claude_update(world, personal_bot, h, run_async):
    _claude(personal_bot, h, run_async)
    assert [c["argv"] for c in world.claude_update_calls()] == [[world.claude_path, "update"]]


def test_claude_update_argv_head_is_absolute(world, personal_bot, h, run_async):
    _claude(personal_bot, h, run_async)
    assert os.path.isabs(world.claude_update_calls()[0]["argv"][0])


def test_claude_update_passes_a_timeout(world, personal_bot, h, run_async):
    _claude(personal_bot, h, run_async)
    assert world.claude_update_calls()[0]["timeout"] == self_update.CLAUDE_UPDATE_TIMEOUT_SECONDS


def test_claude_update_reports_a_to_b(world, personal_bot, h, run_async):
    _, msg, _ = _claude(personal_bot, h, run_async)
    text = h.all_text(personal_bot, msg)
    assert f"{h.CLAUDE_OLD} → {h.CLAUDE_NEW}" in text


def test_claude_update_ends_done(world, personal_bot, h, run_async):
    _, _, phase = _claude(personal_bot, h, run_async)
    assert phase == "done"


def test_claude_update_says_running_sessions_keep_old_version(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "alpha")
    _, msg, _ = _claude(personal_bot, h, run_async)
    text = h.all_text(personal_bot, msg).lower()
    assert "keep the old version" in text and "restarted" in text


def test_claude_update_lists_this_chats_sessions(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "alpha")
    h.add_session(personal_bot.registry, "beta", status=Status.BUSY)
    _, msg, _ = _claude(personal_bot, h, run_async)
    text = h.all_text(personal_bot, msg)
    assert "alpha" in text and "beta" in text


def test_claude_update_omits_gone_sessions(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "ghostly", status=Status.GONE)
    h.add_session(personal_bot.registry, "alpha")
    _, msg, _ = _claude(personal_bot, h, run_async)
    assert "ghostly" not in h.all_text(personal_bot, msg)


def test_claude_update_counts_sessions_in_other_chats(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "alpha")
    h.add_session(personal_bot.registry, "elsewhere1", chat_id=-4242)
    h.add_session(personal_bot.registry, "elsewhere2", chat_id=-4242)
    _, msg, _ = _claude(personal_bot, h, run_async)
    text = h.all_text(personal_bot, msg)
    assert "2 in other chats" in text


def test_claude_update_does_not_name_other_chats_sessions(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "alpha")
    h.add_session(personal_bot.registry, "secretproj", chat_id=-4242)
    _, msg, _ = _claude(personal_bot, h, run_async)
    assert "secretproj" not in h.all_text(personal_bot, msg)


def test_claude_update_says_new_sessions_use_new_version(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "alpha")
    _, msg, _ = _claude(personal_bot, h, run_async)
    assert f"new sessions use {h.CLAUDE_NEW}" in h.all_text(personal_bot, msg).lower()


def test_claude_update_touches_no_session(world, personal_bot, h, run_async, no_session_ops):
    h.add_session(personal_bot.registry, "alpha")
    h.add_session(personal_bot.registry, "beta", status=Status.BUSY)
    _claude(personal_bot, h, run_async)
    assert no_session_ops == []


def test_claude_update_leaves_session_status_alone(world, personal_bot, h, run_async):
    s = h.add_session(personal_bot.registry, "beta", status=Status.BUSY)
    _claude(personal_bot, h, run_async)
    assert s.status is Status.BUSY


def test_claude_update_schedules_no_daemon_restart(world, personal_bot, h, run_async):
    _claude(personal_bot, h, run_async)
    assert world.schedule_calls() == [] and world.upgrade_calls() == []


def test_claude_update_timeout_is_reported(world, personal_bot, h, run_async):
    world.claude_timed_out = True
    _, msg, _ = _claude(personal_bot, h, run_async)
    assert "timed out" in h.all_text(personal_bot, msg)


def test_claude_update_failure_reports_exit_code(world, personal_bot, h, run_async):
    world.claude_rc = 3
    _, msg, _ = _claude(personal_bot, h, run_async)
    assert "failed (exit 3)" in h.all_text(personal_bot, msg)


def test_claude_update_failure_ends_failed(world, personal_bot, h, run_async):
    world.claude_rc = 3
    _, _, phase = _claude(personal_bot, h, run_async)
    assert phase == "failed"


def test_claude_already_current_says_up_to_date(world, personal_bot, h, run_async):
    world.claude_after = h.CLAUDE_OLD
    _, msg, _ = _claude(personal_bot, h, run_async)
    assert "already up to date" in h.all_text(personal_bot, msg)


def test_claude_update_lock_released_afterwards(world, personal_bot, h, run_async):
    _claude(personal_bot, h, run_async)
    assert h.lock_is_free()
