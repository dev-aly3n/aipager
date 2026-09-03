"""spec.md requirement 1 ("Attribution"): a tool event whose ``agent_id``
matches a live ``active_subagents`` entry updates that agent's entry
(activity/tool_count/last_tool_at) and does NOT append a row to the
parent's ``tool_history``. A tool event with an unmatched/empty/missing
``agent_id`` falls back to a parent row (never silently dropped).

Driven at the ``TelegramBot.notify`` event-level contract
(entrypoints.md's "Direct, event-level" surface) and, for the
full-stack scenario, at the hook-datagram level too (entrypoints.md's
"End-to-end, hook-datagram level" surface) — a genuinely independent
angle from the Developer's own per-layer unit tests.
"""

from __future__ import annotations

import time

from aipager.state import AGENT_TOOLS_CAP


def _agent_entry(**overrides):
    entry = {
        "type": "explore",
        "started_at": time.monotonic(),
        "history_idx": None,
        "activity": "",
        "tool_count": 0,
        "last_tool_at": 0.0,
        "tools": [],
    }
    entry.update(overrides)
    return entry


# ---- matching agent_id: routed to the agent, not the parent -------------

def test_matching_agent_id_updates_agent_activity_and_skips_parent_row(
    mk_sess, run_async, mk_bot,
):
    bot = mk_bot()
    sess = mk_sess()
    sess.active_subagents["a-77"] = _agent_entry()
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Grep: TODO",
        "tool_name": "Grep",
        "tool_input_full": None,
        "agent_id": "a-77",
    }))
    assert sess.tool_history == []  # nothing landed in the parent timeline
    assert sess.active_subagents["a-77"]["activity"] == "Grep: TODO"


def test_matching_agent_id_increments_tool_count_across_two_calls(
    mk_sess, run_async, mk_bot,
):
    bot = mk_bot()
    sess = mk_sess()
    sess.active_subagents["a-77"] = _agent_entry()
    for summary in ("Read: /a", "Read: /b"):
        run_async(bot.notify(sess, "tool_use", {
            "tool_summary": summary, "tool_name": "Read",
            "tool_input_full": None, "agent_id": "a-77",
        }))
    info = sess.active_subagents["a-77"]
    assert info["tool_count"] == 2
    assert info["activity"] == "Read: /b"  # latest wins
    assert info["tools"] == ["Read: /a", "Read: /b"]
    assert sess.tool_history == []


def test_matching_agent_id_records_last_tool_at(mk_sess, run_async, mk_bot):
    bot = mk_bot()
    sess = mk_sess()
    sess.active_subagents["a-77"] = _agent_entry(last_tool_at=0.0)
    before = time.monotonic()
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: pytest", "tool_name": "Bash",
        "tool_input_full": None, "agent_id": "a-77",
    }))
    after = time.monotonic()
    last_tool_at = sess.active_subagents["a-77"]["last_tool_at"]
    assert before <= last_tool_at <= after


# ---- unmatched / empty / missing agent_id: never dropped -----------------

def test_unknown_agent_id_falls_back_to_a_parent_row(mk_sess, run_async, mk_bot):
    """The agent already stopped (or never existed) — no entry to
    attribute to, so the tool event must still land somewhere."""
    bot = mk_bot()
    sess = mk_sess()
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: git status", "tool_name": "Bash",
        "tool_input_full": None, "agent_id": "agent-that-never-existed",
    }))
    assert sess.tool_history == [("Bash: git status", False)]


def test_empty_string_agent_id_falls_back_to_a_parent_row(mk_sess, run_async, mk_bot):
    bot = mk_bot()
    sess = mk_sess()
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: pwd", "tool_name": "Bash",
        "tool_input_full": None, "agent_id": "",
    }))
    assert sess.tool_history == [("Bash: pwd", False)]


def test_empty_agent_id_falls_back_even_if_active_subagents_has_a_falsy_key(
    mk_sess, run_async, mk_bot,
):
    """The routing guard must check BOTH truthiness of agent_id AND
    membership — not membership alone. A pathological "" key in
    active_subagents (defensive: should never happen in production, but
    the guard must not rely on that) must not cause an empty agent_id to
    be wrongly attributed."""
    bot = mk_bot()
    sess = mk_sess()
    sess.active_subagents[""] = _agent_entry(type="phantom-key")
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: pwd", "tool_name": "Bash",
        "tool_input_full": None, "agent_id": "",
    }))
    assert sess.tool_history == [("Bash: pwd", False)]
    assert sess.active_subagents[""]["activity"] == ""  # untouched


def test_missing_agent_id_key_entirely_falls_back_to_a_parent_row(
    mk_sess, run_async, mk_bot,
):
    """entrypoints.md: "omit or "" for 'no agent'" — the key may be
    absent from the context dict altogether, not just empty."""
    bot = mk_bot()
    sess = mk_sess()
    run_async(bot.notify(sess, "tool_use", {
        "tool_summary": "Bash: whoami", "tool_name": "Bash",
        "tool_input_full": None,
        # no "agent_id" key at all
    }))
    assert sess.tool_history == [("Bash: whoami", False)]


