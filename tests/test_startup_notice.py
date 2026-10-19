"""Tests for TelegramBot.send_startup_notice / _all_notify_chat_ids.

The one-time claude-provenance notice sent after the bot connects. Mirrors
tests/test_miniapp_launch_button.py's shape: the mocked `_app.bot` double
from `mk_bot()`, never a real Telegram call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager import i18n
from aipager.bot import auth_notice
from aipager.claude_bootstrap import AuthNotice
from aipager.scope import Member, Scope

NOTICE = AuthNotice(found=("ANTHROPIC_API_KEY",), fix="gateway")
ENGLISH = auth_notice.render(NOTICE, i18n.get("en"))


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
    run_async(bot.send_startup_notice(NOTICE))  # must not raise


def test_send_startup_notice_sends_to_each_scope_chat(mk_bot, run_async, monkeypatch):
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(-200, "team")])

    calls = []

    async def _fake_send_with_retry(app_bot, *, chat_id, text, **kw):
        calls.append((chat_id, text))

    monkeypatch.setattr("aipager.bot.lifecycle._send_with_retry", _fake_send_with_retry)

    run_async(bot.send_startup_notice(NOTICE))

    assert calls == [(100, ENGLISH), (-200, ENGLISH)]


def test_send_startup_notice_uses_shared_retry_helper_not_bare_send_message(
        mk_bot, run_async, monkeypatch):
    """Not a bare send_message: goes through transport._send_with_retry,
    the same flood-control-aware helper every other outbound message uses."""
    bot = mk_bot()
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    sentinel = AsyncMock()
    monkeypatch.setattr("aipager.bot.lifecycle._send_with_retry", sentinel)
    bot._app.bot.send_message = AsyncMock()

    run_async(bot.send_startup_notice(NOTICE))

    sentinel.assert_awaited_once_with(bot._app.bot, chat_id=555, text=ENGLISH)
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
        run_async(bot.send_startup_notice(NOTICE))  # must not raise

    assert seen == [100, 200]
    assert any("startup notice" in r.message for r in caplog.records)


def test_each_chat_gets_the_notice_in_its_own_language(mk_bot, run_async, monkeypatch):
    """The notice reaches every configured chat, each in the language in
    force there, not one language for all."""
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(-200, "team")])
    languages = {100: "fa", -200: "de"}
    monkeypatch.setattr("aipager.i18n.for_chat",
                        lambda chat_id: i18n.get(languages[chat_id]))
    calls = []

    async def _fake_send_with_retry(app_bot, *, chat_id, text, **kw):
        calls.append((chat_id, text))

    monkeypatch.setattr("aipager.bot.lifecycle._send_with_retry", _fake_send_with_retry)

    run_async(bot.send_startup_notice(NOTICE))

    assert calls == [(100, auth_notice.render(NOTICE, i18n.get("fa"))),
                     (-200, auth_notice.render(NOTICE, i18n.get("de")))]
    assert calls[0][1] != calls[1][1] != ENGLISH


def test_a_notice_that_cannot_be_worded_in_a_chats_language_goes_in_english(
        mk_bot, run_async, monkeypatch):
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(200, "ben")])
    real = auth_notice.render

    def _render(notice, tr):
        if tr.code == "fa":
            raise RuntimeError("no words")
        return real(notice, tr)

    monkeypatch.setattr(auth_notice, "render", _render)
    monkeypatch.setattr("aipager.i18n.for_chat",
                        lambda chat_id: i18n.get("fa" if chat_id == 100 else "en"))
    calls = []

    async def _fake_send_with_retry(app_bot, *, chat_id, text, **kw):
        calls.append((chat_id, text))

    monkeypatch.setattr("aipager.bot.lifecycle._send_with_retry", _fake_send_with_retry)

    run_async(bot.send_startup_notice(NOTICE))  # must not raise

    # The one message that says no session can log in: English, not nothing.
    assert calls == [(100, ENGLISH), (200, ENGLISH)]


def test_a_notice_that_cannot_be_worded_at_all_still_reaches_the_other_chats(
        mk_bot, run_async, monkeypatch):
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(200, "ben")])
    calls = []

    def _render(notice, tr):
        raise RuntimeError("no words")

    async def _fake_send_with_retry(app_bot, *, chat_id, text, **kw):
        calls.append((chat_id, text))

    monkeypatch.setattr(auth_notice, "render", _render)
    monkeypatch.setattr("aipager.bot.lifecycle._send_with_retry", _fake_send_with_retry)
    run_async(bot.send_startup_notice(NOTICE))  # must not raise
    assert calls == []
