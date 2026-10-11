"""A group in Persian with a member whose own chat is Russian: the group's
messages are Persian, that member's pop-ups Russian, and a marked Persian
keyboard tap is let through with or without its invisible mark."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from aipager import i18n, preferences
from aipager.bot import group_intake, kbwords
from tests.i18n_ml2 import BOB, GROUP, callback


PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")


def test_settings_in_a_persian_group_toast_in_russian(ml2_bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    update, query = callback("_:set:length:short", chat=GROUP, user=BOB)
    run_async(ml2_bot._handle_callback(update, MagicMock()))
    alert = query.answer.await_args.args[0]
    assert CYRILLIC.search(alert) and not PERSIAN.search(alert)
    update, query = callback("_:set", chat=GROUP, user=BOB)
    run_async(ml2_bot._handle_callback(update, MagicMock()))
    edited = query.edit_message_text.await_args
    text = edited.args[0] if edited.args else edited.kwargs["text"]
    assert PERSIAN.search(text) and not CYRILLIC.search(text)


def test_a_marked_persian_tap_is_addressed_with_or_without_its_mark(ml2_bot):
    preferences.set_ui_language(GROUP, "fa")
    word = i18n.get("fa").text(kbwords.KB_STATUS)
    for text in (group_intake.mark_label(word, GROUP),
                 group_intake.mark_label(word.replace("\u200f", ""), GROUP)):
        msg = MagicMock(text=text, chat_id=GROUP, reply_to_message=None, caption=None)
        msg.entities = ()
        msg.caption_entities = ()
        assert group_intake._addressed_to_bot(ml2_bot, msg), text
    # The same word typed without the marker is chatter.
    msg = MagicMock(text=word, chat_id=GROUP, reply_to_message=None, caption=None)
    msg.entities = ()
    msg.caption_entities = ()
    assert not group_intake._addressed_to_bot(ml2_bot, msg)
