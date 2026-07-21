"""Tests for skip_perms field in TrackedSession and SessionRegistry persistence."""

from __future__ import annotations

import json

import pytest

from aipager.state import SessionRegistry, Status, TrackedSession


# ---- TrackedSession field ------------------------------------------------

def test_skip_perms_defaults_to_false():
    sess = TrackedSession(name="claude-dev", label="dev")
    assert sess.skip_perms is False


def test_skip_perms_can_be_set_true():
    sess = TrackedSession(name="claude-dev", label="dev", skip_perms=True)
    assert sess.skip_perms is True


# ---- _PERSIST_FIELDS -------------------------------------------------------

def test_skip_perms_in_persist_fields():
    assert "skip_perms" in SessionRegistry._PERSIST_FIELDS


# ---- round-trip save/load --------------------------------------------------

def test_skip_perms_false_round_trips(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition("claude-dev", Status.IDLE)
    sess = r1.get("claude-dev")
    sess.skip_perms = False
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-dev")
    assert s2 is not None
    assert s2.skip_perms is False


def test_skip_perms_true_round_trips(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition("claude-dev", Status.IDLE)
    sess = r1.get("claude-dev")
    sess.skip_perms = True
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-dev")
    assert s2 is not None
    assert s2.skip_perms is True


# ---- forward-compatibility: old JSON without skip_perms key ----------------

def test_load_old_json_without_skip_perms_defaults_to_false(tmp_state_file):
    """State files written by an older daemon that didn't know about skip_perms
    should load cleanly with skip_perms defaulting to False (Ask mode).
    """
    # Write a state file that looks like an old daemon's output (no skip_perms key)
    old_data = {
        "version": 1,
        "last_active_session": "claude-dev",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-dev": {
                "name": "claude-dev",
                "label": "dev",
                "last_msg_id": None,
                "transcript_path": "",
                "trigger_msg_id": None,
                "pending_queue": [],
                "last_prompt": "",
                "model_name": "",
                "busy_msg_id": None,
                "created_by_user_id": None,
                "last_driver_user_id": None,
                "claude_session_id": "some-uuid",
                "cwd": "/home/user",
                "gone_at": 1234567890.0,
                "last_assistant_preview": "",
                "hidden_from_status": False,
                # NO skip_perms key — simulates old daemon
                "scope_chat_id": 0,
                "scope_kind": "",
            }
        },
    }
    tmp_state_file.write_text(json.dumps(old_data))

    r = SessionRegistry()
    r.load()
    sess = r.get("claude-dev")
    assert sess is not None
    # Must default to False (Ask mode) when key is absent
    assert sess.skip_perms is False


def test_load_json_with_skip_perms_true(tmp_state_file):
    """State file that explicitly has skip_perms=true loads correctly."""
    data = {
        "version": 1,
        "last_active_session": "claude-dev",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-dev": {
                "name": "claude-dev",
                "label": "dev",
                "last_msg_id": None,
                "transcript_path": "",
                "trigger_msg_id": None,
                "pending_queue": [],
                "last_prompt": "",
                "model_name": "",
                "busy_msg_id": None,
                "created_by_user_id": None,
                "last_driver_user_id": None,
                "claude_session_id": "some-uuid",
                "cwd": "/home/user",
                "gone_at": 1234567890.0,
                "last_assistant_preview": "",
                "hidden_from_status": False,
                "skip_perms": True,
                "scope_chat_id": 0,
                "scope_kind": "",
            }
        },
    }
    tmp_state_file.write_text(json.dumps(data))

    r = SessionRegistry()
    r.load()
    sess = r.get("claude-dev")
    assert sess is not None
    assert sess.skip_perms is True
