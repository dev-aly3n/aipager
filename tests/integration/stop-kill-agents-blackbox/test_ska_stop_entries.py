"""design-6 success criteria 1-2: Stop on a session waiting for its job's
background agents, from every entry point (the busy card's Stop, /status
Stop, the /stop picker, the /stop command, ``/<label> stop`` and the Mini
App's stop route).

Partitions: 1 agent / 2 agents (plural boundary); with / without a job
shell; a private chat (the criteria's exact texts) / a group (the "by"
variant). Oracle: the bytes written into the terminal (exactly two
``\\x18\\x0b`` writes with a 1.0 s gap, never ``\\x1b``), the texts the
person reads (design-6 section 6), and the session read back through the
same surfaces as before (shown state, bar, Mini App, CLI).
"""

from __future__ import annotations

import importlib
import re

import pytest

from aipager import i18n

kit = importlib.import_module("tests.integration.stop-kill-agents-blackbox.conftest")
EN = i18n.get("en")
ENTRIES = kit.ENTRIES


def _plain(text: str) -> str:
    return re.sub(r"</?b>", "", text)


def _scenario(h, entry, *, agents=(kit.A1, kit.A2), shells=()):
    seen = {}

    async def go():
        await h.waiting(agents=agents, shells=shells)
        seen["before"] = h.sess.shown_state()
        await h.stop(entry)
        seen["after"] = h.sess.shown_state()
    h.run(go())
    return seen


def _stop_lines(h) -> list[str]:
    """The texts that report the Stop (the stopped agents sentence): the
    pop-up or reply, and for the Mini App its notice plus the line it
    posts into the chat (design-6 section 6)."""
    return [_plain(t) for t in h.said() if "background agent" in t]


# ---- the keys --------------------------------------------------------------------

@pytest.mark.parametrize("entry", ENTRIES)
def test_the_session_waits_before_stop(dm, entry):
    """Precondition of every case below: the replay really is the waiting
    state (agents-only rule)."""
    assert _scenario(dm, entry)["before"].state == "tasks"


@pytest.mark.parametrize("entry", ENTRIES)
def test_stop_writes_the_kill_chord_exactly_twice(dm, entry):
    _scenario(dm, entry)
    assert dm.term.writes() == [kit.CHORD, kit.CHORD]


@pytest.mark.parametrize("entry", ENTRIES)
def test_stop_never_writes_escape(dm, entry):
    _scenario(dm, entry)
    assert dm.term.escapes() == 0


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_two_presses_are_one_second_apart(dm, entry):
    _scenario(dm, entry)
    assert dm.term.log == [("write", kit.CHORD), ("gap", 1.0), ("write", kit.CHORD)]


@pytest.mark.parametrize("entry", ENTRIES)
def test_stop_with_a_job_shell_still_writes_only_the_chord(dm, entry):
    _scenario(dm, entry, shells=(kit.SHELL,))
    assert dm.term.writes() == [kit.CHORD, kit.CHORD]


@pytest.mark.parametrize("entry", ENTRIES)
def test_group_stop_writes_only_the_chord(grp, entry):
    _scenario(grp, entry)
    assert grp.term.writes() == [kit.CHORD, kit.CHORD]


@pytest.mark.parametrize("entry", ENTRIES)
def test_stop_types_no_text(dm, entry):
    """The chord only: no Ctrl+U, no Enter, no letters."""
    _scenario(dm, entry)
    assert b"".join(dm.term.writes()).replace(kit.CHORD, b"") == b""


# ---- the card ----------------------------------------------------------------------

@pytest.mark.parametrize("entry", ENTRIES)
def test_card_reads_stopped_two_background_agents(dm, entry):
    _scenario(dm, entry)
    assert dm.card_texts()[-1] == "⚠️ <b>x1</b> · Stopped 2 background agents"


@pytest.mark.parametrize("entry", ENTRIES)
def test_card_reads_stopped_one_background_agent(dm, entry):
    _scenario(dm, entry, agents=(kit.A1,))
    assert dm.card_texts()[-1] == "⚠️ <b>x1</b> · Stopped 1 background agent"


@pytest.mark.parametrize("entry", ENTRIES)
def test_card_with_a_job_shell_adds_the_shell_line(dm, entry):
    _scenario(dm, entry, shells=(kit.SHELL,))
    assert dm.card_texts()[-1].split("\n") == [
        "⚠️ <b>x1</b> · Stopped 2 background agents",
        "🖥 1 background shell keeps running."]


@pytest.mark.parametrize("entry", ENTRIES)
def test_card_with_two_job_shells_uses_the_plural(dm, entry):
    _scenario(dm, entry, shells=(kit.SHELL, "b4wwpa7zz"))
    assert dm.card_texts()[-1].split("\n")[1] == "🖥 2 background shells keep running."


@pytest.mark.parametrize("entry", ENTRIES)
def test_card_loses_its_stop_button(dm, entry):
    _scenario(dm, entry)
    last = [m for mid, _t, m in dm.edits if mid == kit.CARD][-1]
    assert last is None


@pytest.mark.parametrize("entry", ENTRIES)
def test_group_card_names_who_stopped_the_agents(grp, entry):
    _scenario(grp, entry)
    assert grp.card_texts()[-1] == "⚠️ <b>x1</b> · @alice stopped 2 background agents"


