"""Tests for aipager.state — SessionRegistry transition logic and persistence."""

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
