"""Criterion 15: a restricted member's auto-deny that waits beside a
countdown candidate types nothing, does
not loop, and types its Escape once the other prompt closes (design.md
success criterion 15; the auto-deny is one Escape, roadmap 8.126).

The group's rules refuse Write for a user's turn (bob); a user's turn is
also refused Bash, so the candidate (Claude's own Bash in Full access/Auto
with its hook still waiting, closable through that hook without typing)
is raised while the owner drives, and the refused Write once bob does."""

from __future__ import annotations

import json

import pytest

import tests.two_prompts_rig as rigmod
from aipager.scope import Member, Scope
from tests.two_prompts_rig import BOB, CARD, GROUP, Rig

#: The rig's owner member (alice).
OWNER_ID = 1

WRITE = {"file_path": "/home/alice/app/a.py", "content": "x"}
MODES = ["bypassPermissions", "auto"]


def deny_write_scopes():
    return [Scope(chat_id=GROUP, kind="group", label="team", deny_tools=("Write",),
                  members=(Member(id=OWNER_ID, label="alice", role="owner"),
                           Member(id=BOB, label="bob", role="user")))]


@pytest.fixture
def drig(mk_bot, monkeypatch, run_async):
    monkeypatch.setattr(rigmod, "scopes", deny_write_scopes)
    return Rig(mk_bot, monkeypatch, run_async)


def write_request(r, tool_use_id="toolu_w1"):
    r.sess.last_driver_user_id = BOB
    r.hook("PermissionRequest", tool_name="Write", tool_input=WRITE,
           tool_use_id=tool_use_id, permission_mode="bypassPermissions")


def rm_parked(r, mode):
    r.sess.last_driver_user_id = OWNER_ID
    r.claude_bash("rm -rf build/", request_id="req-rm", mode=mode)


def close_rm_by_hook(r):
    r.tap(CARD, "allow", uid=OWNER_ID)


# -- the rm first, the refused Write after ---------------------------------------------

@pytest.mark.parametrize("mode", MODES)
def test_waiting_auto_deny_types_nothing(drig, mode):
    rm_parked(drig, mode)
    write_request(drig)
    assert drig.keys == []


@pytest.mark.parametrize("mode", MODES)
def test_waiting_auto_deny_keeps_both_prompts_open(drig, mode):
    rm_parked(drig, mode)
    write_request(drig)
    assert len(drig.sess.open_prompts()) == 2


@pytest.mark.parametrize("mode", MODES)
def test_more_hooks_do_not_make_it_type(drig, mode):
    """No loop and no retry that types: other hooks keep arriving."""
    rm_parked(drig, mode)
    write_request(drig)
    for i in range(5):
        drig.hook("PostToolUse", tool_name="Read", tool_input={"file_path": f"/x{i}"},
                  tool_use_id=f"toolu_r{i}", agent_id="c9f8e7d6c5b4a3921",
                  agent_type="ship-reviewer")
    assert drig.keys == []


@pytest.mark.parametrize("mode", MODES)
def test_the_rm_closes_through_its_hook(drig, mode):
    rm_parked(drig, mode)
    write_request(drig)
    close_rm_by_hook(drig)
    assert [(r["request_id"], d["behavior"]) for r, d in drig.decisions] == [
        ("req-rm", "allow")]


@pytest.mark.parametrize("mode", MODES)
def test_once_the_rm_closes_the_auto_deny_types_one_escape(drig, mode):
    rm_parked(drig, mode)
    write_request(drig)
    close_rm_by_hook(drig)
    assert drig.keys == ["Escape"]


@pytest.mark.parametrize("mode", MODES)
def test_once_the_rm_closes_nothing_is_left_open(drig, mode):
    rm_parked(drig, mode)
    write_request(drig)
    close_rm_by_hook(drig)
    assert drig.sess.open_prompts() == []


@pytest.mark.parametrize("mode", MODES)
def test_the_escape_is_typed_only_once(drig, mode):
    rm_parked(drig, mode)
    write_request(drig)
    close_rm_by_hook(drig)
    drig.hook("PostToolUse", tool_name="Bash", tool_input={"command": "ls"},
              tool_use_id="toolu_after")
    assert drig.keys.count("Escape") == 1


# -- the refused Write first, behind a question; then the rm arrives -------------------

def write_behind_question_then_rm(r, mode):
    """Claude's question is on top; the refused Write waits behind it. The
    rm then opens. The question is answered in the terminal (its own
    PostToolUse closes it): the Write was older than the rm, so arrival
    order would put it on top, but the rm may have opened over it."""
    r.question()
    write_request(r)
    rm_parked(r, mode)
    r.hook("PostToolUse", tool_name="AskUserQuestion",
           tool_input=json.loads(json.dumps(rigmod.QUESTION)), tool_use_id="toolu_q1",
           tool_response={"answers": {}})


@pytest.mark.parametrize("mode", MODES)
def test_older_auto_deny_beside_a_later_rm_types_nothing(drig, mode):
    write_behind_question_then_rm(drig, mode)
    assert drig.keys == []


@pytest.mark.parametrize("mode", MODES)
def test_older_auto_deny_types_its_escape_when_the_rm_closes(drig, mode):
    write_behind_question_then_rm(drig, mode)
    close_rm = [m["msg_id"] for m in drig.prompt_messages()
                if m["msg_id"] != CARD][-1]
    drig.tap(close_rm, "allow", uid=OWNER_ID)
    assert drig.keys == ["Escape"]
