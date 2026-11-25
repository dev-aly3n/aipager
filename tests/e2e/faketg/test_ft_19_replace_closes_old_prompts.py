"""Scenario 19: /new's Replace while the old Claude's permission prompt is
open.

alice's private session asks to write a file (its prompt is on the busy
card), and alice sends ``/new`` with the same name and taps Replace. The
old Claude is killed and a fresh one starts under the same entry. Then:

- the old turn is over: the prompt has no buttons left, its busy card
  (when it was sent) says Stopped, and the entry is not left working;
- a tap on the old prompt's Allow (an old client still showing it) is
  answered "already answered" and types nothing into the fresh Claude:
  no dialog key reaches it and the file is never written;
- the fresh Claude takes alice's next message as a turn of its own.

Both modes: the stand-in's event log shows any key typed into it; with
real Claude the file and the control turn do.
"""

from __future__ import annotations

import time

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import conftest as ftc
from tests.e2e.faketg import flows

DM = fti.DM_ID
LABEL = "ft19"


def _buttons(msg: dict | None) -> list[str]:
    kb = ((msg or {}).get("reply_markup") or {}).get("inline_keyboard") or []
    return [b.get("text", "") for row in kb for b in row]


def test_replace_closes_the_old_prompt_and_ends_its_turn(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(DM, fti.ALICE, LABEL)
    flows.settle(fake)
    card, allow = flows.ask_write(inst, name, LABEL, DM, fti.ALICE, "ft19a")
    assert "Allow" in " ".join(_buttons(fake.message(DM, card["message_id"])))

    # alice replaces the session while its prompt is open.
    events_before = len(inst.standin_log(name))
    since, log_since = fake.mark(), inst.log_mark()
    fake.inject_text(DM, fti.user(fti.ALICE), f"/new {LABEL}")
    conflict, replace = fake.wait_button(DM, "Replace", since=since, timeout=60)
    cb = fake.inject_callback(fti.user(fti.ALICE), fake.message(DM, conflict["message_id"]),
                              replace)
    fake.wait_answer(cb, timeout=60)
    inst.wait_log(f"[{LABEL}] /new conflict resolved via Replace", since=log_since,
                  timeout=90)
    fti.wait_until(inst.socket_for(name).exists, 90, "the fresh session's socket",
                   inst.log_tail)
    if ftc.claude_mode() == "standin":
        fti.wait_until(lambda: any(e.get("event") == "start"
                                   for e in inst.standin_log(name)[events_before:]),
                       60, "the fresh stand-in to start")
    else:
        inst.wait_log(f"Launched session {name} (", since=log_since, timeout=90)
        time.sleep(3)

    # The old prompt has no buttons left, whether it was on the busy card or
    # sent as its own message (real Claude can ask before the card is out).
    fti.wait_until(lambda: not _buttons(fake.message(DM, card["message_id"])), 60,
                   "the old prompt's buttons to go")
    on_card = card["message_id"] in flows.busy_card_ids(inst.log_lines(0), LABEL)
    if on_card:
        # The old turn's card: Stopped.
        flows.wait_text(fake, DM, f"{LABEL} · Stopped", since=since, timeout=60)
    # No card of the old turn turns up after the Replace (one still waiting
    # for the chat's pace is dropped unsent).
    assert not flows.busy_card_ids(inst.log_lines(log_since), LABEL), \
        "a busy card was sent after the replace"
    lines = inst.log_lines(log_since)
    assert not [ln for ln in lines if f"[{LABEL}]" in ln and "→ BUSY" in ln], \
        "the replaced entry went back to working"

    # An old client taps the old Allow: nothing is typed into the fresh Claude.
    tap_since = inst.log_mark()
    cb = fake.inject_callback(fti.user(fti.ALICE), fake.message(DM, card["message_id"]),
                              allow)
    toast = flows.wait_toast(fake, cb)
    assert toast == "already answered", toast
    flows.settle(fake)
    if ftc.claude_mode() == "standin":
        keys = [e for e in inst.standin_log(name)[events_before:]
                if e.get("event") in ("dialog_key", "escape", "prompt")]
        assert keys == [], keys
    assert not [ln for ln in inst.log_lines(tap_since)
                if f"[{LABEL}]" in ln and ("send_keys" in ln or "typed" in ln.lower())], \
        "the old prompt's tap typed into the fresh claude"
    assert not inst.file_in_session(name, "ft19a.txt").exists(), \
        "the old claude's write ran"

    # The fresh Claude takes the next message as its own turn.
    flows.control(inst, DM, fti.ALICE, name, "pingft19")
    assert not inst.file_in_session(name, "ft19a.txt").exists()
