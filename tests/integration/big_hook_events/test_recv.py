"""HookReceiver side: pointer resolution at arrival, refusals logged without
content, the spill INFO line, the truncation warning, start-up folder
handling and sweep, the SO_RCVBUF raise."""

from __future__ import annotations

import json
import logging
import os
import socket
import stat
import time
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver, hook_spill
from aipager.state import SessionRegistry, Status
from tests.integration.big_hook_events._kit import (
    SECRET,
    foreign_fstat,
    make_receiver,
    names,
    own_dir,
    plant,
    spill,
)

LOG = "aipager.dtach.hook_receiver"


def _hex():
    return os.urandom(16).hex()


def _pr(session="claude-dc", **extra):
    return json.dumps({"hook_event_name": "PermissionRequest", "session": session,
                       "tool_name": "Write",
                       "tool_input": {"file_path": "/home/alice/x.txt",
                                      "content": SECRET * 10},
                       **extra}).encode()


async def _await(aw):
    return await aw


# ---- resolution at arrival ------------------------------------------------

def test_counted_on_arrival(tmp_path, monkeypatch, run_async):
    recv, _got = make_receiver(monkeypatch, tmp_path)
    name = plant(tmp_path, _pr())
    aw = recv._on_datagram_counted(hook_spill.pointer(name))
    pending = hook_receiver.dialog_datagram_pending("claude-dc")
    run_async(_await(aw))
    assert pending is True


def test_count_cleared(tmp_path, monkeypatch, run_async):
    recv, _got = make_receiver(monkeypatch, tmp_path)
    name = plant(tmp_path, _pr())
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    assert hook_receiver.dialog_datagram_pending("claude-dc") is False


def test_file_gone_on_arrival(tmp_path, monkeypatch, run_async):
    """Read synchronously in the datagram callback, before any await."""
    recv, _got = make_receiver(monkeypatch, tmp_path)
    name = plant(tmp_path, _pr())
    aw = recv._on_datagram_counted(hook_spill.pointer(name))
    gone = names(tmp_path) == []
    run_async(_await(aw))
    assert gone


def test_resolved_bytes_handled(tmp_path, monkeypatch, run_async):
    recv, got = make_receiver(monkeypatch, tmp_path)
    body = _pr()
    name = plant(tmp_path, body)
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    assert got == [json.loads(body)]


def test_inline_still_handled(tmp_path, monkeypatch, run_async):
    recv, got = make_receiver(monkeypatch, tmp_path)
    own_dir(tmp_path)
    run_async(_await(recv._on_datagram_counted(_pr())))
    assert got == [json.loads(_pr())]


def test_nested_pointer_not_followed(tmp_path, monkeypatch, run_async):
    """A spill file that itself holds a pointer is not resolved again."""
    recv, got = make_receiver(monkeypatch, tmp_path, record=False)
    inner = plant(tmp_path, _pr())
    outer = plant(tmp_path, hook_spill.pointer(inner))
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(outer))))
    assert inner in names(tmp_path)


# ---- spilled PermissionRequest through the real handler -------------------

def _real_recv(tmp_path, monkeypatch):
    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", tmp_path / "st.json")
    registry = SessionRegistry()
    notify = AsyncMock()
    return registry, hook_receiver.HookReceiver(registry, notify), notify


def test_spilled_pr_prompt(tmp_path, monkeypatch, run_async):
    _reg, recv, notify = _real_recv(tmp_path, monkeypatch)
    name = plant(tmp_path, _pr())
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    assert notify.await_args.args[1] == "permission_prompt"


def test_spilled_pr_tool(tmp_path, monkeypatch, run_async):
    _reg, recv, notify = _real_recv(tmp_path, monkeypatch)
    name = plant(tmp_path, _pr())
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    assert notify.await_args.args[2]["tool_info"]["name"] == "Write"


def test_spilled_pr_interactive(tmp_path, monkeypatch, run_async):
    reg, recv, _n = _real_recv(tmp_path, monkeypatch)
    name = plant(tmp_path, _pr())
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    assert reg.get("claude-dc").status == Status.INTERACTIVE


