"""A session's folder is trusted in ~/.claude.json before it starts, and
Claude Code's own files are only ever rewritten when that is safe.

Claude Code asks "Do you trust this folder?" in a folder it has never
trusted; the first message sent from Telegram answers it with Enter on
"No, exit" and the session ends (real Claude Code 2.1.301). So
``inject.launch_session`` trusts the folder first
(``claude_bootstrap.ensure_folder_trusted``). ~/.claude.json is Claude
Code's whole user config and every running Claude rewrites it, so
``_update_json`` never writes a file it could not read whole, and never
replaces one that changed since it read it.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import claude_bootstrap, safety
from aipager.dtach import inject as dtach_inject


@pytest.fixture
def claude_json(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home" / ".claude.json"
    path.parent.mkdir()
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", path)
    return path


def _write(path: Path, data) -> bytes:
    raw = (json.dumps(data, indent=2) + "\n").encode()
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    return raw


def _projects(path: Path) -> dict:
    return json.loads(path.read_text())["projects"]


def _leftovers(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if p.name != path.name)


# ---- ensure_folder_trusted ----------------------------------------------------

def test_an_untrusted_folder_is_trusted_by_its_real_path(claude_json, tmp_path):
    real = tmp_path / "work"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    _write(claude_json, {"oauthAccount": {"x": 1}, "projects": {"/other": {"a": 1}}})

    assert claude_bootstrap.ensure_folder_trusted(str(link)) is True

    data = json.loads(claude_json.read_text())
    assert data["oauthAccount"] == {"x": 1}
    assert data["projects"]["/other"] == {"a": 1}
    entry = data["projects"][str(real)]
    assert entry["hasTrustDialogAccepted"] is True
    assert str(link) not in data["projects"]
    assert stat.S_IMODE(claude_json.stat().st_mode) == 0o600
    assert _leftovers(claude_json) == []


@pytest.mark.parametrize("trusted", ["self", "parent", "root"])
def test_no_write_when_the_folder_or_one_above_is_trusted(claude_json, tmp_path, trusted):
    folder = tmp_path / "a" / "b"
    folder.mkdir(parents=True)
    key = {"self": str(folder), "parent": str(tmp_path / "a"), "root": "/"}[trusted]
    raw = _write(claude_json, {"projects": {key: {"hasTrustDialogAccepted": True}}})
    before = claude_json.stat().st_mtime_ns

    assert claude_bootstrap.ensure_folder_trusted(str(folder)) is False

    assert claude_json.read_bytes() == raw
    assert claude_json.stat().st_mtime_ns == before


def test_an_entry_marked_untrusted_is_completed_not_replaced(claude_json, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    _write(claude_json, {"projects": {str(folder): {"allowedTools": ["Read"],
                                                    "hasTrustDialogAccepted": False}}})

    assert claude_bootstrap.ensure_folder_trusted(str(folder)) is True

    entry = _projects(claude_json)[str(folder)]
    assert entry["allowedTools"] == ["Read"]
    assert entry["hasTrustDialogAccepted"] is True


@pytest.mark.parametrize("content", [
    b"{\n  \"oauthAccount\": {\"x\": 1},\n  \"projects\": {",   # half written
    b"",                                                         # truncated
    b"[1, 2, 3]\n",                                              # not an object
    b"\xff\xfe not utf-8",
])
def test_a_file_that_is_not_a_whole_json_object_is_never_written(claude_json, tmp_path,
                                                                 content):
    claude_json.write_bytes(content)

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is False

    assert claude_json.read_bytes() == content
    assert _leftovers(claude_json) == []


def test_an_unreadable_file_is_never_written(claude_json, tmp_path):
    raw = _write(claude_json, {"projects": {}})
    os.chmod(claude_json, 0)
    try:
        if os.access(claude_json, os.R_OK):
            pytest.skip("running as a user who can read a mode-000 file")
        assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is False
    finally:
        os.chmod(claude_json, 0o600)
    assert claude_json.read_bytes() == raw


def test_a_missing_file_is_created(claude_json, tmp_path):
    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is True

    assert _projects(claude_json)[str(tmp_path)]["hasTrustDialogAccepted"] is True
    assert stat.S_IMODE(claude_json.stat().st_mode) == 0o600


def test_a_projects_value_that_is_not_an_object_is_left_alone(claude_json, tmp_path):
    raw = _write(claude_json, {"projects": ["not", "a", "dict"]})

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is False

    assert claude_json.read_bytes() == raw


# ---- the race with a running Claude --------------------------------------------

def _claude_writes(path: Path, key: str) -> None:
    """What a running Claude does meanwhile: rewrites the file whole."""
    data = json.loads(path.read_text())
    data[key] = True
    tmp = path.with_name(".claude-own-write")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def test_a_change_before_the_replace_is_kept_and_the_trust_retried(
        claude_json, tmp_path, monkeypatch):
    _write(claude_json, {"projects": {}})
    calls = {"n": 0}

    def _before_replace(path):
        calls["n"] += 1
        if calls["n"] == 1:
            _claude_writes(path, "claudeWroteThis")

    monkeypatch.setattr(claude_bootstrap, "_before_replace", _before_replace)

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is True

    data = json.loads(claude_json.read_text())
    assert data["claudeWroteThis"] is True            # Claude's change kept
    assert data["projects"][str(tmp_path)]["hasTrustDialogAccepted"] is True
    assert calls["n"] == 2
    assert _leftovers(claude_json) == []


def test_a_read_torn_by_a_writer_is_read_again(claude_json, tmp_path, monkeypatch):
    """Claude rewrote the file while it was read: the bytes read are torn
    (not JSON). Read again, not given up on as a broken file."""
    _write(claude_json, {"projects": {}})
    calls = {"n": 0}
    real_read = claude_bootstrap._read_bytes

    def _read_bytes(path):
        calls["n"] += 1
        if calls["n"] == 1:
            torn = real_read(path)[:7]
            _claude_writes(path, "claudeWroteThis")
            return torn
        return real_read(path)

    monkeypatch.setattr(claude_bootstrap, "_read_bytes", _read_bytes)

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is True

    data = json.loads(claude_json.read_text())
    assert data["claudeWroteThis"] is True
    assert data["projects"][str(tmp_path)]["hasTrustDialogAccepted"] is True
    assert calls["n"] == 3   # the torn read, the read again, the check before the replace


def test_a_file_that_keeps_changing_is_left_as_claude_wrote_it(
        claude_json, tmp_path, monkeypatch):
    _write(claude_json, {"projects": {}})
    calls = {"n": 0}

    def _before_replace(path):
        calls["n"] += 1
        _claude_writes(path, f"claudeWrite{calls['n']}")

    monkeypatch.setattr(claude_bootstrap, "_before_replace", _before_replace)

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is False

    data = json.loads(claude_json.read_text())
    assert calls["n"] == claude_bootstrap.UPDATE_TRIES
    assert all(data[f"claudeWrite{n}"] for n in range(1, calls["n"] + 1))
    assert str(tmp_path) not in data["projects"]
    assert _leftovers(claude_json) == []


def test_a_file_created_meanwhile_is_not_replaced(claude_json, tmp_path, monkeypatch):
    """No file at the read, one by the replace (Claude's first start):
    read again, never clobbered with a file that only has projects."""
    calls = {"n": 0}

    def _before_replace(path):
        calls["n"] += 1
        if calls["n"] == 1:
            _write(path, {"oauthAccount": {"x": 1}})

    monkeypatch.setattr(claude_bootstrap, "_before_replace", _before_replace)

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is True

    data = json.loads(claude_json.read_text())
    assert data["oauthAccount"] == {"x": 1}
    assert data["projects"][str(tmp_path)]["hasTrustDialogAccepted"] is True


def test_a_same_size_rewrite_in_place_is_caught_by_its_bytes(
        claude_json, tmp_path, monkeypatch):
    """Claude rewrote the file in place, same size, within one tick of the
    file clock: inode, size and mtime are as they were. The bytes are not,
    so the trust is retried on top of Claude's change, never over it."""
    _write(claude_json, {"projects": {}, "tipsSeen": "aaaa"})
    calls = {"n": 0}

    def _before_replace(path):
        calls["n"] += 1
        if calls["n"] == 1:
            st = path.stat()
            raw = path.read_bytes()
            with open(path, "r+b") as f:
                f.write(raw.replace(b'"aaaa"', b'"bbbb"'))
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            after = path.stat()
            assert (after.st_ino, after.st_size, after.st_mtime_ns) == \
                (st.st_ino, st.st_size, st.st_mtime_ns)

    monkeypatch.setattr(claude_bootstrap, "_before_replace", _before_replace)

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is True

    data = json.loads(claude_json.read_text())
    assert data["tipsSeen"] == "bbbb"
    assert data["projects"][str(tmp_path)]["hasTrustDialogAccepted"] is True
    assert calls["n"] == 2


def test_a_failed_write_leaves_the_file_and_no_temp_file(claude_json, tmp_path, monkeypatch):
    raw = _write(claude_json, {"oauthAccount": {"x": 1}})

    def _replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(claude_bootstrap, "_replace", _replace)

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is False

    assert claude_json.read_bytes() == raw
    assert _leftovers(claude_json) == []


def test_a_symlinked_file_is_written_where_it_points(claude_json, tmp_path):
    """A dotfile manager's link stays a link."""
    real = tmp_path / "dotfiles" / "claude.json"
    real.parent.mkdir()
    _write(real, {"oauthAccount": {"x": 1}})
    claude_json.symlink_to(real)

    assert claude_bootstrap.ensure_folder_trusted(str(tmp_path)) is True

    assert claude_json.is_symlink()
    assert json.loads(real.read_text())["projects"][str(tmp_path)]["hasTrustDialogAccepted"]


def test_the_file_keeps_its_mode_and_its_own_letters(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    folder = "/home/alice/\u067e\u0631\u0648\u0698\u0647"
    settings.write_text(json.dumps({"projects_note": folder}, ensure_ascii=False))
    os.chmod(settings, 0o600)
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)

    assert claude_bootstrap._ensure_bypass_accepted() is True

    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    assert folder in settings.read_text(encoding="utf-8")


# ---- the bootstrap uses the same guarded write ----------------------------------

def test_the_bootstrap_never_writes_a_broken_claude_json(claude_json, tmp_path, monkeypatch):
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", tmp_path / "settings.json")
    claude_json.write_bytes(b'{"oauthAccount": {')

    claude_bootstrap.bootstrap_claude_settings(str(tmp_path))

    assert claude_json.read_bytes() == b'{"oauthAccount": {'


def test_the_bootstrap_never_marks_the_work_folder_trusted(claude_json, tmp_path, monkeypatch):
    """A folder is marked when a session starts in it, never at daemon
    start: the work folder may never get a session."""
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", tmp_path / "settings.json")
    raw = _write(claude_json, {"oauthAccount": {"x": 1}})

    claude_bootstrap.bootstrap_claude_settings(str(tmp_path))

    assert claude_json.read_bytes() == raw


@pytest.mark.parametrize("where, warned", [
    ("root", True), ("above home", True), ("above home, trusted", False),
    ("above home, a folder below it trusted", True), ("home", False), ("project", False)])
def test_the_bootstrap_warns_when_sessions_would_start_above_home(
        claude_json, tmp_path, monkeypatch, caplog, where, warned):
    """A session started in / or a folder above home that Claude Code does
    not trust yet stops at its trust question (never marked), so the
    daemon says so at start."""
    home = tmp_path / "h"
    (home / "project").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", tmp_path / "settings.json")
    workdir = {"root": Path("/"), "above home": tmp_path, "above home, trusted": tmp_path,
               "above home, a folder below it trusted": tmp_path,
               "home": home, "project": home / "project"}[where]
    raw = None
    if where == "above home, trusted":
        raw = _write(claude_json, {"projects": {str(tmp_path): {"hasTrustDialogAccepted": True}}})
    if where == "above home, a folder below it trusted":
        raw = _write(claude_json, {"projects": {str(home): {"hasTrustDialogAccepted": True}}})

    with caplog.at_level(logging.WARNING, logger="aipager.claude_bootstrap"):
        claude_bootstrap.bootstrap_claude_settings(str(workdir))

    lines = [r.getMessage() for r in caplog.records
             if "Do you trust this folder?" in r.getMessage()]
    assert lines == ([f"claude bootstrap: the default session folder {workdir} is / or a "
                      "folder above the home folder and Claude Code does not trust it; a "
                      "session started there stops at its \"Do you trust this folder?\" "
                      "question - start aipager in a project folder (`aipager service "
                      "install` runs it in the home folder) or set AIPAGER_WORK_DIR"]
                     if warned else [])
    assert (claude_json.read_bytes() if raw else claude_json.exists()) == (raw or False)


def test_the_bootstrap_goes_on_when_the_folder_check_raises(claude_json, tmp_path, monkeypatch):
    def _boom(path):
        raise FileNotFoundError("the daemon's folder was removed")

    monkeypatch.setattr(safety, "holds_home_folder", _boom)
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", tmp_path / "settings.json")
    wired = []
    monkeypatch.setattr(claude_bootstrap, "_ensure_hooks_and_statusline",
                        lambda: wired.append(True) or False)

    claude_bootstrap.bootstrap_claude_settings("relative/folder")

    assert wired == [True]


# ---- safety.holds_home_folder: the folders never marked trusted -----------------

def test_holds_home_folder_is_root_and_the_folders_above_home(tmp_path, monkeypatch):
    home = tmp_path / "home" / "alice"
    (home / "proj").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / "to-home").symlink_to(home)
    (tmp_path / "to-above").symlink_to(home.parent)

    assert safety.holds_home_folder("/")
    assert safety.holds_home_folder(str(home.parent))
    assert safety.holds_home_folder(str(tmp_path))
    assert safety.holds_home_folder(str(tmp_path / "to-above"))
    monkeypatch.chdir(home.parent)
    assert safety.holds_home_folder(".")

    assert not safety.holds_home_folder(str(home))
    assert not safety.holds_home_folder(str(home) + "/")
    assert not safety.holds_home_folder(str(tmp_path / "to-home"))
    assert not safety.holds_home_folder(str(home / "proj"))
    assert not safety.holds_home_folder(str(tmp_path / "elsewhere"))


