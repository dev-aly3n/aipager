"""design-6 success criteria on the other Stop modes, and the refusals.

Partitions of the session Stop is tapped on:
- BUSY with job agents (Claude's own turn runs): today's two Escapes, no
  chord, today's texts (control);
- INTERACTIVE on Claude's own prompt, and on an agent's prompt while
  Claude's own turn still runs: today's two Escapes (control);
- IDLE with only the grace window, or only job shells: no keys at all, the
  job ends, the shells line is shown (requirement f);
- IDLE waiting, with a dialog open in the terminal: refused with
  ``stop.dialog_open``, nothing written, nothing changed.

Error guessing on the chord itself (design-6 section 2):
- a turn starts between the two presses (a message landed): the second
  press is not written;
- the clock says the second press would land past 2.5 s (boundary 2.5 /
  2.75): not written, and the person is told the agents were not stopped.
"""

from __future__ import annotations

import importlib
import itertools
import re

import pytest

kit = importlib.import_module("tests.integration.stop-kill-agents-blackbox.conftest")
ENTRIES = kit.ENTRIES
DIALOG = "x1 has a dialog open in the terminal. Answer it there, then try Stop again."
NOT_STOPPED = "The background agents of x1 could not be stopped. Try Stop again."


def _plain(text: str) -> str:
    return re.sub(r"</?b>", "", text)


def _run(h, setup, entry):
    seen = {}

    async def go():
        await setup(h)
        seen["before"] = (h.sess.status, h.sess.shown_state(),
                          h.sess.job_background_open())
        await h.stop(entry)
        seen["after"] = (h.sess.status, h.sess.shown_state(),
                         h.sess.job_background_open())
    h.run(go())
    return seen


# ---- setups --------------------------------------------------------------------------

async def _busy(h):
    await h.prompt()
    await h.launch(kit.A1)
    await h.launch(kit.A2)


async def _own_prompt(h):
    await _busy(h)
    await h.own_permission()


async def _agent_prompt_in_turn(h):
    await _busy(h)
    await h.agent_permission(kit.A1)


async def _grace(h):
    await h.prompt()
    await h.launch(kit.A1)
    await h.turn_stop(agents=(kit.A1,))
    await h.hook("SubagentStop", agent_id=kit.A1, agent_type="general-purpose")


async def _shells(h):
    await h.waiting(agents=(), shells=(kit.SHELL,))


async def _term_dialog(h):
    await h.waiting()
    h.sess.term_dialog = {"epoch": 1, "pid": 4100, "kind": "other"}


# ---- BUSY and Claude's own prompt keep today's keys --------------------------------

CONTROLS = {"busy": _busy, "own_prompt": _own_prompt,
            "agent_prompt_in_turn": _agent_prompt_in_turn}


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("case", list(CONTROLS))
def test_control_types_two_escapes(dm, case, entry):
    _run(dm, CONTROLS[case], entry)
    assert dm.term.escapes() == 2


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("case", list(CONTROLS))
def test_control_presses_no_chord(dm, case, entry):
    _run(dm, CONTROLS[case], entry)
    assert dm.term.chords() == 0


@pytest.mark.parametrize("entry", ENTRIES)
def test_busy_card_keeps_todays_stopped_text(dm, entry):
    _run(dm, _busy, entry)
    assert dm.card_texts()[-1] == "⚠️ <b>x1</b> · Stopped"


@pytest.mark.parametrize("entry", ENTRIES)
def test_busy_stop_says_nothing_about_background_agents(dm, entry):
    _run(dm, _busy, entry)
    assert not any("background agent" in t for t in dm.said())


@pytest.mark.parametrize("case", list(CONTROLS))
def test_control_was_not_the_waiting_state(dm, case):
    """Precondition: Claude's own turn really runs in each control."""
    seen = _run(dm, CONTROLS[case], "card")
    assert seen["before"][1].state in ("busy", "needs_you")


# ---- grace window only, shells only: no keys, the job ends -------------------------

NO_KEYS = {"grace": _grace, "shells": _shells}


@pytest.mark.parametrize("case", list(NO_KEYS))
def test_no_keys_case_is_idle_with_an_open_job(dm, case):
    """Precondition (amend-5): idle on every surface, but the job is open."""
    seen = _run(dm, NO_KEYS[case], "card")
    assert (seen["before"][1].state, seen["before"][2]) == ("idle", True)


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("case", list(NO_KEYS))
def test_no_keys_case_writes_nothing(dm, case, entry):
    _run(dm, NO_KEYS[case], entry)
    assert dm.term.writes() == []


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("case", list(NO_KEYS))
def test_no_keys_case_ends_the_job(dm, case, entry):
    seen = _run(dm, NO_KEYS[case], entry)
    assert seen["after"][2] is False


@pytest.mark.parametrize("entry", ENTRIES)
def test_grace_stop_card_reads_todays_stopped(dm, entry):
    _run(dm, _grace, entry)
    assert dm.card_texts()[-1] == "⚠️ <b>x1</b> · Stopped"


@pytest.mark.parametrize("entry", ENTRIES)
def test_shells_stop_card_adds_the_shell_line(dm, entry):
    _run(dm, _shells, entry)
    assert dm.card_texts()[-1].split("\n") == [
        "⚠️ <b>x1</b> · Stopped", "🖥 1 background shell keeps running."]


