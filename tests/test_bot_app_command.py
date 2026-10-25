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


# Anything that would have the user run a command on the host. The
# tunnel is managed by aipager itself now, so there is nothing for them
# to install, and the previous version of this message asked for two
# `sudo` lines and a `curl | sh` — from a chat message.
FORBIDDEN_IN_APP_REPLY = (
    "tailscale", "sudo", "curl", "apt-get", "brew install", "| sh",
)


def test_app_dm_enabled_no_public_url_says_wait_and_never_instructs(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """This test used to assert the OPPOSITE — `"tailscale" in text` — and
    so pinned the contract violation in place. Inverted deliberately."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.miniapp.tunnel.detect_public_url", lambda: None)
    monkeypatch.setattr(
        "aipager.miniapp.server.miniapp_extra_available", lambda: True)
    bot = mk_bot()
    update = _dm_update(mk_update)

    # Must not raise / log a traceback.
    run_async(bot._handle_app_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    text = args[0].lower()
    for bad in FORBIDDEN_IN_APP_REPLY:
        assert bad not in text, f"/app told the user to run {bad!r}: {text!r}"
    assert "still being set up" in text
    assert kwargs.get("reply_markup") is None


def test_app_says_so_plainly_when_the_miniapp_cannot_start(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """A different cause deserves a different sentence — and still no
    command for the user to run.

    Reworded when aiohttp became a base dependency: reaching this branch
    no longer means "you skipped an extra", it means the install is
    incomplete, and blaming the user for a missing option they were
    never told about would be wrong."""
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.miniapp.tunnel.detect_public_url", lambda: None)
    monkeypatch.setattr(
        "aipager.miniapp.server.miniapp_extra_available", lambda: False)
    bot = mk_bot()
    update = _dm_update(mk_update)

    run_async(bot._handle_app_cmd(update, MagicMock()))

    text = update.message.reply_text.await_args.args[0].lower()
    assert "can't start" in text
    assert "incomplete" in text
    for bad in FORBIDDEN_IN_APP_REPLY:
        assert bad not in text, f"leaked an instruction: {text!r}"


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


def test_the_app_keyboard_label_routes_to_the_app_command(mk_bot, mk_update, run_async):
    """rev-iter1-004. A `web_app` keyboard button sends no text, so this
    label only ever arrives from a client too old to understand one (or
    typed). The reply keyboard has no App button since 2026-10-12.
    It must reach /app's inline-button fallback rather than being taken
    for a prompt and typed into a session."""
    from unittest.mock import AsyncMock

    from aipager.config import APP_BUTTON

    bot = mk_bot()
    bot._handle_app_cmd = AsyncMock()
    update = mk_update(APP_BUTTON, chat_id=555)  # a DM

    run_async(bot._handle_message(update, None))

    bot._handle_app_cmd.assert_awaited_once()


# ===== /app while the managed tunnel is not working ======================
#
# The state comes from aipager.miniapp.tunnel_state (the tunnel manager's
# in-memory slot); who may see the cause is the /update rule
# (_is_update_admin): the operator in personal mode, an admin of the chat
# in a group setup. Everyone else gets a short "not available right now".

# One phrase of each reason's English text, to tell the replies apart.
REASON_PHRASE = {
    "dns_incomplete": "incomplete answer",
    "dns_lookup_failed": "could not look up",
    "quick_tunnel": "could not get a link",
    "edge_unreachable": "7844",
    "binary_missing": "download or start cloudflared",
    "unreachable": "never answered",
    "other": "aipager doctor",
}
# What cloudflared itself wrote: the log's, never the chat's.
RAW_CLOUDFLARED = "expected at least 2 Cloudflare Regions regions, but SRV only returned 1"


def _failing(monkeypatch, reason="dns_incomplete", phase="retrying"):
    from aipager.miniapp import tunnel_state

    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr("aipager.miniapp.tunnel.detect_public_url", lambda: None)
    monkeypatch.setattr("aipager.miniapp.server.miniapp_extra_available", lambda: True)
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    tunnel_state.publish_status(tunnel_state.TunnelStatus(
        phase=phase, reason=reason, attempt=3, next_try_at=0.0, since=1.0))
    attempts = []

    def _hook():
        attempts.append(1)
        return True
    tunnel_state.set_attempt_hook(_hook)
    return attempts


def _dm_scope(user_id, role):
    from aipager.scope import Member, Scope
    return Scope(chat_id=user_id, kind="dm", label="dm",
                 members=(Member(id=user_id, label="alice", role=role),))


def _scoped_bot(mk_bot, user_id, role):
    from pathlib import Path

    from aipager.policy import load_policy

    bot = mk_bot(scopes=[_dm_scope(user_id, role)])
    bot.policy = load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))
    return bot


def _reply(update):
    update.message.reply_text.assert_awaited_once()
    args, kwargs = update.message.reply_text.await_args
    return args[0], kwargs


@pytest.mark.parametrize("who", ["personal_operator", "scope_admin"])
@pytest.mark.parametrize("reason", list(REASON_PHRASE))
def test_admin_gets_the_reason(mk_bot, mk_update, run_async, monkeypatch, who, reason):
    _failing(monkeypatch, reason)
    if who == "personal_operator":
        bot, update = mk_bot(), _dm_update(mk_update, user_id=555)
    else:
        bot, update = _scoped_bot(mk_bot, 777, "admin"), _dm_update(mk_update, user_id=777)

    run_async(bot._handle_app_cmd(update, MagicMock()))

    text, kwargs = _reply(update)
    assert REASON_PHRASE[reason] in text
    assert "keeps trying by itself" in text
    assert kwargs.get("reply_markup") is None


def test_an_unknown_reason_reads_as_other(mk_bot, mk_update, run_async, monkeypatch):
    _failing(monkeypatch, "martian")
    update = _dm_update(mk_update, user_id=555)
    run_async(mk_bot()._handle_app_cmd(update, MagicMock()))
    text, kwargs = _reply(update)
    assert "<code>aipager doctor</code>" in text and kwargs.get("parse_mode") == "HTML"


@pytest.mark.parametrize("who", ["personal_stranger", "scope_member"])
def test_non_admin_gets_unavailable_without_details(mk_bot, mk_update, run_async,
                                                    monkeypatch, who):
    """A member whose role cannot manage gets no machine details, even in
    their own private chat with the bot."""
    _failing(monkeypatch, "dns_incomplete")
    if who == "personal_stranger":
        bot, update = mk_bot(), _dm_update(mk_update, user_id=999999)
    else:
        bot, update = _scoped_bot(mk_bot, 778, "user"), _dm_update(mk_update, user_id=778)

    run_async(bot._handle_app_cmd(update, MagicMock()))

    text, kwargs = _reply(update)
    assert "isn't available right now" in text
    for phrase in REASON_PHRASE.values():
        assert phrase not in text
    assert "DNS" not in text and "cloudflared" not in text
    assert kwargs.get("reply_markup") is None


@pytest.mark.parametrize("reason", list(REASON_PHRASE) + ["unavailable"])
def test_app_reason_texts_never_carry_cloudflared_text(mk_bot, mk_update, run_async,
                                                       monkeypatch, reason):
    from aipager.miniapp import tunnel_manager as tm

    _failing(monkeypatch, "dns_incomplete" if reason == "unavailable" else reason)
    log, err = tm._log_paths()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"level":"error","error":"%s","message":"Initiating shutdown"}\n'
                   % RAW_CLOUDFLARED)
    err.write_text(RAW_CLOUDFLARED + "\n")
    uid = 999999 if reason == "unavailable" else 555
    update = _dm_update(mk_update, user_id=uid)

    run_async(mk_bot()._handle_app_cmd(update, MagicMock()))

    text, _kwargs = _reply(update)
    assert RAW_CLOUDFLARED not in text and "SRV" not in text
    for bad in FORBIDDEN_IN_APP_REPLY:
        assert bad not in text.lower(), f"/app told the user to run {bad!r}: {text!r}"


def test_app_while_starting_says_setting_up(mk_bot, mk_update, run_async, monkeypatch):
    attempts = _failing(monkeypatch, "", phase="starting")
    update = _dm_update(mk_update, user_id=555)
    run_async(mk_bot()._handle_app_cmd(update, MagicMock()))
    text, _kwargs = _reply(update)
    assert "still being set up" in text
    assert attempts == []


@pytest.mark.parametrize("who", ["personal_operator", "scope_member"])
def test_app_in_the_slow_phase_starts_an_attempt(mk_bot, mk_update, run_async,
                                                 monkeypatch, who):
    attempts = _failing(monkeypatch, "dns_incomplete", phase="slow")
    if who == "personal_operator":
        bot, update = mk_bot(), _dm_update(mk_update, user_id=555)
    else:
        bot, update = _scoped_bot(mk_bot, 778, "user"), _dm_update(mk_update, user_id=778)
    run_async(bot._handle_app_cmd(update, MagicMock()))
    assert attempts == [1]


def test_app_while_retrying_fast_starts_no_attempt(mk_bot, mk_update, run_async,
                                                   monkeypatch):
    attempts = _failing(monkeypatch, "dns_incomplete", phase="retrying")
    run_async(mk_bot()._handle_app_cmd(_dm_update(mk_update, user_id=555), MagicMock()))
    assert attempts == []


def test_personal_stranger_app_does_not_start_an_attempt(mk_bot, mk_update, run_async,
                                                         monkeypatch):
    attempts = _failing(monkeypatch, "dns_incomplete", phase="slow")
    update = _dm_update(mk_update, user_id=999999)
    run_async(mk_bot()._handle_app_cmd(update, MagicMock()))
    assert attempts == []
    text, _kwargs = _reply(update)
    assert "isn't available right now" in text
