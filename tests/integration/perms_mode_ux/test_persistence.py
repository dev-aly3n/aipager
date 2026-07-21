"""Integration tests: SC1, SC2, SC3 — skip_perms persists in state file.

Black-box: we exercise the public registry save/load cycle and check the JSON
file content directly.  We do NOT read source except for the public
SessionRegistry / TrackedSession API listed in entrypoints.md.
"""

from __future__ import annotations

import json

import pytest

from aipager.state import SessionRegistry, Status, TrackedSession


# --------------------------------------------------------------------------- #
# SC1 — /new ben (Ask mode) persists skip_perms: false in state file          #
# --------------------------------------------------------------------------- #

def test_sc1_new_ask_session_persists_skip_perms_false(tmp_state_file):
    """After creating a session with skip_perms=False (Ask mode), the state
    file must contain skip_perms: false for that session entry."""
    r = SessionRegistry()
    r.transition("claude-ben", Status.IDLE)
    sess = r.get("claude-ben")
    sess.skip_perms = False
    r.save()

    raw = json.loads(tmp_state_file.read_text())
    assert "skip_perms" in raw["sessions"]["claude-ben"], (
        "skip_perms key must be present in persisted session"
    )
    assert raw["sessions"]["claude-ben"]["skip_perms"] is False, (
        "skip_perms must be false for Ask mode session"
    )


# --------------------------------------------------------------------------- #
# SC2 — /new !ben (Auto mode) persists skip_perms: true in state file         #
# --------------------------------------------------------------------------- #

def test_sc2_new_auto_session_persists_skip_perms_true(tmp_state_file):
    """After creating a session with skip_perms=True (Auto mode), the state
    file must contain skip_perms: true."""
    r = SessionRegistry()
    r.transition("claude-ben", Status.IDLE)
    sess = r.get("claude-ben")
    sess.skip_perms = True
    r.save()

    raw = json.loads(tmp_state_file.read_text())
    assert raw["sessions"]["claude-ben"]["skip_perms"] is True, (
        "skip_perms must be true for Auto mode session"
    )


# --------------------------------------------------------------------------- #
# SC3 — daemon restart (second SessionRegistry.load()) preserves value        #
# --------------------------------------------------------------------------- #

def test_sc3_restart_preserves_skip_perms_true(tmp_state_file):
    """On daemon restart (second load), skip_perms=True survives."""
    # First daemon writes
    r1 = SessionRegistry()
    r1.transition("claude-ben", Status.IDLE)
    r1.get("claude-ben").skip_perms = True
    r1.save()

    # Second daemon loads
    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-ben")
    assert s2 is not None
    assert s2.skip_perms is True, (
        "skip_perms must survive daemon restart (SessionRegistry.load)"
    )


def test_sc3_restart_preserves_skip_perms_false(tmp_state_file):
    """On daemon restart, skip_perms=False survives (not silently set True)."""
    r1 = SessionRegistry()
    r1.transition("claude-ben", Status.IDLE)
    r1.get("claude-ben").skip_perms = False
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-ben")
    assert s2 is not None
    assert s2.skip_perms is False


def test_sc3_old_state_file_without_skip_perms_defaults_to_false(tmp_state_file):
    """State files from before this feature (no skip_perms key) default to
    False (Ask mode) on load — no crash and no unintended Auto mode."""
    old = {
        "version": 1,
        "last_active_session": "claude-ben",
        "pinned_msg_id": 0,
        "msg_map": {},
        "sessions": {
            "claude-ben": {
                "name": "claude-ben",
                "label": "ben",
                "last_msg_id": None,
                "transcript_path": "",
                "trigger_msg_id": None,
                "pending_queue": [],
                "last_prompt": "",
                "model_name": "",
                "busy_msg_id": None,
                "created_by_user_id": None,
                "last_driver_user_id": None,
                "claude_session_id": "old-uuid",
                "cwd": "/home/user",
                "gone_at": 1234567890.0,
                "last_assistant_preview": "",
                "hidden_from_status": False,
                "scope_chat_id": 0,
                "scope_kind": "",
                # NO skip_perms key
            }
        },
    }
    tmp_state_file.write_text(json.dumps(old))

    r = SessionRegistry()
    r.load()
    s = r.get("claude-ben")
    assert s is not None
    assert s.skip_perms is False, (
        "Migration: missing skip_perms key must default to False (Ask mode)"
    )


# --------------------------------------------------------------------------- #
# Equivalence partition: multiple sessions with different modes                #
# --------------------------------------------------------------------------- #

def test_two_sessions_different_modes_persist_independently(tmp_state_file):
    """Two sessions with different modes must each persist their own value."""
    r = SessionRegistry()
    r.transition("claude-ask", Status.IDLE)
    r.transition("claude-auto", Status.IDLE)
    r.get("claude-ask").skip_perms = False
    r.get("claude-auto").skip_perms = True
    r.save()

    r2 = SessionRegistry()
    r2.load()
    assert r2.get("claude-ask").skip_perms is False
    assert r2.get("claude-auto").skip_perms is True
