"""Tests for design.md's "reply context" feature at the state.py layer:
Part 0 (scoped msg_map + the two ORCHESTRATOR-VERIFIED bugs — serialisation
and load-order), the queue_prompt 4-tuple widening (Part 4), and the
GONE/kill cleanup hook (Part 5).

``policy_snapshot.snapshot_path`` / ``reply_context_path`` are monkeypatched
to ``tmp_path`` in every test that exercises real file writes, per the
project's "never write to a real /tmp/claude-* path" rule — a live daemon
runs on this machine.
"""

from __future__ import annotations

import json
import time

from aipager import policy_snapshot as ps
from aipager.state import SessionRegistry, Status, TrackedSession


def _isolate_tmp_files(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.reply.txt")


# ---- Part 0: serialisation bug (ORCHESTRATOR-VERIFIED) --------------------

def test_save_serializes_msg_map_key_as_chat_colon_msg_not_tuple_repr(tmp_state_file):
    """BUG 2: save() must write "<chat_id>:<msg_id>", never the Python
    tuple repr "(chat_id, msg_id)" that a naive ``str(k)`` would produce."""
    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    r.track_message(1234, "claude-jim", 256113222)
    r.save()
    raw = json.loads(tmp_state_file.read_text())
    assert "256113222:1234" in raw["msg_map"]
    assert "(256113222, 1234)" not in raw["msg_map"]
    assert raw["msg_map"]["256113222:1234"] == "claude-jim"


def test_save_serializes_negative_group_chat_id_correctly(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    r.track_message(500, "claude-jim", -1001234567890)
    r.save()
    raw = json.loads(tmp_state_file.read_text())
    assert "-1001234567890:500" in raw["msg_map"]


# ---- Part 0: load-order + migration bugs (ORCHESTRATOR-VERIFIED) ----------

def _state_with_legacy_msg_map(msg_map: dict, sessions: dict) -> dict:
    return {
        "version": 1,
        "last_active_session": "",
        "pinned_msg_id": 0,
        "msg_map": msg_map,
        "sessions": sessions,
    }


def _bare_session(name, label, *, scope_chat_id=0):
    return {
        "name": name,
        "label": label,
        "last_msg_id": None,
        "transcript_path": "",
        "trigger_msg_id": None,
        "pending_queue": [],
        "last_prompt": "",
        "model_name": "",
        "busy_msg_id": None,
        "scope_chat_id": scope_chat_id,
        "scope_kind": "dm" if scope_chat_id > 0 else ("group" if scope_chat_id else ""),
    }


def test_migration_resolves_legacy_bare_key_for_correct_chat_and_not_another(
    tmp_state_file,
):
    """Criterion 9, both directions: a pre-change state file with a bare
    int msg_map key migrates so the entry resolves for the owning
    session's chat_id and does NOT resolve for a different chat_id."""
    state = _state_with_legacy_msg_map(
        {"555": "claude-jim"},
        {"claude-jim": _bare_session("claude-jim", "jim", scope_chat_id=4242)},
    )
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    resolved = r.get_session_by_msg(555, 4242)
    assert resolved is not None and resolved.name == "claude-jim"
    assert r.get_session_by_msg(555, 9999) is None


def test_migration_runs_after_sessions_are_reconstructed_not_before(tmp_state_file):
    """BUG 1 (load-order): the migration needs sess.scope_chat_id, which
    only exists once the sessions loop below it has run. This is the
    same assertion as the test above, named to point directly at the
    load-order requirement — if the msg_map rebuild ran BEFORE the
    sessions loop (as the pre-fix code did), self._sessions would still
    be empty when the migration looked up "claude-jim", and this would
    silently drop the entry instead of resolving it."""
    state = _state_with_legacy_msg_map(
        {"777": "claude-old"},
        {"claude-old": _bare_session("claude-old", "old", scope_chat_id=1111)},
    )
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    assert r.get_session_by_msg(777, 1111) is not None


def test_migration_drops_entry_when_owning_session_missing(tmp_state_file):
    state = _state_with_legacy_msg_map(
        {"999": "claude-ghost"},  # no "claude-ghost" in sessions
        {},
    )
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()  # must not raise
    assert len(r._msg_map) == 0


def test_migration_drops_entry_when_owning_session_never_stamped(
    tmp_state_file, monkeypatch,
):
    """scope_chat_id == 0 (never stamped, no default scope to backfill
    from) — cannot attribute safely, so the entry is dropped. Must patch
    away the ambient real scope config (this machine has one configured
    for the live daemon), matching the existing
    test_backfill_skipped_without_config convention in test_state.py."""
    from aipager import config
    monkeypatch.setattr(config, "SCOPES", None, raising=False)
    monkeypatch.setattr(config, "CHAT_ID", "")
    state = _state_with_legacy_msg_map(
        {"888": "claude-unstamped"},
        {"claude-unstamped": _bare_session("claude-unstamped", "u", scope_chat_id=0)},
    )
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    assert r.get_session_by_msg(888, 0) is None
    assert len(r._msg_map) == 0


def test_migration_new_shape_splits_on_first_colon_for_negative_chat_id(tmp_state_file):
    """BUG 3: a group chat_id is negative (contains '-') but never
    contains ':' — splitting on the FIRST ':' must not be confused by
    the leading '-'."""
    state = _state_with_legacy_msg_map(
        {"-1001234567890:42": "claude-grp"},
        {"claude-grp": _bare_session("claude-grp", "grp", scope_chat_id=-1001234567890)},
    )
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    resolved = r.get_session_by_msg(42, -1001234567890)
    assert resolved is not None and resolved.name == "claude-grp"


def test_migration_ignores_malformed_keys_without_crashing(tmp_state_file):
    state = _state_with_legacy_msg_map(
        {"not-an-int": "claude-jim", "4242:not-an-int": "claude-jim"},
        {"claude-jim": _bare_session("claude-jim", "jim", scope_chat_id=4242)},
    )
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()  # must not raise
    assert len(r._msg_map) == 0


# ---- Part 0: cross-chat collision at the registry layer -------------------

def test_get_session_by_msg_never_resolves_a_colliding_id_into_the_wrong_chat(
    tmp_state_file,
):
    """Criterion 8. A test that never actually creates a colliding id in
    two chats proves nothing — this one does: the SAME message_id (500)
    is tracked in two different chats, each owned by a different
    session, and each chat must resolve only to its own session."""
    r = SessionRegistry()
    r.transition("claude-a", Status.IDLE)
    r.transition("claude-b", Status.IDLE)
    r.track_message(500, "claude-a", 111)
    r.track_message(500, "claude-b", 222)
    a = r.get_session_by_msg(500, 111)
    b = r.get_session_by_msg(500, 222)
    assert a is not None and a.name == "claude-a"
    assert b is not None and b.name == "claude-b"
    assert a.name != b.name
    assert r.get_session_by_msg(500, 333) is None  # uninvolved third chat


# ---- Part 1: _MAX_MSG_MAP bump ---------------------------------------------

def test_max_msg_map_raised_to_2000(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    for i in range(2001):
        r.track_message(i, "claude-jim", 1)
    r.save()
    raw = json.loads(tmp_state_file.read_text())
    assert len(raw["msg_map"]) == 2000
    # Most recent entries kept (insertion order), oldest evicted.
    assert "1:0" not in raw["msg_map"]
    assert "1:2000" in raw["msg_map"]


# ---- Part 4: queue_prompt 4-tuple + save/load widening ---------------------

def test_queue_prompt_default_reply_context_is_empty_string():
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("hi", 1)
    assert sess.pending_queue[0][3] == ""


def test_queue_prompt_stores_reply_context_when_given():
    sess = TrackedSession(name="claude-jim", label="jim")
    sess.queue_prompt("hi", 1, "pointing at an older message")
    assert sess.pending_queue[0] == ("hi", 1, sess.pending_queue[0][2],
                                     "pointing at an older message")


def test_save_widens_legacy_2_and_3_tuple_queue_entries_to_4(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    sess = r.get("claude-jim")
    now = time.time()
    sess.pending_queue.append(("legacy2", 1))          # 2-tuple
    sess.pending_queue.append(("legacy3", 2, now))       # 3-tuple
    sess.pending_queue.append(("current", 3, now, "ctx"))  # 4-tuple
    r.save()
    raw = json.loads(tmp_state_file.read_text())
    pq = raw["sessions"]["claude-jim"]["pending_queue"]
    assert pq[0] == ["legacy2", 1, pq[0][2], ""]
    assert pq[1] == ["legacy3", 2, now, ""]
    assert pq[2] == ["current", 3, now, "ctx"]


def test_load_upgrades_legacy_3tuple_queue_entries_with_empty_reply_context(
    tmp_state_file,
):
    now = time.time()
    state = {
        "version": 1, "last_active_session": "", "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-jim": {
                **_bare_session("claude-jim", "jim"),
                "pending_queue": [["hi", 100, now]],  # pre-Part-4 3-tuple
            },
        },
    }
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    sess = r.get("claude-jim")
    assert sess.pending_queue == [("hi", 100, now, "")]


def test_load_guards_non_string_reply_context_fails_safe_to_empty(tmp_state_file):
    now = time.time()
    state = {
        "version": 1, "last_active_session": "", "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-jim": {
                **_bare_session("claude-jim", "jim"),
                "pending_queue": [["hi", 100, now, 12345]],  # hand-edited garbage
            },
        },
    }
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()  # must not raise
    sess = r.get("claude-jim")
    assert sess.pending_queue == [("hi", 100, now, "")]


# ---- Part 5: GONE / kill cleanup -------------------------------------------

def test_transition_to_gone_removes_both_tmp_files(tmp_state_file, tmp_path, monkeypatch):
    _isolate_tmp_files(monkeypatch, tmp_path)
    ps.write_snapshot("claude-jim", None, None, None, style_text="s")
    ps.write_reply_context_file("claude-jim", "hdr", "full text")
    assert ps.snapshot_path("claude-jim").exists()
    assert ps.reply_context_path("claude-jim").exists()

    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    r.transition("claude-jim", Status.GONE)

    assert not ps.snapshot_path("claude-jim").exists()
    assert not ps.reply_context_path("claude-jim").exists()


def test_kill_via_registry_remove_removes_both_tmp_files(tmp_state_file, tmp_path, monkeypatch):
    _isolate_tmp_files(monkeypatch, tmp_path)
    ps.write_snapshot("claude-jim", None, None, None, style_text="s")
    ps.write_reply_context_file("claude-jim", "hdr", "full text")

    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    r.remove("claude-jim")

    assert not ps.snapshot_path("claude-jim").exists()
    assert not ps.reply_context_path("claude-jim").exists()


def test_gone_transition_cleanup_failure_does_not_block_the_transition(
    tmp_state_file, monkeypatch,
):
    def _boom(name):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(ps, "clear_session_files", _boom)

    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    result = r.transition("claude-jim", Status.GONE)  # must not raise
    assert result is not None
    assert r.get("claude-jim").status == Status.GONE


def test_kill_cleanup_failure_does_not_block_remove(tmp_state_file, monkeypatch):
    def _boom(name):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(ps, "clear_session_files", _boom)

    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    r.remove("claude-jim")  # must not raise
    assert r.get("claude-jim") is None


def test_gone_transition_cleanup_is_idempotent_no_error_on_repeat(
    tmp_state_file, tmp_path, monkeypatch,
):
    """The idempotency guard (same-state calls return early) means a
    second GONE call is a no-op — cleanup must not be attempted (or
    error) twice."""
    _isolate_tmp_files(monkeypatch, tmp_path)
    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    assert r.transition("claude-jim", Status.GONE) is not None
    assert r.transition("claude-jim", Status.GONE) is None  # no-op, no crash