# ---- the toast / reply / Mini App notice -------------------------------------------

@pytest.mark.parametrize("entry", ENTRIES)
def test_the_person_reads_stopped_two_background_agents_of_x1(dm, entry):
    _scenario(dm, entry)
    assert {t.split("\n")[0] for t in _stop_lines(dm)} == {
        "⏹ Stopped 2 background agents of x1"}


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_person_reads_the_singular_for_one_agent(dm, entry):
    _scenario(dm, entry, agents=(kit.A1,))
    assert {t.split("\n")[0] for t in _stop_lines(dm)} == {
        "⏹ Stopped 1 background agent of x1"}


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_stop_line_has_no_shell_line_without_job_shells(dm, entry):
    _scenario(dm, entry)
    assert not any("keeps running" in t or "keep running" in t for t in dm.said())


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_stop_line_names_the_shell_that_keeps_running(dm, entry):
    _scenario(dm, entry, shells=(kit.SHELL,))
    assert {tuple(t.split("\n")) for t in _stop_lines(dm)} == {(
        "⏹ Stopped 2 background agents of x1",
        "1 background shell keeps running.")}


@pytest.mark.parametrize("entry", ENTRIES)
def test_group_stop_line_names_who_stopped(grp, entry):
    _scenario(grp, entry)
    assert "⏹ @alice stopped 2 background agents of x1" in [
        t.split("\n")[0] for t in _stop_lines(grp)]


@pytest.mark.parametrize("entry", ("command", "label"))
def test_a_typed_stop_gets_exactly_one_reply(dm, entry):
    """No second "is not working" reply after the agents were stopped."""
    _scenario(dm, entry)
    assert len(dm.replies) == 1


@pytest.mark.parametrize("entry", ENTRIES)
def test_nothing_says_the_session_is_not_working(dm, entry):
    _scenario(dm, entry)
    assert not any("is not working" in t for t in dm.said())


def test_miniapp_answers_stopped_with_the_counts(dm):
    _scenario(dm, "miniapp", shells=(kit.SHELL,))
    status, data = dm.app[-1]
    assert (status, data["status"], data["agents"], data["shells"]) == (
        200, "stopped", 2, 1)


def test_miniapp_notice_is_the_stopped_line(dm):
    _scenario(dm, "miniapp", shells=(kit.SHELL,))
    assert dm.app[-1][1]["detail"] == (
        "⏹ Stopped 2 background agents of x1\n1 background shell keeps running.")


@pytest.mark.parametrize("entry", ENTRIES)
def test_no_new_text_has_an_em_dash(dm, entry):
    _scenario(dm, entry, shells=(kit.SHELL,))
    assert not any("\u2014" in t for t in dm.said() + dm.card_texts())


# ---- every surface idle at once -----------------------------------------------------

@pytest.mark.parametrize("entry", ENTRIES)
def test_shown_state_is_idle_at_once(dm, entry):
    assert _scenario(dm, entry)["after"] == ("idle", 0)


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_job_is_over(dm, entry):
    _scenario(dm, entry)
    assert dm.sess.job_background_open() is False


@pytest.mark.parametrize("entry", ENTRIES)
def test_no_agent_is_left_in_either_table(dm, entry):
    _scenario(dm, entry)
    assert (dm.sess.active_subagents, dm.sess.bg_agents) == ({}, {})


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_bar_shows_idle_without_agents(dm, entry):
    _scenario(dm, entry)
    first = _plain(dm.bot._render_pinned(kit.DM, [dm.sess])[0]).split("\n")[0]
    assert first == "💤 x1 (idle)"


@pytest.mark.parametrize("entry", ENTRIES)
def test_the_bar_keeps_the_shell_running_note(dm, entry):
    _scenario(dm, entry, shells=(kit.SHELL,))
    first = _plain(dm.bot._render_pinned(kit.DM, [dm.sess])[0]).split("\n")[0]
    assert first == "💤 x1 (idle, 1 shell running)"


@pytest.mark.parametrize("entry", ENTRIES)
def test_status_head_reads_idle(dm, entry):
    _scenario(dm, entry)
    head = _plain(dm.bot._status_block(dm.sess, None, tr=EN).split("\n")[0])
    assert head.endswith("x1 · 💤 idle")


@pytest.mark.parametrize("entry", ENTRIES)
def test_switch_line_does_not_say_waiting(dm, entry):
    _scenario(dm, entry)
    text = dm.bot._render_switch_reply(kit.DM, dm.sess)[0]
    assert "waiting for" not in text


@pytest.mark.parametrize("entry", ENTRIES)
def test_miniapp_row_reads_idle_with_no_agents(dm, entry):
    from aipager.miniapp.sessions import session_summary
    import time
    _scenario(dm, entry)
    row = session_summary(dm.sess, time.monotonic())
    assert (row["status"], row["job_agents"]) == ("idle", 0)


@pytest.mark.parametrize("entry", ENTRIES)
def test_cli_row_reads_idle(dm, entry, monkeypatch):
    from aipager import status
    _scenario(dm, entry)
    dm.bot.registry.save()
    monkeypatch.setattr(status, "_live_sessions", lambda: {kit.NAME})
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    rows, _live = status._gather_sessions()
    row = next(r for r in rows if r["name"] == kit.NAME)
    assert (row["status"], row["job_agents"]) == ("IDLE", 0)
