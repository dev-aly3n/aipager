"""The cross-process update lock (roadmap 8.36).

``flock`` conflicts between different open file descriptions, including
two in one process, so the same mechanism serialises the CLI against the
daemon and two daemon jobs against each other. The daemon-flow release
paths live in tests/integration/admin-self-update-dev/.
"""

from __future__ import annotations

from aipager import self_update
from aipager.self_update import UpdateLock


def test_second_acquire_refused_same_process():
    first, second = UpdateLock(), UpdateLock()
    assert first.try_acquire() is True
    assert first.held is True
    assert second.try_acquire() is False
    assert second.held is False
    first.release()
    assert first.held is False
    assert second.try_acquire() is True
    second.release()


def test_acquire_is_idempotent_for_the_holder():
    lock = UpdateLock()
    assert lock.try_acquire() is True
    assert lock.try_acquire() is True
    lock.release()
    lock.release()  # a second release is harmless


def test_lock_lives_at_the_redirected_path():
    lock = UpdateLock()
    assert lock.try_acquire()
    try:
        path = self_update.UPDATE_LOCK_PATH
        assert path.exists()
        assert path.read_text().strip().isdigit()
    finally:
        lock.release()


def test_unopenable_lock_path_refuses(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(self_update, "UPDATE_LOCK_PATH", blocker / "sub" / "update.lock")
    assert UpdateLock().try_acquire() is False
