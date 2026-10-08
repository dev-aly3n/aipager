"""SC-21: ``can_report`` is true only for the owner in a private scope, and
the page carries the "Report a problem" row (design.md Success criterion
21). Since the 8.112 follow-up the row opens the Mini App's own report
page (POST /api/report/draft and /api/report/send, tested in
tests/test_report_miniapp.py); the old POST /api/report/preview, which
posted the card in the owner's DM, is gone.

Real routes over aiohttp's test server, signed with the Mini App tests'
own initData recipe.

Methods: equivalence partitioning over the caller (owner, admin, user,
stranger) and the chosen scope (private, group); error guessing: no
owner, an ambiguous owner."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

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


def test_page_calls_the_draft_and_send_routes(bot, run_async):
    """The Settings row opens the in-app report page (8.112 follow-up):
    the page builds a draft and sends it, and never asks for the old
    preview card in the private chat."""
    page = _page(bot, run_async)
    assert "/api/report/draft" in page and "/api/report/send" in page
    assert "/api/report/preview" not in page
