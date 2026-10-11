"""Integration tests: SC1, SC2, SC3 — the permission mode persists in the
state file (``perm_mode``, with the older ``skip_perms`` mirrored beside it
for a rollback: true only for Full access).

Black-box: we exercise the public registry save/load cycle and check the JSON
file content directly.  We do NOT read source except for the public
SessionRegistry / TrackedSession API listed in entrypoints.md.
"""

from __future__ import annotations

import json
import time


from aipager.state import SessionRegistry, Status


# --------------------------------------------------------------------------- #
# SC1 — an Ask session persists perm_mode ask (skip_perms: false)            #
# --------------------------------------------------------------------------- #

def test_sc1_new_ask_session_persists_skip_perms_false(tmp_state_file):
    """An Ask session's entry holds perm_mode "ask" and the legacy
    skip_perms false."""
    r = SessionRegistry()
    r.transition("claude-ben", Status.IDLE)
    sess = r.get("claude-ben")
    sess.perm_mode = "ask"
    r.save()

    raw = json.loads(tmp_state_file.read_text())
    assert "skip_perms" in raw["sessions"]["claude-ben"], (
        "skip_perms key must be present in persisted session"
    )
    assert raw["sessions"]["claude-ben"]["perm_mode"] == "ask"
    assert raw["sessions"]["claude-ben"]["skip_perms"] is False, (
        "skip_perms must be false for Ask mode session"
    )


# --------------------------------------------------------------------------- #
# SC2 — a Full access session persists perm_mode full (skip_perms: true)     #
# --------------------------------------------------------------------------- #

def test_sc2_new_auto_session_persists_skip_perms_true(tmp_state_file):
    """A Full access session's entry holds perm_mode "full" and the legacy
    skip_perms true (what an older daemon called Auto)."""
    r = SessionRegistry()
    r.transition("claude-ben", Status.IDLE)
    sess = r.get("claude-ben")
    sess.perm_mode = "full"
    r.save()

    raw = json.loads(tmp_state_file.read_text())
    assert raw["sessions"]["claude-ben"]["perm_mode"] == "full"
    assert raw["sessions"]["claude-ben"]["skip_perms"] is True, (
        "skip_perms must be true for a Full access session"
    )


# --------------------------------------------------------------------------- #
# SC3 — daemon restart (second SessionRegistry.load()) preserves value        #
# --------------------------------------------------------------------------- #

def test_sc3_restart_preserves_skip_perms_true(tmp_state_file):
    """On daemon restart (second load), mode="full" survives."""
    # First daemon writes
    r1 = SessionRegistry()
    r1.transition("claude-ben", Status.IDLE)
    r1.get("claude-ben").perm_mode = "full"
    r1.save()

    # Second daemon loads
    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-ben")
    assert s2 is not None
    assert s2.perm_mode == "full", (
        "skip_perms must survive daemon restart (SessionRegistry.load)"
    )


def test_sc3_restart_preserves_skip_perms_false(tmp_state_file):
    """On daemon restart, mode="ask" survives (not silently set True)."""
    r1 = SessionRegistry()
    r1.transition("claude-ben", Status.IDLE)
    r1.get("claude-ben").perm_mode = "ask"
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    s2 = r2.get("claude-ben")
    assert s2 is not None
    assert s2.perm_mode == "ask"


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
                "gone_at": time.time() - 3600.0,  # recent: load() ages out older ones (8.12)
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
    assert s.perm_mode == "ask", (
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
    r.get("claude-ask").perm_mode = "ask"
    r.get("claude-auto").perm_mode = "full"
    r.save()

    r2 = SessionRegistry()
    r2.load()
    assert r2.get("claude-ask").perm_mode == "ask"
    assert r2.get("claude-auto").perm_mode == "full"