@pytest.mark.parametrize("entry", ENTRIES)
def test_shells_stop_line_says_the_shell_keeps_running(dm, entry):
    _run(dm, _shells, entry)
    assert any(_plain(t).split("\n")[-1] == "1 background shell keeps running."
               for t in dm.said())


@pytest.mark.parametrize("entry", ENTRIES)
def test_shells_stop_leaves_the_bar_note(dm, entry):
    _run(dm, _shells, entry)
    first = _plain(dm.bot._render_pinned(kit.DM, [dm.sess])[0]).split("\n")[0]
    assert first == "💤 x1 (idle, 1 shell running)"


# ---- a dialog open in the terminal: refused, nothing changes ------------------------

@pytest.mark.parametrize("entry", ENTRIES)
def test_terminal_dialog_writes_nothing(dm, entry):
    _run(dm, _term_dialog, entry)
    assert dm.term.writes() == []


@pytest.mark.parametrize("entry", ENTRIES)
def test_terminal_dialog_refusal_text(dm, entry):
    _run(dm, _term_dialog, entry)
    assert DIALOG in [_plain(t) for t in dm.said()]


@pytest.mark.parametrize("entry", ENTRIES)
def test_terminal_dialog_leaves_the_session_waiting(dm, entry):
    seen = _run(dm, _term_dialog, entry)
    assert seen["after"] == seen["before"]


@pytest.mark.parametrize("entry", ENTRIES)
def test_terminal_dialog_keeps_the_agents(dm, entry):
    _run(dm, _term_dialog, entry)
    assert sorted(dm.sess.active_subagents) == sorted([kit.A1, kit.A2])


@pytest.mark.parametrize("entry", ENTRIES)
def test_terminal_dialog_card_not_settled(dm, entry):
    _run(dm, _term_dialog, entry)
    assert not any("Stopped" in t for t in dm.card_texts())


@pytest.mark.parametrize("entry", ("command", "label"))
def test_terminal_dialog_typed_stop_gets_one_reply(dm, entry):
    _run(dm, _term_dialog, entry)
    assert len(dm.replies) == 1


def test_terminal_dialog_miniapp_answers_409_dialog_open(dm):
    _run(dm, _term_dialog, "miniapp")
    status, data = dm.app[-1]
    assert (status, data.get("error")) == (409, "dialog_open")


# ---- the chord's own guards (error guessing) -------------------------------------

def _turn_starts_in_the_gap(h):
    async def landed():
        await h.prompt("one more thing")
    h.term.during_gap.append(landed)


@pytest.mark.parametrize("entry", ("card", "miniapp", "command"))
def test_a_turn_starting_between_presses_stops_the_second_press(dm, entry):
    _turn_starts_in_the_gap(dm)
    _run(dm, kit.Harness.waiting, entry)
    assert dm.term.chords() == 1


@pytest.mark.parametrize("entry", ("card", "miniapp", "command"))
def test_a_turn_starting_between_presses_is_stopped_with_escapes(dm, entry):
    """design-6 4e: a turn started meanwhile, so Stop does what it does on a
    working session."""
    _turn_starts_in_the_gap(dm)
    _run(dm, kit.Harness.waiting, entry)
    assert dm.term.escapes() == 2


def _clock(h, gap):
    h.term.clock_values = itertools.chain([100.0, 100.0 + gap],
                                          itertools.count(200.0, 0.25))


@pytest.mark.parametrize("gap, chords", [(2.5, 2), (2.75, 1), (10.0, 1)],
                         ids=["at-limit", "just-over", "far-over"])
def test_second_press_only_inside_the_window(dm, gap, chords):
    _clock(dm, gap)
    _run(dm, kit.Harness.waiting, "card")
    assert dm.term.chords() == chords


@pytest.mark.parametrize("entry", ENTRIES)
def test_late_second_press_tells_the_agents_were_not_stopped(dm, entry):
    _clock(dm, 10.0)
    _run(dm, kit.Harness.waiting, entry)
    assert NOT_STOPPED in [_plain(t) for t in dm.said()]


@pytest.mark.parametrize("entry", ENTRIES)
def test_late_second_press_keeps_the_agents_counted(dm, entry):
    _clock(dm, 10.0)
    seen = _run(dm, kit.Harness.waiting, entry)
    assert seen["after"][1] == ("tasks", 2)


def test_late_second_press_miniapp_answers_409(dm):
    _clock(dm, 10.0)
    _run(dm, kit.Harness.waiting, "miniapp")
    status, data = dm.app[-1]
    assert (status, data.get("error")) == (409, "agents_not_stopped")


def test_send_keys_refuses_the_chord_name(dm):
    """design-6 section 2: a lone press is never sent through send_keys."""
    from aipager.dtach import inject
    ok = dm.run(inject.send_keys(kit.NAME, "KillAgents"))
    assert (ok, dm.term.writes()) == (False, [])


def test_the_chord_is_the_kill_agents_key(dm):
    from aipager.dtach import inject
    assert inject.KEYS["KillAgents"] == "\x18\x0b"

