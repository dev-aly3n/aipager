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
    r1.track_message(99, "claude-bar", 4242)  # track_message sets last_msg_id=99
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-bar")
    assert s2 is not None
    assert s2.last_msg_id == 99
    assert s2.transcript_path == "/some/path.jsonl"
    assert s2.last_prompt == "do the thing"
    assert r2.last_active_session == "claude-bar"
    assert r2.get_session_by_msg(99, 4242) is not None


def test_track_message_maps_to_session(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-foo", Status.IDLE)
    r.track_message(123, "claude-foo", 4242)
    s = r.get_session_by_msg(123, 4242)
    assert s is not None
    assert s.name == "claude-foo"
    assert r.get_session_by_msg(999, 4242) is None


def test_get_session_by_msg_scoped_to_chat_id_no_cross_chat_collision(tmp_state_file):
    """Criterion 8: the SAME message_id tracked in two different chats
    must resolve to each chat's OWN session — never the other chat's."""
    r = SessionRegistry()
    r.transition("claude-foo", Status.IDLE)
    r.transition("claude-bar", Status.IDLE)
    r.track_message(500, "claude-foo", 1111)
    r.track_message(500, "claude-bar", 2222)
    foo_side = r.get_session_by_msg(500, 1111)
    bar_side = r.get_session_by_msg(500, 2222)
    assert foo_side is not None and foo_side.name == "claude-foo"
    assert bar_side is not None and bar_side.name == "claude-bar"
    # And a THIRD, uninvolved chat_id must resolve to neither.
    assert r.get_session_by_msg(500, 3333) is None


def test_get_session_by_msg_wildcard_matches_unstamped_legacy_entry(tmp_state_file):
    """A stored chat_id of 0 (unstamped/legacy) is a wildcard — matches
    any calling chat_id, mirroring all_sessions()/find_by_label()."""
    r = SessionRegistry()
    r.transition("claude-legacy", Status.IDLE)
    r.track_message(700, "claude-legacy", 0)
    assert r.get_session_by_msg(700, 9999) is not None
    assert r.get_session_by_msg(700, 9999).name == "claude-legacy"


def test_unknown_session_returns_none(tmp_state_file):
    r = SessionRegistry()
    assert r.get("does-not-exist") is None


# ----- 2.3 queue_prompt + TTL -----

def test_queue_prompt_appends_with_timestamp():
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-jim", label="jim")
    assert sess.queue_prompt("hello", 100) is True
    assert len(sess.pending_queue) == 1
    text, msg_id, ts, reply_context, driver_user_id = sess.pending_queue[0]
    assert text == "hello"
    assert msg_id == 100
    assert ts > 0
    assert reply_context == ""
    assert driver_user_id is None


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
    text, msg_id, ts, reply_context, driver_user_id = sess.pending_queue[0]
    assert text == "legacy"
    assert msg_id == 200
    assert ts > 0  # auto-timestamped to "now"
    assert reply_context == ""
    assert driver_user_id is None


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


# ---- multi-scope: scope_chat_id / scope_kind (Phase B) -----------------

def test_scope_fields_round_trip(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition("claude-jim", Status.IDLE)
    sess = r1.get("claude-jim")
    sess.scope_chat_id = -4152307515
    sess.scope_kind = "group"
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-jim")
    assert s2.scope_chat_id == -4152307515
    assert s2.scope_kind == "group"


def test_backfill_scope_from_chat_id_dm(tmp_state_file, monkeypatch):
    """Legacy session (no scope_chat_id) backfills from config.CHAT_ID."""
    from aipager import config
    monkeypatch.setattr(config, "SCOPES", None, raising=False)
    monkeypatch.setattr(config, "CHAT_ID", "256113222")
    r1 = SessionRegistry()
    r1.transition("claude-jim", Status.IDLE)
    r1.save()
    # Ensure the saved record has no scope_chat_id (legacy shape).
    import json
    data = json.loads(tmp_state_file.read_text())
    data["sessions"]["claude-jim"].pop("scope_chat_id", None)
    data["sessions"]["claude-jim"].pop("scope_kind", None)
    tmp_state_file.write_text(json.dumps(data))

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-jim")
    assert s2.scope_chat_id == 256113222
    assert s2.scope_kind == "dm"   # positive → dm


def test_backfill_scope_kind_group_for_negative(tmp_state_file, monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "SCOPES", None, raising=False)
    monkeypatch.setattr(config, "CHAT_ID", "-100999")
    r1 = SessionRegistry()
    r1.transition("claude-jim", Status.IDLE)
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-jim")
    assert s2.scope_chat_id == -100999
    assert s2.scope_kind == "group"


def test_backfill_skipped_without_config(tmp_state_file, monkeypatch):
    from aipager import config
    monkeypatch.setattr(config, "SCOPES", None, raising=False)
    monkeypatch.setattr(config, "CHAT_ID", "")
    r1 = SessionRegistry()
    r1.transition("claude-jim", Status.IDLE)
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    assert r2.get("claude-jim").scope_chat_id == 0   # left unstamped, no crash


# ---- scoped lookup helpers (Phase C) -----------------------------------

def _mk(reg, name, label, scope, status=Status.IDLE):
    reg.transition(name, status)
    s = reg.get(name)
    s.label = label
    s.scope_chat_id = scope
    return s


def test_find_by_label_scoped(tmp_state_file):
    r = SessionRegistry()
    _mk(r, "claude-jim__d111", "jim", 111)
    _mk(r, "claude-jim__g-222", "jim", -222)
    # Same label, different scopes → resolve independently.
    assert r.find_by_label("jim", 111).name == "claude-jim__d111"
    assert r.find_by_label("jim", -222).name == "claude-jim__g-222"


def test_find_by_label_excludes_gone(tmp_state_file):
    r = SessionRegistry()
    _mk(r, "claude-jim__d111", "jim", 111, status=Status.GONE)
    assert r.find_by_label("jim", 111) is None
    assert r.find_by_label("jim", 111, include_gone=True) is not None


def test_find_by_label_grandfathered_flat(tmp_state_file):
    """A flat-named, unstamped (scope 0) session matches any scope."""
    r = SessionRegistry()
    _mk(r, "claude-tim", "tim", 0)
    assert r.find_by_label("tim", 256113222).name == "claude-tim"


def test_live_labels_scoped(tmp_state_file):
    r = SessionRegistry()
    _mk(r, "claude-a__d111", "a", 111)
    _mk(r, "claude-b__g-222", "b", -222)
    _mk(r, "claude-c__d111", "c", 111, status=Status.GONE)
    assert r.live_labels(111) == {"a"}
    assert r.live_labels(-222) == {"b"}
    assert r.live_labels() == {"a", "b"}  # all scopes, GONE excluded


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


# ---- per-session preference overrides (batch 4) -------------------------

def test_preference_overrides_empty_by_default(tmp_state_file):
    r = SessionRegistry()
    sess = r.get_or_create("claude-dev")
    assert sess.preference_overrides() == {}


def test_preference_override_fields_constant_covers_every_settable_field():
    """Derived from the settable-field allow-list rather than hand-listed,
    so adding a preference without its per-session override (or the
    reverse) fails here instead of silently leaving the Mini App's
    per-session toggle a no-op ("diff-preview-settings-toggle")."""
    from aipager.preferences import _FIELD_VALIDATORS
    from aipager.state import PREFERENCE_OVERRIDE_FIELDS
    assert set(PREFERENCE_OVERRIDE_FIELDS) == set(_FIELD_VALIDATORS)
    assert "diff_preview" in PREFERENCE_OVERRIDE_FIELDS


def test_preference_overrides_distinguishes_false_from_unset():
    """`simple_formatting=False` is a real, explicit override — it must
    show up in preference_overrides(), not be treated as if it were the
    None/unset default just because it's falsy."""
    r = SessionRegistry()
    sess = r.get_or_create("claude-dev")
    sess.override_simple_formatting = False
    overrides = sess.preference_overrides()
    assert "simple_formatting" in overrides
    assert overrides["simple_formatting"] is False


def test_preference_overrides_distinguishes_string_none_from_unset():
    """The string "none" (a real, selectable answer_length/language_level
    value meaning "no rule") must not be conflated with the field being
    unset — only Python None means unset."""
    r = SessionRegistry()
    sess = r.get_or_create("claude-dev")
    sess.override_answer_length = "none"
    overrides = sess.preference_overrides()
    assert "answer_length" in overrides
    assert overrides["answer_length"] == "none"


def test_preference_overrides_omits_unset_fields_entirely(tmp_state_file):
    r = SessionRegistry()
    sess = r.get_or_create("claude-dev")
    sess.override_answer_length = "short"
    overrides = sess.preference_overrides()
    assert "answer_length" in overrides
    # The three untouched fields are absent — not present with a None
    # value — matching what resolve_preferences expects (an absent key
    # falls back to scope; a present None key would need a second rule).
    assert "layout" not in overrides
    assert "simple_formatting" not in overrides
    assert "language_level" not in overrides


def test_override_fields_round_trip(tmp_state_file):
    r1 = SessionRegistry()
    sess = r1.get_or_create("claude-dev")
    sess.label = "dev"
    sess.override_layout = "merged"
    sess.override_simple_formatting = False
    sess.override_answer_length = "none"
    sess.override_language_level = "advanced"
    sess.override_diff_preview = True
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-dev")
    assert s2.override_layout == "merged"
    assert s2.override_simple_formatting is False
    assert s2.override_answer_length == "none"
    assert s2.override_language_level == "advanced"
    assert s2.override_diff_preview is True
    assert s2.preference_overrides() == {
        "layout": "merged",
        "simple_formatting": False,
        "answer_length": "none",
        "language_level": "advanced",
        "diff_preview": True,
    }


def test_unset_overrides_round_trip_as_none(tmp_state_file):
    """A session that never touched per-session settings persists and
    reloads with every override field still None, not e.g. missing keys
    that `.get()` would coerce into some other default."""
    r1 = SessionRegistry()
    r1.get_or_create("claude-dev")
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-dev")
    assert s2.override_layout is None
    assert s2.override_simple_formatting is None
    assert s2.override_answer_length is None
    assert s2.override_language_level is None
    assert s2.override_diff_preview is None
    assert s2.preference_overrides() == {}


def test_evict_then_recreate_same_name_has_no_overrides(tmp_state_file):
    """A label is reused across a session's whole lifetime (`/new`,
    a fresh `claude-<label>` after the old one went GONE and aged out of
    MAX_GONE_HISTORY). Overrides must not leak from one occupant of a name
    to the next — closed by construction: eviction pops the whole
    TrackedSession object (state.py's SessionRegistry._evict_gone_overflow),
    and get_or_create makes a brand-new one from dataclass defaults, not by
    a cleanup step that walks stale overrides and could be forgotten."""
    from aipager.state import MAX_GONE_HISTORY

    r = SessionRegistry()
    sess = r.get_or_create("claude-dev")
    sess.override_answer_length = "short"
    sess.override_simple_formatting = True
    assert sess.preference_overrides() == {
        "answer_length": "short", "simple_formatting": True,
    }
    r.transition("claude-dev", Status.GONE)
    r.get("claude-dev").gone_at = 1.0  # oldest — evicted first once over cap

    # MAX_GONE_HISTORY more, newer, GONE sessions push the total past the
    # cap so the next get_or_create's eviction pass fires and claude-dev
    # (the oldest) is the one dropped.
    for i in range(MAX_GONE_HISTORY):
        name = f"claude-filler{i}"
        r.transition(name, Status.GONE)
        r.get(name).gone_at = 1000.0 + i

    r.get_or_create("claude-trigger")
    assert r.get("claude-dev") is None  # confirms the object was evicted

    fresh = r.get_or_create("claude-dev")
    assert fresh is not sess
    assert fresh.preference_overrides() == {}
    assert fresh.override_answer_length is None
    assert fresh.override_simple_formatting is None


# ---- all_sessions scope filter (Phase G) --------------------------------

def _mk_scoped(r, name, scope_chat_id):
    s = r.get_or_create(name)
    s.scope_chat_id = scope_chat_id
    s.label = name.replace("claude-", "")
    return s


def test_all_sessions_no_arg_returns_everything():
    r = SessionRegistry()
    _mk_scoped(r, "claude-a", 100)
    _mk_scoped(r, "claude-b", -200)
    assert set(r.all_sessions()) == {"claude-a", "claude-b"}


def test_all_sessions_filters_by_scope():
    r = SessionRegistry()
    _mk_scoped(r, "claude-a", 100)
    _mk_scoped(r, "claude-b", -200)
    assert set(r.all_sessions(100)) == {"claude-a"}
    assert set(r.all_sessions(-200)) == {"claude-b"}


def test_all_sessions_includes_unstamped_legacy_session():
    r = SessionRegistry()
    _mk_scoped(r, "claude-a", 100)
    _mk_scoped(r, "claude-legacy", 0)  # not yet stamped → matches any scope
    assert set(r.all_sessions(100)) == {"claude-a", "claude-legacy"}
    assert set(r.all_sessions(-200)) == {"claude-legacy"}


# ---- job_background_open() — design.md "model Claude Code
# background-agent jobs". Four quadrants: {IDLE, BUSY} x
# {active_subagents empty, non-empty}, plus the two other statuses that
# must never read as job-open even with stale entries.

def test_job_background_open_idle_with_agents_is_true():
    r = SessionRegistry()
    sess = r.get_or_create("claude-hiva")
    sess.status = Status.IDLE
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert sess.job_background_open() is True


def test_job_background_open_busy_with_agents_is_true():
    r = SessionRegistry()
    sess = r.get_or_create("claude-hiva")
    sess.status = Status.BUSY
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert sess.job_background_open() is True


def test_job_background_open_idle_no_agents_is_false():
    r = SessionRegistry()
    sess = r.get_or_create("claude-hiva")
    sess.status = Status.IDLE
    assert sess.active_subagents == {}
    assert sess.job_background_open() is False


def test_job_background_open_busy_no_agents_is_false():
    r = SessionRegistry()
    sess = r.get_or_create("claude-hiva")
    sess.status = Status.BUSY
    assert sess.job_background_open() is False


def test_job_background_open_interactive_with_agents_is_false():
    """Even a stale active_subagents entry does not read as job-open for
    a status the predicate doesn't cover — INTERACTIVE/GONE/UNKNOWN are
    never a "waiting on background work" state."""
    r = SessionRegistry()
    sess = r.get_or_create("claude-hiva")
    sess.status = Status.INTERACTIVE
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert sess.job_background_open() is False


def test_job_background_open_gone_with_agents_is_false():
    r = SessionRegistry()
    sess = r.get_or_create("claude-hiva")
    sess.status = Status.GONE
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert sess.job_background_open() is False


# ---- transition(..., preserve_job_state=True) ----------------------------

def test_preserve_job_state_skips_busy_started_wall_stamp(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-hiva", Status.BUSY)
    sess = r.get("claude-hiva")
    original_wall = sess.busy_started_wall
    assert original_wall > 0
    r.transition("claude-hiva", Status.IDLE)
    r.transition("claude-hiva", Status.BUSY, preserve_job_state=True)
    assert sess.busy_started_wall == original_wall  # NOT restamped


def test_preserve_job_state_false_restamps_busy_started_wall(tmp_state_file):
    """The default (omitted / False) behaviour is unchanged — a genuinely
    new BUSY entry still restamps busy_started_wall."""
    r = SessionRegistry()
    r.transition("claude-hiva", Status.BUSY)
    sess = r.get("claude-hiva")
    original_wall = sess.busy_started_wall
    r.transition("claude-hiva", Status.IDLE)
    sess.busy_started_wall = 111.0  # sentinel, distinguishable from "now"
    r.transition("claude-hiva", Status.BUSY)  # preserve_job_state omitted
    assert sess.busy_started_wall != 111.0
    assert sess.busy_started_wall != original_wall


def test_preserve_job_state_true_still_skips_stamp_explicitly(tmp_state_file):
    r = SessionRegistry()
    r.transition("claude-hiva", Status.BUSY)
    sess = r.get("claude-hiva")
    r.transition("claude-hiva", Status.IDLE)
    sess.busy_started_wall = 222.0  # sentinel
    r.transition("claude-hiva", Status.BUSY, preserve_job_state=True)
    assert sess.busy_started_wall == 222.0  # untouched
