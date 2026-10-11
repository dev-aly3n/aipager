"""A /model refused, in the reader's language, by its refusal code
(multilingual phase 3a, design section E).

``session_ops.model_switch_refusal`` keeps returning the Mini App's
English (``miniapp.sessions.MODEL_SWITCH_*``); the chat renders the code
through the same keys (one key per sentence for both surfaces)."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import handlers, session_ops
from aipager.miniapp import sessions as mini
from aipager.state import Status
from tests import i18n_ml2

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")

MINI_APP = {
    "not_live": mini.MODEL_SWITCH_NOT_LIVE_REASON,
    "busy": mini.MODEL_SWITCH_BUSY_REASON,
    "prompt_open": mini.MODEL_SWITCH_PROMPT_OPEN_REASON,
    "unknown": mini.MODEL_SWITCH_UNKNOWN_REASON,
    "switch_pending": mini.MODEL_SWITCH_PENDING_REASON,
}


def test_english_matches_the_mini_app():
    assert set(session_ops.MODEL_REFUSAL_MSGS) == set(MINI_APP)
    en = i18n.get("en")
    for code, english in MINI_APP.items():
        assert en.text(session_ops.MODEL_REFUSAL_MSGS[code]) == english, code
        # One key per sentence: the chat's refusal is the Mini App's key.
        assert session_ops.MODEL_REFUSAL_MSGS[code] is english, code
    assert en.text(session_ops.MODEL_UNCONFIRMED) == mini.MODEL_SWITCH_UNCONFIRMED
    assert session_ops.MODEL_UNCONFIRMED is mini.MODEL_SWITCH_UNCONFIRMED


def test_every_refusal_the_core_returns_has_a_key():
    """The codes model_switch_refusal can return, each with its key."""
    sess = MagicMock()
    sess.dialog_is_open = lambda: False
    sess.model_switch_pending = lambda: False
    seen = set()
    for status in (Status.GONE, Status.BUSY, Status.UNKNOWN):
        sess.status = status
        seen.add(session_ops.model_switch_refusal(sess)[0])
    sess.status = Status.IDLE
    sess.dialog_is_open = lambda: True
    seen.add(session_ops.model_switch_refusal(sess)[0])
    sess.dialog_is_open = lambda: False
    sess.model_switch_pending = lambda: True
    seen.add(session_ops.model_switch_refusal(sess)[0])
    assert seen == set(session_ops.MODEL_REFUSAL_MSGS)


def test_an_unknown_code_shows_the_detail_as_received():
    for code in ("en", "fa", "ru"):
        assert session_ops.model_refusal_text(i18n.get(code), "new_code", "as is") == "as is"


@pytest.mark.parametrize("code,script", [("fa", PERSIAN), ("ru", CYRILLIC)])
def test_a_chat_reads_the_refusal_in_its_language(mk_bot, run_async, code, script):
    preferences.set_ui_language(i18n_ml2.DM, code)
    bot = i18n_ml2.make_bot(mk_bot)
    sess = i18n_ml2.add_session(bot, status=Status.BUSY)
    bot.registry.set_target(sess.name, i18n_ml2.DM, i18n_ml2.OWNER)
    update = i18n_ml2.message("x")
    from aipager.dtach import inject
    import unittest.mock as um
    with um.patch.object(inject, "is_alive", AsyncMock(return_value=True)):
        run_async(bot._send_command(update, "/model opus"))
    reply = update.message.reply_text.await_args.args[0]
    assert script.search(reply) and mini.MODEL_SWITCH_BUSY_REASON not in reply
    assert "x1" in reply
    # The Mini App's English is untouched.
    assert session_ops.model_switch_refusal(sess) == ("busy", mini.MODEL_SWITCH_BUSY_REASON)


def test_the_other_sender_refusal_keeps_its_english():
    assert handlers.MODEL_SWITCH_OTHER_SENDER_REASON == (
        "another member's message is still waiting to be picked up - "
        "switch the model after it.")


def test_a_russian_tapper_gets_a_russian_refusal(mk_bot, run_async):
    """The Ready card's model switch refused in a Persian group: the card
    stays Persian, the pop-up reads in the tapper's own language (their
    DM's choice), rendered from the refusal's code."""
    from aipager.bot import new_flow
    preferences.set_ui_language(i18n_ml2.GROUP, "fa")
    preferences.set_ui_language(i18n_ml2.BOB, "ru")
    bot = i18n_ml2.make_bot(mk_bot)
    sess = i18n_ml2.add_session(bot, chat=i18n_ml2.GROUP)
    bot._switch_model_core = AsyncMock(return_value=session_ops.ModelSwitchOutcome(
        ok=False, reason="busy", label="x1", detail=mini.MODEL_SWITCH_BUSY_REASON))
    update, query = i18n_ml2.callback(f"{sess.name}:rdy_m1", chat=i18n_ml2.GROUP,
                                      user=i18n_ml2.BOB)
    run_async(new_flow.handle_callback(bot, update, query, sess.name, "rdy_m1"))
    toast = query.answer.await_args.args[0]
    assert CYRILLIC.search(toast) and toast != mini.MODEL_SWITCH_BUSY_REASON
    assert toast == i18n.get("ru").text(session_ops.MODEL_REFUSAL_MSGS["busy"])
    card = bot._app.bot.edit_message_text.await_args.kwargs["text"]
    assert PERSIAN.search(card)
