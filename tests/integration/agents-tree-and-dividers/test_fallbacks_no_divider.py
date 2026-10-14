"""No aipager ``---`` line reaches a plain-text or HTML send (design.md
success criterion 12; entrypoints.md "Fallback texts")."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences as prefs
from aipager.bot.divider import DIVIDER_SEP, PLAIN_DIVIDER
from aipager.bot.flood import MUTE
from aipager.bot.held import HELD
from aipager.bot.rich_message import RichMessageFallbackRequired
from aipager.state import Status, TrackedSession
from tests import answer_status_bb as bb

A1 = "a505c081ca3926ed5"
A2 = "ac1d54c85d78f59ea"
PLAIN_METHODS = ("send_message", "edit_message_text")
SECOND = "Here is the second answer, after the agents woke the session."


def _agents(sess):
    now = time.monotonic()
    sess.bg_agent_started(A1, "pipeline-runner", now)
    sess.bg_agent_started(A2, "ship-tester", now)
    sess.agent_launched_child(A1, A2, now)


def _plain_texts(wire):
    out = []
    for m, p in wire.calls:
        if m in PLAIN_METHODS:
            args = p.get("_args", ())
            out.append(p.get("text") or (args[1] if len(args) > 1 else args[0] if args else ""))
    return out


def _has_divider_line(text):
    return "---" in str(text).split("\n")


@pytest.fixture
def rich_refused(wire, monkeypatch):
    """Every rich call is refused with a 400, so the plain path runs."""
    async def _post(method, payload, **kw):
        wire.calls.append((method, dict(payload, _kw=kw)))
        return {"ok": False, "error_code": 400, "description": "can't parse"}
    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return wire


# ---- the answer's plain fallback ----------------------------------------------------------

def test_the_plain_answer_has_no_divider_line(bot, sess, rich_refused, run_async):
    _agents(sess)
    bb.finish(run_async, bot, sess)
    texts = [t for t in _plain_texts(rich_refused) if bb.ANSWER in t]
    assert texts and not any(_has_divider_line(t) for t in texts)


def test_the_plain_answer_has_no_block(bot, sess, rich_refused, run_async):
    _agents(sess)
    bb.finish(run_async, bot, sess)
    texts = [t for t in _plain_texts(rich_refused) if bb.ANSWER in t]
    assert texts and not any("agents working" in t for t in texts)


def test_the_plain_answer_is_lead_and_answer(bot, sess, rich_refused, run_async):
    _agents(sess)
    bb.finish(run_async, bot, sess)
    texts = [t for t in _plain_texts(rich_refused) if bb.ANSWER in t]
    lead, _, body = texts[0].partition("\n\n")
    # "\U0001f4ac <label>" or the stats lead (entrypoints.md "Fallback texts").
    assert (len(texts), lead.split(" \u00b7 ")[0], body) == (1, "\U0001f4ac bbx", bb.ANSWER)


def test_a_later_answers_plain_fallback_has_no_divider(bot, sess, wire, run_async, monkeypatch):
    _agents(sess)
    bb.finish(run_async, bot, sess)

    async def _post(method, payload, **kw):
        wire.calls.append((method, dict(payload, _kw=kw)))
        return {"ok": False, "error_code": 400, "description": "can't parse"}
    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    bb.finish(run_async, bot, sess, SECOND)
    texts = [t for t in _plain_texts(wire) if SECOND in t]
    assert texts and not any(_has_divider_line(t) or "agents working" in t for t in texts)


def test_no_plain_send_in_a_refused_run_carries_a_divider(bot, sess, rich_refused, run_async):
    _agents(sess)
    bb.finish(run_async, bot, sess)
    assert [t for t in _plain_texts(rich_refused) if _has_divider_line(t)] == []


# ---- held answers (a mute) ----------------------------------------------------------------

def _held(bot, sess, run_async, text=bb.ANSWER):
    MUTE.mute(bb.CHAT, 600)
    bb.finish(run_async, bot, sess, text)
    entries = [e for e in HELD.pending(bb.CHAT) if text in e.rich_text]
    assert entries, "the answer was not held"
    return entries[0]


def test_a_held_answer_rich_copy_has_no_divider(bot, sess, wire, run_async):
    _agents(sess)
    assert not _has_divider_line(_held(bot, sess, run_async).rich_text)


def test_a_held_answer_rich_copy_has_no_block(bot, sess, wire, run_async):
    _agents(sess)
    assert "agents working" not in _held(bot, sess, run_async).rich_text


def test_a_held_answer_plain_copy_has_no_divider(bot, sess, wire, run_async):
    _agents(sess)
    assert not _has_divider_line(_held(bot, sess, run_async).plain_text)


def test_a_held_merged_answer_rich_copy_keeps_its_dividers(bot, sess, wire, run_async):
    prefs.set_preference(bb.CHAT, "layout", "merged")
    _agents(sess)
    assert DIVIDER_SEP in _held(bot, sess, run_async).rich_text


def test_a_held_merged_answer_plain_copy_has_no_divider_line(bot, sess, wire, run_async):
    prefs.set_preference(bb.CHAT, "layout", "merged")
    _agents(sess)
    assert not _has_divider_line(_held(bot, sess, run_async).plain_text)


def test_a_held_merged_answer_plain_copy_shows_the_dash_row(bot, sess, wire, run_async):
    prefs.set_preference(bb.CHAT, "layout", "merged")
    _agents(sess)
    assert PLAIN_DIVIDER in _held(bot, sess, run_async).plain_text


def test_a_held_merged_plain_copy_swaps_every_divider(bot, sess, wire, run_async):
    prefs.set_preference(bb.CHAT, "layout", "merged")
    _agents(sess)
    e = _held(bot, sess, run_async)
    assert e.plain_text.count(PLAIN_DIVIDER) == e.rich_text.split("\n").count("---")


# ---- the busy card's plain degrade -----------------------------------------------------------

def _card_sess():
    s = TrackedSession(name="claude-frontend", label="frontend", status=Status.BUSY)
    s.busy_msg_id = 10
    s.busy_started_at = time.monotonic() - 5
    s.stream_last_rendered = ""
    rows = []
    commentary = []
    for n in (3, 4):
        commentary.append((len(rows), f"Phase of {n} rows."))
        rows += [(f"Bash: n{n}-{i}", True) for i in range(n)]
    commentary.append((len(rows), "Newest phase."))
    rows.append(("Bash: newest", True))
    s.tool_history = rows
    s.stream_commentary = commentary
    return s


@pytest.fixture
def degraded(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        AsyncMock(side_effect=RichMessageFallbackRequired("transport changed")))
    bot._app.bot.edit_message_text = AsyncMock()
    run_async(bot._edit_busy_rich(_card_sess(), "Working"))
    return bot._app.bot.edit_message_text.await_args.args[0]


def test_the_card_plain_degrade_has_no_divider_line(degraded):
    assert not _has_divider_line(degraded)


def test_the_card_plain_degrade_status_follows_one_blank_line(degraded):
    head, _, status = degraded.rpartition("\n\n")
    assert (status.startswith("\u23f3 **frontend**") or status.startswith("\u23f3 frontend"),
            head.endswith("\n"), head.strip() != "") == (True, False, True)


def test_the_card_plain_degrade_has_no_triple_newline(degraded):
    assert "\n\n\n" not in degraded


def test_the_card_plain_degrade_keeps_the_summaries(degraded):
    assert "\u25b8 3 tool calls" in degraded and "\u25b8 4 tool calls" in degraded


# ---- HTML cards --------------------------------------------------------------------------------

def test_the_legacy_html_card_has_no_divider(mk_bot):
    bot = mk_bot()
    s = _card_sess()
    s.tool_history.append(("\U0001f916 pipeline-runner \u203a ship-tester", False))
    text = bot._build_busy_text("frontend", "Working", s)
    assert (_has_divider_line(text), "<hr" in text) == (False, False)


def test_the_compacting_card_has_no_divider(mk_bot, run_async):
    bot = mk_bot()
    s = _card_sess()
    s.scope_kind = "dm"
    s.scope_chat_id = 12345
    s.busy_msg_id = 42
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock())
    run_async(bot.notify(s, "compacting", {"trigger": "auto"}))
    text = bot._app.bot.edit_message_text.await_args.args[0] \
        if bot._app.bot.edit_message_text.await_args.args else \
        bot._app.bot.edit_message_text.await_args.kwargs.get("text", "")
    assert (_has_divider_line(text), "<hr" in text) == (False, False)
