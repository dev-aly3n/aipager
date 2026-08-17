"""Tests for aipager.miniapp.sessions — pure, I/O-free session shaping.

No registry, no bot, no server: every function here takes a
TrackedSession (and sometimes a caller-supplied `now`) and returns a
plain dict/list, so these are constructed directly.
"""

import time

from aipager.miniapp.sessions import (
    NO_PERMISSION_REASON,
    NO_TRANSCRIPT_REASON,
    _derive_status,
    build_timeline,
    session_actions,
    session_detail,
    session_summary,
)
from aipager.state import Status, TrackedSession


def _sess(**kwargs) -> TrackedSession:
    defaults = {"name": "claude-dev", "label": "dev"}
    defaults.update(kwargs)
    return TrackedSession(**defaults)


# ===== _derive_status ====================================================

def test_derive_status_busy_passes_through():
    sess = _sess(status=Status.BUSY)
    assert _derive_status(sess) == ("busy", None, None)


def test_derive_status_idle_passes_through():
    sess = _sess(status=Status.IDLE)
    assert _derive_status(sess) == ("idle", None, None)


def test_derive_status_gone_passes_through():
    sess = _sess(status=Status.GONE)
    assert _derive_status(sess) == ("gone", None, None)


def test_derive_status_unknown_passes_through():
    sess = _sess(status=Status.UNKNOWN)
    assert _derive_status(sess) == ("unknown", None, None)


def test_derive_status_interactive_with_tool_permission_is_waiting():
    sess = _sess(
        status=Status.INTERACTIVE,
        pending_permission={"tool_summary": "Bash: rm -rf tmp/", "tool_info": {}},
    )
    status, kind, summary = _derive_status(sess)
    assert status == "waiting"
    assert kind == "permission"
    assert summary == "Bash: rm -rf tmp/"


def test_derive_status_interactive_with_ask_question_is_waiting():
    sess = _sess(
        status=Status.INTERACTIVE,
        pending_permission={
            "ask_question": True,
            "question": "Which approach?",
            "options": [{"label": "A"}, {"label": "B"}],
        },
    )
    status, kind, summary = _derive_status(sess)
    assert status == "waiting"
    assert kind == "question"
    assert summary == "Which approach?"


def test_derive_status_interactive_with_no_pending_permission_is_still_waiting():
    """The separate-message fallback path (notify.py:982,988) clears
    pending_permission to None while status stays INTERACTIVE — this
    must still read "waiting", just without kind/summary detail. This is
    the guard against gating "waiting" on pending_permission instead of
    on status alone (verified by removal — see implementation.md)."""
    sess = _sess(status=Status.INTERACTIVE, pending_permission=None)
    assert _derive_status(sess) == ("waiting", None, None)


def test_derive_status_never_returns_raw_interactive_name():
    sess = _sess(status=Status.INTERACTIVE, pending_permission=None)
    status, _kind, _summary = _derive_status(sess)
    assert status != "interactive"


# ===== session_summary ===================================================

def test_session_summary_shape_and_no_cwd():
    sess = _sess(
        status=Status.BUSY, model_name="Opus 4.6", last_token_pct=42,
        last_cost_usd=1.2345, cwd="/home/dev/myproject",
    )
    sess.last_hook_at = time.monotonic() - 5
    now = time.monotonic()
    row = session_summary(sess, now)

    assert row["label"] == "dev"
    assert row["status"] == "busy"
    assert row["waiting_kind"] is None
    assert row["model"] == "Opus 4.6"
    assert row["context_pct"] == 42
    assert row["cost_usd"] == 1.2345
    assert row["last_active_seconds_ago"] is not None
    assert row["project"] == "myproject"
    assert "cwd" not in row
    assert "waiting_summary" not in row


def test_session_summary_last_active_none_when_never_hooked():
    sess = _sess(status=Status.UNKNOWN)
    row = session_summary(sess, time.monotonic())
    assert row["last_active_seconds_ago"] is None


def test_session_summary_project_empty_for_empty_cwd():
    sess = _sess(status=Status.IDLE, cwd="")
    row = session_summary(sess, time.monotonic())
    assert row["project"] == ""


def test_session_summary_waiting_kind_populated_for_interactive():
    sess = _sess(
        status=Status.INTERACTIVE,
        pending_permission={"ask_question": True, "question": "Pick one"},
    )
    row = session_summary(sess, time.monotonic())
    assert row["status"] == "waiting"
    assert row["waiting_kind"] == "question"


# ===== session_detail =====================================================

def test_session_detail_includes_cwd_and_waiting_summary():
    sess = _sess(
        status=Status.INTERACTIVE, cwd="/home/dev/myproject",
        pending_permission={"tool_summary": "Write: main.py"},
    )
    detail = session_detail(sess, time.monotonic())
    assert detail["cwd"] == "/home/dev/myproject"
    assert detail["status"] == "waiting"
    assert detail["waiting_kind"] == "permission"
    assert detail["waiting_summary"] == "Write: main.py"
    assert detail["timeline"] == []


