"""Roadmap 8.145: a turn ends in an API error only when Claude Code says so.

Reported 2026-10-27: normal answers that merely MENTIONED an error ("the
build server was overloaded", "returns 500 Internal Server Error", "a
connection reset", "the usage limit reached warning") were swallowed and the
chat showed "Anthropic's servers are overloaded" with a Retry button: the
finish path ran the error patterns over the whole answer. Claude Code's own
signals (2.1.311, live): the StopFailure hook ("error": "server_error",
"last_assistant_message": "API Error: 529 Overloaded. ..."), a transcript
record with ``isApiErrorMessage``, and its "API Error: NNN" wording at the
start of the message. The patterns now only choose the notice's words.
"""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import transcript
from aipager.bot import transport
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status, TrackedSession

NAME = "claude-jim"
WORD_ANSWERS = [
    "Done. The build server was overloaded this morning, so the first try timed out",
    "Fixed: the handler now returns 500 Internal Server Error instead of crashing",
    "The tests pass now. The flaky one was a connection reset in the mock",
    "The monthly limit is per seat and the usage limit reached warning shows at 80%",
    "The client maps api_error and overloaded_error to retries",
    "Payment required screens now link to billing; a network error shows a retry",
    "Waiting on the rate_limit_error branch: see the HTTP 429 handler",
    "We log every API Error: 500 we see, as before",
]


def _record(text: str, *, api_error: bool) -> dict:
    entry = {"type": "assistant", "timestamp": "2099-01-01T00:00:00.000Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if api_error:
        entry["isApiErrorMessage"] = True
        entry["message"]["model"] = "<synthetic>"
    return entry


def _transcript(tmp_path, *entries) -> str:
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return str(path)


# ── the transcript reader ────────────────────────────────────────────────

def test_an_error_record_is_flagged(tmp_path):
    tp = _transcript(tmp_path, _record("API Error: 529 Overloaded.", api_error=True))
    assert transcript.extract_last_response_flagged(tp) == ("API Error: 529 Overloaded.", True)


@pytest.mark.parametrize("text", WORD_ANSWERS + ["API Error: 529 Overloaded."])
def test_a_normal_record_is_not_flagged_whatever_it_says(tmp_path, text):
    tp = _transcript(tmp_path, _record(text, api_error=False))
    assert transcript.extract_last_response_flagged(tp) == (text, False)


def test_an_error_record_older_than_the_turn_is_not_this_turns(tmp_path):
    old = _record("API Error: 500", api_error=True)
    old["timestamp"] = "2001-01-01T00:00:00.000Z"
    tp = _transcript(tmp_path, old)
    assert transcript.extract_last_response_flagged(tp, since=time.time()) == ("", False)


def test_a_missing_transcript_is_not_an_error(tmp_path):
    assert transcript.extract_last_response_flagged(str(tmp_path / "none")) == (None, False)


# ── the hook receiver ────────────────────────────────────────────────────

@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    registry.transition(NAME, Status.BUSY)
    return registry, recv, notify_fn


def _send(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps({"session": NAME, **fields}).encode()))


def _ctx(notify_fn):
    _sess, event, ctx = notify_fn.await_args.args
    assert event == "idle_prompt"
    return ctx


@pytest.mark.parametrize("message", ["API Error: 529 Overloaded. Try again.",
                                     "API Error: 401 Invalid bearer token",
                                     "  API Error: 500 Internal server error"])
def test_a_stop_whose_message_starts_with_claude_codes_error_wording_is_flagged(
        receiver, run_async, message):
    _registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="Stop", last_assistant_message=message)
    assert _ctx(notify_fn)["api_error"] == "message_start"


@pytest.mark.parametrize("message", WORD_ANSWERS + ["API Error: abc", "API Error 529"])
def test_a_stop_whose_answer_only_mentions_an_error_is_not_flagged(receiver, run_async,
                                                                   message):
    _registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="Stop", last_assistant_message=message)
    assert "api_error" not in _ctx(notify_fn)


def test_a_stop_read_from_an_error_record_is_flagged(receiver, run_async, tmp_path):
    _registry, recv, notify_fn = receiver
    tp = _transcript(tmp_path, _record("Credit balance is too low", api_error=True))
    _send(recv, run_async, hook_event_name="Stop", transcript_path=tp)
    ctx = _ctx(notify_fn)
    assert ctx["api_error"] == "transcript"
    assert ctx["summary"] == "Credit balance is too low"


def test_a_stop_read_from_a_normal_record_is_not_flagged(receiver, run_async, tmp_path):
    _registry, recv, notify_fn = receiver
    tp = _transcript(tmp_path, _record(WORD_ANSWERS[0], api_error=False))
    _send(recv, run_async, hook_event_name="Stop", transcript_path=tp)
    assert "api_error" not in _ctx(notify_fn)


