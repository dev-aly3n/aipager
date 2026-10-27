"""A tool refused by a role's rules is denied by aipager itself (fixes-2,
rev-iter1-003; roadmap 8.126): through its waiting hook when there is one
(it types nothing), else with the Deny button's key, one Escape (never a
count of Downs: Claude Code's menus wrap, so Downs land on an allow row of
some dialog shape). An extra prompt without a hook waits until its dialog
is on top, then types that key.

The oracle for "the Deny button's keys" is what a Deny tap types on a
lone keystroke-answered prompt in the same rig.
"""

from __future__ import annotations

import pytest

import tests.two_prompts_rig as rigmod
from aipager.scope import Member, Scope
from aipager.state import Status
from tests.two_prompts_rig import ALY, BG, BOB, CARD, GROUP, Rig

WRITE = {"file_path": "/src/a.py", "content": "x"}


def deny_write_scopes():
    return [Scope(chat_id=GROUP, kind="group", label="team", deny_tools=("Write",),
                  members=(Member(id=ALY, label="alice", role="owner"),
                           Member(id=BOB, label="bob", role="user")))]


@pytest.fixture
def drig(mk_bot, monkeypatch, run_async):
    """The rig with the group's rules refusing Write for a user's turn."""
    monkeypatch.setattr(rigmod, "scopes", deny_write_scopes)
    return Rig(mk_bot, monkeypatch, run_async)


def deny_tap_keys(r) -> list[str]:
    """Keys of a Deny tap (by the owner) on a lone Claude Edit prompt
    with no hook. Leaves the session BUSY with nothing typed recorded."""
    r.hook("PermissionRequest", tool_name="Edit",
           tool_input={"file_path": "/src/b.py", "old_string": "a", "new_string": "b"},
           tool_use_id="toolu_e1")
    r.tap(CARD, "deny", uid=ALY)
    keys = list(r.keys)
    r.keys.clear()
    return keys


def write_request(r, *, hook=False, tool_use_id="toolu_w1"):
    fields = {"tool_name": "Write", "tool_input": WRITE, "tool_use_id": tool_use_id}
    if hook:
        fields.update(aipager_reply_addr="/nonexistent/reply-w.sock",
                      aipager_request_id="req-w")
    r.hook("PermissionRequest", **fields)


def users_turn(r):
    r.sess.last_driver_user_id = BOB


# -- the lone (primary) prompt ----------------------------------------------------------------

def test_auto_deny_types_the_deny_buttons_keys(drig):
    tap = deny_tap_keys(drig)
    users_turn(drig)
    write_request(drig)
    assert drig.keys == tap


def test_auto_deny_is_not_a_single_down(drig):
    users_turn(drig)
    write_request(drig)
    assert drig.keys != ["Down", "Enter"]


def test_auto_deny_moves_no_cursor(drig):
    """Claude Code's menus wrap: any count of Downs lands on an allow row of
    some dialog shape (five reach "always allow" on a four-row Bash prompt).
    The refusal moves no cursor (roadmap 8.126)."""
    users_turn(drig)
    write_request(drig)
    assert "Down" not in drig.keys and "Up" not in drig.keys


def test_auto_deny_is_one_escape(drig):
    users_turn(drig)
    write_request(drig)
    assert drig.keys == ["Escape"]


def test_auto_deny_with_a_waiting_hook_denies_by_hook_and_types_nothing(drig):
    """The lone prompt's hook still waits: the refusal goes through it, as
    the Deny button's does, and no key reaches the screen."""
    users_turn(drig)
    write_request(drig, hook=True)
    assert ([(r["request_id"], d["behavior"]) for r, d in drig.decisions], drig.keys) == (
        [("req-w", "deny")], [])
    assert drig.sess.status == Status.BUSY


def test_owners_turn_is_not_auto_denied(drig):
    """Control: the rule is the user's; the owner's turn types nothing."""
    drig.sess.last_driver_user_id = ALY
    write_request(drig)
    assert drig.keys == []


# -- an extra prompt -------------------------------------------------------------------------------

def test_extra_auto_deny_with_parked_hook_denies_by_hook(drig):
    users_turn(drig)
    drig.question()
    write_request(drig, hook=True)
    assert [(r["request_id"], d["behavior"]) for r, d in drig.decisions] == [("req-w", "deny")]


def test_extra_auto_deny_with_parked_hook_types_nothing(drig):
    users_turn(drig)
    drig.question()
    write_request(drig, hook=True)
    assert drig.keys == []


def test_extra_auto_deny_with_parked_hook_leaves_the_question_open(drig):
    users_turn(drig)
    drig.question()
    write_request(drig, hook=True)
    assert [p["tool_info"]["name"] for p in drig.sess.open_prompts()] == ["AskUserQuestion"]


def test_extra_auto_deny_without_hook_types_nothing_while_behind(drig):
    users_turn(drig)
    drig.question()
    write_request(drig)
    assert drig.keys == []


def test_extra_auto_deny_without_hook_keeps_the_session_waiting(drig):
    users_turn(drig)
    drig.question()
    write_request(drig)
    assert drig.sess.status == Status.INTERACTIVE


def test_deferred_auto_deny_types_the_deny_keys_once_on_top(drig):
    tap = deny_tap_keys(drig)
    users_turn(drig)
    drig.question()
    write_request(drig)
    drig.tap(CARD, "opt0", uid=ALY)
    assert drig.keys[-len(tap):] == tap


def test_deferred_auto_deny_leaves_nothing_open(drig):
    users_turn(drig)
    drig.question()
    write_request(drig)
    drig.tap(CARD, "opt0", uid=ALY)
    assert drig.sess.open_prompts() == []


def test_agent_extra_auto_deny_by_hook_reaches_its_request(drig):
    """An agent's refused tool beside Claude's question: denied through
    its own hook, typing nothing into Claude's question."""
    users_turn(drig)
    drig.launch(BG)
    drig.question()
    drig.hook("PermissionRequest", tool_name="Write", tool_input=WRITE, agent_id=BG,
              agent_type="ship-reviewer", aipager_reply_addr="/nonexistent/reply-bg.sock",
              aipager_request_id="req-bg-w")
    assert ([(r["request_id"], d["behavior"]) for r, d in drig.decisions], drig.keys) == (
        [("req-bg-w", "deny")], [])
