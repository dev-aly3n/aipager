"""SC-21 and the Mini App half of SC-4: ``can_report`` is true only for the
owner in a private scope; ``POST /api/report/preview`` opens the card in
the owner's DM and is owner only and rate limited (design.md Success
criteria 4, 21; spec.md "the Mini App row (API) triggers the card in the
DM"; entrypoints.md "HTTP routes").

Real routes over aiohttp's test server, signed with the Mini App tests'
own initData recipe.

Methods: equivalence partitioning over the caller (owner, admin, user,
stranger, no auth) and the chosen scope (private, group); boundary: the
write budget's first refusal; error guessing: bad signature, no owner,
an ambiguous owner, a muted DM."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.bot.flood import MUTE
from aipager.miniapp.server import SCOPE_HEADER, MiniAppServer

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
_PORTS = iter(range(8800, 8900))


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _hdr(user_id, *, scope=None, token=BOT_TOKEN):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    headers = {"X-Telegram-Init-Data": urlencode(fields)}
    if scope is not None:
        headers[SCOPE_HEADER] = str(scope)
    return headers


def _calls(bot, run_async, requests):
    """Run (method, path, headers) requests against one server; return
    [(status, json)]."""
    srv = MiniAppServer(bot, bot.registry, port=next(_PORTS))

    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        out = []
        try:
            for method, path, headers in requests:
                resp = await client.request(method, path, headers=headers,
                                            **({"json": {}} if method == "POST" else {}))
                try:
                    body = await resp.json()
                except Exception:  # noqa: BLE001 - a non-JSON answer
                    body = None
                out.append((resp.status, body))
            await __import__("asyncio").sleep(0.05)
        finally:
            await client.close()
        return out
    return run_async(go())


def _one(bot, run_async, method, path, headers):
    return _calls(bot, run_async, [(method, path, headers)])[0]


# ---- can_report ----------------------------------------------------------------------

def test_can_report_true_for_owner_in_personal_dm(bot, h, run_async):
    _, body = _one(bot, run_async, "GET", "/api/preferences", _hdr(h.OWNER))
    assert body["can_report"] is True


def test_can_report_true_for_owner_in_scope_dm(make_bot, h, run_async):
    bot = make_bot("scope")
    _, body = _one(bot, run_async, "GET", "/api/preferences", _hdr(h.OWNER, scope=h.OWNER))
    assert body["can_report"] is True


def test_can_report_false_for_owner_in_group(make_bot, h, run_async):
    bot = make_bot("scope")
    _, body = _one(bot, run_async, "GET", "/api/preferences", _hdr(h.OWNER, scope=h.GROUP))
    assert body["can_report"] is False


@pytest.mark.parametrize("member", ["ADMIN", "USER", "READ_ONLY"])
def test_can_report_false_for_non_owner(make_bot, h, run_async, member):
    bot = make_bot("scope")
    status, body = _one(bot, run_async, "GET", "/api/preferences", _hdr(getattr(h, member)))
    assert body["can_report"] is False


def test_can_report_false_without_owner(make_bot, h, run_async):
    bot = make_bot("scope_two_owners")
    _, body = _one(bot, run_async, "GET", "/api/preferences", _hdr(h.OWNER, scope=h.OWNER))
    assert body["can_report"] is False


# ---- POST /api/report/preview ------------------------------------------------------------

def test_owner_post_answers_opened(bot, h, run_async):
    assert _one(bot, run_async, "POST", "/api/report/preview", _hdr(h.OWNER)) == \
        (200, {"opened": True})


def test_owner_post_opens_card_in_dm(bot, h, run_async):
    _one(bot, run_async, "POST", "/api/report/preview", _hdr(h.OWNER))
    assert len(bot.tg.cards(h.OWNER)) == 1


def test_owner_post_from_group_scope_opens_card_in_dm(make_bot, h, run_async):
    bot = make_bot("scope")
    _one(bot, run_async, "POST", "/api/report/preview", _hdr(h.OWNER, scope=h.GROUP))
    assert len(bot.tg.cards(h.OWNER)) == 1


def test_owner_post_from_group_scope_posts_nothing_in_group(make_bot, h, run_async):
    bot = make_bot("scope")
    _one(bot, run_async, "POST", "/api/report/preview", _hdr(h.OWNER, scope=h.GROUP))
    assert bot.tg.sent(h.GROUP) == []


def test_miniapp_card_is_a_manual_report(bot, h, run_async):
    _one(bot, run_async, "POST", "/api/report/preview", _hdr(h.OWNER))
    card = bot.tg.cards(h.OWNER)[-1]
    assert json.loads(h.preview_block(card["text"]))["trigger"] == "manual"


def test_miniapp_card_is_sendable(bot, drive, h, run_async, net):
    _one(bot, run_async, "POST", "/api/report/preview", _hdr(h.OWNER))
    card = bot.tg.cards(h.OWNER)[-1]
    run_async(drive(bot).tap("_:rp:send", message_id=card["message_id"]))
    assert len(net.posts) == 1


@pytest.mark.parametrize("member", ["ADMIN", "USER", "READ_ONLY"])
def test_non_owner_post_is_403(make_bot, h, run_async, member):
    bot = make_bot("scope")
    assert _one(bot, run_async, "POST", "/api/report/preview",
                _hdr(getattr(h, member))) == (403, {"error": "forbidden"})


@pytest.mark.parametrize("member", ["ADMIN", "USER"])
def test_non_owner_post_opens_nothing(make_bot, h, run_async, member):
    bot = make_bot("scope")
    _one(bot, run_async, "POST", "/api/report/preview", _hdr(getattr(h, member)))
    assert bot.tg.sent() == []


def test_unsigned_post_is_401(bot, h, run_async):
    status, _ = _one(bot, run_async, "POST", "/api/report/preview", {})
    assert status == 401


def test_badly_signed_post_is_401(bot, h, run_async):
    status, _ = _one(bot, run_async, "POST", "/api/report/preview",
                     _hdr(h.OWNER, token="999:wrong-token-wrong-token"))
    assert status == 401


def test_badly_signed_post_opens_nothing(bot, h, run_async):
    _one(bot, run_async, "POST", "/api/report/preview",
         _hdr(h.OWNER, token="999:wrong-token-wrong-token"))
    assert bot.tg.sent() == []


@pytest.mark.parametrize("mode", ["scope_no_dm", "scope_two_owners"])
def test_no_owner_post_is_409(make_bot, h, run_async, mode):
    bot = make_bot(mode)
    assert _one(bot, run_async, "POST", "/api/report/preview",
                _hdr(h.ADMIN, scope=h.GROUP)) == (409, {"error": "no_owner"})


def test_no_owner_post_opens_nothing(make_bot, h, run_async):
    bot = make_bot("scope_no_dm")
    _one(bot, run_async, "POST", "/api/report/preview", _hdr(h.OWNER, scope=h.GROUP))
    assert bot.tg.sent() == []


def test_owner_posts_are_rate_limited(bot, h, run_async):
    statuses = [s for s, _ in _calls(bot, run_async,
                                     [("POST", "/api/report/preview", _hdr(h.OWNER))] * 40)]
    assert 429 in statuses


def test_rate_limit_answer_body(bot, h, run_async):
    out = _calls(bot, run_async, [("POST", "/api/report/preview", _hdr(h.OWNER))] * 40)
    assert (429, {"error": "too_many_requests"}) in out


def test_rate_limited_post_opens_no_extra_card(bot, h, run_async):
    out = _calls(bot, run_async, [("POST", "/api/report/preview", _hdr(h.OWNER))] * 40)
    assert len(bot.tg.cards(h.OWNER)) == sum(1 for s, _ in out if s == 200)


def test_first_post_is_not_rate_limited(bot, h, run_async):
    """Boundary: the budget lets a human's taps through."""
    statuses = [s for s, _ in _calls(bot, run_async,
                                     [("POST", "/api/report/preview", _hdr(h.OWNER))] * 3)]
    assert statuses == [200, 200, 200]


