"""The divider above the busy card's status line (agents tree, 2026-10-14):
every rich render with a timeline ends ``\\n\\n---\\n\\n<status>``, the
fitter keeps room for it so the card stays inside Telegram's limits, a
card with no timeline is its status line alone, and no plain-text or HTML
form ever carries a ``---`` line of aipager's."""

from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock

import pytest

from aipager.bot import animation, notify
from aipager.bot.animation import (
    _CARD_CHAR_BUDGET,
    _RICH_LIMIT,
    FINAL_VERB,
    build_stream_card,
    build_stream_card_ex,
)
from aipager.bot.divider import DIVIDER_SEP, PLAIN_DIVIDER
from aipager.bot.rich_message import RichMessageFallbackRequired, RichMessageFloodBanned
from aipager.state import Status, TrackedSession
from tests.footer_text import as_md


def _sess(rows=(("Bash: ls", True),), label="jim"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    sess.tool_history = list(rows)
    return sess


def _divided(card: str) -> tuple[str, str]:
    """The card's body, and its status line under the divider read back
    as Markdown (it is a footer block, small text: :func:`as_md` fails
    unless it is one)."""
    body, sep, status = card.rpartition(DIVIDER_SEP)
    assert sep, card
    return body, as_md(status)


@pytest.mark.parametrize("final", [False, True], ids=["live", "final"])
def test_a_card_with_a_timeline_has_the_divider_above_its_status(final):
    card = build_stream_card(_sess(), FINAL_VERB if final else "Working", final=final)
    body, status = _divided(card)
    assert body == "✅ `Bash: ls`"
    assert "\n" not in status and "---" not in status
    assert card.count("---") == 1


def test_the_waiting_card_has_it_too():
    sess = _sess()
    sess.active_subagents = {"a1": {"type": "Explore", "started_at": time.monotonic() - 5,
                                    "history_idx": None}}
    _body, status = _divided(build_stream_card(sess, "Working", waiting=True))
    assert status.startswith("🔄 ")


def test_a_card_with_no_timeline_is_its_status_alone():
    card = build_stream_card(_sess(rows=()), "Working")
    assert "---" not in card
    assert "\n" not in card


def test_a_peer_line_stays_on_top():
    sess = _sess()
    sess.peer_from = "frontend"
    card = build_stream_card(sess, "Working")
    _body, status = _divided(card)
    assert card.count("---") == 1
    assert not card.startswith("---")


@pytest.mark.parametrize("final", [False, True], ids=["live", "final"])
@pytest.mark.parametrize("shape", ["many-rows", "one-long-row", "prose"])
def test_the_card_stays_inside_the_char_budget_at_its_edge(final, shape):
    if shape == "many-rows":
        sess = _sess(rows=[(f"Bash: t-{i} " + "x" * 40, True) for i in range(400)])
    elif shape == "one-long-row":
        sess = _sess(rows=[("Bash: " + "x" * 20000, True)])
    else:
        sess = _sess(rows=())
        sess.stream_commentary = [(0, "y " * 10000)]
    card, _dropped = build_stream_card_ex(sess, FINAL_VERB if final else "Working",
                                          final=final)
    _divided(card)
    # Filled to the edge, and not one character past it.
    assert len(card) == _CARD_CHAR_BUDGET


def test_the_card_stays_inside_the_byte_limit_at_its_edge():
    sess = _sess(rows=[("Bash: " + "\U0001f600" * 9000, True)])
    card, _dropped = build_stream_card_ex(sess, "Working")
    _divided(card)
    assert len(card.encode("utf-8")) <= _RICH_LIMIT
    assert len(card) <= _CARD_CHAR_BUDGET


def test_the_plain_degrade_drops_the_divider(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _sess(rows=[("Bash: ls", True), ("Read: /a.py", True)])
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        AsyncMock(side_effect=RichMessageFallbackRequired("transport")))
    bot._app.bot.edit_message_text = AsyncMock()
    assert run_async(bot._edit_busy_rich(sess, "Working")) is True
    text = bot._app.bot.edit_message_text.await_args.args[0]
    assert "---" not in text
    # Nor a footer's tags: the status line's text alone.
    assert "<footer>" not in text and "<b>" not in text
    # The status line still follows the timeline after one blank line.
    assert text.startswith("✅ `Bash: ls`\n\n✅ `Read: /a.py`\n\n⏳ ")


def test_the_legacy_html_card_has_no_divider(mk_bot):
    bot = mk_bot()
    sess = _sess(rows=[("Bash: ls", True), ("Read: /a.py", False)])
    sess.pending_permission = {"tool_summary": "Bash: rm -rf build/",
                               "tool_info": {"name": "Bash", "detail": "rm -rf build/"}}
    text = bot._build_busy_text(sess.label, "Waiting", sess)
    assert "---" not in text and "<hr" not in text


# ---- the merged layout ------------------------------------------------------------------

def _merged_sess(bot):
    sess = _sess()
    sess.scope_chat_id = 555
    sess.scope_kind = "dm"
    bot.registry._sessions[sess.name] = sess
    return sess


def test_the_merged_message_has_two_dividers_and_no_dash_row(mk_bot, run_async,
                                                            monkeypatch):
    bot = mk_bot()
    sess = _merged_sess(bot)
    edit = AsyncMock(return_value={"message_id": 10})
    monkeypatch.setattr(notify, "edit_message_text_rich", edit)
    assert run_async(bot._send_merged_final(sess, "the answer")) is True
    text = edit.await_args.args[2]
    assert PLAIN_DIVIDER not in text
    parts = text.split(DIVIDER_SEP)
    assert len(parts) == 3
    assert parts[0] == "✅ `Bash: ls`"
    assert as_md(parts[1].rsplit("\n\n", 1)[-1]).startswith("✅ **jim** · Done")
    assert parts[2] == "💬 **jim**\n\nthe answer"


@pytest.mark.parametrize("answer", ["the answer", ""], ids=["answer", "no-answer"])
def test_the_held_merged_plain_copy_shows_a_text_line_for_each_divider(
    mk_bot, run_async, monkeypatch, answer,
):
    bot = mk_bot()
    sess = _merged_sess(bot)
    monkeypatch.setattr(notify, "edit_message_text_rich",
                        AsyncMock(side_effect=RichMessageFloodBanned(555, 60)))
    held = []
    monkeypatch.setattr(bot, "_hold_answer",
                        lambda _s, rich, plain, *_a, **_k: held.append((rich, plain)))
    assert run_async(bot._send_merged_final(sess, answer)) is False
    ((rich, plain),) = held
    assert rich.count(DIVIDER_SEP) == (2 if answer else 1)
    assert "---" not in plain
    # Nor a footer's tags: its text alone (the card's status line), with or
    # without an answer under it.
    assert "<footer>" in rich and "<footer>" not in plain and "<b>" not in plain
    status = re.search(r"<footer>(.*?)</footer>", rich).group(1)
    assert plain == rich.replace(DIVIDER_SEP, f"\n\n{PLAIN_DIVIDER}\n\n").replace(
        f"<footer>{status}</footer>", re.sub(r"</?b>", "", status))


def test_the_merged_separator_is_the_divider():
    assert notify._MERGED_SEPARATOR == "---"
    assert animation._assemble_card("body", "status") == (
        "body\n\n---\n\n<footer>status</footer>")
    assert animation._assemble_card("", "status") == "<footer>status</footer>"


# ---- a plain copy strips only aipager's own footer ----------------------------------------

CLAUDES_FOOTER = "<footer>&copy; Acme &amp; co</footer>"


def test_the_plain_degrade_keeps_claudes_footer_as_written(mk_bot, run_async, monkeypatch):
    """Claude's prose with a literal <footer> (web work) is Claude's: only
    the status line, the footer aipager drew, loses its tags."""
    bot = mk_bot()
    sess = _sess(rows=[("Bash: ls", True), ("Read: /a.py", True)])
    sess.stream_commentary = [(1, f"Use {CLAUDES_FOOTER} here.")]
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        AsyncMock(side_effect=RichMessageFallbackRequired("transport")))
    bot._app.bot.edit_message_text = AsyncMock()
    assert run_async(bot._edit_busy_rich(sess, "Working")) is True
    text = bot._app.bot.edit_message_text.await_args.args[0]
    assert CLAUDES_FOOTER in text
    assert text.rstrip().rsplit("\n\n", 1)[-1].startswith("⏳ jim · ")


def test_the_held_plain_copy_keeps_claudes_footer_as_written(mk_bot, run_async, monkeypatch):
    bot = mk_bot()
    sess = _merged_sess(bot)
    monkeypatch.setattr(notify, "edit_message_text_rich",
                        AsyncMock(side_effect=RichMessageFloodBanned(555, 60)))
    held = []
    monkeypatch.setattr(bot, "_hold_answer",
                        lambda _s, rich, plain, *_a, **_k: held.append((rich, plain)))
    answer = f"Put this at the end:\n\n```html\n{CLAUDES_FOOTER}\n```"
    assert run_async(bot._send_merged_final(sess, answer)) is False
    ((rich, plain),) = held
    assert plain.endswith(answer)                       # the answer, as written
    assert plain.count("<footer>") == 1                 # Claude's alone
    assert rich.count("<footer>") == 2                  # the status line's and Claude's
