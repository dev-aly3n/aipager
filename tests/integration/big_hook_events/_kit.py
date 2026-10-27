"""Shared helpers for the black-box tests of big hook events (8.138).

Everything lives under ``tmp_path``: the control socket is ``tmp_path/s.sock``
(short, so the hook's reply socket beside it stays under 107 bytes), and the
spill folder is ``tmp_path/spill`` (the autouse ``_isolate_hook_spill``).
Sends are steered only through the documented seams
(``notify_hook._send_datagram``, ``_dgram_socket``), never through ``socket``.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

from aipager.dtach import hook_receiver, hook_spill, notify_hook

SECRET = "zq-secret-payload-marker-7731"
BIG = ("a" * 99 + "\n") * 3000  # 300,000 bytes


def spill(tmp_path) -> Path:
    return tmp_path / "spill"


def names(tmp_path) -> list[str]:
    d = spill(tmp_path)
    return sorted(os.listdir(d)) if d.is_dir() else []


def event(size: int, *, session="claude-bh", name="Stop", **extra) -> bytes:
    """An encoded hook event of exactly *size* bytes (size >= ~120)."""
    base = {"hook_event_name": name, "session": session, **extra, "pad": ""}
    raw = json.dumps(base).encode()
    pad = size - len(raw)
    assert pad >= 0, size
    base["pad"] = SECRET[: min(pad, len(SECRET))] + "x" * max(0, pad - len(SECRET))
    out = json.dumps(base).encode()
    assert len(out) == size
    return out


def size_limited(monkeypatch, limit: int, err=errno.EMSGSIZE, *, record=None):
    """Make the hook's send behave like a platform whose datagram limit is
    *limit* bytes; smaller sends go through the real send."""
    real = notify_hook._send_datagram

    def send(data, path):
        if record is not None:
            record.append(bytes(data))
        if len(data) > limit:
            raise OSError(err, os.strerror(err))
        return real(data, path)

    monkeypatch.setattr(notify_hook, "_send_datagram", send)


def recording_send(monkeypatch, sent: list, *, fail=None):
    """Replace the send with a recorder that accepts any size. *fail* maps
    the call index to an errno to raise for that call."""
    fail = fail or {}

    def send(data, path):
        i = len(sent)
        sent.append(bytes(data))
        if i in fail:
            raise OSError(fail[i], os.strerror(fail[i]))

    monkeypatch.setattr(notify_hook, "_send_datagram", send)


def point_sockets(monkeypatch, tmp_path) -> str:
    path = str(tmp_path / "s.sock")
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", path)
    monkeypatch.setattr(hook_receiver, "SOCKET_PATH", path)
    return path


def make_receiver(monkeypatch, tmp_path, *, record=True):
    """A real HookReceiver bound at ``tmp_path/s.sock``. When *record*, its
    ``_on_datagram`` is replaced on the instance by a recorder."""
    from aipager.state import SessionRegistry

    monkeypatch.setattr("aipager.state.SESSION_STATE_FILE", tmp_path / "st.json")
    point_sockets(monkeypatch, tmp_path)
    recv = hook_receiver.HookReceiver(SessionRegistry(), AsyncMock())
    got: list[dict] = []
    if record:
        async def rec(data, *, spilled=0):
            try:
                got.append(json.loads(data))
            except ValueError:
                got.append(bytes(data))
            hook = getattr(recv, "_on_got", None)
            if hook:
                hook(got[-1])

        monkeypatch.setattr(recv, "_on_datagram", rec)
    return recv, got


async def wait_for(pred, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            return False
        await asyncio.sleep(0.01)
    return True


def own_dir(tmp_path) -> Path:
    why = hook_spill.ensure_dir(str(tmp_path))
    assert why == "", why
    return spill(tmp_path)


def plant(tmp_path, body: bytes, name: str | None = None) -> str:
    """Write a file with a valid spill name into the folder."""
    d = own_dir(tmp_path)
    name = name or ("ev-" + os.urandom(16).hex() + ".json")
    (d / name).write_bytes(body)
    os.chmod(d / name, 0o600)
    return name


def foreign_fstat(monkeypatch):
    """Make the opened spill file look owned by another uid."""
    def fake(fd):
        st = os.fstat(fd)
        vals = list(st[:10])
        vals[4] = st.st_uid + 1
        return os.stat_result(vals)

    monkeypatch.setattr(hook_spill, "_fstat_file", fake)
