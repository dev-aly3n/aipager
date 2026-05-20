"""Tests for aipager.state — SessionRegistry transition logic and persistence."""

import os
import time

from aipager.state import SessionRegistry, Status


def test_same_state_transition_returns_none(tmp_state_file):
    r = SessionRegistry()
    assert r.transition("claude-foo", Status.IDLE) is not None
    assert r.transition("claude-foo", Status.IDLE) is None


def test_busy_resets_idle_debounce_timer(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-foo", Status.BUSY)
    r.transition("claude-foo", Status.IDLE)
    r.transition("claude-foo", Status.BUSY)
    sess = r.get("claude-foo")
    assert sess.last_idle_at == 0.0


def test_idle_within_debounce_window_is_suppressed(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-foo", Status.BUSY)
    assert r.transition("claude-foo", Status.IDLE) is not None
    sess = r.get("claude-foo")
    # Simulate rapid cycling: manually mark BUSY + set last_idle_at recent
    sess.status = Status.BUSY
    sess.last_idle_at = time.monotonic() - 1.0
    # Now an IDLE should be debounced
    assert r.transition("claude-foo", Status.IDLE) is None
    # State still updates silently
    assert sess.status == Status.IDLE


def test_persistence_round_trip(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition("claude-bar", Status.IDLE)
    sess = r1.get("claude-bar")
    sess.transcript_path = "/some/path.jsonl"
    sess.last_prompt = "do the thing"
    r1.last_active_session = "claude-bar"
    r1.track_message(99, "claude-bar")  # track_message sets last_msg_id=99
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-bar")
    assert s2 is not None
    assert s2.last_msg_id == 99
    assert s2.transcript_path == "/some/path.jsonl"
    assert s2.last_prompt == "do the thing"
    assert r2.last_active_session == "claude-bar"
    assert r2.get_session_by_msg(99) is not None


def test_track_message_maps_to_session(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-foo", Status.IDLE)
    r.track_message(123, "claude-foo")
    s = r.get_session_by_msg(123)
    assert s is not None
    assert s.name == "claude-foo"
    assert r.get_session_by_msg(999) is None


def test_unknown_session_returns_none(tmp_state_file):
    r = SessionRegistry()
    assert r.get("does-not-exist") is None


# ----- 2.3 queue_prompt + TTL -----

def test_queue_prompt_appends_with_timestamp():
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-jim", label="jim")
    assert sess.queue_prompt("hello", 100) is True
    assert len(sess.pending_queue) == 1
    text, msg_id, ts = sess.pending_queue[0]
    assert text == "hello"
    assert msg_id == 100
    assert ts > 0


def test_queue_prompt_rejects_when_at_cap():
    from aipager.state import QUEUE_CAP, TrackedSession
    sess = TrackedSession(name="claude-jim", label="jim")
    for i in range(QUEUE_CAP):
        assert sess.queue_prompt(f"msg{i}", i) is True
    # 51st should be rejected
    assert sess.queue_prompt("overflow", QUEUE_CAP) is False
    assert len(sess.pending_queue) == QUEUE_CAP


def test_load_drops_expired_queue_entries(tmp_state_file):
    import json
    from aipager.state import QUEUE_MAX_AGE_SECONDS
    # Hand-roll a state file with one fresh + one ancient queue entry
    now = time.time()
    state = {
        "version": 1,
        "last_active_session": "",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-jim": {
                "name": "claude-jim",
                "label": "jim",
                "last_msg_id": None,
                "transcript_path": "",
                "trigger_msg_id": None,
                "pending_queue": [
                    ["fresh", 100, now - 60],          # 1 min old → kept
                    ["ancient", 101, now - QUEUE_MAX_AGE_SECONDS - 100],  # dropped
                ],
                "last_prompt": "",
                "model_name": "",
                "busy_msg_id": None,
            }
        }
    }
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    sess = r.get("claude-jim")
    assert sess is not None
    texts = [e[0] for e in sess.pending_queue]
    assert texts == ["fresh"]


def test_load_upgrades_legacy_2tuple_queue_entries(tmp_state_file):
    import json
    state = {
        "version": 1,
        "last_active_session": "",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-jim": {
                "name": "claude-jim",
                "label": "jim",
                "last_msg_id": None,
                "transcript_path": "",
                "trigger_msg_id": None,
                "pending_queue": [
                    ["legacy", 200],  # 2-element — old shape
                ],
                "last_prompt": "",
                "model_name": "",
                "busy_msg_id": None,
            }
        }
    }
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    sess = r.get("claude-jim")
    assert len(sess.pending_queue) == 1
    text, msg_id, ts = sess.pending_queue[0]
    assert text == "legacy"
    assert msg_id == 200
    assert ts > 0  # auto-timestamped to "now"


# ----- 2.4 record_tool cap + history_idx adjustment -----

def test_record_tool_appends_and_returns_index():
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-jim", label="jim")
    idx = sess.record_tool("Read", False)
    assert idx == 0
    assert sess.tool_history == [("Read", False)]


