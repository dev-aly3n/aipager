"""Unit tests for aipager.bot.rich_message.

HTTP layer is mocked at the httpx.AsyncClient.post level — no real
network calls, no real bot token.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import aipager.bot.rich_message as rm
from aipager.bot.rich_message import (
    RichMessageBlocked,
    RichMessageFallbackRequired,
    close_client,
    detect_rtl,
    send_rich_message,
    send_rich_message_draft,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    """Reset the module-level httpx client between tests."""
    monkeypatch.setattr(rm, "_client", None)
    yield
    # Ensure any client opened during the test is cleaned up.
    if rm._client is not None and not rm._client.is_closed:
        asyncio.new_event_loop().run_until_complete(rm._client.aclose())
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    """Provide a fake bot token so URL construction doesn't use the real one."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")


@pytest.fixture
def run_async():
    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)
    return _run


def _mock_post(response_dict: dict):
    """Return an AsyncMock that simulates httpx.AsyncClient.post."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_dict
    mock_post = AsyncMock(return_value=mock_resp)
    return mock_post


# ── detect_rtl ────────────────────────────────────────────────────────────────

def test_detect_rtl_persian_is_true():
    assert detect_rtl("سلام دنیا") is True


def test_detect_rtl_english_is_false():
    assert detect_rtl("hello world") is False


def test_detect_rtl_empty_is_false():
    assert detect_rtl("") is False


def test_detect_rtl_mixed_persian_dominant():
    # Persian prose with an English identifier quoted
    text = "این یک تابع " + "x" * 5 + " است که " + "abc" * 2 + " برمی‌گرداند " * 10
    assert detect_rtl(text) is True


def test_detect_rtl_mixed_english_dominant():
    # Mostly English with a few Persian chars
    text = "A" * 100 + " سلام " + "B" * 100
    assert detect_rtl(text) is False


def test_detect_rtl_only_digits_is_false():
    assert detect_rtl("1234567890") is False


# ── send_rich_message: success ────────────────────────────────────────────────

def test_send_rich_message_ok_returns_result(run_async, monkeypatch):
    result = {"message_id": 42, "chat_id": 1}
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={"ok": True, "result": result}))
    out = run_async(send_rich_message(12345, "# Hello"))
    assert out == result


def test_send_rich_message_ok_missing_result_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={"ok": True}))
    out = run_async(send_rich_message(12345, "text"))
    assert out is None


def test_send_rich_message_is_rtl_passed_in_payload(run_async, monkeypatch):
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    run_async(send_rich_message(12345, "سلام", is_rtl=True))
    assert captured["rich_message"]["is_rtl"] is True


def test_send_rich_message_reply_to_omitted_when_none(run_async, monkeypatch):
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    run_async(send_rich_message(12345, "hi", reply_to_message_id=None))
    assert "reply_to_message_id" not in captured


def test_send_rich_message_reply_to_included_when_set(run_async, monkeypatch):
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    run_async(send_rich_message(12345, "hi", reply_to_message_id=99))
    assert captured["reply_to_message_id"] == 99


# ── send_rich_message: error taxonomy ────────────────────────────────────────

def test_send_rich_message_400_raises_fallback(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 400, "description": "bad markdown",
    }))
    with pytest.raises(RichMessageFallbackRequired):
        run_async(send_rich_message(1, "bad"))


def test_send_rich_message_403_raises_blocked(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 403, "description": "Forbidden",
    }))
    with pytest.raises(RichMessageBlocked):
        run_async(send_rich_message(1, "text"))


def test_send_rich_message_404_raises_fallback(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 404, "description": "Not Found",
    }))
    with pytest.raises(RichMessageFallbackRequired):
        run_async(send_rich_message(1, "text"))


def test_send_rich_message_404_method_not_found_raises_fallback(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 404, "description": "method not found",
    }))
    with pytest.raises(RichMessageFallbackRequired):
        run_async(send_rich_message(1, "text"))


def test_send_rich_message_500_raises_fallback(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 500, "description": "Internal Server Error",
    }))
    with pytest.raises(RichMessageFallbackRequired):
        run_async(send_rich_message(1, "text"))


def test_send_rich_message_timeout_raises_fallback(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post",
                        AsyncMock(side_effect=httpx.TimeoutException("timed out")))
    with pytest.raises(RichMessageFallbackRequired):
        run_async(send_rich_message(1, "text"))


def test_send_rich_message_connect_error_raises_fallback(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post",
                        AsyncMock(side_effect=httpx.ConnectError("refused")))
    with pytest.raises(RichMessageFallbackRequired):
        run_async(send_rich_message(1, "text"))


# ── send_rich_message: 429 retry logic ───────────────────────────────────────

def test_send_rich_message_429_retries_once_and_succeeds(run_async, monkeypatch):
    """First call returns 429; second call succeeds."""
    result = {"message_id": 7}
    call_count = 0

    async def _fake_post(method, payload):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"ok": False, "error_code": 429,
                    "parameters": {"retry_after": 1},
                    "description": "Too Many Requests"}
        return {"ok": True, "result": result}

    monkeypatch.setattr(rm, "_post", _fake_post)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    out = run_async(send_rich_message(1, "text"))
    assert out == result
    assert call_count == 2


def test_send_rich_message_429_retry_caps_sleep_at_30s(run_async, monkeypatch):
    """retry_after > 30 is capped at 30."""
    slept = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    async def _fake_post(method, payload):
        return {"ok": False, "error_code": 429,
                "parameters": {"retry_after": 999},
                "description": "Too Many Requests"}

    monkeypatch.setattr(rm, "_post", _fake_post)
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    with pytest.raises(RichMessageFallbackRequired):
        run_async(send_rich_message(1, "text"))
    assert slept[0] == 30


def test_send_rich_message_429_twice_raises_fallback(run_async, monkeypatch):
    """Two consecutive 429s raise RichMessageFallbackRequired."""
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 429,
        "parameters": {"retry_after": 1},
        "description": "Too Many Requests",
    }))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with pytest.raises(RichMessageFallbackRequired):
        run_async(send_rich_message(1, "text"))


# ── send_rich_message_draft: success & failure ────────────────────────────────

def test_send_rich_message_draft_ok_returns_true(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={"ok": True}))
    assert run_async(send_rich_message_draft(1, 42, "text")) is True


def test_send_rich_message_draft_error_returns_false(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 400, "description": "bad",
    }))
    assert run_async(send_rich_message_draft(1, 42, "text")) is False


def test_send_rich_message_draft_403_returns_false(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 403, "description": "Forbidden",
    }))
    assert run_async(send_rich_message_draft(1, 42, "text")) is False


def test_send_rich_message_draft_network_error_returns_false(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post",
                        AsyncMock(side_effect=httpx.ConnectError("refused")))
    assert run_async(send_rich_message_draft(1, 42, "text")) is False


def test_send_rich_message_draft_never_raises(run_async, monkeypatch):
    """send_rich_message_draft must NEVER raise, even on unexpected errors."""
    monkeypatch.setattr(rm, "_post",
                        AsyncMock(side_effect=RuntimeError("boom")))
    # MUST NOT raise
    result = run_async(send_rich_message_draft(1, 42, "text"))
    assert result is False


def test_send_rich_message_draft_timeout_returns_false(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post",
                        AsyncMock(side_effect=httpx.TimeoutException("timed out")))
    assert run_async(send_rich_message_draft(1, 42, "text")) is False


# ── close_client ──────────────────────────────────────────────────────────────

def test_close_client_is_idempotent_when_never_opened(run_async):
    """Calling close_client() before any request does not raise."""
    run_async(close_client())  # MUST NOT raise


def test_close_client_closes_open_client(run_async, monkeypatch):
    """After close_client(), the module-level client is reset to None."""
    monkeypatch.setattr(rm, "_client", None)
    # Force a client to be created.
    _client_before = rm._get_client()
    assert rm._client is not None
    run_async(close_client())
    assert rm._client is None


# ── httpx logger level ───────────────────────────────────────────────────────

def test_get_client_silences_httpx_logger(monkeypatch):
    """_get_client() must set the httpx logger to WARNING or higher.

    Regression for rev-iter1-002: httpx logs the full request URL (which
    embeds the bot token) at INFO. Silencing it in _get_client() ensures the
    fix is in effect even when the daemon's logging setup has not run (tests,
    embedded use, other entrypoints).
    """
    import logging
    import aipager.bot.rich_message as rm_mod

    # Reset to a known state — force a fresh client creation.
    monkeypatch.setattr(rm_mod, "_client", None)

    # Lower the httpx logger to DEBUG so we can confirm _get_client raises it.
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.DEBUG)

    rm_mod._get_client()

    assert httpx_logger.level >= logging.WARNING, (
        f"httpx logger level is {httpx_logger.level}, expected >= {logging.WARNING} "
        "(WARNING=30). httpx logs the bot token in its INFO lines."
    )


def test_get_client_no_token_in_info_logs(run_async, monkeypatch):
    """The bot token must never reach log output.

    Drives the REAL ``_post`` through an httpx MockTransport so httpx's own
    logger actually runs.  Mocking ``_post`` instead would make this test
    vacuous: httpx would never execute, no record could contain the token,
    and the assertion would pass even with the suppression deleted.

    Phase 1 is a control proving the harness can see a leak; phase 2 is the
    real assertion.  Uses an obviously fake token — never a real one.
    """
    import logging

    FAKE_TOKEN = "FAKE_BOT_TOKEN_FOR_TESTING_999"
    monkeypatch.setattr("aipager.config.BOT_TOKEN", FAKE_TOKEN)

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    handler.setLevel(logging.DEBUG)

    def _respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level
    httpx_logger.addHandler(handler)
    try:
        client = httpx.AsyncClient(transport=httpx.MockTransport(_respond))
        monkeypatch.setattr(rm, "_client", client)

        # Phase 1 (control): bypass _get_client so nothing suppresses httpx.
        # Proves httpx really does log the token-bearing URL and that the
        # capture harness sees it.
        httpx_logger.setLevel(logging.INFO)
        run_async(client.post(rm._api_url("sendRichMessage"), json={}))
        assert any(FAKE_TOKEN in r.getMessage() for r in records), (
            "Control failed: httpx did not log the token-bearing URL, so this "
            "test cannot prove the suppression works. Either the harness is "
            "broken or httpx stopped logging request URLs."
        )

        # Phase 2: the real path. _post() calls _get_client(), which must
        # raise the httpx logger to WARNING before the request goes out.
        records.clear()
        httpx_logger.setLevel(logging.INFO)
        run_async(send_rich_message(12345, "hello"))
        assert not any(FAKE_TOKEN in r.getMessage() for r in records), (
            "The bot token appeared in log output — the httpx INFO "
            "suppression in _get_client() is not working."
        )
    finally:
        httpx_logger.removeHandler(handler)
        httpx_logger.setLevel(original_level)


# ── request body shape ────────────────────────────────────────────────────────

def test_send_rich_message_body_shape(run_async, monkeypatch):
    """The payload must nest is_rtl inside rich_message, not at the top level."""
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    run_async(send_rich_message(99, "## Title", is_rtl=True, reply_to_message_id=7))
    assert captured["chat_id"] == 99
    assert captured["rich_message"]["markdown"] == "## Title"
    assert captured["rich_message"]["is_rtl"] is True
    assert captured["reply_to_message_id"] == 7
    # is_rtl must NOT be at the top level
    assert "is_rtl" not in captured
