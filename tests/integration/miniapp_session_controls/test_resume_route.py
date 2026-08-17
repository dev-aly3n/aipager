"""Black-box tests for `POST /api/sessions/{label}/resume`.

design.md success criterion 4:
  - GONE + transcript -> 200, session leaves GONE.
  - GONE, no transcript -> 409 no_transcript, shared reason string.
  - live (non-GONE) -> 409 not_gone.

entrypoints.md's failure table additionally documents:
  - 400 launch_failed {"error":"launch_failed","detail": <launcher text>}

Every test that reaches inject.launch_session monkeypatches it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.state import Status

from .conftest import (
    ADMIN_ID,
    DEVELOPER_ID,
    SCOPE_CHAT_ID,
    _client_for,
    _hdr,
    _mk_session,
)

NO_TRANSCRIPT_REASON = "No resumable transcript — start a fresh session instead."


def _mock_launch(monkeypatch, *, ok, err=""):
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session",
        AsyncMock(return_value=(ok, err)))


# ===== happy path ============================================================

def test_resume_gone_with_transcript_returns_200(server, run_async, monkeypatch):
    _mock_launch(monkeypatch, ok=True)

    async def _run():
        _mk_session(server, "dev", status=Status.GONE,
                     claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            body = await resp.json()
            assert body == {"status": "resumed", "label": "dev"}
        finally:
            await client.close()
    run_async(_run())


def test_resume_gone_with_transcript_leaves_gone_status(server, run_async, monkeypatch):
    _mock_launch(monkeypatch, ok=True)

    async def _run():
        _mk_session(server, "dev", status=Status.GONE,
                     claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            await client.post("/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["status"] != "gone"
        finally:
            await client.close()
    run_async(_run())


def test_resume_sends_exactly_one_chat_message(server, run_async, monkeypatch):
    _mock_launch(monkeypatch, ok=True)

    async def _run():
        _mk_session(server, "dev", status=Status.GONE,
                     claude_session_id="uuid-1")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.post("/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_developer_non_admin_can_resume(server, run_async, monkeypatch):
    _mock_launch(monkeypatch, ok=True)

    async def _run():
        _mk_session(server, "dev", status=Status.GONE,
                     claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(DEVELOPER_ID))
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


# ===== refusal: no transcript ===============================================

def test_resume_gone_without_transcript_returns_409_no_transcript(
    server, run_async, monkeypatch,
):
    async def _boom(*a, **kw):
        raise AssertionError(
            "inject.launch_session must not run with no resumable transcript")
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)

    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == "no_transcript"
            assert body["detail"] == NO_TRANSCRIPT_REASON
        finally:
            await client.close()
    run_async(_run())


def test_resume_no_transcript_sends_no_chat_message_and_stays_gone(
    server, run_async, monkeypatch,
):
    monkeypatch.setattr("aipager.dtach.inject.launch_session", AsyncMock())

    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.post("/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            send.assert_not_awaited()
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await resp.json())["status"] == "gone"
        finally:
            await client.close()
    run_async(_run())


# ===== refusal: not gone (still live) =======================================

def test_resume_live_session_returns_409_not_gone(server, run_async, monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError("inject.launch_session must not run on a live session")
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == "not_gone"
            assert body["detail"] == "This session is already running."
        finally:
            await client.close()
    run_async(_run())


def test_resume_busy_session_returns_409_not_gone(server, run_async, monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError("inject.launch_session must not run on a live session")
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)

    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_gone"
        finally:
            await client.close()
    run_async(_run())


# ===== launch failure =========================================================

def test_resume_launch_failure_returns_400_launch_failed(server, run_async, monkeypatch):
    _mock_launch(monkeypatch, ok=False, err="dtach broken")

    async def _run():
        _mk_session(server, "dev", status=Status.GONE,
                     claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert resp.status == 400
            body = await resp.json()
            assert body["error"] == "launch_failed"
            assert body["detail"] == "dtach broken"
        finally:
            await client.close()
    run_async(_run())


def test_resume_launch_failure_sends_no_chat_message(server, run_async, monkeypatch):
    _mock_launch(monkeypatch, ok=False, err="dtach broken")

    async def _run():
        _mk_session(server, "dev", status=Status.GONE,
                     claude_session_id="uuid-1")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.post("/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_resume_launch_failure_then_retry_does_not_report_no_transcript(
    server, run_async, monkeypatch,
):
    """spec.md: `_do_resume`/`_do_resume_core` deliberately clears
    `claude_session_id` BEFORE launching and restores it on failure, so
    a repeat failure cannot cause an infinite resume loop where the
    session silently loses its resumability. This is directly
    observable: a second attempt after a launch failure must fail the
    SAME way (launch_failed again, if we keep failing), never
    no_transcript -- proving the id was restored, not permanently
    wiped."""
    _mock_launch(monkeypatch, ok=False, err="dtach broken")

    async def _run():
        _mk_session(server, "dev", status=Status.GONE,
                     claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            first = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert first.status == 400
            second = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert second.status == 400
            assert (await second.json())["error"] == "launch_failed"
        finally:
            await client.close()
    run_async(_run())


# ===== idempotency ===========================================================

def test_resume_twice_second_call_refuses_not_gone(server, run_async, monkeypatch):
    _mock_launch(monkeypatch, ok=True)

    async def _run():
        _mk_session(server, "dev", status=Status.GONE,
                     claude_session_id="uuid-1")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            first = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert first.status == 200
            second = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert second.status == 409
            assert (await second.json())["error"] == "not_gone"
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())
