"""The Mini App shows every chat a person belongs to, one at a time
(roadmap 8.73, D-A; delivery 17 of the group-mode fixes).

Each request names its chat in the ``X-Aipager-Scope`` header. A named
chat must be one the caller is a member of (else 403) and a well-formed
chat id (else 400); it is never replaced by another chat. With no header
the request is about the default chat: the caller's own DM, else the
first chat that lists them. Every role check, session lookup and chat
mirror uses the chosen chat.

The world below lists the GROUP first in aipager.yaml order, so "the
first chat that lists you" and "your own DM" differ for the operator.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import SCOPE_HEADER, MiniAppServer, _parse_chat_id
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

OPERATOR = 111        # owner of their DM and of the group
BOB = 222             # owner of his own DM, plain `user` in the group
CLEO = 333            # a member of the group only (user)
STRANGER = 444        # a member of nothing
GROUP = -100222
OPERATOR_DM = OPERATOR
BOB_DM = BOB


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _init_data(user_id):
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id, chat=None):
    headers = {"X-Telegram-Init-Data": _init_data(user_id)}
    if chat is not None:
        headers[SCOPE_HEADER] = str(chat)
    return headers


def _policy():
    return load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))


def _mk_session(registry, name, label, chat, status=Status.IDLE):
    sess = registry.get_or_create(name)
    sess.label = label
    sess.scope_chat_id = chat
    sess.status = status
    sess.cwd = "/home/dev/proj"
    if status == Status.GONE:
        sess.gone_at = time.monotonic()
    return sess


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scopes = [
        Scope(chat_id=GROUP, kind="group", label="team",
              members=(Member(id=OPERATOR, label="op", role="owner"),
                       Member(id=BOB, label="bob", role="user"),
                       Member(id=CLEO, label="cleo", role="user"))),
        Scope(chat_id=OPERATOR_DM, kind="dm", label="op DM",
              members=(Member(id=OPERATOR, label="op", role="owner"),)),
        Scope(chat_id=BOB_DM, kind="dm", label="bob DM",
              members=(Member(id=BOB, label="bob", role="owner"),)),
    ]
    bot = mk_bot(registry, scopes=scopes)
    bot.policy = _policy()
    bot._app.bot.username = "aipager_test_bot"
    _mk_session(registry, "claude-grp", "grp", GROUP)
    _mk_session(registry, "claude-mine", "mine", OPERATOR_DM)
    _mk_session(registry, "claude-bobs", "bobs", BOB_DM)
    return MiniAppServer(bot, registry, port=8765)


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


def _labels(body):
    return sorted(row["label"] for row in body["sessions"])


# ===== the chat list and the default =======================================


def test_chats_lists_every_member_chat_in_yaml_order_and_defaults_to_the_dm(
    server, run_async,
):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.get("/api/chats", headers=_hdr(OPERATOR))
            assert resp.status == 200
            assert await resp.json() == {
                "chats": [{"scope": str(GROUP), "label": "team"},
                          {"scope": str(OPERATOR_DM), "label": "op DM"}],
                "current": str(OPERATOR_DM),
            }
        finally:
            await client.close()
    run_async(_run())


def test_a_request_without_a_chat_gets_the_callers_own_dm(server, run_async):
    """The group is first in aipager.yaml; the default is still the DM."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions", headers=_hdr(OPERATOR))
            assert resp.status == 200
            assert _labels(await resp.json()) == ["mine"]
        finally:
            await client.close()
    run_async(_run())


