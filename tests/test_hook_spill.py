"""The hook spill folder and its files (roadmap 8.138).

``aipager/dtach/hook_spill.py`` is what lets an event too big for one
datagram reach the daemon. The daemon reads files on request from a
socket every local user can write to, so most of these tests are about
what it refuses: a name that is not its own, a link, a file of another
user, a file too big, a folder that is not its own.

conftest's ``_isolate_hook_spill`` points the folder at ``tmp_path/spill``
for every test, whatever base folder is passed.
"""

from __future__ import annotations

import errno
import os
import stat
import threading
import time
from types import SimpleNamespace

import pytest

from aipager.dtach import hook_spill

BASE = "/unused"  # the conftest redirect ignores the base folder
NAME = "ev-" + "a" * 32 + ".json"


@pytest.fixture
def folder(tmp_path):
    assert hook_spill.ensure_dir(BASE) == ""
    return tmp_path / "spill"


def _plant(folder, name=NAME, body=b'{"x": 1}', mode=0o600):
    path = folder / name
    path.write_bytes(body)
    path.chmod(mode)
    return path


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


# ---- the folder -------------------------------------------------------------------

def test_ensure_creates_0700(tmp_path):
    assert not (tmp_path / "spill").exists()
    assert hook_spill.ensure_dir(BASE) == ""
    assert _mode(tmp_path / "spill") == 0o700


def test_ensure_tightens_our_loose_folder(tmp_path):
    (tmp_path / "spill").mkdir()
    (tmp_path / "spill").chmod(0o777)
    assert hook_spill.ensure_dir(BASE) == ""
    assert _mode(tmp_path / "spill") == 0o700


def test_ensure_refuses_a_foreign_folder(tmp_path, monkeypatch):
    (tmp_path / "spill").mkdir()
    (tmp_path / "spill").chmod(0o755)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    assert hook_spill.ensure_dir(BASE) == "not-ours"
    assert _mode(tmp_path / "spill") == 0o755      # never changed


