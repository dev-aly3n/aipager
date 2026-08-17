"""Black-box exploratory tests beyond the eight numbered success
criteria: state-transition sequences, label edge cases (encoding,
length, emptiness), and cross-scope label collisions -- per the
orchestrator's explicit ask to go beyond the checklist.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from urllib.parse import quote

from aipager.state import Status

from .conftest import (
    ADMIN_ID,
    FOREIGN_SCOPE_CHAT_ID,
    _client_for,
    _hdr,
    _mk_session,
)


# ===== sequences: stop -> kill ==============================================

def test_stop_then_kill_same_session(server, run_async, monkeypatch):
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False))

    async def _run():
        _mk_session(server, "dev", status=Status.BUSY)
        client = await _client_for(server)
        try:
            stop_resp = await client.post(
                "/api/sessions/dev/stop", headers=_hdr(ADMIN_ID))
            assert stop_resp.status == 200
            kill_resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert kill_resp.status == 200
            gone = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert gone.status == 404
        finally:
            await client.close()
    run_async(_run())


# ===== sequences: kill -> resume the same label =============================

def test_kill_then_resume_the_same_label_is_not_found_not_not_gone(
    server, run_async, monkeypatch,
):
    """Kill fully removes the session (registry.remove()) -- it does
    NOT leave a resumable GONE record behind, unlike a session that
    naturally finished. Attempting to resume a just-killed label must
    therefore 404 as an unknown label, never 409 not_gone (which would
    wrongly imply the label still exists but is live) and never 200."""
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False))

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            kill_resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert kill_resp.status == 200
            resume_resp = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert resume_resp.status == 404
            assert await resume_resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


# ===== sequences: delete -> GET =============================================

def test_delete_then_get_404s(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            del_resp = await client.delete(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert del_resp.status == 200
            get_resp = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert get_resp.status == 404
            assert await get_resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


# ===== sequences: resume -> kill -> resume again =============================

def test_resume_then_kill_then_resume_again_refuses_not_found(
    server, run_async, monkeypatch,
):
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session", AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False))

    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            r1 = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert r1.status == 200
            r2 = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert r2.status == 200
            r3 = await client.post(
                "/api/sessions/dev/resume", headers=_hdr(ADMIN_ID))
            assert r3.status == 404
        finally:
            await client.close()
    run_async(_run())


# ===== a label that exists in another scope with the SAME name =============

def test_same_label_in_two_scopes_kill_only_affects_the_callers_own(
    server, run_async, monkeypatch,
):
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False))

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE,
                     name="claude-dev__mine")  # caller's own scope
        foreign = _mk_session(
            server, "dev", scope_chat_id=FOREIGN_SCOPE_CHAT_ID,
            status=Status.IDLE, name="claude-dev__foreign")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/kill", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            # Our own "dev" is gone...
            mine = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert mine.status == 404
            # ...but the foreign scope's identically-labelled "dev"
            # was never touched.
            assert foreign.status == Status.IDLE
        finally:
            await client.close()
    run_async(_run())


# ===== labels needing URL-encoding ==========================================

def test_label_with_a_space_round_trips_through_percent_encoding(
    server, run_async, monkeypatch,
):
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())

    async def _run():
        _mk_session(server, "my session", status=Status.BUSY)
        client = await _client_for(server)
        try:
            encoded = quote("my session", safe="")
            resp = await client.post(
                f"/api/sessions/{encoded}/stop", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            body = await resp.json()
            assert body["label"] == "my session"
        finally:
            await client.close()
    run_async(_run())


def test_label_with_reserved_url_characters_does_not_crash(server, run_async):
    """A label containing characters that need percent-encoding
    (`/`, `?`, `#`) must never crash the server -- it may be refused by
    the router before reaching our handler, or it may resolve to a
    clean not_found; either is acceptable, a 500 is not."""
    async def _run():
        client = await _client_for(server)
        try:
            for raw in ("a/b", "a?b", "a#b", "a%2Fb"):
                encoded = quote(raw, safe="")
                resp = await client.post(
                    f"/api/sessions/{encoded}/stop", headers=_hdr(ADMIN_ID))
                assert resp.status < 500, (
                    f"label {raw!r} crashed the server: {resp.status}")
        finally:
            await client.close()
    run_async(_run())


# ===== very long labels ======================================================

def test_very_long_unknown_label_returns_clean_404_not_a_crash(server, run_async):
    """Long enough to be an absurd label (far past anything a human or
    the create-route's own validation would ever produce), but short
    enough to stay under aiohttp's raw HTTP request-line limit so the
    request reaches OUR handler rather than being rejected by the
    transport before the app ever sees it."""
    async def _run():
        client = await _client_for(server)
        huge_label = "x" * 3000
        try:
            resp = await client.post(
                f"/api/sessions/{huge_label}/stop", headers=_hdr(ADMIN_ID))
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


# ===== empty labels ==========================================================

def test_empty_label_segment_does_not_crash(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions//stop", headers=_hdr(ADMIN_ID))
            assert resp.status < 500
        finally:
            await client.close()
    run_async(_run())


# ===== repeated identical requests: idempotency, one place per action ======

def test_repeated_identical_stop_kill_resume_delete_calls_refuse_coherently(
    server, run_async, monkeypatch,
):
    """A consolidated sweep: every one of the four routes, called twice
    in a row on a freshly-prepared, action-appropriate session, must
    succeed once and then refuse -- never succeed silently twice, never
    crash on the second call."""
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session", AsyncMock(return_value=(True, "")))

    async def _run():
        client = await _client_for(server)
        try:
            _mk_session(server, "s1", status=Status.BUSY)
            r1a = await client.post(
                "/api/sessions/s1/stop", headers=_hdr(ADMIN_ID))
            r1b = await client.post(
                "/api/sessions/s1/stop", headers=_hdr(ADMIN_ID))
            assert r1a.status == 200 and r1b.status == 409

            _mk_session(server, "s2", status=Status.IDLE)
            r2a = await client.post(
                "/api/sessions/s2/kill", headers=_hdr(ADMIN_ID))
            r2b = await client.post(
                "/api/sessions/s2/kill", headers=_hdr(ADMIN_ID))
            assert r2a.status == 200 and r2b.status == 404

            _mk_session(server, "s3", status=Status.GONE, claude_session_id="u")
            r3a = await client.post(
                "/api/sessions/s3/resume", headers=_hdr(ADMIN_ID))
            r3b = await client.post(
                "/api/sessions/s3/resume", headers=_hdr(ADMIN_ID))
            assert r3a.status == 200 and r3b.status == 409

            _mk_session(server, "s4", status=Status.GONE)
            r4a = await client.delete(
                "/api/sessions/s4", headers=_hdr(ADMIN_ID))
            r4b = await client.delete(
                "/api/sessions/s4", headers=_hdr(ADMIN_ID))
            assert r4a.status == 200 and r4b.status == 404
        finally:
            await client.close()
    run_async(_run())
