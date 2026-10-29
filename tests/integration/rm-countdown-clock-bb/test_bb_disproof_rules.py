"""The disproof rules on their own (roadmap 8.152 c): when a clock may be
disproven, what a disproven one still does, and how it is saved."""

from __future__ import annotations

import hashlib
import json

import pytest

from aipager import rm_countdown, state
from aipager.state import SessionRegistry
from tests.rm_clock_bb import RM, T0
from tests.two_prompts_rig import S

SHA = hashlib.sha256(RM.encode("utf-8")).hexdigest()
AT = T0 + 120.0


def clock(**extra):
    return {"tool_info": {"rm_clock": {"refuse_at": AT, "command_sha": SHA, **extra}}}


def test_disproof_is_due_only_from_the_margin():
    p = clock()
    assert not rm_countdown.disproof_due(p, AT + rm_countdown.DISPROVE_AFTER_SECONDS - 0.01)
    assert rm_countdown.disproof_due(p, AT + rm_countdown.DISPROVE_AFTER_SECONDS)


def test_the_margin_is_twenty_seconds():
    assert rm_countdown.DISPROVE_AFTER_SECONDS == 20.0


def test_a_disproven_clock_is_not_due_again():
    p = clock()
    assert rm_countdown.disprove(p) is True
    assert rm_countdown.disprove(p) is False
    assert not rm_countdown.disproof_due(p, AT + 999.0)


def test_disproving_marks_the_shared_tool_info():
    info = clock()["tool_info"]
    a, b = {"tool_info": info}, {"tool_info": info}
    rm_countdown.disprove(a)
    assert rm_countdown.clock_disproven(b)


def test_a_disproven_clock_opens_no_tap_window():
    p = clock()
    assert rm_countdown.refusal_window_open(p, AT)
    rm_countdown.disprove(p)
    assert not rm_countdown.refusal_window_open(p, AT)
    assert not rm_countdown.refusal_window_open(p, AT + 999.0)


def test_a_disproven_clock_is_still_read_for_a_refusal():
    p = clock()
    rm_countdown.disprove(p)
    assert rm_countdown.result_check_open(p, AT)
    assert not rm_countdown.result_check_open(p, AT - rm_countdown.WINDOW_LEAD_SECONDS - 1)


def test_a_disproven_clock_is_not_shown():
    p = clock()
    assert rm_countdown.shown_clock(p) is not None
    rm_countdown.disprove(p)
    assert rm_countdown.shown_clock(p) is None
    assert rm_countdown.clock_of(p) is not None


@pytest.mark.parametrize("value", [False, 1, "yes", None])
def test_a_disproven_flag_is_only_true(value):
    assert not rm_countdown.valid_clock({"refuse_at": AT, "command_sha": SHA,
                                         "disproven": value})


def test_a_disproven_clock_is_valid():
    assert rm_countdown.valid_clock({"refuse_at": AT, "command_sha": SHA, "disproven": True})


def test_no_clock_disproves_nothing():
    assert rm_countdown.disprove({"tool_info": {}}) is False
    assert not rm_countdown.disproof_due({"tool_info": {}}, AT + 999.0)


# -- the saved record --------------------------------------------------------------------------

def load_after(cenv, rm_clock):
    cenv.rig.bot.registry.save()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    data["sessions"][S]["open_prompt"]["perm"]["rm_clock"] = rm_clock
    state.SESSION_STATE_FILE.write_text(json.dumps(data))
    again = SessionRegistry()
    again.load()
    return again.get(S).restored_open_prompt


def test_a_saved_disproven_clock_reads_back(cenv):
    cenv.rm()
    rec = load_after(cenv, {"refuse_at": AT, "command_sha": SHA, "disproven": True})
    assert rec is not None
    assert rec["perm"]["rm_clock"] == {"refuse_at": AT, "command_sha": SHA,
                                       "disproven": True}


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_a_saved_bad_disproven_flag_invalidates_the_record(cenv, value):
    cenv.rm()
    assert load_after(cenv, {"refuse_at": AT, "command_sha": SHA, "disproven": value}) is None
