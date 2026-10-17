"""A terminal interrupt with prompts of several owners open at once
(fixes-3, rev-iter2-002): Claude's own question, a background agent's,
a foreground agent's and an unknown agent's permission.

At the interrupt every prompt whose owner does not survive closes,
prompt by prompt: Claude's own, the foreground agent's and the unknown
agent's. Only the background agent's stays open and answerable. No
button of a closed prompt types anything or sends a hook decision; the
surviving one still answers (by hook, or by keys once it is the only
dialog left).

The transcript is a real file under ``tmp_path`` (the filesystem
boundary); no clock is patched. Arrival order is varied because the
first prompt sits on the card and the others are their own messages.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.session_monitor import SessionMonitor
from aipager.state import Status
from tests.two_prompts_rig import BG, CARD, FG, OTHER, S, answers

MARKER = "[Request interrupted by user for tool use]"
COMMAND = {"bg": "make", "fg": "pytest", "un": "ruff check"}
AGENT = {"bg": BG, "fg": FG, "un": OTHER}
REQ = {"bg": "req-bg", "fg": "req-fg", "un": "req-un"}

# Arrival orders: background first / last, question first / last, mixed.
ORDERS = [
    ("bg", "fg", "un", "q"),
    ("q", "un", "fg", "bg"),
    ("fg", "q", "bg", "un"),
    ("un", "bg", "q", "fg"),
]
ORDER_IDS = ["-".join(o) for o in ORDERS]


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def interrupt(rig, monkeypatch, run_async, tmp_path):
    """The terminal's "No" on Claude's question, written to the
    transcript, then one session monitor scan."""
    async def _alive():
        return [S]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _alive)
    monitor = SessionMonitor(rig.bot.registry, rig.bot.notify)

    def go():
        path = tmp_path / "t.jsonl"
        now = time.time()
        lines = [
            {"type": "assistant", "timestamp": _iso(now - 3), "message": {
                "role": "assistant", "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "toolu_q1",
                             "name": "AskUserQuestion", "input": {}}]}},
            {"type": "user", "timestamp": _iso(now), "message": {
                "role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "toolu_q1", "is_error": True,
                    "content": "The user doesn't want to proceed with this tool use."}]}},
            {"type": "user", "timestamp": _iso(now), "message": {
                "role": "user", "content": [{"type": "text", "text": MARKER}]}},
        ]
        path.write_text("".join(json.dumps(x) + "\n" for x in lines))
        rig.sess.transcript_path = str(path)
        run_async(monitor._scan())

    return go


def owners(rig):
    return [p["tool_info"].get("agent_id") for p in rig.sess.open_prompts()]


def open_all(rig, order, kinds=("bg", "fg", "un")):
    """Launch the background and foreground agents (the unknown one has
    no matched Agent call), then raise each prompt in *order*."""
    if "bg" in kinds:
        rig.launch(BG)
    if "fg" in kinds:
        rig.launch(FG, background=False)
    for who in order:
        if who == "q":
            rig.question()
        elif who in kinds:
            rig.agent_bash(AGENT[who], COMMAND[who], request_id=REQ[who])


def surface(rig, order, who):
    """The message a prompt's buttons sit on: the card for the first one,
    otherwise its own message."""
    if order[0] == who:
        return CARD
    for m in rig.prompt_messages():
        labels = rig.buttons(m)
        if who == "q" and any("Only the card" in b for b in labels):
            return m["msg_id"]
        if who != "q" and COMMAND[who] in (m.get("text") or ""):
            return m["msg_id"]
    raise AssertionError(f"no message for {who}")


def verb(who):
    return "opt0" if who == "q" else "allow"


# -- preconditions -------------------------------------------------------------------------------

@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_all_four_prompts_open_before_the_interrupt(rig, order):
    open_all(rig, order)
    assert len(rig.sess.open_prompts()) == 4


# -- only the background agent's prompt survives -------------------------------------------------

@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_interrupt_keeps_only_the_background_prompt(rig, interrupt, order):
    open_all(rig, order)
    interrupt()
    assert owners(rig) == [BG]


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_interrupt_with_a_survivor_keeps_waiting(rig, interrupt, order):
    open_all(rig, order)
    interrupt()
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_interrupt_with_a_survivor_dialog_is_open(rig, interrupt, order):
    open_all(rig, order)
    interrupt()
    assert rig.sess.dialog_is_open() is True


# -- each closed prompt's button refuses, types nothing -----------------------------------------

@pytest.mark.parametrize("who", ["fg", "un", "q"])
@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_closed_prompt_button_already_answered(rig, interrupt, order, who):
    open_all(rig, order)
    interrupt()
    q = rig.tap(surface(rig, order, who), verb(who))
    assert answers(q) == [str(ALREADY_ANSWERED)]


@pytest.mark.parametrize("who", ["fg", "un", "q"])
@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_closed_prompt_button_types_nothing(rig, interrupt, order, who):
    open_all(rig, order)
    interrupt()
    rig.tap(surface(rig, order, who), verb(who))
    assert rig.keys == []


@pytest.mark.parametrize("who", ["fg", "un"])
@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_closed_prompt_button_sends_no_hook_decision(rig, interrupt, order, who):
    open_all(rig, order)
    interrupt()
    rig.tap(surface(rig, order, who), "allow")
    assert rig.decisions == []


@pytest.mark.parametrize("who", ["fg", "un"])
@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_closed_prompt_deny_types_nothing(rig, interrupt, order, who):
    open_all(rig, order)
    interrupt()
    rig.tap(surface(rig, order, who), "deny")
    assert (rig.keys, rig.decisions) == ([], [])


@pytest.mark.parametrize("who", ["fg", "un"])
@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_closed_prompt_types_nothing_when_its_hook_is_gone(rig, interrupt, order, who):
    """Error guessing: the background agent's hook gave up, so its dialog
    is the one in the terminal; the dead agent's hook send fails. A tap
    on the dead agent's button must not fall back to keys (they would
    answer the background agent's dialog)."""
    open_all(rig, order)
    rig.timeout("req-bg")
    interrupt()
    rig.hook_ok = False
    rig.tap(surface(rig, order, who), "allow")
    assert rig.keys == []


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_taps_on_every_closed_prompt_leave_the_survivor_open(rig, interrupt, order):
    open_all(rig, order)
    interrupt()
    for who in ("fg", "un", "q"):
        rig.tap(surface(rig, order, who), verb(who))
    assert owners(rig) == [BG]


