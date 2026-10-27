"""Hook events too big for one datagram still reach the daemon (8.138).

The hook's ``_forward`` sends an event inline when it fits and through a
spill file plus a pointer datagram when it does not. The end-to-end tests
run the real hook (``notify_hook.main`` or ``_forward``) against a real
``HookReceiver`` bound in ``tmp_path``. A platform limit smaller than this
machine's is simulated through the ``_send_datagram`` seam, never by
patching ``socket``.

Test names stay short: the hook's reply socket,
``tmp_path/aipager-reply-<32 hex>.sock``, must fit AF_UNIX's 107 bytes.
"""

from __future__ import annotations

import asyncio
import errno
import io
import json
import os
import socket
import stat
import sys
import threading

import pytest

from aipager.dtach import hook_receiver, hook_reply, hook_spill, notify_hook
from aipager.state import SessionRegistry

SESSION = "claude-big"
REAL_SEND = notify_hook._send_datagram


@pytest.fixture
def sock_path(tmp_path, monkeypatch):
    path = str(tmp_path / "s.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", path)
    monkeypatch.setattr(hook_receiver, "SOCKET_PATH", path)
    return path


@pytest.fixture
def folder(tmp_path):
    assert hook_spill.ensure_dir("/unused") == ""
    return tmp_path / "spill"


def _event(event: str = "PreToolUse", size: int = 0, **extra) -> dict:
    data = {"hook_event_name": event, "session": SESSION, "tool_name": "Write",
            "tool_input": {"file_path": "/home/alice/big.txt", "content": "a" * size}}
    data.update(extra)
    return data


def _enc(data: dict) -> bytes:
    return json.dumps(data).encode()


class _Sent:
    """A ``_send_datagram`` stand-in: records every datagram, raises the
    errno *fail(data)* gives (if any), else hands it to *then*."""

    def __init__(self, fail=lambda data: None, then=None):
        self.fail = fail
        self.then = then
        self.data: list[bytes] = []

    def __call__(self, data: bytes, path: str) -> None:
        self.data.append(data)
        code = self.fail(data)
        if code:
            raise OSError(code, os.strerror(code))
        if self.then is not None:
            self.then(data, path)


def _bsd(limit: int = 2048):
    """A platform whose datagrams stop at *limit* bytes (macOS's default)."""
    return _Sent(lambda data: errno.EMSGSIZE if len(data) > limit else None, REAL_SEND)


def _run_with_receiver(run_async, send, expect: int, *, on_datagram=None):
    """Start a real HookReceiver, run *send* in a thread, and return the
    events it handed on (decoded) once *expect* arrived (or 10 s passed)."""
    got: list[dict] = []
    kws: list[dict] = []

    async def scenario():
        recv = hook_receiver.HookReceiver(SessionRegistry(), None)

        async def record(data, **kw):
            msg = json.loads(data)
            got.append(msg)
            kws.append(kw)
            if on_datagram is not None:
                on_datagram(msg)

        recv._on_datagram = record
        await recv.start()
        result = []
        thread = threading.Thread(target=lambda: result.append(send()))
        try:
            thread.start()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            while (thread.is_alive() or len(got) < expect) and loop.time() < deadline:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)       # nothing more may come
        finally:
            thread.join(timeout=10)
            recv.stop()
        return result

    result = run_async(scenario())
    return got, kws, result


def _main_in(monkeypatch, payload: dict):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)
    return notify_hook.main


# ---- the real path end to end ----------------------------------------------------------

def test_big_pre(run_async, sock_path, tmp_path, monkeypatch):
    payload = _event(size=300_000, tool_use_id="toolu_1")
    main = _main_in(monkeypatch, payload)
    got, kws, _ = _run_with_receiver(run_async, main, 2)
    # With no policy for the session the hook then denies the call and
    # says so (safety_blocked), as for any PreToolUse.
    assert [g["hook_event_name"] for g in got] == ["PreToolUse", "safety_blocked"]
    assert got[0]["tool_input"] == payload["tool_input"]
    assert got[0]["session"] == SESSION
    assert kws[0]["spilled"] > 300_000
    assert os.listdir(tmp_path / "spill") == []


def test_big_pr(run_async, sock_path, tmp_path, monkeypatch, capsys):
    """A spilled PermissionRequest keeps its reply channel: the daemon's
    verdict reaches the waiting hook, which prints it for Claude Code."""
    monkeypatch.setenv("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", "10")
    payload = _event("PermissionRequest", size=300_000)
    main = _main_in(monkeypatch, payload)
    sent = []

    def answer(msg):
        sent.append(hook_reply.send_decision(
            {"addr": msg["aipager_reply_addr"], "request_id": msg["aipager_request_id"]},
            {"behavior": "allow"}))

    got, kws, _ = _run_with_receiver(run_async, main, 1, on_datagram=answer)
    assert [g["hook_event_name"] for g in got] == ["PermissionRequest"]
    assert got[0]["tool_input"] == payload["tool_input"]
    assert kws[0]["spilled"] > 300_000
    assert sent == [True]
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                          "decision": {"behavior": "allow"}}}
    assert os.listdir(tmp_path / "spill") == []


