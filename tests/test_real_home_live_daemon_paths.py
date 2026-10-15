"""Roadmap 8.115: the real-home snapshot leaves out what the live daemon writes.

The suite's last net against a test writing the operator's real files is a
snapshot of the guarded real-home roots, taken before and after the run.
The operator's own daemon writes into one of them while a run goes on: a
session launch writes its folder, a Mini App load the SDK cache, a restart
the lock and markers, a chat setting the preferences file. So a full run
that overlapped a deploy, a session launch or a settings tap failed for
nothing. Those paths (``LIVE_DAEMON_PATHS`` in the
conftest) are no longer snapshotted.

That is safe only while every writer of them refuses a real-home path
under pytest (``_test_guard.check_write``): a test that wrote there for
real would then fail on its own. These tests prove it for each one, in a
fake "real home" under ``tmp_path``, so a writer that lost its check would
write into tmp, never the operator's home.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from aipager import _test_guard, config, preferences, self_update, session_store
from aipager.cli import daemon as daemon_cli
from aipager.miniapp import cloudflared_fetch, webapp_sdk
from aipager.report import markers
from aipager.scope import Scope
from tests import conftest as guard


@pytest.fixture
def fake_home(tmp_path, monkeypatch, real_home_refusals_expected):
    """A home the guard takes for the operator's real one, with every
    path a writer below derives (HOME itself, and the module constants
    the conftest otherwise redirects) pointing into it."""
    home = tmp_path / "realhome"
    (home / ".config" / "aipager").mkdir(parents=True)
    monkeypatch.setattr(_test_guard, "_real_home", lambda: str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AIPAGER_CLOUDFLARED_CACHE_DIR", raising=False)
    monkeypatch.delenv("AIPAGER_WEBAPP_SDK_CACHE_DIR", raising=False)
    share = home / ".local" / "share" / "aipager"
    monkeypatch.setattr(session_store, "SESSIONS_ROOT", share / "sessions")
    monkeypatch.setattr(config, "REPORT_INSTALL_FILE", share / "install.json")
    monkeypatch.setattr(config, "REPORT_RUNNING_FILE", share / "running.json")
    monkeypatch.setattr(self_update, "UPDATE_LOCK_PATH", share / "update.lock")
    monkeypatch.setattr(self_update, "UPDATE_MARKER_PATH", share / "update-restart.json")
    monkeypatch.setattr(self_update, "HOOK_NOTICES_PATH", share / "update-hook-notices.json")
    monkeypatch.setattr(preferences, "_PREFERENCES_PATH",
                        home / ".config" / "aipager" / "preferences.json")
    return home


def _session_folder():
    session_store.write_session_files(
        Scope(chat_id=42, kind="dm", label="me"), None, "x1")


SHARE = ".local/share/aipager/"
PREFERENCES = ".config/aipager/preferences.json"

#: Each live-daemon path, from the home, and its writer as the daemon calls it.
WRITERS = {
    SHARE + "sessions": _session_folder,
    SHARE + "webapp-sdk": webapp_sdk.cache_dir,
    SHARE + "cloudflared": cloudflared_fetch.cache_dir,
    SHARE + "daemon.lock": daemon_cli._acquire_daemon_lock,
    SHARE + "install.json": markers.first_start,
    SHARE + "running.json": lambda: markers.write_running(service=False),
    SHARE + "update.lock": lambda: self_update.UpdateLock().try_acquire(),
    SHARE + "update-restart.json": lambda: self_update.write_marker({"to": "1.0"}),
    SHARE + "update-hook-notices.json":
        lambda: self_update.write_hook_notices([{"hook": "1.0"}]),
    PREFERENCES: lambda: preferences.set_preference(42, "layout", "merged"),
}


def test_every_path_left_out_has_its_writer_checked_here():
    home = guard._HOME_AT_IMPORT
    assert sorted(p.relative_to(home).as_posix() for p in guard.LIVE_DAEMON_PATHS) == sorted(WRITERS)


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_its_writer_is_refused_in_the_real_home(fake_home, name):
    target = fake_home / name
    _test_guard.refusals.clear()
    try:
        WRITERS[name]()
    except _test_guard.RealHomeWriteError:
        pass                       # the writers that raise; the markers swallow it
    assert not target.exists(), f"{name} was written in the real home"
    assert any(str(target) in r for r in _test_guard.refusals), _test_guard.refusals


# ---- the snapshot itself ------------------------------------------------------------

def _tree(root):
    for rel in ("sessions/dm-42/x1/SESSION.md", "webapp-sdk/sdk.js", "daemon.lock",
                "running.json", "other.json", "keep/notes.txt"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")


def test_the_live_daemon_paths_are_left_out_and_nothing_else(tmp_path):
    root = tmp_path / "share"
    _tree(root)
    skip = (root / "sessions", root / "webapp-sdk", root / "daemon.lock", root / "running.json")
    snap = guard.snapshot_guarded(roots=(root,), skip=skip)
    assert sorted(guard.Path(p).relative_to(root).as_posix() for p in snap) == [
        ".", "keep", "keep/notes.txt", "other.json"]


def test_the_daemon_writing_beside_a_watched_file_changes_nothing(tmp_path):
    # An atomic write (temp file, then rename) moves the folder's time:
    # a folder counts by being there, its files each on their own.
    root = tmp_path / "share"
    _tree(root)
    skip = (root / "running.json",)
    before = guard.snapshot_guarded(roots=(root,), skip=skip)
    tmp = root / "running.json.tmp"
    tmp.write_text("new")
    tmp.replace(root / "running.json")
    assert guard.snapshot_guarded(roots=(root,), skip=skip) == before


def test_a_test_write_next_to_them_is_still_caught(tmp_path):
    root = tmp_path / "share"
    _tree(root)
    skip = (root / "sessions",)
    before = guard.snapshot_guarded(roots=(root,), skip=skip)
    (root / "other.json").write_text("changed by a test")
    (root / "new-folder").mkdir()
    after = guard.snapshot_guarded(roots=(root,), skip=skip)
    assert guard.real_home_changes(before, after, {}) == sorted(
        [str(root / "other.json"), str(root / "new-folder")])


def test_a_left_out_files_temporary_twin_is_left_out_too(tmp_path):
    # An atomic write passes through "<name>.tmp": a snapshot taken during
    # a daemon restart must not see it as a new file.
    root = tmp_path / "share"
    _tree(root)
    (root / "running.json.tmp").write_text("half written")
    (root / "other.json.tmp").write_text("a test's")
    snap = guard.snapshot_guarded(roots=(root,), skip=(root / "running.json",))
    assert str(root / "running.json.tmp") not in snap
    assert str(root / "other.json.tmp") in snap


#: The left-out files a deleter removes, and that deleter.
DELETERS = {
    "running.json": markers.clear_running,
    "update-restart.json": self_update.clear_marker,
    "update-hook-notices.json": lambda: self_update.write_hook_notices([]),
}


@pytest.mark.parametrize("name", sorted(DELETERS))
def test_its_deleter_is_refused_in_the_real_home(fake_home, name):
    target = fake_home / ".local" / "share" / "aipager" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}")
    _test_guard.refusals.clear()
    try:
        DELETERS[name]()
    except _test_guard.RealHomeWriteError:
        pass
    assert target.exists(), f"{name} was deleted in the real home"
    assert any(str(target) in r for r in _test_guard.refusals), _test_guard.refusals


# ---- the preferences file ----------------------------------------------------------

#: Every function that saves the preferences file, as a chat setting or a
#: supergroup move calls it. They all end in ``preferences._save_raw``.
PREFERENCE_WRITERS = {
    "set_preference": lambda: preferences.set_preference(42, "layout", "merged"),
    "move_chat": lambda: preferences.move_chat(42, 43),
    "set_new_session_default": lambda: preferences.set_new_session_default(42, "mode", "ask"),
    "set_ui_language": lambda: preferences.set_ui_language(42, "fa"),
    "set_problem_reports": lambda: preferences.set_problem_reports("off"),
    "set_appearance": lambda: preferences.set_appearance(42, "stop", "red"),
}


def _uses(node, name):
    return sum(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def test_every_function_that_saves_the_preferences_file_is_checked_here():
    """Only ``_load_raw`` and ``_save_raw`` touch the file's path, so a
    function that wrote it directly (past ``check_write``, while the file
    is no longer snapshotted) fails here; every caller of ``_save_raw`` is
    proven below."""
    tree = ast.parse(inspect.getsource(preferences))
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    path_users = {name for name, fn in funcs.items() if _uses(fn, "_PREFERENCES_PATH")}
    assert path_users == {"_load_raw", "_save_raw"}
    # Nothing outside a function touches it either, past its one definition.
    assert _uses(tree, "_PREFERENCES_PATH") == 1 + sum(
        _uses(funcs[name], "_PREFERENCES_PATH") for name in path_users)
    savers = {name for name, fn in funcs.items() if name != "_save_raw" and any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_save_raw"
        for n in ast.walk(fn))}
    assert savers == set(PREFERENCE_WRITERS)


@pytest.mark.parametrize("name", sorted(PREFERENCE_WRITERS))
def test_every_preferences_writer_is_refused_in_the_real_home(fake_home, monkeypatch, name):
    target = fake_home / PREFERENCES
    # A chat with settings already, so move_chat has an entry to move.
    monkeypatch.setattr(preferences, "_cache", {"42": {"layout": "card"}})
    _test_guard.refusals.clear()
    with pytest.raises(_test_guard.RealHomeWriteError):
        PREFERENCE_WRITERS[name]()
    assert not target.exists(), f"{name} wrote the preferences file in the real home"
    assert not target.with_name(target.name + ".tmp").exists()
    assert any(str(target) in r for r in _test_guard.refusals), _test_guard.refusals


def test_in_the_config_folder_only_the_preferences_file_is_left_out(tmp_path):
    config_dir = guard._HOME_AT_IMPORT / ".config" / "aipager"
    assert [p for p in guard.LIVE_DAEMON_PATHS
            if p == config_dir or config_dir in p.parents] == [config_dir / "preferences.json"]
    root = tmp_path / "aipager"
    for rel in ("aipager.yaml", "policy.yaml", "team.yaml", "daemon.env", ".credentials.json",
                "preferences.json", "preferences.json.tmp", "preferences.json.bak",
                "old/preferences.json"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    snap = guard.snapshot_guarded(roots=(root,), skip=(root / "preferences.json",))
    assert sorted(guard.Path(p).relative_to(root).as_posix() for p in snap) == [
        ".", ".credentials.json", "aipager.yaml", "daemon.env", "old", "old/preferences.json",
        "policy.yaml", "preferences.json.bak", "team.yaml"]
