"""Success criteria 24-26: old state (skip_perms), old preferences
(new_session_mode ""/ask/auto) and aipager.yaml's default_mode keep
loading, migrate to the three modes, and are written back narrow-safe."""

from __future__ import annotations

import argparse
import inspect
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import perm_modes, preferences, scope
from aipager.state import SessionRegistry, Status

from _tm3 import ADA, GROUP, group_scope, scoped_bot, settle_tasks, typed

NAME = "claude-jim"


# ---- the state file ----------------------------------------------------------

def _saved_record(tmp_state_file, mode="ask"):
    reg = SessionRegistry()
    s = reg.get_or_create(NAME)
    s.label = "jim"
    s.perm_mode = mode
    s.claude_session_id = "UUID-1"
    s.cwd = "/x"
    s.status = Status.IDLE
    reg.save()
    return json.loads(tmp_state_file.read_text())


def _reload_with(tmp_state_file, **record_changes):
    data = _saved_record(tmp_state_file)
    rec = data["sessions"][NAME]
    for k, v in record_changes.items():
        if v is _DROP:
            rec.pop(k, None)
        else:
            rec[k] = v
    tmp_state_file.write_text(json.dumps(data))
    reg = SessionRegistry()
    reg.load()
    return reg.get(NAME)


_DROP = object()

LEGACY_STATE = [
    ({"perm_mode": _DROP, "skip_perms": True}, "full"),
    ({"perm_mode": _DROP, "skip_perms": False}, "ask"),
    ({"perm_mode": _DROP, "skip_perms": _DROP}, "ask"),
    ({"perm_mode": _DROP, "skip_perms": "yes"}, "ask"),
    ({"perm_mode": "auto", "skip_perms": True}, "auto"),
    ({"perm_mode": "ask", "skip_perms": True}, "ask"),
    ({"perm_mode": "full", "skip_perms": False}, "full"),
    ({"perm_mode": "bogus", "skip_perms": True}, "full"),
    ({"perm_mode": "bogus", "skip_perms": False}, "ask"),
    ({"perm_mode": True, "skip_perms": _DROP}, "ask"),
]


@pytest.mark.parametrize("changes,expected", LEGACY_STATE, ids=[
    "legacy-true", "legacy-false", "legacy-missing", "legacy-truthy-string",
    "new-auto-wins", "new-ask-wins", "new-full-wins", "invalid-new-legacy-true",
    "invalid-new-legacy-false", "bool-new-ignored"])
def test_state_file_loads_into_a_mode(tmp_state_file, changes, expected):
    sess = _reload_with(tmp_state_file, **changes)
    assert sess.perm_mode == expected


@pytest.mark.parametrize("record,expected", [
    ({"skip_perms": True}, "full"), ({"skip_perms": False}, "ask"), ({}, "ask"),
    ({"perm_mode": "auto", "skip_perms": True}, "auto"),
    ({"perm_mode": "nope", "skip_perms": True}, "full"),
], ids=["legacy-true", "legacy-false", "empty", "new-wins", "invalid-new"])
def test_mode_from_saved(record, expected):
    assert perm_modes.mode_from_saved(record) == expected


@pytest.mark.parametrize("mode,mirror", [("ask", False), ("auto", False), ("full", True)],
                         ids=["ask", "auto", "full"])
def test_save_writes_the_legacy_mirror(tmp_state_file, mode, mirror):
    rec = _saved_record(tmp_state_file, mode)["sessions"][NAME]
    assert rec.get("skip_perms") is mirror


@pytest.mark.parametrize("mode", ["ask", "auto", "full"], ids=["ask", "auto", "full"])
def test_save_writes_perm_mode(tmp_state_file, mode):
    rec = _saved_record(tmp_state_file, mode)["sessions"][NAME]
    assert rec.get("perm_mode") == mode


@pytest.mark.parametrize("mode", ["ask", "auto", "full"], ids=["ask", "auto", "full"])
def test_save_and_load_round_trip(tmp_state_file, mode):
    _saved_record(tmp_state_file, mode)
    reg = SessionRegistry()
    reg.load()
    assert reg.get(NAME).perm_mode == mode


def test_tracked_session_default_is_ask():
    from aipager.state import TrackedSession
    assert TrackedSession(name="claude-x", label="x").perm_mode == "ask"


# ---- aipager resume (the CLI) --------------------------------------------------

