"""Permission and answer lines name who answered as "· @bob", not "by
@bob" (the multilingual design, section 10: no language then needs the
actor's gender). What Claude reads is untouched: the deny reason still
says "Denied via aipager by @bob"."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_separate_permission_prompt import BOB, GROUP, PROMPT_ID, _World


@pytest.fixture
def world(mk_bot, monkeypatch):
    return _World(mk_bot, monkeypatch)


@pytest.mark.parametrize("verb, line", [
    ("allow", "✅ <b>api</b> · Allowed · @bob · Bash: rm -rf build/"),
    ("deny", "🚫 <b>api</b> · Denied · @bob · Bash: rm -rf build/"),
])
def test_the_decision_line_reads_verb_dot_person(world, run_async, verb, line):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    world.tap(run_async, GROUP, BOB, verb)
    assert world.lines(GROUP) == [(line, PROMPT_ID)]


def test_claude_still_reads_denied_via_aipager_by(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    world.tap(run_async, GROUP, BOB, "deny")
    (_reply, decision), = world.decisions
    assert decision["message"] == "Denied via aipager by @bob"


def test_an_option_line_reads_dot_person(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, tool_info={
        "name": "AskUserQuestion", "summary": "AskUserQuestion",
        "input": {"questions": [{"question": "Pick one",
                                 "options": [{"label": "A"}, {"label": "B"}]}]}})
    world.tap(run_async, GROUP, BOB, "opt1")
    assert world.lines(GROUP) == [
        ("· <b>api</b> · Selected option 2 · @bob · Pick one", PROMPT_ID)]


def test_a_multi_select_answer_reads_answered_dot_person(world, run_async):
    sess = world.session()
    sess.busy_msg_id = 50
    sess.pending_permission = {
        "ask_question": True, "question": "Pick some", "multi_select": True,
        "options": [{"label": "A"}, {"label": "B"}], "questions": [
            {"question": "Pick some", "multiSelect": True,
             "options": [{"label": "A"}, {"label": "B"}]}],
        "current_idx": 0, "cursor_pos": 0, "selected": {1},
        "tool_info": {"name": "AskUserQuestion"}}
    world.bot._build_busy_text = MagicMock(return_value="card")
    world.bot._build_stop_keyboard = MagicMock(return_value=None)
    world.tap(run_async, GROUP, BOB, "submit", msg_id=50)
    assert ("✓ <b>api</b> · Answered · @bob · Pick some → Selected: B", 50) in world.lines(GROUP)


def test_no_person_no_dot(mk_bot, run_async, monkeypatch):
    """Personal mode has no member to name: the line ends at the verb."""
    w = _World(mk_bot, monkeypatch)
    w.bot.scopes = None
    sess = w.session()
    w.prompt(sess, run_async=run_async)
    w.bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=PROMPT_ID))
    w.tap(run_async, GROUP, BOB, "allow")
    texts = [t for t, _r in w.lines(GROUP)]
    assert texts == ["✅ <b>api</b> · Allowed · Bash: rm -rf build/"]
