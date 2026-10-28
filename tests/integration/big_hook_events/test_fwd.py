"""notify_hook._forward: inline vs spill decision, errno partitions, the
240 KiB ceiling and the 4 MiB cap, clean-up and the SO_SNDBUF raise. Sends
go through the documented seams only."""

from __future__ import annotations

import errno
import json
import os
import socket
import stat
import subprocess
import sys

import pytest

from aipager.dtach import hook_spill, notify_hook
from tests.integration.big_hook_events._kit import (
    SECRET,
    event,
    names,
    own_dir,
    plant,
    point_sockets,
    recording_send,
    spill,
)

CEIL = 245_760
EVENT_ERRNOS = [errno.EMSGSIZE, errno.ENOBUFS]


def _is_pointer(data: bytes) -> bool:
    try:
        obj = json.loads(data)
    except ValueError:
        return False
    return isinstance(obj, dict) and obj.get("type") == "aipager_spill"


@pytest.fixture
def ptr_capture(tmp_path, monkeypatch):
    """Event sends of any size fail with EMSGSIZE; the pointer is recorded."""
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    sent = []

    def send(data, path):
        sent.append(bytes(data))
        if not _is_pointer(data):
            raise OSError(errno.EMSGSIZE, "Message too long")

    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    return sent


# ---- constants and inline ------------------------------------------------

def test_ceiling_value():
    assert notify_hook._INLINE_MAX_BYTES == CEIL


def test_ceiling_under_asyncio():
    import asyncio.selector_events as se

    assert notify_hook._INLINE_MAX_BYTES < se._SelectorDatagramTransport.max_size


def test_sndbuf_off_linux_default():
    assert notify_hook._RAISE_SNDBUF is (not sys.platform.startswith("linux"))


def test_inline_small_true(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    sent = []
    recording_send(monkeypatch, sent)
    assert notify_hook._forward(event(1000)) is True


def test_inline_small_bytes(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    sent = []
    recording_send(monkeypatch, sent)
    data = event(1000)
    notify_hook._forward(data)
    assert sent == [data]


@pytest.mark.parametrize("size", [CEIL - 1, CEIL], ids=["ceil-1", "ceil"])
def test_inline_at_ceiling(tmp_path, monkeypatch, size):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    sent = []
    recording_send(monkeypatch, sent)
    data = event(size)
    notify_hook._forward(data)
    assert sent == [data]


@pytest.mark.parametrize("size", [CEIL + 1, 250_000, 1_000_000],
                         ids=["ceil+1", "250k", "1m"])
def test_over_ceiling_no_inline(tmp_path, monkeypatch, size):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    sent = []
    recording_send(monkeypatch, sent)
    notify_hook._forward(event(size))
    assert [_is_pointer(s) for s in sent] == [True]


def test_over_ceiling_true(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [])
    assert notify_hook._forward(event(CEIL + 1)) is True


def test_inline_writes_no_file(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [])
    notify_hook._forward(event(CEIL))
    assert names(tmp_path) == []


def test_inline_dir_mtime_same(tmp_path, monkeypatch):
    """No create/unlink in the folder for an inline event."""
    point_sockets(monkeypatch, tmp_path)
    d = own_dir(tmp_path)
    os.utime(d, (1_000_000, 1_000_000))
    recording_send(monkeypatch, [])
    notify_hook._forward(event(50_000))
    assert os.stat(d).st_mtime == 1_000_000


# ---- size errnos spill ---------------------------------------------------

@pytest.mark.parametrize("err", EVENT_ERRNOS, ids=["emsgsize", "enobufs"])
def test_size_errno_spills(tmp_path, monkeypatch, err):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [], fail={0: err})
    assert notify_hook._forward(event(5000)) is True


@pytest.mark.parametrize("err", EVENT_ERRNOS, ids=["emsgsize", "enobufs"])
def test_size_errno_pointer(tmp_path, monkeypatch, err):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    sent = []
    recording_send(monkeypatch, sent, fail={0: err})
    notify_hook._forward(event(5000))
    assert _is_pointer(sent[-1])


def test_pointer_format(ptr_capture):
    notify_hook._forward(event(5000))
    obj = json.loads(ptr_capture[-1])
    assert set(obj) == {"type", "name"}


def test_pointer_name_pattern(ptr_capture):
    notify_hook._forward(event(5000))
    assert hook_spill.NAME_RE.match(json.loads(ptr_capture[-1])["name"])


def test_pointer_le_512(ptr_capture):
    notify_hook._forward(event(5000))
    assert len(ptr_capture[-1]) <= 512


def test_file_body(ptr_capture, tmp_path):
    data = event(5000)
    notify_hook._forward(data)
    name = json.loads(ptr_capture[-1])["name"]
    assert (spill(tmp_path) / name).read_bytes() == data


def test_file_mode(ptr_capture, tmp_path):
    notify_hook._forward(event(5000))
    name = json.loads(ptr_capture[-1])["name"]
    assert stat.S_IMODE(os.lstat(spill(tmp_path) / name).st_mode) == 0o600


def test_spill_at_cap(ptr_capture, tmp_path):
    notify_hook._forward(event(hook_spill.SPILL_MAX_BYTES))
    assert len(names(tmp_path)) == 1


def test_over_cap_false(ptr_capture):
    assert notify_hook._forward(event(hook_spill.SPILL_MAX_BYTES + 1)) is False


def test_over_cap_no_file(ptr_capture, tmp_path):
    notify_hook._forward(event(hook_spill.SPILL_MAX_BYTES + 1))
    assert names(tmp_path) == []


def test_over_cap_no_pointer(ptr_capture):
    notify_hook._forward(event(hook_spill.SPILL_MAX_BYTES + 1))
    assert not any(_is_pointer(s) for s in ptr_capture)


def test_over_cap_debug_clean(ptr_capture, monkeypatch, capsys):
    monkeypatch.setenv("AIPAGER_DEBUG", "1")
    notify_hook._forward(event(hook_spill.SPILL_MAX_BYTES + 1))
    assert SECRET not in capsys.readouterr().err


# ---- other errnos never spill --------------------------------------------

OTHER = [errno.ECONNREFUSED, errno.ENOENT, errno.EACCES, errno.EPERM,
         errno.ENOTCONN, errno.EAGAIN]
OTHER_IDS = ["econnrefused", "enoent", "eacces", "eperm", "enotconn", "eagain"]


@pytest.mark.parametrize("err", OTHER, ids=OTHER_IDS)
def test_other_errno_false(tmp_path, monkeypatch, err):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [], fail={0: err})
    assert notify_hook._forward(event(5000)) is False


