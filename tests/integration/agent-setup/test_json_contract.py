"""design.md success criterion 9 and entrypoints.md 'JSON: aipager
setup': for every outcome stdout is exactly one JSON object holding
every documented key; exit_code and ok agree with the process; changed
is ordered; unchanged iff changed is empty."""

from __future__ import annotations

import json

import pytest

from agent_setup_support import (
    CHANGED_ORDER, CHAT, HTTP, NET, OTHER_TOKEN, SETUP_KEYS, TOKEN,
)


def _prep(env, case):
    """Return a callable producing the run for each outcome."""
    if case == "installed":
        return lambda: env.setup()
    if case == "installed_service":
        return lambda: env.setup("--service")
    if case == "unchanged":
        env.setup()
        return lambda: env.setup()
    if case == "updated":
        env.setup()
        return lambda: env.setup("--force", chat=777)
    if case == "dry_run":
        return lambda: env.setup("--dry-run")
    if case == "missing_token_source":
        return lambda: env.run("setup", "--chat-id", CHAT, "--json")
    if case == "token_source_conflict":
        tf = env.token_file()
        return lambda: env.run("setup", "--token-file", tf, "--token-stdin",
                               "--chat-id", CHAT, "--json", stdin=TOKEN)
    if case == "token_on_command_line":
        return lambda: env.run("setup", "--token", TOKEN, "--chat-id", CHAT,
                               "--json")
    if case == "token_file_unreadable":
        return lambda: env.setup(token_file=env.input_dir / "nope")
    if case == "token_stdin_is_tty":
        return lambda: env.run("setup", "--token-stdin", "--chat-id", CHAT,
                               "--json", stdin=TOKEN, tty=True)
    if case == "token_malformed":
        return lambda: env.setup(token_file=env.token_file("garbage"))
    if case == "bad_chat_id":
        return lambda: env.setup(chat="abc")
    if case == "bad_role":
        return lambda: env.setup("--role", "boss")
    if case == "usage_timeout":
        return lambda: env.setup("--timeout", "3")
    if case == "deps_missing":
        env.missing.add("dtach")
        return lambda: env.setup()
    if case == "token_rejected":
        env.tg.bots.clear()
        return lambda: env.setup()
    if case == "chat_not_started":
        env.tg.set("sendMessage", HTTP(400, "Bad Request: chat not found"))
        return lambda: env.setup()
    if case == "bot_blocked":
        env.tg.set("sendMessage",
                   HTTP(403, "Forbidden: bot was blocked by the user"))
        return lambda: env.setup()
    if case == "existing_install":
        env.setup()
        return lambda: env.setup(token_file=env.token_file(OTHER_TOKEN,
                                                           name="o"))
    if case == "telegram_unreachable":
        env.tg.set("getMe", NET())
        return lambda: env.setup()
    if case == "config_malformed":
        env.config_path.write_text("scopes: [[[\n")
        return lambda: env.setup()
    if case == "settings_invalid":
        env.settings_path.parent.mkdir(parents=True, exist_ok=True)
        env.settings_path.write_text("{nope")
        return lambda: env.setup()
    if case == "test_send_failed":
        env.tg.set("sendMessage", HTTP(400, "Bad Request: odd"))
        return lambda: env.setup()
    if case == "service_failed":
        env.service_rc = 3
        return lambda: env.setup("--service")
    if case == "internal_error":
        env.tg.set("sendMessage", RuntimeError("unexpected"))
        return lambda: env.setup()
    if case == "interrupted":
        env.tg.set("getMe", KeyboardInterrupt())
        return lambda: env.setup()
    raise AssertionError(case)


EXPECTED = {
    "installed": (0, "installed", None),
    "installed_service": (0, "installed", None),
    "unchanged": (0, "unchanged", None),
    "updated": (0, "updated", None),
    "dry_run": (0, "dry_run", None),
    "missing_token_source": (2, "error", "missing_token_source"),
    "token_source_conflict": (2, "error", "token_source_conflict"),
    "token_on_command_line": (2, "error", "token_on_command_line"),
    "token_file_unreadable": (2, "error", "token_file_unreadable"),
    "token_stdin_is_tty": (2, "error", "token_stdin_is_tty"),
    "token_malformed": (2, "error", "token_malformed"),
    "bad_chat_id": (2, "error", "bad_chat_id"),
    "bad_role": (2, "error", "bad_role"),
    "usage_timeout": (2, "error", "usage"),
    "deps_missing": (3, "error", "deps_missing"),
    "token_rejected": (4, "error", "token_rejected"),
    "chat_not_started": (5, "error", "chat_not_started"),
    "bot_blocked": (5, "error", "bot_blocked"),
    "existing_install": (6, "error", "existing_install"),
    "telegram_unreachable": (7, "error", "telegram_unreachable"),
    "config_malformed": (1, "error", "config_malformed"),
    "settings_invalid": (1, "error", "settings_invalid"),
    "test_send_failed": (1, "error", "test_send_failed"),
    "service_failed": (1, "error", "service_failed"),
    "internal_error": (1, "error", "internal_error"),
    "interrupted": (130, "error", "interrupted"),
}
CASES = list(EXPECTED)