def test_a_stop_failure_is_flagged_and_says_its_own_message(receiver, run_async, tmp_path):
    """2.1.311's StopFailure carries the error as last_assistant_message:
    that is the notice's source, before the transcript."""
    _registry, recv, notify_fn = receiver
    tp = _transcript(tmp_path, _record("older text", api_error=False))
    _send(recv, run_async, hook_event_name="StopFailure", error="server_error",
          transcript_path=tp, last_assistant_message="API Error: 529 Overloaded.")
    ctx = _ctx(notify_fn)
    assert ctx["api_error"] == "stop_failure"
    assert ctx["summary"] == "API Error: 529 Overloaded."


def test_a_stop_failure_without_a_message_reads_the_transcript(receiver, run_async,
                                                               tmp_path):
    _registry, recv, notify_fn = receiver
    tp = _transcript(tmp_path, _record("API Error: 500", api_error=True))
    _send(recv, run_async, hook_event_name="StopFailure", transcript_path=tp)
    ctx = _ctx(notify_fn)
    assert ctx["api_error"] == "stop_failure"
    assert ctx["summary"] == "API Error: 500"


def test_a_stop_failure_with_nothing_to_say_is_still_flagged(receiver, run_async):
    _registry, recv, notify_fn = receiver
    _send(recv, run_async, hook_event_name="StopFailure")
    ctx = _ctx(notify_fn)
    assert ctx["api_error"] == "stop_failure"
    assert ctx["summary"] == ""


@pytest.mark.parametrize("api_error,flag", [(False, None), (True, "transcript")])
def test_a_background_jobs_end_is_flagged_only_by_an_error_record(run_async, tmp_path,
                                                                  monkeypatch, api_error,
                                                                  flag):
    """The linked job's SessionEnd ends the turn through the same path as a
    StopFailure, but is no API error by itself."""
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    recv = hr.HookReceiver(registry, notify_fn)
    sess = registry.get_or_create(NAME)
    registry.transition(NAME, Status.BUSY)
    tp = _transcript(tmp_path, _record("API Error: 500 Internal server error",
                                       api_error=api_error))
    run_async(recv._finalize_failed_turn(NAME, tp, sess))
    assert _ctx(notify_fn).get("api_error") == flag


# ── the session monitor's missed-Stop recovery ──────────────────────────

