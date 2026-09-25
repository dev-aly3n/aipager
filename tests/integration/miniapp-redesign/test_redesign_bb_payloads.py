"""Additive read-only fields: grid ``waiting_summary`` / ``can_act``, detail
``answer``, and ``answer_state`` (entrypoints.md "Changed" + "Exported
functions"; design.md success criteria 4, 7, 9).

Boundary values for the 160-character cap: 159, 160, 161 characters, plus
newline collapsing and non-waiting rows.
"""

from __future__ import annotations

import time

import pytest

from aipager.miniapp.sessions import (
    NO_PERMISSION_REASON,
    answer_state,
    session_detail,
    session_summary,
)
from aipager.state import Status, TrackedSession

from miniapp_redesign_bb import (  # noqa: E402 - alias set by conftest
    ADMIN_ID,
    DEVELOPER_ID,
    READONLY_ID,
    client_for,
    hdr,
    mk_session,
    put_on_permission,
)

ELLIPSIS = "…"


def _waiting(summary, status=Status.INTERACTIVE):
    sess = TrackedSession(name="claude-w", label="w", status=status,
                          scope_chat_id=-100)
    sess.pending_permission = {"tool_summary": summary, "tool_info": None,
                               "wait_started_at": 0.0}
    return sess


def _row(summary, status=Status.INTERACTIVE):
    return session_summary(_waiting(summary, status), time.monotonic())


# ── waiting_summary: boundaries of the 160-character cap ───────────────────

def test_summary_of_159_chars_is_kept_whole():
    text = "a" * 159
    assert _row(text)["waiting_summary"] == text


def test_summary_of_exactly_160_chars_is_kept_whole():
    text = "b" * 160
    assert _row(text)["waiting_summary"] == text


def test_summary_of_161_chars_is_cut_to_160():
    assert len(_row("c" * 161)["waiting_summary"]) == 160


def test_summary_of_161_chars_ends_with_an_ellipsis():
    assert _row("c" * 161)["waiting_summary"].endswith(ELLIPSIS)


def test_cut_summary_keeps_the_head_of_the_text():
    text = "".join(chr(ord("a") + i % 26) for i in range(400))
    assert _row(text)["waiting_summary"][:-1] == text[:159]


def test_very_long_summary_is_capped_at_160():
    assert len(_row("x" * 10_000)["waiting_summary"]) == 160


def test_summary_of_160_chars_does_not_gain_an_ellipsis():
    assert not _row("d" * 160)["waiting_summary"].endswith(ELLIPSIS)


def test_non_ascii_summary_is_capped_in_characters_not_bytes():
    assert len(_row("é" * 300)["waiting_summary"]) == 160


# ── waiting_summary: one line ───────────────────────────────────────────────

def test_multiline_summary_is_collapsed_to_one_line():
    value = _row("Bash: make\nbuild\ndeploy")["waiting_summary"]
    assert "\n" not in value


def test_crlf_summary_is_collapsed_to_one_line():
    value = _row("Bash: one\r\ntwo")["waiting_summary"]
    assert "\r" not in value and "\n" not in value


def test_collapsed_summary_keeps_all_its_words():
    value = _row("Bash: make\nbuild")["waiting_summary"]
    assert "make" in value and "build" in value


def test_long_multiline_summary_is_one_line_and_capped():
    value = _row(("line " * 10 + "\n") * 20)["waiting_summary"]
    assert "\n" not in value and len(value) <= 160


# ── waiting_summary: non-waiting rows are null ─────────────────────────────

@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY, Status.GONE,
                                    Status.UNKNOWN])
def test_non_waiting_row_summary_is_null(status):
    sess = TrackedSession(name="claude-n", label="n", status=status,
                          scope_chat_id=-100)
    assert session_summary(sess, time.monotonic())["waiting_summary"] is None


def test_busy_row_with_a_leftover_prompt_has_null_summary():
    """Error guess: a stale pending prompt on a row that moved on."""
    assert _row("Bash: ls", status=Status.BUSY)["waiting_summary"] is None


def test_waiting_row_summary_is_a_string():
    assert isinstance(_row("Bash: ls")["waiting_summary"], str)


def test_summary_row_never_carries_cwd():
    sess = _waiting("Bash: ls")
    sess.cwd = "/srv/secret"
    assert "cwd" not in session_summary(sess, time.monotonic())


# ── answer_state: equivalence classes ──────────────────────────────────────

@pytest.mark.parametrize("status", ["idle", "busy", "gone", "unknown", ""])
def test_answer_state_is_none_unless_waiting(status):
    assert answer_state(status, can_act=True) is None


