"""The gap after a background agent's hook gave up (aipager/prompt_stack.py,
aipager/prompt_gap.py).

A background agent's permission dialog enters Claude Code's terminal only
once its PermissionRequest hook is gone, and a freshly drawn dialog
ignores a key for a moment. Until the dialog is on screen and settled (or
a grace period after the hook's end has run out) no key may be typed for
it: the verdict is ``"opening"``. Claude's own and a foreground agent's
dialogs are drawn beside the hook, so they are never in a gap.
"""

from __future__ import annotations

import logging

import pytest

from aipager import prompt_gap, term_dialog
from aipager import prompt_stack as ps

NOW = 1000.0
#: The gap's own constants, passed explicitly (as reply_deadline/grace are).
KW = {"reply_deadline": 20.0, "grace": 10.0, "settle": 2.0, "open_grace": 4.0}


def own(at=10.0):
    return {"owner": "", "launch": "own", "arrived": at}


def agent(launch="background", at=NOW - 25, *, request_id="r", timed_out_at=0.0,
          seen=None, owner="a1"):
    order = {"owner": owner, "launch": launch, "arrived": at,
             "request_id": request_id, "timed_out_at": timed_out_at}
    if seen is not None:
        order["dialog_seen_at"] = seen
    return order


def test_constants():
    assert ps.DIALOG_SETTLE == 2.0
    assert ps.OPEN_GRACE == 4.0
    assert prompt_gap.GONE_SLACK == 0.5
    assert term_dialog.PERMISSION_PROMPT == "permission prompt"


# -- the verdict ------------------------------------------------------------------

@pytest.mark.parametrize("launch", ["background", "unknown"])
def test_lone_background_timed_out_in_gap_is_opening(launch):
    target = agent(launch, timed_out_at=NOW - 1)
    assert ps.keys_verdict(target, [], NOW, **KW) == "opening"


def test_lone_prompt_out_of_its_gap_is_yes():
    assert ps.keys_verdict(agent(timed_out_at=NOW - 5), [], NOW, **KW) == "yes"


def test_target_in_gap_with_only_absent_others_is_opening():
    """With others open, the existing rules run; a would-be "yes" becomes
    "opening" in the target's gap (here the only other prompt is a parked
    background one, certainly not in the terminal)."""
    target = agent(timed_out_at=NOW - 1, owner="a1")
    other = agent(at=NOW - 5, owner="a2")
    assert ps.keys_verdict(target, [other], NOW, **KW) == "opening"
    # Out of the gap, the same pair is "yes".
    assert ps.keys_verdict(agent(timed_out_at=NOW - 5, owner="a1"), [other],
                           NOW, **KW) == "yes"


def test_target_in_gap_after_a_settled_others_window_is_opening():
    """The last "yes" (target certainly entered before every other) is
    wrapped too."""
    target = agent("unknown", at=NOW - 25, timed_out_at=NOW - 1, owner="a1")
    other = agent("background", at=NOW - 20, request_id="", owner="a2")
    # other: no hook, entered somewhere in [NOW-20, NOW]: overlap -> unknown.
    assert ps.keys_verdict(target, [other], NOW, **KW) == "unknown"
    later = agent("unknown", at=NOW - 0.5, request_id="r2", owner="a2")
    # later: parked unknown, its window starts at NOW-0.5 > target's hi.
    assert ps.keys_verdict(target, [later], NOW, **KW) == "opening"
    out = agent("unknown", at=NOW - 25, timed_out_at=NOW - 4.0, owner="a1")
    assert ps.keys_verdict(out, [later], NOW, **KW) == "yes"


def test_behind_and_unknown_beat_opening():
    target = agent(timed_out_at=NOW - 1)
    assert ps.keys_verdict(target, [own(10.0)], NOW, **KW) == "behind"
    bash = {"owner": "", "launch": "own", "arrived": 10.0, "tool": "Bash",
            "mode": "auto"}
    assert ps.keys_verdict(target, [bash], NOW, **KW) == "unknown"


def test_foreground_and_own_never_in_gap():
    for order in (own(NOW - 1),
                  agent("foreground", timed_out_at=NOW - 1),
                  agent("foreground", at=NOW - 1),
                  agent("own", at=NOW - 1, request_id=""),
                  agent(timed_out_at=NOW - 1, owner=""),
                  None, "x", {"owner": "a1", "launch": "background"},
                  agent(at=True, timed_out_at=NOW - 1)):
        assert ps.dialog_gap(order, NOW, **KW) is None
        assert ps.keys_verdict(order if isinstance(order, dict) else {}, [],
                               NOW, **KW) == "yes"


# -- the gap's boundaries -----------------------------------------------------------

def test_gap_ends_exactly_at_open_grace():
    gone = NOW - 10
    order = agent(timed_out_at=gone)
    assert ps.dialog_gap(order, gone + 3.999, **KW) == gone + 4.0
    assert ps.dialog_gap(order, gone + 4.0, **KW) is None
    assert ps.keys_verdict(order, [], gone + 3.999, **KW) == "opening"
    assert ps.keys_verdict(order, [], gone + 4.0, **KW) == "yes"


