"""Tests for the `/app` command — the Mini App launcher (see design.md /
entrypoints.md 'Launcher command')."""

from unittest.mock import MagicMock

import pytest


def _dm_update(mk_update, text="/app", user_id=555):
    """A private-chat update — Telegram DMs have chat.id == user.id."""
    return mk_update(text, user_id=user_id, chat_id=user_id)


def _group_update(mk_update, text="/app"):
    return mk_update(text, chat_id=-1001)


def test_app_in_group_replies_dm_only_no_button(mk_bot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    bot = mk_bot()
    update = _group_update(mk_update)
    run_async(bot._handle_app_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert "private" in args[0].lower() or "dm" in args[0].lower()
    assert kwargs.get("reply_markup") is None


def test_app_dm_disabled_replies_not_enabled_no_button(mk_bot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    bot = mk_bot()
    update = _dm_update(mk_update)
    run_async(bot._handle_app_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert "enabled" in args[0].lower() or "not enabled" in args[0].lower() \
        or "isn't enabled" in args[0].lower()
    assert kwargs.get("reply_markup") is None


def test_app_dm_enabled_no_public_url_sends_instructions_no_button(
    mk_bot, mk_update, run_async, monkeypatch,
):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.miniapp.tunnel.detect_public_url", lambda: None)
    bot = mk_bot()
    update = _dm_update(mk_update)

    # Must not raise / log a traceback.
    run_async(bot._handle_app_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert "tailscale" in args[0].lower()
    assert kwargs.get("reply_markup") is None


def test_app_dm_enabled_with_url_sends_button(mk_bot, mk_update, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    # Personal mode's /app additionally requires the caller be the
    # operator (rev-iter1-003 / orchestrator F1) — this DM's sender
    # (user_id=555, _dm_update's default) IS the operator here.
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url",
        lambda: "https://my-node.tailxyz.ts.net/",
    )
    bot = mk_bot()
    update = _dm_update(mk_update)
    run_async(bot._handle_app_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    markup = kwargs.get("reply_markup")
    assert markup is not None
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert len(buttons) == 1
    assert buttons[0].web_app is not None
    assert buttons[0].web_app.url.startswith("https://")
    assert buttons[0].web_app.url == "https://my-node.tailxyz.ts.net/"


def test_app_dm_enabled_manual_url_override_skips_autodetect(
    mk_bot, mk_update, run_async, monkeypatch,
):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "https://manual.example/")
    # Personal mode's /app additionally requires the caller be the
    # operator (rev-iter1-003 / orchestrator F1) — this DM's sender
    # (user_id=555, _dm_update's default) IS the operator here.
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")

    def _should_not_be_called():
        raise AssertionError("detect_public_url must not run when a manual URL is set")

    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url", _should_not_be_called,
    )
    bot = mk_bot()
    update = _dm_update(mk_update)
    run_async(bot._handle_app_cmd(update, MagicMock()))

    args, kwargs = update.message.reply_text.await_args
    buttons = [b for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons[0].web_app.url == "https://manual.example/"


def test_app_detect_public_url_runs_off_the_event_loop(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """rev-iter1-002: detect_public_url() shells out synchronously
    (subprocess.run with a timeout) — it must be dispatched via
    run_in_executor, never awaited/called directly on the shared loop,
    or a hung tailscale binary would freeze every other scope's message
    handling for up to _TAILSCALE_TIMEOUT_SECONDS."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url",
        lambda: "https://my-node.tailxyz.ts.net/",
    )

    # asyncio.AbstractEventLoop.run_in_executor is just an abstract
    # stub -- the real implementation every concrete loop actually uses
    # (and overrides the abstract one via MRO) lives on BaseEventLoop.
    from asyncio.base_events import BaseEventLoop

    calls = []
    real_run_in_executor = BaseEventLoop.run_in_executor

    def _spy(self, executor, func, *args):
        calls.append((executor, func))
        return real_run_in_executor(self, executor, func, *args)

    monkeypatch.setattr(BaseEventLoop, "run_in_executor", _spy)

    bot = mk_bot()
    update = _dm_update(mk_update)
    run_async(bot._handle_app_cmd(update, MagicMock()))

    assert calls, "detect_public_url must be dispatched via run_in_executor"
    executor, func = calls[0]
    assert executor is None  # default executor — matches voice.py:101
    assert func() == "https://my-node.tailxyz.ts.net/"  # it's detect_public_url
    # And the result still made it into the button, proving the
    # executor call is actually on the request path, not a decoy.
    args, kwargs = update.message.reply_text.await_args
    buttons = [b for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons[0].web_app.url == "https://my-node.tailxyz.ts.net/"


# ===== Personal-mode operator gate (rev-iter1-003 / orchestrator F1) ===
#
# In personal mode (no team.yaml, no scopes.yaml) every other command is
# reachable by anyone who can DM the bot -- _authorize returns True
# unconditionally, and CommandHandlers are never chat-filtered. /app is
# the one exception: tapping its button discloses the tunnel URL and
# gets Telegram to sign an initData for the tapper's own user id, which
# /api/status would otherwise trust as "the operator" (see
# AuthMixin._is_personal_mode_operator / MiniAppServer's matching guard
# in tests/test_miniapp_server.py). So /app itself must refuse to hand
# the button to anyone but the operator.

def test_app_personal_mode_non_operator_denied_no_button(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """A stranger DMing a personal-mode bot must get the standard
    not-authorized reply, never the launcher button -- the button IS
    the disclosure (it carries the tunnel URL)."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")  # the operator is 555
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url",
        lambda: "https://should-not-be-disclosed.example/",
    )
    bot = mk_bot()  # team=None, scopes=None -> personal mode
    update = _dm_update(mk_update, user_id=999999)  # not the operator

    run_async(bot._handle_app_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert kwargs.get("reply_markup") is None
    assert "should-not-be-disclosed" not in args[0]
    assert "isn't configured to talk to you" in args[0].lower() \
        or "not on" in args[0].lower() or "allow-list" in args[0].lower()


def test_app_personal_mode_operator_still_gets_button(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """The operator themself must be unaffected by the new gate."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url",
        lambda: "https://my-node.tailxyz.ts.net/",
    )
    bot = mk_bot()
    update = _dm_update(mk_update, user_id=555)  # the operator

    run_async(bot._handle_app_cmd(update, MagicMock()))

    args, kwargs = update.message.reply_text.await_args
    assert kwargs.get("reply_markup") is not None
    buttons = [b for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert buttons[0].web_app.url == "https://my-node.tailxyz.ts.net/"


def test_app_personal_mode_malformed_chat_id_fails_closed(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """No operator identity configured (or unparsable CHAT_ID) must
    deny, never fall open to 'any Telegram user is the operator'."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "")  # unconfigured
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url",
        lambda: "https://should-not-be-disclosed.example/",
    )
    bot = mk_bot()
    update = _dm_update(mk_update, user_id=555)

    run_async(bot._handle_app_cmd(update, MagicMock()))

    args, kwargs = update.message.reply_text.await_args
    assert kwargs.get("reply_markup") is None


def test_app_unauthorized_team_member_gets_standard_denial(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """No button, no app-specific text — same allow-list rejection as
    every other command (entrypoints.md: 'no button is ever sent to an
    unauthorized chat')."""
    from aipager.team import Role, Rules, Team, User as TeamUser

    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url",
        lambda: "https://should-not-be-used.example/",
    )
    monkeypatch.setattr("aipager.bot.auth.record_pending_user", MagicMock())
    # Force "first time we see this user" so the denial reply fires.
    monkeypatch.setattr("aipager.bot.auth.remember_unauthorized", lambda uid: False)
    bot = mk_bot(team=Team(
        group_id=-100,
        users={7: TeamUser(id=7, label="member", role=Role.DEVELOPER)},
        rules=Rules(deny_tools=[]),
    ))
    # user_id=8 is not on the allow-list.
    update = _dm_update(mk_update, user_id=8)
    update.effective_message = update.message
    run_async(bot._handle_app_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    assert "allow-list" in args[0].lower() or "not on" in args[0].lower()
    assert kwargs.get("reply_markup") is None


@pytest.mark.parametrize("bad_url", ["http://not-https.example/", ""])
def test_app_refuses_non_https_manual_url(mk_bot, mk_update, run_async, monkeypatch, bad_url):
    """A configured MINIAPP_PUBLIC_URL that isn't https:// must never be
    sent as a button — falls back to the auto-detect / instructions path."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", bad_url)
    monkeypatch.setattr("aipager.miniapp.tunnel.detect_public_url", lambda: None)
    bot = mk_bot()
    update = _dm_update(mk_update)
    run_async(bot._handle_app_cmd(update, MagicMock()))

    args, kwargs = update.message.reply_text.await_args
    assert kwargs.get("reply_markup") is None