def test_a_group_only_member_sees_only_the_group(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            chats = await (await client.get("/api/chats", headers=_hdr(CLEO))).json()
            assert chats == {"chats": [{"scope": str(GROUP), "label": "team"}],
                             "current": str(GROUP)}
            resp = await client.get("/api/sessions", headers=_hdr(CLEO))
            assert _labels(await resp.json()) == ["grp"]
        finally:
            await client.close()
    run_async(_run())


# ===== switching ==========================================================


def test_switching_shows_only_the_chosen_chats_sessions(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            grp = await client.get("/api/sessions", headers=_hdr(OPERATOR, GROUP))
            assert grp.status == 200
            assert _labels(await grp.json()) == ["grp"]
            dm = await client.get("/api/sessions", headers=_hdr(OPERATOR, OPERATOR_DM))
            assert _labels(await dm.json()) == ["mine"]
            status = await client.get("/api/status", headers=_hdr(OPERATOR, GROUP))
            assert [s["label"] for s in (await status.json())["sessions"]] == ["grp"]
        finally:
            await client.close()
    run_async(_run())


def test_a_session_of_the_other_chat_is_not_found_in_the_chosen_one(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            ok = await client.get("/api/sessions/grp", headers=_hdr(OPERATOR, GROUP))
            assert ok.status == 200
            missing = await client.get("/api/sessions/grp",
                                       headers=_hdr(OPERATOR, OPERATOR_DM))
            assert missing.status == 404
            missing = await client.get("/api/sessions/mine", headers=_hdr(OPERATOR, GROUP))
            assert missing.status == 404
        finally:
            await client.close()
    run_async(_run())


def test_delete_acts_on_the_chosen_chat_and_mirrors_there(server, run_async):
    """The same label in both chats: the chosen chat's session goes, the
    other stays, and the notice lands in the chosen chat."""
    _mk_session(server.registry, "claude-old-g", "old", GROUP, Status.GONE)
    _mk_session(server.registry, "claude-old-d", "old", OPERATOR_DM, Status.GONE)

    async def _run():
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.delete("/api/sessions/old", headers=_hdr(OPERATOR, GROUP))
            assert resp.status == 200
            assert server.registry.get("claude-old-g") is None
            assert server.registry.get("claude-old-d") is not None
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == GROUP
            assert "Deleted [old] from the Mini App" in send.await_args.kwargs["text"]
        finally:
            await client.close()
    run_async(_run())


def test_settings_read_and_write_the_chosen_chat(server, run_async):
    from aipager.preferences import get_preferences

    async def _run():
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            before_dm = get_preferences(OPERATOR_DM).answer_length
            target = "short" if get_preferences(GROUP).answer_length != "short" else "medium"
            resp = await client.put("/api/preferences/answer_length",
                                    headers=_hdr(OPERATOR, GROUP), json={"value": target})
            assert resp.status == 200
            assert get_preferences(GROUP).answer_length == target
            assert get_preferences(OPERATOR_DM).answer_length == before_dm
            assert send.await_args.kwargs["chat_id"] == GROUP
            got = await (await client.get("/api/preferences",
                                          headers=_hdr(OPERATOR, GROUP))).json()
            assert got["values"]["answer_length"] == target
            assert got["can_edit"] is True
        finally:
            await client.close()
    run_async(_run())


def test_create_starts_the_session_in_the_chosen_chat(server, run_async):
    server.bot.create_session = AsyncMock(return_value=("claude-fresh__x", ""))

    async def _run():
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.post("/api/sessions", headers=_hdr(OPERATOR, GROUP),
                                     json={"name": "fresh"})
            assert resp.status == 200, await resp.text()
            assert server.bot.create_session.await_args.kwargs["scope_chat_id"] == GROUP
            assert send.await_args.kwargs["chat_id"] == GROUP
        finally:
            await client.close()
    run_async(_run())


# ===== the role is the chosen chat's =====================================


def test_a_user_in_the_group_is_refused_admin_things_there_but_not_in_their_dm(
    server, run_async,
):
    """Bob is `user` in the group and `owner` of his DM: settings writes,
    Auto at create, the admin hints and the update routes follow the
    chosen chat, in both directions."""
    server.bot.create_session = AsyncMock(return_value=("claude-auto__x", ""))

    async def _run():
        client = await _client_for(server)
        try:
            body = {"value": "short"}
            refused = await client.put("/api/preferences/answer_length",
                                       headers=_hdr(BOB, GROUP), json=body)
            assert refused.status == 403
            allowed = await client.put("/api/preferences/answer_length",
                                       headers=_hdr(BOB, BOB_DM), json=body)
            assert allowed.status == 200

            auto = {"name": "auto", "skip_perms": True}
            refused = await client.post("/api/sessions", headers=_hdr(BOB, GROUP), json=auto)
            assert refused.status == 403
            assert (await refused.json())["detail"] == "Auto mode requires admin."
            assert server.bot.create_session.await_count == 0
            allowed = await client.post("/api/sessions", headers=_hdr(BOB, BOB_DM), json=auto)
            assert allowed.status == 200, await allowed.text()
            kwargs = server.bot.create_session.await_args.kwargs
            assert kwargs["scope_chat_id"] == BOB_DM and kwargs["skip_perms"] is True

            hints = await (await client.get("/api/preferences",
                                            headers=_hdr(BOB, GROUP))).json()
            assert hints["can_edit"] is False and hints["can_update"] is False
            hints = await (await client.get("/api/preferences",
                                            headers=_hdr(BOB, BOB_DM))).json()
            assert hints["can_edit"] is True

            assert (await client.get("/api/update", headers=_hdr(BOB, GROUP))).status == 403
        finally:
            await client.close()
    run_async(_run())


def test_a_user_in_the_group_cannot_switch_a_group_session_to_auto(server, run_async):
    """The detail page's admin flag is the chosen chat's role."""
    async def _run():
        client = await _client_for(server)
        try:
            grp = await (await client.get("/api/sessions/grp",
                                          headers=_hdr(BOB, GROUP))).json()
            assert grp["actions"]["perms"]["available"] is False
            bobs = await (await client.get("/api/sessions/bobs",
                                           headers=_hdr(BOB, BOB_DM))).json()
            assert bobs["actions"]["perms"]["available"] is True
            refused = await client.post("/api/sessions/grp/perms",
                                        headers=_hdr(BOB, GROUP), json={"skip_perms": True})
            assert refused.status == 403
        finally:
            await client.close()
    run_async(_run())


# ===== a forged or malformed chat is refused, never replaced ===============


@pytest.mark.parametrize("route", ["/api/chats", "/api/sessions", "/api/preferences",
                                   "/api/session-options"])
def test_a_chat_the_caller_is_not_in_is_refused(server, run_async, route):
    """Cleo names the operator's DM: 403, not her group's data."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.get(route, headers=_hdr(CLEO, OPERATOR_DM))
            assert resp.status == 403
            assert await resp.json() == {"error": "forbidden"}
            # Bob names the operator's DM: refused, never his own DM.
            resp = await client.get(route, headers=_hdr(BOB, OPERATOR_DM))
            assert resp.status == 403
            # A chat nobody configured.
            resp = await client.get(route, headers=_hdr(OPERATOR, -100999))
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_a_forged_chat_cannot_act(server, run_async):
    _mk_session(server.registry, "claude-gone", "gone1", OPERATOR_DM, Status.GONE)

    async def _run():
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.delete("/api/sessions/gone1", headers=_hdr(CLEO, OPERATOR_DM))
            assert resp.status == 403
            assert server.registry.get("claude-gone") is not None
            assert send.await_count == 0
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("raw", [
    "abc", "1.5", "+111", "0111", "-0", "0", "111 222", "1e3", "１１１", "-",
    "12345678901234567890",
])
def test_a_malformed_chat_is_a_bad_request(server, run_async, raw):
    async def _run():
        client = await _client_for(server)
        try:
            headers = {"X-Telegram-Init-Data": _init_data(OPERATOR), SCOPE_HEADER: raw}
            resp = await client.get("/api/sessions", headers=headers)
            assert resp.status == 400
            assert await resp.json() == {"error": "bad_request"}
        finally:
            await client.close()
    run_async(_run())


def test_an_empty_chat_header_means_the_default(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            headers = {"X-Telegram-Init-Data": _init_data(OPERATOR), SCOPE_HEADER: ""}
            resp = await client.get("/api/sessions", headers=headers)
            assert resp.status == 200
            assert _labels(await resp.json()) == ["mine"]
        finally:
            await client.close()
    run_async(_run())


def test_the_chat_is_checked_after_the_signature(server, run_async):
    """An unsigned request naming a real chat is still a 401."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.get("/api/sessions", headers={SCOPE_HEADER: str(GROUP)})
            assert resp.status == 401
            resp = await client.get("/api/sessions", headers=_hdr(STRANGER, GROUP))
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_a_json_true_user_id_is_no_one(mk_bot, run_async):
    """initData's user id `true` is an int in Python and equals 1: it must
    not pass as the member whose id is 1."""
    registry = SessionRegistry()
    scope = Scope(chat_id=-100555, kind="group", label="ones",
                  members=(Member(id=1, label="one", role="owner"),))
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _policy()
    server = MiniAppServer(bot, registry, port=8765)
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": True, "first_name": "Test"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    async def _run():
        client = await _client_for(server)
        try:
            for headers in ({}, {SCOPE_HEADER: "-100555"}):
                resp = await client.get("/api/chats", headers={
                    "X-Telegram-Init-Data": urlencode(fields), **headers})
                assert resp.status == 403
            ok = await client.get("/api/chats", headers=_hdr(1))
            assert ok.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_parse_chat_id():
    assert _parse_chat_id("111") == 111
    assert _parse_chat_id("-1001234567890") == -1001234567890
    for bad in ("", " 111", "111 ", "0", "-0", "0x1", "abc", "1_000"):
        assert _parse_chat_id(bad) is None


# ===== parity: one chat changes nothing =====================================


def _strip_uptime(body):
    body = dict(body)
    body.pop("daemon", None)
    return body


def test_a_one_dm_install_answers_the_same_with_and_without_the_chat(
    mk_bot, run_async,
):
    registry = SessionRegistry()
    scope = Scope(chat_id=OPERATOR, kind="dm", label="owner DM",
                  members=(Member(id=OPERATOR, label="owner", role="owner"),))
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _policy()
    bot._app.bot.username = "aipager_test_bot"
    _mk_session(registry, "claude-mine", "mine", OPERATOR)
    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            chats = await (await client.get("/api/chats", headers=_hdr(OPERATOR))).json()
            assert chats == {"chats": [{"scope": str(OPERATOR), "label": "owner DM"}],
                             "current": str(OPERATOR)}
            for route in ("/api/sessions", "/api/sessions/mine", "/api/preferences",
                          "/api/session-options"):
                bare = await client.get(route, headers=_hdr(OPERATOR))
                named = await client.get(route, headers=_hdr(OPERATOR, OPERATOR))
                assert bare.status == named.status == 200
                assert _strip_uptime(await bare.json()) == _strip_uptime(await named.json())
            assert (await client.get("/api/sessions", headers=_hdr(OPERATOR, -1))).status == 403
        finally:
            await client.close()
    run_async(_run())


def test_personal_mode_has_one_chat_and_refuses_any_other(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    registry = SessionRegistry()
    bot = mk_bot(registry)   # personal mode: no scopes, no team
    bot._app.bot.username = "solo_bot"
    _mk_session(registry, "claude-solo", "solo", 555)
    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            chats = await (await client.get("/api/chats", headers=_hdr(555))).json()
            assert chats == {"chats": [{"scope": "555", "label": "DM"}], "current": "555"}
            bare = await client.get("/api/sessions", headers=_hdr(555))
            named = await client.get("/api/sessions", headers=_hdr(555, 555))
            assert _strip_uptime(await bare.json()) == _strip_uptime(await named.json())
            assert _labels(await bare.json()) == ["solo"]
            assert (await client.get("/api/sessions", headers=_hdr(555, 556))).status == 403
            # A stranger is refused with or without naming the operator's chat.
            assert (await client.get("/api/sessions", headers=_hdr(999))).status == 403
            assert (await client.get("/api/sessions", headers=_hdr(999, 555))).status == 403
        finally:
            await client.close()
    run_async(_run())


def test_legacy_team_mode_has_one_chat(mk_bot, run_async, monkeypatch):
    from aipager.team import Role, Team
    from aipager.team import User as TeamUser

    monkeypatch.setattr("aipager.config.CHAT_ID", "-100777")
    registry = SessionRegistry()
    team = Team(group_id=-100777,
                users={OPERATOR: TeamUser(id=OPERATOR, label="op", role=Role.ADMIN)})
    bot = mk_bot(registry, team=team)
    bot.policy = _policy()
    bot._app.bot.username = "team_bot"
    _mk_session(registry, "claude-t", "t", -100777)
    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = await _client_for(server)
        try:
            chats = await (await client.get("/api/chats", headers=_hdr(OPERATOR))).json()
            assert chats == {"chats": [{"scope": "-100777", "label": "Group"}],
                             "current": "-100777"}
            assert _labels(await (await client.get(
                "/api/sessions", headers=_hdr(OPERATOR, -100777))).json()) == ["t"]
            assert (await client.get("/api/sessions",
                                     headers=_hdr(OPERATOR, OPERATOR))).status == 403
            assert (await client.get("/api/sessions", headers=_hdr(STRANGER))).status == 403
        finally:
            await client.close()
    run_async(_run())


# ===== the page names the chat on every call ===============================


def test_every_request_the_page_makes_carries_the_chosen_chat():
    """authHeaders is the one place the page builds request headers, and
    it adds the chosen chat; no fetch builds its own headers. (The
    driven page checks the same at runtime: test_miniapp_js_smoke's
    chats_* scenarios.)"""
    import re

    from aipager.miniapp.static._app import APP_JS

    assert APP_JS.count('"X-Telegram-Init-Data"') == 1
    assert APP_JS.count(f'"{SCOPE_HEADER}"') == 1
    assert 'h["X-Aipager-Scope"] = chosenScope' in APP_JS
    assert not re.search(r"headers:\s*\{", APP_JS), "a fetch builds its own headers"
    calls = [m.start() for m in re.finditer(r"(?<![\w.])fetch\(", APP_JS)]
    assert len(calls) >= 10
    for at in calls:
        window = APP_JS[at:at + 400]
        if "headers: authHeaders(" in window or "headers: headers" in window:
            continue
        # updatesFetch builds `opts` just above its fetch(path, opts).
        assert APP_JS[at:at + 17] == "fetch(path, opts)", window
        assert "headers: headers" in APP_JS[at - 300:at], window
    for decl in re.findall(r"var headers = [^;]*;", APP_JS):
        assert decl == "var headers = authHeaders();", decl


def test_the_switcher_markup_starts_hidden_and_is_a_real_tap_target():
    from aipager.miniapp.static import INDEX_HTML
    from aipager.miniapp.static._styles import CSS

    assert '<div id="chat-switch" class="chat-switch" role="group" aria-label="Chat" hidden>' \
        in INDEX_HTML
    block = CSS[CSS.index(".chat-chip {"):]
    block = block[:block.index("}")]
    assert "min-height: 44px" in block
    assert "—" not in INDEX_HTML
