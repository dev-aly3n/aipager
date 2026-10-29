"""Fixtures for "Stop ends the background agents of a waiting session"
(roadmap 8.121 F5, design-6).

The rig is ``tests/two_prompts_rig.py``: the real hook receiver, notify
path and tap handler, Telegram sends and key names recorded. On top of it
every byte written to the session's terminal is read on ``inject._run``
(the dtach seam; Escapes, Ctrl+U and the kill chord alike), the chord's
gap on ``inject._key_gap_sleep`` and its clock on ``inject._kill_clock``.
Nothing reaches a real dtach, Telegram, a socket under /tmp or the real
home. Payload shapes follow the 2.1.312 probe (kill-agents-probe.md), with
every name, id and path made up.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from tests.two_prompts_rig import Rig

CHORD = b"\x18\x0b"
ESC = b"\x1b"
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(hr, "HOOK_DEDUP_WINDOW_SECONDS", -1.0)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))


class Writes(list):
    """``(kind, value)``: ``("write", bytes)`` per dtach write and
    ``("gap", seconds)`` per key gap."""

    def bytes(self) -> list[bytes]:
        return [v for k, v in self if k == "write"]

    def gaps(self) -> list[float]:
        return [v for k, v in self if k == "gap"]


@pytest.fixture
def writes(monkeypatch) -> Writes:
    log = Writes()
    clock = itertools.count(1000.0, 0.5)

    async def _fake_run(args, stdin=b"", timeout=5):
        log.append(("write", stdin))
        return True, ""

    async def _gap(delay):
        log.append(("gap", delay))

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(inject, "_run", _fake_run)
    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
    monkeypatch.setattr(inject, "_kill_clock", lambda: next(clock))
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    return log


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async, writes):
    r = Rig(mk_bot, monkeypatch, run_async)
    # The rig records key NAMES on send_keys; the bytes behind them are
    # what this scenario reads, so the real writer goes back (it writes
    # through the recorded ``_run``).
    monkeypatch.setattr(inject, "send_keys", _REAL_SEND_KEYS)
    r.writes = writes
    return r


_REAL_SEND_KEYS = inject.send_keys


def ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def append_lines(path: Path, *entries: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def agents_killed_line(epoch: float) -> dict:
    """The ``system`` line Claude Code writes the moment its kill chord
    stopped the agents (probe G1)."""
    return {"type": "system", "subtype": "agents_killed", "timestamp": ts(epoch),
            "uuid": "0f3a-kill", "sessionId": "5e1d-0000"}


def wake_user_line(epoch: float, text: str) -> dict:
    """The ``user`` line of the turn Claude Code starts after the kill."""
    return {"type": "user", "timestamp": ts(epoch), "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": text}}
