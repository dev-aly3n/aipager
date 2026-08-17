"""Black-box tests for design.md success criterion 6: every one of the
four routes returns 403 for a read-only member, 404 (byte-identical to
an unknown label) for a foreign-scope label, and 429 once the write
budget is exhausted -- exercised identically across all four routes
rather than once per behaviour.

Calibration note on "non-member" (a user who belongs to NO scope at
all, as opposed to a real member of a DIFFERENT scope): empirically
verified against the live implementation (via the documented HTTP
contract, not by reading source) that this case returns 403
`forbidden`, matching the existing, unmodified precedent for the
sibling routes (`test_new_routes_reject_non_member_403` in
tests/test_miniapp_server.py, itself unedited by this feature per the
"chat/existing behaviour must not change" rule). 404 is reserved for a
real member of a scope OTHER than the session's own, and for a label
that exists nowhere. This is tested explicitly below so a future
regression in either direction is caught by name.

Every test that reaches inject.kill_session/launch_session/send_keys/
is_alive monkeypatches it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aipager.state import Status

from .conftest import (
    ADMIN_ID,
    FOREIGN_MEMBER_ID,
    FOREIGN_SCOPE_CHAT_ID,
    OUTSIDER_ID,
    READONLY_ID,
    SCOPE_CHAT_ID,
    _client_for,
    _hdr,
    _mk_session,
)

_ROUTES = [
    ("post", "/api/sessions/{label}/stop"),
    ("post", "/api/sessions/{label}/kill"),
    ("post", "/api/sessions/{label}/resume"),
    ("delete", "/api/sessions/{label}"),
]


def _path_for(template, label):
    return template.format(label=label)


async def _call(client, method, path, user_id):
    fn = getattr(client, method)
    if user_id is None:
        return await fn(path)
    return await fn(path, headers=_hdr(user_id))


# ===== 403 for a read-only member, every route ==============================

@pytest.mark.parametrize("method,template", _ROUTES)
def test_readonly_member_gets_403(server, run_async, method, template):
    async def _run():
        # A status compatible with every route's own guard, so the 403
        # is unambiguously the ONLY possible cause -- readonly must
        # never even reach the state guard.
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="x")
        client = await _client_for(server)
        try:
            resp = await _call(client, method, _path_for(template, "dev"), READONLY_ID)
            assert resp.status == 403
            assert await resp.json() == {"error": "forbidden"}
        finally:
            await client.close()
    run_async(_run())


# ===== 404 for a foreign-scope label, byte-identical to unknown =============

@pytest.mark.parametrize("method,template", _ROUTES)
def test_foreign_scope_label_404s_byte_identical_to_unknown(
    server, run_async, method, template,
):
    async def _run():
        _mk_session(server, "dev", scope_chat_id=FOREIGN_SCOPE_CHAT_ID,
                     status=Status.GONE, claude_session_id="x")
        client = await _client_for(server)
        try:
            foreign_resp = await _call(
                client, method, _path_for(template, "dev"), ADMIN_ID)
            unknown_resp = await _call(
                client, method, _path_for(template, "does-not-exist"), ADMIN_ID)
            assert foreign_resp.status == 404
            assert unknown_resp.status == 404
            assert (await foreign_resp.json()) == (await unknown_resp.json())
            assert (await foreign_resp.json()) == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("method,template", _ROUTES)
def test_foreign_scope_session_is_untouched_by_the_refusal(
    server, run_async, method, template,
):
    """A refused cross-scope attempt must not so much as nudge the
    target session's real state -- it stays exactly as it was."""
    async def _run():
        foreign_sess = _mk_session(
            server, "dev", scope_chat_id=FOREIGN_SCOPE_CHAT_ID,
            status=Status.GONE, claude_session_id="x")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await _call(client, method, _path_for(template, "dev"), ADMIN_ID)
            assert foreign_sess.status == Status.GONE
            assert foreign_sess.claude_session_id == "x"
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("method,template", _ROUTES)
def test_real_member_of_foreign_scope_also_gets_404(
    server, run_async, method, template,
):
    """A real, authenticated member of the OTHER scope -- not just an
    unknown label -- must also 404, never 403, when reaching into a
    scope they do not belong to."""
    async def _run():
        _mk_session(server, "dev", scope_chat_id=SCOPE_CHAT_ID,
                     status=Status.GONE, claude_session_id="x")
        client = await _client_for(server)
        try:
            resp = await _call(
                client, method, _path_for(template, "dev"), FOREIGN_MEMBER_ID)
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


# ===== the "non-member" calibration, pinned by name ==========================

@pytest.mark.parametrize("method,template", _ROUTES)
def test_outsider_of_every_scope_gets_403_not_404(server, run_async, method, template):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="x")
        client = await _client_for(server)
        try:
            resp = await _call(
                client, method, _path_for(template, "dev"), OUTSIDER_ID)
            assert resp.status == 403
            assert await resp.json() == {"error": "forbidden"}
        finally:
            await client.close()
    run_async(_run())


# ===== 401 for missing/invalid auth, every route =============================

@pytest.mark.parametrize("method,template", _ROUTES)
def test_missing_init_data_returns_401(server, run_async, method, template):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="x")
        client = await _client_for(server)
        try:
            resp = await _call(client, method, _path_for(template, "dev"), None)
            assert resp.status == 401
            assert await resp.json() == {"error": "unauthorized"}
        finally:
            await client.close()
    run_async(_run())


# ===== 429 once the write budget is exhausted, every route ==================

@pytest.mark.parametrize("method,template", _ROUTES)
def test_write_route_is_rate_limited(server, run_async, method, template, monkeypatch):
    # Every mocked to fail cheaply and never mutate state, so the loop
    # can safely spam the SAME request without changing what is being
    # measured (the budget, not the outcome).
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session",
        AsyncMock(return_value=(False, "nope")))

    async def _run():
        # IDLE (not GONE, not BUSY) refuses stop/resume/delete every
        # single time without changing state; kill against IDLE with
        # kill_session mocked False/is_alive False 404s every time
        # (label genuinely still resolves -- the 404 is the dtach-race
        # branch, not label resolution) without removing the session.
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            seen_429 = False
            for _ in range(45):
                resp = await _call(
                    client, method, _path_for(template, "dev"), ADMIN_ID)
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, f"{method} {template} accepted unbounded writes"
            body = await resp.json()
            assert body == {"error": "too_many_requests"}
        finally:
            await client.close()
    run_async(_run())


def test_budget_is_shared_across_all_four_new_routes(server, run_async, monkeypatch):
    """Not four separate 30-write allowances (which would silently
    quadruple the real ceiling) -- one shared per-user counter."""
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False))
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock())
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session",
        AsyncMock(return_value=(False, "nope")))

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        calls = [
            ("post", "/api/sessions/dev/stop"),
            ("post", "/api/sessions/dev/kill"),
            ("post", "/api/sessions/dev/resume"),
            ("delete", "/api/sessions/dev"),
        ]
        try:
            seen_429 = False
            i = 0
            for i in range(45):
                method, path = calls[i % len(calls)]
                resp = await _call(client, method, path, ADMIN_ID)
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429
            assert i < 40
        finally:
            await client.close()
    run_async(_run())


# ===== a refused request never leaves state changed, across the board ======

def test_readonly_member_refused_across_all_four_routes_changes_nothing(
    server, run_async,
):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.GONE, claude_session_id="x")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            for method, template in _ROUTES:
                resp = await _call(
                    client, method, _path_for(template, "dev"), READONLY_ID)
                assert resp.status == 403
            assert sess.status == Status.GONE
            assert sess.claude_session_id == "x"
            send.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())