def test_kernel_limit(run_async, sock_path, tmp_path):
    """Between this machine's own datagram limit (about 208 KiB on a
    default Linux) and the hook's ceiling: inline or spilled, it arrives."""
    data = _enc(_event(size=230_000))
    assert len(data) <= notify_hook._INLINE_MAX_BYTES
    got, _, result = _run_with_receiver(run_async, lambda: notify_hook._forward(data), 1)
    assert result == [True]
    assert got == [json.loads(data)]
    assert os.listdir(tmp_path / "spill") == []


def test_inline_no_spill(run_async, sock_path, tmp_path, monkeypatch):
    writes = []
    real = hook_spill.write_spill
    monkeypatch.setattr(hook_spill, "write_spill",
                        lambda base, data: writes.append(len(data)) or real(base, data))
    data = _enc(_event(size=50_000))
    got, kws, result = _run_with_receiver(run_async, lambda: notify_hook._forward(data), 1)
    assert result == [True]
    assert got == [json.loads(data)]
    assert kws == [{}]                       # handed on as an inline datagram
    assert writes == []
    assert os.listdir(tmp_path / "spill") == []


def test_order_small_big_small(run_async, sock_path):
    events = [_event("PreToolUse", size=10, tool_use_id="A"),
              _event("PreToolUse", size=300_000, tool_use_id="B"),
              _event("PostToolUse", size=10, tool_use_id="C")]

    def send():
        return [notify_hook._forward(_enc(e)) for e in events]

    got, kws, result = _run_with_receiver(run_async, send, 3)
    assert result == [[True, True, True]]
    assert [g["tool_use_id"] for g in got] == ["A", "B", "C"]
    assert got[1] == events[1]
    assert [bool(k) for k in kws] == [False, True, False]


@pytest.mark.parametrize("size", [5_000, 20_000, 50_000], ids=["5k", "20k", "50k"])
def test_bsd_limit(run_async, sock_path, tmp_path, monkeypatch, size):
    send = _bsd()
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    data = _enc(_event(size=size))
    got, _, result = _run_with_receiver(run_async, lambda: notify_hook._forward(data), 1)
    assert result == [True]
    assert got == [json.loads(data)]
    # The event was refused inline, then only its pointer went.
    assert len(send.data) == 2 and send.data[0] == data
    assert hook_spill.parse_pointer(send.data[1])
    assert os.listdir(tmp_path / "spill") == []


def test_enobufs_spills(run_async, sock_path, tmp_path, monkeypatch):
    """macOS: a receive buffer too full or too small refuses with ENOBUFS."""
    send = _Sent(lambda data: errno.ENOBUFS if len(data) > 1000 else None, REAL_SEND)
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    data = _enc(_event(size=3_000))
    got, _, result = _run_with_receiver(run_async, lambda: notify_hook._forward(data), 1)
    assert result == [True]
    assert got == [json.loads(data)]
    assert os.listdir(tmp_path / "spill") == []


# ---- the hook's own guards ---------------------------------------------------------------

def test_over_ceiling_no_send(sock_path, folder, monkeypatch):
    send = _Sent()                                # accepts any size
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    data = _enc(_event(size=250_000))
    assert notify_hook._forward(data) is True
    assert [len(d) < 512 for d in send.data] == [True]      # only a pointer went
    name = hook_spill.parse_pointer(send.data[0])
    assert (folder / name).read_bytes() == data


@pytest.mark.parametrize("code", [errno.ECONNREFUSED, errno.ENOENT, errno.EACCES],
                         ids=["refused", "noent", "acces"])
def test_other_errno_no_spill(sock_path, folder, monkeypatch, code):
    send = _Sent(lambda data: code)
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    assert notify_hook._forward(_enc(_event(size=5_000))) is False
    assert len(send.data) == 1                    # no pointer was tried
    assert os.listdir(folder) == []


def test_no_dir_no_spill(sock_path, tmp_path, monkeypatch):
    send = _Sent(lambda data: errno.EMSGSIZE if len(data) > 2048 else None)
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    assert notify_hook._forward(_enc(_event(size=5_000))) is False
    assert not (tmp_path / "spill").exists()
    assert len(send.data) == 1


