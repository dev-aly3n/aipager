"""Lines aipager builds in code from rendered pieces (a status line that
ends in a clock, a question row with aipager's own label) end right to
left in a right-to-left chat, like every pattern line, so Telegram for
iPhone puts them on the right; lines of Claude's own text get no mark."""

from __future__ import annotations

import time

from aipager import i18n
from aipager.bot import animation
from aipager.state import Status, TrackedSession
from tests import bidi_uba

RLM = "\u200f"


def _sess(**fields):
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 250
    for key, value in fields.items():
        setattr(sess, key, value)
    return sess


def test_the_waiting_status_line_ends_right_to_left():
    """The card's last line while background agents run: it ends with
    the clock, which alone would sit the line on the left on iPhone."""
    sess = _sess()
    sess.active_subagents = {"a1": {"type": "Explore", "started_at": time.monotonic() - 5}}
    for code in ("fa", "ar"):
        line = animation._status_line(sess, "Working", waiting=True, tr=i18n.get(code))
        assert line.endswith(RLM) and RLM * 2 not in line, ascii(line)
        assert bidi_uba.sits_right_on_iphone(bidi_uba.shown_text(line)), ascii(line)
    english = animation._status_line(sess, "Working", waiting=True, tr=i18n.get("en"))
    assert RLM not in english and english.endswith(i18n.get("en").clock(250))


def test_the_cards_question_rows_mark_only_aipagers_own_label(mk_bot):
    """A question drawn into the busy card: a row whose label aipager
    wrote (Claude gave none) ends right to left; Claude's rows, and its
    question, get no mark."""
    bot = mk_bot()
    sess = _sess(pending_permission={
        "ask_question": True, "question": "Which one?",
        "options": [{"label": "Postgres", "description": "the usual"},
                    {"description": "x"}]})
    for code in ("fa", "ar"):
        text = bot._build_busy_text("x1", "Working", sess, tr=i18n.get(code))
        rows = text.split("\n")
        (claude,) = [r for r in rows if "Postgres" in r]
        (ours,) = [r for r in rows if r.endswith(" - x" + RLM)]
        assert RLM not in claude, ascii(claude)
        assert ours.startswith("  " + RLM) and RLM * 2 not in ours, ascii(ours)
        assert bidi_uba.sits_right_on_iphone(ours), ascii(ours)
        assert RLM not in [r for r in rows if "Which one?" in r][0]
    english = bot._build_busy_text("x1", "Working", sess, tr=i18n.get("en"))
    assert RLM not in english and "  2. Option 2 - x" in english


def test_a_mode_switch_warning_has_its_marks_at_its_ends():
    """The unconfirmed-switch warning nests a rendered sentence in the
    warning line: the sentence's own marks go, the line's stay."""
    from aipager.bot import mode_switch
    from aipager.state import ModeSwitch
    sess = _sess()
    pending = ModeSwitch(target="auto", presses=1, sent_at=time.monotonic())
    for outcome in ("unconfirmed", "auto_unavailable"):
        html, _plain = mode_switch._notes(i18n.get("fa"), sess, pending, outcome)
        assert html.startswith(RLM + "\u26a0\ufe0f ") and html.endswith(RLM), ascii(html)
        assert html[len(RLM + "\u26a0\ufe0f "):][0] != RLM, ascii(html)
        assert RLM * 2 not in html
        english, _plain = mode_switch._notes(i18n.get("en"), sess, pending, outcome)
        assert RLM not in english
