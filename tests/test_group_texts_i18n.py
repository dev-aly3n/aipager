"""Multilingual phase 3b: the group texts (the /help Talk line, "Post as
yourself", the live-reload "Dropped" reply) in the chat's language, with
English unchanged."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager import i18n, preferences
from aipager.bot import group_intake, live_reload
from aipager.i18n import Command, plurals
from aipager.scope import Member, Scope

GROUP = -1001
BOB = Command("@bob")
RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"


def test_dropped_english_equals_the_constants():
    en = i18n.get("en")
    assert live_reload.dropped_reply(en, 1, BOB) == \
        "Dropped: @bob is no longer allowed to send here."
    assert live_reload.dropped_reply(en, 3, BOB) == \
        "Dropped 3 messages: @bob is no longer allowed to send here."
    assert live_reload.dropped_reply(en, 1, None) == \
        "Dropped: this sender is no longer allowed to send here."
    assert live_reload.dropped_reply(en, 3, None) == \
        "Dropped 3 messages: this sender is no longer allowed to send here."
    assert live_reload.DROPPED_REPLY.format(who="@bob") == live_reload.dropped_reply(en, 1, BOB)
    assert live_reload.DROPPED_REPLY_MANY.format(n=3, who="@bob") == \
        live_reload.dropped_reply(en, 3, BOB)


def test_dropped_in_persian_and_russian():
    fa = i18n.get("fa")
    assert live_reload.dropped_reply(fa, 3, BOB) == (
        f"{RLM}۳ پیام به Claude ارسال نشد: {FSI}@bob{PDI} دیگر اجازه ندارد اینجا پیام بفرستد.{RLM}")
    ru = i18n.get("ru")
    assert live_reload.dropped_reply(ru, 1, None) == \
        "Не отправлено Claude: этот человек больше не может писать здесь."
    words = {"one": "сообщение не отправлено", "few": "сообщения не отправлены",
             "many": "сообщений не отправлено"}
    for n in (2, 5, 11, 12, 21, 22, 25):
        text = live_reload.dropped_reply(ru, n, BOB)
        assert text.startswith(f"{n} {words[plurals.category('ru', n)]} Claude: "), text


def test_dropped_reply_is_in_the_chats_language(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(GROUP, "ru")
    monkeypatch.setattr(live_reload, "can_prompt", lambda *a, **k: False)
    scopes = [Scope(chat_id=GROUP, kind="group", label="team",
                    members=(Member(id=2, label="bob", role="user"),))]
    bot = mk_bot()
    bot._mark_not_delivered = AsyncMock()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9))
    sess = bot.registry.get_or_create("claude-x1")
    sess.label, sess.scope_chat_id, sess.scope_kind = "x1", GROUP, "group"
    sess.pending_queue = [("a", 101, None, None, 2), ("b", 102, None, None, 2)]
    run_async(live_reload._drop_held(bot, sess, GROUP, scopes, scopes, None))
    assert bot._app.bot.send_message.await_args.kwargs["text"] == \
        "2 сообщения не отправлены Claude: @bob больше не может писать здесь."


def test_post_as_yourself_in_the_chats_language(mk_bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    bot = mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="team",
                               members=(Member(id=555, label="aly", role="owner"),))])
    msg = MagicMock(chat_id=GROUP)
    msg.chat = MagicMock(id=GROUP)
    msg.reply_text = AsyncMock(return_value=MagicMock(message_id=3))
    run_async(group_intake._answer_anonymous_sender(bot, msg))
    assert msg.reply_text.await_args.args[0] == \
        f"{RLM}برای استفاده از ربات، با نام خودتان پیام بفرستید.{RLM}"


def _bot(username):
    bot = MagicMock()
    bot._app.bot.username = username
    return bot


def test_the_help_talk_line_both_ways():
    ru = i18n.get("ru")
    assert group_intake.group_help_talk_line(_bot("aipager_bot"), tr=ru) == (
        "<b>Общение:</b> ответь на сообщение сессии или упомяни @aipager_bot · "
        "/&lt;имя&gt; сообщение")
    assert group_intake.group_help_talk_line(_bot(None)) == (
        "<b>Talk:</b> reply to a session's message, or mention the bot · "
        "/&lt;label&gt; message")
    assert group_intake.group_help_talk_line(_bot("a&b")) == (
        "<b>Talk:</b> reply to a session's message, or mention @a&amp;b · "
        "/&lt;label&gt; message")


def test_the_english_talk_hint_keeps_the_label_as_given():
    """Callers without a tr pass the label already escaped: it is used as
    given, never escaped twice."""
    assert group_intake.talk_hint(_bot("x_bot"), GROUP, "a&amp;b", dm="") == (
        "Reply to a message from a&amp;b (or mention @x_bot) to talk to it.")
    assert group_intake.talk_hint(_bot(None), GROUP, "a&amp;b", dm="") == (
        "Reply to a message from a&amp;b (or mention the bot) to talk to it.")
    assert group_intake.talk_hint(_bot(None), 555, "a", dm="DM line") == "DM line"
