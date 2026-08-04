"""Unit tests for edit_message_text_rich and _handle_edit_response.

HTTP layer is mocked at _post — no real network calls, no real bot token.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

import aipager.bot.rich_message as rm
from aipager.bot.rich_message import (
    RichMessageBlocked,
    RichMessageGone,
    edit_message_text_rich,
)


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setattr(rm, "_client", None)
    yield
    if rm._client is not None and not rm._client.is_closed:
        asyncio.new_event_loop().run_until_complete(rm._client.aclose())
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")


@pytest.fixture
def run_async():
    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)
    return _run


# ── Payload shape ─────────────────────────────────────────────────────────────

def test_payload_has_chat_id_and_message_id(run_async, monkeypatch):
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    run_async(edit_message_text_rich(111, 999, "hello"))
    assert captured["chat_id"] == 111
    assert captured["message_id"] == 999


def test_payload_rich_message_nested(run_async, monkeypatch):
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    run_async(edit_message_text_rich(1, 2, "## Title", is_rtl=True))
    assert captured["rich_message"]["markdown"] == "## Title"
    assert captured["rich_message"]["is_rtl"] is True
    assert "is_rtl" not in captured  # NOT top-level
    assert "text" not in captured    # probe-confirmed: no text field


def test_payload_no_text_field(run_async, monkeypatch):
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    run_async(edit_message_text_rich(1, 2, "content"))
    assert "text" not in captured


def test_payload_reply_markup_present_when_given(run_async, monkeypatch):
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    markup = {"inline_keyboard": [[{"text": "Stop", "callback_data": "s:stop"}]]}
    run_async(edit_message_text_rich(1, 2, "hi", reply_markup=markup))
    assert captured["reply_markup"] == markup


def test_payload_reply_markup_absent_when_none(run_async, monkeypatch):
    captured = {}

    async def _fake_post(method, payload):
        captured.update(payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    run_async(edit_message_text_rich(1, 2, "hi", reply_markup=None))
    assert "reply_markup" not in captured


# ── Success paths ─────────────────────────────────────────────────────────────

def test_ok_true_with_result_dict_returns_dict(run_async, monkeypatch):
    result = {"message_id": 42, "chat_id": 1}
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={"ok": True, "result": result}))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out == result


def test_ok_true_missing_result_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={"ok": True}))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


def test_ok_true_result_not_dict_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={"ok": True, "result": True}))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


# ── 400 error taxonomy ────────────────────────────────────────────────────────

def test_400_message_not_modified_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 400,
        "description": "Bad Request: message is not modified: specified new message content",
    }))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None  # benign — no raise


def test_400_message_not_modified_does_not_raise(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 400,
        "description": "message is not modified",
    }))
    # MUST NOT raise anything
    run_async(edit_message_text_rich(1, 2, "hi"))


def test_400_message_to_edit_not_found_raises_gone(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 400,
        "description": "Bad Request: message to edit not found",
    }))
    with pytest.raises(RichMessageGone):
        run_async(edit_message_text_rich(1, 2, "hi"))


def test_400_message_id_invalid_raises_gone(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 400,
        "description": "Bad Request: MESSAGE_ID_INVALID",
    }))
    with pytest.raises(RichMessageGone):
        run_async(edit_message_text_rich(1, 2, "hi"))


def test_400_other_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 400,
        "description": "Bad Request: bad markdown",
    }))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


# ── 403 ───────────────────────────────────────────────────────────────────────

def test_403_raises_blocked(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 403, "description": "Forbidden: bot was blocked",
    }))
    with pytest.raises(RichMessageBlocked):
        run_async(edit_message_text_rich(1, 2, "hi"))


# ── 404 ───────────────────────────────────────────────────────────────────────

def test_404_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 404, "description": "Not Found",
    }))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


def test_404_method_not_found_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 404, "description": "method not found",
    }))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


# ── 5xx ───────────────────────────────────────────────────────────────────────

def test_500_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 500, "description": "Internal Server Error",
    }))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


# ── Network / unexpected errors ───────────────────────────────────────────────

def test_timeout_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post",
                        AsyncMock(side_effect=httpx.TimeoutException("timed out")))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


def test_connect_error_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post",
                        AsyncMock(side_effect=httpx.ConnectError("refused")))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


def test_unexpected_exception_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post",
                        AsyncMock(side_effect=RuntimeError("boom")))
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None


# ── 429 retry logic ───────────────────────────────────────────────────────────

def test_429_retries_once_and_succeeds(run_async, monkeypatch):
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
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out == result
    assert call_count == 2


def test_429_sleep_capped_at_30(run_async, monkeypatch):
    slept = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    async def _fake_post(method, payload):
        return {"ok": False, "error_code": 429,
                "parameters": {"retry_after": 999},
                "description": "Too Many Requests"}

    monkeypatch.setattr(rm, "_post", _fake_post)
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None
    assert slept[0] == 30


def test_429_twice_returns_none(run_async, monkeypatch):
    monkeypatch.setattr(rm, "_post", AsyncMock(return_value={
        "ok": False, "error_code": 429,
        "parameters": {"retry_after": 1},
        "description": "Too Many Requests",
    }))
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    out = run_async(edit_message_text_rich(1, 2, "hi"))
    assert out is None
