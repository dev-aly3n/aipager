"""Black-box rows for roadmap 8.102: AskUserQuestion across a restart
(design.md success criterion 14 and the question rows of criterion 9).

Restored: one question, single choice, untouched, on the card or on its
own message. Never saved: multi-select, multi-question.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import pytest

from aipager import preferences as prefs
from aipager import state
from aipager.state import Status

CHAT = 256113222
NAME = "claude-rppbb_harness"
LABEL = "rppbb"


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _options(n: int) -> list[dict]:
    return [{"label": f"Option {chr(65 + i)}", "description": f"d{i}"}
            for i in range(n)]


def _q(text="Which one?", n=2, multi=False) -> dict:
    return {"question": text, "header": "Pick", "multiSelect": multi,
            "options": _options(n)}


def _user_prompt(r, at: float) -> None:
    r.append({"type": "user", "timestamp": _iso(at), "message": {
        "role": "user", "content": "[via Telegram · @owner]\nask me"}})


async def _card_question(r, questions: list[dict]) -> int:
    _user_prompt(r, r.wall())
    await r.card_turn(1, "ask me")
    r.pre_tool("AskUserQuestion", {"questions": questions},
               tool_use_id="toolu_q1")
    await asyncio.sleep(15)
    return r.sess.busy_msg_id


async def _separate_question(r, questions: list[dict]) -> int:
    before = {m for m, _t, _r in r.chat.sent}
    r.pre_tool("AskUserQuestion", {"questions": questions},
               tool_use_id="toolu_q1")
    await asyncio.sleep(15)
    new = [m for m, _t, _r in r.chat.sent
           if m not in before and any(d.rsplit(":", 1)[-1].startswith("opt")
                                      for d in r.chat.markups.get(m, []))]
    assert new, r.chat.sent
    return new[-1]


# ── restored: one question, single choice ────────────────────────────────

def test_single_question_on_the_card_is_saved(replay, vloop):
    r = replay

    async def scenario():
        await _card_question(r, [_q()])
        r.bot.registry.save()
        return r.saved_entry()

    assert "open_prompt" in _run(vloop, scenario())


def test_single_question_on_the_card_is_restored(replay, vloop):
    r = replay

    async def scenario():
        await _card_question(r, [_q()])
        await r.restart()

    _run(vloop, scenario())
    assert r.sess.status is Status.INTERACTIVE


def test_single_question_restore_logs_the_restored_line(replay, vloop, caplog):
    r = replay

    async def scenario():
        card = await _card_question(r, [_q()])
        await r.restart()
        return card

    with caplog.at_level(logging.INFO):
        card = _run(vloop, scenario())
    assert (f"[{LABEL}] permission prompt restored after restart - waiting "
            f"for an answer (inline card {card})") in caplog.messages


@pytest.mark.parametrize("n,opt,keys", [
    (2, 0, ["Enter"]),
    (2, 1, ["Down", "Enter"]),
    (4, 3, ["Down", "Down", "Down", "Enter"]),
], ids=["first-of-2", "second-of-2", "last-of-4"])
def test_restored_card_question_option_types_downs_then_enter(
        replay, vloop, n, opt, keys):
    r = replay

    async def scenario():
        card = await _card_question(r, [_q(n=n)])
        await r.restart()
        before = list(r.pty.keys)
        await r.tap(card, f"opt{opt}")
        await asyncio.sleep(2)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == keys


def test_restored_card_question_answer_makes_the_session_busy(replay, vloop):
    r = replay

    async def scenario():
        card = await _card_question(r, [_q()])
        await r.restart()
        await r.tap(card, "opt1")
        await asyncio.sleep(2)

    _run(vloop, scenario())
    assert r.sess.status is Status.BUSY


def test_single_question_on_its_own_message_is_restored(replay, vloop):
    r = replay

    async def scenario():
        await _separate_question(r, [_q()])
        await r.restart()

    _run(vloop, scenario())
    assert r.sess.status is Status.INTERACTIVE


def test_restored_separate_question_option_types_down_enter(replay, vloop):
    r = replay

    async def scenario():
        msg = await _separate_question(r, [_q()])
        await r.restart()
        before = list(r.pty.keys)
        await r.tap(msg, "opt1")
        await asyncio.sleep(2)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == ["Down", "Enter"]


def test_question_answered_while_down_is_not_restored(replay, vloop):
    r = replay

    async def scenario():
        await _card_question(r, [_q()])
        await r.stop_process()
        await asyncio.sleep(10)
        r.append({"type": "user", "timestamp": _iso(r.wall()), "message": {
            "role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "toolu_q1",
                "content": "Option B"}]}})
        r.start_process()
        await r.bot.recover_sessions()

    _run(vloop, scenario())
    assert r.sess.status is not Status.INTERACTIVE


# ── never saved: multi-select, multi-question ────────────────────────────

_NOT_SAVED = {
    "multi-select": [_q(multi=True)],
    "multi-question": [_q("First?"), _q("Second?")],
    "multi-question-multi-select": [_q("First?", multi=True),
                                    _q("Second?")],
}


@pytest.mark.parametrize("questions", list(_NOT_SAVED.values()),
                         ids=list(_NOT_SAVED))
def test_unrestorable_question_is_not_saved(replay, vloop, questions):
    r = replay

    async def scenario():
        await _card_question(r, questions)
        status = r.sess.status
        r.bot.registry.save()
        return status, r.saved_entry()

    status, entry = _run(vloop, scenario())
    assert (status, "open_prompt" in entry) == (Status.INTERACTIVE, False)


@pytest.mark.parametrize("questions", list(_NOT_SAVED.values()),
                         ids=list(_NOT_SAVED))
def test_unrestorable_question_is_not_restored(replay, vloop, questions):
    r = replay

    async def scenario():
        await _card_question(r, questions)
        await r.restart()

    _run(vloop, scenario())
    assert r.sess.status is not Status.INTERACTIVE


@pytest.mark.parametrize("questions", list(_NOT_SAVED.values()),
                         ids=list(_NOT_SAVED))
def test_unrestorable_separate_question_is_not_saved(replay, vloop, questions):
    r = replay

    async def scenario():
        await _separate_question(r, questions)
        r.bot.registry.save()
        return r.saved_entry()

    assert "open_prompt" not in _run(vloop, scenario())


# ── invalid question records ─────────────────────────────────────────────

_INVALID_Q = {
    "five-options": lambda rec: rec["perm"]["question"].update(
        options=[{"label": f"L{i}", "description": ""} for i in range(5)]),
    "no-options": lambda rec: rec["perm"]["question"].update(options=[]),
    "empty-label": lambda rec: rec["perm"]["question"]["options"][0].update(
        label=""),
    "option-extra-key": lambda rec: rec["perm"]["question"]["options"][0]
    .update(value=1),
    "option-no-description": lambda rec: rec["perm"]["question"]["options"][0]
    .pop("description"),
    "question-null": lambda rec: rec["perm"].update(question=None),
    "question-text-int": lambda rec: rec["perm"]["question"].update(
        question=3),
    "question-1001": lambda rec: rec["perm"]["question"].update(
        question="q" * 1001),
    "label-201": lambda rec: rec["perm"]["question"]["options"][0].update(
        label="L" * 201),
    "question-with-hook_reply": lambda rec: rec["perm"].update(
        hook_reply={"addr": "/x.sock", "request_id": "r"}),
}

_VALID_Q = {
    "four-options": lambda rec: rec["perm"]["question"].update(
        options=[{"label": f"L{i}", "description": ""} for i in range(4)]),
    "one-option": lambda rec: rec["perm"]["question"].update(
        options=[{"label": "Only", "description": ""}]),
    "label-200": lambda rec: rec["perm"]["question"]["options"][0].update(
        label="L" * 200),
    "question-1000": lambda rec: rec["perm"]["question"].update(
        question="q" * 1000),
}


async def _edited_question_restart(r, edit):
    await _card_question(r, [_q()])
    await r.stop_process()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    rec = data["sessions"][NAME]["open_prompt"]
    edit(rec)
    state.SESSION_STATE_FILE.write_text(json.dumps(data))
    await asyncio.sleep(30)
    r.start_process()
    await r.bot.recover_sessions()


@pytest.mark.parametrize("edit", list(_INVALID_Q.values()), ids=list(_INVALID_Q))
def test_invalid_question_record_logs_invalid_record(replay, vloop, caplog,
                                                     edit):
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _edited_question_restart(r, edit))
    assert (f"[{LABEL}] saved permission prompt ignored - invalid record"
            in caplog.messages)


@pytest.mark.parametrize("edit", list(_INVALID_Q.values()), ids=list(_INVALID_Q))
def test_invalid_question_record_is_not_restored(replay, vloop, edit):
    r = replay
    _run(vloop, _edited_question_restart(r, edit))
    assert r.sess.status is not Status.INTERACTIVE


@pytest.mark.parametrize("edit", list(_VALID_Q.values()), ids=list(_VALID_Q))
def test_valid_question_boundary_record_is_restored(replay, vloop, edit):
    r = replay
    _run(vloop, _edited_question_restart(r, edit))
    assert r.sess.status is Status.INTERACTIVE
