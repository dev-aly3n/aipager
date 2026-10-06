"""design.md success criterion 11: ``aipager doctor --json`` emits the
documented shape, keys from the check names in the documented order,
exit 1 iff any check fails; --json with --fix / --safety-check exits 2
with an error object."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ORDER = ["config_parses", "config", "token_valid", "chat_reachable", "team",
         "role_shell_access", "claude", "claude_auth", "dtach",
         "hook_scripts", "settings_json", "daemon", "service_installed",
         "service_unit_path", "miniapp"]
TOP = ["command", "version", "ok", "summary", "checks"]
MARKUP = re.compile(r"\[/?(bold|dim|red|green|yellow|cyan|magenta|blue|"
                    r"italic|b|i|u|link|white)( [^\]]*)?\]")


@pytest.fixture
def doc_env(env, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: env.home)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "x")
    return env


def _doctor(env, *extra):
    return env.run("doctor", "--json", *extra)


def test_doctor_json_stdout_is_one_object(doc_env):
    assert isinstance(json.loads(_doctor(doc_env).out), dict)


def test_doctor_json_top_keys(doc_env):
    assert list(_doctor(doc_env).json) == TOP


def test_doctor_json_command(doc_env):
    assert _doctor(doc_env).json["command"] == "doctor"


def test_doctor_json_version(doc_env):
    import aipager
    assert _doctor(doc_env).json["version"] == aipager.__version__


def test_doctor_json_check_keys_in_order(doc_env):
    assert [c["key"] for c in _doctor(doc_env).json["checks"]] == ORDER


def test_doctor_json_check_fields(doc_env):
    shapes = {tuple(c) for c in _doctor(doc_env).json["checks"]}
    assert shapes == {("key", "status", "title", "detail", "fix")}


def test_doctor_json_status_values(doc_env):
    assert {c["status"] for c in _doctor(doc_env).json["checks"]} <= {
        "ok", "warn", "fail"}


def test_doctor_json_detail_is_list_of_str(doc_env):
    assert all(isinstance(c["detail"], list) and all(
        isinstance(x, str) for x in c["detail"])
        for c in _doctor(doc_env).json["checks"])


def test_doctor_json_fix_is_str_or_null(doc_env):
    assert all(c["fix"] is None or isinstance(c["fix"], str)
               for c in _doctor(doc_env).json["checks"])


def test_doctor_json_summary_counts(doc_env):
    d = _doctor(doc_env).json
    counts = {s: sum(c["status"] == s for c in d["checks"])
              for s in ("ok", "warn", "fail")}
    assert d["summary"] == counts


def test_doctor_json_ok_iff_no_fail(doc_env):
    d = _doctor(doc_env).json
    assert d["ok"] is (d["summary"]["fail"] == 0)


def test_doctor_json_exit_code_follows_fail(doc_env):
    r = _doctor(doc_env)
    assert r.code == (1 if r.json["summary"]["fail"] else 0)


def test_doctor_json_texts_have_no_markup(doc_env):
    d = _doctor(doc_env).json
    texts = [c["title"] for c in d["checks"]] + [
        x for c in d["checks"] for x in c["detail"]] + [
        c["fix"] for c in d["checks"] if c["fix"]]
    assert [t for t in texts if MARKUP.search(t)] == []


def test_doctor_json_sends_no_message(doc_env):
    _doctor(doc_env)
    assert [c for c in doc_env.tg.calls if c.method == "sendMessage"] == []


def test_doctor_json_never_prompts(doc_env):
    _doctor(doc_env)
    assert (doc_env.traps, doc_env.interactive_calls) == ([], [])


def test_doctor_json_writes_nothing(doc_env):
    before = doc_env.snapshot()
    _doctor(doc_env)
    assert doc_env.snapshot() == before


def test_doctor_json_after_setup_has_token_ok(doc_env):
    """An agent's verify step after a setup: aipager.config re-reads are
    out of scope; this only checks the token check row exists and is not
    crashed into a warn by the JSON path."""
    d = _doctor(doc_env).json
    row = [c for c in d["checks"] if c["key"] == "token_valid"][0]
    assert row["status"] == "ok"


def test_doctor_json_leaks_no_token(doc_env, monkeypatch):
    from agent_setup_support import TOKEN, assert_no_secret
    monkeypatch.setattr("aipager.config.BOT_TOKEN", TOKEN)
    r = _doctor(doc_env)
    assert_no_secret(r.out + r.err + r.log, "doctor --json output")


def _patch_checks(monkeypatch, status_by_key, crash=()):
    from aipager import doctor
    new = []
    for key in ORDER:
        st = status_by_key.get(key, doctor.OK)

        if key in crash:
            def fn():
                raise RuntimeError("[bold]boom[/bold] crashed")
        else:
            def fn(st=st, key=key):
                return doctor.CheckResult(st, f"title {key}", ["d"], None)
        fn.__name__ = f"check_{key}"
        new.append(fn)
    monkeypatch.setattr(doctor, "CHECKS", new)


def test_doctor_json_all_ok_exits_zero(doc_env, monkeypatch):
    _patch_checks(monkeypatch, {})
    r = _doctor(doc_env)
    assert (r.code, r.json["ok"]) == (0, True)


def test_doctor_json_warn_only_exits_zero(doc_env, monkeypatch):
    from aipager import doctor
    _patch_checks(monkeypatch, {"miniapp": doctor.WARN})
    assert _doctor(doc_env).code == 0


def test_doctor_json_one_fail_exits_one(doc_env, monkeypatch):
    from aipager import doctor
    _patch_checks(monkeypatch, {"dtach": doctor.FAIL})
    r = _doctor(doc_env)
    assert (r.code, r.json["ok"]) == (1, False)


def test_doctor_json_crashing_check_keeps_key_as_warn(doc_env, monkeypatch):
    _patch_checks(monkeypatch, {}, crash=("daemon",))
    row = [c for c in _doctor(doc_env).json["checks"]
           if c["key"] == "daemon"]
    assert [r["status"] for r in row] == ["warn"]


def test_doctor_json_crash_detail_is_unescaped_plain_text(doc_env,
                                                         monkeypatch):
    """The crash text is shown as the exception said it: no rich escape
    backslashes (``\\[``) leak into the JSON string."""
    _patch_checks(monkeypatch, {}, crash=("daemon",))
    row = [c for c in _doctor(doc_env).json["checks"]
           if c["key"] == "daemon"][0]
    text = " ".join(row["detail"])
    assert "boom" in text and "\\[" not in text


def test_doctor_json_shared_print_goes_to_stderr(doc_env, monkeypatch):
    from aipager import doctor
    _patch_checks(monkeypatch, {})

    def check_config():
        print("noisy line from a check")
        return doctor.CheckResult(doctor.OK, "t", [], None)
    checks = list(doctor.CHECKS)
    checks[1] = check_config
    monkeypatch.setattr(doctor, "CHECKS", checks)
    r = _doctor(doc_env)
    assert isinstance(json.loads(r.out), dict) and "noisy" in r.err


@pytest.mark.parametrize("flag", ["--fix", "--safety-check"])
def test_doctor_json_with_incompatible_flag_exits_2(doc_env, flag):
    assert _doctor(doc_env, flag).code == 2


@pytest.mark.parametrize("flag", ["--fix", "--safety-check"])
def test_doctor_json_with_incompatible_flag_error_object(doc_env, flag):
    d = _doctor(doc_env, flag).json
    assert {k: d[k] for k in ("command", "status", "ok", "exit_code",
                              "error")} == {
        "command": "doctor", "status": "error", "ok": False,
        "exit_code": 2, "error": "usage"}


@pytest.mark.parametrize("flag", ["--fix", "--safety-check"])
def test_doctor_json_with_incompatible_flag_has_message_and_fix(doc_env,
                                                                flag):
    d = _doctor(doc_env, flag).json
    assert d["message"] and d["fix"]


def test_plain_doctor_is_not_json(doc_env):
    r = doc_env.run("doctor")
    try:
        json.loads(r.out)
        is_json = True
    except ValueError:
        is_json = False
    assert is_json is False and r.out.strip()


def test_plain_doctor_shows_the_same_titles(doc_env, monkeypatch):
    _patch_checks(monkeypatch, {})
    r = doc_env.run("doctor")
    assert all(f"title {k}" in r.out for k in ORDER)
