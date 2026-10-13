"""A still-running note absorbed mid-turn (a ``queue-operation`` line in
the transcript) revives a just-stopped agent while the card animates
(design.md success criterion 8; entrypoints.md "Mid-turn note").

No link here (the S1 signal is missing): P's pause stop finishes it, and
only Claude's note about P brings it back.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aipager.state import Status
from tests import agents_line_waiting_bb as h

P, P_TYPE = h.P, h.P_TYPE

STILL_RUNNING = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed</status>\n"
    "<summary>Agent \"Ship the settings fix\" finished</summary>\n"
    "<note>This agent stopped with background work of its own still running. "
    "It may resume on its own when that work completes or reports, and the same "
    "task-id notifies again if it does; the result below may be interim.</note>\n"
    "<result>Waiting on the reviewer.</result>\n</task-notification>")
DONE_NOTE = (
    "<task-notification>\n<task-id>{tid}</task-id>\n<status>completed</status>\n"
    "<note>A task-notification fires each time this agent stops with no live "
    "background children of its own.</note>\n</task-notification>")


def _queue_line(r, content, operation="enqueue"):
    stamp = datetime.fromtimestamp(r.loop.wall(), timezone.utc).isoformat()
    h.append(r.transcript, {"type": "queue-operation", "operation": operation,
                            "content": content, "timestamp": stamp})


@pytest.fixture
def stopped_mid_turn(rig):
    h.append(rig.transcript, h.user_line())
    rig.prompt("[via Telegram · @alice]\nship the settings fix")
    rig.agent_call(P, P_TYPE)          # by the main loop: no link from P
    rig.sleep(3)
    rig.stop(P, P_TYPE)
    assert rig.sess.status is Status.BUSY
    return rig


def test_without_a_note_the_stop_finishes_it(stopped_mid_turn):
    """Control: P really was taken off by its stop."""
    stopped_mid_turn.sleep(10)
    assert P not in stopped_mid_turn.sess.bg_agents


@pytest.mark.parametrize("operation", ["enqueue", "remove"])
def test_the_still_running_note_brings_it_back(stopped_mid_turn, operation):
    _queue_line(stopped_mid_turn, STILL_RUNNING.format(tid=P), operation)
    stopped_mid_turn.sleep(10)
    assert P in stopped_mid_turn.sess.bg_agents


def test_the_revived_agent_is_working_on_the_answer(stopped_mid_turn):
    _queue_line(stopped_mid_turn, STILL_RUNNING.format(tid=P))
    stopped_mid_turn.sleep(10)
    assert h.is_working_row(h.row_of(stopped_mid_turn.answer(), P_TYPE))


def test_the_revived_agent_never_shows_done_while_it_runs(stopped_mid_turn):
    _queue_line(stopped_mid_turn, STILL_RUNNING.format(tid=P))
    stopped_mid_turn.sleep(10)
    stopped_mid_turn.answer()
    stopped_mid_turn.tick(6)
    stopped_mid_turn.tick(31)
    assert "✅" not in stopped_mid_turn.last_block()


def test_the_finished_note_does_not_bring_it_back(stopped_mid_turn):
    _queue_line(stopped_mid_turn, DONE_NOTE.format(tid=P))
    stopped_mid_turn.sleep(10)
    assert P not in stopped_mid_turn.sess.bg_agents


def test_a_note_for_an_unknown_task_adds_no_agent(stopped_mid_turn):
    _queue_line(stopped_mid_turn, STILL_RUNNING.format(tid="a" + "0" * 16))
    stopped_mid_turn.sleep(10)
    assert "a" + "0" * 16 not in stopped_mid_turn.sess.bg_agents


def test_a_note_quoted_in_ordinary_prose_is_no_signal(stopped_mid_turn):
    """Error guess: the words alone, outside a task notification."""
    _queue_line(stopped_mid_turn,
                f"please check {P}: it says background work of its own still running")
    stopped_mid_turn.sleep(10)
    assert P not in stopped_mid_turn.sess.bg_agents


def test_its_later_final_stop_finishes_it(stopped_mid_turn):
    _queue_line(stopped_mid_turn, STILL_RUNNING.format(tid=P))
    stopped_mid_turn.sleep(10)
    stopped_mid_turn.start(P, P_TYPE)
    stopped_mid_turn.stop(P, P_TYPE)
    assert P not in stopped_mid_turn.sess.bg_agents
