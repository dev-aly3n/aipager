"""``aipager doctor`` and ``--json`` are unchanged by the removal: no new
check, no new key, no observer line, set or not.

design.md success criterion "aipager doctor and --json are unchanged";
entrypoints.md CLI commands. Run in-process through ``cmd_doctor``; only
the local config checks run for real (no network, no spawn), and the
root conftest keeps every path inside the test's tmp folder.
"""

from __future__ import annotations

import argparse
import json

import pytest

from aipager import doctor

#: The ``--json`` check keys before the removal (docs/commands.md).
KEYS_BEFORE = [
    "config_parses", "config", "token_valid", "chat_reachable", "team",
    "role_shell_access", "peer_messages", "claude", "claude_auth", "dtach",
    "hook_scripts", "settings_json", "daemon", "service_installed",
    "service_unit_path", "miniapp",
]

LOCAL_KEYS = ("config_parses", "config")


@pytest.fixture(autouse=True)
def _no_live_daemon_socket(tmp_path, monkeypatch):
    no_daemon = str(tmp_path / "no-daemon.sock")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", no_daemon)
    monkeypatch.setattr("aipager.status.SOCKET_PATH", no_daemon)


@pytest.fixture
def local_checks_only(monkeypatch):
    checks = [f for f in doctor.CHECKS if doctor._check_key(f) in LOCAL_KEYS]
    assert len(checks) == len(LOCAL_KEYS)
    monkeypatch.setattr(doctor, "CHECKS", checks)
    monkeypatch.setattr("aipager.config.SCOPES", None)


def _doctor(monkeypatch, capsys, value, *, as_json):
    if value is None:
        monkeypatch.delenv("OBSERVER_BOTS", raising=False)
    else:
        monkeypatch.setenv("OBSERVER_BOTS", value)
    rc = doctor.cmd_doctor(argparse.Namespace(as_json=as_json))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@pytest.mark.parametrize("value", ["123456:AAx:42", "garbage", ""],
                         ids=["token_shaped", "malformed", "empty"])
def test_the_check_keys_are_the_same_with_the_variable_set(monkeypatch, value):
    monkeypatch.setenv("OBSERVER_BOTS", value)
    assert [doctor._check_key(f) for f in doctor.CHECKS] == KEYS_BEFORE


@pytest.mark.parametrize("value", ["123456:AAx:42", "garbage"],
                         ids=["token_shaped", "malformed"])
def test_json_output_is_the_same_set_or_unset(
        monkeypatch, capsys, local_checks_only, value):
    unset = _doctor(monkeypatch, capsys, None, as_json=True)
    with_value = _doctor(monkeypatch, capsys, value, as_json=True)
    assert with_value == unset


def test_json_output_names_no_observer(monkeypatch, capsys, local_checks_only):
    _rc, out, _err = _doctor(monkeypatch, capsys, "123456:AAx:42", as_json=True)
    assert "observer" not in out.casefold()


def test_json_output_keys_are_the_local_checks(monkeypatch, capsys, local_checks_only):
    _rc, out, _err = _doctor(monkeypatch, capsys, "123456:AAx:42", as_json=True)
    assert [c["key"] for c in json.loads(out)["checks"]] == list(LOCAL_KEYS)


def test_plain_output_is_the_same_set_or_unset(monkeypatch, capsys, local_checks_only):
    unset = _doctor(monkeypatch, capsys, None, as_json=False)
    with_value = _doctor(monkeypatch, capsys, "123456:AAx:42", as_json=False)
    assert with_value == unset


def test_plain_output_never_carries_the_value(monkeypatch, capsys, local_checks_only):
    _rc, out, err = _doctor(monkeypatch, capsys, "123456:AAcanaryTOKEN:42",
                            as_json=False)
    if "AAcanaryTOKEN" in out + err:
        pytest.fail("doctor printed the OBSERVER_BOTS value", pytrace=False)
