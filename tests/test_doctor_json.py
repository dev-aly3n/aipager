"""`aipager doctor --json` (and the unchanged plain doctor)."""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from aipager import doctor

DOCUMENTED_KEYS = [
    "config_parses", "config", "token_valid", "chat_reachable", "team",
    "role_shell_access", "claude", "claude_auth", "dtach", "hook_scripts",
    "settings_json", "daemon", "service_installed", "service_unit_path",
    "miniapp",
]


def _stub_checks(monkeypatch, *, fail=True):
    def a():
        return doctor.CheckResult(doctor.OK, "alpha", detail=["one", "two"])

    def check_beta():
        return doctor.CheckResult(doctor.WARN, "beta", detail=["d [x]"],
                                  fix="do `beta`")

    def check_gamma():
        return doctor.CheckResult(doctor.FAIL if fail else doctor.OK,
                                  "gamma thing", fix="fix gamma")

    def check_boom():
        raise RuntimeError("bad [markup] here\nsecond")

    monkeypatch.setattr(doctor, "CHECKS", [a, check_beta, check_gamma, check_boom])
    monkeypatch.setattr("aipager.config.SCOPES", None)
    monkeypatch.setattr(doctor, "_unanchored_safety_paths", lambda p: [])
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")


def test_keys_of_the_real_checks_are_the_documented_ones():
    assert [doctor._check_key(f) for f in doctor.CHECKS] == DOCUMENTED_KEYS


def test_json_shape_keys_and_plain_text(monkeypatch, capsys):
    _stub_checks(monkeypatch)
    rc = doctor.cmd_doctor(argparse.Namespace(as_json=True))
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert rc == 1 and doc["ok"] is False
    assert set(doc) == {"command", "version", "ok", "summary", "checks"}
    assert doc["command"] == "doctor"
    assert doc["summary"] == {"ok": 1, "warn": 2, "fail": 1}
    assert [c["key"] for c in doc["checks"]] == ["a", "beta", "gamma", "boom"]
    boom = doc["checks"][3]
    assert boom["status"] == "warn" and boom["title"] == "boom"
    assert boom["detail"] == ["check crashed: RuntimeError: bad [markup] here"]
    assert doc["checks"][0]["fix"] is None
    for c in doc["checks"]:
        assert set(c) == {"key", "status", "title", "detail", "fix"}


def test_exit_0_without_failures(monkeypatch, capsys):
    _stub_checks(monkeypatch, fail=False)
    rc = doctor.cmd_doctor(argparse.Namespace(as_json=True))
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["ok"] is True


@pytest.mark.parametrize("flag", ["fix", "safety_check"])
def test_json_with_fix_or_safety_check_is_a_usage_error(monkeypatch, capsys, flag):
    ran = []
    monkeypatch.setattr(doctor, "run_all_keyed", lambda: ran.append(1) or [])
    monkeypatch.setattr(doctor, "cmd_doctor_fix", lambda: ran.append(2) or 0)
    monkeypatch.setattr(doctor, "_print_safety_policy", lambda: ran.append(3))
    rc = doctor.cmd_doctor(argparse.Namespace(as_json=True, **{flag: True}))
    doc = json.loads(capsys.readouterr().out)
    assert rc == 2 and doc["error"] == "usage" and doc["exit_code"] == 2
    assert ran == []


def test_json_stdout_quarantine(monkeypatch, capsys):
    def check_noisy():
        print("noise from a check")
        return doctor.CheckResult(doctor.OK, "noisy")

    monkeypatch.setattr(doctor, "CHECKS", [check_noisy])
    rc = doctor.cmd_doctor(argparse.Namespace(as_json=True))
    out = capsys.readouterr()
    assert rc == 0
    try:
        doc = json.loads(out.out)
    except ValueError:
        doc = None
    assert doc is not None, "stdout must be exactly one JSON object"
    assert doc["checks"][0]["key"] == "noisy"
    assert "noise from a check" in out.err


def test_plain_output_is_byte_identical_to_before(monkeypatch, capsys):
    """Golden captured from cmd_doctor before run_all became run_all_keyed."""
    from aipager import __version__
    _stub_checks(monkeypatch)
    rc = doctor.cmd_doctor(argparse.Namespace())
    out = capsys.readouterr()
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert rc == 1
    assert out.err == ""
    assert out.out == (
        f"aipager {__version__} on linux (python {py})\n\n"
        "  ✓  alpha          one · two\n"
        "  ⚠  beta           d \n"
        "  ✗  gamma thing  \n"
        "  ⚠  boom           check crashed: RuntimeError: bad [markup] here\n"
        "\nSuggested next steps\n"
        "  • beta: do `beta`\n"
        "  • gamma thing: fix gamma\n"
        "  • boom: Re-run `aipager doctor`; if it keeps crashing, report the "
        "line above.\n"
        "\n1 ok · 2 warn · 1 fail\n\n")


def test_run_all_matches_run_all_keyed(monkeypatch):
    _stub_checks(monkeypatch)
    keyed = doctor.run_all_keyed()
    plain = doctor.run_all()
    assert [r for _k, r in keyed] == plain


def test_cli_doctor_json(monkeypatch, capsys):
    _stub_checks(monkeypatch, fail=False)
    monkeypatch.setattr(sys, "argv", ["aipager", "doctor", "--json"])
    from aipager import cli
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    assert json.loads(capsys.readouterr().out)["command"] == "doctor"
