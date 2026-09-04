"""design.md success criteria: the legacy `_build_busy_text`/
`_edit_busy_raw` HTML path never emits `<details` under any input — it
structurally cannot, since it never calls `_build_sections`/
`_fit_sections`/`build_stream_card_ex` at all (it independently reads
`sess.tool_history`, hand-rolled HTML truncation). Pinned here as a
regression guard, including under an oversized `tool_history` that would
force the RICH path to collapse content into a `<details>` block.

Per entrypoints.md: the Tester MAY exercise `_build_busy_text` through
`TelegramBot` instance methods via `mk_bot`, but must not treat its exact
text shape as a documented rendering contract beyond "never emits
`<details`".
"""

from __future__ import annotations

import time

from aipager.state import Status, TrackedSession


def test_legacy_card_never_emits_details_tag_under_normal_input(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [("Bash: ls", True), ("Read: /a.py", True)]
    text = bot._build_busy_text("jim", "Working", sess)
    assert "<details" not in text


def test_legacy_card_never_emits_details_tag_under_an_oversized_tool_history(mk_bot):
    """The same tool_history size that forces the RICH card into a
    <details> block must not make the legacy HTML card emit one — it has
    no such concept at all."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 30
    sess.tool_history = [
        (f"Bash: step-{i} " + "z" * 300, True) for i in range(500)
    ]
    text = bot._build_busy_text("jim", "Working", sess)
    assert "<details" not in text
    assert "<summary" not in text


def test_legacy_card_never_emits_details_tag_with_a_live_agent_row(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.tool_history = [
        (f"Bash: step-{i} " + "z" * 200, True) for i in range(300)
    ] + [("\U0001f916 explore", False)]
    sess.active_subagents["a1"] = {
        "type": "explore", "started_at": time.monotonic() - 5,
        "history_idx": len(sess.tool_history) - 1, "activity": "Bash: ls",
    }
    text = bot._build_busy_text("jim", "Working", sess)
    assert "<details" not in text
