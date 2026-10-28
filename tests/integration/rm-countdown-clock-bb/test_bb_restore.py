"""A daemon restart with Claude's risky-rm prompt open (design.md success
criteria 11 and 21, decision 2 "Restored prompts"): the saved record
carries ``perm.rm_clock``; it round-trips; a record without it reads as
before; any malformed ``rm_clock`` invalidates the record (fail closed).
After the restart the inline line is re-derived, the separate message's
text keeps the line as sent, the refusal window still gates taps, the tick
still closes it as refused, and an orderless restored Bash prompt is a
countdown candidate (criterion 5)."""

from __future__ import annotations

import hashlib
import json
import math

import pytest

from aipager import state
from aipager.state import SessionRegistry
from tests.rm_clock_bb import (
    CLOCK_EN,
    REFUSE_AT,
    RM,
    TOAST_REFUSAL_DUE,
    TOAST_UNKNOWN,
    WINDOW_AT,
    Env,
    hhmm,
)
from tests.two_prompts_rig import CARD, S, Rig, answers

SHA = hashlib.sha256(RM.encode("utf-8")).hexdigest()
LINE = CLOCK_EN.format(time=hhmm(REFUSE_AT))


def saved(env) -> dict:
    env.rig.bot.registry.save()
    return json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][S]


def restart(env, mk_bot, monkeypatch, run_async, *, recover=True) -> Env:
    env.rig.bot.registry.save()
    again = SessionRegistry()
    again.load()
    rig = Rig(mk_bot, monkeypatch, run_async, registry=again)
    if recover:
        run_async(rig.bot.recover_sessions())
    out = Env.__new__(Env)
    out.rig, out.wall, out.run_async, out.path = rig, env.wall, run_async, env.path
    rig.sess.transcript_path = str(env.path)
    from aipager.session_monitor import SessionMonitor
    out.monitor = SessionMonitor(again, rig.bot.notify)
    return out


# -- the saved record --------------------------------------------------------------------------

def test_saved_record_carries_the_clock(cenv):
    cenv.rm()
    assert saved(cenv)["open_prompt"]["perm"]["rm_clock"] == {
        "refuse_at": REFUSE_AT, "command_sha": SHA}


def test_saved_separate_record_carries_the_clock(env):
    env.rm()
    assert saved(env)["open_prompt"]["perm"]["rm_clock"] == {
        "refuse_at": REFUSE_AT, "command_sha": SHA}


def test_saved_separate_record_text_keeps_the_line_as_sent(env):
    env.rm()
    assert LINE in json.dumps(saved(env)["open_prompt"], ensure_ascii=False)


@pytest.mark.parametrize("mode", ["default", None])
def test_a_non_clock_prompt_saves_no_clock(cenv, mode):
    cenv.rm(mode=mode)
    assert "rm_clock" not in saved(cenv)["open_prompt"]["perm"]


# -- round trip ----------------------------------------------------------------------------------

def test_a_clock_record_is_restored(cenv, mk_bot, monkeypatch, run_async):
    cenv.rm()
    after = restart(cenv, mk_bot, monkeypatch, run_async, recover=False)
    assert after.rig.sess.restored_open_prompt is not None


def test_after_restart_the_inline_line_is_re_derived(cenv, mk_bot, monkeypatch, run_async):
    cenv.rm()
    after = restart(cenv, mk_bot, monkeypatch, run_async)
    assert LINE in after.rig.bot._build_busy_text("api", "Waiting", after.rig.sess)


def test_after_restart_the_tick_closes_it_as_refused(cenv, mk_bot, monkeypatch, run_async):
    cenv.rm()
    after = restart(cenv, mk_bot, monkeypatch, run_async)
    cenv.refused_transcript()
    after.scan(REFUSE_AT + 1.0)
    assert [a["via"] for a in after.refused_audit()] == ["claude_code_timeout"]


def test_after_restart_the_window_gates_a_tap(cenv, mk_bot, monkeypatch, run_async):
    cenv.rm()
    after = restart(cenv, mk_bot, monkeypatch, run_async)
    after.wall.t = WINDOW_AT + 1.0
    q = after.rig.tap(CARD, "allow")
    assert (answers(q), after.rig.decisions, after.rig.keys) == ([TOAST_REFUSAL_DUE], [], [])


