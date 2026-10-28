"""A daemon restart with Claude Code's risky-removal prompt open (roadmap
8.128; design.md criteria 11 and 21): the saved prompt keeps its clock
(``perm.rm_clock``), so after the restart the card shows the same line
and the prompt still closes as refused when the transcript says so. A
malformed clock invalidates the saved record (fail closed); a record
without one reads back as before."""

from __future__ import annotations

import copy
import json
import math

import pytest

from aipager import i18n, open_prompt, state
from aipager.bot import rm_refusal
from aipager.bot.callbacks import PROMPT_REFUSED_CLOSED
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status
from tests.rm_clock_rig import ARRIVED, rm_prompt, scan, sha
from tests.two_prompts_rig import CARD, S, Rig, answers

EN = i18n.get("en")
IN_WINDOW = 120.0 - 15.0 + 1.0
CLOCK = {"refuse_at": ARRIVED + 120.0, "command_sha": sha()}


def clock_line() -> str:
    from aipager.bot.flood import clear_time
    return EN.html(rm_refusal.RM_CLOCK, time=clear_time(ARRIVED + 120.0))


def saved(r) -> dict:
    r.bot.registry.save()
    return json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][S]


def restarted(r, mk_bot, monkeypatch, run_async) -> Rig:
    r.bot.registry.save()
    again = SessionRegistry()
    again.load()
    new = Rig(mk_bot, monkeypatch, run_async, registry=again)
    sess = again.get(S)
    rec = sess.restored_open_prompt
    sess.restored_open_prompt = None
    assert new.bot._restore_open_prompt(S, sess, rec, {S}) is True
    return new


# -- the saved record -----------------------------------------------------------------

def test_the_saved_prompt_keeps_its_clock(rig, tx):
    rm_prompt(rig, tx)
    assert saved(rig)["open_prompt"]["perm"]["rm_clock"] == CLOCK


def test_a_prompt_with_no_clock_saves_none(rig, tx):
    rig.claude_bash("make", mode="default", transcript_path=str(tx))
    assert "rm_clock" not in saved(rig)["open_prompt"]["perm"]


def record(rig, tx) -> dict:
    rm_prompt(rig, tx)
    return saved(rig)["open_prompt"]


def valid(raw, rig):
    return open_prompt.validate(raw, busy_msg_id=rig.sess.busy_msg_id, now_wall=ARRIVED + 5)


def test_a_valid_clock_reads_back(rig, tx):
    raw = record(rig, tx)
    assert valid(raw, rig)["perm"]["rm_clock"] == CLOCK


def test_a_record_without_a_clock_reads_back_as_before(rig, tx):
    raw = record(rig, tx)
    del raw["perm"]["rm_clock"]
    out = valid(raw, rig)
    assert out is not None and "rm_clock" not in out["perm"]


BAD_CLOCKS = {
    "extra-key": {**CLOCK, "x": 1},
    "missing-sha": {"refuse_at": CLOCK["refuse_at"]},
    "missing-time": {"command_sha": CLOCK["command_sha"]},
    "nan": {**CLOCK, "refuse_at": math.nan},
    "inf": {**CLOCK, "refuse_at": math.inf},
    "zero": {**CLOCK, "refuse_at": 0},
    "negative": {**CLOCK, "refuse_at": -5.0},
    "bool": {**CLOCK, "refuse_at": True},
    "str-time": {**CLOCK, "refuse_at": "1800000120"},
    "short-sha": {**CLOCK, "command_sha": "ab12"},
    "upper-sha": {**CLOCK, "command_sha": CLOCK["command_sha"].upper()},
    "int-sha": {**CLOCK, "command_sha": 5},
    "none": None,
    "list": [CLOCK["refuse_at"], CLOCK["command_sha"]],
}


@pytest.mark.parametrize("name", BAD_CLOCKS)
def test_a_malformed_clock_invalidates_the_record(rig, tx, name):
    raw = record(rig, tx)
    raw = copy.deepcopy(raw)
    raw["perm"]["rm_clock"] = BAD_CLOCKS[name]
    assert valid(raw, rig) is None


# -- after the restart ---------------------------------------------------------------

def test_the_restored_card_prompt_shows_the_clock(rig, tx, mk_bot, monkeypatch, run_async):
    rm_prompt(rig, tx)
    new = restarted(rig, mk_bot, monkeypatch, run_async)
    assert new.sess.status == Status.INTERACTIVE
    text = new.bot._build_busy_text(new.sess.label, "Waiting", new.sess)
    assert text.count(clock_line()) == 1


def test_the_restored_separate_prompt_keeps_its_clock(cardless, tx, mk_bot, monkeypatch,
                                                      run_async):
    rm_prompt(cardless, tx)
    new = restarted(cardless, mk_bot, monkeypatch, run_async)
    text, _kb = new.bot._pending_prompt_markup(new.sess)
    assert text.count(clock_line()) == 1


def test_a_restored_prompt_closes_as_refused(rig, tx, clock, audits, mk_bot, monkeypatch,
                                             run_async):
    rm_prompt(rig, tx)
    new = restarted(rig, mk_bot, monkeypatch, run_async)
    monitor = SessionMonitor(new.bot.registry, new.bot.notify)
    tx.refused()
    clock.at(IN_WINDOW)
    scan(new, monitor)
    assert new.sess.open_prompts() == []
    assert [a["action"] for a in audits] == ["Refused by Claude Code"]
    q = new.tap(CARD, "allow")
    assert (answers(q), new.keys) == ([str(PROMPT_REFUSED_CLOSED)], [])
