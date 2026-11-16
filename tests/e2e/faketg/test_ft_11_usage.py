"""Scenario 11 (/usage, roadmap 8.131): plan limits and tokens.

alice (owner) prompts a turn in the group. In stand-in mode the stand-in's
status-line payload carries ``rate_limits`` (the ``standin-rate-limits``
knob); with real Claude a setup token reports the week only, after the
first answer. dave (admin) sees that chat's tokens in the group, without
plan limits; bob (user) is refused; alice sees the plan limits in her
private chat, and still does after a daemon restart."""

from __future__ import annotations

import json
import time

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import flows

G = fti.GROUP_ID
DM = fti.DM_ID
WEEK = "This week (all models):"


def _usage(fake, chat_id: int, uid: int, contains: str, *, timeout: float = 60):
    since = fake.mark()
    fake.inject_text(chat_id, fti.user(uid), "/usage")
    return flows.wait_text(fake, chat_id, contains, since=since, timeout=timeout)


def _usage_until(fake, chat_id: int, uid: int, contains: str, *, timeout: float = 30):
    """``/usage`` again until its reply contains *contains*: with real
    Claude the status-line reading lands a moment after the turn ends."""
    deadline = time.monotonic() + timeout
    while True:
        call = _usage(fake, chat_id, uid, "Tokens")
        if contains in call.text or time.monotonic() > deadline:
            assert contains in call.text, call.text
            return call
        time.sleep(3)


def test_usage_shows_plan_limits_and_tokens(fresh):
    inst, fake = fresh, fresh.fake
    name = inst.new_session(G, fti.ALICE, "ft11")
    if inst.claude_mode == "standin":
        now = int(time.time())
        (inst.inst_dir / f"standin-rate-limits-{name}").write_text(json.dumps({
            "seven_day": {"used_percentage": 42, "resets_at": now + 2 * 86400},
            "five_hour": {"used_percentage": 7, "resets_at": now + 3 * 3600},
        }))

    flows.prompt_turn(inst, G, fti.ALICE, name, "/ft11 Reply with exactly: OK", "OK")
    flows.settle(fake)

    # dave (admin) in the group: this chat's tokens, no plan limits.
    group = _usage(fake, G, fti.DAVE, "Tokens in this chat")
    assert "Today:" in group.text
    assert "used" not in group.text
    assert "Plan limits are not shown in a group." in group.text

    # bob (user): refused.
    _usage(fake, G, fti.BOB, "Only an admin can see usage.")

    # alice (owner) in her private chat: the plan limits.
    if inst.claude_mode == "standin":
        dm = _usage(fake, DM, fti.ALICE, f"{WEEK} 42% used")
        assert "5-hour window: 7% used" in dm.text
    else:
        dm = _usage_until(fake, DM, fti.ALICE, WEEK)
    plan_line = next(line for line in dm.text.splitlines() if line.startswith(WEEK))
    assert "$" not in dm.text

    # A restart keeps the reading.
    inst.restart_daemon()
    again = _usage(fake, DM, fti.ALICE, WEEK)
    assert plan_line.split(":")[0] in again.text
    if inst.claude_mode == "standin":
        assert f"{WEEK} 42% used" in again.text
