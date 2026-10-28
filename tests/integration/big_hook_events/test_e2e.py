"""The real hook path (notify_hook.main / _forward, the real send) against a
real HookReceiver bound in tmp_path: big events arrive in full, in order,
the PermissionRequest gets its verdict, a small platform limit still works."""

from __future__ import annotations

import asyncio
import errno
import io
import json
import os
import sys
import threading
import time

import pytest

from aipager.dtach import notify_hook
from tests.integration.big_hook_events._kit import (
    BIG,
    event,
    in_thread,
    make_receiver,
    names,
    own_dir,
    size_limited,
    spill,
    wait_for,
)


def _run_main(monkeypatch, payload, session="claude-bh"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", session)

    def go():
        try:
            notify_hook.main()
        except SystemExit:
            pass

    return go


def _write_payload(tmp_path, name):
    return {"session_id": "s1", "transcript_path": str(tmp_path / "t.jsonl"),
            "cwd": str(tmp_path), "permission_mode": "default",
            "hook_event_name": name, "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "big.txt"), "content": BIG}}


def _reply(addr, rid):
    import socket

    c = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        c.sendto(json.dumps({"v": 1, "request_id": rid,
                             "decision": {"behavior": "allow"}}).encode(), addr)
    finally:
        c.close()


async def _serve(recv, body):
    await recv.start()
    try:
        return await body()
    finally:
        recv.stop()


def _of(got, name):
    return [g for g in got if isinstance(g, dict) and g.get("hook_event_name") == name]


# ---- 300 KB Write PreToolUse and PermissionRequest ------------------------

def _big_pre(tmp_path, monkeypatch, run_async):
    recv, got = make_receiver(monkeypatch, tmp_path)
    go = _run_main(monkeypatch, _write_payload(tmp_path, "PreToolUse"))

    async def body():
        await in_thread(go)
        await wait_for(lambda: _of(got, "PreToolUse"))

    run_async(_serve(recv, body))
    return got


def test_big_pre_arrives(tmp_path, monkeypatch, run_async):
    got = _big_pre(tmp_path, monkeypatch, run_async)
    assert _of(got, "PreToolUse")[0]["tool_input"]["content"] == BIG


def test_big_pre_dir_empty(tmp_path, monkeypatch, run_async):
    _big_pre(tmp_path, monkeypatch, run_async)
    assert names(tmp_path) == []


def _big_pr(tmp_path, monkeypatch, run_async, capsys):
    recv, got = make_receiver(monkeypatch, tmp_path)

    def on_got(msg):
        if msg.get("hook_event_name") == "PermissionRequest" and \
                msg.get("aipager_reply_addr"):
            _reply(msg["aipager_reply_addr"], msg["aipager_request_id"])

    recv._on_got = on_got
    go = _run_main(monkeypatch, _write_payload(tmp_path, "PermissionRequest"))

    async def body():
        await in_thread(go)

    run_async(_serve(recv, body))
    return got, capsys.readouterr().out


def test_pr_full(tmp_path, monkeypatch, run_async, capsys):
    got, _out = _big_pr(tmp_path, monkeypatch, run_async, capsys)
    assert _of(got, "PermissionRequest")[0]["tool_input"]["content"] == BIG


def test_pr_addr(tmp_path, monkeypatch, run_async, capsys):
    got, _out = _big_pr(tmp_path, monkeypatch, run_async, capsys)
    assert _of(got, "PermissionRequest")[0].get("aipager_reply_addr")


def test_pr_ok(tmp_path, monkeypatch, run_async, capsys):
    _got, out = _big_pr(tmp_path, monkeypatch, run_async, capsys)
    dec = json.loads(out.strip().splitlines()[-1])["hookSpecificOutput"]["decision"]
    assert dec == {"behavior": "allow"}


def test_pr_clean(tmp_path, monkeypatch, run_async, capsys):
    _big_pr(tmp_path, monkeypatch, run_async, capsys)
    assert names(tmp_path) == []


def test_pr_nodaemon(tmp_path, monkeypatch, capsys):
    """Pointer cannot be sent (no daemon): no wait for a verdict, no output,
    no file left."""
    from tests.integration.big_hook_events._kit import point_sockets

    point_sockets(monkeypatch, tmp_path)
    own_dir(tmp_path)
    go = _run_main(monkeypatch, _write_payload(tmp_path, "PermissionRequest"))
    t0 = time.monotonic()
    th = threading.Thread(target=go, daemon=True)
    th.start()
    th.join(20)
    elapsed = time.monotonic() - t0
    out = capsys.readouterr().out
    assert (elapsed < 10, out.strip(), names(tmp_path)) == (True, "", [])


# ---- order ---------------------------------------------------------------

def test_order_small_big_small(tmp_path, monkeypatch, run_async):
    recv, got = make_receiver(monkeypatch, tmp_path)
    datas = [event(500, seq="A"), event(300_000, seq="B"), event(500, seq="C")]

    def seq():
        for d in datas:
            assert notify_hook._forward(d)

    async def body():
        await in_thread(seq)
        await wait_for(lambda: len(got) >= 3)

    run_async(_serve(recv, body))
    assert [g.get("seq") for g in got] == ["A", "B", "C"]


def test_order_alternating(tmp_path, monkeypatch, run_async):
    recv, got = make_receiver(monkeypatch, tmp_path)
    datas = [event(300_000 if i % 2 else 400, seq=str(i)) for i in range(8)]

    def seq():
        for d in datas:
            notify_hook._forward(d)

    async def body():
        await in_thread(seq)
        await wait_for(lambda: len(got) >= 8)

    run_async(_serve(recv, body))
    assert [g.get("seq") for g in got] == [str(i) for i in range(8)]


# ---- simulated small platform limit --------------------------------------

def _bsd(tmp_path, monkeypatch, run_async, size, err=errno.EMSGSIZE):
    recv, got = make_receiver(monkeypatch, tmp_path)
    sent: list = []
    size_limited(monkeypatch, 2048, err, record=sent)
    data = event(size)

    async def body():
        ok = await in_thread(notify_hook._forward, data)
        await wait_for(lambda: got)
        return ok

    ok = run_async(_serve(recv, body))
    return ok, data, got, sent


@pytest.mark.parametrize("size", [5_000, 20_000, 50_000], ids=["5k", "20k", "50k"])
def test_bsd_limit_arrives(tmp_path, monkeypatch, run_async, size):
    _ok, data, got, _s = _bsd(tmp_path, monkeypatch, run_async, size)
    assert got == [json.loads(data)]


@pytest.mark.parametrize("size", [5_000, 20_000, 50_000], ids=["5k", "20k", "50k"])
def test_bsd_limit_true(tmp_path, monkeypatch, run_async, size):
    ok, *_ = _bsd(tmp_path, monkeypatch, run_async, size)
    assert ok is True


@pytest.mark.parametrize("size", [5_000, 20_000, 50_000], ids=["5k", "20k", "50k"])
def test_bsd_limit_dir_empty(tmp_path, monkeypatch, run_async, size):
    _bsd(tmp_path, monkeypatch, run_async, size)
    assert names(tmp_path) == []


def test_bsd_limit_one_ok_send(tmp_path, monkeypatch, run_async):
    """Only the pointer fits: of the sends, exactly one is <= 2048 bytes."""
    _ok, _d, _g, sent = _bsd(tmp_path, monkeypatch, run_async, 20_000)
    assert len([s for s in sent if len(s) <= 2048]) == 1


@pytest.mark.parametrize("size", [2000, 2048], ids=["2000", "2048"])
def test_bsd_limit_fits_inline(tmp_path, monkeypatch, run_async, size):
    _ok, _d, _g, sent = _bsd(tmp_path, monkeypatch, run_async, size)
    assert len(sent) == 1


def test_bsd_limit_2049(tmp_path, monkeypatch, run_async):
    _ok, data, got, _s = _bsd(tmp_path, monkeypatch, run_async, 2049)
    assert got == [json.loads(data)]


def test_enobufs_arrives(tmp_path, monkeypatch, run_async):
    _ok, data, got, _s = _bsd(tmp_path, monkeypatch, run_async, 20_000, errno.ENOBUFS)
    assert got == [json.loads(data)]


# ---- inline stays inline (real Linux send) --------------------------------

def _real(tmp_path, monkeypatch, run_async, size):
    recv, got = make_receiver(monkeypatch, tmp_path)
    data = event(size)

    async def body():
        d = spill(tmp_path)
        os.utime(d, (1_000_000, 1_000_000))
        ok = await in_thread(notify_hook._forward, data)
        await wait_for(lambda: got)
        return ok, os.stat(d).st_mtime

    ok, mtime = run_async(_serve(recv, body))
    return ok, data, got, mtime


def test_inline_50k_arrives(tmp_path, monkeypatch, run_async):
    _ok, data, got, _m = _real(tmp_path, monkeypatch, run_async, 50_000)
    assert got == [json.loads(data)]


def test_inline_50k_no_touch(tmp_path, monkeypatch, run_async):
    *_x, mtime = _real(tmp_path, monkeypatch, run_async, 50_000)
    assert mtime == 1_000_000


def _wmem_default():
    try:
        with open("/proc/sys/net/core/wmem_default") as f:
            return int(f.read())
    except OSError:
        return 0


@pytest.mark.skipif(not sys.platform.startswith("linux") or _wmem_default() < 212_992,
                    reason="default Linux wmem only")
def test_inline_212k_no_touch(tmp_path, monkeypatch, run_async):
    """A 212,000-byte event fits the default Linux datagram limit, so it goes
    inline and the spill folder stays untouched. A loaded host can still
    refuse a datagram this big with ENOBUFS (the kernel cannot get a large
    enough block of memory), and then a spill is the correct answer: the
    real send's outcome is recorded through the seam, and only that refusal
    excuses a touched folder."""
    real_send = notify_hook._send_datagram
    sends = []

    def recording(data, path):
        try:
            real_send(data, path)
        except OSError as e:
            sends.append((len(data), e.errno))
            raise
        sends.append((len(data), None))

    monkeypatch.setattr(notify_hook, "_send_datagram", recording)
    ok, data, got, mtime = _real(tmp_path, monkeypatch, run_async, 212_000)
    assert ok
    assert got == [json.loads(data)]
    assert sends and sends[0][0] == len(data)
    first_errno = sends[0][1]
    if first_errno == errno.ENOBUFS:
        assert len(sends) == 2 and sends[1][1] is None and sends[1][0] < len(data)
    else:
        assert sends == [(len(data), None)]
        assert mtime == 1_000_000


def test_230k_arrives(tmp_path, monkeypatch, run_async):
    """Over the Linux default datagram limit, under the 240 KiB ceiling."""
    _ok, data, got, _m = _real(tmp_path, monkeypatch, run_async, 230_000)
    assert got == [json.loads(data)]


def test_230k_dir_empty(tmp_path, monkeypatch, run_async):
    _real(tmp_path, monkeypatch, run_async, 230_000)
    assert names(tmp_path) == []


def test_3m_arrives(tmp_path, monkeypatch, run_async):
    _ok, data, got, _m = _real(tmp_path, monkeypatch, run_async, 3_000_000)
    assert got == [json.loads(data)]


# ---- concurrency (error guessing) ----------------------------------------

def test_concurrent_spills(tmp_path, monkeypatch, run_async):
    recv, got = make_receiver(monkeypatch, tmp_path)
    datas = [event(260_000, seq=str(i), session=f"claude-c{i}") for i in range(8)]

    async def body():
        await asyncio.gather(*(in_thread(notify_hook._forward, d) for d in datas))
        await wait_for(lambda: len(got) >= 8)

    run_async(_serve(recv, body))
    assert sorted(g["seq"] for g in got) == [str(i) for i in range(8)]


def test_concurrent_dir_empty(tmp_path, monkeypatch, run_async):
    recv, got = make_receiver(monkeypatch, tmp_path)
    datas = [event(260_000, seq=str(i)) for i in range(8)]

    async def body():
        await asyncio.gather(*(in_thread(notify_hook._forward, d) for d in datas))
        await wait_for(lambda: len(got) >= 8)

    run_async(_serve(recv, body))
    assert names(tmp_path) == []
