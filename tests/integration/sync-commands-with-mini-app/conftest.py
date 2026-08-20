"""Shared black-box fixtures for the sync-commands-with-mini-app suite.

These tests exercise the REAL dispatch seams documented in
entrypoints.md — ``bot._handle_message`` (text captures for the wizard
and rename), ``bot._handle_callback`` (every ``callback_data`` tap),
and the registered command handlers (``/new``, ``/restart``,
``/rename``, ``/delete``, ``/diff``, ``/settings``, ``/status``) — never
the internals of ``aipager.bot.new_flow`` / ``aipager.bot.session_parity``
directly. Only exported, entrypoints.md-documented functions
(``new_flow.start_wizard`` is reached only via ``/new``;
``session_parity.handle_restart_cmd`` etc. are reached the same way the
real ``CommandHandler`` registration reaches them: ``functools.partial``
composition is irrelevant to a black-box caller, so tests call the
underlying function with ``bot`` as the first arg exactly as
``lifecycle.py`` does).

Root ``tests/conftest.py`` already autouse-blocks real Telegram HTTP,
the real credential probe, and redirects every real-home path to
``tmp_path`` — nothing here needs to repeat that.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession


# --------------------------------------------------------------------------- #
# Scope / bot builders
# --------------------------------------------------------------------------- #

def make_scopes(*, chat_id=-100, kind="group", members=()):
    """One scope with the given members. ``members`` is a list of
    ``(id, label, role)`` tuples."""
    return [Scope(
        chat_id=chat_id, kind=kind, label="scope",
        members=tuple(
            Member(id=mid, label=label, role=role)
            for mid, label, role in members
        ),
    )]


def _upgrade_bot_api_mocks(bot):
    """The shared ``mk_bot`` root fixture only upgrades
    ``bot._app.bot.send_message`` to an ``AsyncMock`` — every other
    ``python-telegram-bot`` Bot API method stays a plain (auto-created)
    ``MagicMock`` attribute of the plain ``MagicMock()`` at
    ``bot._app.bot``. Several of this feature's surfaces (the wizard,
    the ⋮ menu, the second-``/new``-supersedes-the-first edit) render by
    calling ``await bot._app.bot.edit_message_text(...)`` directly
    (the same cross-turn "one message, edited in place" convention
    already used by ``dashboard.py``/``animation.py``/``notify.py``),
    NOT by editing the object ``update.message.reply_text(...)``
    returned in an earlier turn.

    Awaiting a plain ``MagicMock`` call raises ``TypeError: object
    MagicMock can't be used in 'await' expression`` — silently, if the
    call site wraps it in a broad ``try/except`` (common in this
    codebase for best-effort UI updates). A test built on the unmodified
    root fixture would then observe "nothing happened" and could easily
    mistake a swallowed crash for "no assertion needed here", which is
    exactly the "passes for reasons unrelated to its name" failure mode
    this whole suite exists to catch. Every helper below evaluates the
    session's ``.await_args``/``.await_args_list`` on these methods, so
    they must be real, tracking ``AsyncMock`` instances.
    """
    api = bot._app.bot
    for name in (
        "send_message", "edit_message_text", "edit_message_reply_markup",
        "delete_message", "send_document", "answer_callback_query",
        "set_my_commands",
    ):
        setattr(api, name, AsyncMock())
    return bot


def make_scoped_bot(mk_bot, *, chat_id=-100, kind="group", members=(),
                     miniapp_url=""):
    """A TelegramBot in scope mode with the given membership."""
    scopes = make_scopes(chat_id=chat_id, kind=kind, members=members)
    bot = mk_bot(scopes=scopes)
    bot.policy = load_policy()
    bot._miniapp_url = miniapp_url
    return _upgrade_bot_api_mocks(bot)


def make_personal_bot(mk_bot, *, miniapp_url=""):
    """A TelegramBot in personal mode (scopes=None, team=None) — always
    authorized, matching ``mk_bot()``'s own default."""
    bot = mk_bot()
    bot._miniapp_url = miniapp_url
    return _upgrade_bot_api_mocks(bot)


# --------------------------------------------------------------------------- #
# Update / CallbackQuery builders
# --------------------------------------------------------------------------- #

