"""Priority 1 (task brief): `/new !name` must be byte-for-byte unchanged.

spec.md's own live-verified example: ``/new !henlo`` -> "✅ henlo created ·
🤖 Auto mode". design.md's shared-file integration plan says the ONLY
change to ``_handle_new_cmd`` is the no-args branch now delegating to
``new_flow.start_wizard`` — every argument form (``/new name``,
``/new !name``, ``/new name prompt``) falls through unchanged below that
branch. These tests prove the wizard was spliced in without perturbing
that fall-through path: same reply shape, same single edit, and —
mechanically, not just by inspection — the wizard's own entry point is
never invoked for an args-bearing ``/new``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.bot import new_flow
from aipager.state import SessionRegistry


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_update(text, *, user_id=12345, chat_id=0):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = 999
    update.message.reply_text = AsyncMock()
    update.message.reply_to_message = None
    update.message.quote = None
    # calling_chat_id() falls back to update.message.chat.id when
    # effective_chat.id is None — pin it to None too so chat_id=None
    # genuinely produces an unscoped (legacy) call, not a stray MagicMock.
    update.message.chat = None
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = "private"
    return update


def _make_bot():
    from aipager.bot import TelegramBot
    registry = SessionRegistry()
    bot = TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    return bot


async def _launch_ok(*a, **kw):
    return True, ""


# --------------------------------------------------------------------------- #
# The mechanical proof: the wizard's own entry point is never reached        #
# --------------------------------------------------------------------------- #

def test_new_bang_name_never_invokes_wizard_start():
    """`/new !name` must not call new_flow.start_wizard at all — the
    args-bearing branch falls all the way through to the pre-existing
    one-shot code, per design.md's own description of the single-line
    change to `_handle_new_cmd`."""
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !henlo")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok), \
         patch.object(new_flow, "start_wizard", AsyncMock()) as wiz:
        _run(bot._handle_new_cmd(update, MagicMock()))
    wiz.assert_not_awaited()


def test_new_named_no_bang_never_invokes_wizard_start():
    """Same proof for the Ask-mode one-shot form (`/new name`, no `!`)."""
    bot = _make_bot()
    update = _make_update("/new henlo")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok), \
         patch.object(new_flow, "start_wizard", AsyncMock()) as wiz:
        _run(bot._handle_new_cmd(update, MagicMock()))
    wiz.assert_not_awaited()


def test_new_no_args_does_invoke_wizard_start():
    """Sanity control for the two tests above: the mock actually gets
    called when it is supposed to, so `assert_not_awaited` above is
    proving something rather than trivially passing."""
    bot = _make_bot()
    update = _make_update("/new")
    with patch.object(new_flow, "start_wizard", AsyncMock()) as wiz:
        _run(bot._handle_new_cmd(update, MagicMock()))
    wiz.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Exact reply shape — matches spec.md's live-verified example verbatim      #
# --------------------------------------------------------------------------- #

def test_new_bang_name_reply_has_success_checkmark_and_label():
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !henlo")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))
    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "✅ <b>henlo</b> created" in text, text


def test_new_bang_name_reply_says_auto_mode():
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !henlo")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))
    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "🤖 Auto mode" in text, text


def test_new_bang_name_only_edits_the_launch_message_once():
    """No extra step: the success path is exactly one `reply_text`
    (the "🚀 Launching…" message) followed by exactly one `edit_text`
    on it — never a second outbound message (e.g. a confirm prompt)."""
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !henlo")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    status_msg = update.message.reply_text.return_value
    status_msg.edit_text.assert_awaited_once()


def test_new_bang_name_registers_session_with_skip_perms_true():
    """Session shape: Auto mode via `!` prefix must map to
    `skip_perms=True` on the created TrackedSession — the same object
    the Mini App's create route and the wizard's Confirm both produce
    through `create_session()`."""
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update("/new !henlo")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))
    sess = bot.registry.find_by_label("henlo", 0)
    assert sess is not None and sess.skip_perms is True


def test_new_named_no_bang_reply_says_ask_mode():
    bot = _make_bot()
    update = _make_update("/new henlo")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))
    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "💬 Ask mode" in text, text


def test_new_named_no_bang_registers_session_with_skip_perms_false():
    bot = _make_bot()
    update = _make_update("/new henlo")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_new_cmd(update, MagicMock()))
    sess = bot.registry.find_by_label("henlo", 0)
    assert sess is not None and sess.skip_perms is False


# --------------------------------------------------------------------------- #
# `restart`/`rename`/`delete`/`diff` reservation — must be checked on the   #
# BARE label, before scope disambiguation, in EVERY chat mode (legacy and  #
# scoped) — not only inside `inject.launch_session` (which would silently  #
# miss a scoped, suffixed name like "restart__g100").                      #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("reserved", ["restart", "rename", "delete", "diff"])
def test_new_bang_rejects_new_reserved_command_words_legacy_mode(reserved):
    """design.md Stream B item 1: the four new command verbs must be
    reserved session-name words, exactly like `status`/`stop`/`kill`
    already are, so a session can never be created that would shadow one
    of the new commands via `/<label>` routing.

    Legacy/personal mode (no scope suffix). Deliberately does NOT mock
    `inject.launch_session` — the real reserved-word rejection this test
    targets happens in `_handle_new_cmd` itself, entirely before
    `create_session`/`launch_session` is ever reached, so calling the
    real function is safe (it is simply never invoked for these inputs).
    """
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update(f"/new !{reserved}", chat_id=None)
    _run(bot._handle_new_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert "command name" in text.lower(), text
    assert f"/{reserved}" in text, text
    assert bot.registry.get(f"claude-{reserved}") is None


@pytest.mark.parametrize("reserved", ["restart", "rename", "delete", "diff"])
def test_new_bang_reserved_word_check_runs_before_scope_suffixing(reserved):
    """Same intent as the legacy-mode test above, but in a SCOPED chat
    (`scope_chat_id` set, e.g. a group). `create_session` would build the
    internal name via `scope.disambiguated_name`
    (`"claude-<label>__<kind><chat_id>"`) before ever reaching
    `inject.launch_session` — so if the ONLY reserved-word gate were the
    one inside `launch_session` (which checks the already-suffixed
    string), a scoped `/new !restart` would slip through, since
    `"restart__g100"` never matches the bare `_RESERVED` set.

    `inject.launch_session` is mocked here as a SPY (never actually
    invoked for these inputs, so no dtach/subprocess touch either way)
    specifically to prove the rejection happens BEFORE create_session is
    even reached — the check runs on the bare label directly inside
    `_handle_new_cmd`, immune to scope suffixing by construction.
    """
    bot = _make_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = _make_update(f"/new !{reserved}", chat_id=-100)

    with patch("aipager.dtach.inject.launch_session", AsyncMock()) as spy:
        _run(bot._handle_new_cmd(update, MagicMock()))

    spy.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args[0][0]
    assert "command name" in text.lower(), text
    assert f"/{reserved}" in text, text
