"""Iteration 2: the note capture closes on every successful authenticated
non-GET Mini App request, and only on those (design.md Success criterion
8 "a Mini App action"; entrypoints.md "Other Mini App action routes
(existing) now also close the owner's open note capture when they
succeed"; coordinator's iteration-2 rule).

Methods: equivalence partitioning over the request: a successful write
(POST, PUT), a read (GET of preferences, of sessions), a refused write
(a bad value: 400; an unknown session: 404), an unauthenticated write
(401). Error guessing: a page refresh (reads) must not swallow the note
the owner is about to type; a failed write did nothing, so it is not
"something else"."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
NOTE_CLOSED = "Note not added: you did something else. Tap Add a note to try again."
_PORTS = iter(range(8900, 9000))


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _hdr(user_id, *, token=BOT_TOKEN):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


# (method, path, json body or None, auth: "owner" | "none", expected status class)
REQUESTS = {
    "post_clearqueue": ("POST", "/api/sessions/dev/clearqueue", None, "owner", 2),
    "put_preference": ("PUT", "/api/preferences/answer_length", {"value": "short"}, "owner", 2),
    "get_preferences": ("GET", "/api/preferences", None, "owner", 2),
    "get_sessions": ("GET", "/api/sessions", None, "owner", 2),
    "put_bad_value": ("PUT", "/api/preferences/answer_length", {"value": "bogus-length"},
                      "owner", 4),
    "post_unknown_session": ("POST", "/api/sessions/nosuch/clearqueue", None, "owner", 4),
    "post_unauthenticated": ("POST", "/api/sessions/dev/clearqueue", None, "none", 4),
}
CLOSING = ["post_clearqueue", "put_preference"]
NOT_CLOSING = ["get_preferences", "get_sessions", "put_bad_value", "post_unknown_session",
               "post_unauthenticated"]


async def _request(bot, h, name):
    method, path, body, auth, _ = REQUESTS[name]
    sess = h.add_session(bot.registry, "dev")
    sess.queue_prompt("later", 1)
    srv = MiniAppServer(bot, bot.registry, port=next(_PORTS))
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    try:
        headers = _hdr(h.OWNER) if auth == "owner" else {}
        kw = {"json": body} if body is not None else {}
        resp = await client.request(method, path, headers=headers, **kw)
        status = resp.status
    finally:
        await client.close()
    await h.settle(10)
    return status


def _armed_then(bot, drive, h, run_async, name, then_text=None):
    d = drive(bot)
    out = {}

    async def go():
        h.record_exc()
        cid, mid = await h.open_card(bot, d)
        await d.tap("_:rp:note", chat=cid, message_id=mid)
        out["status"] = await _request(bot, h, name)
        if then_text is not None:
            await d.text(then_text)
        out["card"] = (cid, mid)
    run_async(go())
    return out


def _note_of(bot, h, cid, mid):
    block = h.preview_block(bot.tg.text_of(cid, mid))
    return json.loads(block).get("note") if block is not None else "<no block>"


@pytest.mark.parametrize("name", sorted(REQUESTS))
def test_request_has_its_expected_status(make_bot, drive, h, run_async, name):
    """The partition is real: each request ends in the status class it
    stands for."""
    out = _armed_then(make_bot("personal"), drive, h, run_async, name)
    assert out["status"] // 100 == REQUESTS[name][4]


@pytest.mark.parametrize("name", CLOSING)
def test_successful_write_closes_the_capture_with_a_line(make_bot, drive, h, run_async, name):
    bot = make_bot("personal")
    out = _armed_then(bot, drive, h, run_async, name)
    assert NOTE_CLOSED in bot.tg.text_of(*out["card"])


@pytest.mark.parametrize("name", CLOSING)
def test_after_successful_write_next_text_is_not_a_note(make_bot, drive, h, run_async, name):
    bot = make_bot("personal")
    out = _armed_then(bot, drive, h, run_async, name, then_text="not a note")
    assert _note_of(bot, h, *out["card"]) is None


@pytest.mark.parametrize("name", NOT_CLOSING)
def test_read_or_failed_write_keeps_the_capture(make_bot, drive, h, run_async, name):
    bot = make_bot("personal")
    out = _armed_then(bot, drive, h, run_async, name, then_text="my note")
    assert _note_of(bot, h, *out["card"]) == "my note"


@pytest.mark.parametrize("name", NOT_CLOSING)
def test_read_or_failed_write_says_nothing_about_closing(make_bot, drive, h, run_async, name):
    bot = make_bot("personal")
    out = _armed_then(bot, drive, h, run_async, name)
    assert NOTE_CLOSED not in bot.tg.text_of(*out["card"])