@pytest.mark.parametrize("err", OTHER, ids=OTHER_IDS)
def test_other_errno_no_file(tmp_path, monkeypatch, err):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [], fail={0: err})
    notify_hook._forward(event(5000))
    assert names(tmp_path) == []


@pytest.mark.parametrize("err", OTHER, ids=OTHER_IDS)
def test_other_errno_one_send(tmp_path, monkeypatch, err):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    sent = []
    recording_send(monkeypatch, sent, fail={0: err})
    notify_hook._forward(event(5000))
    assert len(sent) == 1


def test_daemon_down_real(tmp_path, monkeypatch):
    """No socket at the path at all: the real send fails, nothing spills."""
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    assert notify_hook._forward(event(5000)) is False


# ---- pointer-send failure cleans up --------------------------------------

@pytest.mark.parametrize("err", [errno.ECONNREFUSED, errno.ENOENT, errno.ENOBUFS,
                                 errno.EMSGSIZE],
                         ids=["econnrefused", "enoent", "enobufs", "emsgsize"])
def test_ptr_fail_false(tmp_path, monkeypatch, err):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE, 1: err})
    assert notify_hook._forward(event(5000)) is False


@pytest.mark.parametrize("err", [errno.ECONNREFUSED, errno.ENOENT, errno.ENOBUFS,
                                 errno.EMSGSIZE],
                         ids=["econnrefused", "enoent", "enobufs", "emsgsize"])
def test_ptr_fail_deletes(tmp_path, monkeypatch, err):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE, 1: err})
    notify_hook._forward(event(5000))
    assert names(tmp_path) == []


def test_ptr_fail_ceiling_deletes(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [], fail={0: errno.ECONNREFUSED})
    notify_hook._forward(event(CEIL + 1))
    assert names(tmp_path) == []


def test_ptr_fail_keeps_others(tmp_path, monkeypatch):
    """Only its own file goes: another pending spill file stays."""
    point_sockets(monkeypatch, tmp_path)
    other = plant(tmp_path, b"{}")
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE, 1: errno.ECONNREFUSED})
    notify_hook._forward(event(5000))
    assert names(tmp_path) == [other]


def test_ptr_unexpected_exc(tmp_path, monkeypatch):
    """Error guessing: a non-OSError on the pointer send is 'not sent'."""
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    calls = []

    def send(data, path):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(errno.EMSGSIZE, "x")
        raise ValueError("odd")

    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    ok = notify_hook._forward(event(5000))
    assert (ok, names(tmp_path)) == (False, [])


def test_ptr_fail_debug_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AIPAGER_DEBUG", "1")
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE, 1: errno.ECONNREFUSED})
    notify_hook._forward(event(5000))
    assert SECRET not in capsys.readouterr().err


# ---- folder unusable: no spill -------------------------------------------