@pytest.mark.parametrize("api_error", [False, True])
def test_the_monitors_recovery_flags_only_an_error_record(monkeypatch, run_async, tmp_path,
                                                          api_error):
    import os

    from aipager.session_monitor import IDLE_RECOVERY_GRACE, SessionMonitor
    entries = [{"type": "user", "message": {"role": "user", "content": "hello"}},
               {**_record("API Error: 529 Overloaded.", api_error=api_error),
                "timestamp": None}]
    entries[1]["message"]["stop_reason"] = "end_turn"
    path = tmp_path / "rec.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    old = time.time() - (IDLE_RECOVERY_GRACE + 5)
    os.utime(path, (old, old))
    sess = TrackedSession(name=NAME, label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - (IDLE_RECOVERY_GRACE + 20)
    sess.busy_started_wall = time.time() - (IDLE_RECOVERY_GRACE + 20)
    sess.transcript_path = str(path)
    registry = SessionRegistry()
    registry._sessions[NAME] = sess
    calls = []

    async def _notify(_s, event, ctx):
        calls.append((event, ctx))

    async def _alive():
        return [NAME]
    monitor = SessionMonitor(registry, _notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _alive)
    run_async(monitor._scan())
    assert calls and calls[-1][0] == "idle_prompt"
    assert calls[-1][1].get("api_error") == ("transcript" if api_error else None)


# ── the helper notify uses ───────────────────────────────────────────────

@pytest.mark.parametrize("text", WORD_ANSWERS)
def test_an_unflagged_answer_is_never_an_error(text):
    assert transport._turn_api_error({"summary": text}) is None


@pytest.mark.parametrize("signal", ["stop_failure", "transcript", "message_start"])
def test_a_flagged_turn_gets_the_notice(signal):
    found = transport._turn_api_error({"summary": "API Error: 529 Overloaded.",
                                       "api_error": signal})
    assert found and "overloaded" in found[0].lower()


def test_a_flagged_turn_with_unknown_words_is_delivered_as_it_is():
    assert transport._turn_api_error({"summary": "Prompt is too long",
                                      "api_error": "stop_failure"}) is None


def test_an_answer_that_looks_like_an_error_asks_the_transcript_once(tmp_path, monkeypatch):
    sess = TrackedSession(name=NAME, label="jim", status=Status.IDLE)
    sess.transcript_path = _transcript(tmp_path, _record(WORD_ANSWERS[0], api_error=False))
    reads = []
    real = transcript.extract_last_response_flagged

    def counting(path, **kw):
        reads.append(path)
        return real(path, **kw)
    monkeypatch.setattr(transcript, "extract_last_response_flagged", counting)
    context = {"summary": WORD_ANSWERS[0]}
    assert transport._turn_api_error(context, sess=sess) is None
    assert transport._turn_api_error(context, sess=sess) is None
    assert len(reads) == 1


def test_an_unflagged_error_confirmed_by_the_transcript_gets_the_notice(tmp_path):
    """A Stop that carries Claude Code's error in other words (no StopFailure,
    no "API Error: NNN" start): its error record says so."""
    sess = TrackedSession(name=NAME, label="jim", status=Status.IDLE)
    sess.transcript_path = _transcript(
        tmp_path, _record("Claude AI usage limit reached|1700000000", api_error=True))
    context = {"summary": "Claude AI usage limit reached|1700000000"}
    assert transport._turn_api_error(context, sess=sess) is not None
    assert context["api_error"] == "transcript"


def test_an_answer_with_no_error_words_never_reads_the_transcript(monkeypatch):
    sess = TrackedSession(name=NAME, label="jim", status=Status.IDLE)
    sess.transcript_path = "/nonexistent"

    def boom(*_a, **_k):
        raise AssertionError("read the transcript for a plain answer")
    monkeypatch.setattr(transcript, "extract_last_response_flagged", boom)
    assert transport._turn_api_error({"summary": "All done."}, sess=sess) is None


# ── what reaches the chat ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _rich(monkeypatch):
    mock = AsyncMock(return_value={})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", mock)
    return mock


def _idle_bot(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name=NAME, label="jim", status=Status.IDLE)
    sess.busy_started_at = time.monotonic()
    sess.scope_chat_id = 4242
    sess.last_prompt = "do it"
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    return bot, sess


def _notices(bot):
    return [c.args[1] for c in bot._app.bot.send_message.await_args_list
            if "⚠️" in (c.args[1] if len(c.args) > 1 else "")]


@pytest.mark.parametrize("text", WORD_ANSWERS)
def test_an_answer_that_mentions_an_error_reaches_the_chat(mk_bot, run_async, _rich, text):
    bot, sess = _idle_bot(mk_bot)
    run_async(bot.notify(sess, "idle_prompt", {"summary": text}))
    assert _notices(bot) == []
    _rich.assert_awaited_once()
    assert text in _rich.await_args.args[1]


def test_a_flagged_turn_gets_the_notice_and_retry_and_says_so_in_the_log(
        mk_bot, run_async, _rich, caplog):
    bot, sess = _idle_bot(mk_bot)
    with caplog.at_level(logging.WARNING, logger="aipager.bot.notify"):
        run_async(bot.notify(sess, "idle_prompt", {"summary": "API Error: 529 Overloaded.",
                                                   "api_error": "stop_failure"}))
    [notice] = _notices(bot)
    assert "overloaded" in notice.lower()
    assert bot._app.bot.send_message.await_args.kwargs.get("reply_markup") is not None
    _rich.assert_not_awaited()
    lines = [r.getMessage() for r in caplog.records if "API error" in r.getMessage()]
    assert lines and "stop_failure" in lines[0] and "API Error: 529" in lines[0]


def test_a_flagged_turn_leaves_the_queue_and_an_answer_takes_from_it(mk_bot, run_async):
    for context, taken in (({"summary": "API Error: 529 Overloaded.",
                             "api_error": "stop_failure"}, False),
                           ({"summary": WORD_ANSWERS[0]}, True)):
        bot, sess = _idle_bot(mk_bot)
        bot._take_next_prompt = MagicMock(return_value=None)
        run_async(bot.notify(sess, "idle_prompt", context))
        assert bot._take_next_prompt.called is taken, context


def test_an_earlier_turns_error_record_is_not_this_answers(tmp_path):
    """The transcript is asked about THIS turn only (its start): an error
    record from before it says nothing about the answer being judged."""
    sess = TrackedSession(name=NAME, label="jim", status=Status.IDLE)
    old = _record("Claude AI usage limit reached|1700000000", api_error=True)
    old["timestamp"] = "2001-01-01T00:00:00.000Z"
    sess.transcript_path = _transcript(tmp_path, old)
    sess.turn_entered_wall = time.time()
    context = {"summary": "Claude AI usage limit reached|1700000000"}
    assert transport._turn_api_error(context, sess=sess) is None
    assert "api_error" not in context


def test_an_error_record_with_other_words_is_not_this_answers(tmp_path):
    sess = TrackedSession(name=NAME, label="jim", status=Status.IDLE)
    sess.transcript_path = _transcript(tmp_path, _record("API Error: 500", api_error=True))
    context = {"summary": WORD_ANSWERS[0]}
    assert transport._turn_api_error(context, sess=sess) is None


def test_a_stop_failure_whose_rich_summary_fails_still_ends_the_turn(receiver, run_async,
                                                                     monkeypatch):
    _registry, recv, notify_fn = receiver
    monkeypatch.setattr(hr, "RICH_SUMMARIES", True)

    def broken(_md):
        raise ValueError("bad markdown")
    monkeypatch.setattr(hr, "markdown_to_telegram_html", broken)
    message = "API Error: 400 bad request\n```\njson\n```"
    _send(recv, run_async, hook_event_name="StopFailure", last_assistant_message=message)
    ctx = _ctx(notify_fn)
    assert ctx["api_error"] == "stop_failure"
    assert ctx["summary"] == message
