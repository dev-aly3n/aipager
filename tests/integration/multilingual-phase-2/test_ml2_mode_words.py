"""/mode takes the mode's word in Persian and Russian next to ask and auto."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import i18n
from aipager.bot import kbwords
from aipager.bot.mode_labels import WORD_ASK, WORD_AUTO
from tests.i18n_ml2 import DM, add_session, message



def _mode(bot, run_async, text):
    run_async(bot._handle_mode_cmd(message(text, chat=DM), MagicMock()))


def test_mode_words_switch(ml2_bot, run_async):
    sess = add_session(ml2_bot)
    ml2_bot.registry.last_active_session = sess.name
    ml2_bot._perms_flow = AsyncMock()
    fa_auto = kbwords.mode_word(i18n.get("fa").text(WORD_AUTO))
    ru_auto = kbwords.mode_word(i18n.get("ru").text(WORD_AUTO))
    ru_ask = kbwords.mode_word(i18n.get("ru").text(WORD_ASK))
    for text, want in ((f"/mode x1 {fa_auto}", True), (f"/mode {ru_auto.upper()}", True),
                       ("/mode x1 auto", True), (f"/mode x1 {ru_ask}", False)):
        sess.skip_perms = not want
        ml2_bot._perms_flow.reset_mock()
        _mode(ml2_bot, run_async, text)
        assert ml2_bot._perms_flow.await_args.args[1] is want, text
