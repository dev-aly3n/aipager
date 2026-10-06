"""design.md success criterion 11 (second half): plain ``aipager doctor``
output is byte-identical to before. Oracle: a golden captured once from
the doctor module of the branch's base commit (dc5f9fe), fed these same
fake checks and environment. It is committed here so no test runs git
(or any subprocess) or depends on the base commit being in the checkout.
A deliberate change to the plain doctor output updates this golden."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _golden_stdout() -> str:
    """Captured from dc5f9fe's ``cmd_doctor`` with :func:`_fake_checks`."""
    from aipager import __version__
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    return (
        f"aipager {__version__} on linux (python {py})\n\n"
        "  \u2713  Config file                        parses\n"
        "  \u26a0  Telegram bot token    network: timed out \u00b7 second line\n"
        "  \u2717  dtach                            \n"
        "  \u26a0  crashes                            check crashed: "
        "RuntimeError: kaboom [x]\n"
        "\nSuggested next steps\n"
        "  \u2022 Telegram bot token: check your network\n"
        "  \u2022 dtach: uv tool install dtach-bin\n"
        "  \u2022 crashes: Re-run `aipager doctor`; if it keeps crashing, "
        "report the line \nabove.\n"
        "\n1 ok \u00b7 2 warn \u00b7 1 fail\n\n")


GOLDEN_EXIT = 1
GOLDEN_STDERR = ""


def _fake_checks(doctor):
    out = []
    specs = [
        ("config_parses", doctor.OK, "Config file", ["parses"], None),
        ("token_valid", doctor.WARN, "Telegram [bold]bot[/bold] token",
         ["network: timed out", "second line"], "check your network"),
        ("dtach", doctor.FAIL, "dtach", [], "uv tool install dtach-bin"),
    ]
    for key, st, title, detail, fix in specs:
        def fn(st=st, title=title, detail=detail, fix=fix):
            return doctor.CheckResult(st, title, list(detail), fix)
        fn.__name__ = f"check_{key}"
        out.append(fn)

    def check_crashes():
        raise RuntimeError("kaboom [x]")
    out.append(check_crashes)
    return out


def _plain(env, monkeypatch, mod, checks=None):
    import argparse
    if checks is not None:
        monkeypatch.setattr(mod, "CHECKS", checks)
    monkeypatch.setattr(mod, "_http_json",
                        lambda url, timeout=10.0: (
                            {"ok": True, "result": {"username": "example_bot"}},
                            ""))
    env.capsys.readouterr()
    ns = argparse.Namespace(fix=False, safety_check=False, as_json=False)
    try:
        code = mod.cmd_doctor(ns)
    except SystemExit as e:
        code = e.code
    out, err = env.capsys.readouterr()
    return code, out, err


@pytest.fixture
def doctor_now(env, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: env.home)
    from aipager import doctor, ui
    # The golden wraps at rich's non-terminal default width; pin it so a
    # COLUMNS in the environment cannot move the wrap.
    monkeypatch.setattr(ui.console, "width", 80)
    return doctor


def test_plain_doctor_stdout_identical_on_fake_checks(env, monkeypatch,
                                                      doctor_now):
    out = _plain(env, monkeypatch, doctor_now, _fake_checks(doctor_now))[1]
    assert out == _golden_stdout()


def test_plain_doctor_exit_code_identical_on_fake_checks(env, monkeypatch,
                                                         doctor_now):
    code = _plain(env, monkeypatch, doctor_now, _fake_checks(doctor_now))[0]
    assert code == GOLDEN_EXIT


def test_plain_doctor_stderr_identical_on_fake_checks(env, monkeypatch,
                                                      doctor_now):
    err = _plain(env, monkeypatch, doctor_now, _fake_checks(doctor_now))[2]
    assert err == GOLDEN_STDERR


def test_plain_doctor_real_checks_output_is_not_empty(env, monkeypatch,
                                                      doctor_now):
    assert len(_plain(env, monkeypatch, doctor_now)[1].strip().splitlines()) >= 10
