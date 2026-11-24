"""Claude Code's own session record as evidence that a gapped prompt's
dialog is on screen (entrypoints.md): ``term_dialog.PERMISSION_PROMPT``,
``term_dialog.permission_waiting_since`` and ``prompt_gap.look_for_dialog``
with the record given through the ``prompt_gap._record`` seam and the
clocks pinned through ``prompt_gap._now_mono`` / ``_now_wall``."""

from __future__ import annotations

import time

import pytest

from aipager import prompt_gap
from aipager.term_dialog import PERMISSION_PROMPT, permission_waiting_since
from tests.two_prompts_rig import BG, FG, OTHER

CMD = "make release"
SINCE = 5000.0


def entry(status="waiting", waiting_for="permission prompt", at=SINCE + 0.3, **extra):
    out = {"status": status, "waitingFor": waiting_for, "statusAt": at}
    out.update(extra)
    return out


# -- permission_waiting_since -------------------------------------------------------

def test_permission_prompt_constant():
    assert PERMISSION_PROMPT == "permission prompt"


def test_waiting_for_permission_after_since_gives_status_at():
    assert permission_waiting_since(entry(), SINCE) == pytest.approx(SINCE + 0.3)


def test_status_at_equal_to_since_counts():
    assert permission_waiting_since(entry(at=SINCE), SINCE) == pytest.approx(SINCE)


def test_status_at_just_before_since_is_none():
    assert permission_waiting_since(entry(at=SINCE - 0.001), SINCE) is None


@pytest.mark.parametrize("status", ["busy", "idle", "", None, "Waiting"],
                         ids=["busy", "idle", "empty", "none", "capitalised"])
def test_not_waiting_is_none(status):
    assert permission_waiting_since(entry(status=status), SINCE) is None


@pytest.mark.parametrize("waiting_for", ["dialog open", "", None, "permission", "input"],
                         ids=["dialog-open", "empty", "none", "one-word", "input"])
def test_other_waiting_for_is_none(waiting_for):
    assert permission_waiting_since(entry(waiting_for=waiting_for), SINCE) is None


@pytest.mark.parametrize("at", [None, "5000.3", float("nan")], ids=["none", "str", "nan"])
def test_unusable_status_at_is_none(at):
    assert permission_waiting_since(entry(at=at), SINCE) is None


@pytest.mark.parametrize("e", [None, {}, [], "waiting", 3, {"status": "waiting"}],
                         ids=["none", "empty", "list", "str", "int", "no-fields"])
def test_garbage_entry_is_none_and_never_raises(e):
    assert permission_waiting_since(e, SINCE) is None


# -- look_for_dialog ---------------------------------------------------------------------

@pytest.fixture
def clocks(monkeypatch):
    mono, wall = time.monotonic(), time.time()
    monkeypatch.setattr(prompt_gap, "_now_mono", lambda: mono)
    monkeypatch.setattr(prompt_gap, "_now_wall", lambda: wall)
    return mono, wall


def give_record(monkeypatch, record):
    calls = []

    async def _record(sess):
        calls.append(sess)
        return record() if callable(record) else record

    monkeypatch.setattr(prompt_gap, "_record", _record)
    return calls


def open_bg(rig, agent=BG, cmd=CMD, req="req-1", timeout=True):
    rig.launch(agent)
    rig.agent_bash(agent, cmd, request_id=req)
    if timeout:
        rig.timeout(req)
    return next(p for p in rig.sess.open_prompts()
                if p["tool_info"].get("agent_id") == agent)


def place(perm, **fields):
    perm["tool_info"]["order"].update(fields)
    return perm["tool_info"]["order"]


def look(rig, perm):
    return rig.run_async(prompt_gap.look_for_dialog(rig.sess, perm))


def test_record_after_timeout_stamps(rig, clocks, monkeypatch):
    mono, wall = clocks
    perm = open_bg(rig)
    place(perm, timed_out_at=mono - 1.0)
    give_record(monkeypatch, entry(at=wall - 0.7))
    assert look(rig, perm) is True


def test_stamp_is_monotonic_of_status_at(rig, clocks, monkeypatch):
    mono, wall = clocks
    perm = open_bg(rig)
    order = place(perm, timed_out_at=mono - 1.0)
    give_record(monkeypatch, entry(at=wall - 0.7))
    look(rig, perm)
    assert order["dialog_seen_at"] == pytest.approx(mono - 0.7, abs=1e-3)


