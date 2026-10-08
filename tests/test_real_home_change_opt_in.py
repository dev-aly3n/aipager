"""``_guard_real_home``'s comparison and its one opt-in
(``expect_real_home_change``): the restart e2e test lets the new daemon
rewrite ``~/.local/share/aipager/daemon.lock``, and nothing else may
slip past the guard because of it. Also what the guard may snapshot: no
file the live daemon rewrites on its own."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import conftest as guard

BEFORE = {"/h/a": (1, 10), "/h/b": (1, 10)}


def test_an_unregistered_change_is_flagged():
    after = {"/h/a": (2, 11), "/h/b": (1, 10), "/h/new": (5, 1)}
    assert guard.real_home_changes(BEFORE, after, {"/h/b": (1, 10)}) == ["/h/a", "/h/new"]
    assert guard.real_home_changes(BEFORE, after, {}) == ["/h/a", "/h/new"]


def test_a_deleted_file_is_flagged_registered_or_not():
    after = {}
    assert guard.real_home_changes(BEFORE, after, {}) == ["/h/a", "/h/b"]
    assert guard.real_home_changes(BEFORE, after, {"/h/b": None}) == ["/h/a", "/h/b"]


def test_only_the_registered_path_is_ignored():
    after = {"/h/a": (2, 11), "/h/b": (2, 11)}
    assert guard.real_home_changes(BEFORE, after, {"/h/a": (2, 11)}) == ["/h/b"]


def test_a_registered_path_changed_again_after_its_test_is_flagged():
    after = {"/h/a": (3, 12), "/h/b": (1, 10)}
    assert guard.real_home_changes(BEFORE, after, {"/h/a": (2, 11)}) == ["/h/a"]


def test_nothing_changed_reports_nothing():
    assert guard.real_home_changes(BEFORE, dict(BEFORE), {}) == []


def test_registration_takes_only_an_exact_guarded_path(monkeypatch, tmp_path):
    root = tmp_path / "share"
    monkeypatch.setattr(guard, "_GUARDED_HOME_PATHS", (root,))
    assert guard.check_expected_real_home_path(root / "daemon.lock") == str(root / "daemon.lock")
    with pytest.raises(ValueError):
        guard.check_expected_real_home_path(tmp_path / "elsewhere" / "daemon.lock")
    with pytest.raises(ValueError):
        guard.check_expected_real_home_path(Path("daemon.lock"))


def test_the_registrar_records_the_state_the_test_left(monkeypatch, tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    lock = root / "daemon.lock"
    lock.write_text("1")
    other = root / "other"
    other.write_text("1")
    monkeypatch.setattr(guard, "_GUARDED_HOME_PATHS", (root,))
    before = guard._snapshot_guarded()

    store: dict = {}
    register, finish = guard.real_home_change_registrar(store)
    with pytest.raises(ValueError):
        register(tmp_path / "not-guarded")
    register(lock)
    with pytest.raises(RuntimeError):
        register(other)
    lock.write_text("22")  # the daemon's rewrite, after registration
    finish()
    assert store == {str(lock): guard._stat_key(lock)}
    assert guard.real_home_changes(before, guard._snapshot_guarded(), store) == []

    other.write_text("333")
    assert guard.real_home_changes(before, guard._snapshot_guarded(), store) == [str(other)]


#: Files the operator's running daemon rewrites on its own, whenever it
#: saves sessions, flood state or a problem-report count.
DAEMON_REWRITES = ("aipager.config.REPORTS_FILE", "aipager.config.SESSION_STATE_FILE",
                   "aipager.config.FLOOD_STATE_FILE")


@pytest.mark.parametrize("name", DAEMON_REWRITES)
def test_a_file_the_live_daemon_rewrites_is_outside_the_guarded_snapshot(name, real_home_paths):
    # The guard's rule (tests/conftest.py, above _GUARDED_HOME_PATHS):
    # such a file is covered by its per-test redirect, never snapshotted.
    # Inside a snapshotted folder, the live daemon's own save (and the
    # folder's mtime its atomic rename bumps) fails a full run whenever
    # the daemon counts something during it (2026-10-08, reports.json).
    path = Path(real_home_paths[name])
    # Not vacuous: the roots exist and the path is the production one
    # (a redirected tmp path is never inside a real-home root).
    assert guard._GUARDED_HOME_PATHS and Path.home() in path.parents, path
    inside = [str(root) for root in guard._GUARDED_HOME_PATHS
              if path == root or root in path.parents]
    assert not inside, (
        f"{name} ({path}) is rewritten by the live daemon but sits inside the "
        f"real-home snapshot root {inside}: move it out (the problem report "
        "store lives in ~/.local/state/aipager), never drop a root from "
        "_GUARDED_HOME_PATHS")
