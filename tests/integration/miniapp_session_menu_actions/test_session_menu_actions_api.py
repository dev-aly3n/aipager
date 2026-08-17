"""Black-box tests for the five new Mini App session-menu-action routes
(``perms``, ``clearqueue``, ``compact``, ``restart``, ``rename``) and the
extended ``GET /api/sessions/{label}`` response, per
``.ship/miniapp-session-menu-actions/entrypoints.md`` and
``design.md``'s eight numbered success criteria.

Written against the documented HTTP contract only — no source under
``aipager/miniapp/sessions.py`` or ``aipager/bot/session_ops.py`` was
read to derive expectations; every assertion traces to a table or
sentence in entrypoints.md/design.md. Fixture plumbing (HMAC initData
signing, role stand-ins, aiohttp TestClient/TestServer) mirrors
``tests/test_miniapp_session_controls_api.py``, the reference fixture
for this surface.

HARD RULE (design.md, spec.md): every test that reaches
``inject.kill_session``, ``inject.launch_session``, ``inject.send_keys``
or ``inject.is_alive`` monkeypatches it — no test here may kill a real
dtach session, launch a real claude process, touch a real dtach socket,
or send real keys. ``aipager.bot.session_ops.asyncio.sleep`` is
neutered wherever a poll loop might run so no test actually waits.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

SCOPE_CHAT_ID = -100
FOREIGN_SCOPE_CHAT_ID = -200

ADMIN_ID = 555          # bypass_safety AND can_prompt
DEVELOPER_ID = 777      # can_prompt, NOT bypass_safety
READONLY_ID = 888       # neither bypass_safety NOR can_prompt
OUTSIDER_ID = 999        # member of no scope at all
FOREIGN_MEMBER_ID = 321  # a real member, but of the OTHER scope

QUEUE_FULL_DETAIL = "Queue is full (50 pending) — clear it or wait for it to drain."
# The POST route's 403 `detail` and the GET actions dict's `reason` for
# the SAME refusal are two distinct strings per entrypoints.md — the
# route table says "Auto mode requires admin." while the actions-object
# section says "Switching to Auto mode requires admin.". Not a typo:
# tested separately below so a future accidental unification (or
# divergence) is caught either way.
PERMS_ADMIN_ROUTE_DETAIL = "Auto mode requires admin."
PERMS_ADMIN_ACTIONS_REASON = "Switching to Auto mode requires admin."
NO_PERMISSION_REASON = "You don't have permission to control this session."


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id, *, bot_token=BOT_TOKEN, auth_date=None):
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


class _Role:
    def __init__(self, *, bypass_safety=False, can_prompt=True):
        self.bypass_safety = bypass_safety
        self.can_prompt = can_prompt


class _Policy:
    """Minimal role stand-in — see test_miniapp_session_controls_api.py
    for why both ``bypass_safety`` and ``can_prompt`` must be present."""

    _ROLES = {
        "admin": _Role(bypass_safety=True, can_prompt=True),
        "developer": _Role(bypass_safety=False, can_prompt=True),
        "read_only": _Role(bypass_safety=False, can_prompt=False),
    }

    def get_role(self, name):
        return self._ROLES.get(name)


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=SCOPE_CHAT_ID, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=DEVELOPER_ID, label="bob", role="developer"),
            Member(id=READONLY_ID, label="cleo", role="read_only"),
        ),
    )
    foreign_scope = Scope(
        chat_id=FOREIGN_SCOPE_CHAT_ID, kind="group", label="other-team",
        members=(Member(id=FOREIGN_MEMBER_ID, label="zed", role="admin"),),
    )
    bot = mk_bot(registry, scopes=[scope, foreign_scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, registry, port=8768)


def _mk_session(
    server, label, *, scope_chat_id=SCOPE_CHAT_ID, status=Status.IDLE,
    claude_session_id="", skip_perms=False, pending_queue=None,
    cwd="/home/dev/proj",
):
    sess = server.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.status = status
    sess.claude_session_id = claude_session_id
    sess.skip_perms = skip_perms
    sess.cwd = cwd
    if pending_queue is not None:
        sess.pending_queue = list(pending_queue)
    if status == Status.GONE:
        sess.gone_at = time.monotonic()
    return sess


def _queue(n):
    now = time.time()
    return [(f"msg-{i}", None, now) for i in range(n)]


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


def _mock_inject_happy(monkeypatch):
    """Point every dtach seam a kill/relaunch or a prompt-injection path
    could plausibly reach at a mocked, always-succeeding double, and
    neuter session_ops's poll-loop sleep. No test in this file may let
    any of these functions run for real."""
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.send_keys", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.send_text_and_enter", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session",
        AsyncMock(return_value=(True, "")),
    )
    monkeypatch.setattr(
        "aipager.bot.session_ops.asyncio.sleep", AsyncMock(),
    )


# ===== Criterion 1/2: exact key sets, in order =============================

def test_busy_session_with_queued_messages_returns_exact_six_keys_in_order(
    server, run_async,
):
    """design.md success criterion 1."""
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, pending_queue=_queue(2))
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert list(body["actions"].keys()) == [
                "stop", "clearqueue", "compact", "rename", "perms", "restart",
            ]
        finally:
            await client.close()
    run_async(_run())


def test_gone_session_returns_exact_three_keys_in_order(server, run_async):
    """design.md success criterion 2."""
    async def _run():
        _mk_session(server, "dev", status=Status.GONE, claude_session_id="u1")
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert list(body["actions"].keys()) == ["resume", "rename", "delete"]
        finally:
            await client.close()
    run_async(_run())


def test_waiting_session_returns_exact_five_keys_in_order(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.INTERACTIVE)
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["status"] == "waiting"
            assert list(body["actions"].keys()) == [
                "stop", "clearqueue", "rename", "perms", "restart",
            ]
        finally:
            await client.close()
    run_async(_run())


def test_idle_session_returns_exact_five_key_set(server, run_async):
    """Only criteria 1 and 2 (busy, gone) pin an explicit ORDER;
    idle's row in entrypoints.md's table is exercised for its key SET
    here. (See issue tester-iter1-001: the observed order for idle is
    compact/rename/kill/perms/restart — matching design.md's stated
    canonical server-emission order filtered to idle's set — not the
    literal "kill, compact, rename, perms, restart" sequence written
    in entrypoints.md's prose table for that row.)"""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert set(body["actions"].keys()) == {
                "kill", "compact", "rename", "perms", "restart",
            }
        finally:
            await client.close()
    run_async(_run())


