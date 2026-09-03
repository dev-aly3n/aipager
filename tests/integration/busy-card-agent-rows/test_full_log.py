"""spec.md requirement 4 ("Full log"): ``build_full_log`` gains an
AGENTS section listing every agent seen this turn, and omits it
entirely when no agent ran. entrypoints.md's orchestrator amendment:
``agents`` is a TRAILING KEYWORD parameter with a default —
``build_full_log(label, tool_history, commentary, answer, agents=[...])``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.bot.animation import build_full_log
from aipager.state import Status, TrackedSession

MARK = "\U0001f916"


def _agent(type_, elapsed, tool_count, tools, started_at=0.0):
    return {
        "type": type_, "started_at": started_at, "elapsed": elapsed,
        "tool_count": tool_count, "tools": tools,
    }


# ---- presence / content ----------------------------------------------------

def test_agents_section_lists_each_agents_type_elapsed_and_count():
    agents = [_agent("crawler", 12.0, 3, ["Grep: a", "Grep: b", "Read: c"])]
    log = build_full_log("jim", [], [], "done", agents=agents)
    assert "AGENTS" in log
    assert f"{MARK} crawler — 12s — 3 tool calls" in log


def test_agents_section_lists_every_tool_summary_as_a_bullet():
    agents = [_agent("crawler", 3.0, 2, ["Grep: TODO", "Read: /x.py"])]
    log = build_full_log("jim", [], [], "done", agents=agents)
    assert "  - Grep: TODO" in log
    assert "  - Read: /x.py" in log


def test_agents_section_uses_singular_for_one_tool_call():
    agents = [_agent("crawler", 1.0, 1, ["Grep: TODO"])]
    log = build_full_log("jim", [], [], "done", agents=agents)
    assert "1 tool call" in log
    assert "1 tool calls" not in log


def test_agents_section_formats_elapsed_over_a_minute_as_minutes_and_seconds():
    agents = [_agent("crawler", 75.0, 4, ["a", "b", "c", "d"])]
    log = build_full_log("jim", [], [], "done", agents=agents)
    assert "1m 15s" in log


def test_agents_section_lists_multiple_agents_each_with_their_own_tools():
    agents = [
        _agent("crawler", 5.0, 1, ["Grep: a"]),
        _agent("auditor", 20.0, 2, ["Read: b", "Read: c"]),
    ]
    log = build_full_log("jim", [], [], "done", agents=agents)
    assert f"{MARK} crawler" in log
    assert f"{MARK} auditor" in log
    assert "  - Grep: a" in log
    assert "  - Read: b" in log
    assert "  - Read: c" in log


def test_agents_section_appears_before_final_answer_block():
    agents = [_agent("crawler", 2.0, 1, ["Grep: a"])]
    log = build_full_log("jim", [], [], "the final answer text", agents=agents)
    assert log.index("AGENTS") < log.index("FINAL ANSWER")


def test_agents_section_lists_agents_in_the_order_given():
    """entrypoints.md: "sorted by started_at (chronological / start
    order)" — the caller (notify.py) is responsible for sorting; this
    test pins that build_full_log itself preserves whatever order the
    caller passes (does not re-sort or reverse it)."""
    agents = [
        _agent("first", 1.0, 1, ["a"], started_at=1.0),
        _agent("second", 1.0, 1, ["b"], started_at=2.0),
        _agent("third", 1.0, 1, ["c"], started_at=3.0),
    ]
    log = build_full_log("jim", [], [], "done", agents=agents)
    assert log.index("first") < log.index("second") < log.index("third")


# ---- absence ----------------------------------------------------------------

def test_agents_section_omitted_when_agents_kwarg_not_passed_at_all():
    log = build_full_log("jim", [("Bash: x", True)], [], "answer")
    assert "AGENTS" not in log


def test_agents_section_omitted_when_agents_is_none():
    log = build_full_log("jim", [("Bash: x", True)], [], "answer", agents=None)
    assert "AGENTS" not in log


def test_agents_section_omitted_when_agents_is_empty_list():
    log = build_full_log("jim", [("Bash: x", True)], [], "answer", agents=[])
    assert "AGENTS" not in log


def test_tool_row_list_is_unaffected_when_no_agents_ran():
    """Backward-compatible: the rest of the log's shape is unchanged when
    there are no agents to report."""
    log_with_default = build_full_log("jim", [("Bash: x", True)], [], "answer")
    log_with_empty = build_full_log(
        "jim", [("Bash: x", True)], [], "answer", agents=[],
    )
    assert log_with_default == log_with_empty


# ---- end-to-end: notify.py actually builds and threads log_agents --------

def test_end_to_end_idle_close_threads_finished_and_active_agents_through(
    mk_bot, run_async, monkeypatch,
):
    """A different scenario than a single-agent snapshot: TWO finished
    agents plus one still (defensively) active, verified via a spy on
    the real build_full_log call made by notify.py's idle-close path —
    the actual production wiring, not a hand-built agents= list."""
    monkeypatch.setattr(
        "aipager.bot.notify.send_rich_message", AsyncMock(return_value={}),
    )
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_msg_id = None
    sess.last_card_truncated = True  # forces the .txt attachment path
    sess.finished_subagents = [
        _agent("crawler", 5.0, 2, ["Grep: a", "Grep: b"], started_at=1.0),
        _agent("auditor", 9.0, 1, ["Read: c"], started_at=2.0),
    ]
    sess.active_subagents["still-going"] = {
        "type": "reviewer", "started_at": 3.0, "tool_count": 1,
        "tools": ["Bash: lint"],
    }
    sess.job_background_open = lambda: False
    bot._app.bot.send_document = AsyncMock()

    import aipager.bot.notify as notify_mod
    real_build_full_log = notify_mod.build_full_log
    captured = {}

    def _spy(*args, **kwargs):
        captured["agents"] = kwargs.get("agents")
        captured["log_text"] = real_build_full_log(*args, **kwargs)
        return captured["log_text"]

    monkeypatch.setattr(notify_mod, "build_full_log", _spy)
    run_async(bot.notify(sess, "idle_prompt", {"summary": "done"}))

    types = [a["type"] for a in captured["agents"]]
    assert types == ["crawler", "auditor", "reviewer"]
    assert "AGENTS" in captured["log_text"]
    assert f"{MARK} crawler" in captured["log_text"]
    assert f"{MARK} reviewer" in captured["log_text"]
