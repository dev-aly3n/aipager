"""Iteration 2, task item #2 — the highest-value item this round: hunt
for the SAME defect shape (a session-scoped verb reaching a side
effect for a session absent from the registry) across every
documented verb, not just the three iteration 1 found.

entrypoints.md documents ~24 session-scoped verbs (17 non-opt +
opt0-3 + 3 legacy resume_mode_*). For each, this file taps a
long-form callback for a session name that is registered NOWHERE --
not in ``registry._sessions``, and (since every "_pending"-style
gate this codebase uses is keyed by session name, per the pre-ship
convention in tests/test_bot_callbacks_perms.py and
tests/test_bot_callbacks.py) therefore also absent from whatever
pending dict that verb's flow might consult -- and asserts that NONE
of the side effects entrypoints.md documents as observable
("Keystroke injection via inject.send_keys", the process-management
equivalents inject.kill_session / inject.launch_session used
elsewhere in this same verb table, and "No new messages") ever fire.

This is deliberately independent of whether the exact toast text is
'Session not found' for every verb -- some verbs are pure
UI-cancel/no-ops (kill-cancel, new_cancel, perms_cancel, perms_wait,
resume_mode_cancel) for which the wording promise is looser (see the
recorded observations at the bottom of this file); what must ALWAYS
hold, per spec.md's explicit worst-case framing ("Silently doing
nothing when an old button is tapped is the worst outcome" -- and by
extension, silently doing SOMETHING is worse still), is that no real
action fires against a name nothing tracks.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.dtach import inject

from _verbs import SESSION_SCOPED_VERBS, OPT_VERBS

CHAT_ID = -100

LEGACY_RESUME_MODE_VERBS = ["resume_mode_ask", "resume_mode_auto", "resume_mode_cancel"]

ALL_VERBS_FOR_SWEEP = SESSION_SCOPED_VERBS + OPT_VERBS + LEGACY_RESUME_MODE_VERBS


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize("verb", ALL_VERBS_FOR_SWEEP)
def test_verb_reaches_no_side_effect_for_a_session_absent_everywhere(
        scb_bot, helpers, monkeypatch, verb):
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock()
    launch_session = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(inject, "send_keys", send_keys)
    monkeypatch.setattr(inject, "kill_session", kill_session)
    monkeypatch.setattr(inject, "launch_session", launch_session)

    bot = scb_bot()
    # A name that was never rendered, never registered, never pending
    # anywhere -- the purest form of "a session absent from the
    # registry" for every gating mechanism this codebase's verbs use.
    ghost = f"claude-ghost-{verb.replace('-', '_')}__d100000000001"
    cb_upd, q = helpers.make_callback_update(f"{ghost}:{verb}", chat_id=CHAT_ID)

    try:
        _run(bot._handle_callback(cb_upd, MagicMock()))
    except Exception as exc:  # noqa: BLE001 - a crash IS the failure mode under test
        pytest.fail(
            f"verb={verb!r}: dispatch raised {exc!r} for a session absent "
            f"everywhere -- entrypoints.md promises 'never a crash'"
        )

    assert kill_session.await_args_list == [], (
        f"verb={verb!r}: inject.kill_session fired for a session absent "
        f"from the registry: {kill_session.await_args_list!r}"
    )
    assert launch_session.await_args_list == [], (
        f"verb={verb!r}: inject.launch_session fired for a session absent "
        f"from the registry: {launch_session.await_args_list!r}"
    )
    assert send_keys.await_args_list == [], (
        f"verb={verb!r}: inject.send_keys fired for a session absent from "
        f"the registry: {send_keys.await_args_list!r}"
    )
    assert bot._app.bot.send_message.await_args_list == [], (
        f"verb={verb!r}: a NEW outward message was sent for a session "
        f"absent from the registry (entrypoints.md: 'No new messages'): "
        f"{bot._app.bot.send_message.await_args_list!r}"
    )
    # "never silence, never a crash" -- some acknowledgement of the tap
    # must occur, whether that's answer_callback_query or an edit of
    # the (stale) message the button lives on.
    acknowledged = (
        q.answer.await_args_list != []
        or q.edit_message_text.await_args_list != []
    )
    assert acknowledged, (
        f"verb={verb!r}: neither answer() nor edit_message_text() was "
        f"called -- a genuinely silent stale-button tap"
    )


@pytest.mark.parametrize("verb", ALL_VERBS_FOR_SWEEP)
def test_verb_registry_gains_no_new_entry_for_the_ghost_name(scb_bot, helpers, monkeypatch, verb):
    """A stricter, orthogonal probe for the same defect class: state
    mutation. Even setting aside the three explicit inject.* calls
    above, a verb must not cause the ghost name to become a real,
    tracked session as a side effect of being tapped (e.g. an errant
    registry.add / launch path that doesn't go through
    inject.launch_session directly)."""
    send_keys = AsyncMock(return_value=True)
    kill_session = AsyncMock()
    launch_session = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(inject, "send_keys", send_keys)
    monkeypatch.setattr(inject, "kill_session", kill_session)
    monkeypatch.setattr(inject, "launch_session", launch_session)

    bot = scb_bot()
    ghost = f"claude-ghostreg-{verb.replace('-', '_')}__d100000000002"
    before = set(bot.registry._sessions.keys())
    cb_upd, q = helpers.make_callback_update(f"{ghost}:{verb}", chat_id=CHAT_ID)
    _run(bot._handle_callback(cb_upd, MagicMock()))
    after = set(bot.registry._sessions.keys())
    assert after == before, (
        f"verb={verb!r}: the registry gained/lost entries from tapping a "
        f"stale button for a name that was never registered: "
        f"before={before!r} after={after!r}"
    )
