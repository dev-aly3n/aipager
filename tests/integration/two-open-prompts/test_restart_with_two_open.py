"""A restart with two prompts open (roadmap 8.120): none comes back, every
one of their buttons refuses and types nothing; one prompt open still
comes back (8.102)."""

from __future__ import annotations

import json

from aipager import state
from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.state import SessionRegistry
from tests.two_prompts_rig import BG, CARD, S, Rig, answers


def test_nothing_restored_and_every_button_refuses(rig, mk_bot, monkeypatch, run_async):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.question()
    question_msg = rig.sess.extra_prompts[0]["msg_id"]
    rig.bot.registry.save()
    saved = json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][S]
    assert "open_prompt" not in saved
    again = SessionRegistry()
    again.load()
    assert again.get(S).restored_open_prompt is None
    after = Rig(mk_bot, monkeypatch, run_async, registry=again)
    for msg_id, verb in ((CARD, "allow"), (CARD, "deny"), (question_msg, "opt1")):
        assert answers(after.tap(msg_id, verb)) == [str(ALREADY_ANSWERED)]
    assert after.keys == [] and after.decisions == []


def test_one_left_open_comes_back(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.claude_bash()
    # The agent's prompt is answered: only Claude's own is left, and saved.
    rig.tap(CARD, "allow")
    rig.bot.registry.save()
    again = SessionRegistry()
    again.load()
    rec = again.get(S).restored_open_prompt
    assert rec is not None and rec["kind"] == "separate"
