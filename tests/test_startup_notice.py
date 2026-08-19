"""Tests for TelegramBot.send_startup_notice / _all_notify_chat_ids.

The one-time claude-provenance notice sent after the bot connects. Mirrors
tests/test_miniapp_launch_button.py's shape: the mocked `_app.bot` double
from `mk_bot()`, never a real Telegram call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.scope import Member, Scope


def _scope(chat_id, label):
    return Scope(chat_id=chat_id, kind="dm" if chat_id > 0 else "group",
                label=label,
                members=(Member(id=abs(chat_id), label="u", role="user"),))


# ----- _all_notify_chat_ids -----

def test_all_notify_chat_ids_multiscope(mk_bot):
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(-200, "team")])
    assert bot._all_notify_chat_ids() == [100, -200]


def test_all_notify_chat_ids_legacy_single_chat(mk_bot, monkeypatch):
    bot = mk_bot()  # scopes=None → legacy/personal mode
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    assert bot._all_notify_chat_ids() == [555]


def test_all_notify_chat_ids_legacy_no_chat_id_is_empty(mk_bot, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "")
    assert bot._all_notify_chat_ids() == []


def test_all_notify_chat_ids_no_scopes_no_config_never_raises(mk_bot, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "not-a-number")
    assert bot._all_notify_chat_ids() == []


# ----- send_startup_notice -----

def test_send_startup_notice_no_app_is_noop(mk_bot, run_async):
    bot = mk_bot()
    bot._app = None
    run_async(bot.send_startup_notice("claude: /x/claude (2.1.235)"))  # must not raise


def test_send_startup_notice_sends_to_each_scope_chat(mk_bot, run_async, monkeypatch):
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(-200, "team")])

    calls = []

    async def _fake_send_with_retry(app_bot, *, chat_id, text, **kw):
        calls.append((chat_id, text))

    monkeypatch.setattr("aipager.bot.lifecycle._send_with_retry", _fake_send_with_retry)

    run_async(bot.send_startup_notice("claude: /x/claude (2.1.235) · auth: none"))

    assert calls == [
        (100, "claude: /x/claude (2.1.235) · auth: none"),
        (-200, "claude: /x/claude (2.1.235) · auth: none"),
    ]


def test_send_startup_notice_uses_shared_retry_helper_not_bare_send_message(
        mk_bot, run_async, monkeypatch):
    """Not a bare send_message: goes through transport._send_with_retry,
    the same flood-control-aware helper every other outbound message uses."""
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    sentinel = AsyncMock()
    monkeypatch.setattr("aipager.bot.lifecycle._send_with_retry", sentinel)
    bot._app.bot.send_message = AsyncMock()

    run_async(bot.send_startup_notice("hello"))

    sentinel.assert_awaited_once_with(bot._app.bot, chat_id=555, text="hello")
    bot._app.bot.send_message.assert_not_awaited()


def test_send_startup_notice_one_chat_failure_does_not_abort_others(
        mk_bot, run_async, monkeypatch, caplog):
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(200, "ben")])

    seen = []

    async def _flaky(app_bot, *, chat_id, text, **kw):
        seen.append(chat_id)
        if chat_id == 100:
            raise RuntimeError("boom")

    monkeypatch.setattr("aipager.bot.lifecycle._send_with_retry", _flaky)

    import logging
    with caplog.at_level(logging.WARNING):
        run_async(bot.send_startup_notice("hi"))  # must not raise

    assert seen == [100, 200]
    assert any("startup notice" in r.message for r in caplog.records)
