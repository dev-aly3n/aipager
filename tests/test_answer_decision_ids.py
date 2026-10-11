"""An answered prompt's record is decided by the action id, never by the
verb shown (the multilingual design, 5.4).

The verb ("Allowed", "Denied") is display text and will be translated;
whether the audit record says ``denied`` and which icon the chat line
carries must not change with it. The id passed is the EFFECTIVE decision:
an "Allow always" with no rule to save is a single allow.
"""

from __future__ import annotations

import pytest

from aipager.bot import callbacks
from tests.test_separate_permission_prompt import BOB, GROUP, PROMPT_ID, _audit, _World


@pytest.fixture
def world(mk_bot, monkeypatch):
    return _World(mk_bot, monkeypatch)


def _record(world, run_async, verb, decision):
    sess = world.session()
    perm = {"tool_summary": "Bash: ls", "tool_info": {"name": "Bash"}}
    member = world.bot._driver_user_by_id(BOB, chat_id=GROUP)
    run_async(world.bot._record_prompt_answer(sess, perm, verb, member, "hook_decision",
                                              decision=decision))
    return _audit()[-1], world.lines(GROUP)[-1][0]


def test_a_reworded_verb_still_audits_denied(world, run_async):
    rec, line = _record(world, run_async, "Abgelehnt", "deny")
    assert rec["denied"] is True and rec["action"] == "Abgelehnt"
    assert line.startswith("🚫 ")
    rec, line = _record(world, run_async, "Denied", "allow")
    assert rec["denied"] is False and line.startswith("✅ ")


@pytest.mark.parametrize("decision, icon", [
    ("allow", "✅"), ("allow_always", "🟢"), ("deny", "🚫"), ("continue", "▶️"),
    (None, "·"), ("opt3", "·")])
def test_the_icon_comes_from_the_action_id(world, run_async, decision, icon):
    _rec, line = _record(world, run_async, "Whatever", decision)
    assert line.startswith(icon + " <b>api</b>")


def test_degraded_always_records_allow(world, run_async):
    """No standing rule on offer: the tap allows once, and the record and
    the line say so."""
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    world.tap(run_async, GROUP, BOB, "allow_always")
    rec = _audit()[-1]
    assert (rec["action"], rec["denied"]) == ("Allowed", False)
    line = world.lines(GROUP)[-1]
    assert line[0].startswith("✅ <b>api</b> · Allowed") and line[1] == PROMPT_ID


def test_the_keystroke_degraded_always_records_allow(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, hook=None)
    world.tap(run_async, GROUP, BOB, "allow_always")
    rec = _audit()[-1]
    assert (rec["action"], rec["via"], rec["denied"]) == (
        "Allowed", "keystroke_fallback", False)
    assert world.lines(GROUP)[-1][0].startswith("✅ ")


def test_a_real_always_records_allow_always(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, tool_info={
        "name": "Bash", "summary": "Bash: ls", "input": {"command": "ls"},
        "standing_rule_suggestion": {"type": "addRules", "rules": [
            {"toolName": "Bash", "ruleContent": "ls"}], "behavior": "allow",
            "destination": "localSettings"}})
    world.tap(run_async, GROUP, BOB, "allow_always")
    rec = _audit()[-1]
    assert (rec["action"], rec["denied"]) == ("Allowed always", False)
    assert world.lines(GROUP)[-1][0].startswith("🟢 ")


def test_the_table_covers_every_action_verb():
    from aipager.bot.transport import ACTION_VERBS
    assert set(callbacks._DECISION_ICONS) == set(ACTION_VERBS)