# -- the surviving prompt still answers ----------------------------------------------------------

@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_survivor_answers_by_hook(rig, interrupt, order):
    open_all(rig, order)
    interrupt()
    rig.tap(surface(rig, order, "bg"), "allow")
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [("req-bg", "allow")]


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_survivor_answers_after_the_closed_ones_were_tapped(rig, interrupt, order):
    open_all(rig, order)
    interrupt()
    for who in ("fg", "un", "q"):
        rig.tap(surface(rig, order, who), verb(who))
    rig.tap(surface(rig, order, "bg"), "allow")
    assert [r["request_id"] for r, _d in rig.decisions] == ["req-bg"]


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_survivor_typed_once_its_hook_gave_up(rig, interrupt, order):
    """The background agent's hook gave up, so its dialog is in the
    terminal and is the only one left: keys may answer it."""
    open_all(rig, order)
    rig.timeout("req-bg")
    interrupt()
    rig.hook_ok = False
    rig.tap(surface(rig, order, "bg"), "allow")
    assert rig.keys != []


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_survivor_answer_leaves_nothing_open(rig, interrupt, order):
    open_all(rig, order)
    interrupt()
    rig.tap(surface(rig, order, "bg"), "allow")
    assert rig.sess.dialog_is_open() is False


@pytest.mark.parametrize("order", ORDERS, ids=ORDER_IDS)
def test_survivor_answer_leaves_interactive(rig, interrupt, order):
    open_all(rig, order)
    interrupt()
    rig.tap(surface(rig, order, "bg"), "allow")
    assert rig.sess.status != Status.INTERACTIVE


# -- partitions: other survivor kinds, no survivor ------------------------------------------------

def test_went_async_agent_survives_beside_an_unknown_one(rig, interrupt):
    """A foreground agent moved to the background mid-run survives; the
    unknown agent's prompt beside it closes."""
    rig.launch(FG, background=False)
    rig.hook("PostToolUse", tool_name="Agent",
             tool_input={"subagent_type": "ship-reviewer", "description": "Review",
                         "prompt": "Review it"},
             tool_use_id=f"toolu_launch_{FG}",
             tool_response={"isAsync": True, "status": "async_launched", "agentId": FG})
    rig.agent_bash(FG, "pytest", request_id="req-fg")
    rig.agent_bash(OTHER, "ruff check", request_id="req-un")
    rig.question()
    interrupt()
    assert owners(rig) == [FG]


@pytest.mark.parametrize("order", [("fg", "un", "q"), ("q", "un", "fg"), ("un", "q", "fg")],
                         ids=["fg-un-q", "q-un-fg", "un-q-fg"])
def test_no_survivor_ends_the_turn(rig, interrupt, order):
    open_all(rig, order, kinds=("fg", "un"))
    interrupt()
    assert rig.sess.status == Status.IDLE


@pytest.mark.parametrize("order", [("fg", "un", "q"), ("q", "un", "fg"), ("un", "q", "fg")],
                         ids=["fg-un-q", "q-un-fg", "un-q-fg"])
def test_no_survivor_closes_everything(rig, interrupt, order):
    open_all(rig, order, kinds=("fg", "un"))
    interrupt()
    assert rig.sess.open_prompts() == []


@pytest.mark.parametrize("order", [("fg", "un", "q"), ("q", "un", "fg"), ("un", "q", "fg")],
                         ids=["fg-un-q", "q-un-fg", "un-q-fg"])
def test_no_survivor_no_button_types_anything(rig, interrupt, order):
    open_all(rig, order, kinds=("fg", "un"))
    interrupt()
    rig.hook_ok = False
    for who in order:
        rig.tap(surface(rig, order, who), verb(who))
    assert (rig.keys, rig.decisions) == ([], [])


def test_two_background_agents_both_survive(rig, interrupt):
    """Boundary: two survivors; each keeps its prompt and answers."""
    rig.launch(BG)
    rig.launch(OTHER, agent_type="ship-tester")
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.agent_bash(FG, "pytest", request_id="req-fg")
    rig.agent_bash(OTHER, "ruff check", request_id="req-un")
    rig.question()
    interrupt()
    assert owners(rig) == [BG, OTHER]
