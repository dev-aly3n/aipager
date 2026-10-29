"""inject.press_kill_agents: Claude Code's chat:killAgents chord (ctrl+x
ctrl+k) pressed twice, KILL_AGENTS_PRESS_GAP apart, one ``dtach -p`` write
per press, under one write-lock hold, through the typing gate with the
caller's own "may I press" check in place of the dialog probe (roadmap
8.121 F5). Writes are read on ``inject._run``, the gap on
``inject._key_gap_sleep`` and the clock on ``inject._kill_clock``: no real
dtach, no real sleep."""

from __future__ import annotations

import pytest

from aipager.dtach import inject

CHORD = b"\x18\x0b"


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
    monkeypatch.setattr(inject, "_kill_clock", lambda: 100.0)
    return log


def _written(log) -> list[bytes]:
    return [w[2] for w in log if w[0] == "write"]


def test_the_key_is_the_chord():
    assert inject.KEYS["KillAgents"] == "\x18\x0b"


def test_two_chord_writes_a_second_apart_and_no_escape(writes, run_async):
    assert run_async(inject.press_kill_agents("claude-k1", lambda: True)) is True
    assert _written(writes) == [CHORD, CHORD]
    assert all(b"\x1b" not in w for w in _written(writes))
    assert all(w[1] == "-p" for w in writes if w[0] == "write")
    # write, gap, write: the gap sits between the presses.
    assert [w[0] for w in writes] == ["write", "gap", "write"]


def test_the_gap_is_one_second_well_inside_claude_codes_three(writes, run_async):
    run_async(inject.press_kill_agents("claude-k2", lambda: True))
    assert [w[1] for w in writes if w[0] == "gap"] == [inject.KILL_AGENTS_PRESS_GAP]
    assert inject.KILL_AGENTS_PRESS_GAP == 1.0
    assert inject.KILL_AGENTS_MAX_GAP < 3.0


def test_a_parked_session_gets_nothing(writes, run_async, monkeypatch):
    monkeypatch.setattr(inject, "_parked_probe", lambda _s: ("job", 1))
    assert run_async(inject.press_kill_agents("claude-k3", lambda: True)) is False
    assert writes == []


def test_may_press_is_asked_in_place_of_the_dialog_probe(writes, run_async, monkeypatch):
    # The dialog probe says open (an agent's prompt): the chord still goes.
    monkeypatch.setattr(inject, "_dialog_probe", lambda _s: True)
    assert run_async(inject.press_kill_agents("claude-k4", lambda: True)) is True
    assert _written(writes) == [CHORD, CHORD]


def test_a_refusing_check_presses_nothing(writes, run_async):
    result = run_async(inject.press_kill_agents("claude-k5", lambda: False))
    assert result is inject.DIALOG_BEFORE_TYPING
    assert writes == []


def test_the_check_is_looked_at_again_under_the_lock(writes, run_async):
    answers = iter([True, False])

    def may_press():
        return next(answers, False)

    result = run_async(inject.press_kill_agents("claude-k6", may_press))
    assert result is inject.DIALOG_BEFORE_TYPING
    assert writes == []


def test_the_second_press_is_skipped_when_the_check_turns_false_in_the_gap(
        writes, run_async, monkeypatch):
    state = {"ok": True}

    async def _gap(delay):
        writes.append(("gap", delay))
        state["ok"] = False  # a turn started during the gap

    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
    result = run_async(inject.press_kill_agents("claude-k7", lambda: state["ok"]))
    assert result is False
    assert _written(writes) == [CHORD]


def test_the_second_press_is_skipped_past_the_window(writes, run_async, monkeypatch):
    clock = iter([100.0, 102.6, 102.6])
    monkeypatch.setattr(inject, "_kill_clock", lambda: next(clock))
    result = run_async(inject.press_kill_agents("claude-k8", lambda: True))
    assert result is False
    assert _written(writes) == [CHORD]


def test_inside_the_window_the_second_press_goes(writes, run_async, monkeypatch):
    clock = iter([100.0, 102.4, 102.4])
    monkeypatch.setattr(inject, "_kill_clock", lambda: next(clock))
    assert run_async(inject.press_kill_agents("claude-k9", lambda: True)) is True
    assert _written(writes) == [CHORD, CHORD]


def test_a_failed_first_press_writes_no_second(monkeypatch, run_async):
    calls = []

    async def _fake_run(args, stdin=b"", timeout=5):
        calls.append(stdin)
        return False, ""

    async def _gap(_delay):
        pass

    monkeypatch.setattr(inject, "_run", _fake_run)
    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
    assert run_async(inject.press_kill_agents("claude-k10", lambda: True)) is False
    assert calls == [CHORD]


def test_counted_as_one_write(writes, run_async):
    before = inject._WRITES.get("claude-k11", 0)
    run_async(inject.press_kill_agents("claude-k11", lambda: True))
    assert inject._WRITES.get("claude-k11", 0) == before + 1


@pytest.mark.parametrize("writer", ["send_keys", "press_key_in_dialog"])
def test_no_other_writer_presses_it(writes, run_async, writer):
    if writer == "send_keys":
        result = run_async(inject.send_keys("claude-k12", "KillAgents"))
    else:
        async def still_open():
            return True
        result = run_async(inject.press_key_in_dialog("claude-k12", "KillAgents",
                                                      still_open))
    assert result is False
    assert writes == []
