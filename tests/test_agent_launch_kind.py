"""Roadmap 8.120: how an agent was launched, for the dialog order.

A background agent's dialog enters the terminal only after its hook gives
up; a foreground one's at once. aipager learns which from the Agent call
that started it (``run_in_background``), demotes a foreground agent that
went async mid-run to "unknown", and says "unknown" whenever the calls
cannot be told apart.
"""

from __future__ import annotations

from aipager import agent_tree
from aipager.state import TrackedSession

A, B = "a1b2c3d4e5f60718a", "b1b2c3d4e5f60718b"


def _sess():
    return TrackedSession(name="claude-x1", label="x1")


def test_run_in_background_is_background():
    sess = _sess()
    sess.agent_call_launched("ship-reviewer", "Review", 100.0, background=True)
    sess.bg_agent_started(A, "ship-reviewer", 101.0)
    assert sess.agent_launch(A) == "background"
    sess.agent_call_launched("ship-tester", "Test", 102.0)
    sess.bg_agent_started(B, "ship-tester", 103.0)
    assert sess.agent_launch(B) == "foreground"


def test_isasync_on_foreground_turns_unknown():
    sess = _sess()
    sess.agent_call_launched("ship-tester", "Test", 100.0)
    sess.bg_agent_started(A, "ship-tester", 101.0)
    assert sess.agent_launch(A) == "foreground"
    msg = {"tool_name": "Agent",
           "tool_response": {"isAsync": True, "agentId": A}}
    assert agent_tree.async_launched(msg) == A
    sess.bg_agent_went_async(A)
    assert sess.agent_launch(A) == "unknown"
    # A background one stays background.
    sess.agent_call_launched("ship-reviewer", "Review", 102.0, background=True)
    sess.bg_agent_started(B, "ship-reviewer", 103.0)
    sess.bg_agent_went_async(B)
    assert sess.agent_launch(B) == "background"


def test_ambiguous_match_is_unknown():
    sess = _sess()
    sess.agent_call_launched("ship-reviewer", "One", 100.0, background=True)
    sess.agent_call_launched("ship-reviewer", "Two", 100.5)
    sess.bg_agent_started(A, "ship-reviewer", 101.0)
    assert sess.agent_launch(A) == "unknown"
    # No call at all (a teammate, a resume of a stopped one).
    sess.bg_agent_started(B, "ship-tester", 102.0)
    assert sess.agent_launch(B) == "unknown"
    assert sess.agent_launch("") == "unknown"


def test_two_background_calls_of_one_type_agree():
    sess = _sess()
    sess.agent_call_launched("ship-reviewer", "One", 100.0, background=True)
    sess.agent_call_launched("ship-reviewer", "Two", 100.5, background=True)
    sess.bg_agent_started(A, "ship-reviewer", 101.0)
    assert sess.agent_launch(A) == "background"


def test_async_launched_reads_only_an_async_agent_call():
    assert agent_tree.async_launched({"tool_name": "Bash",
                                      "tool_response": {"isAsync": True,
                                                        "agentId": A}}) == ""
    assert agent_tree.async_launched({"tool_name": "Task",
                                      "tool_response": {"isAsync": False,
                                                        "agentId": A}}) == ""
    assert agent_tree.async_launched({"tool_name": "Task",
                                      "tool_response": {"isAsync": True,
                                                        "agentId": "x y"}}) == ""
    assert agent_tree.async_launched("nope") == ""