def test_unknown_session_has_no_actions_at_all(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.UNKNOWN)
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["status"] == "unknown"
            assert body["actions"] == {}
        finally:
            await client.close()
    run_async(_run())


def test_busy_session_with_empty_queue_omits_clearqueue_key_entirely(
    server, run_async,
):
    """clearqueue's ABSENCE (not merely unavailable=false) when nothing
    is queued would be a bug per entrypoints.md's status->keys table —
    the table lists clearqueue for busy/waiting unconditionally; its
    *availability* (not presence) depends on queue_depth. This pins
    that the key stays present with available=false rather than
    disappearing."""
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, pending_queue=[])
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert "clearqueue" in body["actions"]
            assert body["actions"]["clearqueue"] == {
                "available": False, "reason": "Nothing queued to clear.",
            }
        finally:
            await client.close()
    run_async(_run())


def test_new_top_level_fields_skip_perms_and_queue_depth_present(
    server, run_async,
):
    async def _run():
        _mk_session(
            server, "dev", status=Status.BUSY, skip_perms=True,
            pending_queue=_queue(3),
        )
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await resp.json()
            assert body["skip_perms"] is True
            assert body["queue_depth"] == 3
        finally:
            await client.close()
    run_async(_run())


# ===== Criterion 4 / perms asymmetry ========================================

def test_perms_nonadmin_targeting_auto_gets_403_with_exact_detail(
    server, run_async,
):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(DEVELOPER_ID),
            )
            assert resp.status == 403
            body = await resp.json()
            assert body["detail"] == PERMS_ADMIN_ROUTE_DETAIL
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_perms_full_asymmetric_round_trip_admin_auto_then_nonadmin_ask(
    server, run_async, monkeypatch,
):
    """Criterion 4, in full, and design.md's "perms twice in a row"
    sequence: a non-admin is refused Ask->Auto, an admin can do it, a
    GET reflects it, and the SAME non-admin then succeeds switching
    Auto->Ask. A test asserting only the refusal half would pass even
    if the code blocked BOTH directions — this proves the non-admin
    direction is genuinely open, not merely untested.

    Between the two switches, `restarting_until` is cleared directly —
    simulating the real few seconds a human takes between two taps,
    not bypassing any guard an attacker could exploit (the OTHER
    perms/restart tests exercise the guard itself, with the window
    still open)."""
    _mock_inject_happy(monkeypatch)

    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        try:
            refused = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(DEVELOPER_ID),
            )
            assert refused.status == 403

            as_admin = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert as_admin.status == 200
            assert (await as_admin.json())["skip_perms"] is True

            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["skip_perms"] is True

            sess.restarting_until = 0.0  # simulate time passing

            # Same non-admin, now switching Auto -> Ask: must be ALLOWED.
            back = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(DEVELOPER_ID),
            )
            assert back.status == 200, (
                "non-admin was refused Auto->Ask — the asymmetry only "
                "permits the reverse direction"
            )
            assert (await back.json())["skip_perms"] is False

            check2 = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check2.json())["skip_perms"] is False
        finally:
            await client.close()
    run_async(_run())


