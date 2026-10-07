"""The record of prompt surfaces whose prompt is known closed (roadmap
8.102): ``TrackedSession.closed_prompt_msgs``.

Pinned here: it is bounded and keeps the newest ids, a prompt shown again
takes its surface off, it reads back through the real state file (only
positive ints), a chat migration forgets it, and an invalid saved
prompt's card and a card prompt the file could not carry are closed after
a restart.
"""

from __future__ import annotations

import json
import time

import pytest

from aipager import state
from aipager.state import (
    MAX_CLOSED_PROMPT_SURFACES, SessionRegistry, Status, TrackedSession,
)

CHAT = 256113222
NAME = "claude-closedprompt"


def _sess() -> TrackedSession:
    return TrackedSession(name=NAME, label="op")


# ── the record itself ────────────────────────────────────────────────────

def test_mark_and_reopen():
    s = _sess()
    s.mark_prompt_closed(41)
    assert s.prompt_known_closed(41)
    s.reopen_prompt_surface(41)
    assert not s.prompt_known_closed(41)


@pytest.mark.parametrize("bad", [0, -1, None, "41", 41.0, True])
def test_only_positive_ints_are_recorded(bad):
    s = _sess()
    s.mark_prompt_closed(bad)
    assert s.closed_prompt_msgs == []
    assert not s.prompt_known_closed(bad)


def test_the_record_is_bounded_and_keeps_the_newest():
    s = _sess()
    for m in range(1, MAX_CLOSED_PROMPT_SURFACES + 6):
        s.mark_prompt_closed(m)
    assert len(s.closed_prompt_msgs) == MAX_CLOSED_PROMPT_SURFACES
    assert not s.prompt_known_closed(1)
    assert s.prompt_known_closed(MAX_CLOSED_PROMPT_SURFACES + 5)


def test_marking_again_moves_an_id_to_the_newest():
    s = _sess()
    s.mark_prompt_closed(1)
    for m in range(2, MAX_CLOSED_PROMPT_SURFACES + 1):
        s.mark_prompt_closed(m)
    s.mark_prompt_closed(1)  # newest again
    s.mark_prompt_closed(999)
    assert s.prompt_known_closed(1)
    assert not s.prompt_known_closed(2)


def test_a_chat_migration_forgets_the_record():
    s = _sess()
    s.mark_prompt_closed(41)
    s.forget_chat_messages()
    assert s.closed_prompt_msgs == []


# ── through the state file ───────────────────────────────────────────────

def _saved_and_loaded(build, edit=None) -> TrackedSession:
    reg = SessionRegistry()
    sess = reg.get_or_create(NAME)
    sess.label = "op"
    sess.scope_chat_id, sess.scope_kind = CHAT, "dm"
    build(reg, sess)
    reg.save()
    if edit is not None:
        data = json.loads(state.SESSION_STATE_FILE.read_text())
        edit(data["sessions"][NAME])
        state.SESSION_STATE_FILE.write_text(json.dumps(data))
    fresh = SessionRegistry()
    fresh.load()
    return fresh.get(NAME)


def test_the_record_survives_a_restart():
    def build(reg, sess):
        sess.mark_prompt_closed(41)
        sess.mark_prompt_closed(42)

    assert _saved_and_loaded(build).closed_prompt_msgs == [41, 42]


def test_a_hostile_saved_record_loads_only_positive_ints():
    def edit(sd):
        sd["closed_prompt_msgs"] = [41, "42", -3, 0, None, 4.5, True,
                                    [7], 43]

    assert _saved_and_loaded(lambda r, s: None, edit).closed_prompt_msgs \
        == [41, 43]


@pytest.mark.parametrize("raw", ["41", 41, {"a": 1}, None])
def test_a_saved_record_of_another_type_loads_empty(raw):
    def edit(sd):
        sd["closed_prompt_msgs"] = raw

    try:
        loaded = _saved_and_loaded(lambda r, s: None, edit).closed_prompt_msgs
    except Exception as exc:  # noqa: BLE001 - the guard under test
        loaded = f"raised {type(exc).__name__}"
    assert loaded == []


