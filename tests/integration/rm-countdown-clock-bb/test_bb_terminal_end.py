"""How a turn that ended at the terminal on a permission dialog is told on
the busy card (roadmap 8.152 b).

Live (Claude Code 2.1.312, release harness): "No" or Escape in the dialog
(D5), and Yes in the dialog then Escape while the command runs (D3), both
fire no hook and write the same rejected result and the same interrupt
marker; only the marker's ``interruptedMessageId`` differs (D3 names the
assistant message holding the call). The card says "Denied in the
terminal" for the first, "Stopped in the terminal while it ran" for the
second, and "Ended in the terminal" when the transcript does not say."""

from __future__ import annotations

import pytest

from aipager import transcript
from aipager.state import Status
from tests import terminal_end_tx as tx
from tests.rm_clock_bb import T0

DENIED = "🚫 <b>api</b> · Denied in the terminal"
RAN = "⚠️ <b>api</b> · Stopped in the terminal while it ran"
ENDED = "⚠️ <b>api</b> · Ended in the terminal"
TUID = "toolu_p1"


# -- the transcript reading --------------------------------------------------------------

def kind(path, tuid=TUID):
    return transcript.terminal_end_kind(str(path), tuid)


def test_the_live_terminal_no_reads_denied(tmp_path):
    p = tmp_path / "t.jsonl"
    tx.terminal_no(p, TUID)
    assert kind(p) == transcript.END_DENIED


def test_the_live_stop_while_running_reads_ran(tmp_path):
    p = tmp_path / "t.jsonl"
    tx.stopped_while_running(p, TUID)
    assert kind(p) == transcript.END_RAN


@pytest.mark.parametrize("tuid", ["", None])
def test_with_no_call_id_the_pair_still_tells(tmp_path, tuid):
    p = tmp_path / "t.jsonl"
    tx.stopped_while_running(p, TUID)
    assert transcript.terminal_end_kind(str(p), tuid) == transcript.END_RAN
    q = tmp_path / "u.jsonl"
    tx.terminal_no(q, TUID)
    assert transcript.terminal_end_kind(str(q), tuid) == transcript.END_DENIED


def test_another_calls_result_is_unknown(tmp_path):
    p = tmp_path / "t.jsonl"
    tx.terminal_no(p, "toolu_other")
    assert kind(p) == transcript.END_UNKNOWN


def test_a_named_message_without_the_call_is_unknown(tmp_path):
    p = tmp_path / "t.jsonl"
    tx.write(p, tx.call(TUID, message_id="msg_a"), tx.rejected(TUID),
             tx.marker(interrupted="msg_b"))
    assert kind(p) == transcript.END_UNKNOWN


@pytest.mark.parametrize("named", ["", 5, None])
def test_a_marker_naming_nothing_usable_is_unknown(tmp_path, named):
    """Not even a call line whose message has no id (or that same odd
    value) counts as named."""
    p = tmp_path / "t.jsonl"
    entry = tx.marker()
    entry["interruptedMessageId"] = named
    call = tx.call(TUID)
    if named is None:
        del call["message"]["id"]
    else:
        call["message"]["id"] = named
    tx.write(p, call, tx.rejected(TUID), entry)
    assert kind(p) == transcript.END_UNKNOWN


@pytest.mark.parametrize("denial", [None, "interrupted", "cancelled", "permission-rule"])
def test_a_result_not_rejected_by_the_person_is_unknown(tmp_path, denial):
    """An older Claude Code (no ``toolDenialKind``) or another kind of end."""
    p = tmp_path / "t.jsonl"
    tx.write(p, tx.call(TUID), tx.rejected(TUID, kind=denial), tx.marker())
    assert kind(p) == transcript.END_UNKNOWN


def test_a_bare_marker_is_unknown(tmp_path):
    p = tmp_path / "t.jsonl"
    tx.write(p, tx.call(TUID), tx.marker())
    assert kind(p) == transcript.END_UNKNOWN


def test_a_newer_entry_after_the_marker_is_unknown(tmp_path):
    p = tmp_path / "t.jsonl"
    tx.terminal_no(p, TUID)
    tx.write(p, {"type": "user", "message": {"role": "user", "content": "next prompt"}})
    assert kind(p) == transcript.END_UNKNOWN


def test_a_rejected_result_with_no_marker_after_it_is_unknown(tmp_path):
    """The rejected result alone (the marker not written yet) says
    nothing: the newest entry must be the marker."""
    p = tmp_path / "t.jsonl"
    tx.write(p, tx.call(TUID), tx.rejected(TUID),
             {"type": "assistant", "message": {"role": "assistant", "content": [
                 {"type": "text", "text": "ok"}]}})
    assert kind(p) == transcript.END_UNKNOWN


def test_bookkeeping_lines_are_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    tx.write(p, tx.call(TUID), tx.rejected(TUID),
             {"type": "file-history-snapshot", "snapshot": {}},
             {"type": "api-request-blob", "message": {"role": "user", "content": []}},
             tx.marker(interrupted="msg_t5"), {"type": "system", "subtype": "x"})
    p.write_text(p.read_text() + "{torn line\n")
    assert kind(p) == transcript.END_RAN


@pytest.mark.parametrize("path", ["", "missing"])
def test_no_file_is_unknown(tmp_path, path):
    target = str(tmp_path / "nope.jsonl") if path else ""
    assert transcript.terminal_end_kind(target, TUID) == transcript.END_UNKNOWN


# -- the card -----------------------------------------------------------------------------

def ended(e, write):
    e.rm(hook=False)
    write(e.path)
    e.scan(T0 + 22.0)
    assert e.rig.sess.status == Status.IDLE


def test_d3_the_card_says_stopped_while_it_ran(cenv):
    ended(cenv, lambda p: tx.stopped_while_running(p, TUID))
    assert cenv.last_card_text() == RAN


def test_d5_the_card_says_denied_in_the_terminal(cenv):
    ended(cenv, lambda p: tx.terminal_no(p, TUID))
    assert cenv.last_card_text() == DENIED


def test_with_no_signal_the_card_says_ended(cenv):
    ended(cenv, lambda p: tx.write(p, tx.marker()))
    assert cenv.last_card_text() == ENDED


def test_another_calls_rejection_says_ended(cenv):
    """The marker pair is for a call other than the prompt's."""
    ended(cenv, lambda p: tx.terminal_no(p, "toolu_other"))
    assert cenv.last_card_text() == ENDED


def test_an_interrupted_failure_hook_says_ran(cenv):
    """A PostToolUseFailure with is_interrupt (the allowed call ran) marks
    the dialog gone: the card says it was stopped while it ran, whatever
    the marker carries."""
    cenv.rm(hook=False)
    cenv.wall.t = T0 + 20.0
    cenv.hook("PostToolUseFailure", tool_name="Bash",
              tool_input={"command": "rm -rf build/", "description": "rm -rf build/"},
              tool_use_id=TUID, error="Interrupted", is_interrupt=True)
    tx.write(cenv.path, tx.marker())
    cenv.scan(T0 + 22.0)
    assert cenv.last_card_text() == RAN