def _cli_resume(monkeypatch, tmp_state_file, capsys, record, name="jim"):
    data = {"sessions": {NAME: {"name": NAME, "label": "jim", "claude_session_id": "UUID-1",
                                "cwd": "/x", "gone_at": time.time() - 60, **record}}}
    tmp_state_file.write_text(json.dumps(data))
    monkeypatch.setattr("pathlib.Path.is_socket", lambda self: False)
    monkeypatch.setattr("aipager.status._live_sessions", lambda: set())
    launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launch)
    from aipager.cli import resume as cli_resume
    rc = cli_resume._cmd_resume(argparse.Namespace(name=name))
    out = capsys.readouterr()
    mode = launch.await_args.kwargs.get("mode") if launch.await_args else None
    return rc, mode, out.out + out.err


CLI_CASES = [
    ({"skip_perms": True}, "jim", "full", "mode=Full access (--dangerously-skip-permissions)"),
    ({"skip_perms": False}, "jim", "ask", "mode=Ask (--permission-mode default)"),
    ({}, "jim", "ask", "mode=Ask (--permission-mode default)"),
    ({"perm_mode": "auto", "skip_perms": False}, "jim", "auto",
     "mode=Auto (--permission-mode auto)"),
    ({"perm_mode": "ask", "skip_perms": True}, "jim", "ask",
     "mode=Ask (--permission-mode default)"),
    ({"perm_mode": "auto"}, "!jim", "full", "mode=Full access (--dangerously-skip-permissions)"),
]
CLI_IDS = ["legacy-true", "legacy-false", "legacy-missing", "new-auto", "new-wins-over-legacy",
           "bang-forces-full"]


@pytest.mark.parametrize("record,name,mode,_line", CLI_CASES, ids=CLI_IDS)
def test_cli_resume_launches_in_the_saved_mode(monkeypatch, tmp_state_file, capsys, record,
                                               name, mode, _line):
    rc, launched, _out = _cli_resume(monkeypatch, tmp_state_file, capsys, record, name)
    assert (rc, launched) == (0, mode)


@pytest.mark.parametrize("record,name,_mode,line", CLI_CASES, ids=CLI_IDS)
def test_cli_resume_prints_the_mode(monkeypatch, tmp_state_file, capsys, record, name,
                                    _mode, line):
    _rc, _launched, out = _cli_resume(monkeypatch, tmp_state_file, capsys, record, name)
    flat = " ".join(out.split())   # the console soft-wraps long lines
    assert line in flat, out


# ---- preferences ----------------------------------------------------------------

CHAT = 4242


def _chat_record():
    raw = json.loads(preferences._PREFERENCES_PATH.read_text())
    hits = []

    def walk(x):
        if isinstance(x, dict):
            if "new_session_perm_mode" in x or "new_session_mode" in x:
                hits.append(x)
            for v in x.values():
                walk(v)
    walk(raw)
    assert len(hits) == 1, raw
    return raw, hits[0]


def _with_legacy(**fields):
    """Write a preferences file the way an older aipager left it, then read
    it as a restarted daemon would."""
    preferences.set_new_session_default(CHAT, "mode", "ask")
    raw, rec = _chat_record()
    rec.pop("new_session_perm_mode", None)
    rec.pop("new_session_mode", None)
    for k, v in fields.items():
        rec[k] = v
    preferences._PREFERENCES_PATH.write_text(json.dumps(raw))
    preferences._cache = None   # a restarted daemon reads the file afresh
    return preferences.get_new_session_defaults(CHAT).mode


@pytest.mark.parametrize("fields,expected", [
    ({"new_session_mode": "auto"}, "full"),
    ({"new_session_mode": "ask"}, "ask"),
    ({"new_session_mode": ""}, "auto"),
    ({}, "auto"),
    ({"new_session_mode": "nonsense"}, "auto"),
    ({"new_session_perm_mode": "ask", "new_session_mode": "auto"}, "ask"),
    ({"new_session_perm_mode": "auto", "new_session_mode": "ask"}, "auto"),
    ({"new_session_perm_mode": "full"}, "full"),
    ({"new_session_perm_mode": "bogus", "new_session_mode": "ask"}, "ask"),
], ids=["legacy-auto-is-full", "legacy-ask", "legacy-empty-is-auto", "missing-is-auto",
        "legacy-junk-is-auto", "new-ask-wins", "new-auto-wins", "new-full",
        "invalid-new-falls-back"])
def test_preferences_migration(fields, expected):
    assert _with_legacy(**fields) == expected


