"""spec.md requirement 6 / "Facts established": phantom SubagentStop
events (empty type, unknown ids, 0.0s elapsed) arrive constantly in
production and must remain harmless — no crash, no bogus row, no
pollution of ``finished_subagents``. Driven both at the ``notify()``
event level and end-to-end through the real hook-datagram path.
"""

from __future__ import annotations

from aipager.state import Status, TrackedSession

MARK = "\U0001f916"


# ---- notify() event-level ---------------------------------------------------

def test_phantom_empty_type_unknown_id_adds_no_row(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = []
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "ghost-1", "agent_type": "",
        "elapsed": 0.0, "history_idx": None,
    }))
    assert sess.tool_history == []


def test_phantom_zero_elapsed_does_not_crash_with_a_real_type(mk_bot, run_async):
    """A zero-elapsed stop for a real (non-empty) agent_type is not a
    "phantom" by the empty-type test — it's a legitimately instant agent
    (or one racing a very fast SubagentStart→Stop) — must not raise and
    must render a usable elapsed string ("0s" floor, never empty)."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    sess.tool_history = [(f"{MARK} instant-agent", False)]
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "a-fast", "agent_type": "instant-agent",
        "elapsed": 0.0, "history_idx": 0, "tool_count": 0,
    }))
    summary, done = sess.tool_history[0]
    assert done is True
    assert "0s" in summary  # floor, never blank


def test_phantom_stop_does_not_pollute_finished_subagents(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    run_async(bot.notify(sess, "subagent_stop", {
        "agent_id": "ghost-2", "agent_type": "",
        "elapsed": 0.0, "history_idx": None,
    }))
    assert sess.finished_subagents == []


def test_multiple_phantom_stops_in_a_row_never_raise(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = None
    for i in range(5):
        run_async(bot.notify(sess, "subagent_stop", {
            "agent_id": f"ghost-{i}", "agent_type": "",
            "elapsed": 0.0, "history_idx": None,
        }))
    assert sess.tool_history == []


# ---- end-to-end: hook datagram, unknown-id SubagentStop with no start ------

def test_end_to_end_subagent_stop_with_no_matching_start_does_not_raise(
    mk_bot, run_async,
):
    import json

    from aipager.dtach import hook_receiver as hr
    from aipager.state import SessionRegistry

    registry = SessionRegistry()
    bot = mk_bot(registry)
    recv = hr.HookReceiver(registry, bot.notify)

    def _send(**fields):
        run_async(recv._on_datagram(json.dumps(fields).encode()))

    # No preceding SubagentStart — a SubagentStop that finds nothing to
    # pop out of active_subagents.
    _send(hook_event_name="SubagentStop", session="claude-jim",
          agent_id="never-started", agent_type="")

    sess = registry.get("claude-jim")
    assert sess is not None
    assert sess.finished_subagents == []


def test_end_to_end_subagent_stop_after_real_start_archives_exactly_once(
    mk_bot, run_async,
):
    """Contrast case: a REAL (non-phantom) stop for an agent that did
    start must still archive normally — phantom tolerance must not
    accidentally swallow legitimate stops."""
    import json

    from aipager.dtach import hook_receiver as hr
    from aipager.state import SessionRegistry

    registry = SessionRegistry()
    bot = mk_bot(registry)
    recv = hr.HookReceiver(registry, bot.notify)

    def _send(**fields):
        run_async(recv._on_datagram(json.dumps(fields).encode()))

    _send(hook_event_name="SubagentStart", session="claude-jim",
          agent_id="real-1", agent_type="explore")
    _send(hook_event_name="SubagentStop", session="claude-jim",
          agent_id="real-1", agent_type="explore")

    sess = registry.get("claude-jim")
    assert "real-1" not in sess.active_subagents
    assert len(sess.finished_subagents) == 1
    assert sess.finished_subagents[0]["type"] == "explore"
