"""AskUserQuestion across a restart (roadmap 8.102, design.md
"AskUserQuestion (decision)").

A question with ONE part, single choice, not yet touched comes back like a
permission prompt: option N is N Downs then Enter from the dialog's first
row, the same keys as before a restart. Multi-select and multi-part forms,
and a question already touched from Telegram, are not saved: their
answers are relative to a TUI cursor and tab the daemon cannot see after
a restart, so they are left to the terminal.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from aipager.state import Status

# The harness's (conftest.py; a hyphenated directory is no package).
LABEL = "aipager_boss"

ONE = [{"question": "Which branch?", "multiSelect": False,
        "options": [{"label": "main", "description": "the default"},
                    {"label": "dev", "description": ""}]}]
MULTI_SELECT = [{"question": "Which files?", "multiSelect": True,
                 "options": [{"label": "a.py"}, {"label": "b.py"}]}]
TWO_PARTS = [{"question": "Which branch?", "options": [{"label": "main"},
                                                       {"label": "dev"}]},
             {"question": "Push?", "options": [{"label": "yes"},
                                               {"label": "no"}]}]


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


async def _inline_question(r, questions) -> int:
    card = await r.open_card()
    await r.ask(questions)
    assert r.sess.status is Status.INTERACTIVE
    await r.shown(card, "opt0")
    await asyncio.sleep(5)
    return card


def test_a_single_question_is_restored_and_answered(replay, vloop, caplog):
    r = replay

    async def scenario():
        card = await _inline_question(r, ONE)
        await r.restart()
        restored = r.sess.status
        toast = await r.tap(card, r.cb(card, "opt1"))
        await asyncio.sleep(2)
        return card, restored, toast

    with caplog.at_level(logging.INFO):
        card, restored, toast = _run(vloop, scenario())
    assert restored is Status.INTERACTIVE
    assert (f"[{LABEL}] permission prompt restored after restart - waiting "
            f"for an answer (inline card {card})") in caplog.messages
    assert r.keys == ["Down", "Enter"]
    assert toast == f"Selected option 2 [{LABEL}]"
    assert r.sess.status is Status.BUSY


def test_a_single_question_sent_as_its_own_message_is_restored(replay, vloop):
    r = replay

    async def scenario():
        r.bot.registry.transition(r.sess.name, Status.BUSY)
        r.sess.trigger_msg_id = 1
        await r.ask(ONE)
        (msg,) = [m for m, rec in r.chat.messages.items()
                  if "Which branch?" in rec["text"]]
        await r.restart()
        restored = r.sess.status
        await r.tap(msg, r.cb(msg, "opt0"), text=r.chat.messages[msg]["text"])
        await asyncio.sleep(2)
        return restored

    assert _run(vloop, scenario()) is Status.INTERACTIVE
    assert r.keys == ["Enter"]
    assert r.sess.status is Status.BUSY


@pytest.mark.parametrize("questions,touch", [
    pytest.param(MULTI_SELECT, None, id="multi-select"),
    pytest.param(TWO_PARTS, None, id="multi-question"),
    pytest.param(ONE, "cursor_pos", id="touched-cursor"),
])
def test_multi_select_question_is_not_restored(replay, vloop, caplog,
                                               questions, touch):
    r = replay

    async def scenario():
        await _inline_question(r, questions)
        if touch:
            # What a tap that moved the dialog's cursor leaves behind.
            r.sess.pending_permission[touch] = 1
        await r.restart()
        await asyncio.sleep(2)

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert r.sess.status is not Status.INTERACTIVE
    assert r.sess.pending_permission is None
    assert not any("permission prompt restored" in m for m in caplog.messages)
    assert r.keys == []
