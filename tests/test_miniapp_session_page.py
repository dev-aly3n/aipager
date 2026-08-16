"""Session-page shaping: the last-message preview and the info line.

Both live in `sessions.py` as pure functions rather than in JavaScript so
the omission rules ("a finished session has no cost worth showing") are
pinned here instead of only being observable in a browser.
"""

import pytest

from aipager.miniapp.sessions import display_facts, preview_lines, session_detail
from aipager.state import SessionRegistry, Status


# ===== preview ============================================================

def test_empty_preview_returns_empty_string():
    """The client renders its own explicit "nothing captured" state — it
    needs to be able to tell empty from present."""
    assert preview_lines("") == ""
    assert preview_lines("   \n\n  ") == ""
    assert preview_lines(None) == ""


def test_preview_collapses_blank_lines():
    """Stored previews carry markdown paragraph breaks; on a phone those
    waste most of the visible box."""
    assert preview_lines("first\n\n\n\nsecond") == "first\nsecond"


def test_preview_strips_per_line_whitespace():
    assert preview_lines("  hello  \n   world  ") == "hello\nworld"


def test_short_preview_is_returned_intact():
    assert preview_lines("all done") == "all done"


def test_long_preview_is_capped_and_marked():
    out = preview_lines("word " * 200)
    assert len(out) <= 241          # cap + the ellipsis
    assert out.endswith("…")


def test_long_preview_cuts_on_a_word_boundary():
    """The word length is chosen so the 240-char cap lands MID-WORD.

    An earlier version repeated a 39-char phrase, which divides into 240
    leaving the cut exactly on a space — so the test passed even with the
    word-boundary logic removed, pinning nothing.
    """
    word = "supercalifragilistic"          # 20 chars + space = 21
    text = (word + " ") * 30
    assert text[239] not in " ", "fixture no longer cuts mid-word"

    out = preview_lines(text)
    body = out[:-1]                        # drop the ellipsis
    assert body.split()[-1] == word, (
        "preview cut mid-word instead of falling back to the last space"
    )


def test_preview_without_spaces_is_still_capped():
    """A single enormous token must not defeat the word-boundary logic."""
    out = preview_lines("x" * 500)
    assert len(out) <= 241
    assert out.endswith("…")


# ===== info line ==========================================================

def test_finished_session_shows_no_zero_noise():
    """A dead session legitimately has no model, cost or context. Showing
    "0% ctx · $0.00" states something false-looking about it."""
    facts = display_facts({
        "model": "", "context_pct": 0, "cost_usd": 0.0,
        "last_active_seconds_ago": None, "cwd": "",
        "busy_elapsed_seconds": None,
    })
    assert facts == []


def test_live_session_shows_every_populated_fact():
    facts = display_facts({
        "model": "Opus 4.6", "context_pct": 34, "cost_usd": 1.5,
        "last_active_seconds_ago": 125, "cwd": "/home/aly/aipager",
        "busy_elapsed_seconds": 90,
    })
    labels = [f["label"] for f in facts]
    assert labels == ["Model", "Context", "Cost", "Working for", "Last active", "Directory"]
    by_label = {f["label"]: f["value"] for f in facts}
    assert by_label["Context"] == "34%"
    assert by_label["Cost"] == "$1.50"
    assert by_label["Working for"] == "1m"
    assert by_label["Last active"] == "2m ago"
    assert by_label["Directory"] == "/home/aly/aipager"


def test_zero_seconds_ago_is_shown_but_null_is_omitted():
    """0 is a real reading ("just now"); None means never recorded."""
    assert any(f["label"] == "Last active"
               for f in display_facts({"last_active_seconds_ago": 0}))
    assert not any(f["label"] == "Last active"
                   for f in display_facts({"last_active_seconds_ago": None}))


@pytest.mark.parametrize("seconds,expected", [
    (0, "0s ago"), (59, "59s ago"), (60, "1m ago"),
    (3599, "59m ago"), (3600, "1h ago"), (86400, "1d ago"),
])
def test_duration_units(seconds, expected):
    facts = display_facts({"last_active_seconds_ago": seconds})
    assert facts[0]["value"] == expected


def test_facts_tolerate_a_payload_missing_every_key():
    assert display_facts({}) == []


# ===== wired into the detail payload ======================================

def _sess(registry, **kw):
    sess = registry.get_or_create("claude-x")
    sess.label = "x"
    for key, value in kw.items():
        setattr(sess, key, value)
    return sess


def test_detail_payload_carries_preview_and_facts():
    registry = SessionRegistry()
    sess = _sess(
        registry,
        status=Status.IDLE,
        model_name="Opus 4.6",
        last_assistant_preview="Done — the tests pass.",
        cwd="/home/aly/aipager",
    )
    detail = session_detail(sess, 1000.0)
    assert detail["last_message"] == "Done — the tests pass."
    assert {f["label"] for f in detail["facts"]} >= {"Model", "Directory"}


def test_detail_payload_preview_is_empty_when_nothing_captured():
    registry = SessionRegistry()
    sess = _sess(registry, status=Status.GONE, last_assistant_preview="")
    detail = session_detail(sess, 1000.0)
    assert detail["last_message"] == ""
    # And a finished session with nothing recorded produces no noisy facts.
    assert detail["facts"] == []
