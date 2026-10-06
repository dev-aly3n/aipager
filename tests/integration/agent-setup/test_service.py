"""design.md success criterion 7: --service calls the installer (mocked)
with yes=True, and skips it while a daemon is detected."""

from __future__ import annotations


def test_service_calls_installer_once(env):
    env.setup("--service")
    assert len(env.installs) == 1


def test_service_calls_installer_with_yes_true(env):
    env.setup("--service")
    assert env.installs == [True]


def test_service_exits_zero(env):
    assert env.setup("--service").code == 0


def test_service_result_installed(env):
    assert env.setup("--service").json["service"] == {
        "requested": True, "result": "installed"}


def test_service_listed_in_changed(env):
    assert env.setup("--service").json["changed"][-1] == "service"


def test_service_never_prompts(env):
    env.setup("--service")
    assert env.traps == [] and env.interactive_calls == []


def test_service_installer_runs_after_the_yaml_is_written(env):
    seen = []
    import aipager.service as svc

    def _install(*, yes):
        seen.append(env.config_path.exists())
        return 0
    env.monkeypatch.setattr(svc, "install_service", _install)
    env.monkeypatch.setitem(svc._DISPATCH["linux"], "install", _install)
    env.setup("--service")
    assert seen == [True]


def test_no_service_flag_no_installer(env):
    env.setup()
    assert env.installs == []


def test_no_service_flag_result_not_requested(env):
    assert env.setup().json["service"] == {"requested": False,
                                           "result": "not_requested"}


def test_service_skipped_while_daemon_runs(env):
    env.daemon_pid = 4242
    env.setup("--service")
    assert env.installs == []


def test_service_skipped_result(env):
    env.daemon_pid = 4242
    assert env.setup("--service").json["service"]["result"] == (
        "skipped_daemon_running")


def test_service_skipped_daemon_pid_minus_one(env):
    """-1 is 'running, pid unknown' per the seam contract."""
    env.daemon_pid = -1
    env.setup("--service")
    assert env.installs == []


def test_service_skipped_still_exits_zero(env):
    env.daemon_pid = 4242
    assert env.setup("--service").code == 0


def test_service_skipped_sends_no_signal(env):
    env.daemon_pid = 4242
    env.setup("--service")
    assert env.signals == []


def test_service_on_unchanged_install_still_runs_installer(env):
    env.setup()
    env.setup("--service")
    assert env.installs == [True]


def test_service_failure_exits_1(env):
    env.service_rc = 1
    r = env.setup("--service")
    assert (r.code, r.json["error"]) == (1, "service_failed")


def test_service_failure_keeps_the_config(env):
    env.service_rc = 1
    r = env.setup("--service")
    assert env.config_path.exists() and "owner_dm" in r.json["changed"]


def test_service_failure_result(env):
    env.service_rc = 1
    assert env.setup("--service").json["service"]["result"] == "failed"


def test_service_already_installed_when_unit_unchanged(env):
    env.setup("--service")
    r = env.setup("--service")
    assert (r.json["service"]["result"], "service" in r.json["changed"]) == (
        "already_installed", False)


def test_service_not_called_when_setup_refuses(env):
    env.missing.add("dtach")
    env.setup("--service")
    assert env.installs == []


def test_service_not_called_when_chat_not_started(env):
    from agent_setup_support import HTTP
    env.tg.set("sendMessage", HTTP(400, "Bad Request: chat not found"))
    env.setup("--service")
    assert env.installs == []
