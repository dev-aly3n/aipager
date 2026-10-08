"""Roadmap 8.119: `aipager uninstall` takes its entries out of Claude Code's
settings.json.

`aipager config` (and the daemon at start) add a hook running
`aipager-hook` to every hook event and, when the user had no working one,
a statusLine running `aipager-statusline`. Uninstall used to leave them, so
every event of every Claude Code session then ran a program that was gone
(measured: exit 127 and one non-blocking hook error per event). Now it
removes exactly those entries, found the way the wizard finds them, and
leaves the user's own hooks, statusLine and every other key as they were.
A file it cannot safely rewrite is left exactly as it is, with what to
remove by hand.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time

import pytest

from aipager import claude_bootstrap, updater
from aipager.wizard import settings_patch
from aipager.wizard._constants import HOOK_EVENTS

HOOK = "/opt/aipager/bin/aipager-hook"
OURS_ONLY = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": HOOK}]}]}}

USER = {
    "model": "opus",
    "env": {"FOO": "1"},
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "/usr/local/bin/my-guard"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}],
    },
}


def _path():
    return settings_patch.CLAUDE_SETTINGS


def _write(obj) -> str:
    _path().parent.mkdir(parents=True, exist_ok=True)
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2) + "\n"
    _path().write_text(text)
    return text


def _read() -> dict:
    return json.loads(_path().read_text())


def _backups() -> list:
    return sorted(_path().parent.glob(f"{_path().name}.bak.*"))


@pytest.fixture
def at_opt(monkeypatch):
    """aipager installed in /opt/aipager/bin, for the wizard and the daemon."""
    monkeypatch.setattr(settings_patch, "_resolve", lambda cmd: f"/opt/aipager/bin/{cmd}")
    monkeypatch.setattr(claude_bootstrap, "_resolve", lambda cmd: f"/opt/aipager/bin/{cmd}")


# ---- exactly what aipager added comes out ------------------------------------------------

@pytest.mark.parametrize("user", ["their_hooks", "nothing", "their_status_line"])
def test_uninstall_takes_out_exactly_what_the_wizard_put_in(at_opt, user):
    before = {"their_hooks": copy.deepcopy(USER), "nothing": {"model": "opus"},
              "their_status_line": {**copy.deepcopy(USER),
                                    "statusLine": {"type": "command", "command": "/bin/sh"}},
              }[user]
    _write(before)
    settings_patch.apply_settings(settings_patch.plan_settings())
    patched = _read()
    assert all(any(h["command"] == HOOK for b in patched["hooks"][e] for h in b["hooks"])
               for e in HOOK_EVENTS)
    plan = settings_patch.plan_unpatch()
    assert plan.hooks == len(HOOK_EVENTS)
    assert plan.status_line is (user != "their_status_line")   # theirs was kept
    settings_patch.apply_unpatch(plan)
    assert _read() == before


def test_uninstall_takes_out_what_the_daemon_put_in(at_opt):
    _write(copy.deepcopy(USER))
    assert claude_bootstrap._ensure_hooks_and_statusline() is True
    settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    assert _read() == USER


def test_empty_entries_the_user_had_stay():
    # Only what removing aipager's entries left empty goes.
    _write({"hooks": {"Notification": [], "Stop": [{"hooks": [
        {"type": "command", "command": HOOK}]}]},
        "statusLine": {"type": "command", "command": "/opt/aipager/bin/aipager-statusline"}})
    settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    assert _read() == {"hooks": {"Notification": []}}
    _write({"hooks": {}, "statusLine": {
        "type": "command", "command": "/opt/aipager/bin/aipager-statusline"}})
    settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    assert _read() == {"hooks": {}}


def test_a_block_shared_with_the_users_own_hook_keeps_theirs():
    _write({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": HOOK},
        {"type": "command", "command": "/usr/local/bin/my-guard"}]}]}})
    settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    assert _read() == {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": "/usr/local/bin/my-guard"}]}]}}


@pytest.mark.parametrize("hook, status_line", [
    ("/usr/local/bin/my-aipager-hook", "/usr/local/bin/my-aipager-statusline"),
    ("/bin/echo aipager-hook", "/bin/echo aipager-statusline"),
    ("aipager-statusline", "aipager-hook"),        # each in the other's place
])
def test_a_look_alike_is_the_users_own(hook, status_line):
    text = _write({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": hook}]}]},
                   "statusLine": {"type": "command", "command": status_line}})
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None and plan.problem is None
    assert _path().read_text() == text


def test_a_file_without_aipagers_entries_is_not_written():
    text = _write(copy.deepcopy(USER))
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None and plan.problem is None
    assert settings_patch.apply_unpatch(plan) is None
    assert _path().read_text() == text and _backups() == []


def test_no_file_is_nothing_to_do():
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None and plan.problem is None and not _path().exists()


# ---- a file it cannot safely rewrite -------------------------------------------------------

@pytest.mark.parametrize("case, problem", [
    ("link", "it is a link to another file"),
    ("not_json", "it is not valid JSON"),
    ("hooks_not_a_dict", "it is not in the shape Claude Code reads"),
    ("not_an_object", "it is not in the shape Claude Code reads"),
])
def test_a_file_it_cannot_safely_rewrite_is_left_as_it_is(tmp_path, case, problem):
    ours = json.dumps(OURS_ONLY)
    if case == "link":
        target = tmp_path / "dotfiles" / "settings.json"
        target.parent.mkdir()
        target.write_text(ours)
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().symlink_to(target)
    elif case == "not_json":
        _write(ours[:-1])
    elif case == "hooks_not_a_dict":
        _write({"hooks": [OURS_ONLY["hooks"]["Stop"]]})
    else:
        _write([OURS_ONLY])
    before = _path().read_text()
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None and plan.problem == problem
    assert settings_patch.apply_unpatch(plan) is None
    assert _path().read_text() == before and _backups() == []
    assert _path().is_symlink() is (case == "link")


def test_a_broken_file_that_never_mentions_aipager_is_no_problem():
    _write('{"model": "opus",')
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None and plan.problem is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_file_it_cannot_read_may_hold_them():
    _write(OURS_ONLY)
    os.chmod(_path(), 0)
    try:
        assert settings_patch.plan_unpatch().problem == "it cannot be read"
    finally:
        os.chmod(_path(), 0o600)


# ---- how it is written ------------------------------------------------------------------------

def test_the_old_file_is_backed_up_and_the_new_one_keeps_its_mode():
    before = _write({**copy.deepcopy(USER), **{"statusLine": {
        "type": "command", "command": "/opt/aipager/bin/aipager-statusline"}}})
    os.chmod(_path(), 0o600)
    name = settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    backup = _path().with_name(name)
    assert backup.read_text() == before
    assert (backup.stat().st_mode & 0o777) == 0o600
    assert (_path().stat().st_mode & 0o777) == 0o600
    assert _read() == USER
    assert sorted(p.name for p in _path().parent.iterdir()) == sorted([_path().name, name])


def test_a_failed_write_leaves_the_file_as_it_was():
    before = _write(OURS_ONLY)
    plan = settings_patch.plan_unpatch()
    # The temporary file's name is taken by a folder: it cannot be made.
    _path().with_name(f"{_path().name}.aipager-tmp").mkdir()
    with pytest.raises(OSError):
        settings_patch.apply_unpatch(plan)
    assert _path().read_text() == before


def test_a_failed_replace_leaves_no_temporary_file(monkeypatch):
    before = _write(OURS_ONLY)
    plan = settings_patch.plan_unpatch()

    def _refuse(src, dst):
        raise OSError(30, "Read-only file system")

    with monkeypatch.context() as m:
        m.setattr(settings_patch.os, "replace", _refuse)
        with pytest.raises(OSError):
            settings_patch.apply_unpatch(plan)
    assert _path().read_text() == before
    assert not _path().with_name(f"{_path().name}.aipager-tmp").exists()


# ---- what uninstall says and does -------------------------------------------------------------

def _flat(text: str) -> str:
    """*text* without the line breaks rich adds to wrap a long path."""
    return text.replace("\n", "")


def _uninstall(monkeypatch, capsys, *, force=True, events=None):
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    monkeypatch.setattr(updater, "_stop_daemon",
                        lambda: events.append("daemon stopped") if events is not None else None)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert updater.cmd_uninstall(argparse.Namespace(force=force)) == 0
    return capsys.readouterr()


def test_uninstall_says_what_it_takes_out_and_does_it(at_opt, monkeypatch, capsys):
    _write(copy.deepcopy(USER))
    settings_patch.apply_settings(settings_patch.plan_settings())
    for old in _backups():
        old.unlink()
    out = _flat(_uninstall(monkeypatch, capsys).out)
    where = f"{_path()}"
    assert (f"aipager's {len(HOOK_EVENTS)} hooks and its status line in {where} "
            "(your other settings there stay)") in out
    (backup,) = _backups()
    assert (f"removed aipager's {len(HOOK_EVENTS)} hooks and its status line from "
            f"{where} (backup: {backup.name})") in out
    assert "the rest of Claude Code's settings.json" in out
    assert _read() == USER


def test_the_preview_alone_changes_nothing(at_opt, monkeypatch, capsys):
    _write(copy.deepcopy(OURS_ONLY))
    before = _path().read_text()
    out = _flat(_uninstall(monkeypatch, capsys, force=False).out)     # answered "n"
    assert "aipager's 1 hook in" in out
    assert _path().read_text() == before


def test_it_is_done_after_the_daemon_stopped(monkeypatch, capsys):
    # The daemon adds the entries again when it starts.
    _write(copy.deepcopy(OURS_ONLY))
    events: list[str] = []
    real = settings_patch.apply_unpatch
    monkeypatch.setattr(settings_patch, "apply_unpatch",
                        lambda plan: events.append("settings") or real(plan))
    _uninstall(monkeypatch, capsys, events=events)
    assert events == ["daemon stopped", "settings"]


def test_a_file_left_as_it_is_says_what_to_remove_by_hand(monkeypatch, capsys):
    _write(json.dumps(OURS_ONLY)[:-1])
    before = _path().read_text()
    err = _flat(_uninstall(monkeypatch, capsys).err)
    assert f"{_path()} is left as it is: it is not valid JSON." in err
    assert err.count("is left as it is") == 2       # before asking, and at the end
    assert "Remove the hooks that run aipager-hook and the statusLine" in err
    assert _path().read_text() == before


def test_a_failed_write_is_reported_and_the_uninstall_goes_on(monkeypatch, capsys):
    _write(copy.deepcopy(OURS_ONLY))
    binary = []

    def _fail(plan):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(settings_patch, "apply_unpatch", _fail)
    monkeypatch.setattr(updater, "_uninstall_binary", lambda installer: binary.append(1) or 0)
    captured = _uninstall(monkeypatch, capsys)
    assert ("is left as it is: writing it failed ([Errno 13] Permission denied)."
            in _flat(captured.err))
    assert binary == [1] and "aipager uninstalled." in captured.out


def test_nothing_of_aipagers_means_no_settings_line(monkeypatch, capsys):
    _write(copy.deepcopy(USER))
    captured = _uninstall(monkeypatch, capsys)
    assert "aipager's" not in captured.out and "left as it is" not in captured.err


def test_the_warning_shows_the_path_as_it_is_named(monkeypatch, capsys, tmp_path):
    odd = tmp_path / "[bold]home" / ".claude" / "settings.json"
    monkeypatch.setattr(settings_patch, "CLAUDE_SETTINGS", odd)
    _write(json.dumps(OURS_ONLY)[:-1])
    err = _flat(_uninstall(monkeypatch, capsys).err)
    assert f"{odd} is left as it is: it is not valid JSON." in err


# ---- review round 1 ---------------------------------------------------------------------------

@pytest.mark.parametrize("mentions_us", [True, False])
def test_a_file_that_is_not_utf8_never_stops_the_uninstall(monkeypatch, capsys, mentions_us):
    body = b'{"model": "caf\xe9"' + (b', "hooks": {"Stop": [{"hooks": [{"type": "command", '
                                     b'"command": "/opt/aipager/bin/aipager-hook"}]}]}'
                                     if mentions_us else b"") + b"}"
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_bytes(body)
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None
    assert plan.problem == ("it is not valid UTF-8 text" if mentions_us else None)
    err = _flat(_uninstall(monkeypatch, capsys).err)
    assert ("is left as it is: it is not valid UTF-8 text." in err) is mentions_us
    assert _path().read_bytes() == body


def test_a_file_nested_too_deep_to_read_is_left_as_it_is():
    _write('{"x": ' + "[" * 100_000 + "]" * 100_000 + ', "y": "aipager-hook"}')
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None and plan.problem == "it is not in the shape Claude Code reads"


@pytest.mark.parametrize("hooks, status_line, words", [
    (16, True, "aipager's 16 hooks and its status line"),
    (1, False, "aipager's 1 hook"),
    (0, True, "aipager's status line"),
])
def test_the_summary_reads_as_plain_words(hooks, status_line, words):
    plan = settings_patch.UnpatchPlan(_path(), "", "{}", hooks=hooks, status_line=status_line)
    assert settings_patch.unpatch_summary(plan) == words


def test_the_file_is_read_again_once_the_daemon_stopped(monkeypatch, capsys):
    # What changed while the question was open (here: the user's own key)
    # is kept.
    _write(copy.deepcopy(OURS_ONLY))

    def _stop():
        _write({**copy.deepcopy(OURS_ONLY), "theme": "dark"})

    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    monkeypatch.setattr(updater, "_stop_daemon", _stop)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)
    updater.cmd_uninstall(argparse.Namespace(force=True))
    assert _read() == {"theme": "dark"}


def test_a_wrapper_of_the_users_is_named_not_removed(monkeypatch, capsys):
    wrapper = "/home/me/bin/aipager-hook-capped.sh"
    line = {"type": "command", "command": "/home/me/bin/aipager-statusline-wrapped"}
    _write({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": HOOK}]}],
                      "PreToolUse": [{"matcher": "*", "hooks": [
                          {"type": "command", "command": wrapper}]}]},
            "statusLine": line})
    plan = settings_patch.plan_unpatch()
    assert plan.hooks == 1 and plan.left == (wrapper, line["command"])
    err = _flat(_uninstall(monkeypatch, capsys).err)
    assert err.count("still runs these after the uninstall:") == 2   # before asking, and after
    assert wrapper in err and line["command"] in err
    assert _read() == {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": wrapper}]}]}, "statusLine": line}


@pytest.mark.parametrize("hooks", [True, False])
def test_open_sessions_are_said_to_keep_their_hooks(monkeypatch, capsys, hooks):
    content = {"statusLine": {"type": "command", "command": "/opt/aipager/bin/aipager-statusline"}}
    if hooks:
        content.update(copy.deepcopy(OURS_ONLY))
    _write(content)
    out = _flat(_uninstall(monkeypatch, capsys).out)
    assert ("sessions already open keep running them until you restart them" in out) is hooks


def test_the_users_own_words_stay_readable():
    _write({**copy.deepcopy(OURS_ONLY), "language": "français ✓"})
    settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    assert '"language": "français ✓"' in _path().read_text(encoding="utf-8")


def test_copies_are_private_from_the_moment_they_exist(monkeypatch):
    _write(copy.deepcopy(OURS_ONLY))
    os.chmod(_path(), 0o600)
    plan = settings_patch.plan_unpatch()
    created = []
    real_open = os.open

    def _open(path, flags, mode=0o777, *a, **kw):
        if flags & os.O_CREAT:
            created.append((os.path.basename(path), mode))
        return real_open(path, flags, mode, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(settings_patch.os, "open", _open)
        settings_patch.apply_unpatch(plan)
    assert len(created) == 2 and all(mode == 0o600 for _name, mode in created)


def test_a_file_changed_since_it_was_read_is_not_overwritten():
    _write(copy.deepcopy(OURS_ONLY))
    plan = settings_patch.plan_unpatch()
    changed = _write({**copy.deepcopy(OURS_ONLY), "theme": "dark"})    # Claude Code wrote it
    with pytest.raises(OSError, match="changed while uninstall was running"):
        settings_patch.apply_unpatch(plan)
    assert _path().read_text() == changed and _backups() == []


def test_the_mode_is_kept_whatever_the_umask():
    _write(copy.deepcopy(OURS_ONLY))
    os.chmod(_path(), 0o644)
    old = os.umask(0o077)
    try:
        name = settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    finally:
        os.umask(old)
    assert (_path().stat().st_mode & 0o777) == 0o644
    assert (_path().with_name(name).stat().st_mode & 0o777) == 0o644


# ---- review round 2 ---------------------------------------------------------------------------

def test_half_an_emoji_in_the_users_settings_never_stops_the_uninstall(monkeypatch, capsys):
    # Claude Code (Node) escapes a lone surrogate as \ud83d; Python reads it
    # back, but UTF-8 cannot write it raw.
    _write('{"permissions": {"allow": ["Bash(echo \\ud83d)"]}, ' + json.dumps(OURS_ONLY)[1:])
    captured = _uninstall(monkeypatch, capsys)
    assert "aipager uninstalled." in captured.out and "left as it is" not in captured.err
    assert _read() == {"permissions": {"allow": ["Bash(echo \ud83d)"]}}
    assert "\\ud83d" in _path().read_text()


def test_a_very_deep_file_never_stops_the_uninstall(monkeypatch, capsys):
    # How deep Python reads and writes back depends on its version: 3.10
    # and 3.11 refuse to read these 2000 levels, 3.12 reads them but cannot
    # write them back with an indent, 3.13 does both. Whichever it is, the
    # uninstall goes through, and the file is either left exactly as it was
    # or rewritten with only aipager's entry gone.
    deep = "[" * 2000 + "]" * 2000
    before = _write('{"x": ' + deep + ", " + json.dumps(OURS_ONLY)[1:])
    plan = settings_patch.plan_unpatch()
    assert "aipager uninstalled." in _uninstall(monkeypatch, capsys).out
    after = _path().read_text()
    if plan.new_text is None:
        assert plan.problem == "it is not in the shape Claude Code reads"
        assert after == before
    else:
        assert plan.problem is None and plan.hooks == 1
        assert "".join(after.split()) == '{"x":' + deep + "}"


def test_a_file_it_cannot_write_back_is_left_as_it_is(monkeypatch):
    # The way the deep file above ends on Python 3.12, on every Python.
    before = _write(copy.deepcopy(OURS_ONLY))
    tried = []

    def _too_deep(*args, **kwargs):
        tried.append(True)
        raise RecursionError("maximum recursion depth exceeded")

    with monkeypatch.context() as m:
        m.setattr(settings_patch.json, "dumps", _too_deep)
        plan = settings_patch.plan_unpatch()
    assert tried, "plan_unpatch never tried to write the file back"
    assert plan.new_text is None and plan.problem == "it is not in the shape Claude Code reads"
    assert settings_patch.apply_unpatch(plan) is None
    assert _path().read_text() == before and _backups() == []


def test_a_backup_from_the_same_second_is_kept(tmp_path):
    _write(copy.deepcopy(OURS_ONLY))
    now = int(time.time())
    older = [_path().with_name(f"{_path().name}.bak.{now + d}") for d in range(3)]
    for i, old in enumerate(older):
        old.write_text(f"earlier backup {i}")
    name = settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    assert _path().with_name(name) not in older
    assert [old.read_text() for old in older] == [f"earlier backup {i}" for i in range(3)]


def test_a_leftover_temporary_link_is_not_written_through(tmp_path):
    _write(copy.deepcopy(OURS_ONLY))
    victim = tmp_path / "victim.json"
    _path().with_name(f"{_path().name}.aipager-tmp").symlink_to(victim)
    settings_patch.apply_unpatch(settings_patch.plan_unpatch())
    assert not victim.exists()
    assert _read() == {}


def test_an_unexpected_error_never_stops_the_uninstall(monkeypatch, capsys):
    _write(copy.deepcopy(OURS_ONLY))

    def _broken(plan):
        raise ValueError("something unexpected")

    monkeypatch.setattr(settings_patch, "apply_unpatch", _broken)
    captured = _uninstall(monkeypatch, capsys)
    assert "is left as it is: writing it failed (something unexpected)." in _flat(captured.err)
    assert "aipager uninstalled." in captured.out


@pytest.mark.skipif(not hasattr(sys, "get_int_max_str_digits"),
                    reason="this Python reads integers of any length")
def test_an_integer_too_long_to_read_never_stops_the_uninstall(monkeypatch, capsys):
    before = _write('{"x": ' + "9" * 5000 + ", " + json.dumps(OURS_ONLY)[1:])
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None and plan.problem == "it is not valid JSON"
    assert "aipager uninstalled." in _uninstall(monkeypatch, capsys).out
    assert _path().read_text() == before


def test_a_number_claude_code_could_not_read_back_is_left_as_it_is():
    before = _write('{"cleanupPeriodDays": 1e999, ' + json.dumps(OURS_ONLY)[1:])
    plan = settings_patch.plan_unpatch()
    assert plan.new_text is None and plan.problem == "it is not in the shape Claude Code reads"
    assert settings_patch.apply_unpatch(plan) is None
    assert _path().read_text() == before


def test_an_error_without_words_is_still_named(monkeypatch, capsys):
    _write(copy.deepcopy(OURS_ONLY))

    def _bare(plan):
        raise ValueError()

    monkeypatch.setattr(settings_patch, "apply_unpatch", _bare)
    assert "writing it failed (ValueError)." in _flat(_uninstall(monkeypatch, capsys).err)


def test_a_file_changed_meanwhile_says_so_plainly(monkeypatch, capsys):
    _write(copy.deepcopy(OURS_ONLY))

    def _changed(plan):
        raise settings_patch.SettingsChanged("it changed while uninstall was running")

    monkeypatch.setattr(settings_patch, "apply_unpatch", _changed)
    err = _flat(_uninstall(monkeypatch, capsys).err)
    assert "is left as it is: it changed while uninstall was running." in err
    assert "writing it failed" not in err


def test_a_copy_is_never_written_into_a_file_already_there(tmp_path):
    # A name taken meanwhile (by a backup, a link) fails rather than being
    # overwritten or followed.
    taken = tmp_path / "taken"
    taken.write_text("someone else's")
    with pytest.raises(FileExistsError):
        settings_patch._write_with_mode(taken, "new", 0o600)
    assert taken.read_text() == "someone else's"
