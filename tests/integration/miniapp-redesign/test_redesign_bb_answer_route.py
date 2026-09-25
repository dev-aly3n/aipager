"""POST /api/sessions/{label}/answer: status matrix and side effects.

Black-box against entrypoints.md ("HTTP routes / New" and "Side effects")
and design.md success criterion 7: the route re-sends the pending prompt
with its answer keyboard into the scope chat, registers the copy, returns
401 / 403 / 404 / 409 / 429 / 503 / 502 as specified, and never types
into the PTY.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager.state import Status

from miniapp_redesign_bb import (  # noqa: E402 - alias set by conftest
    ADMIN_ID,
    BOT_TOKEN,
    DEVELOPER_ID,
    FOREIGN_MEMBER_ID,
    FOREIGN_SCOPE_CHAT_ID,
    OUTSIDER_ID,
    READONLY_ID,
    SCOPE_CHAT_ID,
    client_for,
    hdr,
    init_data,
    mk_session,
    put_on_permission,
)


def _post(server, run_async, label, headers, *, repeat=1):
    """POST the answer route ``repeat`` times; return (status, json) of
    every response."""
    async def _run():
        client = await client_for(server)
        out = []
        try:
            for _ in range(repeat):
                resp = await client.post(f"/api/sessions/{label}/answer",
                                         headers=headers)
                try:
                    body = await resp.json()
                except Exception:  # noqa: BLE001 - non-JSON is itself a finding
                    body = await resp.text()
                out.append((resp.status, body))
        finally:
            await client.close()
        return out
    return run_async(_run())


def _buttons(markup):
    if markup is None:
        return []
    return [b for row in markup.inline_keyboard for b in row]


# ── 401: authentication ─────────────────────────────────────────────────────

def test_answer_without_initdata_is_401(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    [(status, _body)] = _post(server, run_async, "alpha", {})
    assert status == 401


def test_answer_without_initdata_body_is_unauthorized(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    [(_status, body)] = _post(server, run_async, "alpha", {})
    assert body == {"error": "unauthorized"}


def test_answer_with_wrongly_signed_initdata_is_401(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    bad = {"X-Telegram-Init-Data": init_data(ADMIN_ID, bot_token="999:WRONG")}
    [(status, _body)] = _post(server, run_async, "alpha", bad)
    assert status == 401


def test_answer_with_expired_initdata_is_401(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    old = {"X-Telegram-Init-Data": init_data(ADMIN_ID, auth_date=1)}
    [(status, _body)] = _post(server, run_async, "alpha", old)
    assert status == 401


def test_answer_unauthorized_sends_nothing(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", {})
    assert tg.sends == []


# ── 403: membership and prompt permission ───────────────────────────────────

def test_answer_by_non_member_is_403_forbidden(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    [(status, body)] = _post(server, run_async, "alpha", hdr(OUTSIDER_ID))
    assert (status, body) == (403, {"error": "forbidden"})


def test_answer_by_member_who_cannot_prompt_is_403_forbidden(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    [(status, body)] = _post(server, run_async, "alpha", hdr(READONLY_ID))
    assert (status, body) == (403, {"error": "forbidden"})


def test_answer_by_member_who_cannot_prompt_sends_nothing(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(READONLY_ID))
    assert tg.sends == []


# ── 404: unknown / foreign labels, identical body ───────────────────────────

def test_answer_unknown_label_is_404_not_found(server, run_async):
    [(status, body)] = _post(server, run_async, "nobody", hdr(ADMIN_ID))
    assert (status, body) == (404, {"error": "not_found"})


def test_answer_other_chats_label_is_404_not_found(server, run_async):
    put_on_permission(mk_session(server, "theirs",
                                 scope_chat_id=FOREIGN_SCOPE_CHAT_ID))
    [(status, body)] = _post(server, run_async, "theirs", hdr(ADMIN_ID))
    assert (status, body) == (404, {"error": "not_found"})


def test_answer_other_chats_label_is_byte_identical_to_unknown(server, run_async):
    put_on_permission(mk_session(server, "theirs",
                                 scope_chat_id=FOREIGN_SCOPE_CHAT_ID))
    [foreign] = _post(server, run_async, "theirs", hdr(ADMIN_ID))
    [unknown] = _post(server, run_async, "nobody", hdr(ADMIN_ID))
    assert foreign == unknown


def test_answer_other_chats_waiting_session_sends_nothing(server, tg, run_async):
    put_on_permission(mk_session(server, "theirs",
                                 scope_chat_id=FOREIGN_SCOPE_CHAT_ID))
    _post(server, run_async, "theirs", hdr(ADMIN_ID))
    assert tg.sends == []


def test_foreign_scope_member_cannot_answer_our_session(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    [(status, _b)] = _post(server, run_async, "alpha", hdr(FOREIGN_MEMBER_ID))
    assert status == 404 and tg.sends == []


# ── 409: not waiting (one test per non-waiting status class) ───────────────

@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY, Status.GONE])
def test_answer_non_waiting_session_is_409_not_waiting(server, run_async, status):
    mk_session(server, "alpha", status=status)
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, body.get("error")) == (409, "not_waiting")


def test_answer_not_waiting_carries_a_string_detail(server, run_async):
    mk_session(server, "alpha", status=Status.IDLE)
    [(_code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert isinstance(body.get("detail"), str) and body["detail"]


def test_answer_not_waiting_sends_nothing(server, tg, run_async):
    mk_session(server, "alpha", status=Status.BUSY)
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert tg.sends == []


def test_busy_session_with_a_leftover_prompt_is_still_not_waiting(
    server, tg, run_async,
):
    """Error guess: the prompt was answered in the terminal, the status
    moved on, but the stored prompt was not cleared yet."""
    sess = put_on_permission(mk_session(server, "alpha"))
    sess.status = Status.BUSY
    [(code, _body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, tg.sends) == (409, [])


# ── 409: waiting but nothing stored to re-send ─────────────────────────────

def test_waiting_without_a_stored_prompt_is_409_not_resendable(server, run_async):
    mk_session(server, "alpha", status=Status.INTERACTIVE)
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, body.get("error")) == (409, "not_resendable")


def test_not_resendable_carries_a_string_detail(server, run_async):
    mk_session(server, "alpha", status=Status.INTERACTIVE)
    [(_code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert isinstance(body.get("detail"), str) and body["detail"]


def test_not_resendable_sends_nothing(server, tg, run_async):
    mk_session(server, "alpha", status=Status.INTERACTIVE)
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert tg.sends == []


# ── 503 / 502: Telegram-side failures ──────────────────────────────────────

def test_throttled_send_is_503_chat_busy(server, tg, run_async):
    from aipager.bot.flood_budget import FloodSkipped
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = FloodSkipped(SCOPE_CHAT_ID, "sendMessage")
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, body.get("error")) == (503, "chat_busy")


def test_chat_busy_carries_a_string_detail(server, tg, run_async):
    from aipager.bot.flood_budget import FloodSkipped
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = FloodSkipped(SCOPE_CHAT_ID, "sendMessage")
    [(_code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert isinstance(body.get("detail"), str) and body["detail"]


def _telegram_error(kind):
    from telegram.error import BadRequest, NetworkError, TimedOut
    return {"network": NetworkError("connection reset"),
            "timeout": TimedOut(),
            "bad_request": BadRequest("Message is too long")}[kind]


@pytest.mark.parametrize("kind", ["network", "timeout", "bad_request"])
def test_telegram_refusing_the_send_is_502_send_failed(server, tg, run_async, kind):
    """Error guess: Telegram answers the send with an error. The route must
    report send_failed, not crash with a bare 500."""
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _telegram_error(kind)
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, body.get("error") if isinstance(body, dict) else body) == (
        502, "send_failed")


def test_a_dropped_send_is_502_send_failed(server, tg, run_async):
    """The transport gave up on the send without raising (no message
    came back)."""
    put_on_permission(mk_session(server, "alpha"))
    tg.return_none = True
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, body.get("error") if isinstance(body, dict) else body) == (
        502, "send_failed")


# ── error guessing: every other way the send can fail ──────────────────────
# entrypoints.md lists exactly two outcomes for a send that does not go
# through: 503 chat_busy ("Telegram send throttled") and 502 send_failed.
# So any other refusal or crash of the send is 502 send_failed with a string
# detail, and the body is always JSON (never aiohttp's bare 500 page).

def _other_send_error(kind):
    import asyncio
    import errno

    from telegram.error import ChatMigrated, Forbidden, TelegramError
    return {
        "forbidden": Forbidden("Forbidden: bot was kicked from the supergroup chat"),
        "chat_migrated": ChatMigrated(-1001234),
        "telegram_error": TelegramError("Unknown error"),
        "os_error": OSError(errno.ECONNRESET, "Connection reset by peer"),
        "asyncio_timeout": asyncio.TimeoutError(),
        "runtime_error": RuntimeError("event loop is closed"),
    }[kind]


OTHER_SEND_ERRORS = ["forbidden", "chat_migrated", "telegram_error", "os_error",
                     "asyncio_timeout", "runtime_error"]
ALL_SEND_ERRORS = ["network", "timeout", "bad_request"] + OTHER_SEND_ERRORS


def _failing_send(kind):
    if kind in ("network", "timeout", "bad_request"):
        return _telegram_error(kind)
    return _other_send_error(kind)


@pytest.mark.parametrize("kind", ALL_SEND_ERRORS)
def test_a_failed_send_answers_json(server, tg, run_async, kind):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _failing_send(kind)
    [(_code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert isinstance(body, dict), body


@pytest.mark.parametrize("kind", OTHER_SEND_ERRORS)
def test_other_send_errors_are_502_send_failed(server, tg, run_async, kind):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _other_send_error(kind)
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, body.get("error") if isinstance(body, dict) else body) == (
        502, "send_failed")


@pytest.mark.parametrize("kind", ALL_SEND_ERRORS)
def test_send_failed_carries_a_string_detail(server, tg, run_async, kind):
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _failing_send(kind)
    [(_code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    detail = body.get("detail") if isinstance(body, dict) else None
    assert isinstance(detail, str) and detail.strip(), body


def test_send_failed_detail_never_echoes_the_bot_token(server, tg, run_async):
    """Error guess: an HTTP-layer error message can carry the request URL,
    which holds the bot token. The Mini App must not show it."""
    from telegram.error import NetworkError
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = NetworkError(
        f"POST https://api.telegram.org/bot{BOT_TOKEN}/sendMessage failed")
    [(_code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert BOT_TOKEN.split(":")[1] not in str(body)


def test_telegram_retry_after_is_json(server, tg, run_async):
    from datetime import timedelta

    from telegram.error import RetryAfter
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = RetryAfter(timedelta(seconds=1))
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert isinstance(body, dict) and code in (502, 503), (code, body)


def test_telegram_retry_after_is_503_chat_busy(server, tg, run_async):
    """RetryAfter is Telegram's own throttle: the documented outcome for a
    throttled send is 503 chat_busy."""
    from datetime import timedelta

    from telegram.error import RetryAfter
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = RetryAfter(timedelta(seconds=1))
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, body.get("error") if isinstance(body, dict) else body) == (
        503, "chat_busy")


@pytest.mark.parametrize("kind", ["network", "forbidden", "runtime_error"])
def test_a_failed_send_leaves_the_session_waiting(server, tg, run_async, kind):
    sess = put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _failing_send(kind)
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("kind", ["network", "forbidden", "runtime_error"])
def test_a_retry_after_a_failed_send_goes_through(server, tg, run_async, kind):
    """Error guess: a failure must not leave the prompt marked as re-sent
    (or the route stuck), so the next tap works."""
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = _failing_send(kind)
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    tg.raise_on_send = None
    [(code, _body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, len(tg.sends)) == (200, 1)


def test_a_failed_send_records_no_message_for_the_session(server, tg, run_async):
    from telegram.error import NetworkError
    put_on_permission(mk_session(server, "alpha"))
    tg.raise_on_send = NetworkError("connection reset")
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    tg.raise_on_send = None
    # the next id the recorder would hand out was never registered
    assert server.registry.get_session_by_msg(tg._next_id + 1, SCOPE_CHAT_ID) is None


# ── 429: write rate limit, boundary at 30 per 60 s ─────────────────────────

def test_thirtieth_write_in_a_minute_is_not_rate_limited(server, run_async):
    mk_session(server, "alpha", status=Status.IDLE)
    results = _post(server, run_async, "alpha", hdr(ADMIN_ID), repeat=30)
    assert results[-1][0] != 429


def test_thirty_first_write_in_a_minute_is_429(server, run_async):
    mk_session(server, "alpha", status=Status.IDLE)
    results = _post(server, run_async, "alpha", hdr(ADMIN_ID), repeat=31)
    assert results[-1] == (429, {"error": "too_many_requests"})


def test_rate_limited_answer_sends_nothing(server, tg, run_async):
    mk_session(server, "idle1", status=Status.IDLE)
    _post(server, run_async, "idle1", hdr(ADMIN_ID), repeat=30)
    put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert tg.sends == []


# ── 200: success and its side effects ──────────────────────────────────────

def test_answer_waiting_session_returns_200_sent(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    [(code, body)] = _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert (code, body) == (200, {"status": "sent", "label": "alpha"})


def test_member_who_can_prompt_but_is_not_admin_may_answer(server, run_async):
    put_on_permission(mk_session(server, "alpha"))
    [(code, _body)] = _post(server, run_async, "alpha", hdr(DEVELOPER_ID))
    assert code == 200


def test_success_sends_exactly_one_message(server, tg, run_async):
    """Also covers "no extra 'changed from the Mini App' mirror line"."""
    put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert len(tg.sends) == 1


def test_success_message_goes_to_the_scope_chat(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert tg.sends[0][0] == SCOPE_CHAT_ID


def test_success_message_carries_the_prompt_text(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"), "Bash: make deploy-xyz")
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert "make deploy-xyz" in tg.sends[0][1]


def test_success_message_carries_answer_buttons(server, tg, run_async):
    put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    labels = [b.text for b in _buttons(tg.sends[0][2])]
    assert any("Allow" in t for t in labels) and any("Deny" in t for t in labels)


def test_success_resends_a_separate_message_prompt_verbatim(server, tg, run_async):
    sess = mk_session(server, "alpha", status=Status.INTERACTIVE)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Yes", callback_data="x")]])
    sess.pending_prompt_msg = {"text": "question for alpha", "keyboard": kb,
                               "summary": "Pick one"}
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert [(s[1], s[2]) for s in tg.sends] == [("question for alpha", kb)]


def test_success_records_the_copy_as_the_sessions_message(server, tg, run_async):
    sess = put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert server.registry.get_session_by_msg(tg.ids[0], SCOPE_CHAT_ID) is sess


def test_success_never_types_into_the_pty(server, run_async, _no_pty_keystrokes):
    put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert _no_pty_keystrokes == []


def test_success_leaves_the_session_waiting(server, run_async):
    """The route only re-sends; answering happens in chat."""
    sess = put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    assert sess.status == Status.INTERACTIVE


def test_answer_route_takes_no_body_and_ignores_one(server, tg, run_async):
    """Error guess: a client sending a JSON body must not change anything."""
    put_on_permission(mk_session(server, "alpha"))

    async def _run():
        client = await client_for(server)
        try:
            resp = await client.post("/api/sessions/alpha/answer",
                                     headers=hdr(ADMIN_ID),
                                     json={"choice": "allow"})
            return resp.status
        finally:
            await client.close()
    assert run_async(_run()) == 200 and len(tg.sends) == 1


# ── stale copies: "already answered" ───────────────────────────────────────

def _tap(bot, data, *, message_id, user_id=ADMIN_ID, chat_id=SCOPE_CHAT_ID):
    toasts: list = []

    async def _answer(text=None, **_kw):
        toasts.append(text)

    async def _edit(*_a, **_k):
        return None

    query = MagicMock()
    query.data = data
    query.answer = _answer
    query.edit_message_text = _edit
    query.edit_message_reply_markup = _edit
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "supergroup"
    return update, toasts


def _allow_button(markup):
    hits = [b for b in _buttons(markup) if "Allow" in b.text]
    assert hits, [b.text for b in _buttons(markup)]
    return hits[0]


def test_tap_on_copy_after_prompt_answered_says_already_answered(
    server, tg, run_async,
):
    sess = put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    allow = _allow_button(tg.sends[0][2])
    sess.status = Status.BUSY
    sess.pending_permission = None
    update, toasts = _tap(server.bot, allow.callback_data, message_id=tg.ids[0])
    run_async(server.bot._handle_callback(update, MagicMock()))
    assert "already answered" in toasts


def test_tap_on_copy_after_prompt_answered_types_nothing(
    server, tg, run_async, _no_pty_keystrokes,
):
    sess = put_on_permission(mk_session(server, "alpha"))
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    allow = _allow_button(tg.sends[0][2])
    sess.status = Status.BUSY
    sess.pending_permission = None
    update, _toasts = _tap(server.bot, allow.callback_data, message_id=tg.ids[0])
    run_async(server.bot._handle_callback(update, MagicMock()))
    assert _no_pty_keystrokes == []


def test_tap_on_copy_after_a_newer_prompt_replaced_it_says_already_answered(
    server, tg, run_async,
):
    sess = put_on_permission(mk_session(server, "alpha"), "Bash: first")
    _post(server, run_async, "alpha", hdr(ADMIN_ID))
    allow = _allow_button(tg.sends[0][2])
    # answered in the terminal, then a NEW prompt arrives
    sess.status = Status.BUSY
    sess.pending_permission = None
    put_on_permission(sess, "Bash: second")
    update, toasts = _tap(server.bot, allow.callback_data, message_id=tg.ids[0])
    run_async(server.bot._handle_callback(update, MagicMock()))
    assert "already answered" in toasts
