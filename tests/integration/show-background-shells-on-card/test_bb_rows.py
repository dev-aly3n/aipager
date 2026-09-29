"""Black-box: a background shell's row on the live busy card.

Written from design.md's success criteria and entrypoints.md's observable
contract only. Every scenario drives the daemon through the replay harness
(conftest.py): Telegram messages through ``_handle_message``, hook
datagrams through the hook receiver, transcript ``queue-operation`` lines
appended to the session's transcript, and asserts on what the chat shows.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import preferences as prefs

CHAT = 256113222
DESC = "run the tests"


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _worker(r):
    return asyncio.ensure_future(r._updates())


async def _turn(r, mid: int = 1, text: str = "please run it") -> None:
    r.say(mid, text)
    await r.updates.join()
    r.prompt_hooks(mid, text)
    await asyncio.sleep(1)


def _card(r) -> str:
    """The latest text of the newest card."""
    assert r.chat.cards, "no card was sent"
    return r.chat.cards[max(r.chat.cards)]["text"]


def _all_card_texts(r) -> str:
    return "\n".join(t for c in r.chat.cards.values()
                     for t in c.get("texts", [c["text"]]))


def _scenario(r, vloop, body, *, settle: float = 20.0) -> str:
    """Open a turn, run ``body(r)``, let the card tick, return the card."""
    prefs.set_preference(CHAT, "layout", "card")

    async def scenario():
        w = _worker(r)
        await _turn(r)
        await body(r)
        await asyncio.sleep(settle)
        w.cancel()
        return _card(r)

    return _run(vloop, scenario())


async def _launch(r, desc=DESC, task_id="bshell1", **kw):
    r.bg_bash(desc, task_id, **kw)
    await asyncio.sleep(5)


# ── launch: equivalence classes of the launch event ─────────────────────────

def test_bb_launch_shows_running_shell_row(replay, vloop):
    card = _scenario(replay, vloop, _launch)
    assert f"⏳ `shell: {DESC} (" in card


def test_bb_launch_row_is_not_marked_done(replay, vloop):
    card = _scenario(replay, vloop, _launch)
    assert "✅ `shell:" not in card


def test_bb_launch_without_description_uses_first_command_line(replay, vloop):
    async def body(r):
        await _launch(r, "", command="pytest -q tests\necho second line")
    card = _scenario(replay, vloop, body)
    assert "⏳ `shell: pytest -q tests (" in card


def test_bb_label_whitespace_is_collapsed(replay, vloop):
    async def body(r):
        await _launch(r, "run   the \t tests")
    card = _scenario(replay, vloop, body)
    assert "shell: run the tests (" in card


def test_bb_label_of_exactly_60_chars_is_not_cut(replay, vloop):
    label = "x" * 60

    async def body(r):
        await _launch(r, label)
    card = _scenario(replay, vloop, body)
    assert f"shell: {label} (" in card


def test_bb_label_of_61_chars_is_cut_with_ellipsis(replay, vloop):
    label = "y" * 61

    async def body(r):
        await _launch(r, label)
    card = _scenario(replay, vloop, body)
    assert "y" * 59 + "…" in card


def test_bb_label_of_61_chars_never_shows_in_full(replay, vloop):
    label = "y" * 61

    async def body(r):
        await _launch(r, label)
    card = _scenario(replay, vloop, body)
    assert label not in card


def test_bb_non_bash_tool_with_background_id_is_no_shell(replay, vloop):
    async def body(r):
        await _launch(r, tool_name="Read")
    card = _scenario(replay, vloop, body)
    assert "shell:" not in card


def test_bb_subagent_shell_is_ignored(replay, vloop):
    async def body(r):
        r.hook(hook_event_name="SubagentStart", agent_id="a1",
               agent_type="pipeline-runner")
        await asyncio.sleep(1)
        await _launch(r, agent_id="a1")
    card = _scenario(replay, vloop, body)
    assert "shell:" not in card


def test_bb_ctrl_b_backgrounded_shell_is_tracked(replay, vloop):
    async def body(r):
        await _launch(r, response_extra={"backgroundedByUser": True})
    card = _scenario(replay, vloop, body)
    assert f"⏳ `shell: {DESC} (" in card


def test_bb_timeout_backgrounded_shell_is_tracked(replay, vloop):
    async def body(r):
        await _launch(r, response_extra={"timedOutAfterMs": 120000})
    card = _scenario(replay, vloop, body)
    assert f"⏳ `shell: {DESC} (" in card


# ── ends seen mid-turn (transcript queue-operation lines) ──────────────────

def _end_mid_turn(status, summary, *, absorb_only=False, task_id="bshell1"):
    async def body(r):
        await _launch(r)
        block = r.notification(task_id, status, summary)
        if absorb_only:
            r.absorb(block)
        else:
            r.enqueue(block)
            r.absorb(block)
        await asyncio.sleep(3)
    return body


def test_bb_mid_turn_completed_settles_row_done(replay, vloop):
    card = _scenario(replay, vloop, _end_mid_turn(
        "completed", f'Background command "{DESC}" completed (exit code 0)'))
    assert f"✅ `shell: {DESC} - done (" in card


def test_bb_mid_turn_completed_leaves_no_running_row(replay, vloop):
    card = _scenario(replay, vloop, _end_mid_turn(
        "completed", f'Background command "{DESC}" completed (exit code 0)'))
    assert f"⏳ `shell: {DESC}" not in card


def test_bb_mid_turn_failed_row_shows_exit_code(replay, vloop):
    card = _scenario(replay, vloop, _end_mid_turn(
        "failed", f'Background command "{DESC}" failed with exit code 3'))
    assert f"❌ `shell: {DESC} - failed (exit 3)`" in card


def test_bb_mid_turn_killed_row_reads_stopped(replay, vloop):
    card = _scenario(replay, vloop, _end_mid_turn(
        "killed", f'Background command "{DESC}" was stopped'))
    assert f"⏹ `shell: {DESC} - stopped (" in card


def test_bb_end_seen_only_in_remove_line_still_settles(replay, vloop):
    """entrypoints: every queue-operation line carrying the notification is
    read, not only ``enqueue``."""
    card = _scenario(replay, vloop, _end_mid_turn(
        "completed", f'Background command "{DESC}" completed (exit code 0)',
        absorb_only=True))
    assert f"✅ `shell: {DESC} - done (" in card


def test_bb_monitor_event_without_status_ends_nothing(replay, vloop):
    card = _scenario(replay, vloop, _end_mid_turn(
        None, 'Monitor event: "log watcher" matched'))
    assert f"⏳ `shell: {DESC} (" in card


def test_bb_notification_for_unknown_id_ends_nothing(replay, vloop):
    card = _scenario(replay, vloop, _end_mid_turn(
        "completed", 'Background command "other" completed (exit code 0)',
        task_id="bother99"))
    assert f"⏳ `shell: {DESC} (" in card


def test_bb_unknown_status_value_ends_nothing(replay, vloop):
    card = _scenario(replay, vloop, _end_mid_turn(
        "running", f'Background command "{DESC}" is running'))
    assert f"⏳ `shell: {DESC} (" in card


def test_bb_agent_result_quoting_a_shell_notification_ends_nothing(replay,
                                                                    vloop):
    async def body(r):
        await _launch(r)
        inner = r.notification(
            "bshell1", "completed",
            f'Background command "{DESC}" completed (exit code 0)')
        r.enqueue(r.notification("a77", "completed", "Agent finished",
                                 result="I saw this:\n" + inner))
        await asyncio.sleep(3)
    card = _scenario(replay, vloop, body)
    assert f"⏳ `shell: {DESC} (" in card


def test_bb_two_blocks_in_one_line_end_both_shells(replay, vloop):
    async def body(r):
        await _launch(r, "first job", "bone1")
        await _launch(r, "second job", "btwo2")
        r.enqueue(r.notification(
            "bone1", "completed",
            'Background command "first job" completed (exit code 0)')
            + "\n" + r.notification(
            "btwo2", "failed",
            'Background command "second job" failed with exit code 1'))
        await asyncio.sleep(3)
    card = _scenario(replay, vloop, body)
    assert ("✅ `shell: first job - done (" in card
            and "❌ `shell: second job - failed (exit 1)`" in card)


def test_bb_repeated_end_lines_are_harmless(replay, vloop):
    async def body(r):
        await _launch(r)
        block = r.notification(
            "bshell1", "failed",
            f'Background command "{DESC}" failed with exit code 2')
        for _ in range(3):
            r.enqueue(block)
            await asyncio.sleep(3)
    card = _scenario(replay, vloop, body)
    assert card.count(f"❌ `shell: {DESC} - failed (exit 2)`") == 1


# ── TaskStop / KillShell ────────────────────────────────────────────────────

def test_bb_taskstop_settles_row_stopped(replay, vloop):
    async def body(r):
        await _launch(r)
        r.task_stop("bshell1")
        await asyncio.sleep(3)
    card = _scenario(replay, vloop, body)
    assert f"⏹ `shell: {DESC} - stopped (" in card


def test_bb_killshell_with_shell_id_settles_row_stopped(replay, vloop):
    async def body(r):
        await _launch(r)
        r.task_stop("bshell1", tool_name="KillShell", key="shell_id")
        await asyncio.sleep(3)
    card = _scenario(replay, vloop, body)
    assert f"⏹ `shell: {DESC} - stopped (" in card


def test_bb_taskstop_with_shell_id_key_settles_row_stopped(replay, vloop):
    async def body(r):
        await _launch(r)
        r.task_stop("bshell1", key="shell_id")
        await asyncio.sleep(3)
    card = _scenario(replay, vloop, body)
    assert f"⏹ `shell: {DESC} - stopped (" in card


def test_bb_taskstop_of_unknown_id_leaves_row_running(replay, vloop):
    async def body(r):
        await _launch(r)
        r.task_stop("bnotmine")
        await asyncio.sleep(3)
    card = _scenario(replay, vloop, body)
    assert f"⏳ `shell: {DESC} (" in card


def test_bb_taskstop_of_one_shell_leaves_the_other_running(replay, vloop):
    async def body(r):
        await _launch(r, "first job", "bone1")
        await _launch(r, "second job", "btwo2")
        r.task_stop("bone1")
        await asyncio.sleep(3)
    card = _scenario(replay, vloop, body)
    assert "⏳ `shell: second job (" in card


# ── settled marks on the Finished card ─────────────────────────────────────

def _finished_card(r, vloop, status, summary):
    prefs.set_preference(CHAT, "layout", "card")

    async def scenario():
        w = _worker(r)
        await _turn(r)
        await _launch(r)
        r.enqueue(r.notification("bshell1", status, summary))
        await asyncio.sleep(3)
        r.tool("look at the output")
        await asyncio.sleep(2)
        r.stop("all done")
        await asyncio.sleep(20)
        w.cancel()
        return _card(r)

    return _run(vloop, scenario())


def test_bb_failed_mark_survives_onto_finished_card(replay, vloop):
    card = _finished_card(replay, vloop, "failed",
                          f'Background command "{DESC}" failed with exit '
                          'code 1')
    assert f"❌ `shell: {DESC} - failed (exit 1)`" in card


def test_bb_stopped_mark_survives_onto_finished_card(replay, vloop):
    card = _finished_card(replay, vloop, "killed",
                          f'Background command "{DESC}" was stopped')
    assert f"⏹ `shell: {DESC} - stopped (" in card


def test_bb_ended_shell_lets_the_turn_finish(replay, vloop):
    """Complement of the interim case: a shell that ended mid-turn holds
    nothing open, so the Stop settles the card (no Stop button left)."""
    _finished_card(replay, vloop, "completed",
                   f'Background command "{DESC}" completed (exit code 0)')
    assert replay.chat.live_cards() == []


# ── no em dash, no extra message per shell ──────────────────────────────────

def test_bb_no_em_dash_in_any_row(replay, vloop):
    async def body(r):
        await _launch(r, "one", "bone1")
        await _launch(r, "two", "btwo2")
        await _launch(r, "three", "bthree3")
        await _launch(r, "four", "bfour4")
        r.enqueue(r.notification("bone1", "completed",
                                 'Background command "one" completed '
                                 '(exit code 0)'))
        r.enqueue(r.notification("btwo2", "failed",
                                 'Background command "two" failed with exit '
                                 'code 1'))
        r.enqueue(r.notification("bthree3", "killed",
                                 'Background command "three" was stopped'))
        await asyncio.sleep(3)
    _scenario(replay, vloop, body)
    assert "—" not in _all_card_texts(replay)


@pytest.mark.parametrize("n", [1, 5])
def test_bb_no_message_is_sent_per_shell(replay, vloop, n):
    prefs.set_preference(CHAT, "layout", "card")
    r = replay

    async def scenario():
        w = _worker(r)
        await _turn(r)
        for i in range(n):
            await _launch(r, f"job {i}", f"bjob{i}")
        for i in range(n):
            r.enqueue(r.notification(
                f"bjob{i}", "completed",
                f'Background command "job {i}" completed (exit code 0)'))
        await asyncio.sleep(3)
        r.stop("done")
        await asyncio.sleep(20)
        w.cancel()

    _run(vloop, scenario())
    sent = (len(r.chat.cards), len(r.chat.answers), len(r.chat.plain))
    assert sent == (1, 1, 0)
