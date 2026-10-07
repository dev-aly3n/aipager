"""Scenario 10 (roadmap 8.99): a Deny typed into Claude Code's dialog.

When the PermissionRequest hook has stopped waiting, a Deny tap falls
back to typing keys into Claude Code's dialog: Downs that clamp on the
last row, "No, and tell Claude what to do differently (esc)", and Enter.
Claude Code 2.1.291 then ends the turn like an interrupt: no PostToolUse
and no Stop. The daemon must still settle the card as Denied and return
the session to IDLE (it used to stay BUSY until its 900 s cap), whether
the prompt was on the busy card or a separate message.

Stand-in only: it can shorten the hook's wait for one session (real
Claude runs with the instance's 120 s wait, and its timing decides where
the prompt lands).
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G = fti.GROUP_ID
BOT = f"@{fti.BOT_USERNAME}"


@pytest.mark.parametrize("where", ["card", "separate"])
def test_typed_deny_ends_the_turn_and_frees_the_session(fresh, where):
    inst, fake = fresh, fresh.fake
    if inst.claude_mode != "standin":
        pytest.skip("stand-in only: it shortens the permission hook's wait")
    label = "ft16" if where == "card" else "ft17"
    name = inst.new_session(G, fti.BOB, label)
    # The hook gives up after 1 s, so the tap below is typed into the dialog.
    (inst.inst_dir / f"standin-hook-deadline-{name}").write_text("1")
    flows.settle(fake)
    since, log_since = fake.mark(), inst.log_mark()
    if where == "card":
        card, _allow = flows.ask_write(inst, name, label, G, fti.BOB, label)
    else:
        inst.release_tools(name)  # no hold: the prompt beats the busy card
        fake.inject_text(G, fti.user(fti.BOB),
                         f"{BOT} Use the Write tool to create {label}.txt containing hi")
        card, _allow = fake.wait_button(G, "Allow", since=since, timeout=120)
        assert "Permission needed" in card.get("text", ""), card.get("text")
    fti.wait_until(lambda: any(e.get("event") == "dialog_open"
                               for e in inst.standin_log(name)),
                   30, "the hook to stop waiting and the dialog to open")

    since = fake.mark()
    _msg, deny = fake.wait_button(G, "Deny", since=0, message_text_contains=label)
    cb = fake.inject_callback(fti.user(fti.BOB), fake.message(G, card["message_id"]), deny)
    # Updates are handled one at a time: on the separate path the tap
    # waits behind the prompt's own handler, which is sending the busy
    # card through the group's flood pacing.
    fake.wait_answer(cb, timeout=90)
    answered = time.monotonic()
    # Typed: the stand-in's dialog took the last row, and its turn ended
    # like an interrupt (no Stop).
    fti.wait_until(lambda: any(e.get("event") == "interrupted" and e.get("by") == "dialog"
                               for e in inst.standin_log(name)),
                   30, "the typed Deny to reach the dialog's last row")
    assert [e.get("answer") for e in inst.standin_log(name)
            if e.get("event") == "dialog_key"] == ["rejected"]
    flows.wait_text(fake, G, "Denied by @bob", since=since, timeout=30)
    # The session is IDLE again within seconds, not after the 900 s cap.
    flows.wait_turn_end(inst, name, label, since_log=log_since, timeout=30)
    assert time.monotonic() - answered < 30
    assert not inst.file_in_project(f"{label}.txt").exists()
    assert not [e for e in inst.standin_log(name) if e.get("event") == "stop"]
    inst.wait_log(f"[{label}] turn ended at the permission dialog", since=log_since,
                  timeout=30)

    # And it takes the next message as a new turn.
    (inst.inst_dir / f"standin-hook-deadline-{name}").unlink(missing_ok=True)
    flows.control(inst, G, fti.BOB, name, f"ping{label}")
