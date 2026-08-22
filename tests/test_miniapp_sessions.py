"""Tests for aipager.miniapp.sessions — pure, I/O-free session shaping.

No registry, no bot, no server: every function here takes a
TrackedSession (and sometimes a caller-supplied `now`) and returns a
plain dict/list, so these are constructed directly.
"""

import time

from aipager.miniapp.sessions import (
    NO_PERMISSION_REASON,
    NO_TRANSCRIPT_REASON,
    PERMS_ADMIN_REQUIRED_REASON,
    QUEUE_EMPTY_REASON,
    QUEUE_FULL_REASON,
    _derive_status,
    build_timeline,
    session_actions,
    session_detail,
    session_summary,
)
from aipager.state import QUEUE_CAP
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
    # Nonzero so `compact` isn't gated off by the live-message-stack
    # feature's context_pct <= 0 rule (design.md Decision 6) — that rule
    # has its own dedicated coverage in test_miniapp_session_actions_api.py.
    sess.last_token_pct = 40
    detail = session_detail(sess, time.monotonic())
    assert "actions" in detail
    # Not admin, no queue -> perms unavailable, clearqueue unavailable,
    # everything else on offer for a BUSY session available.
    assert detail["actions"] == {
        "stop": {"available": True, "reason": None},
        "clearqueue": {"available": False, "reason": QUEUE_EMPTY_REASON},
        "compact": {"available": True, "reason": None},
        "rename": {"available": True, "reason": None},
        "perms": {"available": False, "reason": PERMS_ADMIN_REQUIRED_REASON},
        "restart": {"available": True, "reason": None},
    }


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


def test_session_detail_includes_skip_perms_and_queue_depth():
    sess = _sess(status=Status.BUSY)
    sess.skip_perms = True
    sess.queue_prompt("a", 1)
    sess.queue_prompt("b", 2)
    detail = session_detail(sess, time.monotonic())
    assert detail["skip_perms"] is True
    assert detail["queue_depth"] == 2


def test_session_detail_queue_depth_includes_outstanding_notes(monkeypatch, tmp_path):
    """design.md "queue handoff": queue_depth uses the same
    combined-count helper chat's own Stop/`/clearqueue` use, so the Mini
    App and chat can never disagree about how many prompts sit behind a
    session's turn."""
    from aipager import policy_snapshot as ps

    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / f"notes-{n}")
    sess = _sess(status=Status.BUSY)
    sess.queue_prompt("a", 1)
    ps.write_note(sess.name, None, None, None, msg_id=9, chat_id=1,
                  sender_key=(1, 1), body="note text", raw_text="note text")

    detail = session_detail(sess, time.monotonic())

    assert detail["queue_depth"] == 2  # 1 queued + 1 note


def test_session_detail_passes_is_admin_into_perms_action():
    sess = _sess(status=Status.IDLE)
    sess.skip_perms = False  # target is Auto -> needs admin
    detail = session_detail(sess, time.monotonic(), is_admin=True)
    assert detail["actions"]["perms"] == {"available": True, "reason": None}


# ===== session_actions (status -> buttons matrix) ==========================

def test_session_actions_busy_shows_the_full_busy_set_in_canonical_order():
    actions = session_actions(
        "busy", resumable=False, can_act=True, is_admin=True, queue_depth=1,
        context_pct=40,
    )
    assert list(actions.keys()) == [
        "stop", "clearqueue", "compact", "rename", "perms", "restart",
    ]
    for key in actions:
        assert actions[key] == {"available": True, "reason": None}


def test_session_actions_waiting_excludes_compact():
    """Compact is deliberately excluded from waiting — an open
    permission/question prompt risks reading `/compact` as its input."""
    actions = session_actions(
        "waiting", resumable=False, can_act=True, is_admin=True, queue_depth=1,
    )
    assert list(actions.keys()) == ["stop", "clearqueue", "rename", "perms", "restart"]
    assert "compact" not in actions


def test_session_actions_idle_shows_compact_but_not_stop_or_clearqueue():
    actions = session_actions(
        "idle", resumable=False, can_act=True, is_admin=True, context_pct=40,
    )
    assert list(actions.keys()) == ["compact", "rename", "kill", "perms", "restart"]
    assert actions["compact"] == {"available": True, "reason": None}


