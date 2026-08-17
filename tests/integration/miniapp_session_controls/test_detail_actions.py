"""Black-box tests for design.md success criterion 7: `GET
/api/sessions/{label}`'s `actions` object.

entrypoints.md's contract, tested exactly as documented:
  - status busy/waiting -> actions == {"stop": {...}} exactly
  - status idle         -> actions == {"kill": {...}} exactly
  - status gone         -> actions == {"resume": {...}, "delete": {...}} exactly
  - any other status    -> actions == {}
  - a key ABSENT means "don't show", never "disabled" -- so every test
    below asserts both what IS present (and its available/reason shape)
    AND, just as importantly, that the other keys are genuinely absent
    (`"kill" not in actions`), not merely present-and-disabled.
  - available:false always comes with a non-null reason.
"""

from __future__ import annotations

from aipager.state import Status

from .conftest import ADMIN_ID, READONLY_ID, _client_for, _hdr, _mk_session

NO_TRANSCRIPT_REASON = "No resumable transcript — start a fresh session instead."
NO_PERMISSION_REASON = "You don't have permission to control this session."


async def _actions_for(client, label, user_id):
    resp = await client.get(f"/api/sessions/{label}", headers=_hdr(user_id))
    assert resp.status == 200
    body = await resp.json()
    return body["status"], body["actions"]


# ===== busy / waiting -> stop only ==========================================

def test_busy_status_yields_only_stop_key(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            status, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert status == "busy"
            assert actions == {"stop": {"available": True, "reason": None}}
        finally:
            await client.close()
    run_async(_run())


def test_waiting_status_yields_only_stop_key(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.INTERACTIVE)
        client = await _client_for(server)
        try:
            status, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert status == "waiting"
            assert actions == {"stop": {"available": True, "reason": None}}
        finally:
            await client.close()
    run_async(_run())


def test_busy_status_has_no_kill_resume_or_delete_key(server, run_async):
    """Absent, not disabled -- a present-but-disabled kill/resume/delete
    here would be just as wrong as showing a working one."""
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            _, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert "kill" not in actions
            assert "resume" not in actions
            assert "delete" not in actions
        finally:
            await client.close()
    run_async(_run())


# ===== idle -> kill only =====================================================

def test_idle_status_yields_only_kill_key(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            status, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert status == "idle"
            assert actions == {"kill": {"available": True, "reason": None}}
        finally:
            await client.close()
    run_async(_run())


def test_idle_status_has_no_stop_resume_or_delete_key(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            _, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert "stop" not in actions
            assert "resume" not in actions
            assert "delete" not in actions
        finally:
            await client.close()
    run_async(_run())


# ===== gone -> resume + delete ===============================================

def test_gone_resumable_yields_resume_and_delete_both_available(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            status, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert status == "gone"
            assert actions == {
                "resume": {"available": True, "reason": None},
                "delete": {"available": True, "reason": None},
            }
        finally:
            await client.close()
    run_async(_run())


def test_gone_status_has_no_stop_or_kill_key(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            _, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert "stop" not in actions
            assert "kill" not in actions
        finally:
            await client.close()
    run_async(_run())


def test_gone_without_transcript_resume_unavailable_with_reason_delete_still_available(
    server, run_async,
):
    """Delete does not depend on resumability at all -- only Resume
    does."""
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="")
        client = await _client_for(server)
        try:
            _, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert actions["resume"] == {
                "available": False, "reason": NO_TRANSCRIPT_REASON,
            }
            assert actions["delete"] == {"available": True, "reason": None}
        finally:
            await client.close()
    run_async(_run())


# ===== unknown/uncharacterized status -> empty actions =======================

def test_unknown_status_yields_empty_actions_dict(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.UNKNOWN)
        client = await _client_for(server)
        try:
            status, actions = await _actions_for(client, "dev", ADMIN_ID)
            assert status == "unknown"
            assert actions == {}
        finally:
            await client.close()
    run_async(_run())


# ===== can_act=False (readonly member) permission gating =====================

def test_readonly_member_sees_stop_present_but_unavailable_with_permission_reason(
    server, run_async,
):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            status, actions = await _actions_for(client, "dev", READONLY_ID)
            assert status == "busy"
            assert actions == {
                "stop": {"available": False, "reason": NO_PERMISSION_REASON},
            }
        finally:
            await client.close()
    run_async(_run())


def test_readonly_member_sees_kill_present_but_unavailable(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            _, actions = await _actions_for(client, "dev", READONLY_ID)
            assert actions == {
                "kill": {"available": False, "reason": NO_PERMISSION_REASON},
            }
        finally:
            await client.close()
    run_async(_run())


def test_readonly_member_sees_resume_and_delete_both_unavailable(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            _, actions = await _actions_for(client, "dev", READONLY_ID)
            assert actions == {
                "resume": {"available": False, "reason": NO_PERMISSION_REASON},
                "delete": {"available": False, "reason": NO_PERMISSION_REASON},
            }
        finally:
            await client.close()
    run_async(_run())


def test_readonly_member_with_no_transcript_gets_permission_reason_not_transcript_reason(
    server, run_async,
):
    """design.md's precise combinatorial rule: when BOTH conditions
    would make resume unavailable (no permission AND no transcript),
    the permission reason wins -- it must never leak the transcript
    reason to someone who couldn't act on it anyway."""
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="")
        client = await _client_for(server)
        try:
            _, actions = await _actions_for(client, "dev", READONLY_ID)
            assert actions["resume"]["reason"] == NO_PERMISSION_REASON
            assert actions["resume"]["available"] is False
        finally:
            await client.close()
    run_async(_run())