def test_no_dir_false(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    assert notify_hook._forward(event(5000)) is False


def test_no_dir_not_created(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    notify_hook._forward(event(5000))
    assert not os.path.lexists(spill(tmp_path))


def test_no_dir_no_pointer(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    sent = []
    recording_send(monkeypatch, sent, fail={0: errno.EMSGSIZE})
    notify_hook._forward(event(5000))
    assert not any(_is_pointer(s) for s in sent)


def test_loose_dir_no_spill(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    os.chmod(spill(tmp_path), 0o770)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    notify_hook._forward(event(5000))
    assert names(tmp_path) == []


def test_foreign_dir_no_spill(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    assert notify_hook._forward(event(5000)) is False


def test_linked_dir_no_spill(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    spill(tmp_path).symlink_to(real)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    notify_hook._forward(event(5000))
    assert os.listdir(real) == []


def test_planted_name_false(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    name = plant(tmp_path, b"planted")
    monkeypatch.setattr(hook_spill, "_new_name", lambda: name)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    assert notify_hook._forward(event(5000)) is False


def test_planted_name_kept(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    name = plant(tmp_path, b"planted")
    monkeypatch.setattr(hook_spill, "_new_name", lambda: name)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    notify_hook._forward(event(5000))
    assert (spill(tmp_path) / name).read_bytes() == b"planted"


def test_planted_link_target_kept(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    d = own_dir(tmp_path)
    target = tmp_path / "victim"
    target.write_bytes(b"orig")
    name = "ev-" + "1" * 32 + ".json"
    (d / name).symlink_to(target)
    monkeypatch.setattr(hook_spill, "_new_name", lambda: name)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    notify_hook._forward(event(5000))
    assert target.read_bytes() == b"orig"


def test_write_fail_false(tmp_path, monkeypatch):
    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)

    def boom(fd, data):
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(hook_spill, "_write", boom)
    recording_send(monkeypatch, [], fail={0: errno.EMSGSIZE})
    ok = notify_hook._forward(event(5000))
    assert (ok, names(tmp_path)) == (False, [])


# ---- SO_SNDBUF and socket hygiene ----------------------------------------

class _Sock:
    def __init__(self, raise_opt=False):
        self.opts, self.sent, self.closed, self.raise_opt = [], [], False, raise_opt

    def setsockopt(self, *a):
        self.opts.append(a)
        if self.raise_opt:
            raise OSError(errno.EINVAL, "no")

    def sendto(self, data, addr):
        self.sent.append((bytes(data), addr))
        return len(data)

    def close(self):
        self.closed = True

    def __getattr__(self, name):  # settimeout, setblocking, fileno...
        return lambda *a, **k: None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _with_sock(monkeypatch, tmp_path, sock, raise_flag):
    point_sockets(monkeypatch, tmp_path)
    monkeypatch.setattr(notify_hook, "_dgram_socket", lambda: sock)
    monkeypatch.setattr(notify_hook, "_RAISE_SNDBUF", raise_flag)


def test_sndbuf_raised(tmp_path, monkeypatch):
    s = _Sock()
    _with_sock(monkeypatch, tmp_path, s, True)
    notify_hook._forward(event(1000))
    assert (socket.SOL_SOCKET, socket.SO_SNDBUF, CEIL) in s.opts


def test_sndbuf_not_raised(tmp_path, monkeypatch):
    s = _Sock()
    _with_sock(monkeypatch, tmp_path, s, False)
    notify_hook._forward(event(1000))
    assert not any(o[:2] == (socket.SOL_SOCKET, socket.SO_SNDBUF) for o in s.opts)


def test_sndbuf_error_ignored(tmp_path, monkeypatch):
    s = _Sock(raise_opt=True)
    _with_sock(monkeypatch, tmp_path, s, True)
    assert notify_hook._forward(event(1000)) is True


def test_socket_closed_ok(tmp_path, monkeypatch):
    s = _Sock()
    _with_sock(monkeypatch, tmp_path, s, False)
    notify_hook._forward(event(1000))
    assert s.closed


def test_socket_closed_on_error(tmp_path, monkeypatch):
    s = _Sock()

    def bad(data, addr):
        raise OSError(errno.ECONNREFUSED, "x")

    s.sendto = bad
    _with_sock(monkeypatch, tmp_path, s, False)
    notify_hook._forward(event(1000))
    assert s.closed


def test_send_to_socket_path(tmp_path, monkeypatch):
    s = _Sock()
    _with_sock(monkeypatch, tmp_path, s, False)
    notify_hook._forward(event(1000))
    assert s.sent[0][1] == str(tmp_path / "s.sock")


# ---- lazy import (fresh interpreter) -------------------------------------

_CHILD = r"""
import json, socket, sys
p = sys.argv[1]
srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
srv.bind(p)
from aipager.dtach import notify_hook as n
n.SOCKET_PATH = p
ok = n._forward(json.dumps({"hook_event_name": "Stop", "session": "claude-bh",
                            "pad": "x" * 5000}).encode())
srv.close()
print(json.dumps([ok, "aipager.dtach.hook_spill" in sys.modules]))
"""


def test_small_no_spill_import(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AIPAGER")}
    env.update(HOME=str(tmp_path), XDG_RUNTIME_DIR=str(tmp_path),
               XDG_CONFIG_HOME=str(tmp_path / "cfg"), XDG_STATE_HOME=str(tmp_path / "st"),
               XDG_DATA_HOME=str(tmp_path / "dat"),
               PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    r = subprocess.run([sys.executable, "-c", _CHILD, str(tmp_path / "c.sock")], env=env, capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert json.loads(r.stdout.strip().splitlines()[-1]) == [True, False], r.stderr