@pytest.mark.parametrize("path", ["", None, 7, "/srv/a\x00b"])
def test_holds_home_folder_fails_closed_on_an_unusable_path(path):
    assert safety.holds_home_folder(path) is True


def test_holds_home_folder_matches_home_in_any_case_where_case_is_ignored(
        tmp_path, monkeypatch):
    """On a case-insensitive file system (macOS) home spelled in other
    letters is still home itself, not a folder above it."""
    home = tmp_path / "Users" / "alice"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(safety, "_case_insensitive_fs", lambda: True)

    assert not safety.holds_home_folder(str(tmp_path / "Users" / "ALICE"))
    assert safety.holds_home_folder(str(tmp_path / "USERS"))


def test_holds_home_folder_fails_closed_when_the_current_folder_is_gone(
        tmp_path, monkeypatch):
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()

    assert safety.holds_home_folder("relative/folder") is True


def test_root_holds_the_home_folder_even_when_it_is_home(monkeypatch):
    monkeypatch.setenv("HOME", "/")

    assert safety.holds_home_folder("/") is True


# ---- launch_session trusts the folder before the session starts -----------------

def _spawn_recorder(monkeypatch, seen: dict, claude_json: Path):
    async def _fake_exec(*args, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        seen["claude_json_at_spawn"] = (claude_json.read_bytes()
                                        if claude_json.exists() else None)
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)


