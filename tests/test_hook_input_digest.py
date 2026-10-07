"""The whole-input digest a PermissionRequest stores (roadmap 8.102,
review rev-iter3-002).

``_summarize_tool`` is lossy (a Bash summary is its description only, an
MCP tool's just its name), so it cannot tell two parallel calls of one
tool apart. The PermissionRequest stores the sha256 of the canonical JSON
of its whole ``tool_input``; a PostToolUse closes the prompt only when its
own input has the same digest. No digest, no close.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status

NAME = "claude-digest"
_SEQ = itertools.count(1)
TUID = "toolu_01Digest"


@pytest.fixture
def receiver():
    registry = SessionRegistry()
    notify_fn = AsyncMock()
    sess = registry.get_or_create(NAME)
    sess.label = "digest"
    registry.transition(NAME, Status.BUSY)
    return registry, hr.HookReceiver(registry, notify_fn), notify_fn


def _send(recv, run_async, **payload):
    payload.setdefault("session", NAME)
    payload["_seq"] = next(_SEQ)  # never a duplicate fingerprint
    run_async(recv._on_datagram(json.dumps(payload).encode()))


def _prompt_tool_info(notify_fn) -> dict:
    for call in reversed(notify_fn.await_args_list):
        _sess, event, context = call.args
        if event == "permission_prompt":
            return context["tool_info"]
    raise AssertionError("no permission prompt was notified")


def _canonical_digest(tool_input) -> str:
    canon = json.dumps(tool_input, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


# ── the digest itself ────────────────────────────────────────────────────

def test_the_digest_is_the_sha256_of_the_canonical_json():
    inp = {"description": "Clean", "command": "rm -rf build"}
    assert hr._input_digest(inp) == _canonical_digest(inp)


def test_key_order_does_not_change_the_digest():
    assert hr._input_digest({"a": 1, "b": [1, 2]}) \
        == hr._input_digest({"b": [1, 2], "a": 1})


def test_another_input_with_the_same_summary_has_another_digest():
    a = {"command": "rm -rf build", "description": "Clean"}
    b = {"command": "rm -rf dist", "description": "Clean"}
    assert hr._summarize_tool("Bash", a) == hr._summarize_tool("Bash", b)
    assert hr._input_digest(a) != hr._input_digest(b)


@pytest.mark.parametrize("bad", [
    {"x": object()}, {"x": {1, 2}}, {1: "a", "b": 2}])
def test_an_input_that_is_not_json_has_no_digest(bad):
    try:
        got = hr._input_digest(bad)
    except Exception as exc:  # noqa: BLE001 - the guard under test
        got = f"raised {type(exc).__name__}"
    assert got == ""


# ── stored at PermissionRequest ──────────────────────────────────────────

def test_the_permission_request_stores_its_inputs_digest(receiver, run_async):
    _reg, recv, notify_fn = receiver
    inp = {"command": "rm -rf build", "description": "Clean"}
    _send(recv, run_async, hook_event_name="PermissionRequest",
          tool_name="Bash", tool_input=inp, tool_use_id=TUID)
    assert _prompt_tool_info(notify_fn).get("input_digest") == _canonical_digest(inp)


def test_an_unserialisable_input_stores_no_digest(receiver, run_async,
                                                  monkeypatch):
    """A datagram is JSON, so its input always serialises; force the
    failure path to pin that no digest (not an empty one) is stored."""
    _reg, recv, notify_fn = receiver
    monkeypatch.setattr(hr, "_input_digest", lambda _inp: "")
    _send(recv, run_async, hook_event_name="PermissionRequest",
          tool_name="Bash", tool_input={"command": "ls"}, tool_use_id=TUID)
    assert "input_digest" not in _prompt_tool_info(notify_fn)


# ── compared at PostToolUse ──────────────────────────────────────────────

def _open_prompt(sess, **info_over) -> None:
    info = {"name": "Bash", "input": {}, "summary": "Bash: Clean",
            "tool_use_id": TUID}
    info.update(info_over)
    sess.pending_permission = {"tool_info": info}


A = {"command": "rm -rf build", "description": "Clean"}
B = {"command": "rm -rf dist", "description": "Clean"}


def test_the_same_input_finishes_the_prompt():
    sess = SessionRegistry().get_or_create(NAME)
    _open_prompt(sess, input_digest=hr._input_digest(A))
    msg = {"tool_use_id": TUID}
    assert hr._finishes_open_prompt(msg, "Bash", dict(A), sess)


def test_the_same_summary_with_another_input_does_not():
    sess = SessionRegistry().get_or_create(NAME)
    _open_prompt(sess, input_digest=hr._input_digest(A))
    msg = {"tool_use_id": TUID}
    assert not hr._finishes_open_prompt(msg, "Bash", B, sess)


@pytest.mark.parametrize("digest", [None, "", 7], ids=["absent", "empty", "int"])
def test_a_prompt_without_a_digest_is_finished_by_nothing(digest):
    sess = SessionRegistry().get_or_create(NAME)
    if digest is None:
        _open_prompt(sess)
    else:
        _open_prompt(sess, input_digest=digest)
    msg = {"tool_use_id": TUID}
    assert not hr._finishes_open_prompt(msg, "Bash", A, sess)


def test_an_unserialisable_ending_input_does_not_finish_it():
    sess = SessionRegistry().get_or_create(NAME)
    _open_prompt(sess, input_digest=hr._input_digest(A))
    msg = {"tool_use_id": TUID}
    assert not hr._finishes_open_prompt(msg, "Bash", {"x": object()}, sess)