def test_record_tool_trims_to_cap():
    from aipager.state import TOOL_HISTORY_CAP, TrackedSession
    sess = TrackedSession(name="claude-jim", label="jim")
    for i in range(TOOL_HISTORY_CAP + 50):
        sess.record_tool(f"tool{i}", True)
    assert len(sess.tool_history) == TOOL_HISTORY_CAP
    # Oldest entries dropped; newest preserved
    assert sess.tool_history[-1] == (f"tool{TOOL_HISTORY_CAP + 49}", True)
    assert sess.tool_history[0] == ("tool50", True)


def test_record_tool_shifts_active_subagent_indices():
    from aipager.state import TOOL_HISTORY_CAP, TrackedSession
    sess = TrackedSession(name="claude-jim", label="jim")
    # Fill near the cap, then add a subagent entry referencing the last index
    for i in range(TOOL_HISTORY_CAP - 1):
        sess.record_tool(f"tool{i}", True)
    idx = sess.record_tool("agent", False)
    sess.active_subagents["agent-1"] = {
        "type": "explore", "started_at": 0.0, "history_idx": idx,
    }
    # Now push 5 more entries — should trigger trimming and shift history_idx
    for i in range(5):
        sess.record_tool(f"after{i}", True)
    # The active subagent's history_idx should now point at the new
    # (shifted) position of the "agent" entry, which is still in the list
    new_idx = sess.active_subagents["agent-1"]["history_idx"]
    assert sess.tool_history[new_idx] == ("agent", False)


# ----- Status not persisted (2.2 invariant lock-in) -----

def test_status_not_persisted_across_save_load(tmp_state_file):
    import json
    # Force a state file with a session in INTERACTIVE status. If status
    # were ever added to _PERSIST_FIELDS by mistake, this would break.
    state = {
        "version": 1,
        "last_active_session": "",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-jim": {
                "name": "claude-jim",
                "label": "jim",
                "status": "INTERACTIVE",      # would-be field
                "pending_permission": {       # would-be field
                    "tool_summary": "Bash"
                },
                "last_msg_id": None,
                "transcript_path": "",
                "trigger_msg_id": None,
                "pending_queue": [],
                "last_prompt": "",
                "model_name": "",
                "busy_msg_id": None,
            }
        }
    }
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    sess = r.get("claude-jim")
    # Status MUST come back as UNKNOWN (not INTERACTIVE) — session_monitor
    # will then transition to IDLE/GONE based on dtach socket presence.
    assert sess.status == Status.UNKNOWN
    # pending_permission MUST come back as None — never persisted.
    assert sess.pending_permission is None


# ----- Resume support: claude_session_id, cwd, gone_at, preview -----