def test_launch_trusts_the_folder_before_the_session_starts(
        claude_json, tmp_path, monkeypatch, run_async):
    folder = tmp_path / "aipager-users" / "group-team"
    folder.mkdir(parents=True)
    _write(claude_json, {"oauthAccount": {"x": 1}})
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen, claude_json)

    ok, err = run_async(dtach_inject.launch_session("ft1", mode="ask", cwd=str(folder)))

    assert ok, err
    assert seen["cwd"] == str(folder)
    at_spawn = json.loads(seen["claude_json_at_spawn"])
    assert at_spawn["projects"][str(folder)]["hasTrustDialogAccepted"] is True
    assert at_spawn["oauthAccount"] == {"x": 1}


def test_launch_goes_on_when_the_folder_cannot_be_trusted(
        claude_json, tmp_path, monkeypatch, run_async):
    claude_json.write_bytes(b"{ half")
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen, claude_json)

    ok, err = run_async(dtach_inject.launch_session("ft2", mode="ask", cwd=str(tmp_path)))

    assert ok, err
    assert claude_json.read_bytes() == b"{ half"


def test_launch_trusts_the_home_folder(claude_json, tmp_path, monkeypatch, run_async):
    """The home folder is the default session folder (a systemd user unit,
    the container). Claude Code keeps a yes given there for that session
    only, which a session started from Telegram cannot give."""
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _write(claude_json, {"oauthAccount": {"x": 1}})
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen, claude_json)

    ok, err = run_async(dtach_inject.launch_session("ft5", mode="ask", cwd=str(home)))

    assert ok, err
    at_spawn = json.loads(seen["claude_json_at_spawn"])
    assert at_spawn["projects"][str(home)]["hasTrustDialogAccepted"] is True
    assert list(at_spawn["projects"]) == [str(home)]


@pytest.mark.parametrize("where", ["above home", "root"])
def test_launch_never_trusts_a_folder_that_holds_the_home_folder(
        claude_json, tmp_path, monkeypatch, run_async, where):
    """Trusting / or a folder above home would trust every folder under it
    outside a git repository, other users' included: never done at
    launch, and the launch goes on."""
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cwd = {"above home": tmp_path, "root": Path("/")}[where]
    raw = _write(claude_json, {"oauthAccount": {"x": 1}})
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen, claude_json)

    ok, err = run_async(dtach_inject.launch_session("ft4", mode="ask", cwd=str(cwd)))

    assert ok, err
    assert seen["cwd"] == str(cwd)
    assert claude_json.read_bytes() == raw


def test_launch_goes_on_when_trusting_raises(claude_json, tmp_path, monkeypatch, run_async):
    def _boom(folder):
        raise RuntimeError("boom")

    monkeypatch.setattr(claude_bootstrap, "ensure_folder_trusted", _boom)
    seen: dict = {}
    _spawn_recorder(monkeypatch, seen, claude_json)

    ok, err = run_async(dtach_inject.launch_session("ft3", mode="ask", cwd=str(tmp_path)))

    assert ok, err
    assert seen["cwd"] == str(tmp_path)
