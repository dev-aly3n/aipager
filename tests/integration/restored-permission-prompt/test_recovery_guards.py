"""The startup recovery never stops on one session (roadmap 8.102, test
report iter 2): a transcript the card loop cannot read, or a card whose
recovery raises, is that session's problem alone. And a restore that fails
half way leaves the session in the status it was loaded with.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aipager.state import Status

LABEL = "aipager_boss"


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _ts(wall: float) -> str:
    return datetime.fromtimestamp(wall, timezone.utc).isoformat()


def _running_turn(r, at: float) -> None:
    r.append({"type": "user", "timestamp": _ts(at),
              "message": {"role": "user", "content": "clean the build"}})


async def _recover_or_exception(r) -> str:
    try:
        await r.bot.recover_sessions()
    except Exception as exc:  # noqa: BLE001 - the guard under test
        return f"raised {type(exc).__name__}: {exc}"
    return "ok"


def test_a_transcript_reader_failure_closes_the_card_and_recovery_ends(
        replay, vloop, caplog, monkeypatch):
    """The card's turn cannot be read: no evidence it runs, so the card is
    closed as before 8.101, and the recovery reaches its summary."""
    r = replay

    def _boom(path):
        raise RecursionError("maximum recursion depth exceeded")

    async def scenario():
        _running_turn(r, vloop.wall() - 60)
        card = await r.open_card()
        await r.restart(recover=False)
        monkeypatch.setattr("aipager.bot.lifecycle.turn_appears_complete",
                            _boom)
        outcome = await _recover_or_exception(r)
        await asyncio.sleep(2)
        return card, outcome

    with caplog.at_level(logging.INFO):
        card, outcome = _run(vloop, scenario())
    assert outcome == "ok"
    assert any(m.startswith(f"[{LABEL}] transcript not readable at startup "
                            "- RecursionError") for m in caplog.messages)
    assert not any(f"busy card {card} adopted" in m for m in caplog.messages)
    assert any(m.startswith("recovered 1 sessions: ") for m in caplog.messages)


def test_a_card_whose_recovery_raises_does_not_stop_the_recovery(
        replay, vloop, caplog, monkeypatch):
    r = replay

    async def _boom(*args, **kwargs):
        raise KeyError("chat")

    async def scenario():
        await r.open_card()  # no transcript: the card is not adopted
        await r.restart(recover=False)
        monkeypatch.setattr(r.bot, "_recover_busy_message", _boom)
        return await _recover_or_exception(r)

    with caplog.at_level(logging.INFO):
        outcome = _run(vloop, scenario())
    assert outcome == "ok"
    assert any(m.startswith(f"[{LABEL}] busy card ")
               and "not recovered - KeyError" in m for m in caplog.messages)
    assert any(m.startswith("recovered 1 sessions: 1 error")
               for m in caplog.messages)


def test_a_card_whose_adoption_raises_does_not_stop_the_recovery(
        replay, vloop, caplog, monkeypatch):
    r = replay

    def _boom(*args, **kwargs):
        raise ValueError("clock")

    async def scenario():
        _running_turn(r, vloop.wall() - 60)
        await r.open_card()
        await r.restart(recover=False)
        monkeypatch.setattr(r.bot, "_adopt_running_card", _boom)
        return await _recover_or_exception(r)

    with caplog.at_level(logging.INFO):
        outcome = _run(vloop, scenario())
    assert outcome == "ok"
    assert any(m.startswith("recovered 1 sessions: 1 error")
               for m in caplog.messages)


def test_a_restore_failing_half_way_keeps_the_loaded_status(
        replay, vloop, tmp_path, monkeypatch):
    """A separate prompt (no card, so the card loop never touches it):
    the restore re-entered the turn (BUSY) before it failed; the session
    goes back to the status it was loaded with."""
    r = replay

    def _boom(*args, **kwargs):
        raise KeyError("perm")

    async def scenario():
        r.bot.registry.transition(r.sess.name, Status.BUSY)
        r.sess.trigger_msg_id = 1
        await r.permission(hook={"addr": str(tmp_path / "gone.sock"),
                                 "request_id": "req-dead"})
        await r.restart(recover=False)
        loaded = r.sess.status
        monkeypatch.setattr(r.bot, "_rebuild_open_prompt", _boom)
        outcome = await _recover_or_exception(r)
        return loaded, outcome

    loaded, outcome = _run(vloop, scenario())
    assert outcome == "ok"
    assert loaded is Status.UNKNOWN  # premise
    assert r.sess.status is loaded
    assert r.sess.pending_prompt_msg is None
