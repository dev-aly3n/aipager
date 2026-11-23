"""Live check (opt-in, stand-in only): a background agent's permission
prompt that opens after Claude's own turn ended, through the real daemon,
the real hook binary and the real hook receiver.

The stand-in plays it on cue (``agent asks after stop``; see
standin_claude.py): Claude launches agent A in the background and its turn
ends (the session idle, the job waiting on A); 11 s later A asks for Bash.

- The prompt comes as a NEW message with Allow (sent after the turn's
  answer), not as an edit of the job's old waiting card above the answer.
- The answer reaches A's dialog: by its hook (``[hook]``), by keys after
  the hook gave up (``[typed]``), or in the terminal (``[terminal]``).
- ``[gap]``: A's dialog enters the terminal a moment after the hook gave
  up (the stand-in waits 2 s, Claude Code 2.1.326 draws it then and a
  fresh dialog ignores keys for a moment). A tap right after the timeout
  types nothing, keeps its buttons and says the prompt is still opening;
  a tap once the gap is over answers A by key.
- Once it closes the session is idle again, not working: the next
  message gets its own card and is never taken for a queued one.

Run with ``AIPAGER_E2E_FAKETG=1 AIPAGER_E2E_FAKETG_CLAUDE=standin``.
"""

from __future__ import annotations

import pytest

from aipager import i18n
from aipager.bot.callbacks import PROMPT_OPENING
from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import conftest as ftc
from tests.e2e.faketg import flows

pytestmark = pytest.mark.skipif(
    ftc.claude_mode() != "standin",
    reason="stand-in only: it plays a background agent asking on cue")

DM = fti.DM_ID
LABELS = {"hook": "ft40", "typed": "ft41", "terminal": "ft42", "gap": "ft43"}
#: The pop-up of a tap in the gap (tests may compare shown text).
OPENING = i18n.get("en").text(PROMPT_OPENING)
SEND = ("sendMessage", "sendRichMessage")


def _events(inst, name, event, **match):
    return [e for e in inst.standin_log(name) if e.get("event") == event
            and all(e.get(k) == v for k, v in match.items())]


def _wait_event(inst, name, event, what, timeout=60, **match):
    return fti.wait_until(lambda: _events(inst, name, event, **match), timeout, what,
                          lambda: inst.log_tail(30))


def _tap(fake, msg, data):
    cb = fake.inject_callback(fti.user(fti.ALICE), fake.message(DM, msg["message_id"]), data)
    return flows.wait_toast(fake, cb, timeout=30)


def _sent_id(call) -> int | None:
    result = (call.response or {}).get("result")
    return result.get("message_id") if isinstance(result, dict) else None


def _replies_to(call, message_id: int) -> bool:
    p = call.params
    rp = p.get("reply_parameters")
    if isinstance(rp, str):
        return f'"message_id": {message_id}' in rp or f'"message_id":{message_id}' in rp
    if isinstance(rp, dict) and rp.get("message_id") == message_id:
        return True
    return str(p.get("reply_to_message_id")) == str(message_id)


@pytest.mark.parametrize("case", list(LABELS))
def test_agent_prompt_after_the_turn_is_its_own_message(fresh, case):
    inst, fake = fresh, fresh.fake
    label = LABELS[case]
    name = inst.new_session(DM, fti.ALICE, label)
    deadline = inst.inst_dir / f"standin-hook-deadline-{name}"
    if case != "hook":
        deadline.write_text("4")
    # A works on after its tool until the end of the check.
    inst.hold_tools(name)
    try:
        flows.settle(fake)
        since, log0 = fake.mark(), inst.log_mark()
        fake.inject_text(DM, fti.user(fti.ALICE), "agent asks after stop")

        # 1. Claude's turn ends with A still running; then A asks.
        _wait_event(inst, name, "stop", "the turn's Stop", timeout=90)
        answer = flows.wait_text(fake, DM, "stand-in: agent launched", since=since,
                                 timeout=60, methods=SEND)
        answer_id = _sent_id(answer)
        assert answer_id, answer.summary()
        _wait_event(inst, name, "agent_after_stop_asked", "A's ask", timeout=60)

        # 2. Its prompt is a new message below the answer, not the old card.
        a_msg, a_allow = fake.wait_button(DM, "Allow", since=since, timeout=30)
        assert a_msg["message_id"] > answer_id, (a_msg["message_id"], answer_id)
        cards = flows.busy_card_ids(inst.log_lines(log0), label)
        assert a_msg["message_id"] not in cards, (a_msg["message_id"], cards)
        assert "touch marker-a" in a_msg.get("text", "")

        # 3. The answer reaches A's dialog.
        if case == "hook":
            _tap(fake, a_msg, a_allow)
            _wait_event(inst, name, "agent_answer", "A answered by its hook",
                        answer="allowed", by="hook")
        else:
            inst.wait_log(f"[{label}] hook permission_reply_timeout", since=log0)
            if case == "gap":
                # Right after the timeout: A's dialog is not drawn yet.
                toast = _tap(fake, a_msg, a_allow)
                assert toast == OPENING, toast
                assert not _events(inst, name, "dialog_key")
                assert not _events(inst, name, "stray_enter")
                assert not _events(inst, name, "agent_answer")
                again, _ = fake.wait_button(DM, "Allow", since=since, timeout=10)
                assert again["message_id"] == a_msg["message_id"]
            _wait_event(inst, name, "dialog_open", "A's dialog on screen", dialog="A")
            if case in ("typed", "gap"):
                toast = flows.tap_until(fake, DM, fti.ALICE, a_msg, a_allow, retry_on=OPENING)
                assert toast != OPENING, toast
            else:
                inst.send_keys(name, b"\r")
            _wait_event(inst, name, "agent_answer", "A answered by a key",
                        answer="allowed", by="key")
            assert not _events(inst, name, "stray_enter")

        # 4. Closed: idle again, never working (A still runs).
        inst.wait_log(f"[{label}] INTERACTIVE → IDLE", since=log0, timeout=30)
        assert not [ln for ln in inst.log_lines(log0)
                    if f"[{label}] INTERACTIVE → BUSY" in ln]

        # 5. The next message is a new turn with its own card. Sent as a
        # reply to the session's own prompt, so it reaches this session
        # even when an earlier case's session (gone by now) posts a late
        # card meanwhile, which makes that one the chat's target.
        since2, log1 = fake.mark(), inst.log_mark()
        sent = fake.inject_text(DM, fti.user(fti.ALICE), "Reply with exactly: OK",
                                reply_to=a_msg["message_id"])
        mid = sent["message_id"]
        fake.wait_for(lambda c: c.method in SEND and c.chat_id == DM
                      and _replies_to(c, mid), timeout=10, since=since2,
                      what=f"a card under message {mid}")
        flows.wait_text(fake, DM, "OK", since=since2, timeout=60, methods=SEND)
        inst.wait_log(f"[{label}]", "→ IDLE", since=log1, timeout=60)
        lines = inst.log_lines(log1)
        assert not [ln for ln in lines if f"[{label}] queued while busy" in ln], lines
        assert not [ln for ln in lines if "taken inside the open job" in ln], lines
    finally:
        # 6. A finishes.
        inst.release_tools(name)
        deadline.unlink(missing_ok=True)
    _wait_event(inst, name, "agent_after_stop_done", "A's end", timeout=30)