def test_spilled_same_as_inline(tmp_path, monkeypatch, run_async):
    """Differential: the notify call of a spilled event equals the inline one."""
    _r1, inline, n1 = _real_recv(tmp_path, monkeypatch)
    run_async(_await(inline._on_datagram_counted(_pr())))
    _r2, spilled, n2 = _real_recv(tmp_path, monkeypatch)
    name = plant(tmp_path, _pr())
    run_async(_await(spilled._on_datagram_counted(hook_spill.pointer(name))))
    def shape(n):
        out = []
        for c in n.await_args_list:
            info = dict(c.args[2].get("tool_info") or {})
            order = {k: v for k, v in (info.pop("order", None) or {}).items()
                     if not isinstance(v, float)}  # arrival clocks differ
            out.append((c.args[1], sorted(c.args[2]), info, order))
        return out

    assert shape(n2) == shape(n1)


def test_spilled_info_line(tmp_path, monkeypatch, run_async, caplog):
    caplog.set_level(logging.DEBUG, logger=LOG)
    _reg, recv, _n = _real_recv(tmp_path, monkeypatch)
    body = _pr()
    name = plant(tmp_path, body)
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    lines = [r.getMessage() for r in caplog.records if "spill file" in r.getMessage()]
    assert len(lines) == 1


def test_spilled_info_size(tmp_path, monkeypatch, run_async, caplog):
    caplog.set_level(logging.DEBUG, logger=LOG)
    _reg, recv, _n = _real_recv(tmp_path, monkeypatch)
    body = _pr()
    name = plant(tmp_path, body)
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    line = next(r.getMessage() for r in caplog.records if "spill file" in r.getMessage())
    assert str(len(body)) in line


def test_spilled_info_no_payload(tmp_path, monkeypatch, run_async, caplog):
    caplog.set_level(logging.DEBUG, logger=LOG)
    _reg, recv, _n = _real_recv(tmp_path, monkeypatch)
    name = plant(tmp_path, _pr())
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    spill_lines = [r.getMessage() for r in caplog.records if "spill" in r.getMessage()]
    assert not any(SECRET in m for m in spill_lines)


def test_inline_no_info_line(tmp_path, monkeypatch, run_async, caplog):
    caplog.set_level(logging.DEBUG, logger=LOG)
    _reg, recv, _n = _real_recv(tmp_path, monkeypatch)
    run_async(_await(recv._on_datagram_counted(_pr())))
    assert not any("spill file" in r.getMessage() for r in caplog.records)


# ---- refusals -------------------------------------------------------------

def _case(kind, tmp_path, monkeypatch):
    """Return the pointer datagram for refusal *kind*."""
    body = _pr()
    if kind == "dotdot":
        (tmp_path / "x.json").write_bytes(body)
        own_dir(tmp_path)
        return hook_spill.pointer("../x.json")
    if kind == "abs":
        own_dir(tmp_path)
        p = tmp_path / ("ev-" + _hex() + ".json")
        p.write_bytes(body)
        return hook_spill.pointer(str(p))
    if kind == "badname":
        d = own_dir(tmp_path)
        (d / "notes.json").write_bytes(body)
        return hook_spill.pointer("notes.json")
    if kind == "noname":
        own_dir(tmp_path)
        return json.dumps({"type": "aipager_spill"}).encode()
    if kind == "intname":
        own_dir(tmp_path)
        return json.dumps({"type": "aipager_spill", "name": 5}).encode()
    if kind == "missing":
        own_dir(tmp_path)
        return hook_spill.pointer("ev-" + _hex() + ".json")
    if kind == "symlink":
        d = own_dir(tmp_path)
        (tmp_path / "t.json").write_bytes(body)
        name = "ev-" + _hex() + ".json"
        (d / name).symlink_to(tmp_path / "t.json")
        return hook_spill.pointer(name)
    if kind == "foreign":
        name = plant(tmp_path, body)
        foreign_fstat(monkeypatch)
        return hook_spill.pointer(name)
    if kind == "overcap":
        monkeypatch.setattr(hook_spill, "SPILL_MAX_BYTES", 64)
        return hook_spill.pointer(plant(tmp_path, body))
    if kind == "fifo":
        d = own_dir(tmp_path)
        name = "ev-" + _hex() + ".json"
        os.mkfifo(d / name, 0o600)
        return hook_spill.pointer(name)
    if kind == "loosedir":
        name = plant(tmp_path, body)
        os.chmod(spill(tmp_path), 0o750)
        return hook_spill.pointer(name)
    if kind == "foreigndir":
        name = plant(tmp_path, body)
        monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
        return hook_spill.pointer(name)
    if kind == "nodir":
        return hook_spill.pointer("ev-" + _hex() + ".json")
    raise AssertionError(kind)


KINDS = ["dotdot", "abs", "badname", "noname", "intname", "missing", "symlink",
         "foreign", "overcap", "fifo", "loosedir", "foreigndir", "nodir"]