@pytest.mark.parametrize("case", CASES)
def test_stdout_is_exactly_one_json_object(env, case):
    r = _prep(env, case)()
    assert isinstance(json.loads(r.out), dict)


@pytest.mark.parametrize("case", CASES)
def test_every_key_is_present(env, case):
    d = _prep(env, case)().json
    assert [k for k in SETUP_KEYS if k not in d] == []


@pytest.mark.parametrize("case", CASES)
def test_no_undocumented_keys(env, case):
    d = _prep(env, case)().json
    assert [k for k in d if k not in SETUP_KEYS] == []


@pytest.mark.parametrize("case", CASES)
def test_exit_status_and_error(env, case):
    r = _prep(env, case)()
    assert (r.code, r.json["status"], r.json["error"]) == EXPECTED[case]


@pytest.mark.parametrize("case", CASES)
def test_exit_code_field_matches_process(env, case):
    r = _prep(env, case)()
    assert r.json["exit_code"] == r.code


@pytest.mark.parametrize("case", CASES)
def test_ok_field_matches_exit_zero(env, case):
    r = _prep(env, case)()
    assert r.json["ok"] is (r.code == 0)


@pytest.mark.parametrize("case", CASES)
def test_command_is_setup(env, case):
    assert _prep(env, case)().json["command"] == "setup"


@pytest.mark.parametrize("case", CASES)
def test_changed_is_ordered_subset(env, case):
    ch = _prep(env, case)().json["changed"]
    assert ch == [c for c in CHANGED_ORDER if c in ch] and len(ch) == len(
        set(ch))


@pytest.mark.parametrize("case", [c for c in CASES
                                  if EXPECTED[c][1] not in ("error",
                                                            "dry_run")])
def test_unchanged_iff_changed_empty(env, case):
    d = _prep(env, case)().json
    assert (d["status"] == "unchanged") is (d["changed"] == [])


@pytest.mark.parametrize("case", [c for c in CASES
                                  if EXPECTED[c][1] == "error"])
def test_errors_carry_message_and_fix(env, case):
    d = _prep(env, case)().json
    assert isinstance(d["message"], str) and d["message"] and isinstance(
        d["fix"], str) and d["fix"]


@pytest.mark.parametrize("case", CASES)
def test_next_step_is_a_sentence(env, case):
    assert isinstance(_prep(env, case)().json["next_step"], str)


@pytest.mark.parametrize("case", CASES)
def test_nested_shapes(env, case):
    d = _prep(env, case)().json
    shapes = []
    if d["deps"] is not None:
        shapes.append(sorted(d["deps"]) == ["items", "ok"])
        shapes.extend(sorted(i) == ["fix", "found", "name", "path"]
                      for i in d["deps"]["items"])
    if d["settings_json"] is not None:
        shapes.append(sorted(d["settings_json"]) == [
            "backup", "path", "repointed", "status"])
    if d["daemon"] is not None:
        shapes.append(sorted(d["daemon"]) == ["reload", "restart_needed",
                                              "running"])
    if d["service"] is not None:
        shapes.append(sorted(d["service"]) == ["requested", "result"])
    shapes.append(isinstance(d["warnings"], list) and all(
        sorted(w) == ["code", "message"] for w in d["warnings"]))
    assert all(shapes)


@pytest.mark.parametrize("case", CASES)
def test_enum_values(env, case):
    d = _prep(env, case)().json
    bad = []
    if d["test_message"] not in ("sent", "not_needed", "skipped_dry_run",
                                 "failed", "not_attempted"):
        bad.append(("test_message", d["test_message"]))
    if d["settings_json"] and d["settings_json"]["status"] not in (
            "created", "patched", "unchanged", "would_change", "not_checked"):
        bad.append(("settings_json.status", d["settings_json"]["status"]))
    if d["daemon"] and d["daemon"]["reload"] not in (
            "reloaded", "refused", "not_reloaded", "not_needed"):
        bad.append(("daemon.reload", d["daemon"]["reload"]))
    if d["service"] and d["service"]["result"] not in (
            None, "not_requested", "installed", "already_installed",
            "skipped_daemon_running", "would_install", "failed"):
        bad.append(("service.result", d["service"]["result"]))
    assert bad == []


@pytest.mark.parametrize("case", CASES)
def test_dry_run_field(env, case):
    assert _prep(env, case)().json["dry_run"] is (case == "dry_run")


@pytest.mark.parametrize("case", ["installed", "unchanged", "updated",
                                  "dry_run", "chat_not_started"])
def test_bot_username_known_after_getme(env, case):
    assert _prep(env, case)().json["bot_username"] == "example_bot"


def test_shared_code_print_goes_to_stderr(env, monkeypatch):
    """Error guessing: anything the shared wizard cores print must not
    break the one-object stdout."""
    import aipager.claude_resolve as cr

    def _noisy(*a, **k):
        print("resolver says hello")
        from types import SimpleNamespace
        return SimpleNamespace(chosen=SimpleNamespace(
            path=str(env.bin_dir / "claude")))
    monkeypatch.setattr(cr, "try_resolve_claude_binary", _noisy)
    r = env.setup()
    assert isinstance(json.loads(r.out), dict) and "hello" in r.err


def test_plain_mode_prints_no_json(env):
    r = env.setup(json_out=False)
    assert not r.out.lstrip().startswith("{")
