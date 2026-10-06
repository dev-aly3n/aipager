"""Iteration 2: ``aipager doctor --json`` never contains a token shape
(``\\d{6,12}:[A-Za-z0-9_-]{20,80}``, entrypoints.md "Token scan"), even
when a check's title, detail, fix or crash text carries one that is not
the configured token."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_setup_support import OTHER_TOKEN, TOKEN

SHAPE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,80}")
ORDER = ["config_parses", "config", "token_valid", "chat_reachable", "team",
         "role_shell_access", "claude", "claude_auth", "dtach",
         "hook_scripts", "settings_json", "daemon", "service_installed",
         "service_unit_path", "miniapp"]


@pytest.fixture
def doc_env(env, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: env.home)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", TOKEN)
    return env


def _no_shape(text: str, where: str) -> None:
    if SHAPE.search(text):
        pytest.fail(f"a token shape reached {where}", pytrace=False)


def _checks(monkeypatch, where: str, *, crash: bool = False):
    from aipager import doctor
    new = []
    for key in ORDER:
        if key == "daemon" and crash:
            def fn():
                raise RuntimeError(f"connect failed for {OTHER_TOKEN}")
        elif key == "daemon":
            def fn(where=where):
                title = f"t {OTHER_TOKEN}" if where == "title" else "t"
                detail = ([f"saw {OTHER_TOKEN} here"]
                          if where == "detail" else ["d"])
                fix = f"run x {OTHER_TOKEN}" if where == "fix" else None
                return doctor.CheckResult(doctor.WARN, title, detail, fix)
        else:
            def fn():
                return doctor.CheckResult(doctor.OK, "t", ["d"], None)
        fn.__name__ = f"check_{key}"
        new.append(fn)
    monkeypatch.setattr(doctor, "CHECKS", new)


@pytest.mark.parametrize("where", ["title", "detail", "fix"])
def test_doctor_json_stdout_has_no_token_shape(doc_env, monkeypatch, where):
    _checks(monkeypatch, where)
    _no_shape(doc_env.run("doctor", "--json").out, f"doctor --json {where}")


@pytest.mark.parametrize("where", ["title", "detail", "fix"])
def test_doctor_json_stderr_has_no_token_shape(doc_env, monkeypatch, where):
    _checks(monkeypatch, where)
    _no_shape(doc_env.run("doctor", "--json").err, f"stderr ({where})")


def test_doctor_json_crash_text_has_no_token_shape(doc_env, monkeypatch):
    _checks(monkeypatch, "", crash=True)
    _no_shape(doc_env.run("doctor", "--json").out, "the crash detail")


@pytest.mark.parametrize("where", ["title", "detail", "fix"])
def test_doctor_json_scrubbed_output_still_parses(doc_env, monkeypatch,
                                                 where):
    _checks(monkeypatch, where)
    r = doc_env.run("doctor", "--json")
    _no_shape(r.out, "doctor --json")
    assert [c["key"] for c in r.json["checks"]] == ORDER


def test_doctor_json_real_checks_have_no_token_shape(doc_env):
    """The real checks with a configured token: nothing token-shaped."""
    _no_shape(doc_env.run("doctor", "--json").out, "real doctor --json")


def test_doctor_json_usage_error_has_no_token_shape(doc_env):
    r = doc_env.run("doctor", "--json", "--fix")
    _no_shape(r.out + r.err, "doctor usage error")
