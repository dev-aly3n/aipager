"""A malformed v2 config must degrade diagnostics, never the daemon.

`aipager/config.py` loads ``aipager.yaml`` / ``policy.yaml`` at import
time. It used to let the loaders raise, which took down every command
importing the module — including `aipager doctor`, the command the
generic error handler tells users to run. Now the failure is recorded
in ``config.CONFIG_ERROR`` instead.

The dangerous half of that trade is covered here too: ``SCOPES = None``
reads as "legacy personal mode" to ``bot/auth._is_admin``, which returns
True for everyone. So the start path must stay closed on CONFIG_ERROR.
"""

from __future__ import annotations

import argparse
import importlib
import json

import pytest

import aipager.config as _config
import aipager.policy as _policy
import aipager.scope as _scope


@pytest.fixture
def reloaded_config():
    """Reload ``aipager.config``, restoring its attributes afterwards.

    Teardown restores the saved values rather than reloading again: a
    second reload would re-run the load with the autouse home-isolation
    patches still active, leaving ``SCOPES = None`` in ``sys.modules``
    for every later test in the session.
    """
    saved = dict(vars(_config))

    def _reload():
        return importlib.reload(_config)

    yield _reload

    for name in set(vars(_config)) - set(saved):
        delattr(_config, name)
    for name, value in saved.items():
        setattr(_config, name, value)


def test_malformed_scopes_records_error_instead_of_raising(
    reloaded_config, monkeypatch
):
    def _boom(*a, **kw):
        raise _scope.ScopeConfigError("aipager.yaml: `scopes` must be a list")

    monkeypatch.setattr(_scope, "load_scopes", _boom)
    cfg = reloaded_config()

    assert cfg.CONFIG_ERROR == "aipager.yaml: `scopes` must be a list"
    assert cfg.SCOPES is None


def test_malformed_policy_falls_back_to_builtin_floor(
    reloaded_config, monkeypatch
):
    real_load = _policy.load_policy

    def _boom_on_default(policy_path=_policy.POLICY_PATH,
                         policy_d=_policy.POLICY_D_DIR):
        if policy_path == _policy.POLICY_PATH:
            raise _policy.PolicyError("policy.yaml: bad role")
        return real_load(policy_path, policy_d)

    monkeypatch.setattr(_policy, "load_policy", _boom_on_default)
    cfg = reloaded_config()

    assert cfg.CONFIG_ERROR == "policy.yaml: bad role"
    # Degraded to the built-in safety floor, not to "no policy at all".
    assert cfg.POLICY is not None
    assert cfg.POLICY.safety_deny_paths_no_access


def test_clean_config_records_no_error(reloaded_config):
    assert reloaded_config().CONFIG_ERROR is None


def test_preflight_refuses_to_start_on_malformed_config(monkeypatch, capsys):
    """Fail-closed: a parse error must never reach the auth layer."""
    from aipager import preflight

    monkeypatch.setattr(_config, "CONFIG_ERROR", "aipager.yaml: broken")

    with pytest.raises(SystemExit) as exc:
        preflight.require_config()

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "malformed" in err
    assert "aipager.yaml: broken" in err


def test_preflight_allows_start_when_config_is_clean(monkeypatch):
    from aipager import preflight

    monkeypatch.setattr(_config, "CONFIG_ERROR", None)
    monkeypatch.setattr(_config, "BOT_TOKEN", "tok")
    monkeypatch.setattr(_config, "SCOPES", ["something"])

    preflight.require_config()


def test_doctor_reports_malformed_config_as_failed_check(monkeypatch):
    from aipager import doctor

    monkeypatch.setattr(_config, "CONFIG_ERROR", "aipager.yaml: broken")
    result = doctor.check_config_parses()

    assert result.status == doctor.FAIL
    assert "aipager.yaml: broken" in result.detail
    assert result.fix


def test_doctor_config_check_passes_when_clean(monkeypatch):
    from aipager import doctor

    monkeypatch.setattr(_config, "CONFIG_ERROR", None)
    assert doctor.check_config_parses().status == doctor.OK


def test_doctor_runs_config_parse_check_first():
    """It gates interpretation of every later check, so ordering matters."""
    from aipager import doctor

    assert doctor.CHECKS[0] is doctor.check_config_parses


def test_status_warns_about_malformed_config_but_still_runs(monkeypatch, capsys):
    from aipager import status

    monkeypatch.setattr(_config, "CONFIG_ERROR", "aipager.yaml: broken")
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "123")
    monkeypatch.setattr(status, "_daemon_alive", lambda: False)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))

    assert status.cmd_status() == 1
    out = capsys.readouterr()
    assert "aipager.yaml: broken" in out.out + out.err


def test_status_names_malformed_config_instead_of_not_configured(
    monkeypatch, capsys
):
    """Without a token, the honest reason is "malformed", not "unconfigured"."""
    from aipager import status

    monkeypatch.setattr(_config, "CONFIG_ERROR", "aipager.yaml: broken")
    monkeypatch.setattr(status, "BOT_TOKEN", "")
    monkeypatch.setattr(status, "CHAT_ID", "")

    assert status.cmd_status() == 2
    err = capsys.readouterr().err
    assert "malformed" in err
    assert "aipager.yaml: broken" in err
    assert "isn't configured yet" not in err


def test_status_json_reports_config_error(monkeypatch, capsys):
    from aipager import status

    monkeypatch.setattr(_config, "CONFIG_ERROR", "aipager.yaml: broken")
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "123")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))

    status.cmd_status(argparse.Namespace(as_json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["config_error"] == "aipager.yaml: broken"


def test_status_json_config_error_is_null_when_clean(monkeypatch, capsys):
    from aipager import status

    monkeypatch.setattr(_config, "CONFIG_ERROR", None)
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "123")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))

    status.cmd_status(argparse.Namespace(as_json=True))

    assert json.loads(capsys.readouterr().out)["config_error"] is None


def test_malformed_team_config_still_raises(tmp_path):
    """`TEAM` is deliberately NOT softened.

    ``bot/core.py`` reads ``config.TEAM`` rather than reloading it, so a
    silent ``None`` would start the daemon in personal mode — where
    every sender is admin. Unlike scopes/policy there is no second load
    to catch it, so this must keep raising.
    """
    from aipager.team import TeamConfigError, load_team

    bad = tmp_path / "team.yaml"
    bad.write_text("mode: team\ngroup_id: -100\nusers: not-a-list\n")

    with pytest.raises(TeamConfigError):
        load_team(bad)