@pytest.mark.parametrize("kind", KINDS)
def test_refused_not_handled(tmp_path, monkeypatch, run_async, kind):
    recv, got = make_receiver(monkeypatch, tmp_path)
    ptr = _case(kind, tmp_path, monkeypatch)
    run_async(_await(recv._on_datagram_counted(ptr)))
    assert got == []


@pytest.mark.parametrize("kind", KINDS)
def test_refused_warns(tmp_path, monkeypatch, run_async, caplog, kind):
    caplog.set_level(logging.DEBUG, logger=LOG)
    recv, _got = make_receiver(monkeypatch, tmp_path)
    ptr = _case(kind, tmp_path, monkeypatch)
    run_async(_await(recv._on_datagram_counted(ptr)))
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("kind", KINDS)
def test_refused_no_content(tmp_path, monkeypatch, run_async, caplog, kind):
    caplog.set_level(logging.DEBUG)
    recv, _got = make_receiver(monkeypatch, tmp_path)
    ptr = _case(kind, tmp_path, monkeypatch)
    run_async(_await(recv._on_datagram_counted(ptr)))
    assert SECRET not in caplog.text


@pytest.mark.parametrize("kind", KINDS)
def test_refused_no_count(tmp_path, monkeypatch, run_async, kind):
    recv, _got = make_receiver(monkeypatch, tmp_path)
    ptr = _case(kind, tmp_path, monkeypatch)
    aw = recv._on_datagram_counted(ptr)
    pending = hook_receiver.dialog_datagram_pending("claude-dc")
    run_async(_await(aw))
    assert pending is False


@pytest.mark.parametrize("kind", ["symlink", "foreign", "overcap", "fifo"])
def test_refused_deleted(tmp_path, monkeypatch, run_async, kind):
    recv, _got = make_receiver(monkeypatch, tmp_path)
    ptr = _case(kind, tmp_path, monkeypatch)
    run_async(_await(recv._on_datagram_counted(ptr)))
    assert names(tmp_path) == []


def test_refused_outside_kept(tmp_path, monkeypatch, run_async):
    recv, _got = make_receiver(monkeypatch, tmp_path)
    ptr = _case("dotdot", tmp_path, monkeypatch)
    run_async(_await(recv._on_datagram_counted(ptr)))
    assert (tmp_path / "x.json").read_bytes() == _pr()


def test_refused_link_target_kept(tmp_path, monkeypatch, run_async):
    recv, _got = make_receiver(monkeypatch, tmp_path)
    ptr = _case("symlink", tmp_path, monkeypatch)
    run_async(_await(recv._on_datagram_counted(ptr)))
    assert (tmp_path / "t.json").read_bytes() == _pr()


def test_refused_long_name_cut(tmp_path, monkeypatch, run_async, caplog):
    caplog.set_level(logging.DEBUG, logger=LOG)
    recv, _got = make_receiver(monkeypatch, tmp_path)
    own_dir(tmp_path)
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer("q" * 300))))
    assert "q" * 81 not in caplog.text


def test_refused_dedup_free(tmp_path, monkeypatch, run_async):
    """Two refused pointers do not stop a later good one being handled."""
    recv, got = make_receiver(monkeypatch, tmp_path)
    own_dir(tmp_path)
    bad = hook_spill.pointer("ev-" + _hex() + ".json")
    run_async(_await(recv._on_datagram_counted(bad)))
    run_async(_await(recv._on_datagram_counted(bad)))
    name = plant(tmp_path, _pr())
    run_async(_await(recv._on_datagram_counted(hook_spill.pointer(name))))
    assert len(got) == 1


# ---- truncation warning ---------------------------------------------------

def _trunc(tmp_path, monkeypatch, run_async, caplog, size):
    caplog.set_level(logging.DEBUG, logger=LOG)
    recv, _got = make_receiver(monkeypatch, tmp_path)
    run_async(_await(recv._on_datagram_counted(b"x" * size)))
    return [r for r in caplog.records
            if r.levelno == logging.WARNING and "cut short" in r.getMessage()]


def test_trunc_at_256k(tmp_path, monkeypatch, run_async, caplog):
    assert len(_trunc(tmp_path, monkeypatch, run_async, caplog, 262_144)) == 1


def test_trunc_over_256k(tmp_path, monkeypatch, run_async, caplog):
    assert len(_trunc(tmp_path, monkeypatch, run_async, caplog, 300_000)) == 1


