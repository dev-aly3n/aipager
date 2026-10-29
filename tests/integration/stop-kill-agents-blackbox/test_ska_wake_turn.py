"""design-6 success criterion: the turn Claude Code starts by itself after
aipager's Stop pressed the chord is not a person's terminal turn.

The wake, per the probe (G1, G6): a UserPromptSubmit whose prompt is the
plain sentence ``N background agents were stopped by the user: ...`` (no
``<task-notification>`` wrapper, no Telegram marker), the transcript's
queue-operation, ``agents_killed`` and ``user`` (origin task-notification)
lines, then a Stop whose ``background_tasks`` is ``[]``.

Partitions: transcript flushed / not yet flushed at the UserPromptSubmit
(it is written lazily); 2 agents / 1 agent (the two sentence shapes);
private chat / group (attribution). A second markerless prompt later in
the window is a person's terminal turn (the recognition is one-shot).
"""

from __future__ import annotations

import importlib
import time

import pytest

kit = importlib.import_module("tests.integration.stop-kill-agents-blackbox.conftest")
ANSWER = "Noted, the agents are stopped."
ENTRIES = ("card", "miniapp", "command")


def _scenario(h, entry="card", *, flushed=True, agents=(kit.A1, kit.A2),
              second: str | None = None):
    seen = {}
    sentence = kit.WAKE_TWO if len(agents) == 2 else kit.WAKE_ONE

    async def go():
        await h.waiting(agents=agents)
        await h.stop(entry)
        seen["origin"] = h.sess.last_prompt_origin
        seen["writes"] = len(h.term.writes())
        seen["sent"] = len(h.sent)
        seen["cards"] = len(h.cards_opened)
        if flushed:
            now = time.time()
            h.append(*kit.queue_lines(now, sentence), kit.agents_killed_line(now),
                     kit.wake_user_line(now, sentence))
        await h.hook("UserPromptSubmit", prompt=sentence)
        seen["at_wake"] = (h.sess.status, h.sess.turn_sender_id,
                           h.sess.last_prompt_origin, h.sess.turn_prompt)
        await h.turn_stop(ANSWER, agents=())
        seen["after"] = (h.sess.shown_state(), h.sess.job_background_open())
        if second is not None:
            await h.hook("UserPromptSubmit", prompt=second)
            seen["second"] = (h.sess.last_prompt_origin, h.sess.turn_prompt)
    h.run(go())
    seen["new_texts"] = [m["text"] for m in h.sent[seen["sent"]:]]
    seen["new_cards"] = h.cards_opened[seen["cards"]:]
    return seen


FLUSH = pytest.mark.parametrize("flushed", [True, False], ids=["flushed", "lazy"])


@FLUSH
@pytest.mark.parametrize("entry", ENTRIES)
def test_wake_is_not_tagged_as_typed_in_the_terminal(dm, entry, flushed):
    seen = _scenario(dm, entry, flushed=flushed)
    assert seen["at_wake"][2] == seen["origin"]


@FLUSH
@pytest.mark.parametrize("entry", ENTRIES)
def test_wake_sentence_is_never_the_persons_words(dm, entry, flushed):
    seen = _scenario(dm, entry, flushed=flushed)
    assert "stopped by the user" not in (seen["at_wake"][3] or "")


@FLUSH
@pytest.mark.parametrize("entry", ENTRIES)
def test_wake_sets_no_sender_in_a_group(grp, entry, flushed):
    seen = _scenario(grp, entry, flushed=flushed)
    assert seen["at_wake"][1] is None


@FLUSH
@pytest.mark.parametrize("entry", ENTRIES)
def test_wake_sentence_is_not_mirrored_into_the_chat(dm, entry, flushed):
    seen = _scenario(dm, entry, flushed=flushed)
    assert not any("stopped by the user" in t for t in seen["new_texts"])


@FLUSH
def test_wake_sentence_is_not_mirrored_in_a_group(grp, flushed):
    seen = _scenario(grp, flushed=flushed)
    assert not any("stopped by the user" in t for t in seen["new_texts"])


@FLUSH
@pytest.mark.parametrize("entry", ENTRIES)
def test_claudes_reply_is_delivered_once(dm, entry, flushed):
    seen = _scenario(dm, entry, flushed=flushed)
    assert sum(ANSWER in t for t in seen["new_texts"]) == 1


@FLUSH
@pytest.mark.parametrize("entry", ENTRIES)
def test_wake_gets_the_lazy_self_woken_card(dm, entry, flushed):
    seen = _scenario(dm, entry, flushed=flushed)
    assert [c.get("lazy") for c in seen["new_cards"]] == [True]


@FLUSH
def test_wake_runs_as_a_turn(dm, flushed):
    seen = _scenario(dm, flushed=flushed)
    assert seen["at_wake"][0].name == "BUSY"


@FLUSH
def test_wake_types_nothing(dm, flushed):
    seen = _scenario(dm, flushed=flushed)
    assert len(dm.term.writes()) == seen["writes"]


@FLUSH
def test_after_the_wake_the_session_is_idle_with_no_job(dm, flushed):
    seen = _scenario(dm, flushed=flushed)
    assert seen["after"] == (("idle", 0), False)


def test_one_agent_sentence_is_also_recognised(dm):
    seen = _scenario(dm, agents=(kit.A1,))
    assert seen["at_wake"][2] == seen["origin"]


def test_one_agent_wake_sets_no_sender_in_a_group(grp):
    seen = _scenario(grp, agents=(kit.A1,))
    assert seen["at_wake"][1] is None


# ---- one-shot: a second markerless prompt is a person's terminal turn -----------------

SECOND = "please also check the logs"


@FLUSH
def test_a_second_markerless_prompt_is_a_terminal_turn(dm, flushed):
    seen = _scenario(dm, flushed=flushed, second=SECOND)
    assert seen["second"][0] == "terminal"


@FLUSH
def test_a_second_markerless_prompt_carries_the_persons_words(dm, flushed):
    seen = _scenario(dm, flushed=flushed, second=SECOND)
    assert SECOND in (seen["second"][1] or "")


def test_control_a_markerless_prompt_without_any_stop_is_a_terminal_turn(dm):
    """The same sentence typed with no kill anywhere: a person's turn (the
    recognition is structural, never the sentence's English)."""
    seen = {}

    async def go():
        await dm.prompt()
        await dm.turn_stop("Done.", agents=())
        await dm.hook("UserPromptSubmit", prompt=kit.WAKE_TWO)
        seen["origin"] = dm.sess.last_prompt_origin
    dm.run(go())
    assert seen["origin"] == "terminal"
