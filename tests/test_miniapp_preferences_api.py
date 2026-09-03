"""The Mini App's first WRITE route: GET/PUT /api/preferences.

Every later batch (session control, per-session settings, session
creation) copies this gate, so these tests are deliberately adversarial:
the page is reachable over a public tunnel, and a signed `initData` only
proves *a* Telegram user, never an authorized one.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

ADMIN_ID = 555      # role with bypass_safety -> may write
MEMBER_ID = 777     # ordinary member -> may read, must not write
OUTSIDER_ID = 999   # in no scope at all


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


class _Role:
    def __init__(self, bypass_safety):
        self.bypass_safety = bypass_safety


class _Policy:
    """Minimal stand-in for the real policy: 'admin' bypasses safety
    (and so may write settings), every other role does not."""

    def get_role(self, name):
        return _Role(name == "admin")


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=-100, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=MEMBER_ID, label="bob", role="developer"),
        ),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    return MiniAppServer(bot, registry, port=8765)


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


# ===== read ===============================================================

def test_get_preferences_returns_schema_and_values(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.get("/api/preferences", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            body = await resp.json()
            fields = {g["field"] for g in body["schema"]}
            # Every settable field the chat menu exposes must be present —
            # the schema is shared, not a second hand-written list.
            assert fields == {
                "layout", "simple_formatting", "answer_length", "language_level",
                "diff_preview",
            }
            assert set(body["values"]) == fields
            assert body["can_edit"] is True
        finally:
            await client.close()
    run_async(_run())


def test_every_group_keeps_a_dont_apply_any_rule_option(server, run_async):
    """The operator added that option deliberately in v0.6.0."""
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/preferences", headers=_hdr(ADMIN_ID))).json()
            for group in body["schema"]:
                if group["field"] in ("answer_length", "language_level"):
                    assert any(o["value"] == "none" for o in group["options"])
        finally:
            await client.close()
    run_async(_run())


def test_non_admin_member_can_read_but_is_told_they_cannot_edit(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.get("/api/preferences", headers=_hdr(MEMBER_ID))
            assert resp.status == 200
            assert (await resp.json())["can_edit"] is False
        finally:
            await client.close()
    run_async(_run())


# ===== the write gate =====================================================

def test_write_persists_and_is_readable_back(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            assert resp.status == 200
            assert (await resp.json())["values"]["answer_length"] == "short"

            again = await client.get("/api/preferences", headers=_hdr(ADMIN_ID))
            assert (await again.json())["values"]["answer_length"] == "short"
        finally:
            await client.close()
    run_async(_run())


def test_write_without_init_data_is_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/preferences/answer_length", json={"value": "short"})
            assert resp.status == 401
            # And nothing was written.
            body = await (await client.get(
                "/api/preferences", headers=_hdr(ADMIN_ID))).json()
            assert body["values"]["answer_length"] != "short"
        finally:
            await client.close()
    run_async(_run())


def test_write_with_signature_from_a_different_bot_token_is_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            forged = _init_data(ADMIN_ID, bot_token="999999:WRONG-TOKEN-VALUE")
            resp = await client.put(
                "/api/preferences/answer_length",
                headers={"X-Telegram-Init-Data": forged}, json={"value": "short"},
            )
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


def test_write_with_stale_auth_date_is_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            stale = _init_data(ADMIN_ID, auth_date=int(time.time()) - 4000)
            resp = await client.put(
                "/api/preferences/answer_length",
                headers={"X-Telegram-Init-Data": stale}, json={"value": "short"},
            )
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


def test_validly_signed_outsider_is_rejected(server, run_async):
    """A real Telegram user who belongs to no scope."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/preferences/answer_length",
                headers=_hdr(OUTSIDER_ID), json={"value": "short"},
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_non_admin_member_write_is_rejected_and_changes_nothing(server, run_async):
    """Authentication proves a member; changing a scope-wide setting is
    admin-gated exactly as the chat callback is."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/preferences/answer_length",
                headers=_hdr(MEMBER_ID), json={"value": "long"},
            )
            assert resp.status == 403
            body = await (await client.get(
                "/api/preferences", headers=_hdr(ADMIN_ID))).json()
            assert body["values"]["answer_length"] != "long"
        finally:
            await client.close()
    run_async(_run())


# ===== server-side validation (the UI is not the gate) ====================

def test_unknown_field_is_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/preferences/not_a_real_field",
                headers=_hdr(ADMIN_ID), json={"value": "x"},
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("field,value", [
    ("answer_length", "enormous"),
    ("layout", "sideways"),
    ("language_level", "l33t"),
    ("simple_formatting", "yes-please"),   # must be a real bool
    ("answer_length", None),
    ("answer_length", {"nested": "object"}),
])
def test_invalid_value_is_rejected(server, run_async, field, value):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.put(
                f"/api/preferences/{field}",
                headers=_hdr(ADMIN_ID), json={"value": value},
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


def test_malformed_body_is_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/preferences/answer_length",
                headers=_hdr(ADMIN_ID), data="not json",
            )
            assert resp.status == 400
            resp2 = await client.put(
                "/api/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"no_value_key": 1},
            )
            assert resp2.status == 400
        finally:
            await client.close()
    run_async(_run())


# ===== idempotency + the chat mirror ======================================

def test_repeat_write_of_the_same_value_does_not_re_mirror_to_chat(server, run_async):
    async def _run():
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.put("/api/preferences/answer_length",
                             headers=_hdr(ADMIN_ID), json={"value": "medium"})
            assert send.await_count == 1
            resp = await client.put("/api/preferences/answer_length",
                                    headers=_hdr(ADMIN_ID), json={"value": "medium"})
            # Idempotent: still succeeds, but says nothing changed and
            # does not post a second line to the chat.
            assert resp.status == 200
            assert (await resp.json())["changed"] is False
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())


def test_mirror_goes_to_the_callers_own_scope_chat(server, run_async):
    async def _run():
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.put("/api/preferences/layout",
                             headers=_hdr(ADMIN_ID), json={"value": "merged"})
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == -100
        finally:
            await client.close()
    run_async(_run())


def test_a_failing_chat_mirror_does_not_fail_the_write(server, run_async):
    """The value is already persisted by the time we mirror — losing the
    notification must not turn a successful write into a 500."""
    async def _run():
        client = await _client_for(server)
        server.bot._app.bot.send_message.side_effect = RuntimeError("telegram down")
        try:
            resp = await client.put("/api/preferences/answer_length",
                                    headers=_hdr(ADMIN_ID), json={"value": "long"})
            assert resp.status == 200
            assert (await resp.json())["values"]["answer_length"] == "long"
        finally:
            await client.close()
    run_async(_run())


# ===== bounded =============================================================

def test_write_route_is_rate_limited(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(45):
                value = "short" if i % 2 else "long"
                resp = await client.put(
                    "/api/preferences/answer_length",
                    headers=_hdr(ADMIN_ID), json={"value": value},
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "write route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


# ===== the read routes stay GET-only ======================================

@pytest.mark.parametrize("path", [
    "/api/status", "/api/sessions", "/api/sessions/dev", "/api/sessions/dev/diff",
])
def test_read_routes_still_reject_writes(server, run_async, path):
    async def _run():
        client = await _client_for(server)
        try:
            for method in ("put", "post", "delete", "patch"):
                if method == "post" and path == "/api/sessions":
                    continue   # batch 5's create route — see test_miniapp_session_create_api
                if method == "delete" and path == "/api/sessions/dev":
                    # The session-controls batch added DELETE on this exact
                    # path (the Delete action) — see
                    # test_miniapp_session_controls_api.py.
                    continue
                resp = await getattr(client, method)(path, headers=_hdr(ADMIN_ID))
                assert resp.status == 405, (
                    f"{method.upper()} {path} returned {resp.status}, expected 405"
                )
        finally:
            await client.close()
    run_async(_run())


def test_diff_preview_round_trips_through_the_api(server, run_async):
    """Guard 5 ("diff-preview-settings-toggle"): the new field reads back
    False by default, accepts a boolean write, and rejects a non-boolean
    through the same allow-list chat uses."""
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/preferences", headers=_hdr(ADMIN_ID))).json()
            assert body["values"]["diff_preview"] is False
            resp = await client.put(
                "/api/preferences/diff_preview",
                headers=_hdr(ADMIN_ID), json={"value": True},
            )
            assert resp.status == 200
            assert (await resp.json())["values"]["diff_preview"] is True
            again = await (await client.get(
                "/api/preferences", headers=_hdr(ADMIN_ID))).json()
            assert again["values"]["diff_preview"] is True
            bad = await client.put(
                "/api/preferences/diff_preview",
                headers=_hdr(ADMIN_ID), json={"value": "yes"},
            )
            assert bad.status == 400
        finally:
            await client.close()
    run_async(_run())