def test_trunc_under_256k(tmp_path, monkeypatch, run_async, caplog):
    assert _trunc(tmp_path, monkeypatch, run_async, caplog, 262_143) == []


def test_trunc_no_content(tmp_path, monkeypatch, run_async, caplog):
    caplog.set_level(logging.DEBUG, logger=LOG)
    recv, _got = make_receiver(monkeypatch, tmp_path)
    data = (SECRET * 20000).encode()[:262_144]
    run_async(_await(recv._on_datagram_counted(data)))
    assert SECRET not in caplog.text


# ---- start-up -------------------------------------------------------------

def _start(recv, run_async):
    async def go():
        await recv.start()
        recv.stop()

    run_async(go())


def test_start_creates_0700(tmp_path, monkeypatch, run_async):
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    assert stat.S_IMODE(os.lstat(spill(tmp_path)).st_mode) == 0o700


def test_start_tightens(tmp_path, monkeypatch, run_async):
    spill(tmp_path).mkdir()
    os.chmod(spill(tmp_path), 0o777)
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    assert stat.S_IMODE(os.lstat(spill(tmp_path)).st_mode) == 0o700


def _aged(tmp_path, age, name=None):
    name = plant(tmp_path, b"{}", name)
    t = time.time() - age
    os.utime(spill(tmp_path) / name, (t, t))
    return name


def test_start_sweeps_old(tmp_path, monkeypatch, run_async):
    old = _aged(tmp_path, 3600)
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    assert old not in names(tmp_path)


def test_start_keeps_fresh(tmp_path, monkeypatch, run_async):
    fresh = _aged(tmp_path, 60)
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    assert fresh in names(tmp_path)


def test_start_keeps_other(tmp_path, monkeypatch, run_async):
    _aged(tmp_path, 3600, name="keep.txt")
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    assert "keep.txt" in names(tmp_path)


def test_start_foreign_warns(tmp_path, monkeypatch, run_async, caplog):
    caplog.set_level(logging.DEBUG, logger=LOG)
    own_dir(tmp_path)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    assert any(r.levelno == logging.WARNING and "spill" in r.getMessage()
               for r in caplog.records)


def test_start_foreign_still_binds(tmp_path, monkeypatch, run_async):
    own_dir(tmp_path)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    recv, _g = make_receiver(monkeypatch, tmp_path)
    bound = []

    async def go():
        await recv.start()
        bound.append(os.path.exists(tmp_path / "s.sock"))
        recv.stop()

    run_async(go())
    assert bound == [True]


def test_start_foreign_not_swept(tmp_path, monkeypatch, run_async):
    old = _aged(tmp_path, 3600)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    assert old in names(tmp_path)


def test_start_symlink_dir_warns(tmp_path, monkeypatch, run_async, caplog):
    caplog.set_level(logging.DEBUG, logger=LOG)
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    spill(tmp_path).symlink_to(real)
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    assert any(r.levelno == logging.WARNING and "spill" in r.getMessage()
               for r in caplog.records)


# ---- SO_RCVBUF ------------------------------------------------------------

def _default_rcvbuf():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        return s.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    finally:
        s.close()


def _rcvbuf_after_start(tmp_path, monkeypatch, run_async, flag):
    monkeypatch.setattr(hook_receiver, "_RAISE_RCVBUF", flag)
    seen = []
    real = hook_receiver._raise_rcvbuf

    def spy(transport):
        real(transport)
        seen.append(transport.get_extra_info("socket")
                    .getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))

    monkeypatch.setattr(hook_receiver, "_raise_rcvbuf", spy)
    recv, _g = make_receiver(monkeypatch, tmp_path)
    _start(recv, run_async)
    return seen


@pytest.mark.skipif(not os.path.exists("/proc/sys/net/core/rmem_max"),
                    reason="Linux clamp observable only")
def test_rcvbuf_raised(tmp_path, monkeypatch, run_async):
    seen = _rcvbuf_after_start(tmp_path, monkeypatch, run_async, True)
    assert seen and seen[0] > _default_rcvbuf()


def test_rcvbuf_not_raised(tmp_path, monkeypatch, run_async):
    seen = _rcvbuf_after_start(tmp_path, monkeypatch, run_async, False)
    assert all(v == _default_rcvbuf() for v in seen)


def test_rcvbuf_off_linux_default():
    import sys

    assert hook_receiver._RAISE_RCVBUF is (not sys.platform.startswith("linux"))


def test_rcvbuf_bytes():
    assert hook_receiver._RCVBUF_BYTES == 4 * 1024 * 1024
