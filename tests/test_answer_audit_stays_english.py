"""What a person reads about an answered prompt follows the chat's
language; what the audit log records does not (G70), and the prompt's
hidden-direction warning key says what ``bidi_guard.WARNING`` says (G84)."""

from __future__ import annotations

import pytest

from aipager import bidi_guard, preferences
from aipager.bot import callbacks
from aipager.bot.transport import BIDI_WARNING
from aipager.i18n import locales
from tests.test_separate_permission_prompt import BOB, GROUP, _audit, _World


@pytest.fixture
def world(mk_bot, monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    return _World(mk_bot, monkeypatch)


@pytest.mark.parametrize("verb, decision", [
    ("Allowed", "allow"), ("Denied", "deny"), ("Allowed always", "allow_always"),
    ("Continued", "continue"), ("Selected option 2", None)])
def test_the_audit_action_stays_english(world, run_async, verb, decision):
    preferences.set_ui_language(GROUP, "qps")
    sess = world.session()
    perm = {"tool_summary": "Bash: ls", "tool_info": {"name": "Bash"}}
    member = world.bot._driver_user_by_id(BOB, chat_id=GROUP)
    run_async(world.bot._record_prompt_answer(sess, perm, verb, member, "hook_decision",
                                              decision=decision))
    record = _audit()[-1]
    assert record["action"] == verb
    line = world.lines(GROUP)[-1][0]
    assert "⟦" in line and verb not in line
    # The stored row the card reads stays English too.
    assert sess.tool_history[-1] == (f"🔑 Bash: ls → {verb}", True)


def test_a_tap_audits_english_in_a_translated_chat(world, run_async):
    preferences.set_ui_language(GROUP, "qps")
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    world.tap(run_async, GROUP, BOB, "deny")
    assert _audit()[-1]["action"] == "Denied" and _audit()[-1]["denied"] is True
    assert "⟦" in world.lines(GROUP)[-1][0]


def test_the_warning_key_matches_the_guard():
    assert BIDI_WARNING == bidi_guard.WARNING
    assert callbacks.ANSWER_VERBS["deny"] == "Denied"
