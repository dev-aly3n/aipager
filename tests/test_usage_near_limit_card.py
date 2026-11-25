"""The near-limit note on the busy card (roadmap 8.131 follow-up): drawn
right above the divider while a window is at 90% or more and has not
reset, never on the finished card, always inside the card's ceilings, and
stamped by the card's own edit paths only for a private chat whose person
may see plan usage. It is never a frame-state change, so it never edits a
card sooner than the age floor allows."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import i18n, usage_store
from aipager.bot import animation, usage_note
from aipager.bot.animation import (
    _CARD_CHAR_BUDGET,
    _RICH_LIMIT,
    FINAL_VERB,
    build_stream_card,
    build_stream_card_ex,
)
from aipager.bot.divider import DIVIDER_SEP
from aipager.claude_bootstrap import SessionLogin
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession
from aipager.statusline_file import Window
from tests.footer_text import as_md

OWNER, USER, GROUP = 111, 777, -100
ALICE = SessionLogin("saved", "", "/home/alice/.claude")
WEEK_ROW = "⚠️ Weekly limit almost reached: 92% used · Resets in 2 d"
INVISIBLE = "\u200e\u200f\u2066\u2067\u2068\u2069"


@pytest.fixture(autouse=True)
def _gmtime(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)


def _week(pct=92, *, left=2 * 86400 + 3600):
    return {"seven_day": Window(pct, int(time.time()) + left)}


def _sess(rows=(("Bash: ls", True),), *, windows=None, label="frontend"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    sess.tool_history = list(rows)
    sess.card_usage_windows = windows
    return sess


def _plain(text: str) -> str:
    return "".join(ch for ch in text if ch not in INVISIBLE)


# ---- the renderer -------------------------------------------------------------------

def test_busy_card_shows_note_above_divider():
    card = build_stream_card(_sess(windows=_week()), "Working")
    body, sep, status = card.rpartition(DIVIDER_SEP)
    assert sep and card.count(DIVIDER_SEP) == 1
    assert as_md(status).startswith("⏳ **frontend** · Working")
    # The note is the body's last row, right above the divider; the
    # timeline stays above it.
    rows = body.split("\n\n")
    assert rows[0] == "✅ `Bash: ls`"
    assert rows[-1].startswith(WEEK_ROW)


def test_busy_card_without_windows_has_no_note():
    card = build_stream_card(_sess(), "Working")
    assert "limit" not in card


def test_note_alone_when_the_timeline_is_empty():
    card = build_stream_card(_sess(rows=(), windows=_week()), "Working")
    body, sep, _status = card.rpartition(DIVIDER_SEP)
    assert sep and body.startswith(WEEK_ROW)


def test_final_card_has_no_note():
    card = build_stream_card(_sess(windows=_week()), FINAL_VERB, final=True)
    assert "limit" not in card and "Resets" not in card


def test_waiting_card_shows_note():
    sess = _sess(windows=_week())
    sess.active_subagents = {"a1": {"type": "Explore", "started_at": time.monotonic() - 5,
                                    "history_idx": None}}
    card = build_stream_card(sess, "Working", waiting=True)
    body, _sep, status = card.rpartition(DIVIDER_SEP)
    assert as_md(status).startswith("🔄 ")
    assert body.split("\n\n")[-1].startswith(WEEK_ROW)


def test_card_note_drops_after_reset():
    windows = {"seven_day": Window(92, int(time.time()) - 1)}
    card = build_stream_card(_sess(windows=windows), "Working")
    assert "limit" not in card


def test_reached_row_on_the_card():
    card = build_stream_card(_sess(windows={"spend_limit": Window(250, int(time.time())
                                                                  + 600)}), "Working")
    assert "⛔ Spend limit reached: 250% used · Resets in " in card


THREE = ("five_hour", "seven_day", "spend_limit")


# "byte-bound" rows (400 four-byte emoji each) and zh-Hans "byte-heavy"
# rows carry enough bytes per char that the card hits the 32,768-byte
# ceiling before the 8,800-char one: only the note's BYTE reserve keeps
# those inside it (the char reserve alone lets them reach ~33,300 bytes).
@pytest.mark.parametrize("code", ["en", "fa", "zh-Hans"])
@pytest.mark.parametrize("shape", ["many-rows", "byte-heavy", "byte-bound"])
def test_note_reserved_within_ceiling(code, shape):
    tr = i18n.get(code)
    if shape == "many-rows":
        rows = [(f"Bash: t-{i} " + "x" * 40, True) for i in range(400)]
    elif shape == "byte-heavy":
        rows = [("Bash: " + "\U0001f600" * 150, True) for _ in range(400)]
    else:
        rows = [("Bash: " + "\U0001f600" * 400, True) for _ in range(400)]
    windows = {name: Window(95, int(time.time()) + 3 * 3600) for name in THREE}
    sess = _sess(rows=rows, windows=windows)
    card, dropped = build_stream_card_ex(sess, "Working", tr=tr)
    assert dropped
    assert len(card) <= _CARD_CHAR_BUDGET
    assert len(card.encode("utf-8")) <= _RICH_LIMIT
    body, sep, status = card.rpartition(DIVIDER_SEP)
    assert sep
    assert _plain(as_md(status)).startswith("⏳")
    # The three rows, whole, right above the divider.
    tail = body.split("\n\n")[-3:]
    want = usage_note.card_rows(tr.directed(tr.language_rtl), windows, now=time.time())
    assert len(want) == 3
    assert [_plain(r) for r in tail] == [_plain(r) for r in want]


# ---- the edit paths stamp it ----------------------------------------------------------

@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[
        Scope(chat_id=OWNER, kind="dm", label="owner", members=(
            Member(id=OWNER, label="alice", role="owner"),)),
        Scope(chat_id=USER, kind="dm", label="bob", members=(
            Member(id=USER, label="bob", role="user"),)),
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=USER, label="bob", role="user"))),
    ])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    return b


def _live(bot, chat):
    """A busy session in *chat* whose sign-in has a reading at 92%."""
    usage_store.note_plan("frontend", _week(), ALICE)
    sess = _sess()
    sess.scope_chat_id = chat
    sess.scope_kind = "dm" if chat > 0 else "group"
    sess.usage_plan_key = usage_store.plan_key(ALICE)
    bot.registry._sessions[sess.name] = sess
    return sess


def _edited(bot, sess, run_async, monkeypatch, **kw) -> str:
    edit = AsyncMock(return_value={"message_id": sess.busy_msg_id})
    monkeypatch.setattr(animation, "edit_message_text_rich", edit)
    run_async(bot._edit_busy_rich(sess, kw.pop("verb", "Working"), **kw))
    return edit.await_args.args[2]


def test_edit_stamps_windows_for_private_owner_card(bot, run_async, monkeypatch):
    sess = _live(bot, OWNER)
    text = _edited(bot, sess, run_async, monkeypatch)
    assert set(sess.card_usage_windows) == {"seven_day"}
    assert "Weekly limit almost reached: 92% used" in text


def test_edit_stamps_none_for_group_card(bot, run_async, monkeypatch):
    sess = _live(bot, GROUP)
    text = _edited(bot, sess, run_async, monkeypatch)
    assert sess.card_usage_windows is None
    assert "limit" not in text


def test_edit_stamps_none_for_plain_user_card(bot, run_async, monkeypatch):
    sess = _live(bot, USER)
    text = _edited(bot, sess, run_async, monkeypatch)
    assert sess.card_usage_windows is None
    assert "limit" not in text


def test_edit_clears_a_stale_stamp(bot, run_async, monkeypatch):
    """A card once allowed and now not (a role change) loses the note at
    its next frame: the stamp is asked again on every edit."""
    sess = _live(bot, GROUP)
    sess.card_usage_windows = _week()
    text = _edited(bot, sess, run_async, monkeypatch)
    assert sess.card_usage_windows is None and "limit" not in text


def test_final_edit_stamps_none(bot, run_async, monkeypatch):
    sess = _live(bot, OWNER)
    sess.card_usage_windows = _week()
    text = _edited(bot, sess, run_async, monkeypatch, verb=FINAL_VERB, final=True)
    assert sess.card_usage_windows is None
    assert "limit" not in text


def _copied(bot, sess, run_async, monkeypatch, *, final=False) -> str:
    rich = AsyncMock(return_value={"message_id": 56})
    monkeypatch.setattr(animation, "send_rich_message", rich)
    run_async(bot._send_card_copy(sess, 9, verb=FINAL_VERB if final else "Working",
                                  final=final, waiting=False))
    return rich.await_args.args[1]


def test_card_copy_stamps_windows(bot, run_async, monkeypatch):
    sess = _live(bot, OWNER)
    assert "Weekly limit almost reached: 92% used" in _copied(bot, sess, run_async,
                                                             monkeypatch)


def test_card_copy_stamps_none_for_group(bot, run_async, monkeypatch):
    sess = _live(bot, GROUP)
    sess.card_usage_windows = _week()
    assert "limit" not in _copied(bot, sess, run_async, monkeypatch)
    assert sess.card_usage_windows is None


def test_final_card_copy_has_no_note(bot, run_async, monkeypatch):
    sess = _live(bot, OWNER)
    assert "limit" not in _copied(bot, sess, run_async, monkeypatch, final=True)
    assert sess.card_usage_windows is None


# ---- pacing ---------------------------------------------------------------------------

def test_note_change_is_not_a_frame_state_change(bot):
    sess = _live(bot, OWNER)
    sess.card_usage_windows = None
    before = bot._card_frame_state(sess)
    sess.card_usage_windows = _week()
    assert bot._card_frame_state(sess) == before
    # Inside the age floor (a 5-minute turn: 10 s), with the frame state
    # unchanged since the last landed edit, the card is not due.
    now = time.monotonic()
    sess.busy_started_at = now - 300
    sess.last_tool_edit_at = now - 3
    sess.card_frame_state = before
    assert bot._card_edit_due(sess, now, base_gap=0.0) is False
