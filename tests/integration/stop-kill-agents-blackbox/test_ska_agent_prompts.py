"""design-6 success criterion: an agent's open prompt after Stop.

Claude's own turn has ended; a background agent then asks (the "needs
you" case where every open prompt is an agent's). Each prompt is its own
message, the card keeps only its Stop (an agent asking after Claude's turn
ended never lands on the job's waiting card). Stop (from the card, the
/stop command,
``/<label> stop`` and the Mini App) presses the chord; afterwards the
prompt's own message has no buttons and says its agent was stopped, and a
late tap on any of the prompt's buttons types nothing and answers no hook.
"""

from __future__ import annotations

import importlib

import pytest

kit = importlib.import_module("tests.integration.stop-kill-agents-blackbox.conftest")
ENTRIES = ("card", "command", "label", "miniapp")
STOPPED_NOTE = "⏹ This prompt closed because its background agent was stopped."


def _prompt_messages(h):
    return [m for m in h.sent if m.get("reply_markup") is not None]


def _buttons(markup):
    rows = getattr(markup, "inline_keyboard", None) or []
    return [(b.text, b.callback_data) for row in rows for b in row]


def _scenario(h, entry, *, two=True):
    seen = {}

    async def go():
        await h.waiting()
        await h.agent_permission(kit.A1)
        if two:
            await h.agent_permission(kit.A2, "rm -rf cache/")
        seen["before"] = h.sess.shown_state().state
        seen["card_buttons"] = _buttons(
            [m for mid, _t, m in h.edits if mid == kit.CARD][-1])
        seen["msgs"] = [(m["msg_id"], m["text"], _buttons(m["reply_markup"]))
                        for m in _prompt_messages(h)]
        await h.stop(entry)
        seen["writes_after_stop"] = len(h.term.writes())
        seen["decisions_after_stop"] = len(h.decisions)
    h.run(go())
    return seen


def _late_taps(h, seen, which):
    """Tap every Allow / Deny the person still saw before the Stop, on the
    first prompt's message or the second's."""
    mid, _text, buttons = seen["msgs"][0 if which == "first" else 1]

    async def go():
        for _label, data in buttons:
            await h.tap_data(data, mid)
    h.run(go())


@pytest.mark.parametrize("entry", ENTRIES)
def test_precondition_the_session_needs_you(dm, entry):
    assert _scenario(dm, entry)["before"] == "needs_you"


@pytest.mark.parametrize("entry", ENTRIES)
def test_precondition_each_prompt_is_its_own_message(dm, entry):
    seen = _scenario(dm, entry)
    assert len(seen["msgs"]) == 2
    assert all(len(b) == 2 for _m, _t, b in seen["msgs"])


@pytest.mark.parametrize("entry", ENTRIES)
def test_precondition_the_card_offers_no_answer(dm, entry):
    seen = _scenario(dm, entry)
    assert not any("Allow" in label or "Deny" in label
                   for label, _d in seen["card_buttons"])


@pytest.mark.parametrize("entry", ENTRIES)
def test_stop_presses_the_chord_twice(dm, entry):
    _scenario(dm, entry)
    assert dm.term.writes() == [kit.CHORD, kit.CHORD]


@pytest.mark.parametrize("entry", ENTRIES)
def test_stop_types_no_escape_into_the_agents_dialog(dm, entry):
    _scenario(dm, entry)
    assert dm.term.escapes() == 0


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_prompt_message_loses_its_buttons(dm, entry):
    seen = _scenario(dm, entry)
    mid = seen["msgs"][0][0]
    assert [m for i, _t, m in dm.edits if i == mid][-1] is None


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_prompt_message_says_its_agent_was_stopped(dm, entry):
    seen = _scenario(dm, entry)
    mid = seen["msgs"][0][0]
    last = [t for i, t, _m in dm.edits if i == mid][-1]
    assert last.split("\n")[-1] == STOPPED_NOTE


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_prompt_message_keeps_what_it_asked(dm, entry):
    seen = _scenario(dm, entry)
    mid, text, _b = seen["msgs"][0]
    last = [t for i, t, _m in dm.edits if i == mid][-1]
    assert last.startswith(text)


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_card_loses_its_buttons(dm, entry):
    _scenario(dm, entry)
    assert [m for i, _t, m in dm.edits if i == kit.CARD][-1] is None


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("which", ["first", "second"])
def test_a_late_tap_types_nothing(dm, entry, which):
    seen = _scenario(dm, entry)
    _late_taps(dm, seen, which)
    assert len(dm.term.writes()) == seen["writes_after_stop"]


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("which", ["first", "second"])
def test_a_late_tap_answers_no_hook(dm, entry, which):
    seen = _scenario(dm, entry)
    _late_taps(dm, seen, which)
    assert len(dm.decisions) == seen["decisions_after_stop"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_after_stop_the_session_is_idle(dm, entry):
    _scenario(dm, entry)
    assert dm.sess.shown_state() == ("idle", 0)


@pytest.mark.parametrize("entry", ENTRIES)
def test_one_agent_prompt_also_presses_the_chord(dm, entry):
    _scenario(dm, entry, two=False)
    assert dm.term.writes() == [kit.CHORD, kit.CHORD]


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_card_reads_stopped_two_background_agents(dm, entry):
    _scenario(dm, entry)
    assert dm.card_texts()[-1] == "⚠️ <b>x1</b> · Stopped 2 background agents"


@pytest.mark.parametrize("which", ["first", "second"])
def test_control_the_same_tap_before_stop_answers_the_hook(dm, which):
    """The late-tap tests are not vacuous: before the Stop, the very same
    Allow answers the agent's waiting hook."""
    seen = {}

    async def go():
        await dm.waiting()
        await dm.agent_permission(kit.A1)
        await dm.agent_permission(kit.A2, "rm -rf cache/")
        msg = _prompt_messages(dm)[0 if which == "first" else 1]
        data = dict(_buttons(msg["reply_markup"]))["✅ Allow"]
        await dm.tap_data(data, msg["msg_id"])
        seen["decisions"] = len(dm.decisions)
    dm.run(go())
    assert seen["decisions"] == 1