def test_file_mode_and_body(sock_path, folder, monkeypatch):
    send = _Sent(lambda data: errno.EMSGSIZE if len(data) > 2048 else None)
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    data = _enc(_event(size=5_000))
    assert notify_hook._forward(data) is True
    name = hook_spill.parse_pointer(send.data[-1])
    assert hook_spill.NAME_RE.fullmatch(name)
    path = folder / name
    assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
    assert path.read_bytes() == data


@pytest.mark.parametrize("kind", ["file", "link"])
def test_planted_name(sock_path, folder, tmp_path, monkeypatch, kind):
    name = "ev-" + "b" * 32 + ".json"
    target = tmp_path / "victim.txt"
    target.write_text("planted")
    if kind == "file":
        (folder / name).write_text("planted")
    else:
        (folder / name).symlink_to(target)
    monkeypatch.setattr(hook_spill, "_new_name", lambda: name)
    send = _Sent(lambda data: errno.EMSGSIZE if len(data) > 2048 else None)
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    assert notify_hook._forward(_enc(_event(size=5_000))) is False
    assert target.read_text() == "planted"
    if kind == "file":
        assert (folder / name).read_text() == "planted"
    assert len(send.data) == 1                    # no pointer to someone's file


def test_spill_cap_no_write(sock_path, folder, monkeypatch):
    monkeypatch.setattr(hook_spill, "SPILL_MAX_BYTES", 1024)
    send = _Sent(lambda data: errno.EMSGSIZE if len(data) > 512 else None)
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    assert notify_hook._forward(_enc(_event(size=2_000))) is False
    assert os.listdir(folder) == []
    assert len(send.data) == 1


def test_pointer_fail_deletes(sock_path, folder, monkeypatch):
    """The daemon is gone by the time the pointer goes: no file is left."""
    send = _Sent(lambda data: errno.EMSGSIZE if len(data) > 2048 else errno.ECONNREFUSED)
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    assert notify_hook._forward(_enc(_event(size=5_000))) is False
    assert len(send.data) == 2
    assert os.listdir(folder) == []


def test_pr_pointer_fail_no_wait(sock_path, folder, monkeypatch, capsys):
    """A PermissionRequest whose pointer could not be sent waits for no
    verdict: nothing is printed and no timeout notice follows."""
    monkeypatch.setenv("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS", "0.3")
    send = _Sent(lambda data: errno.EMSGSIZE if len(data) > 2048 else errno.ECONNREFUSED)
    monkeypatch.setattr(notify_hook, "_send_datagram", send)
    _main_in(monkeypatch, _event("PermissionRequest", size=5_000))()
    names = [json.loads(d).get("hook_event_name") for d in send.data
             if not hook_spill.parse_pointer(d)]
    assert names == ["PermissionRequest"]
    assert capsys.readouterr().out.strip() == ""
    assert os.listdir(folder) == []


class _FakeSocket:
    def __init__(self, fail: int = 0):
        self.calls: list[tuple] = []
        self.fail = fail

    def setsockopt(self, *args):
        self.calls.append(("setsockopt", *args))

    def sendto(self, data, path):
        self.calls.append(("sendto", len(data), path))
        if self.fail:
            raise OSError(self.fail, os.strerror(self.fail))

    def close(self):
        self.calls.append(("close",))


@pytest.mark.parametrize("raise_it", [True, False], ids=["mac", "linux"])
def test_sndbuf_only_off_linux(monkeypatch, raise_it):
    fake = _FakeSocket()
    monkeypatch.setattr(notify_hook, "_dgram_socket", lambda: fake)
    monkeypatch.setattr(notify_hook, "_RAISE_SNDBUF", raise_it)
    notify_hook._send_datagram(b"{}", "/nowhere/s.sock")
    expected = [("sendto", 2, "/nowhere/s.sock"), ("close",)]
    if raise_it:
        expected.insert(0, ("setsockopt", socket.SOL_SOCKET, socket.SO_SNDBUF, 245_760))
    assert fake.calls == expected


def test_send_closes_on_failure(monkeypatch):
    fake = _FakeSocket(fail=errno.ENOENT)
    monkeypatch.setattr(notify_hook, "_dgram_socket", lambda: fake)
    with pytest.raises(OSError):
        notify_hook._send_datagram(b"{}", "/nowhere/s.sock")
    assert fake.calls[-1] == ("close",)


def test_inline_max_under_asyncio():
    """An inline datagram always fits the daemon's asyncio read."""
    from asyncio import selector_events
    assert notify_hook._INLINE_MAX_BYTES == 240 * 1024
    assert notify_hook._INLINE_MAX_BYTES < hook_receiver._RECV_MAX_BYTES
    assert hook_receiver._RECV_MAX_BYTES <= selector_events._SelectorDatagramTransport.max_size