def test_a_long_saved_record_loads_bounded():
    def edit(sd):
        sd["closed_prompt_msgs"] = list(range(1, 100))

    loaded = _saved_and_loaded(lambda r, s: None, edit).closed_prompt_msgs
    assert loaded == list(range(100 - MAX_CLOSED_PROMPT_SURFACES, 100))


def _inline_wait(reg, sess, card: int = 77, **info_over) -> None:
    reg.transition(NAME, Status.BUSY)
    reg.transition(NAME, Status.INTERACTIVE)
    sess.busy_msg_id = card
    info = {"name": "Bash", "input": {"command": "ls"}, "summary": "Bash: ls",
            "always_available": False, "standing_rule_suggestion": None,
            "detail": "", "tool_use_id": "toolu_1"}
    info.update(info_over)
    sess.pending_permission = {
        "tool_summary": "Bash: ls", "tool_info": info,
        "wait_started_at": time.monotonic(), "shown_wall": time.time() - 5,
        "hook_reply": None}


def test_a_saved_prompt_does_not_close_its_card():
    loaded = _saved_and_loaded(lambda r, s: _inline_wait(r, s))
    assert loaded.restored_open_prompt is not None  # premise: it was saved
    assert not loaded.prompt_known_closed(77)


def test_an_unsaved_card_prompt_is_closed_after_a_restart():
    """A subagent's prompt is never saved: a restart cannot bring it back,
    so its card's old buttons type nothing after one."""
    loaded = _saved_and_loaded(
        lambda r, s: _inline_wait(r, s, agent_id="a1agent"))
    assert loaded.restored_open_prompt is None  # premise: not saved
    assert loaded.prompt_known_closed(77)


def test_an_unsaved_card_prompt_stays_open_in_the_running_daemon():
    reg = SessionRegistry()
    sess = reg.get_or_create(NAME)
    sess.scope_chat_id, sess.scope_kind = CHAT, "dm"
    _inline_wait(reg, sess, agent_id="a1agent")
    reg.save()
    assert not sess.prompt_known_closed(77)


def test_an_invalid_saved_prompt_closes_its_card():
    def edit(sd):
        sd["open_prompt"]["v"] = 99

    loaded = _saved_and_loaded(lambda r, s: _inline_wait(r, s), edit)
    assert loaded.restored_open_prompt is None  # premise: ignored
    assert loaded.prompt_known_closed(77)


def test_an_invalid_separate_record_does_not_close_the_card():
    def edit(sd):
        sd["open_prompt"] = {"v": 99, "kind": "separate"}

    loaded = _saved_and_loaded(lambda r, s: _inline_wait(r, s), edit)
    assert not loaded.prompt_known_closed(77)


def test_a_stale_busy_prompt_is_not_written_closed():
    """A BUSY session still holding ``pending_permission`` (a stale BUSY,
    or the hook-deadline race) on its busy card: the dialog may still be
    up, so the file must not list the card as closed (review
    rev-iter3-001); after a restart its Allow still types."""
    def build(reg, sess):
        reg.transition(NAME, Status.BUSY)
        sess.busy_msg_id = 77
        sess.pending_permission = {
            "tool_summary": "Bash: ls",
            "tool_info": {"name": "Bash", "input": {"command": "ls"},
                          "summary": "Bash: ls", "tool_use_id": "toolu_1"},
            "wait_started_at": time.monotonic(),
            "shown_wall": time.time() - 5, "hook_reply": None}
        assert sess.status is Status.BUSY  # premise

    seen = {}

    def edit(sd):
        seen.update(sd)

    loaded = _saved_and_loaded(build, edit)
    # The premise: the card was saved, and no prompt with it.
    assert seen["busy_msg_id"] == 77
    assert "open_prompt" not in seen
    assert 77 not in seen.get("closed_prompt_msgs", [])
    assert not loaded.prompt_known_closed(77)
