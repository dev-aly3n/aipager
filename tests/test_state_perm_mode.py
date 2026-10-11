"""``TrackedSession.perm_mode`` (Ask / Auto / Full access): persistence and
the migration from ``skip_perms``, and the transient state that tracks
what Claude Code reports (aipager/perm_modes.py, aipager/state.py)."""

from __future__ import annotations

import json
import time

import pytest

from aipager import perm_modes
from aipager.state import ModeSwitch, SessionRegistry, Status, TrackedSession


def _sess(mode="ask", **kw):
    s = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE, **kw)
    s.perm_mode = mode
    return s


# ---- the field ---------------------------------------------------------------

def test_a_session_aipager_did_not_launch_reads_as_ask():
    assert TrackedSession(name="claude-dev", label="dev").perm_mode == "ask"


def test_perm_mode_is_persisted_and_skip_perms_is_not_a_field():
    assert "perm_mode" in SessionRegistry._PERSIST_FIELDS
    assert "skip_perms" not in SessionRegistry._PERSIST_FIELDS
    assert not hasattr(TrackedSession(name="claude-dev", label="dev"), "skip_perms")


# ---- round trip and the legacy mirror ----------------------------------------

@pytest.mark.parametrize("mode, mirror", [("ask", False), ("auto", False), ("full", True)],
                         ids=["ask", "auto", "full"])
def test_each_mode_round_trips_with_a_narrow_legacy_mirror(tmp_state_file, mode, mirror):
    """The file carries the mode and, for an older daemon after a rollback,
    the old boolean: true only for Full access (what it called Auto), so
    Ask and Auto read as its Ask, never wider."""
    r1 = SessionRegistry()
    r1.transition("claude-dev", Status.IDLE)
    r1.get("claude-dev").perm_mode = mode
    r1.save()

    raw = json.loads(tmp_state_file.read_text())["sessions"]["claude-dev"]
    assert (raw["perm_mode"], raw["skip_perms"]) == (mode, mirror)

    r2 = SessionRegistry()
    r2.load()
    assert r2.get("claude-dev").perm_mode == mode


def test_the_transient_mode_state_is_never_saved(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition("claude-dev", Status.IDLE)
    s = r1.get("claude-dev")
    s.reported_mode, s.auto_unavailable = "default", True
    s.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=1.0)
    r1.save()
    raw = json.loads(tmp_state_file.read_text())["sessions"]["claude-dev"]
    assert not {"reported_mode", "auto_unavailable", "mode_switch",
                "mode_launch_unchecked"} & set(raw)


def _old_file(tmp_state_file, **mode_keys):
    tmp_state_file.write_text(json.dumps({
        "version": 1, "last_active_session": "claude-dev", "msg_map": {},
        "sessions": {"claude-dev": {
            "name": "claude-dev", "label": "dev", "pending_queue": [],
            "claude_session_id": "some-uuid", "cwd": "/home/user",
            # recent: load() ages out older ones (8.12)
            "gone_at": time.time() - 3600.0,
            "scope_chat_id": 0, "scope_kind": "", **mode_keys,
        }},
    }))


@pytest.mark.parametrize("keys, want", [
    ({}, "ask"),
    ({"skip_perms": False}, "ask"),
    ({"skip_perms": True}, "full"),
    ({"perm_mode": "auto", "skip_perms": False}, "auto"),
    ({"perm_mode": "ask", "skip_perms": True}, "ask"),
    ({"perm_mode": "bogus", "skip_perms": True}, "full"),
    ({"perm_mode": True}, "ask"),
    ({"skip_perms": "yes"}, "ask"),
], ids=["no-key", "legacy-false", "legacy-true", "new-key-auto", "new-key-wins",
        "invalid-new-key", "bool-new-key", "non-bool-legacy"])
def test_an_older_file_migrates(tmp_state_file, keys, want):
    """An old daemon's skip_perms True was Auto that bypassed every check:
    Full access now. Anything else is Ask, what its owner picked."""
    _old_file(tmp_state_file, **keys)
    r = SessionRegistry()
    r.load()
    assert r.get("claude-dev").perm_mode == want


# ---- what Claude Code reports ------------------------------------------------

@pytest.mark.parametrize("raw, verdict", [
    ("default", "recorded"), ("manual", "recorded"), ("auto", "recorded"),
    ("bypassPermissions", "recorded"), ("plan", "recorded"), ("acceptEdits", "recorded"),
    ("dontAsk", "recorded"), ("", "ignored"), ("weird", "ignored"), (None, "ignored"),
    (3, "ignored"),
], ids=["default", "manual", "auto", "bypass", "plan", "accept-edits", "dont-ask", "empty",
        "unknown", "none", "int"])
def test_note_reported_mode_records_only_real_modes(raw, verdict):
    s = _sess()
    assert s.note_reported_mode(raw, 100.0) == verdict
    assert s.reported_mode == (raw if verdict == "recorded" else "")


