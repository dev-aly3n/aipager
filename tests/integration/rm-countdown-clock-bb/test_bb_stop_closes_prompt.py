"""Stop with Claude's own prompt open (live round 2, H4): Claude's turn is
still running, so Stop types its two Escapes, and the prompt goes with the
turn on every surface, as a prompt answered in the terminal does. Its own
message loses its Allow / Deny (and its clock line, since Claude Code did
not refuse it), and a late tap on it types nothing. The same with a
background agent running beside it (the live shape) and without one (a
plain working session, the same path on main)."""

from __future__ import annotations

import pytest

from aipager.state import STOP_KEYS, Status
from tests.rm_clock_bb import CLOCK_HEAD
from tests.two_prompts_rig import BG, answers

ALREADY = "already answered"


def _own_prompt(env, *, agent: bool, mode: str = "bypassPermissions") -> int:
    if agent:
        env.rig.launch(BG)
    env.rm(hook=False, mode=mode)
    sess = env.rig.sess
    assert sess.status is Status.INTERACTIVE
    assert sess.stop_mode() == STOP_KEYS
    ids = env.separate_ids()
    assert len(ids) == 1, env.rig.sent
    return ids[0]


def _edits_of(env, msg_id):
    bot = env.rig.bot._app.bot
    texts = [c.kwargs for c in bot.edit_message_text.await_args_list
             if c.kwargs.get("message_id") == msg_id]
    marks = [c.kwargs for c in bot.edit_message_reply_markup.await_args_list
             if c.kwargs.get("message_id") == msg_id]
    return texts, marks


@pytest.fixture(autouse=True)
def _markup_recorded(env):
    from unittest.mock import AsyncMock
    env.rig.bot._app.bot.edit_message_reply_markup = AsyncMock()


@pytest.mark.parametrize("agent", [True, False], ids=["with-agent", "plain"])
def test_stop_takes_the_clocked_prompts_buttons_and_clock(env, agent):
    msg_id = _own_prompt(env, agent=agent)
    sent = next(m for m in env.rig.sent if m["msg_id"] == msg_id)
    assert CLOCK_HEAD in sent["text"]
    outcome = env.run_async(env.rig.bot._stop_session_core(env.rig.sess))
    assert outcome.ok and env.rig.keys == ["Escape", "Escape"]
    texts, _marks = _edits_of(env, msg_id)
    assert texts, env.rig.bot._app.bot.edit_message_text.await_args_list
    last = texts[-1]
    assert last["reply_markup"] is None
    assert CLOCK_HEAD not in last["text"]
    assert last["text"] and last["text"] in sent["text"]


@pytest.mark.parametrize("agent", [True, False], ids=["with-agent", "plain"])
def test_stop_takes_an_unclocked_prompts_buttons(env, agent):
    msg_id = _own_prompt(env, agent=agent, mode="default")
    sent = next(m for m in env.rig.sent if m["msg_id"] == msg_id)
    assert CLOCK_HEAD not in sent["text"]
    env.run_async(env.rig.bot._stop_session_core(env.rig.sess))
    _texts, marks = _edits_of(env, msg_id)
    assert marks and marks[-1]["reply_markup"] is None, marks


@pytest.mark.parametrize("agent", [True, False], ids=["with-agent", "plain"])
def test_a_late_tap_after_the_stop_types_nothing(env, agent):
    msg_id = _own_prompt(env, agent=agent)
    env.run_async(env.rig.bot._stop_session_core(env.rig.sess))
    env.rig.keys.clear()
    q = env.rig.tap(msg_id, "allow")
    assert env.rig.keys == [] and env.rig.decisions == []
    assert ALREADY in answers(q)


def test_control_without_the_stop_the_tap_answers(env):
    """The tap reaches the dialog while the prompt is open, so the test
    above is not passing for an unrelated reason."""
    msg_id = _own_prompt(env, agent=True)
    env.rig.tap(msg_id, "allow")
    assert env.rig.keys == ["Enter"]
