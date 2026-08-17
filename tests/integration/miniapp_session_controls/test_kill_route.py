"""Black-box tests for `POST /api/sessions/{label}/kill`.

design.md success criterion 3: on an IDLE session, 200; a subsequent
`GET /api/sessions/{label}` 404s; the label is absent from
`GET /api/sessions`.

entrypoints.md's failure table for Kill lists THREE outcomes and,
notably, no "wrong status" guard at all (unlike Stop/Resume/Delete):
  - 409 still_running: dtach reports still alive after the kill attempt
  - 404 not_found: unknown/foreign label, OR the dtach socket was
    already gone by the time the kill ran (same body/shape either way)
  - 200 killed: registry.remove() + mark_dirty(), fully forgotten

Every test that reaches inject.kill_session/is_alive monkeypatches it.
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


def _mock_kill(monkeypatch, *, killed, alive_after):
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=killed))
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=alive_after))


# ===== happy path (criterion 3) ============================================

def test_kill_idle_session_returns_200(server, run_async, monkeypatch):
    _mock_kill(monkeypatch, killed=True, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            body = await resp.json()
            assert body == {"status": "killed", "label": "dev"}
        finally:
            await client.close()
    run_async(_run())


def test_kill_idle_session_subsequent_get_404s(server, run_async, monkeypatch):
    _mock_kill(monkeypatch, killed=True, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            await client.post("/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert resp.status == 404
        finally:
            await client.close()
    run_async(_run())


def test_kill_idle_session_label_absent_from_grid(server, run_async, monkeypatch):
    _mock_kill(monkeypatch, killed=True, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        _mk_session(server, "other-live", status=Status.IDLE)
        client = await _client_for(server)
        try:
            await client.post("/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            resp = await client.get("/api/sessions", headers=_hdr(ADMIN_ID))
            labels = {s["label"] for s in (await resp.json())["sessions"]}
            assert "dev" not in labels
            assert "other-live" in labels
        finally:
            await client.close()
    run_async(_run())


def test_kill_sends_exactly_one_chat_message(server, run_async, monkeypatch):
    _mock_kill(monkeypatch, killed=True, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.post("/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_kill_busy_session_also_succeeds(server, run_async, monkeypatch):
    """Kill has no status guard in entrypoints.md's failure table (unlike
    Stop/Resume/Delete) -- a crafted request against a BUSY session must
    still be allowed to kill it server-side, matching chat's own /kill,
    which can interrupt a busy session too. The absence of a button on
    a stale client page is not the gate."""
    _mock_kill(monkeypatch, killed=True, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_developer_non_admin_can_kill(server, run_async, monkeypatch):
    """Authorization headline: the gate is _can_prompt_user, not
    _is_admin_user -- a developer (can_prompt=True, bypass_safety=False)
    must succeed exactly like an admin."""
    _mock_kill(monkeypatch, killed=True, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(DEVELOPER_ID))
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


# ===== still running (dtach kill failed but the process lives) =============

def test_kill_still_alive_returns_409(server, run_async, monkeypatch):
    _mock_kill(monkeypatch, killed=False, alive_after=True)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == "still_running"
            assert body["detail"] == (
                "Could not kill — the process is still running. Try again."
            )
        finally:
            await client.close()
    run_async(_run())


def test_kill_still_alive_session_survives_and_no_message_sent(
    server, run_async, monkeypatch,
):
    _mock_kill(monkeypatch, killed=False, alive_after=True)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.post("/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            send.assert_not_awaited()
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            assert (await resp.json())["status"] == "idle"
        finally:
            await client.close()
    run_async(_run())


# ===== post-lookup race: dtach socket already gone ==========================

def test_kill_already_gone_process_returns_404_not_found(
    server, run_async, monkeypatch,
):
    """entrypoints.md: 'the process was already gone by the time the
    kill ran' -- same shape as a genuinely unknown label."""
    _mock_kill(monkeypatch, killed=False, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


def test_kill_already_gone_process_sends_no_chat_message(
    server, run_async, monkeypatch,
):
    _mock_kill(monkeypatch, killed=False, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.post("/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


# ===== idempotency ===========================================================

def test_kill_twice_second_call_gets_404_not_found(server, run_async, monkeypatch):
    _mock_kill(monkeypatch, killed=True, alive_after=False)

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            first = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert first.status == 200
            second = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            # The label no longer resolves in the caller's scope at all
            # now -- this 404 comes from route-level resolution, not
            # from _kill_session_core's own post-lookup race branch.
            assert second.status == 404
            assert await second.json() == {"error": "not_found"}
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())
