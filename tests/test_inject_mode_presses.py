"""inject.press_mode_cycle: Shift+Tab presses for an in-place Ask <-> Auto
switch, one ``dtach -p`` write per press, MODE_PRESS_GAP apart, under one
write-lock hold."""

from __future__ import annotations

import pytest

from aipager import perm_modes
from aipager.dtach import inject


@pytest.fixture
def writes(monkeypatch):
    log: list = []

    async def _fake_run(args, stdin=b"", timeout=5):
        log.append(("write", args[1] if len(args) > 1 else None, stdin))
        return True, ""

    async def _gap(delay):
        log.append(("gap", delay))

    monkeypatch.setattr(inject, "_run", _fake_run)
    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
    return log


def test_back_tab_is_shift_tab():
    assert inject.KEYS["BackTab"] == "\x1b[Z"


@pytest.mark.parametrize("count", [1, 3], ids=["one", "three"])
def test_one_write_per_press_with_a_gap_between(writes, run_async, count):
    assert run_async(inject.press_mode_cycle("claude-jim", count)) is True
    assert [w[2] for w in writes if w[0] == "write"] == [b"\x1b[Z"] * count
    assert all(w[1] == "-p" for w in writes if w[0] == "write")
    gaps = [w[1] for w in writes if w[0] == "gap"]
    assert gaps == [perm_modes.MODE_PRESS_GAP] * (count - 1)
    # Gaps sit between writes, never before the first or after the last.
    assert writes[0][0] == "write" and writes[-1][0] == "write"


@pytest.mark.parametrize("count", [0, -1], ids=["zero", "negative"])
def test_no_presses_writes_nothing(writes, run_async, count):
    assert run_async(inject.press_mode_cycle("claude-jim", count)) is True
    assert writes == []


def test_stops_at_the_first_failed_write(monkeypatch, run_async):
    calls = []

    async def _fake_run(args, stdin=b"", timeout=5):
        calls.append(stdin)
        return len(calls) < 2, ""

    async def _gap(_delay):
        pass

    monkeypatch.setattr(inject, "_run", _fake_run)
    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
    assert run_async(inject.press_mode_cycle("claude-jim", 3)) is False
    assert calls == [b"\x1b[Z", b"\x1b[Z"]


def test_counted_as_one_write(writes, run_async):
    before = inject._WRITES.get("claude-jim-count", 0)
    run_async(inject.press_mode_cycle("claude-jim-count", 3))
    assert inject._WRITES.get("claude-jim-count", 0) == before + 1
    assert inject.last_write_at("claude-jim-count") > 0
