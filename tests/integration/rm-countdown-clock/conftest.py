"""Fixtures for Claude Code's risky-removal countdown (roadmap 8.128): the
wall clock, the two-prompts rig with a transcript under ``tmp_path``, the
session monitor over the rig's registry, and the audit records
(helpers: ``tests/rm_clock_rig.py``)."""

from __future__ import annotations

import pytest

from aipager import audit, rm_countdown
from aipager.session_monitor import SessionMonitor
from tests.rm_clock_rig import Clock, Transcript
from tests.two_prompts_rig import Rig


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(rm_countdown, "_now_wall", c)
    return c


@pytest.fixture
def audits(monkeypatch):
    records: list[dict] = []
    monkeypatch.setattr(audit, "append", lambda **kw: records.append(kw) or True)
    return records


@pytest.fixture
def tx(tmp_path):
    return Transcript(tmp_path / "conversation.jsonl")


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async, clock, audits):
    return Rig(mk_bot, monkeypatch, run_async)


@pytest.fixture
def cardless(mk_bot, monkeypatch, run_async, clock, audits):
    return Rig(mk_bot, monkeypatch, run_async, card=False)


@pytest.fixture
def monitor(rig):
    return SessionMonitor(rig.bot.registry, rig.bot.notify)


@pytest.fixture
def monitor_cl(cardless):
    return SessionMonitor(cardless.bot.registry, cardless.bot.notify)