def test_answer_state_waiting_and_can_act_is_available():
    assert answer_state("waiting", can_act=True) == {"available": True,
                                                      "reason": None}


def test_answer_state_waiting_without_permission_is_unavailable():
    assert answer_state("waiting", can_act=False) == {
        "available": False, "reason": NO_PERMISSION_REASON}


def test_answer_state_non_waiting_without_permission_is_still_none():
    assert answer_state("idle", can_act=False) is None


# ── session_detail.answer ──────────────────────────────────────────────────

def test_detail_answer_for_waiting_session_is_available():
    d = session_detail(_waiting("Bash: ls"), time.monotonic(), can_act=True)
    assert d["answer"] == {"available": True, "reason": None}


def test_detail_answer_for_viewer_is_unavailable_with_reason():
    d = session_detail(_waiting("Bash: ls"), time.monotonic(), can_act=False)
    assert d["answer"] == {"available": False, "reason": NO_PERMISSION_REASON}


@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY, Status.GONE])
def test_detail_answer_is_null_when_not_waiting(status):
    sess = TrackedSession(name="claude-n", label="n", status=status,
                          scope_chat_id=-100)
    assert session_detail(sess, time.monotonic())["answer"] is None


# ── routes: GET /api/sessions and GET /api/sessions/{label} ────────────────

def _get(server, run_async, path, user):
    async def _run():
        client = await client_for(server)
        try:
            resp = await client.get(path, headers=hdr(user))
            return resp.status, await resp.json()
        finally:
            await client.close()
    return run_async(_run())


@pytest.mark.parametrize("user", [ADMIN_ID, DEVELOPER_ID])
def test_grid_can_act_true_for_members_who_may_prompt(server, run_async, user):
    mk_session(server, "alpha")
    _s, body = _get(server, run_async, "/api/sessions", user)
    assert body["can_act"] is True


def test_grid_can_act_false_for_member_who_cannot_prompt(server, run_async):
    mk_session(server, "alpha")
    _s, body = _get(server, run_async, "/api/sessions", READONLY_ID)
    assert body["can_act"] is False


def test_grid_can_act_is_present_with_no_sessions(server, run_async):
    _s, body = _get(server, run_async, "/api/sessions", ADMIN_ID)
    assert body.get("can_act") is True


def test_grid_rows_carry_waiting_summary_for_waiting(server, run_async):
    put_on_permission(mk_session(server, "alpha"), "Bash: rm -rf build")
    _s, body = _get(server, run_async, "/api/sessions", ADMIN_ID)
    [row] = [r for r in body["sessions"] if r["label"] == "alpha"]
    assert row["waiting_summary"] == "Bash: rm -rf build"


def test_grid_rows_carry_null_summary_for_idle(server, run_async):
    mk_session(server, "beta", status=Status.IDLE)
    _s, body = _get(server, run_async, "/api/sessions", ADMIN_ID)
    [row] = [r for r in body["sessions"] if r["label"] == "beta"]
    assert row["waiting_summary"] is None


@pytest.mark.parametrize("user", [ADMIN_ID, READONLY_ID])
def test_grid_rows_never_carry_cwd(server, run_async, user):
    put_on_permission(mk_session(server, "alpha"))
    mk_session(server, "beta", status=Status.BUSY)
    _s, body = _get(server, run_async, "/api/sessions", user)
    assert all("cwd" not in r for r in body["sessions"])


def test_viewer_still_sees_the_waiting_summary_in_the_grid(server, run_async):
    """The tray shows the prompt to viewers too (disabled button)."""
    put_on_permission(mk_session(server, "alpha"), "Bash: ls")
    _s, body = _get(server, run_async, "/api/sessions", READONLY_ID)
    assert body["sessions"][0]["waiting_summary"] == "Bash: ls"


def test_detail_route_answer_available_for_admin(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    _s, body = _get(server, run_async, "/api/sessions/alpha", ADMIN_ID)
    assert body["answer"] == {"available": True, "reason": None}


def test_detail_route_answer_unavailable_for_viewer(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    _s, body = _get(server, run_async, "/api/sessions/alpha", READONLY_ID)
    assert body["answer"] == {"available": False, "reason": NO_PERMISSION_REASON}


def test_detail_route_answer_null_for_idle(server, run_async):
    mk_session(server, "alpha", status=Status.IDLE)
    _s, body = _get(server, run_async, "/api/sessions/alpha", ADMIN_ID)
    assert body["answer"] is None
