"""Permission and question prompts in a Persian group: Allow, Deny, Stop
and an option tap work, the lines in the chat read in Persian, and the
audit log stays English."""

from __future__ import annotations

import json
import re

import pytest

from aipager import audit as audit_mod
from aipager import preferences
from tests.test_separate_permission_prompt import BOB, GROUP, _World

PERSIAN = re.compile("[؀-ۿ]")


@pytest.fixture
def world(mk_bot, monkeypatch):
    preferences.set_ui_language(GROUP, "fa")
    return _World(mk_bot, monkeypatch)


def _audit():
    path = audit_mod.AUDIT_LOG_PATH
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("verb, action", [("allow", "Allowed"), ("deny", "Denied")])
def test_allow_and_deny_in_persian(world, run_async, verb, action):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    query = world.tap(run_async, GROUP, BOB, verb)
    line = world.lines(GROUP)[-1][0]
    assert PERSIAN.search(line) and "api" in line
    assert _audit()[-1]["action"] == action
    toast = query.answer.await_args.args[0]
    assert PERSIAN.search(toast)


def test_stop_on_a_persian_prompt(world, run_async):
    sess = world.session()
    sess.busy_msg_id = 42
    run_async(world.bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Bash", "summary": "Bash: ls", "input": {}},
        "hook_reply": {"addr": "/x", "nonce": "n"}}))
    query = world.tap(run_async, GROUP, BOB, "stop", msg_id=42)
    assert PERSIAN.search(query.answer.await_args.args[0])


def test_a_question_option_in_persian(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, tool_info={
        "name": "AskUserQuestion", "summary": "AskUserQuestion",
        "input": {"questions": [{"question": "Which database?",
                                 "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}})
    world.tap(run_async, GROUP, BOB, "opt1")
    assert world.keys[-2:] == ["Down", "Enter"]
    line = world.lines(GROUP)[-1][0]
    assert PERSIAN.search(line) and "Which database?" in line
    assert _audit()[-1]["action"] == "Selected option 2"