def test_a_hook_never_changes_perm_mode():
    """The hook socket is world-writable: a forged bypassPermissions is
    shown, never turned into the flag a restart passes."""
    s = _sess("ask")
    s.note_reported_mode("bypassPermissions", 100.0)
    assert s.perm_mode == "ask"
    assert s.current_mode() == "full" and s.shown_mode() == ("full", "bypassPermissions")


def test_a_hook_inside_the_settle_window_is_not_recorded():
    s = _sess("auto")
    s.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=100.0)
    assert s.note_reported_mode("default", 100.0 + perm_modes.MODE_SETTLE_SECONDS - 0.01) \
        == "settling"
    assert s.reported_mode == ""
    assert s.note_reported_mode("auto", 100.0 + perm_modes.MODE_SETTLE_SECONDS) == "recorded"
    assert s.reported_mode == "auto"


@pytest.mark.parametrize("launched, raw, unavailable", [
    ("auto", "default", True), ("auto", "manual", True), ("auto", "auto", False),
    ("ask", "default", False), ("full", "default", False),
], ids=["auto-default", "auto-manual", "auto-auto", "ask-default", "full-default"])
def test_the_first_hook_after_a_launch_checks_auto(launched, raw, unavailable):
    s = _sess(launched)
    s.mode_launch_unchecked = True
    s.note_reported_mode(raw, 100.0)
    assert s.auto_unavailable is unavailable
    assert s.mode_launch_unchecked is False
    # Only the first: a later Manual (someone pressed Shift+Tab there) is
    # just Manual.
    s2 = _sess(launched)
    s2.note_reported_mode(raw, 100.0)
    assert s2.auto_unavailable is False


def test_ask_because_auto_is_not_available_is_shown_as_such_until_auto_is_seen():
    s = _sess("auto")
    s.mode_launch_unchecked = True
    s.note_reported_mode("default", 100.0)
    assert s.shown_mode() == ("ask_no_auto", "default")
    s.note_reported_mode("auto", 101.0)
    assert s.auto_unavailable is False and s.shown_mode() == ("auto", "auto")


def test_another_claude_code_mode_is_shown_by_its_name():
    s = _sess("ask")
    s.note_reported_mode("plan", 100.0)
    assert s.current_mode() == "other" and s.shown_mode() == ("other", "plan")


def test_a_pending_switch_shows_its_target_until_a_hook_arrives():
    s = _sess("auto")
    s.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=100.0)
    assert s.shown_mode() == ("auto", "")
    s.note_reported_mode("default", 200.0)
    assert s.shown_mode() == ("ask", "default")


# ---- which switches restart --------------------------------------------------

@pytest.mark.parametrize("current, reported, target, restarts", [
    ("ask", "", "auto", False), ("auto", "", "ask", False),
    ("ask", "", "full", True), ("auto", "", "full", True),
    ("full", "", "ask", True), ("full", "", "auto", True),
    ("ask", "plan", "auto", False), ("ask", "acceptEdits", "ask", False),
    ("ask", "bypassPermissions", "auto", True), ("ask", "dontAsk", "ask", True),
], ids=["ask-auto", "auto-ask", "ask-full", "auto-full", "full-ask", "full-auto",
        "plan-auto", "accept-edits-ask", "forged-bypass", "dont-ask"])
def test_mode_switch_restarts(current, reported, target, restarts):
    s = _sess(current)
    s.reported_mode = reported
    assert s.mode_switch_restarts(target) is restarts


def test_auto_unavailable_makes_auto_a_restart():
    """With Auto out of Claude Code's cycle Shift+Tab cannot reach it: a
    relaunch with --permission-mode auto is the only try left."""
    s = _sess("ask")
    s.auto_unavailable = True
    assert s.mode_switch_restarts("auto") is True
    assert s.mode_switch_restarts("ask") is False


# ---- starting over -----------------------------------------------------------

def test_reset_mode_tracking_clears_every_transient_field():
    s = _sess("auto")
    s.reported_mode, s.auto_unavailable, s.mode_launch_unchecked = "default", True, True
    s.mode_switch = ModeSwitch(target="ask", presses=1, sent_at=1.0)
    s.reset_mode_tracking()
    assert (s.reported_mode, s.auto_unavailable, s.mode_launch_unchecked, s.mode_switch) == (
        "", False, False, None)
    assert s.perm_mode == "auto"


def test_the_end_of_a_session_resets_its_mode_tracking():
    r = SessionRegistry()
    r.transition("claude-dev", Status.IDLE)
    s = r.get("claude-dev")
    s.reported_mode, s.auto_unavailable = "default", True
    s.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=1.0)
    r.transition("claude-dev", Status.GONE)
    assert (s.reported_mode, s.auto_unavailable, s.mode_switch) == ("", False, None)


# ---- launch_model (2026-09-30) ----------------------------------------------

def test_launch_model_round_trips(tmp_state_file):
    r1 = SessionRegistry()
    r1.transition("claude-dev", Status.IDLE)
    r1.get("claude-dev").launch_model = "haiku"
    r1.save()

    r2 = SessionRegistry()
    r2.load()
    assert r2.get("claude-dev").launch_model == "haiku"