def test_stamp_is_never_before_the_timeout(rig, clocks, monkeypatch):
    """A record stamped inside the 0.5 s slack before the timeout counts,
    but the stamp is the timeout itself."""
    mono, wall = clocks
    perm = open_bg(rig)
    order = place(perm, timed_out_at=mono - 1.0)
    give_record(monkeypatch, entry(at=wall - 1.3))
    look(rig, perm)
    assert order["dialog_seen_at"] == pytest.approx(mono - 1.0, abs=1e-3)


def test_gone_slack_is_half_a_second():
    assert prompt_gap.GONE_SLACK == 0.5


def test_record_at_the_slack_edge_stamps(rig, clocks, monkeypatch):
    mono, wall = clocks
    perm = open_bg(rig)
    place(perm, timed_out_at=mono - 1.0)
    give_record(monkeypatch, entry(at=wall - 1.0 - 0.5 + 1e-4))
    assert look(rig, perm) is True


def test_record_before_the_slack_does_not_stamp(rig, clocks, monkeypatch):
    mono, wall = clocks
    perm = open_bg(rig)
    place(perm, timed_out_at=mono - 1.0)
    give_record(monkeypatch, entry(at=wall - 1.0 - 0.5 - 0.01))
    assert look(rig, perm) is False


def test_record_before_the_slack_leaves_no_stamp(rig, clocks, monkeypatch):
    mono, wall = clocks
    perm = open_bg(rig)
    order = place(perm, timed_out_at=mono - 1.0)
    give_record(monkeypatch, entry(at=wall - 2.0))
    look(rig, perm)
    assert not order.get("dialog_seen_at")


@pytest.mark.parametrize("record", [
    None,
    entry(status="busy"),
    entry(waiting_for="dialog open"),
    {},
], ids=["no-record", "not-waiting", "other-dialog", "empty"])
def test_no_stamp_without_permission_waiting(rig, clocks, monkeypatch, record):
    mono, wall = clocks
    perm = open_bg(rig)
    place(perm, timed_out_at=mono - 1.0)
    if isinstance(record, dict) and "statusAt" in record:
        record = dict(record, statusAt=wall - 0.5)
    give_record(monkeypatch, record)
    assert look(rig, perm) is False


def test_record_read_failure_never_raises(rig, clocks, monkeypatch):
    mono, _wall = clocks
    perm = open_bg(rig)
    place(perm, timed_out_at=mono - 1.0)

    async def _boom(sess):
        raise OSError("unreadable")

    monkeypatch.setattr(prompt_gap, "_record", _boom)
    assert look(rig, perm) is False


def test_no_stamp_before_the_timeout_is_known(rig, clocks, monkeypatch):
    """The hook is parked (timed_out_at 0): the record cannot be tied to
    a time after the hook's end."""
    _mono, wall = clocks
    perm = open_bg(rig, timeout=False)
    give_record(monkeypatch, entry(at=wall - 0.1))
    assert look(rig, perm) is False


def test_no_second_stamp(rig, clocks, monkeypatch):
    mono, wall = clocks
    perm = open_bg(rig)
    order = place(perm, timed_out_at=mono - 1.0, dialog_seen_at=mono - 0.9)
    give_record(monkeypatch, entry(at=wall - 0.2))
    look(rig, perm)
    assert order["dialog_seen_at"] == pytest.approx(mono - 0.9)


def test_no_stamp_with_two_prompts_in_gap(rig, clocks, monkeypatch):
    mono, wall = clocks
    first = open_bg(rig, BG, "make a", "req-a")
    second = open_bg(rig, OTHER, "make b", "req-b")
    place(first, timed_out_at=mono - 1.0)
    place(second, timed_out_at=mono - 0.9)
    give_record(monkeypatch, entry(at=wall - 0.5))
    assert look(rig, first) is False


def test_stamp_when_the_other_prompt_is_out_of_its_gap(rig, clocks, monkeypatch):
    mono, wall = clocks
    first = open_bg(rig, BG, "make a", "req-a")
    second = open_bg(rig, OTHER, "make b", "req-b")
    place(first, timed_out_at=mono - 1.0)
    place(second, arrived=mono - 60.0, timed_out_at=mono - 30.0)
    give_record(monkeypatch, entry(at=wall - 0.5))
    assert look(rig, first) is True


def test_no_stamp_for_a_foreground_prompt(rig, clocks, monkeypatch):
    mono, wall = clocks
    rig.launch(FG, background=False)
    rig.agent_bash(FG, CMD, request_id="req-fg")
    rig.timeout("req-fg")
    [perm] = rig.sess.open_prompts()
    order = place(perm, timed_out_at=mono - 1.0)
    give_record(monkeypatch, entry(at=wall - 0.5))
    look(rig, perm)
    assert not order.get("dialog_seen_at")
