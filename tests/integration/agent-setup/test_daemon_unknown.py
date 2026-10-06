"""Iteration 2: daemon detection that raises means "unknown", which setup
treats as running (entrypoints.md, "Daemon detection"): ``daemon.running``
true, ``--service`` skipped and never installed, warning
``daemon_unknown``. Setup still never restarts or signals-to-stop a
daemon."""

from __future__ import annotations

import pytest

from agent_setup_support import OTHER_TOKEN, TOKEN, assert_no_secret


@pytest.fixture
def unknown(env, monkeypatch):
    import aipager.wizard.daemon_io as daemon_io

    def _boom():
        raise PermissionError("cannot read the daemon socket")
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", _boom)
    return env


def _codes(r):
    return [w["code"] for w in r.json["warnings"]]


def test_unknown_daemon_fresh_install_exits_zero(unknown):
    assert unknown.setup().code == 0


def test_unknown_daemon_reported_running(unknown):
    assert unknown.setup().json["daemon"]["running"] is True


def test_unknown_daemon_warning(unknown):
    assert "daemon_unknown" in _codes(unknown.setup())


def test_unknown_daemon_warning_has_a_message(unknown):
    w = [w for w in unknown.setup().json["warnings"]
         if w["code"] == "daemon_unknown"]
    assert len(w) == 1 and w[0]["message"].strip() != ""


def test_unknown_daemon_service_never_installed(unknown):
    unknown.setup("--service")
    assert unknown.installs == []


def test_unknown_daemon_service_result_skipped(unknown):
    assert unknown.setup("--service").json["service"] == {
        "requested": True, "result": "skipped_daemon_running"}


def test_unknown_daemon_service_not_in_changed(unknown):
    assert "service" not in unknown.setup("--service").json["changed"]


def test_unknown_daemon_service_still_exits_zero(unknown):
    assert unknown.setup("--service").code == 0


def test_unknown_daemon_service_warning(unknown):
    assert "daemon_unknown" in _codes(unknown.setup("--service"))


def test_unknown_daemon_dry_run_service_not_would_install(unknown):
    r = unknown.setup("--service", "--dry-run")
    assert r.json["service"]["result"] == "skipped_daemon_running"


def test_unknown_daemon_dry_run_installs_nothing(unknown):
    unknown.setup("--service", "--dry-run")
    assert unknown.installs == []


def test_unknown_daemon_service_on_unchanged_install_not_installed(unknown):
    """Error guessing: the re-run path (nothing to write) must also gate
    the installer."""
    assert unknown.setup().code == 0
    unknown.setup("--service")
    assert unknown.installs == []


def test_unknown_daemon_service_sends_no_signal(unknown):
    unknown.setup("--service")
    assert unknown.signals == []


def test_unknown_daemon_service_spawns_no_service_manager(unknown):
    unknown.setup("--service")
    assert [a for a in unknown.spawned if "systemctl" in str(a)
            or "launchctl" in str(a)] == []


def test_unknown_daemon_token_change_restart_needed(unknown):
    assert unknown.setup().code == 0
    d = unknown.setup("--force", token_file=unknown.token_file(
        OTHER_TOKEN, name="o")).json
    assert d["daemon"]["restart_needed"] is True


def test_unknown_daemon_token_change_sends_no_reload(unknown):
    assert unknown.setup().code == 0
    unknown.reloads.clear()
    unknown.setup("--force", token_file=unknown.token_file(
        OTHER_TOKEN, name="o"))
    assert (unknown.reloads, unknown.signals) == ([], [])


def test_unknown_daemon_plain_mode_mentions_it_on_stderr(unknown):
    """Plain mode: the warning is human output, not silent."""
    r = unknown.setup("--service", json_out=False)
    assert r.code == 0 and "daemon" in r.err.lower()


def test_unknown_daemon_detection_error_text_carrying_token_is_scrubbed(
        env, monkeypatch):
    """Error guessing: the detection exception text reaches the warning;
    it must not carry the token out."""
    import aipager.wizard.daemon_io as daemon_io

    def _boom():
        raise OSError(f"bad state near {TOKEN}")
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", _boom)
    r = env.setup("--service")
    assert_no_secret(r.out + r.err + r.log, "daemon_unknown output")


def test_known_running_daemon_has_no_unknown_warning(env):
    env.daemon_pid = 4242
    assert "daemon_unknown" not in _codes(env.setup())


def test_no_daemon_has_no_unknown_warning(env):
    assert "daemon_unknown" not in _codes(env.setup())


def test_no_daemon_service_still_installs(env):
    """Boundary: a clean None detection keeps the installer path."""
    env.setup("--service")
    assert env.installs == [True]