@pytest.mark.parametrize("raw,expected", [
    ({"new_session_mode": "auto"}, "full"), ({"new_session_mode": "ask"}, "ask"),
    ({"new_session_mode": ""}, "auto"), ({}, "auto"),
    ({"new_session_perm_mode": "ask", "new_session_mode": "auto"}, "ask"),
], ids=["legacy-auto", "legacy-ask", "legacy-empty", "missing", "new-wins"])
def test_mode_from_new_session_prefs(raw, expected):
    assert perm_modes.mode_from_new_session_prefs(raw) == expected


@pytest.mark.parametrize("value,legacy", [("ask", "ask"), ("auto", "ask"), ("full", None)],
                         ids=["ask", "auto", "full"])
def test_preferences_write_the_narrow_safe_legacy_mirror(value, legacy):
    preferences.set_new_session_default(CHAT, "mode", value)
    _raw, rec = _chat_record()
    assert rec.get("new_session_mode") == legacy


@pytest.mark.parametrize("value", ["ask", "auto", "full"], ids=["ask", "auto", "full"])
def test_preferences_write_the_new_key(value):
    preferences.set_new_session_default(CHAT, "mode", value)
    _raw, rec = _chat_record()
    assert rec.get("new_session_perm_mode") == value


def test_preferences_empty_writes_auto():
    preferences.set_new_session_default(CHAT, "mode", "")
    _raw, rec = _chat_record()
    assert rec.get("new_session_perm_mode") == "auto"


# ---- narrowest_allowed: the one fallback rule -----------------------------------

@pytest.mark.parametrize("mode,may_full,expected", [
    ("full", False, "auto"), ("full", True, "full"), ("auto", False, "auto"),
    ("ask", False, "ask"), ("ask", True, "ask"), ("auto", True, "auto"),
], ids=["full-refused", "full-allowed", "auto-member", "ask-member", "ask-admin",
        "auto-admin"])
def test_narrowest_allowed(mode, may_full, expected):
    assert perm_modes.narrowest_allowed(mode, may_full) == expected


def test_modes_tuple():
    assert perm_modes.MODES == ("ask", "auto", "full")


@pytest.mark.parametrize("raw,expected", [
    ("default", "ask"), ("manual", "ask"), ("auto", "auto"), ("bypassPermissions", "full"),
    ("acceptEdits", "other"), ("plan", "other"), ("dontAsk", "other"),
    ("ask", None), ("", None), (None, None), (1, None),
], ids=["default", "manual", "auto", "bypass", "acceptEdits", "plan", "dontAsk",
        "aipager-id-is-not-claudes", "empty", "none", "int"])
def test_from_claude(raw, expected):
    assert perm_modes.from_claude(raw) == expected


# ---- aipager.yaml default_mode ---------------------------------------------------

def _yaml_with_default_mode(tmp_path, value):
    path = tmp_path / "aipager.yaml"
    scope.dump_scopes([group_scope()], "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", path)
    path.write_text(path.read_text() + f"default_mode: {value}\n")
    return path


@pytest.mark.parametrize("value", ["auto", "ask", "full"], ids=["auto", "ask", "full"])
def test_yaml_with_default_mode_loads(tmp_path, value):
    path = _yaml_with_default_mode(tmp_path, value)
    loaded = scope.load_scopes(path)
    assert loaded is not None and loaded[0][0].chat_id == GROUP


def test_dump_scopes_has_no_default_mode_parameter():
    assert "default_mode" not in inspect.signature(scope.dump_scopes).parameters


def test_load_default_mode_is_gone():
    assert not hasattr(scope, "load_default_mode")


def test_rewrite_drops_default_mode(tmp_path):
    path = _yaml_with_default_mode(tmp_path, "auto")
    scopes, token = scope.load_scopes(path)
    scope.dump_scopes(scopes, token, path)
    assert "default_mode" not in path.read_text()


def test_yaml_default_mode_does_not_change_new_sessions(tmp_path, mk_bot, run_async, pty,
                                                        monkeypatch):
    """`default_mode: ask` (the old Ask) in aipager.yaml: nothing acts on it,
    a new session still starts in Auto."""
    path = _yaml_with_default_mode(tmp_path, "ask")
    monkeypatch.setattr(scope, "CONFIG_PATH", path)
    scopes, _tok = scope.load_scopes(path)
    bot = scoped_bot(mk_bot, SessionRegistry())
    bot.scopes = scopes
    u = typed("/new x1", chat_id=GROUP, user_id=ADA, chat_type="group")

    async def _go():
        await bot._handle_new_cmd(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    assert pty.launched_modes == ["auto"]
