"""Task instruction #6 — the permission prompt specifically.

This is the case that motivated the whole ship (spec.md: "a session
named with 33+ characters makes the permission prompt unsendable").
design.md's success criteria: "A 40+ character label gets a working
permission prompt: Allow, Deny, Allow-always and Stop all fire the
correct keystroke injection."

Uses a 40-character label -- past spec.md's own measured break point
for `:allow_always` (32 chars) and `:allow`/`:retry` (39 chars), the
tightest verbs in the ORIGINAL long-form encoding -- to prove the
short-form keyboard is both sendable (budget) and correctly wired
(each button reaches the right action for the right session).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.dtach import inject
from aipager.state import Status, TrackedSession

from _verbs import TELEGRAM_CALLBACK_DATA_LIMIT

CHAT_ID = -100
LABEL_40 = "L" * 40


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def prompt(scb_bot, monkeypatch):
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "send_keys", sent)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    bot = scb_bot()
    sess = TrackedSession(name=f"claude-{LABEL_40}__d123456789012", label=LABEL_40,
                           status=Status.BUSY, scope_chat_id=CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    # The with-rule case: Allow-always is offered (and navigates) only when
    # the hook reported permission suggestions ("allow-always-auto-mode-guard").
    sess.pending_permission = {"tool_summary": "Bash: x",
                               "tool_info": {"name": "Bash", "always_available": True}}
    kb = bot._build_permission_keyboard(sess)
    return bot, sess, kb, sent


def test_permission_keyboard_is_sendable_for_a_40_char_label(prompt):
    """The core regression this ship exists to fix: this exact scenario
    (a 33+ char label) made the pre-ship long-form keyboard exceed 64
    bytes and get silently rejected by Telegram -- the whole keyboard,
    not just one button."""
    _bot, _sess, kb, _sent = prompt
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs, "expected a non-empty permission keyboard"
    over = [(cb, len(cb.encode())) for cb in cbs
            if len(cb.encode()) > TELEGRAM_CALLBACK_DATA_LIMIT]
    assert not over, f"permission keyboard unsendable for a 40-char label: {over}"


def test_permission_keyboard_has_all_four_buttons(prompt):
    _bot, _sess, kb, _sent = prompt
    verbs = {b.callback_data.rsplit(":", 1)[1]
             for row in kb.inline_keyboard for b in row}
    assert verbs == {"allow", "deny", "allow_always", "stop"}


def _tap(bot, kb, verb, helpers):
    cb = next(b.callback_data for row in kb.inline_keyboard for b in row
              if b.callback_data.endswith(f":{verb}"))
    cb_upd, q = helpers.make_callback_update(cb, chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    return q


def test_allow_button_injects_enter_on_the_right_session(prompt, helpers):
    bot, sess, kb, sent = prompt
    _tap(bot, kb, "allow", helpers)
    sent.assert_awaited_with(sess.name, "Enter")


def test_deny_button_injects_downs_then_enter_on_the_right_session(prompt, helpers):
    bot, sess, kb, sent = prompt
    _tap(bot, kb, "deny", helpers)
    calls = [c.args for c in sent.await_args_list]
    assert calls[-1] == (sess.name, "Enter")
    assert all(c == (sess.name, "Down") for c in calls[:-1])
    assert len(calls) >= 2, "expected at least one Down before the final Enter"


def test_allow_always_button_injects_down_then_enter_on_the_right_session(
        prompt, helpers):
    bot, sess, kb, sent = prompt
    _tap(bot, kb, "allow_always", helpers)
    calls = [c.args for c in sent.await_args_list]
    assert calls == [(sess.name, "Down"), (sess.name, "Enter")]


def test_stop_button_reaches_the_right_session(prompt, helpers):
    bot, sess, kb, sent = prompt
    _tap(bot, kb, "stop", helpers)
    assert sent.await_count > 0
    assert all(c.args[0] == sess.name for c in sent.await_args_list), (
        "stop injected a keystroke into a DIFFERENT session than the one "
        "the button was rendered for"
    )


def test_each_button_answers_the_operator_confirming_which_session(prompt, helpers):
    """Every tap should acknowledge the operator with feedback naming
    the session it acted on -- not silence, and not a generic message
    that could apply to any session (the whole point of index-based
    resolution vs. accidentally hitting the wrong row)."""
    bot, sess, kb, _sent = prompt
    for verb in ("allow", "deny", "allow_always", "stop"):
        # rebuild a fresh keyboard/query pair per tap; kb is reusable, but
        # give each verb its own mocked send_keys state via the shared
        # fixture's sent, already patched globally for the whole test.
        q = _tap(bot, kb, verb, helpers)
        assert q.answer.await_args is not None, f"{verb}: expected an answer"
        assert LABEL_40 in q.answer.await_args.args[0], (
            f"{verb}: answer {q.answer.await_args!r} does not confirm which "
            f"session was acted on"
        )
