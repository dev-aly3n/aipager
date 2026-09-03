"""spec.md requirement 5 / entrypoints.md: the legacy HTML card
(``TelegramBot._build_busy_text``) shows the same agent-line shape as
the rich card (live/settled), HTML-escaped, inside ``<code>...</code>``.
"""

from __future__ import annotations

import time

from aipager.state import Status, TrackedSession

MARK = "\U0001f916"


def test_legacy_card_live_agent_row_shows_type_activity_and_elapsed(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [(f"{MARK} auditor", False)]
    sess.active_subagents["x1"] = {
        "type": "auditor",
        "started_at": time.monotonic() - 22,
        "history_idx": 0,
        "activity": "Glob: **/*.py",
    }
    text = bot._build_busy_text("jim", "Working", sess)
    assert "auditor · Glob: **/*.py · 22s" in text


def test_legacy_card_live_agent_row_shows_starting_with_no_activity(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [(f"{MARK} auditor", False)]
    sess.active_subagents["x1"] = {
        "type": "auditor",
        "started_at": time.monotonic(),
        "history_idx": 0,
    }
    text = bot._build_busy_text("jim", "Working", sess)
    assert "auditor · starting ·" in text


def test_legacy_card_agent_row_html_escapes_special_characters_in_activity(mk_bot):
    """The activity/type substrings must be HTML-escaped, not left raw —
    a subagent's tool summary can legitimately contain "<", ">", "&"
    (e.g. shell redirections or comparisons echoed in a Bash summary)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [(f"{MARK} auditor", False)]
    sess.active_subagents["x1"] = {
        "type": "auditor",
        "started_at": time.monotonic() - 3,
        "history_idx": 0,
        "activity": "Bash: a<b && echo 'x' > out.txt",
    }
    text = bot._build_busy_text("jim", "Working", sess)
    assert "a<b" not in text
    assert "a&lt;b" in text
    assert "&amp;&amp;" in text
    assert "&gt; out.txt" in text


def test_legacy_card_agent_row_settled_shows_frozen_plural_count(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = [(f"{MARK} auditor", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "x1", "agent_type": "auditor",
        "elapsed": 90.0, "history_idx": 0, "tool_count": 12,
    }))
    text = bot._build_busy_text("jim", "Working", sess)
    assert "auditor · 12 tool calls · 1m 30s" in text


def test_legacy_card_agent_row_settled_shows_frozen_singular_count(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = [(f"{MARK} auditor", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "x1", "agent_type": "auditor",
        "elapsed": 2.0, "history_idx": 0, "tool_count": 1,
    }))
    text = bot._build_busy_text("jim", "Working", sess)
    assert "1 tool call ·" in text
    assert "1 tool calls" not in text


def test_legacy_card_agent_row_lives_inside_a_code_span(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [(f"{MARK} auditor", False)]
    sess.active_subagents["x1"] = {
        "type": "auditor", "started_at": time.monotonic() - 1,
        "history_idx": 0, "activity": "Read: /x",
    }
    text = bot._build_busy_text("jim", "Working", sess)
    assert "<code>" in text and "</code>" in text
    # The agent row text is contained within SOME code span in the card.
    import re
    spans = re.findall(r"<code>(.*?)</code>", text, re.DOTALL)
    assert any("auditor · Read: /x" in span for span in spans)
