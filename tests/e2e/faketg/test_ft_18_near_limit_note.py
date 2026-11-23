"""Scenario 18 (roadmap 8.131 follow-up): the near-limit note on the busy
card.

Stand-in mode: both sessions' status-line payloads carry ``rate_limits``
with the week at 92% and the 5-hour window at 40% (the
``standin-rate-limits`` knob). The reading of a turn lands after its Stop,
so turn 1 only stores it; turn 2 is held open (``standin-hold``) so its
card is edited while it runs.

- alice's private chat (she is the owner): the busy card shows
  "⚠️ Weekly limit almost reached: 92% used" (and nothing for the 5-hour
  window, which is under the threshold).
- the group, where bob (a plain user) reads every card: the busy card is
  edited (the positive control) and never carries the note."""

from __future__ import annotations

import json
import time

import pytest

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.faketg import conftest as ftc
from tests.e2e.faketg import flows

G = fti.GROUP_ID
DM = fti.DM_ID
NOTE = "Weekly limit almost reached: 92% used"
NEVER_IN_GROUP = ("limit almost reached", "limit reached", "Weekly limit")


def _limits(inst, name: str) -> None:
    now = int(time.time())
    (inst.inst_dir / f"standin-rate-limits-{name}").write_text(json.dumps({
        "seven_day": {"used_percentage": 92, "resets_at": now + 2 * 86400},
        "five_hour": {"used_percentage": 40, "resets_at": now + 3 * 3600},
    }))


def _held_turn(inst, chat_id: int, name: str, text: str):
    """Send *text* with the session's turn held; returns (fake mark, log
    mark) taken before the send."""
    fake = inst.fake
    inst.hold_tools(name)
    since, log_since = fake.mark(), inst.log_mark()
    before = len(inst.prompts_seen(name))
    fake.inject_text(chat_id, fti.user(fti.ALICE), text)
    flows.wait_prompt(inst, name, "hold this turn", after=before)
    return since, log_since


def test_near_limit_note_in_the_owners_dm_never_in_the_group(fresh):
    if ftc.claude_mode() != "standin":
        pytest.skip("stand-in run: the rate_limits knob")
    inst, fake = fresh, fresh.fake
    dm = inst.new_session(DM, fti.ALICE, "ft18d")
    grp = inst.new_session(G, fti.ALICE, "ft18g")
    _limits(inst, dm)
    _limits(inst, grp)

    # Turn 1 in each: the reading lands after its Stop, so it is only
    # stored here.
    flows.prompt_turn(inst, DM, fti.ALICE, dm, "Reply with exactly: OK", "OK")
    flows.prompt_turn(inst, G, fti.ALICE, grp, "/ft18g Reply with exactly: OK", "OK")
    flows.settle(fake)

    # Turn 2 in alice's private chat, held: its card carries the note.
    try:
        since, log_since = _held_turn(inst, DM, dm, "hold this turn. Reply with exactly: OK2")
        call = flows.wait_text(fake, DM, NOTE, since=since, timeout=90)
        assert "5-hour limit" not in call.text, call.text
        assert "Resets in 1 d" in call.text or "Resets in 2 d" in call.text, call.text
    finally:
        inst.release_tools(dm)
    flows.wait_turn_end(inst, dm, "ft18d", since_log=log_since)
    flows.settle(fake)

    # Turn 2 in the group, held: the card is edited (the positive
    # control), and no message in the group ever carries the note.
    try:
        since, log_since = _held_turn(inst, G, grp,
                                      "/ft18g hold this turn. Reply with exactly: OK2")
        edits = []

        def _two_edits():
            edits[:] = [c for c in fake.calls(chat_id=G, since=since)
                        if c.method == "editMessageText" and "ft18g" in c.text]
            return len(edits) >= 2
        fti.wait_until(_two_edits, 90, "two edits of the group's busy card",
                       lambda: inst.log_tail(25))
    finally:
        inst.release_tools(grp)
    flows.wait_turn_end(inst, grp, "ft18g", since_log=log_since)
    flows.settle(fake)
    texts = flows.chat_texts(fake, G, since)
    assert texts, "no group message after the held turn's start"
    for text in texts:
        for word in NEVER_IN_GROUP:
            assert word not in text, text
    # The same reading in alice's private chat did show (the note is the
    # chat's rule, not a missing reading).
    assert any(NOTE in t for t in flows.chat_texts(fake, DM, 0))
