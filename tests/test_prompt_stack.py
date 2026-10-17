"""Roadmap 8.120: which of several open prompts Claude Code shows first.

The rule (aipager/prompt_stack.py): the dialog that entered the terminal
first is on top; the main loop's and a foreground agent's enter when they
are asked, a background agent's only once its parked hook gives up. Keys
may be typed only when the target's dialog is certainly on top.
"""

from __future__ import annotations

import pytest

from aipager import prompt_stack as ps

NOW = 1000.0


def own(at):
    return {"owner": "", "launch": "own", "arrived": at}


def agent(at, launch, *, request_id="", timed_out_at=0.0, owner="a1"):
    return {"owner": owner, "launch": launch, "arrived": at,
            "request_id": request_id, "timed_out_at": timed_out_at}


@pytest.mark.parametrize("order, expected", [
    (None, (False, 0.0, 0.0)),
    (own(10.0), (False, 10.0, 10.0)),
    (agent(10.0, "foreground"), (False, 10.0, 10.0)),
    (agent(10.0, "background", timed_out_at=40.0), (False, 40.0, 40.0)),
    (agent(NOW - 5, "background", request_id="r"), (True, None, None)),
    (agent(NOW - 60, "background", request_id="r"), (True, NOW - 60, NOW)),
    (agent(10.0, "background"), (True, 10.0, NOW)),
    (agent(10.0, "unknown", timed_out_at=40.0), (False, 10.0, 40.0)),
    (agent(NOW - 5, "unknown", request_id="r"), (True, NOW - 5, NOW - 5)),
    (agent(NOW - 60, "unknown", request_id="r"), (True, NOW - 60, NOW)),
    (agent(10.0, "unknown"), (True, 10.0, NOW)),
], ids=["no-order", "own", "foreground", "background-timed-out",
        "background-parked", "background-past-deadline", "background-no-hook",
        "unknown-timed-out", "unknown-parked", "unknown-past-deadline",
        "unknown-no-hook"])
def test_place(order, expected):
    assert ps.place(order, NOW) == expected


def test_a_single_prompt_is_always_yes():
    for target in (own(1.0), agent(NOW - 1, "background", request_id="r"),
                   agent(1.0, "unknown")):
        assert ps.keys_verdict(target, [], NOW) == "yes"


@pytest.mark.parametrize("target, others, expected", [
    # Claude's two parallel prompts: first come, first shown.
    (own(10.0), [own(20.0)], "yes"),
    (own(20.0), [own(10.0)], "behind"),
    # A background agent's prompt while its hook is parked is not there.
    (own(20.0), [agent(NOW - 5, "background", request_id="r")], "yes"),
    (agent(NOW - 5, "background", request_id="r"), [own(20.0)], "behind"),
    # Its hook gave up after Claude asked: it went under.
    (own(20.0), [agent(10.0, "background", timed_out_at=30.0)], "yes"),
    (agent(10.0, "background", timed_out_at=30.0), [own(20.0)], "behind"),
    # A foreground agent asked first: its dialog is on top.
    (agent(10.0, "foreground"), [own(20.0)], "yes"),
    (own(20.0), [agent(10.0, "foreground")], "behind"),
    # Not known whether its dialog raced its hook: no keys for either.
    (own(20.0), [agent(10.0, "unknown")], "unknown"),
    (agent(10.0, "unknown"), [own(20.0)], "unknown"),
    # The other is certainly present and earlier, though the target may be absent.
    (agent(NOW - 60, "background", request_id="r"), [own(20.0)], "behind"),
    # Every other one certainly absent.
    (own(20.0), [agent(NOW - 1, "background", request_id="r"),
                 agent(NOW - 2, "background", request_id="s", owner="a2")], "yes"),
    # A tie is not an order.
    (own(10.0), [agent(10.0, "foreground")], "unknown"),
], ids=["own-first", "own-second", "bg-parked-other", "bg-parked-target",
        "bg-timed-out-other", "bg-timed-out-target", "fg-first", "fg-first-other",
        "unknown-other", "unknown-target", "target-maybe-absent-behind",
        "others-absent", "tie"])
def test_keys_verdict(target, others, expected):
    assert ps.keys_verdict(target, others, NOW) == expected


def test_a_bad_order_fails_closed():
    assert ps.keys_verdict(own(1.0), [{"owner": "a", "arrived": "x",
                                       "launch": "unknown"}], NOW) in ("unknown", "behind")
    assert ps.keys_verdict({"arrived": object()}, [own(1.0)], NOW) in ("unknown", "behind")


def test_prompt_key_tells_parallel_calls_apart():
    a = ps.prompt_key("a1", "Bash", "", "d1", "Bash: make")
    b = ps.prompt_key("a1", "Bash", "", "d2", "Bash: make")
    assert a != b
    assert ps.prompt_key("a1", "Bash", "toolu_1", "d1", "s") != ps.prompt_key(
        "a1", "Bash", "toolu_2", "d1", "s")
    assert ps.prompt_key("", "Bash", "", "", "Bash: x") == ps.prompt_key(
        "", "Bash", "", "", "Bash: x")
    assert ps.prompt_key("a1", "Bash", "", "d1", "s") != ps.prompt_key(
        "a2", "Bash", "", "d1", "s")
