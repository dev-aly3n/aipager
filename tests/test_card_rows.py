"""Stored card rows drawn in the chat's language (``aipager.bot.card_rows``):
English rows are the stored bytes (G66), tool summaries and unknown shapes
are never translated (G67), the status line's tally reads the stored
English whatever the language (G68), and each shape our code writes is
pinned against its writer."""

from __future__ import annotations

import pytest

from aipager import bg_shells, i18n, preferences
from aipager.bot import animation, card_rows
from aipager.bot.notify import settled_agent_row
from aipager.i18n import locales

RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"


def _rows():
    """One row of every shape, each built by the code that stores it."""
    return [
        settled_agent_row("Explore", 3, 65),
        settled_agent_row("general-purpose", 1, 0.4),
        bg_shells.settled_row("npm test", "completed", 90)[0],
        bg_shells.settled_row("make all", "failed", 5, exit_code=2)[0],
        bg_shells.settled_row("make lint", "failed", 65)[0],
        bg_shells.settled_row("sleep 900", "lost", 900)[0],
        bg_shells.settled_row("tail -f log", "stopped", 30)[0],
        bg_shells.settled_row("a - b - c", "completed", 3725)[0],
        "🔑 Bash: rm -rf build/ → Allowed",
        "🔑 Edit: /a.py → Allowed always",
        "🔑 Bash: git push → Denied",
        "🔑 Bash: x → Continued",
        "❓ Which database? → Selected option 2",
        "❓ Pick some → Selected: A, B",
        "❓ Pick some → Submitted (none)",
    ]


TOOL_ROWS = ["Read: /src/app.py", "Bash: npm test", "Grep: TODO in src", "",
             "🤖 general-purpose", "🤖 x · many tool calls · soon", "shell: x - done (soon)",
             "🔑 Bash: ls → Perhaps", "Agent: shell: npm - done (0:01)"]


@pytest.fixture
def qps(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    return i18n.get("qps")


def test_english_rows_are_the_stored_bytes():
    en = i18n.get("en")
    for summary in _rows() + TOOL_ROWS:
        assert card_rows.render_md(en, summary) == animation._mono(summary)
        assert card_rows.render_text(en, summary) == summary


def test_every_shape_we_write_is_drawn_from_a_key(qps):
    for summary in _rows():
        md = card_rows.render_md(qps, summary)
        assert md != animation._mono(summary), summary
        assert "⟦" in md, summary
        text = card_rows.render_text(qps, summary)
        assert "⟦" in text and "`" not in text, summary


def test_a_tool_summary_is_never_translated(qps):
    for summary in TOOL_ROWS:
        assert card_rows.render_md(qps, summary) == animation._mono(summary)
        assert card_rows.render_text(qps, summary) == summary


def test_names_stay_in_the_code_span_and_words_go_outside(qps):
    md = card_rows.render_md(qps, bg_shells.settled_row("npm test", "completed", 90)[0])
    assert "`npm test`" in md
    md = card_rows.render_md(qps, "🔑 Bash: rm -rf build/ → Allowed")
    assert "`Bash: rm -rf build/`" in md and "Allowed" not in md


def test_persian_rows_keep_marks_out_of_the_spans():
    fa = i18n.get("fa")
    for summary in _rows():
        md = card_rows.render_md(fa, summary)
        for span in __import__("re").findall(r"`+[^`]*`+", md):
            assert not any(ch in span for ch in (RLM, FSI, PDI)), (summary, md)
    md = card_rows.render_md(fa, settled_agent_row("Explore", 3, 65))
    assert "۱:۰۵" in md and "1:05" not in md
    md = card_rows.render_md(fa, bg_shells.settled_row("make all", "failed", 5, exit_code=2)[0])
    assert "۲" in md


def test_the_tally_ignores_the_language(qps, mk_bot):
    from tests import i18n_surfaces
    sess = i18n_surfaces.card_session()
    english = animation._status_line(sess, "Working").split(" · ")
    preferences.set_ui_language(i18n_surfaces.CHAT, "qps")
    marked = animation._status_line(sess, "Working").split(" · ")
    tally = [p for p in english if "×" in p]
    assert tally and tally == [p for p in marked if "×" in p]
    assert "Bash ×8" in tally and not any(p.startswith("🤖") for p in tally)