def test_session_detail_busy_elapsed_seconds_populated_when_busy():
    sess = _sess(status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 30
    detail = session_detail(sess, time.monotonic())
    assert detail["busy_elapsed_seconds"] is not None
    assert detail["busy_elapsed_seconds"] >= 29


def test_session_detail_busy_elapsed_seconds_none_when_idle():
    sess = _sess(status=Status.IDLE)
    sess.busy_started_at = time.monotonic() - 30
    detail = session_detail(sess, time.monotonic())
    assert detail["busy_elapsed_seconds"] is None


def test_session_detail_busy_elapsed_seconds_populated_while_waiting():
    """busy_started_at is shifted forward across a permission wait
    (state.py's own docstring) so it stays meaningful through
    INTERACTIVE, not just BUSY."""
    sess = _sess(status=Status.INTERACTIVE, pending_permission=None)
    sess.busy_started_at = time.monotonic() - 10
    detail = session_detail(sess, time.monotonic())
    assert detail["busy_elapsed_seconds"] is not None


# ===== build_timeline ======================================================

def test_build_timeline_row_count_matches_raw_fields():
    sess = _sess()
    sess.tool_history = [("Read foo.py", True), ("Write bar.py", "failed"), ("Bash: ls", False)]
    sess.stream_commentary = [(0, "Let me check that file"), (2, "Now let's run ls")]
    rows = build_timeline(sess)
    assert len(rows) == len(sess.tool_history) + len(sess.stream_commentary)


def test_build_timeline_empty_session_returns_empty_list():
    sess = _sess()
    assert build_timeline(sess) == []


def test_build_timeline_orders_commentary_before_its_anchor_tool():
    sess = _sess()
    sess.tool_history = [("Read foo.py", True)]
    sess.stream_commentary = [(0, "About to read the file")]
    rows = build_timeline(sess)
    assert rows[0] == {"kind": "commentary", "text": "About to read the file"}
    assert rows[1]["kind"] == "tool"
    assert rows[1]["text"] == "Read foo.py"
    assert rows[1]["state"] == "done"


def test_build_timeline_tool_states():
    sess = _sess()
    sess.tool_history = [("done tool", True), ("failed tool", "failed"), ("running tool", False)]
    rows = build_timeline(sess)
    states = [r["state"] for r in rows]
    assert states == ["done", "failed", "running"]


def test_session_detail_includes_actions_key():
    sess = _sess(status=Status.BUSY)
    detail = session_detail(sess, time.monotonic())
    assert "actions" in detail
    assert detail["actions"] == {"stop": {"available": True, "reason": None}}


def test_session_detail_default_can_act_is_permissive():
    """The existing unit tests in this file call session_detail(sess, now)
    positionally — the can_act=True default must let those calls through
    unchanged while gaining the new actions key (design.md)."""
    sess = _sess(status=Status.IDLE)
    detail = session_detail(sess, time.monotonic())
    assert detail["actions"]["kill"]["available"] is True


def test_session_detail_can_act_false_disables_actions():
    sess = _sess(status=Status.IDLE)
    detail = session_detail(sess, time.monotonic(), can_act=False)
    assert detail["actions"]["kill"] == {
        "available": False, "reason": NO_PERMISSION_REASON,
    }


# ===== session_actions (status -> buttons matrix) ==========================

def test_session_actions_busy_shows_only_stop():
    actions = session_actions("busy", resumable=False, can_act=True)
    assert actions == {"stop": {"available": True, "reason": None}}


def test_session_actions_waiting_shows_only_stop():
    actions = session_actions("waiting", resumable=False, can_act=True)
    assert actions == {"stop": {"available": True, "reason": None}}


def test_session_actions_idle_shows_only_kill():
    actions = session_actions("idle", resumable=False, can_act=True)
    assert actions == {"kill": {"available": True, "reason": None}}


def test_session_actions_gone_resumable_shows_resume_and_delete_available():
    actions = session_actions("gone", resumable=True, can_act=True)
    assert actions == {
        "resume": {"available": True, "reason": None},
        "delete": {"available": True, "reason": None},
    }


def test_session_actions_gone_not_resumable_resume_unavailable():
    actions = session_actions("gone", resumable=False, can_act=True)
    assert actions["resume"] == {
        "available": False, "reason": NO_TRANSCRIPT_REASON,
    }
    # Delete never depends on resumability — a GONE session can always
    # be forgotten regardless of whether its transcript is resumable.
    assert actions["delete"] == {"available": True, "reason": None}


def test_session_actions_unknown_status_shows_nothing():
    assert session_actions("unknown", resumable=False, can_act=True) == {}


def test_session_actions_cannot_act_disables_every_present_key():
    """any status + can_act=False -> every present key unavailable with
    the permission reason."""
    busy = session_actions("busy", resumable=False, can_act=False)
    assert busy["stop"] == {"available": False, "reason": NO_PERMISSION_REASON}

    idle = session_actions("idle", resumable=False, can_act=False)
    assert idle["kill"] == {"available": False, "reason": NO_PERMISSION_REASON}

    gone = session_actions("gone", resumable=True, can_act=False)
    assert gone["resume"] == {"available": False, "reason": NO_PERMISSION_REASON}
    assert gone["delete"] == {"available": False, "reason": NO_PERMISSION_REASON}


def test_session_actions_permission_reason_wins_over_no_transcript():
    """When BOTH can_act is False and the session isn't resumable, the
    permission reason wins — a caller who cannot act at all should not
    be told the OTHER reason it can't act (design.md)."""
    actions = session_actions("gone", resumable=False, can_act=False)
    assert actions["resume"] == {
        "available": False, "reason": NO_PERMISSION_REASON,
    }


def test_build_timeline_trailing_commentary_past_last_tool_is_appended():
    """An anchor beyond the current tool_history length (prose that
    arrived after the last recorded tool row) must still surface,
    clamped to the end rather than silently dropped."""
    sess = _sess()
    sess.tool_history = [("Read foo.py", True)]
    sess.stream_commentary = [(5, "Trailing thought")]
    rows = build_timeline(sess)
    assert {"kind": "commentary", "text": "Trailing thought"} in rows
    assert len(rows) == 2