def test_perms_reason_string_in_get_actions_matches_documented_string(
    server, run_async,
):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        try:
            detail = await client.get(
                "/api/sessions/dev", headers=_hdr(DEVELOPER_ID),
            )
            body = await detail.json()
            assert body["actions"]["perms"] == {
                "available": False, "reason": PERMS_ADMIN_ACTIONS_REASON,
            }
        finally:
            await client.close()
    run_async(_run())


def test_perms_readonly_caller_sees_no_permission_reason_not_admin_reason(
    server, run_async,
):
    """can_act=False must win over the admin-specific reason — a
    read-only caller is told they can't control the session at all,
    not that Auto needs admin (entrypoints.md's generalised rule)."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        try:
            detail = await client.get(
                "/api/sessions/dev", headers=_hdr(READONLY_ID),
            )
            body = await detail.json()
            assert body["actions"]["perms"] == {
                "available": False, "reason": NO_PERMISSION_REASON,
            }
        finally:
            await client.close()
    run_async(_run())


def test_perms_readonly_caller_gets_403_forbidden_not_admin_detail(
    server, run_async,
):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
            body = await resp.json()
            assert body.get("detail") != PERMS_ADMIN_ROUTE_DETAIL
        finally:
            await client.close()
    run_async(_run())


def test_perms_refused_403_leaves_skip_perms_unchanged_and_no_mirror(
    server, run_async,
):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(DEVELOPER_ID),
            )
            assert resp.status == 403
            assert sess.skip_perms is False
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_perms_busy_session_success_mirrors_and_leaves_status_non_gone(
    server, run_async, monkeypatch,
):
    """Exercises the busy interrupt-first path — mock every possible
    dtach seam so the test doesn't need to know which one the core
    picks (Ctrl-C vs kill), per entrypoints.md's explicit exclusion of
    that mechanism from the observable contract."""
    _mock_inject_happy(monkeypatch)

    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, skip_perms=False)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["status"] != "gone"
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_perms_already_restarting_returns_409(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        sess.restarting_until = time.monotonic() + 100.0
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "already_restarting"
        finally:
            await client.close()
    run_async(_run())


def test_perms_launch_failed_returns_400(server, run_async, monkeypatch):
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.send_keys", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session",
        AsyncMock(return_value=(False, "dtach broken")),
    )
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", AsyncMock())

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, skip_perms=False)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 400
            body = await resp.json()
            assert body == {"error": "launch_failed", "detail": "dtach broken"}
        finally:
            await client.close()
    run_async(_run())


def test_perms_not_live_status_gone_returns_409(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/perms", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
        finally:
            await client.close()
    run_async(_run())


# ===== Criterion 3 / compact at cap vs below ================================

def test_compact_at_queue_cap_returns_409_queue_full(server, run_async):
    async def _run():
        _mk_session(
            server, "dev", status=Status.BUSY, pending_queue=_queue(50),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            body = await resp.json()
            assert body == {"error": "queue_full", "detail": QUEUE_FULL_DETAIL}
        finally:
            await client.close()
    run_async(_run())


def test_compact_refused_at_cap_leaves_queue_depth_unchanged(server, run_async):
    """Criterion 3's second half: a refused compact must not mutate
    the queue — verified by re-GETting, not by inspecting state
    directly, to prove the observable HTTP contract holds."""
    async def _run():
        _mk_session(
            server, "dev", status=Status.BUSY, pending_queue=_queue(50),
        )
        client = await _client_for(server)
        try:
            before = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            depth_before = (await before.json())["queue_depth"]

            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409

            after = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            depth_after = (await after.json())["queue_depth"]
            assert depth_after == depth_before == 50
        finally:
            await client.close()
    run_async(_run())


def test_compact_refused_at_cap_sends_no_mirror(server, run_async):
    async def _run():
        _mk_session(
            server, "dev", status=Status.BUSY, pending_queue=_queue(50),
        )
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_compact_below_cap_succeeds_and_increments_queue_depth(
    server, run_async,
):
    async def _run():
        _mk_session(
            server, "dev", status=Status.BUSY, pending_queue=_queue(3),
        )
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert (await resp.json())["status"] == "queued"

            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["queue_depth"] == 4

            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())


def test_compact_one_below_cap_at_49_still_succeeds_boundary(
    server, run_async,
):
    """Boundary-value: 49 (just inside) succeeds; 50 (the cap itself,
    tested above) refuses."""
    async def _run():
        _mk_session(
            server, "dev", status=Status.BUSY, pending_queue=_queue(49),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_compact_idle_session_sends_immediately(server, run_async, monkeypatch):
    monkeypatch.setattr(
        "aipager.dtach.inject.send_keys", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.send_text_and_enter", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=True),
    )

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert (await resp.json())["status"] == "sent"
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())


def test_compact_waiting_session_refused_not_live(server, run_async):
    """entrypoints.md's status->keys table excludes compact for
    "waiting"; the route itself must independently refuse it too (the
    design's own "every route re-validates" belt-and-braces claim), not
    merely hide the menu button."""
    async def _run():
        _mk_session(server, "dev", status=Status.INTERACTIVE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
        finally:
            await client.close()
    run_async(_run())


def test_compact_gone_session_refused_not_live(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
        finally:
            await client.close()
    run_async(_run())


# ===== clearqueue: empty vs populated, and it must NOT interrupt ===========

def test_clearqueue_empty_returns_409_queue_empty(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, pending_queue=[])
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/clearqueue", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            body = await resp.json()
            assert body == {
                "error": "queue_empty", "detail": "Nothing queued to clear.",
            }
        finally:
            await client.close()
    run_async(_run())


def test_clearqueue_empty_refusal_sends_no_mirror(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, pending_queue=[])
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.post(
                "/api/sessions/dev/clearqueue", headers=_hdr(ADMIN_ID),
            )
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_clearqueue_populated_drops_all_and_reports_count(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, pending_queue=_queue(4))
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/clearqueue", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {
                "status": "cleared", "label": "dev", "dropped": 4,
            }
            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["queue_depth"] == 0
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())


def test_clearqueue_does_not_interrupt_the_running_turn(server, run_async):
    """The explicit non-negotiable from entrypoints.md's side-effects
    section: clearing the queue must leave the in-progress turn
    running — status stays "busy", not flipped to idle/gone."""
    async def _run():
        sess = _mk_session(
            server, "dev", status=Status.BUSY, pending_queue=_queue(2),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/clearqueue", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert sess.status is Status.BUSY

            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["status"] == "busy"
        finally:
            await client.close()
    run_async(_run())


def test_clearqueue_waiting_session_with_queue_succeeds(server, run_async):
    """clearqueue is offered for waiting too, per the status table."""
    async def _run():
        _mk_session(
            server, "dev", status=Status.INTERACTIVE, pending_queue=_queue(1),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/clearqueue", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert (await resp.json())["dropped"] == 1
        finally:
            await client.close()
    run_async(_run())


# ===== restart ===============================================================

def test_restart_busy_session_succeeds(server, run_async, monkeypatch):
    """Criterion 7, part 1."""
    _mock_inject_happy(monkeypatch)

    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, claude_session_id="uuid-1")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert await resp.json() == {"status": "restarted", "label": "dev"}
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())


def test_restart_never_reports_gone_mid_flight(server, run_async, monkeypatch):
    """Criterion 7, part 2: a GET fired WHILE the restart's kill step
    is in flight (before relaunch completes) must never report
    "gone". The probe runs inside the mocked kill_session's own
    side_effect, so it genuinely lands inside the window between kill
    and relaunch, not merely before or after the whole request."""
    seen_statuses = []

    async def _kill_side_effect(*a, **k):
        probe = await probe_client.get(
            "/api/sessions/dev", headers=_hdr(ADMIN_ID),
        )
        seen_statuses.append((await probe.json())["status"])
        return True

    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(side_effect=_kill_side_effect),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.send_keys", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session",
        AsyncMock(return_value=(True, "")),
    )
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", AsyncMock())

    async def _run():
        nonlocal probe_client
        _mk_session(server, "dev", status=Status.BUSY, claude_session_id="uuid-1")
        client = await _client_for(server)
        probe_client = client
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert seen_statuses, "the kill_session mock was never reached"
            assert "gone" not in seen_statuses, (
                f"a poll during the restart window reported gone: {seen_statuses}"
            )
        finally:
            await client.close()

    probe_client = None
    run_async(_run())


def test_restart_preserves_history_and_permission_mode(server, run_async, monkeypatch):
    """entrypoints.md: "relaunched with the same conversation history
    and the same permission mode"."""
    _mock_inject_happy(monkeypatch)

    async def _run():
        _mk_session(
            server, "dev", status=Status.BUSY, claude_session_id="uuid-keep-me",
            skip_perms=True,
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            body = await check.json()
            assert body["skip_perms"] is True
        finally:
            await client.close()
    run_async(_run())


def test_restart_preserves_pending_queue_not_dropped(server, run_async, monkeypatch):
    """entrypoints.md: "any queued messages behind it are preserved,
    not dropped" — restart's queue behaviour is the opposite of
    clearqueue's."""
    _mock_inject_happy(monkeypatch)

    async def _run():
        _mk_session(
            server, "dev", status=Status.BUSY, claude_session_id="uuid-1",
            pending_queue=_queue(2),
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            check = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert (await check.json())["queue_depth"] == 2
        finally:
            await client.close()
    run_async(_run())


def test_restart_already_restarting_returns_409(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.BUSY)
        sess.restarting_until = time.monotonic() + 100.0
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "already_restarting"
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_restart_launch_failed_returns_400(server, run_async, monkeypatch):
    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.send_keys", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive", AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "aipager.dtach.inject.launch_session",
        AsyncMock(return_value=(False, "boom")),
    )
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", AsyncMock())

    async def _run():
        _mk_session(server, "dev", status=Status.IDLE, claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 400
            assert await resp.json() == {
                "error": "launch_failed", "detail": "boom",
            }
        finally:
            await client.close()
    run_async(_run())


def test_restart_not_live_gone_session_returns_409(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/restart", headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "not_live"
        finally:
            await client.close()
    run_async(_run())


def test_two_overlapping_restarts_only_one_succeeds(server, run_async, monkeypatch):
    """design.md Risks: "two overlapping restart requests (double-tap,
    two tabs) — mitigated by the is_restarting() guard; the second
    409s instead of racing." Fired truly concurrently via
    asyncio.gather over real loopback sockets, not sequentially."""
    _mock_inject_happy(monkeypatch)
    # Give the kill step one real await so the two requests can
    # actually interleave instead of one completing before the other
    # is even scheduled.
    release = asyncio.Event()

    async def _slow_kill(*a, **k):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return True

    monkeypatch.setattr(
        "aipager.dtach.inject.kill_session", AsyncMock(side_effect=_slow_kill),
    )

    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, claude_session_id="uuid-1")
        client = await _client_for(server)
        try:
            r1, r2 = await asyncio.gather(
                client.post("/api/sessions/dev/restart", headers=_hdr(ADMIN_ID)),
                client.post("/api/sessions/dev/restart", headers=_hdr(DEVELOPER_ID)),
            )
            statuses = sorted([r1.status, r2.status])
            assert statuses == [200, 409], (
                f"expected exactly one 200 and one 409, got {statuses}"
            )
        finally:
            await client.close()
    run_async(_run())
    del release


# ===== rename ================================================================

def test_rename_to_own_current_name_is_idempotent_no_op(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "dev"},
            )
            assert resp.status == 200
            assert await resp.json() == {
                "status": "renamed", "label": "dev",
                "previous_label": "dev", "changed": False,
            }
            assert sess.label == "dev"
            assert send.await_count == 0, (
                "a no-op rename must not mirror to chat"
            )
        finally:
            await client.close()
    run_async(_run())


def test_rename_success_changes_label_and_mirrors(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "devnew"},
            )
            assert resp.status == 200
            assert await resp.json() == {
                "status": "renamed", "label": "devnew",
                "previous_label": "dev", "changed": True,
            }
            assert sess.label == "devnew"
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
        finally:
            await client.close()
    run_async(_run())


def test_rename_collision_with_live_session_returns_409(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        _mk_session(server, "taken", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "taken"},
            )
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == "conflict"
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_rename_collision_with_gone_but_resumable_session_returns_409(
    server, run_async,
):
    """Rename must reject into a GONE-but-resumable label, same rule
    /new applies — a resumed GONE session recreates the ambiguity."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        _mk_session(
            server, "taken", status=Status.GONE, claude_session_id="uuid-resumable",
        )
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "taken"},
            )
            assert resp.status == 409
            assert (await resp.json())["error"] == "conflict"
        finally:
            await client.close()
    run_async(_run())


def test_rename_into_gone_and_unresumable_label_succeeds(server, run_async):
    """A GONE session with no claude_session_id can never come back to
    collide — rename into its old label must be ALLOWED. A test that
    only checked the resumable-blocks case would pass even if the
    code blocked GONE labels unconditionally."""
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        _mk_session(server, "free", status=Status.GONE, claude_session_id="")
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "free"},
            )
            assert resp.status == 200, (
                f"rename into a GONE-unresumable label was refused: "
                f"{await resp.json()}"
            )
            assert (await resp.json())["changed"] is True
            assert sess.label == "free"
        finally:
            await client.close()
    run_async(_run())


def test_rename_foreign_scope_caller_404s_like_unknown_label(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(FOREIGN_MEMBER_ID),
                json={"label": "newname"},
            )
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("bad_label,why", [
    ("", "empty"),
    ("a" * 65, "too long (65 chars)"),
    ("bad name!", "invalid characters"),
    ("new", "reserved word"),
])
def test_rename_invalid_names_return_400_bad_request(
    server, run_async, bad_label, why,
):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": bad_label},
            )
            assert resp.status == 400, f"{why!r} should be rejected"
            assert (await resp.json())["error"] == "bad_request"
            assert sess.label == "dev", f"{why!r} must not mutate the label"
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


def test_rename_malformed_json_body_returns_400(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename",
                headers={**_hdr(ADMIN_ID), "Content-Type": "application/json"},
                data="not json{{{",
            )
            assert resp.status == 400
            assert (await resp.json())["error"] == "bad_request"
        finally:
            await client.close()
    run_async(_run())


def test_rename_missing_label_field_returns_400(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={},
            )
            assert resp.status == 400
            assert (await resp.json())["error"] == "bad_request"
        finally:
            await client.close()
    run_async(_run())


def test_rename_readonly_member_gets_403(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(READONLY_ID),
                json={"label": "newname"},
            )
            assert resp.status == 403
            assert sess.label == "dev"
        finally:
            await client.close()
    run_async(_run())


# ===== Criterion 6 / sequence: rename then GET old and new =================

def test_rename_then_get_by_new_label_succeeds_and_by_old_label_404s(
    server, run_async,
):
    """Criterion 6."""
    async def _run():
        _mk_session(server, "dev", status=Status.IDLE)
        client = await _client_for(server)
        try:
            pre = await client.get("/api/sessions/dev", headers=_hdr(ADMIN_ID))
            assert pre.status == 200

            rename = await client.post(
                "/api/sessions/dev/rename", headers=_hdr(ADMIN_ID),
                json={"label": "devnew"},
            )
            assert rename.status == 200

            by_new = await client.get(
                "/api/sessions/devnew", headers=_hdr(ADMIN_ID),
            )
            assert by_new.status == 200
            assert (await by_new.json())["label"] == "devnew"

            by_old = await client.get(
                "/api/sessions/dev", headers=_hdr(ADMIN_ID),
            )
            assert by_old.status == 404
        finally:
            await client.close()
    run_async(_run())


# ===== Authorization matrix across all five routes ==========================

_ALL_FIVE = [
    ("post", "/perms", None),
    ("post", "/clearqueue", None),
    ("post", "/compact", None),
    ("post", "/restart", None),
    ("post", "/rename", {"label": "somethingnew"}),
]


@pytest.mark.parametrize("method,suffix,body", _ALL_FIVE)
def test_missing_init_data_header_returns_401_on_every_route(
    server, run_async, method, suffix, body,
):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, pending_queue=_queue(1))
        client = await _client_for(server)
        try:
            kwargs = {"json": body} if body is not None else {}
            resp = await getattr(client, method)(
                f"/api/sessions/dev{suffix}", **kwargs,
            )
            assert resp.status == 401
            assert await resp.json() == {"error": "unauthorized"}
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("method,suffix,body", _ALL_FIVE)
def test_readonly_member_gets_403_on_every_route(
    server, run_async, method, suffix, body,
):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, pending_queue=_queue(1))
        client = await _client_for(server)
        try:
            kwargs = {"json": body} if body is not None else {}
            resp = await getattr(client, method)(
                f"/api/sessions/dev{suffix}", headers=_hdr(READONLY_ID), **kwargs,
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("method,suffix,body", _ALL_FIVE)
def test_outsider_gets_403_on_every_route(
    server, run_async, method, suffix, body,
):
    async def _run():
        _mk_session(server, "dev", status=Status.BUSY, pending_queue=_queue(1))
        client = await _client_for(server)
        try:
            kwargs = {"json": body} if body is not None else {}
            resp = await getattr(client, method)(
                f"/api/sessions/dev{suffix}", headers=_hdr(OUTSIDER_ID), **kwargs,
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("label", ["ghost-does-not-exist", "other"])
@pytest.mark.parametrize("method,suffix,body", _ALL_FIVE)
def test_foreign_or_unknown_label_404s_identically_on_every_route(
    server, run_async, method, suffix, body, label,
):
    async def _run():
        # "other" genuinely exists — but in the FOREIGN scope.
        _mk_session(
            server, "other", scope_chat_id=FOREIGN_SCOPE_CHAT_ID,
            status=Status.BUSY, pending_queue=_queue(1),
        )
        client = await _client_for(server)
        try:
            kwargs = {"json": body} if body is not None else {}
            resp = await getattr(client, method)(
                f"/api/sessions/{label}{suffix}", headers=_hdr(ADMIN_ID), **kwargs,
            )
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


def test_404_matches_the_existing_detail_route_404_byte_for_byte_for_new_routes(
    server, run_async,
):
    async def _run():
        client = await _client_for(server)
        try:
            detail_resp = await client.get(
                "/api/sessions/ghost", headers=_hdr(ADMIN_ID),
            )
            rename_resp = await client.post(
                "/api/sessions/ghost/rename", headers=_hdr(ADMIN_ID),
                json={"label": "x"},
            )
            assert detail_resp.status == rename_resp.status == 404
            assert await detail_resp.json() == await rename_resp.json()
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("method,suffix,body", _ALL_FIVE)
def test_rate_limited_returns_429_on_every_route(
    server, run_async, method, suffix, body, monkeypatch,
):
    _mock_inject_happy(monkeypatch)

    async def _run():
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(35):
                # Fresh session each request for perms/restart/rename so
                # a successful call on one iteration doesn't put the
                # session into a state (e.g. already_restarting/gone)
                # that would itself 409 and mask the rate limit.
                _mk_session(
                    server, f"dev{i}", status=Status.BUSY,
                    pending_queue=_queue(1),
                )
                this_body = (
                    {"label": f"renamed{i}"} if body is not None else None
                )
                kwargs = {"json": this_body} if this_body is not None else {}
                resp = await getattr(client, method)(
                    f"/api/sessions/dev{i}{suffix}", headers=_hdr(ADMIN_ID),
                    **kwargs,
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, f"{suffix} route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


def test_rate_limit_ceiling_is_shared_across_different_routes(
    server, run_async, monkeypatch,
):
    """design.md Risks: "_WRITE_MAX_PER_WINDOW (30/60s) now shared
    across nine write actions instead of four." Exhaust it via
    clearqueue, then confirm a DIFFERENT route (compact) for the SAME
    caller is also refused in the same window — proving the ceiling is
    per-caller, not silently re-instantiated per-route."""
    async def _run():
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(35):
                _mk_session(
                    server, f"q{i}", status=Status.BUSY, pending_queue=_queue(1),
                )
                resp = await client.post(
                    f"/api/sessions/q{i}/clearqueue", headers=_hdr(ADMIN_ID),
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "did not reach the rate ceiling via clearqueue"

            _mk_session(
                server, "other-route", status=Status.BUSY, pending_queue=_queue(1),
            )
            resp2 = await client.post(
                "/api/sessions/other-route/compact", headers=_hdr(ADMIN_ID),
            )
            assert resp2.status == 429, (
                "compact was not rate-limited even though the SAME caller "
                "just exhausted the ceiling on clearqueue — the limiter is "
                "not actually shared across routes"
            )
        finally:
            await client.close()
    run_async(_run())