def test_open_grace_follows_its_keyword():
    gone = NOW - 10
    order = agent(timed_out_at=gone)
    assert ps.dialog_gap(order, gone + 5.0, **{**KW, "open_grace": 6.0}) == gone + 6.0


def test_seen_dialog_closes_gap_after_settle():
    gone = NOW - 10
    seen = gone + 0.3
    order = agent(timed_out_at=gone, seen=seen)
    assert ps.dialog_gap(order, seen + 1.999, **KW) == seen + 2.0
    assert ps.dialog_gap(order, seen + 2.0, **KW) is None
    assert ps.keys_verdict(order, [], seen + 2.0, **KW) == "yes"


def test_seen_before_the_hook_left_is_no_evidence():
    gone = NOW - 10
    order = agent(timed_out_at=gone, seen=gone - 0.1)
    assert ps.dialog_gap(order, gone + 3.0, **KW) == gone + 4.0


def test_parked_hook_is_in_gap_until_deadline_plus_grace():
    """The socket-unlink race: the hook may be gone while the timeout is
    not handled yet (timed_out_at 0), so a parked prompt is in its gap."""
    order = agent(at=NOW - 29.999, timed_out_at=0.0)
    assert ps.dialog_gap(order, NOW, **KW) == pytest.approx(NOW + 0.001)
    # After the window, the hook is taken as gone at the deadline (a lost
    # datagram): long past that plus the grace, so out of the gap.
    assert ps.dialog_gap(agent(at=NOW - 30.0), NOW, **KW) is None


def test_no_reply_channel_gap_runs_from_arrival():
    assert ps.dialog_gap(agent(at=NOW - 3.9, request_id=""), NOW, **KW) == pytest.approx(
        NOW + 0.1)
    assert ps.dialog_gap(agent(at=NOW - 4.0, request_id=""), NOW, **KW) is None


@pytest.mark.parametrize("launch", ["background", "unknown"])
def test_a_parked_agent_question_stays_gated_until_its_hook_is_gone(launch):
    """Decision (fixes-2.md, rev-iter1-002): an agent's AskUserQuestion
    whose PermissionRequest hook is parked is gated like any other prompt,
    for unknown launch too, although no hook decision can answer a
    question. Its dialog may not be drawn until the hook leaves (a
    background agent's, or one moved there), and option keys with no
    dialog go into Claude's input line: up and down walk its history,
    Enter sends what is there. Failing closed costs a person at most the
    hook's wait plus the grace."""
    order = dict(agent(launch, at=NOW - 15.0), tool="AskUserQuestion")
    assert ps.keys_verdict(order, [], NOW, **KW) == "opening"
    gone = dict(order, timed_out_at=NOW - 4.0)
    assert ps.keys_verdict(gone, [], NOW, **KW) == "yes"


def test_gap_next_look_probes_at_timeout_plus_settle():
    gone = NOW - 1
    order = agent(timed_out_at=gone)
    assert ps.gap_next_look(order, NOW, **KW) == gone + 2.0
    # The probe instant passed: the gap's end.
    assert ps.gap_next_look(order, gone + 2.5, **KW) == gone + 4.0
    # Evidence stamped: the gap's end (seen + settle).
    order["dialog_seen_at"] = gone + 0.2
    assert ps.gap_next_look(order, NOW, **KW) == pytest.approx(gone + 2.2)
    # Parked (no timeout known): the gap's end.
    parked = agent(at=NOW - 5)
    assert ps.gap_next_look(parked, NOW, **KW) == pytest.approx(NOW + 25)
    assert ps.gap_next_look(agent(timed_out_at=NOW - 5), NOW, **KW) is None


# -- the record --------------------------------------------------------------------

WALL = 1_800_000_000.0


def entry(status="waiting", waiting_for="permission prompt", at=WALL):
    return {"status": status, "waitingFor": waiting_for, "statusAt": at}


def test_permission_waiting_since():
    assert term_dialog.permission_waiting_since(entry(), WALL) == WALL
    assert term_dialog.permission_waiting_since(entry(), WALL + 0.001) is None
    assert term_dialog.permission_waiting_since(entry(waiting_for="dialog open"),
                                                WALL - 1) is None
    assert term_dialog.permission_waiting_since(entry(status="busy"), WALL - 1) is None
    assert term_dialog.permission_waiting_since(entry(at=None), WALL - 1) is None
    assert term_dialog.permission_waiting_since(entry(at=True), 0.0) is None
    assert term_dialog.permission_waiting_since(None, 0.0) is None
    assert term_dialog.permission_waiting_since("x", 0.0) is None  # type: ignore[arg-type]


class FakeSess:
    label = "frontend"

    def __init__(self, *perms):
        self.perms = list(perms)

    def open_prompts(self):
        return list(self.perms)


