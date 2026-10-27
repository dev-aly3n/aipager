"""The daemon's side of spilled hook events (roadmap 8.138).

A pointer datagram is resolved in ``_on_datagram_counted``, at arrival,
before anything else looks at the datagram: the dialog count, the dedup
and the event's handling all see the event itself. A refused pointer is
logged without content and handled as nothing. ``HookReceiver.start()``
makes the folder and sweeps old files.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import stat
import time

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.dtach import hook_spill
from aipager.state import SessionRegistry

SESSION = "claude-spilled"
LOGGER = "aipager.dtach.hook_receiver"
SECRET = "do-not-log-this-text"


@pytest.fixture
def folder(tmp_path):
    assert hook_spill.ensure_dir("/unused") == ""
    return tmp_path / "spill"


@pytest.fixture
def sock_path(tmp_path, monkeypatch):
    path = str(tmp_path / "s.sock")
    monkeypatch.setattr(hr, "SOCKET_PATH", path)
    return path


def _event(event: str = "PermissionRequest", **extra) -> dict:
    data = {"hook_event_name": event, "session": SESSION, "tool_name": "Write",
            "tool_input": {"file_path": "/home/alice/x.txt", "content": SECRET * 100}}
    data.update(extra)
    return data


def _spill(data: dict) -> bytes:
    name = hook_spill.write_spill("/unused", json.dumps(data).encode())
    assert name is not None
    return hook_spill.pointer(name)


def _recording(handled: list):
    recv = hr.HookReceiver(SessionRegistry(), None)

    async def record(data, **kw):
        handled.append((json.loads(data), kw))

    recv._on_datagram = record
    return recv


def test_dialog_counted_on_arrival(folder, run_async):
    handled = []
    recv = _recording(handled)
    coro = recv._on_datagram_counted(_spill(_event()))
    # Counted synchronously at arrival, before the handler has started.
    assert hr.dialog_datagram_pending(SESSION) is True
    assert os.listdir(folder) == []          # read and deleted at arrival too
    run_async(coro)
    assert hr.dialog_datagram_pending(SESSION) is False
    assert handled[0][0]["hook_event_name"] == "PermissionRequest"


def test_resolved_event_handed_on_with_its_size(folder, run_async):
    handled = []
    recv = _recording(handled)
    event = _event("PreToolUse")
    run_async(recv._on_datagram_counted(_spill(event)))
    size = len(json.dumps(event).encode())
    assert handled == [(event, {"spilled": size})]


def test_dedup_resolved(folder, run_async, caplog):
    """Two copies of one spilled event (a double-wired hook) are handled
    once, as two inline copies are."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    recv = hr.HookReceiver(SessionRegistry(), None)
    event = {"hook_event_name": "Notification", "notification_type": "auth_success",
             "session": SESSION}
    run_async(recv._on_datagram_counted(_spill(event)))
    run_async(recv._on_datagram_counted(_spill(event)))
    dups = [r for r in caplog.records if "dropping duplicate hook event" in r.getMessage()]
    assert len(dups) == 1


