"""Roadmap 8.120: a daemon restart with two prompts open.

One open prompt survives a restart as in 8.102. With two or more open,
none is saved: an agent's cannot be checked after a restart, and while the
daemon was down the dialog on top may have been answered at the terminal,
uncovering another, so a restored prompt that is not certainly on top
could have its keys land in the other dialog. Every one of their messages
is closed in the state file instead, so their buttons refuse after the
restart and type nothing.
"""

from __future__ import annotations

import json

import pytest

from aipager import state
from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.state import SessionRegistry
from tests.two_prompts_rig import BG, CARD, S, Rig, answers


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)


def _saved():
    return json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][S]


def _two_open(rig):
    rig.launch(BG)
    rig.claude_bash()
    rig.agent_bash(BG, "make")
    rig.question(tool_use_id="toolu_q9")


def test_two_open_nothing_saved(rig):
    _two_open(rig)
    assert len(rig.sess.open_prompts()) == 3
    rig.bot.registry.save()
    assert "open_prompt" not in _saved()
    again = SessionRegistry()
    again.load()
    assert again.get(S).restored_open_prompt is None


def test_one_open_is_still_saved(rig):
    rig.claude_bash()
    rig.bot.registry.save()
    assert _saved()["open_prompt"]["kind"] == "inline"


def test_two_open_buttons_refuse_after_restart(rig, mk_bot, monkeypatch, run_async):
    _two_open(rig)
    extras = [r["msg_id"] for r in rig.sess.extra_prompts]
    rig.bot.registry.save()
    closed = _saved()["closed_prompt_msgs"]
    assert set([CARD, *extras]) <= set(closed)
    # Live, before the restart, they still answer.
    assert not rig.sess.prompt_known_closed(extras[0])
    again = SessionRegistry()
    again.load()
    after = Rig(mk_bot, monkeypatch, run_async, registry=again)
    for msg_id in (CARD, *extras):
        q = after.tap(msg_id, "allow")
        assert answers(q) == [str(ALREADY_ANSWERED)]
    assert after.keys == [] and after.decisions == []


def test_unsaved_separate_prompt_closed_in_file(mk_bot, monkeypatch, run_async):
    rig = Rig(mk_bot, monkeypatch, run_async, card=False)
    rig.launch(BG)
    rig.claude_bash()
    rig.agent_bash(BG, "make")
    primary = rig.sess.pending_prompt_msg["msg_id"]
    extra = rig.sess.extra_prompts[0]["msg_id"]
    rig.bot.registry.save()
    saved = _saved()
    assert "open_prompt" not in saved
    assert {primary, extra} <= set(saved["closed_prompt_msgs"])
    # In the file only.
    assert not rig.sess.prompt_known_closed(primary)