def test_resume_fields_round_trip(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition("claude-jim", Status.IDLE)
    sess = r1.get("claude-jim")
    sess.claude_session_id = "e4f739a9-e19a-4d17-a8c2-12ba1b288907"
    sess.cwd = "/home/aly/project"
    sess.last_assistant_preview = "I have refactored the module."
    sess.gone_at = 1716230400.0
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-jim")
    assert s2.claude_session_id == "e4f739a9-e19a-4d17-a8c2-12ba1b288907"
    assert s2.cwd == "/home/aly/project"
    assert s2.last_assistant_preview == "I have refactored the module."
    assert s2.gone_at == 1716230400.0


def test_gone_at_present_loads_as_gone_status(tmp_state_file):
    """A session with a saved gone_at comes back GONE — not UNKNOWN.

    This prevents the session_monitor from re-firing "session_end" on
    every daemon restart for sessions that were already dead.
    """
    r1 = SessionRegistry()
    r1.transition("claude-jim", Status.IDLE)
    sess = r1.get("claude-jim")
    sess.gone_at = time.time()
    sess.claude_session_id = "abc-123"
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-jim")
    assert s2.status == Status.GONE


def test_alive_session_loads_as_unknown(tmp_state_file):
    """Sessions with no gone_at stamp keep the old UNKNOWN-on-load behavior."""
    r1 = SessionRegistry()
    r1.transition("claude-jim", Status.IDLE)
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    assert r2.get("claude-jim").status == Status.UNKNOWN


def test_hidden_from_status_round_trips(tmp_state_file):
    """`hidden_from_status` survives daemon restart via the state file."""
    r1 = SessionRegistry()
    r1.transition("claude-jim", Status.IDLE)
    sess = r1.get("claude-jim")
    sess.hidden_from_status = True
    sess.claude_session_id = "uuid-1"
    sess.gone_at = 1716230400.0
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-jim")
    assert s2.hidden_from_status is True
    # Resume metadata also preserved
    assert s2.claude_session_id == "uuid-1"


def test_transition_to_gone_stamps_gone_at(tmp_state_file):
    """Any path to GONE must stamp gone_at — not just session_monitor."""
    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    sess = r.get("claude-jim")
    assert sess.gone_at is None
    before = time.time()
    r.transition("claude-jim", Status.GONE)
    after = time.time()
    assert sess.gone_at is not None
    assert before <= sess.gone_at <= after


def test_transition_to_gone_preserves_existing_stamp(tmp_state_file):
    """If a caller already stamped gone_at, transition() respects it."""
    r = SessionRegistry()
    r.transition("claude-jim", Status.IDLE)
    sess = r.get("claude-jim")
    sess.gone_at = 1234567890.0  # explicit prior stamp
    r.transition("claude-jim", Status.GONE)
    assert sess.gone_at == 1234567890.0


def test_transition_idempotent_does_not_re_stamp(tmp_state_file):
    """transition() returns early for same-state — gone_at not re-stamped."""
    r = SessionRegistry()
    r.transition("claude-jim", Status.GONE)
    sess = r.get("claude-jim")
    original = sess.gone_at
    assert original is not None
    time.sleep(0.01)
    r.transition("claude-jim", Status.GONE)
    assert sess.gone_at == original  # untouched


def test_load_backfills_gone_at_from_transcript_mtime(tmp_state_file, tmp_path):
    """Sessions saved with claude_session_id but no gone_at (orphans from
    the pre-fix SessionEnd path) get gone_at backfilled from the
    transcript file's mtime."""
    import json
    transcript = tmp_path / "uuid.jsonl"
    transcript.write_text("{}\n")
    os.utime(transcript, (1700000000.0, 1700000000.0))

    state = {
        "sessions": {
            "claude-old": {
                "name": "claude-old",
                "label": "old",
                "claude_session_id": "uuid",
                "transcript_path": str(transcript),
                # NB: no gone_at
            },
        },
        "msg_map": {},
    }
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    s = r.get("claude-old")
    assert s.gone_at == 1700000000.0
    assert s.status == Status.GONE  # status follows gone_at presence


def test_load_marks_dirty_when_backfill_occurs(tmp_state_file, tmp_path):
    """Backfill in load() flips _dirty so the derived gone_at gets saved."""
    import json
    transcript = tmp_path / "uuid.jsonl"
    transcript.write_text("{}\n")

    state = {
        "sessions": {
            "claude-old": {
                "name": "claude-old",
                "label": "old",
                "claude_session_id": "uuid",
                "transcript_path": str(transcript),
            },
        },
        "msg_map": {},
    }
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    assert r._dirty is True  # ready to persist the backfilled gone_at


def test_load_no_backfill_when_transcript_missing(tmp_state_file):
    """If the transcript file isn't on disk, load() doesn't fabricate a
    gone_at — the session loads as UNKNOWN and session_monitor will
    stamp it later when it observes the missing socket."""
    import json
    state = {
        "sessions": {
            "claude-orphan": {
                "name": "claude-orphan",
                "label": "orphan",
                "claude_session_id": "uuid-x",
                "transcript_path": "/nope/missing.jsonl",
            },
        },
        "msg_map": {},
    }
    tmp_state_file.write_text(json.dumps(state))
    r = SessionRegistry()
    r.load()
    s = r.get("claude-orphan")
    assert s.gone_at is None
    assert s.status == Status.UNKNOWN


def test_hidden_from_status_defaults_false_on_legacy_state(tmp_state_file):
    """State files saved before this field existed load with the flag False."""
    import json
    legacy = {
        "sessions": {
            "claude-old": {
                "name": "claude-old",
                "label": "old",
                "claude_session_id": "uuid-x",
                "gone_at": 1716230400.0,
                # NB: no `hidden_from_status` key
            },
        },
        "msg_map": {},
    }
    tmp_state_file.write_text(json.dumps(legacy))
    r = SessionRegistry()
    r.load()
    assert r.get("claude-old").hidden_from_status is False


def test_max_gone_history_evicts_oldest(tmp_state_file):
    """Adding a new session past MAX_GONE_HISTORY drops the oldest GONE."""
    from aipager.state import MAX_GONE_HISTORY
    r = SessionRegistry()
    # Fill exactly to the cap with GONE entries, each older than the next.
    for i in range(MAX_GONE_HISTORY):
        name = f"claude-old{i}"
        r.transition(name, Status.GONE)
        r.get(name).gone_at = 1000.0 + i
    # Add an alive session — should not evict (only GONE counts).
    r.transition("claude-alive", Status.IDLE)
    assert len(r.all_sessions()) == MAX_GONE_HISTORY + 1

    # Now add one more GONE — oldest GONE (old0) must be evicted.
    r.transition("claude-fresh", Status.GONE)
    r.get("claude-fresh").gone_at = 2000.0
    # Trigger an explicit eviction (transition doesn't run it; only
    # get_or_create does — that's fine, the next get_or_create call
    # is the normal trigger path).
    r.get_or_create("claude-trigger")
    r.get("claude-trigger").gone_at = None  # not GONE; force-tagged below

    # After get_or_create, gone count > cap → eviction should have fired
    assert r.get("claude-old0") is None
    # Newer entries survive
    assert r.get("claude-fresh") is not None
    assert r.get("claude-alive") is not None
    # The non-GONE trigger session is never touched
    assert r.get("claude-trigger") is not None
