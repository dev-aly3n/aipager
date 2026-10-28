"""Fixtures for the black-box risky-rm clock scenarios (roadmap 8.128);
the shared constants and the ``Env`` helper live in ``tests/rm_clock_bb.py``."""

from __future__ import annotations

import pytest

from aipager import rm_countdown
from tests.rm_clock_bb import T0, Env, Wall
from tests.two_prompts_rig import S, Rig


@pytest.fixture
def wall(monkeypatch):
    w = Wall(T0)
    monkeypatch.setattr(rm_countdown, "_now_wall", lambda: w.t)
    return w


@pytest.fixture
def _alive(monkeypatch):
    async def _list():
        return [S]
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _list)


@pytest.fixture
def env(mk_bot, monkeypatch, run_async, tmp_path, wall, _alive):
    """No busy card: the prompt is its own (separate) message."""
    return Env(Rig(mk_bot, monkeypatch, run_async, card=False), wall, tmp_path, run_async)


@pytest.fixture
def cenv(mk_bot, monkeypatch, run_async, tmp_path, wall, _alive):
    """A busy card: Claude's lone prompt is inline on it."""
    return Env(Rig(mk_bot, monkeypatch, run_async, card=True), wall, tmp_path, run_async)
