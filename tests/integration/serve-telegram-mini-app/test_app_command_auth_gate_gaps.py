"""Gap-fill tests for the `/app` launcher command's authorization gate.

entrypoints.md, 'Launcher command': "Every reply (all four cases) is
subject to the same authorization gate as /status -- an unauthorized
sender sees the existing 'not on the allow-list' behaviour instead of
any of the above, and no button is ever sent to an unauthorized chat."

The developer's tests/test_bot_app_command.py exercises this gate for
exactly one combination: DM, feature enabled, URL available. This file
covers the three remaining feature-state combinations, and does so from
BOTH a group chat and a DM, since the developer's only unauthorized
test used a DM. It also pins down the single-message / single-button
invariant for the authorized happy path independently of the
developer's test.
"""

from unittest.mock import MagicMock

import pytest

from aipager.team import Role, Rules, Team, User as TeamUser


def _team_bot(mk_bot, *, allowed_id=7):
    return mk_bot(team=Team(
        group_id=-100,
        users={allowed_id: TeamUser(id=allowed_id, label="member", role=Role.DEVELOPER)},
        rules=Rules(deny_tools=[]),
    ))


def _prep_denial(monkeypatch):
    monkeypatch.setattr("aipager.bot.auth.record_pending_user", MagicMock())
    monkeypatch.setattr("aipager.bot.auth.remember_unauthorized", lambda uid: False)


@pytest.mark.parametrize(
    "miniapp_enabled,public_url",
    [
        (False, None),
        (True, None),
        (True, "https://should-not-be-used.example/"),
    ],
    ids=["disabled", "enabled-no-url", "enabled-with-url"],
)
def test_app_unauthorized_group_sender_always_gets_standard_denial(
    mk_bot, mk_update, run_async, monkeypatch, miniapp_enabled, public_url,
):
    """Unauthorized sender in a GROUP chat, across every feature-state
    combination: always the plain allow-list denial, never the group's
    DM-only explanation, never a button."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", miniapp_enabled)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url", lambda: public_url,
    )
    _prep_denial(monkeypatch)
    bot = _team_bot(mk_bot)
    update = mk_update("/app", user_id=8)  # group chat via mk_update's default chat_id
    update.effective_message = update.message

    run_async(bot._handle_app_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    msg = args[0].lower()
    # Positively confirms the standard denial fired (as opposed to any of
    # the app-command-specific replies below). Deliberately not also
    # asserting the DM-only copy is absent by substring match on "dm" --
    # the denial text itself legitimately contains "admin", producing a
    # false positive on a naive "dm" in msg check.
    assert "allow-list" in msg or "not on" in msg
    assert kwargs.get("reply_markup") is None


@pytest.mark.parametrize(
    "miniapp_enabled,public_url",
    [(False, None), (True, None)],
    ids=["disabled", "enabled-no-url"],
)
def test_app_unauthorized_dm_sender_gets_standard_denial_not_feature_reply(
    mk_bot, mk_update, run_async, monkeypatch, miniapp_enabled, public_url,
):
    """Unauthorized DM sender must get the allow-list denial even for the
    two feature-state combinations the developer's suite never paired
    with an unauthorized sender."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", miniapp_enabled)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url", lambda: public_url,
    )
    _prep_denial(monkeypatch)
    bot = _team_bot(mk_bot)
    update = mk_update("/app", user_id=8, chat_id=8)  # DM: chat.id == user.id
    update.effective_message = update.message

    run_async(bot._handle_app_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    msg = args[0].lower()
    assert "allow-list" in msg or "not on" in msg
    assert "tailscale" not in msg
    assert "isn't enabled" not in msg and "not enabled" not in msg
    assert kwargs.get("reply_markup") is None


def test_app_authorized_happy_path_sends_exactly_one_message_and_one_button(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """entrypoints.md: 'bot sends exactly one message containing an
    inline keyboard with one button.'"""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr(
        "aipager.miniapp.tunnel.detect_public_url",
        lambda: "https://my-node.tailxyz.ts.net/",
    )
    # Personal mode's operator IS CHAT_ID (a DM's chat_id is the peer's
    # user id), so pin it to the sender — without this the sender is
    # just "some Telegram user" and the operator guard correctly denies.
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    bot = mk_bot()
    update = mk_update("/app", user_id=555, chat_id=555)

    run_async(bot._handle_app_cmd(update, MagicMock()))

    assert update.message.reply_text.await_count == 1
    _, kwargs = update.message.reply_text.await_args
    markup = kwargs.get("reply_markup")
    assert markup is not None
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert len(buttons) == 1
    assert buttons[0].web_app.url.startswith("https://")