def test_spilled_info_line(folder, run_async, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    recv = hr.HookReceiver(SessionRegistry(), None)
    event = {"hook_event_name": "Notification", "notification_type": "auth_success",
             "session": SESSION, "message": SECRET}
    run_async(recv._on_datagram_counted(_spill(event)))
    size = len(json.dumps(event).encode())
    lines = [r for r in caplog.records if r.levelno == logging.INFO
             and "arrived through a spill file" in r.getMessage()]
    assert [r.getMessage() for r in lines] == [
        f"[{SESSION}] auth_success arrived through a spill file ({size} bytes)"]
    assert SECRET not in caplog.text


def test_inline_event_logs_no_spill_line(run_async, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    recv = hr.HookReceiver(SessionRegistry(), None)
    event = {"hook_event_name": "Notification", "notification_type": "auth_success",
             "session": SESSION}
    run_async(recv._on_datagram_counted(json.dumps(event).encode()))
    assert "spill" not in caplog.text


def test_pointer_without_resolution_dropped(folder, run_async, caplog):
    handled = []
    recv = _recording(handled)
    name = "ev-" + "c" * 32 + ".json"
    run_async(recv._on_datagram_counted(hook_spill.pointer(name)))
    assert handled == []
    assert hr._DIALOG_DATAGRAMS == {}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [f"hook spill file refused (missing): {name!r}"]


def test_refusal_logged_no_content(folder, run_async, caplog, monkeypatch):
    """Refusals say why, never what the file held, and never more than 80
    characters of the name a stranger sent."""
    handled = []
    recv = _recording(handled)
    monkeypatch.setattr(hook_spill, "SPILL_MAX_BYTES", 1024)
    name = "ev-" + "d" * 32 + ".json"
    (folder / name).write_text(SECRET * 100)
    (folder / name).chmod(0o600)
    run_async(recv._on_datagram_counted(hook_spill.pointer(name)))
    long_name = "../" + "n" * 300
    run_async(recv._on_datagram_counted(hook_spill.pointer(long_name)))
    run_async(recv._on_datagram_counted(b'{"type": "aipager_spill", "name": {"a": 1}}'))
    assert handled == []
    assert not (folder / name).exists()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        f"hook spill file refused (too-big): {name!r}",
        f"hook spill file refused (bad-name): {long_name[:80]!r}",
        "hook spill file refused (bad-name): 'dict'",
    ]
    assert SECRET not in caplog.text


def test_resolved_pointer_is_not_resolved_again(folder, run_async):
    """A spill file holding a pointer is handed on as data, never followed."""
    handled = []
    recv = _recording(handled)
    inner = _spill(_event("PreToolUse"))
    outer_name = hook_spill.write_spill("/unused", inner)
    run_async(recv._on_datagram_counted(hook_spill.pointer(outer_name)))
    assert [h[0] for h in handled] == [json.loads(inner)]
    assert len(os.listdir(folder)) == 1       # the inner file was never read


@pytest.mark.parametrize("size, warned", [(256 * 1024, True), (256 * 1024 - 1, False)],
                         ids=["full", "under"])
def test_truncation_warning(run_async, caplog, size, warned):
    handled = []
    recv = _recording(handled)
    data = b'{"x": "' + b"y" * (size - 9) + b'"}'
    assert len(data) == size
    run_async(recv._on_datagram_counted(data))
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    expected = [f"hook datagram of {size} bytes reached the receive size and may be cut short"]
    assert lines == (expected if warned else [])
    assert len(handled) == 1                   # handed on either way


# ---- start-up -------------------------------------------------------------------------

def _start_stop(run_async, recv=None):
    recv = recv or hr.HookReceiver(SessionRegistry(), None)

    async def scenario():
        await recv.start()
        recv.stop()

    run_async(scenario())
    return recv


def test_start_creates_0700(sock_path, tmp_path, run_async):
    _start_stop(run_async)
    assert stat.S_IMODE(os.lstat(tmp_path / "spill").st_mode) == 0o700


def test_start_tightens_our_loose_folder(sock_path, tmp_path, run_async):
    (tmp_path / "spill").mkdir()
    (tmp_path / "spill").chmod(0o775)
    _start_stop(run_async)
    assert stat.S_IMODE(os.lstat(tmp_path / "spill").st_mode) == 0o700


def test_start_sweeps(sock_path, folder, run_async, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    old = folder / ("ev-" + "1" * 32 + ".json")
    fresh = folder / ("ev-" + "2" * 32 + ".json")
    for path in (old, fresh):
        path.write_text("{}")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    _start_stop(run_async)
    assert not old.exists()
    assert fresh.exists()
    assert "removed 1 stale hook spill file(s)" in caplog.text


def test_start_foreign_dir_warns(sock_path, tmp_path, run_async, caplog, monkeypatch):
    (tmp_path / "spill").mkdir()
    (tmp_path / "spill").chmod(0o755)
    monkeypatch.setattr(hook_spill, "_getuid", lambda: os.getuid() + 1)
    seen = []

    async def scenario():
        recv = hr.HookReceiver(SessionRegistry(), None)
        await recv.start()
        seen.append(os.path.exists(sock_path))     # the daemon still listens
        recv.stop()

    run_async(scenario())
    assert seen == [True]
    assert stat.S_IMODE(os.lstat(tmp_path / "spill").st_mode) == 0o755
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "is not usable (not-ours)" in warnings[0]


class _FakeSock:
    def __init__(self):
        self.calls = []

    def setsockopt(self, *args):
        self.calls.append(args)


class _FakeTransport:
    def __init__(self):
        self.sock = _FakeSock()

    def get_extra_info(self, key):
        return self.sock if key == "socket" else None


@pytest.mark.parametrize("raise_it", [True, False], ids=["mac", "linux"])
def test_rcvbuf_only_off_linux(monkeypatch, raise_it):
    monkeypatch.setattr(hr, "_RAISE_RCVBUF", raise_it)
    transport = _FakeTransport()
    hr._raise_rcvbuf(transport)
    expected = [(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)] if raise_it else []
    assert transport.sock.calls == expected


def test_start_tunes_its_own_socket(sock_path, run_async, monkeypatch):
    seen = []
    monkeypatch.setattr(hr, "_raise_rcvbuf",
                        lambda transport: seen.append(transport.get_extra_info("socket")))
    recv = _start_stop(run_async)
    assert len(seen) == 1 and seen[0] is not None
    assert recv._transport.get_extra_info("socket") is seen[0]


def test_rcvbuf_failure_is_quiet(monkeypatch):
    monkeypatch.setattr(hr, "_RAISE_RCVBUF", True)

    class _Refusing(_FakeTransport):
        def get_extra_info(self, key):
            raise OSError(55, "No buffer space available")

    hr._raise_rcvbuf(_Refusing())             # never raises


# ---- a spilled PermissionRequest through the real handler ---------------------------------

def test_spilled_pr_raises_prompt(folder, run_async):
    raised = []
    recv = hr.HookReceiver(SessionRegistry(), None)

    async def _raise_prompt(session_name, tool_info, context, *, source, own_tool_use_id=""):
        raised.append((session_name, tool_info["name"], context["hook_reply"], source))

    recv._raise_prompt = _raise_prompt
    event = _event(aipager_reply_addr="/home/alice/r.sock", aipager_request_id="r" * 32)
    run_async(recv._on_datagram_counted(_spill(event)))
    assert raised == [(SESSION, "Write",
                       {"addr": "/home/alice/r.sock", "request_id": "r" * 32,
                        "session": SESSION},
                       "permission_request")]


def test_read_error_never_raises_at_arrival(folder, run_async, monkeypatch, caplog):
    """The pointer is resolved inside ``datagram_received``, where an
    exception would make asyncio close the socket for good."""
    def _boom(base, name):
        raise MemoryError("no room")

    monkeypatch.setattr(hook_spill, "read_spill", _boom)
    handled = []
    recv = _recording(handled)
    name = "ev-" + "e" * 32 + ".json"
    run_async(recv._on_datagram_counted(hook_spill.pointer(name)))
    assert handled == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [f"hook spill file refused (error:MemoryError): {name!r}"]
