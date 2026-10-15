"""Live check (opt-in): a background agent waiting for its own agent, and
the running agents across a daemon restart, through the real daemon, the
real hook binary and the real hook receiver (agents-line-waiting
design.md "Live check").

The stand-in plays Claude Code 2.1.296's hook shapes (``agents: launch``,
``agents: hold``, ``agents: finish``; see standin_claude.py). The daemon is
restarted in place with ``restart_daemon()`` (its own PID only) while a
turn is held, so the new daemon adopts the running card.

Run with ``AIPAGER_E2E_FAKETG=1 AIPAGER_E2E_FAKETG_CLAUDE=standin`` and
``-s`` to see the agents line before and after the restart.
"""

from __future__ import annotations

import re
import time

import pytest

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import conftest as ftc
from tests.e2e.faketg import flows

#: ``agents: launch`` and the rest are the stand-in's own words: real Claude
#: would only answer them.
pytestmark = pytest.mark.skipif(
    ftc.claude_mode() != "standin",
    reason="stand-in only: it plays Claude Code's agent hooks on cue")

DM = fti.DM_ID
LABEL = "ft20"
WORKING_2 = "⏳ 2 agents working"
DONE_2 = "✅ 2 agents done ("
WAITING_ROW = "‖ 🤖 pipeline-runner · waiting for its own agents"
_TAG = re.compile(r"</?[a-z]+(?: [^>]*)?>")


def _block(text: str) -> str:
    """The agents block of an answer: from its header on."""
    i = text.find("⏳ ")
    j = text.find("✅ ")
    starts = [k for k in (i, j) if k >= 0]
    return text[min(starts):] if starts else ""


def _plain(text: str) -> str:
    """*text* as read: no footer or bold tags, no Markdown bold."""
    return _TAG.sub("", text).replace("**", "")


def _rows_shown(text: str, *agents: str) -> bool:
    """Every one of *agents* has a 🤖 row in the answer's block."""
    plain = _plain(_block(text))
    return all(f"🤖 {agent}" in plain for agent in agents)


def _show(title: str, text: str) -> None:
    print(f"\n----- {title} -----\n{text}\n", flush=True)


def _card_text(fake, msg_id: int) -> str:
    """The latest text the daemon gave message *msg_id* in the DM."""
    texts = [c.text for c in fake.calls(chat_id=DM)
             if c.method in flows.SEND_METHODS
             and (c.params.get("message_id") == msg_id
                  or (c.response.get("result") or {}).get("message_id") == msg_id)]
    return texts[-1] if texts else ""


def _pinned_texts(fake) -> list[str]:
    pinned = {c.params.get("message_id") for c in fake.calls(method="pinChatMessage")}
    return [c.text for c in fake.calls(chat_id=DM) if c.method == "editMessageText"
            and c.params.get("message_id") in pinned]


def test_waiting_agent_and_restart_on_the_agents_line(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(DM, fti.ALICE, LABEL)
    sockets = inst.live_sockets()

    # 1. P launches C and pauses; the answer lists both as working.
    since, log0 = fake.mark(), inst.log_mark()
    fake.inject_text(DM, fti.user(fti.ALICE), "agents: launch")
    flows.wait_turn_end(inst, name, LABEL, since_log=log0)
    first = flows.wait_text(fake, DM, WORKING_2, since=since)
    _show("answer after 'agents: launch' (before the restart)", _block(first.text))
    assert _rows_shown(first.text, "pipeline-runner", "ship-reviewer"), first.text
    inst.wait_log(f"[{LABEL}] SubagentStop: pipeline-runner",
                  "waiting for 1 agent(s) of its own", since=log0)
    first_id = (first.response.get("result") or {}).get("message_id") \
        or first.params.get("message_id")

    # Past the 5 s settle and a 30 s edit: no edit marks P done.
    time.sleep(40)
    later = [c.text for c in fake.calls(chat_id=DM, since=first.seq)
             if c.method == "editMessageText" and c.params.get("message_id") == first_id]
    assert not any("✅" in t or "✓ 🤖 pipeline-runner" in _plain(t) for t in later), later
    # P paused for C says so (in the answer, or in an edit once it paused).
    assert any(WAITING_ROW in _plain(t) for t in [first.text, *later]), [first.text, *later]
    if later:
        _show("the same answer 40 s later (last edit)", _block(later[-1]))

    # 2. A held turn, so the restart finds a running card.
    log1 = inst.log_mark()
    inst.hold_tools(name)
    fake.inject_text(DM, fti.user(fti.ALICE), "agents: hold")
    busy_line = inst.wait_log(f"[{LABEL}] Busy message sent", since=log1)
    card_id = int(re.search(r"msg_id=(\d+)", busy_line).group(1))
    time.sleep(3)
    _show("busy card before the restart", _card_text(fake, card_id))

    # 3. The restart: the card is adopted with both agents back on it.
    mark = inst.log_mark()
    card_mark = fake.mark()
    inst.restart_daemon()
    inst.wait_log("busy card", "adopted", since=mark)
    restored = inst.wait_log("restored 2 running agents (1 waiting)", since=mark)
    adopted = inst.wait_log("adopted card: 2 agents", since=mark)
    _show("log after the restart", restored + "\n" + adopted)
    time.sleep(5)
    after_card = [c.text for c in fake.calls(chat_id=DM, since=card_mark)
                  if c.method == "editMessageText" and c.params.get("message_id") == card_id]
    _show("busy card after the restart", after_card[-1] if after_card else "(no edit yet)")

    # 4. The held turn ends: its answer lists both agents.
    since2 = fake.mark()
    inst.release_tools(name)
    second = flows.wait_text(fake, DM, WORKING_2, since=since2, timeout=90)
    _show("answer after the restart", _block(second.text))
    assert _rows_shown(second.text, "pipeline-runner", "ship-reviewer"), second.text

    # 5. C stops, P resumes and stops for good: the answer settles to done.
    log2 = inst.log_mark()
    since3 = fake.mark()
    fake.inject_text(DM, fti.user(fti.ALICE), "agents: finish")
    inst.wait_log(f"[{LABEL}] SubagentStart: pipeline-runner", "- resumed", since=log2)
    done = flows.wait_text(fake, DM, DONE_2, since=since3, timeout=60)
    _show("the agents line once both finished", _block(done.text))
    pins = _pinned_texts(fake)
    if pins:
        _show("pinned bar (last edit)", pins[-1])

    # 6. The sessions lived through the restart.
    assert inst.live_sockets() == sockets
