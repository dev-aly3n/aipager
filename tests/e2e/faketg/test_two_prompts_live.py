"""Live check (opt-in, stand-in only): two prompts open at once (roadmap
8.120), through the real daemon, the real hook binary and the real hook
receiver.

The stand-in plays Claude Code 2.1.302's dialog order (``two prompts:
agent-first`` / ``two prompts: claude-first``; see standin_claude.py): a
background agent A asks for Bash while Claude asks a question. Both must
show in the chat, each with its own buttons, and each answer must reach
its own dialog: the stand-in logs which dialog every key and every hook
decision reached.

- ``[agent-first-hook]``: A asks first, its hook waits. The question is
  answered by keys (it is on screen), A by its hook. No key reaches A.
- ``[agent-first-typed]``: A's hook gives up after 4 s, so A's dialog goes
  under the question. Allow on A is refused (another prompt is on top) and
  types nothing; once the question is answered, Allow on A types into A.
- ``[claude-first]``: the question first, A second; A answered by its hook
  first, then the question.

Run with ``AIPAGER_E2E_FAKETG=1 AIPAGER_E2E_FAKETG_CLAUDE=standin``.
"""

from __future__ import annotations

import pytest

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import conftest as ftc
from tests.e2e.faketg import flows

pytestmark = pytest.mark.skipif(
    ftc.claude_mode() != "standin",
    reason="stand-in only: it plays two Claude Code dialogs on cue")

DM = fti.DM_ID
OPTION_2 = "All of them"
BEHIND = "Claude Code is showing another prompt first, so answer that one before this one."
LABELS = {"agent-first-hook": "ft30", "agent-first-typed": "ft31", "claude-first": "ft32"}


def _events(inst, name, event, **match):
    return [e for e in inst.standin_log(name) if e.get("event") == event
            and all(e.get(k) == v for k, v in match.items())]


def _wait_event(inst, name, event, what, timeout=60, **match):
    return fti.wait_until(lambda: _events(inst, name, event, **match), timeout, what,
                          lambda: inst.log_tail(30))


def _tap(fake, msg, data):
    cb = fake.inject_callback(fti.user(fti.ALICE), fake.message(DM, msg["message_id"]), data)
    return flows.wait_toast(fake, cb, timeout=30)


@pytest.mark.parametrize("case", list(LABELS))
def test_both_prompts_show_and_both_answers_land(fresh, case):
    inst, fake = fresh, fresh.fake
    label = LABELS[case]
    name = inst.new_session(DM, fti.ALICE, label)
    if case == "agent-first-typed":
        # A's hook gives up after 4 s, after the question opened: its
        # dialog enters under the question.
        (inst.inst_dir / f"standin-hook-deadline-{name}").write_text("4")
    flows.settle(fake)
    since, log0 = fake.mark(), inst.log_mark()
    order = "claude-first" if case == "claude-first" else "agent-first"
    fake.inject_text(DM, fti.user(fti.ALICE), f"two prompts: {order}")

    # 1. Both prompts are in the chat, each with its own buttons.
    q_msg, q_data = fake.wait_button(DM, OPTION_2, since=since, timeout=90)
    a_msg, a_allow = fake.wait_button(DM, "Allow", since=since, timeout=60)
    assert q_msg["message_id"] != a_msg["message_id"]
    assert "Which checks should run?" in q_msg.get("text", "")
    inst.wait_log(f"[{label}] another open prompt", since=log0)
    _wait_event(inst, name, "dialog_open", "the question's dialog", dialog="Q")

    if case == "agent-first-hook":
        _tap(fake, q_msg, q_data)
        _wait_event(inst, name, "question_answered", "the question answered", row=2)
        assert not _events(inst, name, "dialog_key", dialog="A")
        assert not _events(inst, name, "agent_answer")
        _tap(fake, a_msg, a_allow)
        _wait_event(inst, name, "agent_answer", "A answered by its hook",
                    answer="allowed", by="hook")
        assert _events(inst, name, "hook_decision", dialog="A", behavior="allow")
    elif case == "agent-first-typed":
        _wait_event(inst, name, "dialog_open", "A's dialog under the question",
                    dialog="A", place=2)
        inst.wait_log(f"[{label}] hook permission_reply_timeout", since=log0)
        toast = _tap(fake, a_msg, a_allow)
        assert toast == BEHIND, toast
        assert not _events(inst, name, "dialog_key")
        _tap(fake, q_msg, q_data)
        _wait_event(inst, name, "question_answered", "the question answered", row=2)
        assert [e["dialog"] for e in _events(inst, name, "dialog_key")] == ["Q"]
        a_msg2, a_allow2 = fake.wait_button(DM, "Allow", since=since, timeout=10)
        assert a_msg2["message_id"] == a_msg["message_id"]
        _tap(fake, a_msg2, a_allow2)
        _wait_event(inst, name, "agent_answer", "A answered by its key",
                    answer="allowed", by="key")
        assert [e["dialog"] for e in _events(inst, name, "dialog_key")] == ["Q", "A"]
    else:
        _tap(fake, a_msg, a_allow)
        _wait_event(inst, name, "agent_answer", "A answered by its hook",
                    answer="allowed", by="hook")
        assert not _events(inst, name, "question_answered")
        _tap(fake, q_msg, q_data)
        _wait_event(inst, name, "question_answered", "the question answered", row=2)
        assert not _events(inst, name, "dialog_key", dialog="A")

    # 2. The turn ends as usual, and its answer names both outcomes.
    flows.wait_turn_end(inst, name, label, since_log=log0, timeout=120)
    flows.wait_text(fake, DM, "question All of them; agent allowed", since=since,
                    timeout=60)
    (inst.inst_dir / f"standin-hook-deadline-{name}").unlink(missing_ok=True)