def make_message_update(text, *, user_id=12345, chat_id=-100,
                          chat_type="group", message_id=999):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = message_id
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    update.message.reply_to_message = None
    update.message.quote = None
    update.effective_message = update.message
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "tester"
    update.effective_user.first_name = "Test"
    update.effective_user.last_name = ""
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    return update


def make_callback_update(callback_data, *, user_id=12345, chat_id=-100,
                           chat_type="group", message_id=42, text=""):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = text
    query.message.chat = MagicMock()
    query.message.chat.id = chat_id
    query.message.chat.type = chat_type
    query.message.reply_text = AsyncMock()
    query.message.edit_text = AsyncMock()
    query.from_user = MagicMock()
    query.from_user.id = user_id
    query.from_user.username = "tester"
    query.from_user.first_name = "Test"
    query.from_user.last_name = ""
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = query.message.chat
    update.effective_message = query.message
    return update, query


def make_session(name, label, *, status=Status.IDLE, scope_chat_id=-100,
                  cwd=""):
    return TrackedSession(name=name, label=label, status=status,
                           scope_chat_id=scope_chat_id, cwd=cwd)


@pytest.fixture
def registry():
    return SessionRegistry()


# --------------------------------------------------------------------------- #
# Multi-step surfaces (the wizard, the ⋮ menu, /settings) render by editing #
# ONE message across turns via ``bot._app.bot.edit_message_text`` — the    #
# established convention already used by dashboard.py/animation.py/        #
# notify.py for exactly this "one persistent message, edited in place"     #
# shape. These helpers read that shared mock robustly across both the      #
# positional (`text, chat_id, message_id`) and keyword calling styles.     #
# --------------------------------------------------------------------------- #

def latest_edit(bot):
    """(text, reply_markup, chat_id, message_id) of the most recent
    ``bot._app.bot.edit_message_text`` call."""
    mock = bot._app.bot.edit_message_text
    assert mock.await_args is not None, (
        "expected bot._app.bot.edit_message_text to have been called at "
        "least once"
    )
    return _decode_edit_call(mock.await_args)


def all_edits(bot):
    """Every ``bot._app.bot.edit_message_text`` call, decoded, in order."""
    mock = bot._app.bot.edit_message_text
    return [_decode_edit_call(c) for c in mock.await_args_list]


def _decode_edit_call(call):
    args, kwargs = call
    text = kwargs.get("text")
    chat_id = kwargs.get("chat_id")
    message_id = kwargs.get("message_id")
    markup = kwargs.get("reply_markup")
    positional = list(args)
    # Fill in from positional args in the (text, chat_id, message_id)
    # order dashboard.py's own call site uses, for whichever of the four
    # weren't passed as keywords.
    if text is None and len(positional) > 0:
        text = positional[0]
    if chat_id is None and len(positional) > 1:
        chat_id = positional[1]
    if message_id is None and len(positional) > 2:
        message_id = positional[2]
    if markup is None and len(positional) > 3:
        markup = positional[3]
    return text, markup, chat_id, message_id


def callback_data_in(markup):
    """Every ``callback_data`` string present in an InlineKeyboardMarkup,
    in row-major order. ``[]`` for ``None``."""
    if markup is None:
        return []
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data is not None
    ]


# --------------------------------------------------------------------------- #
# Bundled fixture — this directory's name isn't a valid Python identifier
# (hyphens), so sibling test files can't `from .conftest import x` (pytest
# still loads this conftest.py by path, but the package machinery needed
# for a relative import doesn't exist for a non-identifier directory name).
# Every helper above is exposed through one `helpers` fixture instead —
# the same "no plain-function import, everything via a fixture" shape this
# repo's OTHER hyphenated `tests/integration/*` directories already use.
# --------------------------------------------------------------------------- #

@pytest.fixture
def helpers():
    return types.SimpleNamespace(
        make_scopes=make_scopes,
        make_scoped_bot=make_scoped_bot,
        make_personal_bot=make_personal_bot,
        make_message_update=make_message_update,
        make_callback_update=make_callback_update,
        make_session=make_session,
        latest_edit=latest_edit,
        all_edits=all_edits,
        callback_data_in=callback_data_in,
    )
