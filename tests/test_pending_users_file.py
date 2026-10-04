"""The pending-users file (roadmap 8.94f): written atomically with mode
0600, capped to the newest PENDING_USERS_MAX records, records not seen for
PENDING_USERS_MAX_AGE_SECONDS dropped on each write, and every reader
tolerates a missing, corrupt or old-format file.

The file is redirected to tmp_path here (on top of conftest's own
redirect): no test touches the real ~/.claude.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from aipager import team


@pytest.fixture
def pending(monkeypatch, tmp_path):
    path = tmp_path / "claude" / "aipager-pending-users.json"
    monkeypatch.setattr(team, "PENDING_USERS_PATH", path)
    return path


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")


def _seed(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records), encoding="utf-8")


def _ids(path) -> list:
    return [r["user_id"] for r in json.loads(path.read_text(encoding="utf-8"))]


def test_limits_are_fixed():
    assert team.PENDING_USERS_MAX == 200
    assert team.PENDING_USERS_MAX_AGE_SECONDS == 30 * 24 * 3600


# ---- atomic write, mode 0600 ----------------------------------------------

def test_written_with_mode_0600_even_over_an_existing_0644_file(pending):
    _seed(pending, [])
    os.chmod(pending, 0o644)
    team.record_pending_user(7, username="bob")
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    assert _ids(pending) == [7]


def test_first_write_creates_the_folder_and_is_0600(pending):
    assert not pending.parent.exists()
    team.record_pending_user(7, username="bob")
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    assert _ids(pending) == [7]


def test_a_crash_before_the_move_leaves_the_old_file(pending, monkeypatch):
    """The new content goes to a temporary file first; when the final move
    fails the old file is untouched and no temporary file is left."""
    old = [{"user_id": 5, "username": "eve", "first_seen": _ago(1)}]
    _seed(pending, old)
    before = pending.read_bytes()

    def _boom(src, dst):
        raise OSError("disk gone")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", _boom)
        team.record_pending_user(7, username="bob")  # swallowed and logged

    assert pending.read_bytes() == before
    assert sorted(p.name for p in pending.parent.iterdir()) == [pending.name]


def test_a_failed_write_cleans_up_its_temporary_file(pending):
    old = [{"user_id": 5, "username": "eve", "first_seen": _ago(1)}]
    _seed(pending, old)
    before = pending.read_bytes()
    with pytest.raises(TypeError):
        team._write_pending_users([{"user_id": 7, "x": object()}])
    assert pending.read_bytes() == before
    assert sorted(p.name for p in pending.parent.iterdir()) == [pending.name]


def test_the_new_content_is_flushed_to_disk_before_the_move(pending,
                                                            monkeypatch):
    synced = []
    real_fsync = os.fsync
    moved = []
    real_replace = os.replace

    def _fsync(fd):
        synced.append(fd)
        real_fsync(fd)

    def _replace(src, dst):
        moved.append(bool(synced))
        real_replace(src, dst)
    with monkeypatch.context() as m:
        m.setattr(os, "fsync", _fsync)
        m.setattr(os, "replace", _replace)
        team.record_pending_user(7, username="bob")
    assert synced and moved == [True]


def test_prune_skips_entries_that_are_not_records():
    assert team._prune_pending_users([1, "x", None, {"user_id": 7}]) == [
        {"user_id": 7}]


def test_clear_also_writes_atomically_and_0600(pending):
    _seed(pending, [{"user_id": 5, "first_seen": _ago(1)},
                    {"user_id": 7, "first_seen": _ago(1)}])
    os.chmod(pending, 0o644)
    assert team.clear_pending_user(5) is True
    assert _ids(pending) == [7]
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600


def test_clear_crash_before_the_move_leaves_the_old_file(pending, monkeypatch):
    _seed(pending, [{"user_id": 5, "first_seen": _ago(1)},
                    {"user_id": 7, "first_seen": _ago(1)}])
    before = pending.read_bytes()

    def _boom(src, dst):
        raise OSError("disk gone")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", _boom)
        assert team.clear_pending_user(5) is False
    assert pending.read_bytes() == before
    assert sorted(p.name for p in pending.parent.iterdir()) == [pending.name]


# ---- cap and age ----------------------------------------------------------

def test_cap_keeps_the_newest(pending):
    _seed(pending, [{"user_id": 1000 + i, "first_seen": _ago(1)}
                    for i in range(250)])
    team.record_pending_user(7, username="bob")
    ids = _ids(pending)
    assert len(ids) == team.PENDING_USERS_MAX
    assert ids[-1] == 7
    assert ids[0] == 1000 + 250 - (team.PENDING_USERS_MAX - 1)


def test_cap_also_on_clear(pending):
    _seed(pending, [{"user_id": 1000 + i, "first_seen": _ago(1)}
                    for i in range(250)])
    assert team.clear_pending_user(1249) is True
    ids = _ids(pending)
    assert len(ids) == team.PENDING_USERS_MAX
    assert ids[-1] == 1248


def test_old_records_are_dropped_on_write(pending):
    _seed(pending, [
        {"user_id": 1, "first_seen": _ago(31)},
        {"user_id": 2, "first_seen": _ago(29)},
        # A time with no zone is read as UTC.
        {"user_id": 3, "first_seen": (datetime.now(timezone.utc)
                                      - timedelta(days=40)).replace(
                                          tzinfo=None).isoformat()},
        # No readable time (an older file): kept, the cap bounds it.
        {"user_id": 4},
        {"user_id": 5, "first_seen": "not a time"},
        {"user_id": 6, "first_seen": 12345},
    ])
    team.record_pending_user(7, username="bob")
    assert _ids(pending) == [2, 4, 5, 6, 7]


def test_a_repeat_sender_moves_to_newest_with_a_fresh_time(pending):
    _seed(pending, [{"user_id": 7, "first_seen": _ago(29)},
                    {"user_id": 8, "first_seen": _ago(1)}])
    team.record_pending_user(7, username="bob")
    data = json.loads(pending.read_text(encoding="utf-8"))
    assert [r["user_id"] for r in data] == [8, 7]
    assert team._pending_seen_at(data[-1]) > datetime.now(
        timezone.utc) - timedelta(minutes=5)


# ---- readers tolerate old and corrupt files -------------------------------

def test_missing_file_reads_empty(pending):
    assert team.list_pending_users() == []
    assert team.clear_pending_user(7) is False


@pytest.mark.parametrize("raw", [
    b"{not json", b"\xff\xfe\x00garbage", b'{"user_id": 7}', b"",
    b"null", b"5",
])
def test_corrupt_file_reads_empty_and_is_replaced(pending, raw):
    pending.parent.mkdir(parents=True)
    pending.write_bytes(raw)
    assert team.list_pending_users() == []
    team.record_pending_user(7, username="bob")
    assert _ids(pending) == [7]


def test_entries_that_are_not_records_are_skipped(pending):
    _seed(pending, [1, "x", None, ["y"],
                    {"user_id": 7, "username": "bob"}])
    assert team.list_pending_users() == [{"user_id": 7, "username": "bob"}]
    team.record_pending_user(8, username="carol")
    assert _ids(pending) == [7, 8]


def test_wizard_handle_lookup_reads_an_old_file(pending):
    """The wizard's "add a person by @handle" first looks in the pending
    file; an old file with stray entries must not crash it."""
    from aipager.wizard import team_setup
    _seed(pending, [1, "x", {"user_id": 7, "username": "Bob"}])
    assert team_setup._resolve_user("T", "@bob") == (7, "bob")


def test_wizard_auto_detect_reads_old_and_corrupt_files(pending):
    from aipager.wizard import telegram_api
    _seed(pending, [1, {"user_id": 7, "username": "bob"},
                    {"user_id": 9, "chat_id": -1009, "chat_type": "group",
                     "chat_title": "ops"}])
    assert telegram_api._fetch_id_from_pending(want="group") == (
        -1009, "ops", None)
    uid, _who, adv = telegram_api._fetch_id_from_pending(want="user")
    assert (uid, adv) == (9, None)
    pending.write_bytes(b"\xff\xfe")
    uid, _who, adv = telegram_api._fetch_id_from_pending(want="user")
    assert uid is None and adv == telegram_api.DAEMON_NO_USER_ADVISORY