def test_unmatched_agent_id_tool_event_is_never_silently_dropped(
    mk_sess, run_async, mk_bot,
):
    """Regardless of which agent_id shape is unmatched, the event count
    in tool_history must equal the number of tool_use events sent — none
    vanish."""
    bot = mk_bot()
    sess = mk_sess()
    for i, aid in enumerate(["", "ghost-1", "ghost-2"]):
        run_async(bot.notify(sess, "tool_use", {
            "tool_summary": f"Bash: cmd{i}", "tool_name": "Bash",
            "tool_input_full": None, "agent_id": aid,
        }))
    assert len(sess.tool_history) == 3


# ---- tool_done / tool_failed for an attributed tool: no parent mutation --

def test_tool_done_for_attributed_tool_leaves_unrelated_inflight_row_untouched(
    mk_sess, run_async, mk_bot,
):
    bot = mk_bot()
    sess = mk_sess()
    sess.tool_history = [("Read: /parent-in-flight", False)]
    sess.active_subagents["a-9"] = _agent_entry()
    run_async(bot.notify(sess, "tool_done", {
        "tool_name": "Bash", "tool_summary": "Bash: ls",
        "agent_id": "a-9",
    }))
    assert sess.tool_history == [("Read: /parent-in-flight", False)]


def test_tool_failed_for_attributed_tool_leaves_unrelated_inflight_row_untouched(
    mk_sess, run_async, mk_bot,
):
    bot = mk_bot()
    sess = mk_sess()
    sess.tool_history = [("Read: /parent-in-flight", False)]
    sess.active_subagents["a-9"] = _agent_entry()
    run_async(bot.notify(sess, "tool_failed", {
        "tool_name": "Bash", "tool_summary": "Bash: ls",
        "agent_id": "a-9",
    }))
    assert sess.tool_history == [("Read: /parent-in-flight", False)]


# ---- documented cap (entrypoints.md: AGENT_TOOLS_CAP=100) ----------------

def test_agent_tools_list_never_exceeds_the_documented_cap(
    mk_sess, run_async, mk_bot,
):
    bot = mk_bot()
    sess = mk_sess()
    sess.active_subagents["a-1"] = _agent_entry()
    for i in range(AGENT_TOOLS_CAP + 10):
        run_async(bot.notify(sess, "tool_use", {
            "tool_summary": f"Bash: step{i}", "tool_name": "Bash",
            "tool_input_full": None, "agent_id": "a-1",
        }))
    info = sess.active_subagents["a-1"]
    assert len(info["tools"]) == AGENT_TOOLS_CAP
    assert info["tool_count"] == AGENT_TOOLS_CAP + 10  # count keeps growing
    assert info["tools"][-1] == f"Bash: step{AGENT_TOOLS_CAP + 9}"  # newest kept


# ---- full-stack: hook datagram -> HookReceiver -> notify -> TrackedSession

def test_end_to_end_hook_datagram_attributes_tool_to_agent_not_parent(
    mk_bot, run_async,
):
    """Drives the real public chain: a raw JSON hook datagram through
    HookReceiver._on_datagram, wired straight to a real TelegramBot.notify
    (no direct TrackedSession poking) — the actual path a live subagent's
    PreToolUse hook takes in production."""
    import json

    from aipager.dtach import hook_receiver as hr
    from aipager.state import SessionRegistry

    registry = SessionRegistry()
    bot = mk_bot(registry)
    recv = hr.HookReceiver(registry, bot.notify)

    def _send(**fields):
        run_async(recv._on_datagram(json.dumps(fields).encode()))

    _send(hook_event_name="SubagentStart", session="claude-jim",
          agent_id="a-42", agent_type="ship-reviewer")
    sess = registry.get("claude-jim")
    assert "a-42" in sess.active_subagents

    _send(hook_event_name="PreToolUse", session="claude-jim",
          tool_name="Bash", tool_input={"command": "pytest -q"},
          agent_id="a-42")

    assert sess.active_subagents["a-42"]["tool_count"] == 1
    assert "pytest" in sess.active_subagents["a-42"]["activity"]
    # The subagent's own Bash call must not have flooded the parent's
    # timeline with a second row (only the "🤖 ship-reviewer" row from
    # SubagentStart itself is present).
    bash_rows = [s for s, _ in sess.tool_history if s.startswith("Bash:")]
    assert bash_rows == []


def test_end_to_end_hook_datagram_unattributed_tool_lands_in_parent_history(
    mk_bot, run_async,
):
    """Same full-stack chain, but the PreToolUse carries no agent_id (a
    parent's own tool call) — must land in tool_history exactly as
    before this feature."""
    import json

    from aipager.dtach import hook_receiver as hr
    from aipager.state import SessionRegistry

    registry = SessionRegistry()
    bot = mk_bot(registry)
    recv = hr.HookReceiver(registry, bot.notify)

    def _send(**fields):
        run_async(recv._on_datagram(json.dumps(fields).encode()))

    _send(hook_event_name="PreToolUse", session="claude-jim",
          tool_name="Bash", tool_input={"command": "ls -la"})

    sess = registry.get("claude-jim")
    assert any(s.startswith("Bash: ls") for s, _ in sess.tool_history)
