"""Black-box tests for `POST /api/sessions/{label}/stop`.

design.md success criteria 1-2:
 1. On a BUSY session: 200 {"status":"stopped"}, status becomes idle on
    a subsequent GET, exactly one chat message sent.
 2. On an IDLE session: 409 not_busy, no chat message sent.

entrypoints.md: success body is
  {"status": "stopped", "label": string, "dropped": integer}
refusal is 409 {"error": "not_busy",
  "detail": "This session isn't busy right now — nothing to stop."}
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.state import Status

from .conftest import ADMIN_ID, SCOPE_CHAT_ID, _client_for, _hdr, _mk_session


# ===== happy path (criterion 1) ============================================

def test_stop_busy_session_returns_200_and_stopped_status(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "stopped"
            assert body["label"] == "dev"
        finally:
            await client.close()
    run_async(_run())


def test_stop_busy_session_status_becomes_idle_on_subsequent_get(
    server, run_async, monkeypatch,
):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
        client = await _client_for(server)
        try:
            await client.post("/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await resp.json())["status"] == "idle"
        finally:
            await client.close()
    run_async(_run())


def test_stop_interactive_session_also_succeeds(server, run_async, monkeypatch):
    """entrypoints.md's status matrix groups BUSY and INTERACTIVE
    (wire: "waiting") together under the Stop button -- the server-side
    guard must accept both, not BUSY alone."""
    async def _run():
        _mk_session(server, "dev", status=Status.INTERACTIVE)
        monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_stop_sends_exactly_one_chat_message_to_own_scope(
    server, run_async, monkeypatch,
):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.post("/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_stop_dropped_count_reflects_pending_queue(server, run_async, monkeypatch):
    """The success body's `dropped` field is a real observable count,
    not always zero -- a queued follow-up message must show up here."""
    async def _run():
        sess = _mk_session(server, "dev", status=Status.BUSY)
        sess.pending_queue = [("first queued", None), ("second queued", None)]
        monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["dropped"] == 2
        finally:
            await client.close()
    run_async(_run())


def test_stop_dropped_count_is_zero_with_empty_queue(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["dropped"] == 0
        finally:
            await client.close()
    run_async(_run())


# ===== refusal: not busy (criterion 2) ======================================

def test_stop_idle_session_returns_409_not_busy(server, run_async, monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError(
            "inject.send_keys must not run for a non-busy session")
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == "not_busy"
            assert body["detail"] == (
                "This session isn't busy right now — nothing to stop."
            )
        finally:
            await client.close()
    run_async(_run())


def test_stop_idle_session_sends_no_chat_message(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_stop_idle_session_leaves_status_unchanged(server, run_async):
    """The ONLY possible cause of the still-idle status on the next GET
    must be the refused stop, not some unrelated precondition -- the
    session starts IDLE, we refuse a stop on it, and assert it is
    still, unambiguously, IDLE."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            await client.post("/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await resp.json())["status"] == "idle"
        finally:
            await client.close()
    run_async(_run())


def test_stop_gone_session_returns_409_not_busy_not_404(server, run_async):
    """A GONE session is resolvable (it exists in the caller's scope,
    `find_by_label(..., include_gone=True)`) so refusing it is a status
    conflict (409 not_busy), never a 404 -- only unknown/foreign labels
    404."""
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_busy"
        finally:
            await client.close()
    run_async(_run())


# ===== idempotency ===========================================================

def test_stop_twice_second_call_refuses_coherently(server, run_async, monkeypatch):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            first = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert first.status == 200
            second = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert second.status == 409
            assert (await second.json())["error"] == "not_busy"
            # Only the first (successful) stop mirrors to chat.
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())