def test_ensure_refuses_a_symlinked_folder(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    real.chmod(0o755)
    (tmp_path / "spill").symlink_to(real)
    assert hook_spill.ensure_dir(BASE) != ""
    assert _mode(real) == 0o755


def test_spill_dir_names_the_uid(_isolate_hook_spill):
    assert str(_isolate_hook_spill("/x")) == f"/x/aipager-spill-{os.getuid()}"


# ---- writing ---------------------------------------------------------------------

def test_write_then_read_round_trip(folder):
    data = b'{"hook_event_name": "Stop", "big": "' + b"y" * 5000 + b'"}'
    name = hook_spill.write_spill(BASE, data)
    assert name is not None and hook_spill.NAME_RE.fullmatch(name)
    assert _mode(folder / name) == 0o600
    assert (folder / name).read_bytes() == data
    assert hook_spill.read_spill(BASE, name) == (data, "")


def test_read_deletes(folder):
    name = hook_spill.write_spill(BASE, b"{}")
    assert hook_spill.read_spill(BASE, name) == (b"{}", "")
    assert os.listdir(folder) == []
    assert hook_spill.read_spill(BASE, name) == (None, "missing")


def test_write_never_creates_the_folder(tmp_path):
    assert hook_spill.write_spill(BASE, b"{}") is None
    assert not (tmp_path / "spill").exists()


def test_write_refuses_foreign_dir(folder, monkeypatch):
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    assert hook_spill.write_spill(BASE, b"{}") is None
    assert os.listdir(folder) == []


def test_write_refuses_loose_dir(folder):
    folder.chmod(0o750)
    assert hook_spill.write_spill(BASE, b"{}") is None
    assert os.listdir(folder) == []


def test_write_refuses_symlinked_dir(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir(mode=0o700)
    real.chmod(0o700)
    (tmp_path / "spill").symlink_to(real)
    assert hook_spill.write_spill(BASE, b"{}") is None
    assert os.listdir(real) == []


def test_write_refuses_over_cap(folder, monkeypatch):
    monkeypatch.setattr(hook_spill, "SPILL_MAX_BYTES", 1024)
    assert hook_spill.write_spill(BASE, b"x" * 1025) is None
    assert os.listdir(folder) == []
    assert hook_spill.write_spill(BASE, b"x" * 1024) is not None


def test_write_error_cleans(folder, monkeypatch):
    calls = []

    def _fail(fd, data):
        calls.append(len(data))
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(hook_spill, "_write", _fail)
    assert hook_spill.write_spill(BASE, b"x" * 100) is None
    assert calls == [100]                       # the write was tried
    assert os.listdir(folder) == []


def test_write_never_through_a_planted_name(folder, tmp_path, monkeypatch):
    target = tmp_path / "victim.txt"
    target.write_text("keep me")
    (folder / NAME).symlink_to(target)
    monkeypatch.setattr(hook_spill, "_new_name", lambda: NAME)
    assert hook_spill.write_spill(BASE, b"evil") is None
    assert target.read_text() == "keep me"


# ---- the pointer -----------------------------------------------------------------

def test_pointer_round_trip():
    data = hook_spill.pointer(NAME)
    assert len(data) < 100
    assert hook_spill.parse_pointer(data) == NAME


@pytest.mark.parametrize("data, expected", [
    (b'{"hook_event_name": "Stop", "session": "s"}', None),
    (b'{"type": "statusline", "session": "aipager_spill"}', None),
    (b'[1, 2]', None),
    (b'not json aipager_spill', None),
    (b'{"type": "aipager_spill"}', ""),
    (b'{"type": "aipager_spill", "name": 7}', 7),
    (b'{"type": "aipager_spill", "name": "../x"}', "../x"),
], ids=["event", "other-type", "list", "garbage", "no-name", "int-name", "path-name"])
def test_parse_pointer(data, expected):
    assert hook_spill.parse_pointer(data) == expected


def test_parse_pointer_ignores_long_datagrams():
    data = b'{"type": "aipager_spill", "name": "' + NAME.encode() + b'", "pad": "' \
        + b"p" * 600 + b'"}'
    assert hook_spill.parse_pointer(data) is None


# ---- reading: every refusal ------------------------------------------------------

@pytest.mark.parametrize("name", ["../outside.json", "/abs/ev-" + "a" * 32 + ".json",
                                  "../" + NAME], ids=["dotdot", "abs", "dotdot-valid"])
def test_refuse_outside(folder, tmp_path, name):
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": 1}')
    (tmp_path / NAME).write_text('{"secret": 2}')     # beside the folder, not in it
    assert hook_spill.read_spill(BASE, name) == (None, "bad-name")
    assert outside.read_text() == '{"secret": 1}'
    assert (tmp_path / NAME).read_text() == '{"secret": 2}'


@pytest.mark.parametrize("name", ["ev-" + "A" * 32 + ".json", "ev-abc.json", NAME + "\n",
                                  "x" + NAME, 7, None, ["x"]],
                         ids=["upper", "short", "newline", "prefix", "int", "none", "list"])
def test_refuse_bad_name(folder, name):
    if isinstance(name, str) and "/" not in name:
        planted = _plant(folder, name)
    else:
        planted = None
    kept = _plant(folder)                      # a valid file the bad name must not reach
    assert hook_spill.read_spill(BASE, name) == (None, "bad-name")
    if planted is not None:
        assert planted.exists()               # a name that is not ours is never acted on
    assert kept.exists()


def test_refuse_missing(folder):
    assert hook_spill.read_spill(BASE, NAME) == (None, "missing")


def test_refuse_symlink(folder, tmp_path):
    target = tmp_path / "target.json"
    target.write_text('{"secret": "do not read"}')
    (folder / NAME).symlink_to(target)
    assert hook_spill.read_spill(BASE, NAME) == (None, "symlink")
    assert not os.path.lexists(folder / NAME)      # the link went
    assert target.read_text() == '{"secret": "do not read"}'


def test_refuse_fifo(folder):
    """A FIFO planted at a spill name is refused at once: the daemon's
    open must never wait for a writer (``O_NONBLOCK``), or the event loop
    would hang on it. Run in a daemon thread so a regression fails here
    instead of hanging the suite; a stuck open is released by opening the
    FIFO for writing."""
    fifo = folder / NAME
    os.mkfifo(fifo, 0o600)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(hook_spill.read_spill(BASE, NAME)), daemon=True)
    worker.start()
    worker.join(5)
    stuck = worker.is_alive()
    if stuck:
        try:
            wfd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            pass
        else:
            os.close(wfd)
        worker.join(5)
    assert not stuck, "read_spill blocked opening a FIFO"
    assert result == [(None, "not-regular")]
    assert not os.path.lexists(fifo)


def test_refuse_foreign_file(folder, monkeypatch):
    """A file of another user cannot be made without root: the owner the
    daemon sees comes from the ``_fstat_file`` seam instead."""
    _plant(folder)
    real = os.fstat

    def _foreign(fd):
        st = real(fd)
        return SimpleNamespace(st_mode=st.st_mode, st_uid=os.getuid() + 1,
                               st_size=st.st_size)

    monkeypatch.setattr(hook_spill, "_fstat_file", _foreign)
    assert hook_spill.read_spill(BASE, NAME) == (None, "not-ours")
    assert not (folder / NAME).exists()


def test_refuse_over_cap(folder, monkeypatch):
    monkeypatch.setattr(hook_spill, "SPILL_MAX_BYTES", 1024)
    _plant(folder, body=b"x" * 1025)
    assert hook_spill.read_spill(BASE, NAME) == (None, "too-big")
    assert not (folder / NAME).exists()
    _plant(folder, body=b"x" * 1024)
    assert hook_spill.read_spill(BASE, NAME) == (b"x" * 1024, "")


def test_refuse_foreign_dir(folder, monkeypatch):
    _plant(folder)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    assert hook_spill.read_spill(BASE, NAME) == (None, "folder-not-ours")
    assert (folder / NAME).exists()                 # nothing touched


def test_refuse_loose_dir(folder):
    _plant(folder)
    folder.chmod(0o750)
    assert hook_spill.read_spill(BASE, NAME) == (None, "folder-open-to-others")
    assert (folder / NAME).exists()


def test_refuse_symlinked_dir(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    real.chmod(0o700)
    _plant(real)
    (tmp_path / "spill").symlink_to(real)
    body, why = hook_spill.read_spill(BASE, NAME)
    assert body is None and why.startswith("folder-")
    assert (real / NAME).exists()


def test_remove_spill_only_removes_spill_names(folder):
    other = _plant(folder, "notes.txt")
    hook_spill.remove_spill(BASE, "notes.txt")
    assert other.exists()
    ours = _plant(folder)
    hook_spill.remove_spill(BASE, NAME)
    assert not ours.exists()


# ---- the start-up sweep -----------------------------------------------------------

def test_sweep_old_fresh(folder, tmp_path):
    now = time.time()
    old = _plant(folder, "ev-" + "1" * 32 + ".json")
    fresh = _plant(folder, "ev-" + "2" * 32 + ".json")
    other = _plant(folder, "keep.txt")
    target = tmp_path / "target.txt"
    target.write_text("target")
    link = folder / ("ev-" + "3" * 32 + ".json")
    link.symlink_to(target)
    for path in (old, other):
        os.utime(path, (now - 400, now - 400))
    os.utime(fresh, (now - 200, now - 200))
    os.utime(link, (now - 400, now - 400), follow_symlinks=False)

    assert hook_spill.sweep(BASE, now=now) == 2
    assert not old.exists()
    assert fresh.exists()
    assert other.exists()                          # not a spill name
    assert not os.path.lexists(link)
    assert target.read_text() == "target"


def test_sweep_removes_a_fresh_non_file(folder):
    os.mkfifo(folder / NAME, 0o600)
    assert hook_spill.sweep(BASE) == 1
    assert not os.path.lexists(folder / NAME)


def test_sweep_leaves_a_foreign_folder_alone(folder, monkeypatch):
    old = _plant(folder)
    os.utime(old, (1, 1))
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    assert hook_spill.sweep(BASE) == 0
    assert old.exists()