def perm_of(order):
    return {"tool_info": {"name": "Bash", "order": order}}


class Clocks:
    def __init__(self, monkeypatch, mono=5000.0):
        self.mono, self.wall = mono, WALL
        monkeypatch.setattr(prompt_gap, "_now_mono", lambda: self.mono)
        monkeypatch.setattr(prompt_gap, "_now_wall", lambda: self.wall)

    def wall_of(self, mono):
        return self.wall - (self.mono - mono)


def _record_returns(monkeypatch, value):
    reads = []

    async def _record(sess):
        reads.append(sess)
        return value

    monkeypatch.setattr(prompt_gap, "_record", _record)
    return reads


def test_look_stamps_on_a_waiting_permission_record(monkeypatch, run_async, caplog):
    caplog.set_level(logging.INFO)
    clk = Clocks(monkeypatch)
    gone = clk.mono - 1.0
    order = agent(at=gone - 20, timed_out_at=gone)
    perm = perm_of(order)
    reads = _record_returns(monkeypatch, entry(at=clk.wall_of(gone) + 0.3))
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm), perm)) is True
    assert len(reads) == 1
    assert order["dialog_seen_at"] == pytest.approx(gone + 0.3)
    assert any("permission dialog on screen" in r.getMessage() for r in caplog.records)
    # Stamped once: a second look reads nothing.
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm), perm)) is False
    assert len(reads) == 1


def test_stamp_is_never_before_the_timeout(monkeypatch, run_async):
    clk = Clocks(monkeypatch)
    gone = clk.mono - 1.0
    order = agent(at=gone - 20, timed_out_at=gone)
    perm = perm_of(order)
    _record_returns(monkeypatch, entry(at=clk.wall_of(gone) - 0.4))
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm), perm)) is True
    assert order["dialog_seen_at"] == gone


def test_no_stamp_for_other_waiting_for(monkeypatch, run_async):
    clk = Clocks(monkeypatch)
    gone = clk.mono - 1.0
    order = agent(at=gone - 20, timed_out_at=gone)
    perm = perm_of(order)
    _record_returns(monkeypatch, entry(waiting_for="dialog open",
                                       at=clk.wall_of(gone) + 0.3))
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm), perm)) is False
    assert "dialog_seen_at" not in order


def test_no_stamp_for_status_before_timeout_slack(monkeypatch, run_async):
    clk = Clocks(monkeypatch)
    gone = clk.mono - 1.0
    order = agent(at=gone - 20, timed_out_at=gone)
    perm = perm_of(order)
    _record_returns(monkeypatch, entry(at=clk.wall_of(gone) - 0.6))
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm), perm)) is False
    assert "dialog_seen_at" not in order


def test_no_stamp_when_not_waiting(monkeypatch, run_async):
    clk = Clocks(monkeypatch)
    gone = clk.mono - 1.0
    order = agent(at=gone - 20, timed_out_at=gone)
    perm = perm_of(order)
    _record_returns(monkeypatch, entry(status="busy", at=clk.wall_of(gone) + 0.3))
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm), perm)) is False
    assert "dialog_seen_at" not in order


def test_no_stamp_with_two_prompts_in_gap(monkeypatch, run_async):
    clk = Clocks(monkeypatch)
    gone = clk.mono - 1.0
    order = agent(at=gone - 20, timed_out_at=gone)
    perm = perm_of(order)
    other = perm_of(agent(at=clk.mono - 5, owner="a2"))  # parked: in its gap
    reads = _record_returns(monkeypatch, entry(at=clk.wall_of(gone) + 0.3))
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm, other), perm)) is False
    assert "dialog_seen_at" not in order
    assert reads == []
    # Beside a prompt out of any gap (Claude's own), the record is read.
    own_perm = perm_of(own(10.0))
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm, own_perm), perm)) is True


@pytest.mark.parametrize("case", ["no-timeout", "out-of-gap", "own", "no-order"])
def test_no_read_outside_a_known_timeout_gap(monkeypatch, run_async, case):
    clk = Clocks(monkeypatch)
    order = {"no-timeout": agent(at=clk.mono - 5),
             "out-of-gap": agent(at=clk.mono - 30, timed_out_at=clk.mono - 5),
             "own": own(clk.mono - 1),
             "no-order": None}[case]
    perm = perm_of(order) if order is not None else {"tool_info": {}}
    reads = _record_returns(monkeypatch, entry(at=clk.wall))
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm), perm)) is False
    assert reads == []


def test_look_never_raises(monkeypatch, run_async):
    clk = Clocks(monkeypatch)
    gone = clk.mono - 1.0
    perm = perm_of(agent(at=gone - 20, timed_out_at=gone))

    async def _boom(sess):
        raise OSError("unreadable")

    monkeypatch.setattr(prompt_gap, "_record", _boom)
    assert run_async(prompt_gap.look_for_dialog(FakeSess(perm), perm)) is False
