"""QA: why a session ended (E2), through the monitor's "disappeared" path.

design.md success criterion 8: "A vanished session's notice names the exit
code or signal; with no exit file it reads as today."

Partitions of the exit record ``{"code","start","end"}``:
- none / a symlink (not ours) / stale (``end`` more than 120 s ago);
- code 0 / non-zero within the 10 s start window (boundary 10 and 11) /
  128+N with a known signal (129, 137, 143) / 128 and 255 (not a signal) /
  another non-zero;
- a start failure with or without a live background holder of the
  conversation (``start_failed_bg`` with Bring it back).
"""

from __future__ import annotations

import logging
import os
import time

import pytest

from aipager.dtach import bg_jobs
from aipager.state import Status


def _kit():
    import importlib.util
    import sys
    from pathlib import Path
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()

TODAY = "🔴 <b>frontend</b> · Session crashed or killed"


def _end_notice(stack) -> str:
    texts = [t for t in qa.sent_texts(stack) if t.startswith("🔴 <b>frontend</b>")]
    assert len(texts) == 1, texts
    return texts[0]


def _idle(stack):
    stack.registry.transition(stack.ids.NAME, Status.IDLE)


def _ended(stack, *, code, ran=60, age=1):
    """Claude exited *age* seconds ago after running *ran* seconds."""
    _idle(stack)
    end = int(time.time()) - age
    qa.write_exit(stack, code=code, start=end - ran, end=end)
    qa.vanish(stack)
    assert stack.sess.status == Status.GONE, "precondition"
    return _end_notice(stack)


# ---- no usable record: today's text ------------------------------------------------------

def test_no_exit_record_reads_as_today(stack):
    _idle(stack)
    qa.vanish(stack)
    assert _end_notice(stack) == TODAY


def test_a_stale_exit_record_reads_as_today(stack):
    assert _ended(stack, code=1, ran=60, age=121) == TODAY


def test_an_exit_record_just_inside_the_freshness_window_counts(stack):
    assert "exited with error code 2" in _ended(stack, code=2, ran=60, age=110)


def test_a_symlinked_exit_record_is_ignored(stack, tmp_path):
    _idle(stack)
    real = tmp_path / "planted.json"
    end = int(time.time())
    real.write_text('{"code": 2, "start": %d, "end": %d}' % (end - 60, end))
    path = bg_jobs.exit_path(stack.ids.NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(real, path)
    qa.vanish(stack)
    assert _end_notice(stack) == TODAY


@pytest.mark.parametrize("body", ["", "{", "[]", '{"code": "x", "start": 1, "end": 2}',
                                  '{"start": 1, "end": 2}'],
                         ids=["empty", "truncated", "list", "code-text", "no-code"])
def test_a_malformed_exit_record_reads_as_today(stack, body):
    _idle(stack)
    path = bg_jobs.exit_path(stack.ids.NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    qa.vanish(stack)
    assert _end_notice(stack) == TODAY


# ---- the code ---------------------------------------------------------------------------------

def test_a_start_failure_names_the_code(stack):
    assert _ended(stack, code=1, ran=1) == (
        "🔴 <b>frontend</b> · Claude Code stopped right after starting (exit code 1)")


def test_ten_seconds_is_still_a_start_failure(stack):
    assert "stopped right after starting (exit code 3)" in _ended(stack, code=3, ran=10)


def test_eleven_seconds_is_an_error_exit(stack):
    assert _ended(stack, code=3, ran=11) == (
        "🔴 <b>frontend</b> · Session exited with error code 3")


def test_a_later_non_zero_exit_names_the_code(stack):
    assert _ended(stack, code=2, ran=600) == (
        "🔴 <b>frontend</b> · Session exited with error code 2")


@pytest.mark.parametrize("code, name", [(129, "SIGHUP"), (137, "SIGKILL"),
                                        (143, "SIGTERM")])
def test_a_signal_death_names_the_signal(stack, code, name):
    assert _ended(stack, code=code, ran=600) == (
        f"🔴 <b>frontend</b> · Session killed by signal {name}")


@pytest.mark.parametrize("code", [128, 255])
def test_a_code_that_is_no_signal_is_an_error_exit(stack, code):
    assert _ended(stack, code=code, ran=600) == (
        f"🔴 <b>frontend</b> · Session exited with error code {code}")


def test_a_clean_exit_is_not_a_crash(stack):
    text = _ended(stack, code=0, ran=600)
    assert ("crashed" not in text, "error code" not in text, "exited" in text) == (
        True, True, True)


def test_the_log_names_the_exit_code(stack, caplog):
    caplog.set_level(logging.DEBUG)
    _ended(stack, code=42, ran=600)
    assert any("42" in r.getMessage() and stack.ids.NAME in r.getMessage()
               for r in caplog.records)


# ---- a start failure because a background job holds the conversation --------------------------

def _held(stack, status="idle"):
    ids = stack.ids
    stack.sess.claude_session_id = ids.JS
    stack.claude.transcript(ids.JS, {"type": "user"})
    stack.claude.unreg(ids.CLIENT)
    stack.claude.reg(ids.WORKER, kind="bg", jobId=ids.JOB, sessionId=ids.JS,
                     status=status)
    stack.claude.stop_ends[ids.JOB] = ids.WORKER


def test_a_start_failure_with_a_holder_says_so(stack):
    _held(stack)
    assert _ended(stack, code=1, ran=1) == (
        "🔴 <b>frontend</b> · Claude Code stopped right after starting because its "
        "conversation is running in Claude Code's background")


def test_a_start_failure_with_a_holder_offers_bring_it_back(stack):
    _held(stack)
    _ended(stack, code=1, ran=1)
    call = qa.calls_with_text(stack, "stopped right after starting")[0]
    assert "bgback" in qa.buttons(call)


def test_a_start_failure_without_a_holder_offers_no_bring_it_back(stack):
    _ended(stack, code=1, ran=1)
    call = qa.calls_with_text(stack, "stopped right after starting")[0]
    assert "bgback" not in qa.buttons(call)


def test_a_later_exit_with_a_holder_is_not_blamed_on_the_holder(stack):
    _held(stack)
    assert _ended(stack, code=1, ran=600) == (
        "🔴 <b>frontend</b> · Session exited with error code 1")


def test_bring_it_back_on_the_end_notice_of_a_busy_holder_asks(stack):
    _held(stack, "busy")
    _ended(stack, code=1, ran=1)
    from unittest.mock import AsyncMock, MagicMock

    from aipager.bot import session_parity
    q = MagicMock()
    q.from_user = MagicMock(id=12345)
    q.message = MagicMock(chat=MagicMock(id=stack.ids.CHAT), chat_id=stack.ids.CHAT,
                          message_id=77)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock(callback_query=q, effective_chat=MagicMock(id=stack.ids.CHAT),
                  effective_user=MagicMock(id=12345))
    stack.run(session_parity.handle_callback(stack.bot, u, q, stack.ids.NAME, "bgback"))
    assert (qa.stops(stack), stack.launches()) == ([], [])
