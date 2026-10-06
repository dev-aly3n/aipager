"""design.md success criterion 11 (second half): plain ``aipager doctor``
output is byte-identical to before. Oracle: the doctor module of the
branch's base commit (dc5f9fe), loaded in-process beside the current one
and fed the same checks and environment."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

BASE = "dc5f9fe"
REPO = Path(__file__).resolve().parents[3]


def _base_source() -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "show", f"{BASE}:aipager/doctor.py"],
            capture_output=True, text=True, timeout=20, check=True).stdout
    except Exception:
        return None


_SRC = _base_source()   # at collection, before the env fixture traps spawns
pytestmark = pytest.mark.skipif(_SRC is None,
                                reason="base commit not in this checkout")


def _oracle():
    name = "aipager._doctor_base_oracle"
    mod = types.ModuleType(name)
    mod.__package__ = "aipager"
    mod.__file__ = "<doctor@base>"
    sys.modules[name] = mod
    try:
        exec(compile(_SRC, mod.__file__, "exec"), mod.__dict__)
    finally:
        sys.modules.pop(name, None)
    return mod


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
def both(env, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: env.home)
    from aipager import doctor
    return doctor, _oracle()


def test_plain_doctor_stdout_identical_on_fake_checks(env, monkeypatch, both):
    now, base = both
    a = _plain(env, monkeypatch, now, _fake_checks(now))
    b = _plain(env, monkeypatch, base, _fake_checks(base))
    assert a[1] == b[1]


def test_plain_doctor_exit_code_identical_on_fake_checks(env, monkeypatch,
                                                         both):
    now, base = both
    a = _plain(env, monkeypatch, now, _fake_checks(now))
    b = _plain(env, monkeypatch, base, _fake_checks(base))
    assert a[0] == b[0]


def test_plain_doctor_stderr_identical_on_fake_checks(env, monkeypatch, both):
    now, base = both
    a = _plain(env, monkeypatch, now, _fake_checks(now))
    b = _plain(env, monkeypatch, base, _fake_checks(base))
    assert a[2] == b[2]


def test_plain_doctor_stdout_identical_on_real_checks(env, monkeypatch, both):
    now, base = both
    a = _plain(env, monkeypatch, now)
    b = _plain(env, monkeypatch, base)
    assert a[1] == b[1]


def test_plain_doctor_real_checks_output_is_not_empty(env, monkeypatch,
                                                      both):
    now, _base = both
    assert len(_plain(env, monkeypatch, now)[1].strip().splitlines()) >= 10
