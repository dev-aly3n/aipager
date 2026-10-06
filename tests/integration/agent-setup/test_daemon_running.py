"""design.md success criterion 8: a running daemon gets a live reload
after a scope change; a token change yields restart_needed and no
signal. Setup never restarts or stops it."""

from __future__ import annotations

from agent_setup_support import OTHER_TOKEN


def _installed_then_daemon(env):
    assert env.setup().code == 0
    env.daemon_pid = 4242
    env.reloads.clear()
    env.signals.clear()


def test_chat_change_reloads_running_daemon(env):
    _installed_then_daemon(env)
    env.setup("--force", chat=777)
    assert env.reloads == [1]


def test_chat_change_reports_reloaded(env):
    _installed_then_daemon(env)
    d = env.setup("--force", chat=777).json["daemon"]
    assert d == {"running": True, "reload": "reloaded",
                 "restart_needed": False}


def test_role_change_reloads_running_daemon(env):
    _installed_then_daemon(env)
    env.setup("--force", "--role", "admin")
    assert env.reloads == [1]


def test_token_change_sends_no_reload(env):
    _installed_then_daemon(env)
    env.setup("--force", token_file=env.token_file(OTHER_TOKEN, name="o"))
    assert (env.reloads, env.signals) == ([], [])


def test_token_change_restart_needed(env):
    _installed_then_daemon(env)
    d = env.setup("--force", token_file=env.token_file(OTHER_TOKEN,
                                                       name="o")).json
    assert d["daemon"]["restart_needed"] is True


def test_token_change_next_step_names_stop_and_start(env):
    _installed_then_daemon(env)
    d = env.setup("--force", token_file=env.token_file(OTHER_TOKEN,
                                                       name="o")).json
    assert ("aipager service stop" in d["next_step"]
            and "aipager service start" in d["next_step"])


def test_token_change_without_daemon_no_restart_needed(env):
    env.setup()
    d = env.setup("--force", token_file=env.token_file(OTHER_TOKEN,
                                                       name="o")).json
    assert d["daemon"]["restart_needed"] is False


def test_reload_refused_is_a_warning(env):
    _installed_then_daemon(env)
    env.reload_outcome = ("refused", "the daemon said no")
    r = env.setup("--force", chat=777)
    assert (r.code, [w["code"] for w in r.json["warnings"]]) == (
        0, ["reload_refused"])


def test_reload_refused_reported(env):
    _installed_then_daemon(env)
    env.reload_outcome = ("refused", "the daemon said no")
    assert env.setup("--force", chat=777).json["daemon"]["reload"] == (
        "refused")


def test_not_reloaded_means_restart_needed(env):
    _installed_then_daemon(env)
    env.reload_outcome = ("not_reloaded", None)
    assert env.setup("--force", chat=777).json["daemon"][
        "restart_needed"] is True


def test_unchanged_with_daemon_does_not_reload(env):
    _installed_then_daemon(env)
    r = env.setup()
    assert (r.json["status"], env.reloads, env.signals) == (
        "unchanged", [], [])


def test_unchanged_with_daemon_reload_not_needed(env):
    _installed_then_daemon(env)
    assert env.setup().json["daemon"]["reload"] == "not_needed"


def test_daemon_running_reported(env):
    env.daemon_pid = 4242
    assert env.setup().json["daemon"]["running"] is True


def test_daemon_not_running_reported(env):
    assert env.setup().json["daemon"]["running"] is False


def test_fresh_install_with_daemon_never_signals_directly(env):
    """conftest's daemon_io.os.kill raises; setup must stay on the reload
    seam."""
    env.daemon_pid = 4242
    r = env.setup()
    assert r.code == 0


def test_daemon_running_never_spawns_service_manager(env):
    env.daemon_pid = 4242
    env.setup("--service", "--force", chat=777)
    assert [a for a in env.spawned if "systemctl" in str(a)
            or "launchctl" in str(a)] == []