def test_session_actions_gone_resumable_shows_resume_rename_and_delete_available():
    actions = session_actions("gone", resumable=True, can_act=True)
    assert actions == {
        "resume": {"available": True, "reason": None},
        "rename": {"available": True, "reason": None},
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
    assert actions["rename"] == {"available": True, "reason": None}


def test_session_actions_unknown_status_shows_nothing():
    assert session_actions("unknown", resumable=False, can_act=True) == {}


def test_session_actions_cannot_act_disables_every_present_key():
    """any status + can_act=False -> every present key unavailable with
    the permission reason — including the four new keys."""
    busy = session_actions(
        "busy", resumable=False, can_act=False, is_admin=True, queue_depth=1,
    )
    for key, entry in busy.items():
        assert entry == {"available": False, "reason": NO_PERMISSION_REASON}, key

    idle = session_actions("idle", resumable=False, can_act=False, is_admin=True)
    for key, entry in idle.items():
        assert entry == {"available": False, "reason": NO_PERMISSION_REASON}, key

    gone = session_actions("gone", resumable=True, can_act=False)
    assert gone["resume"] == {"available": False, "reason": NO_PERMISSION_REASON}
    assert gone["delete"] == {"available": False, "reason": NO_PERMISSION_REASON}
    assert gone["rename"] == {"available": False, "reason": NO_PERMISSION_REASON}


def test_session_actions_permission_reason_wins_over_no_transcript():
    """When BOTH can_act is False and the session isn't resumable, the
    permission reason wins — a caller who cannot act at all should not
    be told the OTHER reason it can't act (design.md)."""
    actions = session_actions("gone", resumable=False, can_act=False)
    assert actions["resume"] == {
        "available": False, "reason": NO_PERMISSION_REASON,
    }


def test_session_actions_permission_reason_wins_over_admin_reason():
    """can_act=False must win over the perms admin gate too — the same
    "one reason, not two" rule generalised to the new key."""
    actions = session_actions(
        "busy", resumable=False, can_act=False, is_admin=False, skip_perms=False,
    )
    assert actions["perms"] == {"available": False, "reason": NO_PERMISSION_REASON}


# ---- clearqueue ------------------------------------------------------------

def test_session_actions_clearqueue_unavailable_when_empty():
    actions = session_actions("busy", resumable=False, can_act=True, queue_depth=0)
    assert actions["clearqueue"] == {"available": False, "reason": QUEUE_EMPTY_REASON}


def test_session_actions_clearqueue_available_when_non_empty():
    actions = session_actions("busy", resumable=False, can_act=True, queue_depth=1)
    assert actions["clearqueue"] == {"available": True, "reason": None}


def test_session_actions_clearqueue_absent_when_idle():
    actions = session_actions("idle", resumable=False, can_act=True, queue_depth=5)
    assert "clearqueue" not in actions


# ---- compact ----------------------------------------------------------------

def test_session_actions_compact_busy_unavailable_at_cap():
    actions = session_actions(
        "busy", resumable=False, can_act=True, queue_depth=QUEUE_CAP,
        context_pct=40,
    )
    assert actions["compact"] == {"available": False, "reason": QUEUE_FULL_REASON}


def test_session_actions_compact_busy_available_below_cap():
    actions = session_actions(
        "busy", resumable=False, can_act=True, queue_depth=QUEUE_CAP - 1,
        context_pct=40,
    )
    assert actions["compact"] == {"available": True, "reason": None}


def test_session_actions_compact_idle_ignores_queue_depth():
    """IDLE compact sends immediately rather than queueing — no
    queue-depth rule applies, even at a (nonsensical) full depth."""
    actions = session_actions(
        "idle", resumable=False, can_act=True, queue_depth=QUEUE_CAP,
        context_pct=40,
    )
    assert actions["compact"] == {"available": True, "reason": None}


def test_session_actions_compact_absent_when_waiting():
    actions = session_actions("waiting", resumable=False, can_act=True)
    assert "compact" not in actions


# ---- perms --------------------------------------------------------------

def test_session_actions_perms_target_auto_requires_admin():
    """skip_perms=False -> target is Auto -> needs admin."""
    actions = session_actions(
        "idle", resumable=False, can_act=True, is_admin=False, skip_perms=False,
    )
    assert actions["perms"] == {
        "available": False, "reason": PERMS_ADMIN_REQUIRED_REASON,
    }


def test_session_actions_perms_target_auto_admin_allowed():
    actions = session_actions(
        "idle", resumable=False, can_act=True, is_admin=True, skip_perms=False,
    )
    assert actions["perms"] == {"available": True, "reason": None}


def test_session_actions_perms_target_ask_never_needs_admin():
    """skip_perms=True -> target is Ask -> allowed for a non-admin too,
    matching chat's own /perms rule."""
    actions = session_actions(
        "idle", resumable=False, can_act=True, is_admin=False, skip_perms=True,
    )
    assert actions["perms"] == {"available": True, "reason": None}


def test_session_actions_perms_present_for_busy_waiting_and_idle():
    for status in ("busy", "waiting", "idle"):
        actions = session_actions(
            status, resumable=False, can_act=True, is_admin=True,
        )
        assert "perms" in actions, status
    assert "perms" not in session_actions("gone", resumable=True, can_act=True)


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
