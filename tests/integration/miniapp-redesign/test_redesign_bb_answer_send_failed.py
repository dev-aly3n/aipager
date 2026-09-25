"""POST /api/sessions/{label}/answer: error guessing around a failed send.

Black-box against entrypoints.md ("HTTP routes / New": 502
``{"error":"send_failed","detail":str}``, 503 chat_busy for a throttled
send; "Side effects": only a *successful* answer sends and records a
message) and design.md success criterion 7.

Guesses covered here:
- the detail is a fixed sentence and never carries the exception text
  (which can hold chat ids, URLs or the bot token);
- a cancelled request is not swallowed and reported as send_failed;
- a 502 does not count as sent: no message, no registration, no change
  to the session, and an earlier good copy stays registered.
"""

from __future__ import annotations

import asyncio
import errno

import pytest

from miniapp_redesign_bb import (  # noqa: E402 - alias set by conftest
    ADMIN_ID,
    SCOPE_CHAT_ID,
    client_for,
    hdr,
    mk_session,
    put_on_permission,
)

SENTINEL = "zq-sentinel-7731"


def _post_once(server, run_async, label="alpha", user=ADMIN_ID):
    """One POST; returns (status, content_type, parsed-or-text body), or
    ("disconnected", exc type name, None) when the server dropped it."""
    async def _run():
        client = await client_for(server)
        try:
            try:
                resp = await asyncio.wait_for(
                    client.post(f"/api/sessions/{label}/answer",
                                headers=hdr(user)), 10)
            except Exception as exc:  # noqa: BLE001 - the drop is the observation
                return ("disconnected", type(exc).__name__, None)
            try:
                body = await resp.json()
            except Exception:  # noqa: BLE001 - non-JSON is itself a finding
                body = await resp.text()
            return (resp.status, resp.headers.get("Content-Type", ""), body)
        finally:
            await client.close()
    return run_async(_run())


def _error(kind, text=SENTINEL):
    from telegram.error import (
        BadRequest,
        Forbidden,
        NetworkError,
        TelegramError,
        TimedOut,
    )
    return {
        "network": NetworkError(text),
        "timed_out": TimedOut(text),
        "bad_request": BadRequest(text),
        "forbidden": Forbidden(text),
        "telegram_error": TelegramError(text),
        "os_error": OSError(errno.ECONNRESET, text),
        "runtime_error": RuntimeError(text),
        "value_error": ValueError(text),
        "key_error": KeyError(text),
    }[kind]


KINDS = ["network", "timed_out", "bad_request", "forbidden", "telegram_error",
         "os_error", "runtime_error", "value_error", "key_error"]


def _detail(body):
    return body.get("detail") if isinstance(body, dict) else None


# ── the detail never leaks the exception message ────────────────────────────

@pytest.mark.parametrize("kind", KINDS)
def test_send_failed_detail_never_carries_the_exception_text(
        server, tg, run_async, kind):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _error(kind)
    _code, _ctype, body = _post_once(server, run_async)
    assert SENTINEL not in str(body)


@pytest.mark.parametrize("kind", KINDS)
def test_send_failed_detail_is_the_same_sentence_for_any_failure(
        server, tg, run_async, kind):
    """A fixed sentence: the detail does not vary with what went wrong."""
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _error("network", "first failure text")
    _c1, _t1, reference = _post_once(server, run_async)
    tg.raise_on_send = _error(kind, "a completely different failure")
    _c2, _t2, body = _post_once(server, run_async)
    assert _detail(body) == _detail(reference) and _detail(body)


