"""SC-4 / SC-8 / SC-6: "Update aipager" on a systemd install with
KillMode=process -- happy path, the absolute installer argv, the marker,
exactly one detached restart; and every failure class (failed, timed out,
unchanged, broken new version, refused source, schedule failure) that
must schedule no restart and write no marker.
(design.md Success criteria #4, #5 (argv half), #6, #8)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from aipager import self_update


def _ap(bot, h, run_async, **kw):
    async def go():
        return await h.run_job(bot, "aipager", **kw)
    return run_async(go())


def _marker():
    return self_update.UPDATE_MARKER_PATH


# ---- happy path ---------------------------------------------------------

def test_happy_path_ends_restart_scheduled(world, personal_bot, h, run_async):
    _, _, phase = _ap(personal_bot, h, run_async)
    assert phase == "restart_scheduled"


def test_happy_path_runs_the_installer_once(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert len(world.upgrade_calls()) == 1


@pytest.mark.parametrize("kind,expected", [
    ("pipx", ["/abs/tools/pipx", "upgrade", "aipager"]),
    ("uv", ["/abs/tools/uv", "tool", "upgrade", "aipager", "--refresh"]),
])
def test_installer_argv_matches_the_table(world, personal_bot, h, run_async, kind, expected):
    world.source_kind = kind
    _ap(personal_bot, h, run_async)
    assert world.upgrade_calls()[0]["argv"] == expected


def test_pip_venv_argv_uses_the_venv_python(world, personal_bot, h, run_async):
    world.source_kind = "pip"
    _ap(personal_bot, h, run_async)
    argv = world.upgrade_calls()[0]["argv"]
    assert argv[1:] == ["-m", "pip", "install", "--upgrade", "aipager"] \
        and argv[0].startswith(world.prefix) and os.path.isabs(argv[0])


def test_installer_argv_head_is_absolute(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert os.path.isabs(world.upgrade_calls()[0]["argv"][0])


def test_installer_runs_with_the_upgrade_timeout(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert world.upgrade_calls()[0]["timeout"] == self_update.UPGRADE_TIMEOUT_SECONDS


def test_installer_env_pins_pipx_home_to_this_install(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    env = world.upgrade_calls()[0]["env"] or {}
    expected = os.path.normpath(os.path.join(world.prefix, "..", ".."))
    assert os.path.normpath(env.get("PIPX_HOME", "")) == expected


def test_installer_env_carries_no_bot_token(world, personal_bot, h, run_async, monkeypatch):
    monkeypatch.setenv("CLAUDE_TG_BOT_TOKEN", "123456789:AAHfakeTokenValueForTestsOnly_abcdefghij")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-fake")
    _ap(personal_bot, h, run_async)
    env = world.upgrade_calls()[0]["env"] or {}
    assert "CLAUDE_TG_BOT_TOKEN" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_no_call_uses_a_shell(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    heads = {a[0].rsplit("/", 1)[-1] for a in world.argvs()}
    assert not heads & {"sh", "bash", "zsh"}


def test_happy_path_schedules_exactly_one_restart(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert len(world.schedule_calls()) == 1


def test_restart_argv_is_detached_single_restart(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    argv = world.schedule_calls()[0]["argv"]
    assert argv[0] == "/abs/tools/systemd-run" and argv[1] == "--user" \
        and "--on-active=5s" in argv and "--collect" in argv \
        and argv[-4:] == ["/abs/tools/systemctl", "--user", "restart", "aipager.service"]


def test_restart_argv_uses_unique_transient_unit(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    argv = world.schedule_calls()[0]["argv"]
    assert any(a.startswith("--unit=aipager-update-restart-") for a in argv)


def test_restart_never_starts_a_second_daemon(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    argv = world.schedule_calls()[0]["argv"]
    assert "start" not in argv and argv.count("restart") == 1


def test_no_systemctl_restart_is_run_inline(world, personal_bot, h, run_async):
    """The daemon must not restart its own unit synchronously."""
    _ap(personal_bot, h, run_async)
    inline = [a for a in world.argvs()
              if a[0].rsplit("/", 1)[-1] == "systemctl" and "restart" in a]
    assert inline == []


def test_happy_path_writes_the_marker(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert _marker().exists()


def test_marker_has_the_documented_keys(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "alpha")
    _ap(personal_bot, h, run_async)
    data = json.loads(_marker().read_text())
    assert {"from", "to", "chat_id", "user_id", "sessions", "scheduled_at"} <= set(data)


def test_marker_records_versions_and_requester(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    data = json.loads(_marker().read_text())
    assert (data["from"], data["to"], data["chat_id"], data["user_id"]) == \
        (h.RUNNING, h.LATEST, h.DM, h.OPERATOR)


def test_marker_lists_sessions_by_name_and_label(world, personal_bot, h, run_async):
    h.add_session(personal_bot.registry, "alpha")
    _ap(personal_bot, h, run_async)
    sessions = json.loads(_marker().read_text())["sessions"]
    assert {"name": "claude-alpha", "label": "alpha"} in sessions


def test_marker_is_private(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert stat.S_IMODE(_marker().stat().st_mode) == 0o600


def test_marker_written_before_restart_is_scheduled(world, personal_bot, h, run_async, monkeypatch):
    seen = {}
    real = world._run_command

    def spy(argv, *, timeout, env=None, capture=True):
        if str(argv[0]).endswith("systemd-run"):
            seen["marker_at_schedule"] = _marker().exists()
        return real(argv, timeout=timeout, env=env, capture=capture)
    monkeypatch.setattr(self_update, "_run_command", spy)
    _ap(personal_bot, h, run_async)
    assert seen.get("marker_at_schedule") is True


def test_happy_path_reports_a_to_b_restarting(world, personal_bot, h, run_async):
    """Roadmap 8.45: "installed, restarting…" with no countdown (was:
    "installed. Restarting in 5 s…", which the timer did not honour)."""
    _, msg, _ = _ap(personal_bot, h, run_async)
    text = h.all_text(personal_bot, msg).replace("<b>", "").replace("</b>", "")
    assert f"aipager {h.RUNNING} → {h.LATEST} installed, restarting…" in text
    assert "Restarting in" not in text


def test_lock_stays_held_once_restart_is_scheduled(world, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert not h.lock_is_free()


def test_status_message_is_the_only_new_message_in_chat_flow(world, personal_bot, h, run_async):
    """Flood discipline: a chat-started job edits its one status message
    instead of sending new ones."""
    _ap(personal_bot, h, run_async)
    assert personal_bot._app.bot.send_message.await_count == 0


# ---- failure classes: no marker, no restart -------------------------------

@pytest.fixture(params=["failed", "timeout", "unchanged", "smoke", "schedule_fail"])
def failing(request, world):
    kind = request.param
    if kind == "failed":
        world.upgrade_rc = 1
    elif kind == "timeout":
        world.upgrade_timed_out = True
    elif kind == "unchanged":
        world.probe = (h_running(), True, None)
    elif kind == "smoke":
        world.probe = (None, False, "ImportError: cannot import name 'x'")
    elif kind == "schedule_fail":
        world.schedule_rc = 1
    return kind


def h_running():
    from aipager.self_update import running_version
    return running_version()


def test_failure_class_writes_no_marker(world, failing, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert not _marker().exists()


def test_failure_class_does_not_end_restart_scheduled(world, failing, personal_bot, h, run_async):
    _, _, phase = _ap(personal_bot, h, run_async)
    assert phase != "restart_scheduled"


def test_failure_class_releases_the_lock(world, failing, personal_bot, h, run_async):
    _ap(personal_bot, h, run_async)
    assert h.lock_is_free()


@pytest.mark.parametrize("knob", ["failed", "timeout", "unchanged", "smoke"])
def test_non_scheduling_failure_calls_no_systemd_run(world, personal_bot, h, run_async, knob):
    if knob == "failed":
        world.upgrade_rc = 1
    elif knob == "timeout":
        world.upgrade_timed_out = True
    elif knob == "unchanged":
        world.probe = (h.RUNNING, True, None)
    else:
        world.probe = (None, False, "ImportError")
    _ap(personal_bot, h, run_async)
    assert world.schedule_calls() == []


def test_failed_upgrade_reports_exit_code(world, personal_bot, h, run_async):
    world.upgrade_rc = 2
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "upgrade failed (exit 2)" in h.all_text(personal_bot, msg)


def test_timed_out_upgrade_says_timed_out(world, personal_bot, h, run_async):
    world.upgrade_timed_out = True
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "timed out" in h.all_text(personal_bot, msg)


def test_unchanged_version_says_already_at(world, personal_bot, h, run_async):
    world.probe = (h.RUNNING, True, None)
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert f"already at {h.RUNNING}" in h.all_text(personal_bot, msg)


def test_a_local_folder_install_is_never_upgraded_from_here(world, personal_bot, h, run_async):
    """8.46 (operator 2026-09-26): was "unchanged local install explains the
    source". `pipx upgrade` reinstalls a folder install from the folder, not
    PyPI, so an update is refused before any installer runs."""
    world.origin = "local"
    with pytest.raises(AssertionError, match="not_upgradable"):
        _ap(personal_bot, h, run_async)
    assert not any("upgrade" in " ".join(map(str, c)) for c in world.calls)


def test_broken_new_version_says_failed_to_import(world, personal_bot, h, run_async):
    world.probe = (None, False, "ImportError")
    _, msg, _ = _ap(personal_bot, h, run_async)
    text = h.all_text(personal_bot, msg)
    assert "failed to import" in text and f"still running {h.RUNNING}" in text


def test_schedule_failure_removes_the_marker(world, personal_bot, h, run_async):
    world.schedule_rc = 1
    _ap(personal_bot, h, run_async)
    assert not _marker().exists()


def test_upgrade_output_tail_reaches_the_chat_on_failure(world, personal_bot, h, run_async):
    world.upgrade_rc = 1
    world.upgrade_output = "ERROR: No matching distribution found for aipager"
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert "No matching distribution" in h.all_text(personal_bot, msg)


# ---- refused sources and the pre-check ------------------------------------

def test_editable_install_start_is_refused_not_upgradable(world, personal_bot, h, run_async):
    world.origin = "editable"

    async def go():
        return await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM))
    res = run_async(go())
    assert (res.ok, res.error) == (False, "not_upgradable")


def test_editable_install_runs_no_installer(world, personal_bot, h, run_async):
    world.origin = "editable"

    async def go():
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM))
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert world.upgrade_calls() == [] and world.schedule_calls() == []


def test_foreign_owned_prefix_runs_no_installer(world, personal_bot, h, run_async, monkeypatch):
    """Never touch another user's install: a prefix not owned by this uid."""
    from aipager import install_source
    patched = install_source.detect_install_source
    monkeypatch.setattr(install_source, "detect_install_source",
                        lambda **kw: patched(**{"uid": os.getuid() + 4242, **kw}))

    async def go():
        await personal_bot.updates.start(
            "aipager", chat_id=h.DM, user_id=h.OPERATOR, origin="chat",
            status_message=h.status_message(h.DM))
        await h.wait_for(lambda: False, 0.2)
    run_async(go())
    assert world.upgrade_calls() == []


def test_index_install_already_current_skips_everything(world, personal_bot, h, run_async):
    world.latest_pypi = h.RUNNING
    _, msg, _ = _ap(personal_bot, h, run_async)
    assert world.upgrade_calls() == [] and "already" in h.all_text(personal_bot, msg)


def test_unknown_latest_still_allows_upgrade(world, personal_bot, h, run_async):
    """Boundary: PyPI unreachable must not be read as "up to date"."""
    world.latest_pypi = None
    _ap(personal_bot, h, run_async)
    assert len(world.upgrade_calls()) == 1


# ---- "Both" ---------------------------------------------------------------

def test_both_runs_claude_before_the_installer(world, personal_bot, h, run_async):
    async def go():
        return await h.run_job(personal_bot, "both")
    run_async(go())
    argvs = world.argvs()
    ci = next(i for i, a in enumerate(argvs) if a[1:] == ["update"])
    ui = next(i for i, a in enumerate(argvs) if world.is_upgrade(a))
    assert ci < ui


def test_both_schedules_one_restart(world, personal_bot, h, run_async):
    async def go():
        return await h.run_job(personal_bot, "both")
    run_async(go())
    assert len(world.schedule_calls()) == 1
