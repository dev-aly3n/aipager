"""Roadmap 8.115: the real-home snapshot leaves out what the live daemon writes.

The suite's last net against a test writing the operator's real files is a
snapshot of the guarded real-home roots, taken before and after the run.
The operator's own daemon writes into one of them while a run goes on: a
session launch writes its folder, a Mini App load the SDK cache, a restart
the lock and markers. So a full run that overlapped a deploy or a session
launch failed for nothing. Those paths (``LIVE_DAEMON_PATHS`` in the
conftest) are no longer snapshotted.

That is safe only while every writer of them refuses a real-home path
under pytest (``_test_guard.check_write``): a test that wrote there for
real would then fail on its own. These tests prove it for each one, in a
fake "real home" under ``tmp_path``, so a writer that lost its check would
write into tmp, never the operator's home.
"""

from __future__ import annotations

import pytest

from aipager import _test_guard, config, self_update, session_store
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
    return home


def _session_folder():
    session_store.write_session_files(
        Scope(chat_id=42, kind="dm", label="me"), None, "x1")


#: Each live-daemon path and its writer, as the daemon calls it.
WRITERS = {
    "sessions": _session_folder,
    "webapp-sdk": webapp_sdk.cache_dir,
    "cloudflared": cloudflared_fetch.cache_dir,
    "daemon.lock": daemon_cli._acquire_daemon_lock,
    "install.json": markers.first_start,
    "running.json": lambda: markers.write_running(service=False),
    "update.lock": lambda: self_update.UpdateLock().try_acquire(),
    "update-restart.json": lambda: self_update.write_marker({"to": "1.0"}),
}


def test_every_path_left_out_has_its_writer_checked_here():
    assert sorted(p.name for p in guard.LIVE_DAEMON_PATHS) == sorted(WRITERS)
    share = guard.Path.home() / ".local" / "share" / "aipager"
    assert all(p.parent == share for p in guard.LIVE_DAEMON_PATHS)


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_its_writer_is_refused_in_the_real_home(fake_home, name):
    target = fake_home / ".local" / "share" / "aipager" / name
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