def test_send_failed_detail_does_not_depend_on_the_exception_text(
        server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = RuntimeError("alpha text")
    _c1, _t1, first = _post_once(server, run_async)
    tg.raise_on_send = RuntimeError("omega text")
    _c2, _t2, second = _post_once(server, run_async)
    assert _detail(first) == _detail(second)


def test_send_failed_detail_never_names_the_scope_chat_id(server, tg, run_async):
    """Error guess: ChatMigrated / BadRequest texts carry chat ids."""
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _error("bad_request", f"chat {SCOPE_CHAT_ID} not found")
    _code, _ctype, body = _post_once(server, run_async)
    assert str(SCOPE_CHAT_ID) not in str(_detail(body))


def test_send_failed_detail_never_names_the_exception_class(
        server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = RuntimeError("x")
    _code, _ctype, body = _post_once(server, run_async)
    assert "RuntimeError" not in str(body)


def test_send_failed_body_is_exactly_error_and_detail(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _error("runtime_error")
    _code, _ctype, body = _post_once(server, run_async)
    assert isinstance(body, dict) and set(body) == {"error", "detail"}


def test_send_failed_is_served_as_json(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _error("os_error")
    _code, ctype, _body = _post_once(server, run_async)
    assert ctype.startswith("application/json")


def test_asyncio_timeout_is_502_send_failed(server, tg, run_async):
    """asyncio.TimeoutError must not reach aiohttp (which would answer a
    bare 504)."""
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = asyncio.TimeoutError()
    code, _ctype, body = _post_once(server, run_async)
    assert (code, body.get("error") if isinstance(body, dict) else body) == (
        502, "send_failed")


def test_a_dropped_send_detail_matches_a_raised_one(server, tg, run_async):
    """A send the transport dropped (None back) is the same failure to the
    caller as one that raised."""
    put_on_permission(mk_session(server, "alpha"))
    tg.return_none = True
    _c1, _t1, dropped = _post_once(server, run_async)
    tg.return_none = False
    tg.raise_on_send = _error("network")
    _c2, _t2, raised = _post_once(server, run_async)
    assert _detail(dropped) == _detail(raised)


# ── throttle vs failure are distinct ───────────────────────────────────────

def test_chat_busy_detail_differs_from_send_failed_detail(server, tg, run_async):
    """503 and 502 tell the user different things (wait vs. it failed)."""
    from aipager.bot.flood_budget import FloodSkipped
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = FloodSkipped(SCOPE_CHAT_ID, "sendMessage")
    _c1, _t1, busy = _post_once(server, run_async)
    tg.raise_on_send = _error("network")
    _c2, _t2, failed = _post_once(server, run_async)
    assert _detail(busy) != _detail(failed)


def test_chat_busy_detail_never_names_the_scope_chat_id(server, tg, run_async):
    from aipager.bot.flood_budget import FloodSkipped
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = FloodSkipped(SCOPE_CHAT_ID, "sendMessage")
    _code, _ctype, body = _post_once(server, run_async)
    assert str(SCOPE_CHAT_ID) not in str(_detail(body))


def test_throttled_send_sends_nothing(server, tg, run_async):
    from aipager.bot.flood_budget import FloodSkipped
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = FloodSkipped(SCOPE_CHAT_ID, "sendMessage")
    _post_once(server, run_async)
    assert tg.sends == []


# ── CancelledError is not swallowed ─────────────────────────────────────────

def test_cancelled_send_is_not_reported_as_send_failed(server, tg, run_async):
    """Error guess: a broad ``except BaseException`` would turn a cancelled
    request into 502 send_failed. Cancellation must propagate, so the
    caller sees anything but a send_failed answer."""
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = asyncio.CancelledError()
    code, _ctype, body = _post_once(server, run_async)
    assert not (code == 502 and isinstance(body, dict)
                and body.get("error") == "send_failed"), (code, body)


def test_cancelled_send_is_not_reported_as_sent(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = asyncio.CancelledError()
    code, _ctype, _body = _post_once(server, run_async)
    assert code != 200


def test_route_still_serves_after_a_cancelled_send(server, tg, run_async):
    """The cancellation stays with its own request."""
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = asyncio.CancelledError()
    _post_once(server, run_async)
    tg.raise_on_send = None
    code, _ctype, _body = _post_once(server, run_async)
    assert (code, len(tg.sends)) == (200, 1)


# ── a 502 does not count as sent ────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["network", "runtime_error", "dropped"])
def test_send_failed_body_never_says_sent(server, tg, run_async, kind):
    put_on_permission(mk_session(server, "alpha"))
    if kind == "dropped":
        tg.return_none = True
    else:
        tg.raise_on_send = _error(kind)
    _code, _ctype, body = _post_once(server, run_async)
    assert not (isinstance(body, dict) and body.get("status") == "sent")


@pytest.mark.parametrize("kind", ["network", "forbidden", "os_error",
                                  "runtime_error", "dropped"])
def test_send_failed_leaves_the_session_untouched(server, tg, run_async, kind):
    """No field of the session changes: nothing recorded as its message,
    no 'resent' mark, no status change."""
    sess = put_on_permission(mk_session(server, "alpha"))
    before = dict(vars(sess))
    if kind == "dropped":
        tg.return_none = True
    else:
        tg.raise_on_send = _error(kind)
    _post_once(server, run_async)
    after = dict(vars(sess))
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == set()


@pytest.mark.parametrize("kind", ["network", "runtime_error", "dropped"])
def test_send_failed_keeps_the_earlier_copy_registered(server, tg, run_async, kind):
    """A good copy was posted; a later failed re-send must not unregister
    it or replace it."""
    sess = put_on_permission(mk_session(server, "alpha"))
    _post_once(server, run_async)
    first_copy = tg.ids[0]
    if kind == "dropped":
        tg.return_none = True
    else:
        tg.raise_on_send = _error(kind)
    _post_once(server, run_async)
    assert server.registry.get_session_by_msg(first_copy, SCOPE_CHAT_ID) is sess


@pytest.mark.parametrize("kind", ["network", "runtime_error", "dropped"])
def test_send_failed_after_a_good_copy_leaves_the_session_untouched(
        server, tg, run_async, kind):
    sess = put_on_permission(mk_session(server, "alpha"))
    _post_once(server, run_async)
    before = dict(vars(sess))
    if kind == "dropped":
        tg.return_none = True
    else:
        tg.raise_on_send = _error(kind)
    _post_once(server, run_async)
    after = dict(vars(sess))
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == set()


def test_throttled_send_leaves_the_session_untouched(server, tg, run_async):
    from aipager.bot.flood_budget import FloodSkipped
    sess = put_on_permission(mk_session(server, "alpha"))
    before = dict(vars(sess))
    tg.raise_on_send = FloodSkipped(SCOPE_CHAT_ID, "sendMessage")
    _post_once(server, run_async)
    after = dict(vars(sess))
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == set()