def test_muted_dm_post_is_503(bot, h, run_async):
    """Error guessing: the card cannot be posted; the page must not claim it was."""
    MUTE.mute(h.OWNER, 600.0)
    assert _one(bot, run_async, "POST", "/api/report/preview", _hdr(h.OWNER)) == \
        (503, {"error": "not_sent"})


def test_get_on_post_route_opens_nothing(bot, h, run_async):
    _one(bot, run_async, "GET", "/api/report/preview", _hdr(h.OWNER))
    assert bot.tg.sent() == []


# ---- the page --------------------------------------------------------------------------

def _page(bot, run_async) -> str:
    srv = MiniAppServer(bot, bot.registry, port=next(_PORTS))

    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.get("/")
            return await resp.text()
        finally:
            await client.close()
    return run_async(go())


def test_page_has_report_a_problem_row(bot, run_async):
    assert "Report a problem" in _page(bot, run_async)


@pytest.mark.parametrize("sentence", ["The report preview is in your private chat with the bot.",
                                      "Nothing is sent until you tap Send there."])
def test_page_says_where_the_card_went(bot, run_async, sentence):
    """The page's success line (written as concatenated JS strings)."""
    assert sentence in _page(bot, run_async)


def test_page_calls_the_preview_route(bot, run_async):
    assert "/api/report/preview" in _page(bot, run_async)
