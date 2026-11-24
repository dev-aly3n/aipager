"""design.md success criteria "Mini App": ``GET /api/usage`` gives each "ok"
window entry a ``near`` flag (rounded percent at 90 or more); "reset"
entries carry no ``near``."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import usage_store
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.statusline_file import Window

from ._kit import DAY, LOGIN_A, OWNER, scopes

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(usage_store, "localtime", time.gmtime)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=scopes())
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    b._app.bot.username = "aipager_test_bot"
    return b


def _hdr(user_id, scope):
    user = {"id": user_id, "first_name": "T"}
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields), "X-Aipager-Scope": str(scope)}


def _api(bot, run_async):
    srv = MiniAppServer(bot, bot.registry, port=8765)

    async def scenario():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            r = await client.get("/api/usage", headers=_hdr(OWNER, OWNER))
            return r.status, await r.json()
        finally:
            await client.close()
    return run_async(scenario())


def _entry(bot, run_async, window_name, window):
    usage_store.note_plan("frontend", {window_name: window}, LOGIN_A, scope=OWNER)
    status, body = _api(bot, run_async)
    assert status == 200
    (login,) = body["plan"]["logins"]
    (entry,) = login["windows"]
    return entry


def _later():
    return int(time.time()) + 2 * DAY


@pytest.mark.parametrize("pct, near", [
    (0, False), (50, False), (89.4, False),
    (89.5, True), (89.9, True), (90, True), (99.4, True), (100, True), (250, True),
], ids=lambda v: str(v).replace(".", "p"))
def test_window_entry_near_flag_edges(bot, run_async, pct, near):
    name = "spend_limit" if pct > 100 else "seven_day"
    assert _entry(bot, run_async, name, Window(pct, _later()))["near"] is near


def test_near_flag_is_a_bool(bot, run_async):
    assert type(_entry(bot, run_async, "seven_day", Window(92, _later()))["near"]) is bool


def test_ok_entry_state_is_ok(bot, run_async):
    assert _entry(bot, run_async, "seven_day", Window(92, _later()))["state"] == "ok"


def test_reset_entry_has_no_near(bot, run_async):
    entry = _entry(bot, run_async, "seven_day", Window(95, int(time.time()) - 60))
    assert "near" not in entry


def test_reset_entry_state_is_reset(bot, run_async):
    entry = _entry(bot, run_async, "seven_day", Window(95, int(time.time()) - 60))
    assert entry["state"] == "reset"


def test_each_window_gets_its_own_flag(bot, run_async):
    t = _later()
    usage_store.note_plan("frontend", {"five_hour": Window(40, t), "seven_day": Window(92, t)},
                          LOGIN_A, scope=OWNER)
    _status, body = _api(bot, run_async)
    flags = sorted(w["near"] for w in body["plan"]["logins"][0]["windows"])
    assert flags == [False, True]