def test_after_restart_a_tap_before_the_window_still_answers(cenv, mk_bot, monkeypatch,
                                                             run_async):
    cenv.rm()
    after = restart(cenv, mk_bot, monkeypatch, run_async)
    after.wall.t = WINDOW_AT - 1.0
    after.rig.tap(CARD, "allow")
    assert [d["behavior"] for _r, d in after.rig.decisions] == ["allow"]


def test_after_restart_the_restored_bash_prompt_is_a_candidate(cenv, mk_bot, monkeypatch,
                                                               run_async):
    """Criterion 5: a restored (orderless) Bash prompt beside another one
    refuses typed taps."""
    cenv.rm("ls -la", mode="default", hook=False)
    after = restart(cenv, mk_bot, monkeypatch, run_async)
    after.rig.question()
    extra = [m["msg_id"] for m in after.rig.prompt_messages()]
    assert answers(after.rig.tap(extra[-1], "opt0")) == [TOAST_UNKNOWN]


def test_a_record_without_the_clock_reads_as_before(cenv, mk_bot, monkeypatch, run_async):
    cenv.rm(mode="default")
    after = restart(cenv, mk_bot, monkeypatch, run_async, recover=False)
    assert after.rig.sess.restored_open_prompt is not None


# -- malformed rm_clock invalidates the record --------------------------------------------------

BAD = {
    "extra-key": {"refuse_at": REFUSE_AT, "command_sha": SHA, "x": 1},
    "missing-sha": {"refuse_at": REFUSE_AT},
    "missing-at": {"command_sha": SHA},
    "nan": {"refuse_at": math.nan, "command_sha": SHA},
    "inf": {"refuse_at": math.inf, "command_sha": SHA},
    "zero": {"refuse_at": 0, "command_sha": SHA},
    "negative": {"refuse_at": -1.0, "command_sha": SHA},
    "bool-at": {"refuse_at": True, "command_sha": SHA},
    "str-at": {"refuse_at": "1800000120", "command_sha": SHA},
    "upper-sha": {"refuse_at": REFUSE_AT, "command_sha": SHA.upper()},
    "short-sha": {"refuse_at": REFUSE_AT, "command_sha": SHA[:63]},
    "long-sha": {"refuse_at": REFUSE_AT, "command_sha": SHA + "0"},
    "non-hex-sha": {"refuse_at": REFUSE_AT, "command_sha": "g" * 64},
    "int-sha": {"refuse_at": REFUSE_AT, "command_sha": 5},
    "list": [REFUSE_AT, SHA],
    "null": None,
    "bool": True,
    "str": "rm",
    "empty": {},
}


def write_record(clock, *, present=True):
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    perm = data["sessions"][S]["open_prompt"]["perm"]
    if present:
        perm["rm_clock"] = clock
    else:
        perm.pop("rm_clock", None)
    state.SESSION_STATE_FILE.write_text(json.dumps(data))


def load_after(clock, **kw):
    write_record(clock, **kw)
    again = SessionRegistry()
    again.load()
    return again.get(S).restored_open_prompt


@pytest.mark.parametrize("name", BAD)
def test_a_malformed_clock_invalidates_the_record(cenv, name):
    cenv.rm()
    cenv.rig.bot.registry.save()
    assert load_after(BAD[name]) is None


@pytest.mark.parametrize("clock", [
    {"refuse_at": REFUSE_AT, "command_sha": SHA},
    {"refuse_at": 1, "command_sha": SHA},
    {"refuse_at": 0.001, "command_sha": SHA},
], ids=["as-saved", "int-one", "tiny-positive"])
def test_a_valid_clock_keeps_the_record(cenv, clock):
    cenv.rm()
    cenv.rig.bot.registry.save()
    assert load_after(clock) is not None


def test_a_removed_clock_keeps_the_record(cenv):
    cenv.rm()
    cenv.rig.bot.registry.save()
    assert load_after(None, present=False) is not None
