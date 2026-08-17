"""Black-box tests for `DELETE /api/sessions/{label}`.

design.md success criterion 5: on a GONE session, 200 and the label
disappears; on any non-GONE session, 409 conflict and the session
survives.

spec.md's headline safety rule: "Delete must 409 (or equivalent) unless
the session is GONE" -- server-side truth, not UI-side; a stale page or
a crafted request must not be able to forget a live session and orphan
its process. This file exercises every non-GONE status against DELETE,
not just one representative.
"""

from __future__ import annotations

from aipager.state import Status

from .conftest import (
    ADMIN_ID,
    DEVELOPER_ID,
    SCOPE_CHAT_ID,
    _client_for,
    _hdr,
    _mk_session,
)


# ===== happy path ============================================================

def test_delete_gone_session_returns_200(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            assert await resp.json() == {"status": "deleted", "label": "dev"}
        finally:
            await client.close()
    run_async(_run())


def test_delete_gone_session_disappears_from_detail_and_grid(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            await client.delete("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            detail = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert detail.status == 404
            grid = await client.get("/api/sessions", headers=_hdr(ADMIN_ID))
            labels = {s["label"] for s in (await grid.json())["sessions"]}
            assert "dev" not in labels
        finally:
            await client.close()
    run_async(_run())


def test_delete_sends_exactly_one_chat_message(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.delete("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_developer_non_admin_can_delete(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(DEVELOPER_ID))
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_delete_never_touches_inject(server, run_async, monkeypatch):
    """Delete's whole capability is registry.remove() -- it must never
    reach the dtach boundary at all, unlike Kill."""
    async def _boom(*a, **kw):
        raise AssertionError("DELETE must never call inject.* -- it does not touch process state")
    monkeypatch.setattr("aipager.dtach.inject.kill_session", _boom)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)
    monkeypatch.setattr("aipager.dtach.inject.launch_session", _boom)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", _boom)

    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


# ===== refusal: not gone, for EVERY non-GONE status =========================

def test_delete_busy_session_returns_409_conflict_and_survives(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == "conflict"
            assert body["detail"] == (
                "Session is still running. Use Kill to stop it first."
            )
            check = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert check.status == 200
            assert (await check.json())["status"] == "busy"
        finally:
            await client.close()
    run_async(_run())


def test_delete_idle_session_returns_409_conflict_and_survives(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            assert (await resp.json())["error"] == "conflict"
            check = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert check.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_delete_interactive_session_returns_409_conflict(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.INTERACTIVE)
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            assert (await resp.json())["error"] == "conflict"
        finally:
            await client.close()
    run_async(_run())


def test_delete_refusal_sends_no_chat_message(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.delete("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


# ===== idempotency ===========================================================

def test_delete_twice_second_call_returns_404_not_409(server, run_async):
    """After the first DELETE the label no longer resolves in the
    caller's scope at all -- the second identical request must refuse
    coherently as `not_found`, not repeat `conflict` (there is nothing
    left to conflict with) and not silently succeed again."""
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            first = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert first.status == 200
            second = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert second.status == 404
            assert await second.json() == {"error": "not_found"}
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())
