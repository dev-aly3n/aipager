"""Group intake (roadmap 8.84, 8.85): in a group only messages addressed to
the bot count, and they are read right.

Telegram delivers every group message to a bot that is a group admin (the
pinned status bar needs admin rights), whatever its privacy setting. The
gate in group -2 lets a group message through only when it is a command
for this bot, a reply to one of its messages, a mention of it, or a tap on
a keyboard button aipager puts there. The text router strips the bot's own
mention, and a ``/<label>@thisbot`` token acts on ``<label>``.

Groups cannot be tested live, so these tests are the proof. Every private
chat path keeps its wording and behaviour byte for byte (the DM parity
cases below).
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import (
    Chat,
    Document,
    Message,
    MessageEntity,
    PhotoSize,
    Update,
    User,
    Voice,
)
from telegram.ext import ApplicationHandlerStop, TypeHandler

from aipager.bot import group_intake, handlers, new_flow, session_parity
from aipager.config import (
    APP_BUTTON,
    BACK_BUTTON,
    COMMANDS_BUTTON,
    MODELS_BUTTON,
    QUICK_COMMANDS,
    QUICK_TEMPLATES,
    TEMPLATES_BUTTON,
)
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

G = -1001234
DM = 11
ALICE, BOB = 11, 22
BOT_ID, BOT_NAME = 4242, "aipagerbot"
OTHER_BOT_ID = 999
X1, X2 = "claude-x1__g1001234", "claude-x2__g1001234"
DATE = datetime(2026, 10, 4, tzinfo=timezone.utc)


# ---- building real Telegram objects -----------------------------------------

def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _mention(text: str, word: str, *, nth: int = 0) -> MessageEntity:
    """A ``mention`` entity for the ``nth`` occurrence of ``word`` in
    ``text``, with its offset in UTF-16 code units as Telegram sends it."""
    i = -1
    for _ in range(nth + 1):
        i = text.index(word, i + 1)
    return MessageEntity(MessageEntity.MENTION, _u16(text[:i]), _u16(word))


def _text_mention(text: str, word: str, user_id: int) -> MessageEntity:
    i = text.index(word)
    return MessageEntity(MessageEntity.TEXT_MENTION, _u16(text[:i]), _u16(word),
                         user=User(user_id, word, user_id in (BOT_ID, OTHER_BOT_ID)))


def _msg(text=None, *, chat_id=G, chat_type="supergroup", entities=(), caption=None,
         caption_entities=(), reply_from=None, voice=False, photo=False,
         document=False, media_group_id=None, mid=1, **extra) -> Message:
    chat = Chat(chat_id, chat_type)
    reply = None
    if reply_from is not None:
        reply = Message(5, DATE, chat, from_user=reply_from, text="earlier")
    return Message(
        mid, DATE, chat, from_user=User(ALICE, "alice", False), text=text,
        entities=tuple(entities), caption=caption,
        caption_entities=tuple(caption_entities), reply_to_message=reply,
        voice=Voice("v", "vu", 3) if voice else None,
        photo=(PhotoSize("p", "pu", 10, 10),) if photo else None,
        document=Document("d", "du") if document else None,
        media_group_id=media_group_id, **extra)


def _the_bot() -> User:
    return User(BOT_ID, "aipager", True, username=BOT_NAME)


def _identity(bot, *, username=BOT_NAME, bot_id=BOT_ID):
    bot._app.bot.username = username
    bot._app.bot.id = bot_id
    return bot


def _passes(bot, run_async, update) -> bool:
    try:
        run_async(group_intake.intake_gate(bot, update))
    except ApplicationHandlerStop:
        return False
    return True


@pytest.fixture
def gate_bot(mk_bot):
    """A bot that knows its own name and id, with live sessions x1, x2 in
    the group."""
    r = SessionRegistry()
    for name, label in ((X1, "x1"), (X2, "x2")):
        s = TrackedSession(name=name, label=label, status=Status.IDLE)
        s.scope_chat_id = G
        r._sessions[name] = s
    bot = mk_bot(r, scopes=[Scope(chat_id=G, kind="group", label="team", members=(
        Member(id=ALICE, label="alice", role="owner"),
        Member(id=BOB, label="bob", role="user")))])
    return _identity(bot)


# ---- the gate: what a group lets through -------------------------------------

@pytest.mark.parametrize("message", [
    pytest.param(lambda: _msg("lunch?"), id="text"),
    pytest.param(lambda: _msg(photo=True), id="photo"),
    pytest.param(lambda: _msg(photo=True, caption="look at this"), id="photo-caption"),
    pytest.param(lambda: _msg(document=True), id="document"),
    pytest.param(lambda: _msg(voice=True), id="voice"),
    pytest.param(lambda: _msg("x9"), id="not-a-session-label"),
    pytest.param(lambda: _msg("Status"), id="keyboard-word-other-case"),
    pytest.param(lambda: _msg("ok", reply_from=User(BOB, "bob", False)),
                 id="reply-to-a-member"),
    pytest.param(lambda: _msg("ok", reply_from=User(OTHER_BOT_ID, "ci", True)),
                 id="reply-to-another-bot"),
    pytest.param(lambda: _msg("@bob look", entities=[_mention("@bob look", "@bob")]),
                 id="another-users-mention"),
    pytest.param(lambda: _msg("@aipagerbot_x hi",
                              entities=[_mention("@aipagerbot_x hi", "@aipagerbot_x")]),
                 id="a-longer-name-mentioned"),
    pytest.param(lambda: _msg("hey bob", entities=[_text_mention("hey bob", "bob", BOB)]),
                 id="text-mention-of-a-member"),
    pytest.param(lambda: _msg("/x1@otherbot"), id="command-for-another-bot"),
    pytest.param(lambda: _msg("/x1@otherbot hi @aipagerbot", entities=[
        _mention("/x1@otherbot hi @aipagerbot", "@aipagerbot")]),
                 id="another-bots-command-mentioning-us"),
    pytest.param(lambda: _msg(photo=True, caption="/x1@otherbot look"),
                 id="caption-command-for-another-bot"),
])
def test_group_chatter_is_stopped(gate_bot, run_async, message):
    assert not _passes(gate_bot, run_async, Update(1, message=message()))


@pytest.mark.parametrize("message", [
    pytest.param(lambda: _msg("fix it", reply_from=_the_bot()), id="reply-text"),
    pytest.param(lambda: _msg(voice=True, reply_from=_the_bot()), id="reply-voice"),
    pytest.param(lambda: _msg(photo=True, reply_from=_the_bot()), id="reply-photo"),
    pytest.param(lambda: _msg("@aipagerbot fix the tests", entities=[
        _mention("@aipagerbot fix the tests", "@aipagerbot")]), id="mention-start"),
    pytest.param(lambda: _msg("please @AipagerBot fix it", entities=[
        _mention("please @AipagerBot fix it", "@AipagerBot")]), id="mention-middle-case"),
    pytest.param(lambda: _msg("🙂🙂 @aipagerbot hi", entities=[
        _mention("🙂🙂 @aipagerbot hi", "@aipagerbot")]), id="mention-after-emoji"),
    pytest.param(lambda: _msg("@bob and @aipagerbot", entities=[
        _mention("@bob and @aipagerbot", "@bob"),
        _mention("@bob and @aipagerbot", "@aipagerbot")]), id="second-mention"),
    pytest.param(lambda: _msg(photo=True, caption="@aipagerbot what is this",
                              caption_entities=[_mention("@aipagerbot what is this",
                                                         "@aipagerbot")]),
                 id="mention-in-caption"),
    pytest.param(lambda: _msg("hi aipager", entities=[
        _text_mention("hi aipager", "aipager", BOT_ID)]), id="text-mention-of-bot"),
    pytest.param(lambda: _msg("/status"), id="command"),
    pytest.param(lambda: _msg("/x1 fix it"), id="label-command"),
    pytest.param(lambda: _msg("/x1@aipagerbot"), id="command-for-us"),
    pytest.param(lambda: _msg("/x1@AIPAGERBOT fix it"), id="command-for-us-case"),
    pytest.param(lambda: _msg(photo=True, caption="/x1@aipagerbot look"),
                 id="caption-command-for-us"),
    pytest.param(lambda: _msg("status"), id="keyboard-status"),
    pytest.param(lambda: _msg("stop"), id="keyboard-stop"),
    pytest.param(lambda: _msg("new"), id="keyboard-new"),
    pytest.param(lambda: _msg("x1"), id="keyboard-session-label"),
])
def test_group_messages_for_the_bot_pass(gate_bot, run_async, message):
    assert _passes(gate_bot, run_async, Update(1, message=message()))


@pytest.mark.parametrize("label", [
    TEMPLATES_BUTTON, COMMANDS_BUTTON, MODELS_BUTTON, BACK_BUTTON, APP_BUTTON,
    QUICK_TEMPLATES[0][0], QUICK_COMMANDS[0][0],
])
def test_keyboard_buttons_pass(gate_bot, run_async, label):
    assert _passes(gate_bot, run_async, Update(1, message=_msg(label)))


def test_model_buttons_pass(gate_bot, run_async):
    gate_bot._model_map = {"🧠 Big": "/model big"}
    assert _passes(gate_bot, run_async, Update(1, message=_msg("🧠 Big")))


def test_an_ended_sessions_label_is_not_a_button(gate_bot, run_async):
    gate_bot.registry._sessions[X2].status = Status.GONE
    assert not _passes(gate_bot, run_async, Update(1, message=_msg("x2")))
    assert _passes(gate_bot, run_async, Update(1, message=_msg("x1")))


def test_a_basic_group_is_gated_too(gate_bot, run_async):
    assert not _passes(gate_bot, run_async,
                       Update(1, message=_msg("lunch?", chat_type="group")))


@pytest.mark.parametrize("extra", [
    pytest.param({"new_chat_members": (User(BOB, "bob", False),)}, id="member-joined"),
    pytest.param({"migrate_to_chat_id": -100999}, id="migrated"),
    pytest.param({"pinned_message": None, "sticker": None, "left_chat_member":
                  User(BOB, "bob", False)}, id="member-left"),
])
def test_service_messages_pass(gate_bot, run_async, extra):
    assert _passes(gate_bot, run_async, Update(1, message=_msg(**extra)))


def test_edited_messages_are_ignored_in_groups_and_dms(gate_bot, run_async):
    for m in (_msg("/x1 fix it", reply_from=_the_bot()),
              _msg("/x1 fix it", chat_id=DM, chat_type="private")):
        assert not _passes(gate_bot, run_async, Update(1, edited_message=m))


@pytest.mark.parametrize("message", [
    pytest.param(lambda: _msg("lunch?", chat_id=DM, chat_type="private"), id="text"),
    pytest.param(lambda: _msg(photo=True, chat_id=DM, chat_type="private"), id="photo"),
    pytest.param(lambda: _msg(voice=True, chat_id=DM, chat_type="private"), id="voice"),
    pytest.param(lambda: _msg("/x1@otherbot", chat_id=DM, chat_type="private"),
                 id="other-bots-command"),
])
def test_private_chats_are_untouched(gate_bot, run_async, message):
    assert _passes(gate_bot, run_async, Update(1, message=message()))


def test_updates_without_a_message_pass(gate_bot, run_async):
    assert _passes(gate_bot, run_async, Update(1))


def test_a_failure_while_judging_stops_a_group_message(gate_bot, run_async, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("broken")
    monkeypatch.setattr(group_intake, "_addressed_to_bot", boom)
    assert not _passes(gate_bot, run_async,
                       Update(1, message=_msg("/x1 hi", reply_from=_the_bot())))
    # A private chat is never judged, so nothing there can fail.
    assert _passes(gate_bot, run_async,
                   Update(1, message=_msg("hi", chat_id=DM, chat_type="private")))


def test_album_items_after_an_admitted_one_pass(gate_bot, run_async):
    """Telegram sends an album one item at a time, with the caption (and
    so the mention) on one of them: the rest of that album follows it."""
    first = _msg(photo=True, caption="@aipagerbot compare", media_group_id="a1", mid=1,
                 caption_entities=[_mention("@aipagerbot compare", "@aipagerbot")])
    assert _passes(gate_bot, run_async, Update(1, message=first))
    assert _passes(gate_bot, run_async,
                   Update(2, message=_msg(photo=True, media_group_id="a1", mid=2)))
    # Another album, nobody addressed: stopped.
    assert not _passes(gate_bot, run_async,
                       Update(3, message=_msg(photo=True, media_group_id="a2", mid=3)))


def test_an_admitted_album_is_forgotten_after_a_while(gate_bot, run_async):
    first = _msg(photo=True, reply_from=_the_bot(), media_group_id="a1", mid=1)
    assert _passes(gate_bot, run_async, Update(1, message=first))
    gate_bot._admitted_albums = {
        k: v - group_intake._ALBUM_ADMIT_SECONDS - 1
        for k, v in gate_bot._admitted_albums.items()}
    assert not _passes(gate_bot, run_async,
                       Update(2, message=_msg(photo=True, media_group_id="a1", mid=2)))


# ---- the gate when the bot does not know who it is yet ------------------------

def test_unknown_identity_fails_open_only_for_commands_and_replies_to_a_bot(
        gate_bot, run_async, monkeypatch, caplog):
    gate_bot._app.bot = MagicMock()          # not initialized: no name, no id
    monkeypatch.setattr(group_intake, "_unknown_identity_logged", False)
    caplog.set_level(logging.WARNING, logger="aipager.bot.group_intake")
    ok = functools.partial(_passes, gate_bot, run_async)
    assert ok(Update(1, message=_msg("/x1@somebot hi")))
    assert ok(Update(1, message=_msg("/status")))
    assert ok(Update(1, message=_msg("fix it", reply_from=User(OTHER_BOT_ID, "b", True))))
    assert ok(Update(1, message=_msg("status")))           # a keyboard tap
    assert not ok(Update(1, message=_msg("fix it", reply_from=User(BOB, "bob", False))))
    assert not ok(Update(1, message=_msg("@aipagerbot hi", entities=[
        _mention("@aipagerbot hi", "@aipagerbot")])))
    assert not ok(Update(1, message=_msg("lunch?")))
    logged = [r for r in caplog.records if "unknown" in r.getMessage()]
    assert len(logged) == 1


# ---- registration ----------------------------------------------------------------

def test_the_gate_runs_before_everything(mk_bot, run_async, monkeypatch):
    """Registered by the real ``start()`` as a TypeHandler for every Update in
    group -2, before the Name card's close (group -1) and all the rest."""
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "-1001")
    bot = mk_bot()
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda h, *a, **k: registered.append(
        (h, k.get("group", a[0] if a else 0))))
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater.start_polling = AsyncMock()
    bot._make_builder = MagicMock(
        return_value=MagicMock(build=MagicMock(return_value=stub)))
    bot._update_bot_commands = AsyncMock()
    run_async(bot.start())

    gates = [(h, g) for h, g in registered
             if isinstance(h, TypeHandler)
             and getattr(h.callback, "func", None) is group_intake.intake_gate]
    assert len(gates) == 1
    handler, group = gates[0]
    assert handler.type is Update and handler.callback.args == (bot,)
    assert group == -2
    assert all(g > -2 for h, g in registered if h is not handler)


# ---- reading a message: mention strip ----------------------------------------------

def _as_mock_update(mk_update, real: Message, *, user=ALICE, chat_id=G, reply_to=None,
                    message_id=900):
    """A handler-ready mock update carrying a real message's text and
    entities (so ``parse_entities`` is Telegram's own)."""
    u = mk_update(real.text, user_id=user, chat_id=chat_id, message_id=message_id)
    u.message.entities = real.entities
    u.message.parse_entities = real.parse_entities
    u.message.caption = None
    u.effective_user.username = f"u{user}"
    u.effective_user.first_name = "U"
    u.effective_user.last_name = ""
    u.effective_message = u.message
    u.effective_chat.type = "supergroup" if chat_id < 0 else "private"
    if reply_to is not None:
        rt = MagicMock()
        rt.message_id = reply_to
        rt.text = "answer"
        rt.caption = None
        rt.from_user = _the_bot()
        u.message.reply_to_message = rt
    return u


def _mention_update(mk_update, text, *words, **kw):
    ents = [_mention(text, w) for w in words]
    return _as_mock_update(mk_update, _msg(text, entities=ents), **kw)


@pytest.fixture
def hbot(gate_bot, monkeypatch):
    bot = gate_bot
    from aipager.policy import load_policy
    bot.policy = load_policy()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(side_effect=lambda n: n in bot.registry._sessions))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot.refresh_pinned = AsyncMock()
    bot.registry.last_active_session = X1
    return bot


def _injected(bot):
    assert bot._inject_prompt.await_args is not None, "nothing was injected"
    return bot._inject_prompt.await_args.args[0].label, bot._inject_prompt.await_args.args[1]


def _replies(update):
    return [c.args[0] for c in update.message.reply_text.await_args_list]


def test_a_mention_never_reaches_claude(hbot, mk_update, run_async):
    run_async(hbot._handle_message(
        _mention_update(mk_update, "@aipagerbot fix the tests", "@aipagerbot"), MagicMock()))
    assert _injected(hbot) == ("x1", "fix the tests")


def test_a_mention_in_the_middle_and_after_an_emoji(hbot, mk_update, run_async):
    run_async(hbot._handle_message(_mention_update(
        mk_update, "🙂 please @AipagerBot fix 👍 it", "@AipagerBot"), MagicMock()))
    assert _injected(hbot) == ("x1", "🙂 please fix 👍 it")
    run_async(hbot._handle_message(_mention_update(
        mk_update, "fix the tests @aipagerbot", "@aipagerbot"), MagicMock()))
    assert _injected(hbot) == ("x1", "fix the tests")


def test_other_peoples_mentions_stay_in_the_prompt(hbot, mk_update, run_async):
    text = "@aipagerbot ask @bob about @aipagerbot_x"
    run_async(hbot._handle_message(_mention_update(
        mk_update, text, "@aipagerbot", "@bob", "@aipagerbot_x"), MagicMock()))
    assert _injected(hbot) == ("x1", "ask @bob about @aipagerbot_x")


def test_a_text_mention_of_the_bot_is_stripped_and_of_a_member_kept(
        hbot, mk_update, run_async):
    text = "aipager tell bob hi"
    real = _msg(text, entities=[_text_mention(text, "aipager", BOT_ID),
                                _text_mention(text, "bob", BOB)])
    run_async(hbot._handle_message(_as_mock_update(mk_update, real), MagicMock()))
    assert _injected(hbot) == ("x1", "tell bob hi")


def test_a_bare_mention_answers_with_the_home_screen(hbot, mk_update, run_async):
    hbot._send_keyboard = AsyncMock()
    u = _mention_update(mk_update, "@aipagerbot", "@aipagerbot")
    run_async(hbot._handle_message(u, MagicMock()))
    hbot._inject_prompt.assert_not_awaited()
    sent = hbot._app.bot.send_message.await_args
    assert sent is not None, "no home screen was sent"
    text = sent.kwargs.get("text") or sent.args[1]
    assert "Reply to a message from <b>x1</b> (or mention @aipagerbot) to talk to it." in text
    rows = sent.kwargs["reply_markup"].inline_keyboard
    assert [b.callback_data for b in rows[0]][0] == "_:nw:open"


def test_mention_plus_status_runs_status(hbot, mk_update, run_async):
    hbot._handle_status = AsyncMock()
    run_async(hbot._handle_message(
        _mention_update(mk_update, "@aipagerbot status", "@aipagerbot"), MagicMock()))
    hbot._handle_status.assert_awaited_once()
    hbot._inject_prompt.assert_not_awaited()


def test_mention_plus_label_switches(hbot, mk_update, run_async):
    u = _mention_update(mk_update, "@aipagerbot x2", "@aipagerbot")
    run_async(hbot._handle_message(u, MagicMock()))
    hbot._inject_prompt.assert_not_awaited()
    assert hbot.registry.target_for(G).label == "x2"


@pytest.mark.parametrize("word,expected", [
    ("stop", "Nothing is running."), ("kill", "Which session to end?")])
def test_mention_plus_stop_or_kill_reads_the_bare_word(hbot, mk_update, run_async,
                                                       word, expected):
    """``/stop`` and ``/kill`` read their argument from the message: the
    mention must not become one ("No session named stop")."""
    u = _mention_update(mk_update, f"@aipagerbot {word}", "@aipagerbot")
    run_async(hbot._handle_message(u, MagicMock()))
    hbot._inject_prompt.assert_not_awaited()
    assert _replies(u) == [expected]


def test_mention_plus_new_opens_the_name_card(hbot, mk_update, run_async, monkeypatch):
    opened = AsyncMock()
    monkeypatch.setattr(new_flow, "start_wizard", opened)
    created = AsyncMock()
    monkeypatch.setattr(new_flow, "create_from_text", created)
    run_async(hbot._handle_message(
        _mention_update(mk_update, "@aipagerbot new", "@aipagerbot"), MagicMock()))
    opened.assert_awaited_once()
    created.assert_not_awaited()


def test_a_mentioned_name_answers_the_name_card(hbot, mk_update, run_async, monkeypatch):
    new_flow._pending_store(hbot)[G] = {
        "step": "name", "user_id": ALICE, "msg_id": 7000, "path_options": [],
        "new_folder_parent": None, "last_active": new_flow._now(),
        "skip_perms": True, "can_auto": True, "model": None, "model_label": None,
        "cwd": None}
    created = AsyncMock()
    monkeypatch.setattr(new_flow, "create_from_text", created)
    run_async(hbot._handle_message(
        _mention_update(mk_update, "@aipagerbot x3 fix it", "@aipagerbot"), MagicMock()))
    created.assert_awaited_once()
    assert created.await_args.args[2] == "x3 fix it"
    hbot._inject_prompt.assert_not_awaited()


def test_a_mentioned_name_answers_a_rename(hbot, mk_update, run_async):
    session_parity._start_rename_capture(hbot, G, hbot.registry._sessions[X1], ALICE)
    hbot._rename_session_core = AsyncMock(return_value=SimpleNamespace(
        changed=True, previous_label="x1", new_label="api"))
    run_async(hbot._handle_message(
        _mention_update(mk_update, "@aipagerbot api", "@aipagerbot"), MagicMock()))
    assert hbot._rename_session_core.await_args.args[1] == "api"


def test_dm_text_is_sent_exactly_as_typed(hbot, mk_update, run_async):
    """A private chat's text is never touched (no mention of the bot in it)."""
    hbot.scopes = None
    hbot._authorize = AsyncMock(return_value=True)
    dm = _sess(DM, "d1")
    hbot.registry._sessions[dm.name] = dm
    hbot.registry.last_active_session = dm.name
    text = "  fix @bob's tests  "
    real = _msg(text, chat_id=DM, chat_type="private", entities=[_mention(text, "@bob")])
    u = _as_mock_update(mk_update, real, chat_id=DM)
    run_async(hbot._handle_message(u, MagicMock()))
    assert _injected(hbot) == ("d1", "fix @bob's tests")


def test_own_text_without_a_mention_is_the_stripped_text(hbot, mk_update):
    u = mk_update("  /stop x1  ")
    assert hbot._own_text(u) == "/stop x1"


# ---- 8.85: /<label>@thisbot -----------------------------------------------------------

def test_label_command_with_our_name_switches(hbot, mk_update, run_async):
    hbot.registry.last_active_session = X2
    u = _as_mock_update(mk_update, _msg("/x1@aipagerbot"))
    run_async(hbot._handle_message(u, MagicMock()))
    assert hbot.registry.target_for(G).label == "x1"
    assert not any("Unknown" in r for r in _replies(u))


def test_label_command_with_our_name_in_any_case_sends(hbot, mk_update, run_async):
    run_async(hbot._handle_message(
        _as_mock_update(mk_update, _msg("/x2@AipagerBot fix it")), MagicMock()))
    assert _injected(hbot) == ("x2", "fix it")


def test_label_command_with_our_name_stops(hbot, mk_update, run_async):
    hbot._stop_by_label = AsyncMock()
    run_async(hbot._handle_message(
        _as_mock_update(mk_update, _msg("/x1@aipagerbot stop")), MagicMock()))
    hbot._stop_by_label.assert_awaited_once()
    assert hbot._stop_by_label.await_args.args[1] == "x1"


def test_another_bots_name_is_not_dropped(hbot, mk_update, run_async):
    """Only our own name goes: in a DM (no gate) ``/x1@otherbot`` stays an
    unknown session, not a send to x1."""
    u = _as_mock_update(mk_update, _msg("/x1@otherbot fix it"))
    run_async(hbot._handle_message(u, MagicMock()))
    hbot._inject_prompt.assert_not_awaited()
    assert any("x1@otherbot" in r for r in _replies(u))


def test_caption_label_with_our_name():
    split = handlers._split_caption_target
    assert split("/x1@aipagerbot compare", BOT_NAME) == ("x1", "compare")
    assert split("/x1@AIPAGERBOT", BOT_NAME) == ("x1", "")
    assert split("/x1@otherbot compare", BOT_NAME) == ("x1@otherbot", "compare")
    assert split("/x1@aipagerbot compare") == ("x1@aipagerbot", "compare")
    assert split("/@aipagerbot", BOT_NAME) == ("/@aipagerbot", "")


def test_drop_own_suffix():
    assert group_intake.drop_own_suffix("x1@aipagerbot", BOT_NAME) == "x1"
    assert group_intake.drop_own_suffix("x1@other", BOT_NAME) == "x1@other"
    assert group_intake.drop_own_suffix("x1@aipagerbot", "") == "x1@aipagerbot"
    assert group_intake.drop_own_suffix("x1", BOT_NAME) == "x1"


# ---- file captions -------------------------------------------------------------------

def _file_bot(mk_bot, monkeypatch, tmp_path):
    from tests.test_bot_handlers_file_retry_album import _wire
    bot = _identity(mk_bot())
    sess, sent = _wire(bot, monkeypatch, tmp_path)
    x1 = TrackedSession(name="claude-x1", label="x1", status=Status.IDLE)
    bot.registry._sessions[x1.name] = x1
    bot.refresh_pinned = AsyncMock()
    return bot, sent


def _captioned(mk_update, downloads, caption, *words, **kw):
    from tests.test_bot_handlers_file_retry_album import _photo_update
    update, _photo, _tg = _photo_update(mk_update, downloads, caption=caption, **kw)
    real = _msg(photo=True, caption=caption,
                caption_entities=[_mention(caption, w) for w in words])
    update.message.parse_caption_entities = real.parse_caption_entities
    return update


def test_a_captions_mention_never_reaches_claude(mk_bot, mk_update, run_async,
                                                 monkeypatch, tmp_path):
    from tests.test_bot_handlers_file_retry_album import _Downloads
    bot, sent = _file_bot(mk_bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    u = _captioned(mk_update, downloads, "@aipagerbot what is this", "@aipagerbot")
    run_async(bot._handle_file(u, MagicMock()))
    assert sent.await_args.args[1] == f"what is this {downloads.written[0]}"


def test_a_caption_of_only_a_mention_is_no_caption(mk_bot, mk_update, run_async,
                                                   monkeypatch, tmp_path):
    from tests.test_bot_handlers_file_retry_album import _Downloads
    bot, sent = _file_bot(mk_bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    u = _captioned(mk_update, downloads, "@aipagerbot ", "@aipagerbot")
    run_async(bot._handle_file(u, MagicMock()))
    assert sent.await_args.args[1] == f"check this: {downloads.written[0]}"


def test_a_caption_label_with_our_name_routes(mk_bot, mk_update, run_async,
                                              monkeypatch, tmp_path):
    from tests.test_bot_handlers_file_retry_album import _Downloads
    bot, sent = _file_bot(mk_bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    u = _captioned(mk_update, downloads, "/x1@aipagerbot compare")
    run_async(bot._handle_file(u, MagicMock()))
    assert sent.await_args.args[0] == "claude-x1"
    assert sent.await_args.args[1] == f"compare {downloads.written[0]}"


def test_an_albums_caption_mention_is_stripped(mk_bot, mk_update, run_async,
                                               monkeypatch, tmp_path):
    from tests.test_bot_handlers_file_retry_album import CHAT_ID, _Downloads
    bot, sent = _file_bot(mk_bot, monkeypatch, tmp_path)
    monkeypatch.setattr(handlers, "_ALBUM_SETTLE_SECONDS", 0)
    downloads = _Downloads()
    u1 = _captioned(mk_update, downloads, "@aipagerbot /x1 compare", "@aipagerbot",
                    message_id=101, media_group_id="g1")
    from tests.test_bot_handlers_file_retry_album import _photo_update
    u2, _p, _t = _photo_update(mk_update, downloads, message_id=102, media_group_id="g1")

    async def scenario():
        await bot._handle_file(u1, MagicMock())
        await bot._handle_file(u2, MagicMock())
        await bot._albums[(CHAT_ID, "g1")].settle_task
    run_async(scenario())
    assert sent.await_args.args[0] == "claude-x1"
    assert sent.await_args.args[1] == "compare " + " ".join(downloads.written)


def test_a_dm_caption_is_untouched(mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    from tests.test_bot_handlers_file_retry_album import _Downloads
    bot, sent = _file_bot(mk_bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    u = _captioned(mk_update, downloads, " look at @bob ", "@bob")
    run_async(bot._handle_file(u, MagicMock()))
    assert sent.await_args.args[1] == f" look at @bob  {downloads.written[0]}"


# ---- wording: groups say reply, DMs keep theirs --------------------------------------

def test_talk_hint():
    bot = _identity(MagicMock())
    assert group_intake.talk_hint(bot, DM, "x1", dm="dm line") == "dm line"
    assert group_intake.talk_hint(bot, G, "x1", dm="dm line") == (
        "Reply to a message from x1 (or mention @aipagerbot) to talk to it.")
    bot._app.bot = MagicMock()
    assert group_intake.talk_hint(bot, G, "x1", dm="dm line") == (
        "Reply to a message from x1 (or mention the bot) to talk to it.")


def _sess(chat_id, label="x1"):
    s = TrackedSession(name=f"claude-{label}", label=label, status=Status.IDLE)
    s.scope_chat_id = chat_id
    return s


def test_ready_card_wording(mk_bot):
    bot = _identity(mk_bot())
    bot._app_button_row = MagicMock(return_value=[])
    dm_text, _ = new_flow.render_ready(bot, None, _sess(DM))
    assert "✍️ Just send a message, it goes to x1.\n" in dm_text
    g_text, _ = new_flow.render_ready(bot, None, _sess(G))
    assert "Just send a message" not in g_text
    assert "Reply to a message from x1 (or mention @aipagerbot) to talk to it." in g_text


def test_switch_reply_wording(mk_bot):
    bot = _identity(mk_bot())
    dm_text, _ = bot._render_switch_reply(DM, _sess(DM))
    assert dm_text.splitlines()[1] == "Send a message and it goes to x1."
    g_text, _ = bot._render_switch_reply(G, _sess(G))
    assert g_text.splitlines()[1] == (
        "Reply to a message from x1 (or mention @aipagerbot) to talk to it.")


def test_name_card_wording():
    pending = {"skip_perms": True, "can_auto": True, "model_label": None, "cwd": "/w"}
    dm_text, _ = new_flow._render_name_card(dict(pending), chat_id=DM)
    assert "\nSend a name, and what to do first if you like:\n" in dm_text
    assert dm_text == new_flow._render_name_card(dict(pending))[0]
    g_text, _ = new_flow._render_name_card(dict(pending), chat_id=G)
    assert "Send a name" not in g_text
    assert "\nReply to this message with a name, and what to do first if you like:\n" in g_text


def test_name_card_is_sent_with_the_group_wording(hbot, mk_update, run_async):
    u = _as_mock_update(mk_update, _msg("/new"))
    u.message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=7000))
    run_async(new_flow.start_wizard(hbot, u))
    assert "Reply to this message with a name" in u.message.reply_text.await_args.args[0]


def test_rename_prompt_wording(mk_bot):
    bot = _identity(mk_bot())
    dm_text, _ = session_parity._start_rename(bot, DM, _sess(DM), ALICE)
    assert dm_text == "✏️ New name for [<b>x1</b>]? Send it as a message."
    g_text, _ = session_parity._start_rename(bot, G, _sess(G), ALICE)
    assert g_text == "✏️ New name for [<b>x1</b>]? Reply to this message with the new name."


def test_help_wording(hbot, mk_update, run_async):
    u = _as_mock_update(mk_update, _msg("/help"))
    run_async(hbot._handle_help_cmd(u, MagicMock()))
    g_text = u.message.reply_text.await_args.args[0]
    assert "just type" not in g_text
    assert ("<b>Talk:</b> reply to a session's message, or mention @aipagerbot"
            " · /&lt;label&gt; message\n") in g_text

    hbot.scopes = None
    hbot._authorize = AsyncMock(return_value=True)
    d = _as_mock_update(mk_update, _msg("/help", chat_id=DM, chat_type="private"),
                        chat_id=DM)
    run_async(hbot._handle_help_cmd(d, MagicMock()))
    assert d.message.reply_text.await_args.args[0] == handlers.HELP_TEXT
    assert ("<b>Talk:</b> just type (it goes to the ✍️ session) · reply to a message"
            " · /&lt;label&gt; message\n") in handlers.HELP_TEXT


def _start_text(bot, run_async, update):
    bot._send_keyboard = AsyncMock()
    bot._app.bot.send_message.reset_mock()
    run_async(bot._handle_start_cmd(update, MagicMock()))
    sent = bot._app.bot.send_message.await_args
    return sent.kwargs.get("text") or sent.args[1]


def test_start_wording(hbot, mk_update, run_async):
    g_text = _start_text(hbot, run_async, _as_mock_update(mk_update, _msg("/start")))
    assert "Messages go to" not in g_text
    assert "\n\nReply to a message from <b>x1</b> (or mention @aipagerbot) to talk to it.\n\n" \
        in g_text

    hbot.scopes = None
    hbot._authorize = AsyncMock(return_value=True)
    dm = _sess(DM)
    hbot.registry._sessions[dm.name] = dm
    hbot.registry.last_active_session = dm.name
    d = _as_mock_update(mk_update, _msg("/start", chat_id=DM, chat_type="private"),
                        chat_id=DM)
    d_text = _start_text(hbot, run_async, d)
    assert "\n\n✍️ Messages go to <b>x1</b>.\n\n" in d_text


def test_a_trailing_caption_mention_leaves_no_space(mk_bot, mk_update, run_async,
                                                    monkeypatch, tmp_path):
    from tests.test_bot_handlers_file_retry_album import _Downloads
    bot, sent = _file_bot(mk_bot, monkeypatch, tmp_path)
    downloads = _Downloads()
    u = _captioned(mk_update, downloads, "what is this @aipagerbot", "@aipagerbot")
    run_async(bot._handle_file(u, MagicMock()))
    assert sent.await_args.args[1] == f"what is this {downloads.written[0]}"


def test_name_card_back_from_an_option_keeps_the_group_wording(hbot, run_async):
    """The card is re-drawn in place after an option (mode, model, folder):
    it must keep saying "Reply to this message" in a group."""
    hbot._app.bot.edit_message_text = AsyncMock()
    pending = {"step": "opt_model", "user_id": ALICE, "msg_id": 7000,
               "skip_perms": True, "can_auto": True, "model_label": None, "cwd": "/w"}
    run_async(new_flow._goto_name(hbot, G, pending))
    edited = hbot._app.bot.edit_message_text.await_args
    text = edited.kwargs.get("text") or edited.args[0]
    assert "Reply to this message with a name" in text


# ---- review 1: the card's other text steps, the close, punctuation, DM home ----

def test_other_model_and_new_folder_steps_say_reply_in_a_group():
    for render, what in ((new_flow._model_custom_prompt_text, "the model name"),
                         (new_flow._path_newfolder_prompt_text, "the new folder's name")):
        assert f"\nType {what}" in render(chat_id=DM)
        assert render(chat_id=DM) == render()
        assert f"\nReply to this message with {what}" in render(chat_id=G)
        assert "Type" not in render(chat_id=G)
        assert f"\n\nType {what}." in render("bad", DM)
        assert f"\n\nReply to this message with {what}." in render("bad", G)
    assert new_flow._model_custom_prompt_text() == (
        "🧠 <b>Other model</b>\n\nType the model name (e.g. claude-opus-5).")
    assert new_flow._path_newfolder_prompt_text() == (
        "📁 <b>New folder</b>\n\nType the new folder's name.")


def _open_card(bot, step, chat_id=G, **extra):
    pending = {"step": step, "user_id": ALICE, "msg_id": 7000, "path_options": [],
               "new_folder_parent": "/w", "last_active": new_flow._now(),
               "skip_perms": True, "can_auto": True, "model": None, "model_label": None,
               "cwd": None, **extra}
    new_flow._pending_store(bot)[chat_id] = pending
    return pending


def _last_edit(bot):
    edited = bot._app.bot.edit_message_text.await_args
    assert edited is not None, "the card was not edited"
    return edited.kwargs.get("text") or edited.args[0]


@pytest.mark.parametrize("chat_id,ask", [
    (G, "Reply to this message with"), (DM, "Type")])
def test_new_folder_and_model_steps_are_sent_with_the_chats_wording(
        hbot, mk_update, run_async, monkeypatch, chat_id, ask):
    """Through the real token handlers and the steps' error paths."""
    hbot._app.bot.edit_message_text = AsyncMock()
    monkeypatch.setattr(new_flow.launch, "allowed_roots", lambda *a, **k: ["/w"])
    q = MagicMock()
    pending = _open_card(hbot, "opt_model", chat_id)
    run_async(new_flow._handle_model_token(hbot, q, chat_id, pending, "custom"))
    assert f"\n\n{ask} the model name (e.g. claude-opus-5)." in _last_edit(hbot)
    pending = _open_card(hbot, "opt_path", chat_id)
    run_async(new_flow._handle_path_token(hbot, q, chat_id, pending, "new"))
    assert f"\n\n{ask} the new folder's name." in _last_edit(hbot)

    # A bad answer keeps the step and says it again in the same words.
    _open_card(hbot, "opt_path_newfolder", chat_id)
    monkeypatch.setattr(new_flow.launch, "create_directory",
                        MagicMock(return_value=("", False, "bad name")))
    u = mk_update("../x", user_id=ALICE, chat_id=chat_id)
    assert run_async(new_flow.maybe_handle_text(hbot, u, None, "../x"))
    assert f"\n\n{ask} the new folder's name." in _last_edit(hbot)
    _open_card(hbot, "opt_model_custom", chat_id)
    monkeypatch.setattr(new_flow.launch, "validate_model",
                        MagicMock(return_value=("", "bad model")))
    assert run_async(new_flow.maybe_handle_text(hbot, u, None, "zzz"))
    assert f"\n\n{ask} the model name." in _last_edit(hbot)


def test_a_reply_to_the_card_answers_the_new_folder_step(hbot, mk_update, run_async,
                                                         monkeypatch):
    """In a group the folder name comes as a reply to the card: the gate
    lets it through (a reply to the bot) and the step takes it."""
    _open_card(hbot, "opt_path_newfolder")
    made = MagicMock(return_value=("/w/api", False, ""))
    monkeypatch.setattr(new_flow.launch, "create_directory", made)
    monkeypatch.setattr(new_flow.launch, "allowed_roots", lambda *a, **k: ["/w"])
    monkeypatch.setattr(new_flow.launch, "remember_created", lambda *a, **k: None)
    hbot._app.bot.edit_message_text = AsyncMock()
    real = _msg("api", reply_from=_the_bot())
    assert _passes(hbot, run_async, Update(1, message=real))
    run_async(hbot._handle_message(
        _as_mock_update(mk_update, _msg("api"), reply_to=7000), MagicMock()))
    assert made.call_args.args[1] == "api"
    hbot._inject_prompt.assert_not_awaited()


def test_a_mentioned_command_closes_the_card_like_the_bare_command(
        hbot, mk_update, run_async):
    """ "@bot /x1 fix it" with a Name card open: the card closes and x1 gets
    the prompt, as "/x1 fix it" does (it used to be read as a name)."""
    _open_card(hbot, "name")
    hbot._app.bot.edit_message_text = AsyncMock()
    u = _mention_update(mk_update, "@aipagerbot /x1 fix it", "@aipagerbot")
    run_async(new_flow.close_if_moved_on(hbot, u))
    assert G not in new_flow._pending_store(hbot)
    run_async(hbot._handle_message(u, MagicMock()))
    assert _injected(hbot) == ("x1", "fix it")


def test_a_comma_or_colon_after_the_mention_goes_with_it(hbot, mk_update, run_async):
    for text in ("@aipagerbot, fix it", "@aipagerbot: fix it"):
        hbot._inject_prompt.reset_mock()
        run_async(hbot._handle_message(
            _mention_update(mk_update, text, "@aipagerbot"), MagicMock()))
        assert _injected(hbot) == ("x1", "fix it")


def test_a_dm_of_only_the_bots_name_shows_the_home_screen(hbot, mk_update, run_async):
    hbot.scopes = None
    hbot._authorize = AsyncMock(return_value=True)
    hbot._send_keyboard = AsyncMock()
    dm = _sess(DM)
    hbot.registry._sessions[dm.name] = dm
    hbot.registry.last_active_session = dm.name
    real = _msg("@aipagerbot", chat_id=DM, chat_type="private",
                entities=[_mention("@aipagerbot", "@aipagerbot")])
    run_async(hbot._handle_message(_as_mock_update(mk_update, real, chat_id=DM),
                                   MagicMock()))
    hbot._inject_prompt.assert_not_awaited()
    sent = hbot._app.bot.send_message.await_args
    assert sent is not None, "no home screen was sent"
    assert "\n\n✍️ Messages go to <b>x1</b>.\n\n" in (sent.kwargs.get("text") or sent.args[1])


# ---- review 2: a command after the mention -------------------------------------

@pytest.mark.parametrize("command", ["/stop", "/status", "/kill", "/new x1", "/rename x1 api",
                                     "/STOP", "/stop@aipagerbot"])
def test_a_mentioned_aipager_command_is_never_typed_into_a_session(
        hbot, mk_update, run_async, command):
    u = _mention_update(mk_update, f"@aipagerbot {command}", "@aipagerbot")
    run_async(hbot._handle_message(u, MagicMock()))
    hbot._inject_prompt.assert_not_awaited()
    word = command[1:].split()[0].split("@")[0]
    assert _replies(u) == [f"Send /{word} on its own, without mentioning the bot."]


def test_a_mentioned_aipager_command_in_a_dm_too(hbot, mk_update, run_async):
    hbot.scopes = None
    hbot._authorize = AsyncMock(return_value=True)
    dm = _sess(DM)
    hbot.registry._sessions[dm.name] = dm
    hbot.registry.last_active_session = dm.name
    real = _msg("@aipagerbot /status", chat_id=DM, chat_type="private",
                entities=[_mention("@aipagerbot /status", "@aipagerbot")])
    u = _as_mock_update(mk_update, real, chat_id=DM)
    run_async(hbot._handle_message(u, MagicMock()))
    hbot._inject_prompt.assert_not_awaited()
    assert _replies(u) == ["Send /status on its own, without mentioning the bot."]


def test_a_mentioned_label_command_still_switches(hbot, mk_update, run_async):
    hbot.registry.last_active_session = X1
    run_async(hbot._handle_message(
        _mention_update(mk_update, "@aipagerbot /x2", "@aipagerbot"), MagicMock()))
    hbot._inject_prompt.assert_not_awaited()
    assert hbot.registry.target_for(G).label == "x2"


def test_a_slash_command_typed_without_a_mention_is_routed_as_before(
        hbot, mk_update, run_async):
    """Only the mention case changes: a `/compact`-style text that no
    command handler took still reaches the session (keyboard commands)."""
    run_async(hbot._handle_message(_as_mock_update(mk_update, _msg("/x1 /compact")),
                                   MagicMock()))
    assert _injected(hbot) == ("x1", "/compact")


def test_own_commands_match_the_registered_handlers(mk_bot, run_async, monkeypatch):
    from telegram.ext import CommandHandler
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "-1001")
    bot = mk_bot()
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda h, *a, **k: registered.append(h))
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater.start_polling = AsyncMock()
    bot._make_builder = MagicMock(
        return_value=MagicMock(build=MagicMock(return_value=stub)))
    bot._update_bot_commands = AsyncMock()
    run_async(bot.start())
    names = set()
    for h in registered:
        if isinstance(h, CommandHandler):
            names |= set(h.commands)
    assert names == group_intake.OWN_COMMANDS


def test_without_a_mention_a_command_text_is_routed_exactly_as_before(
        hbot, mk_update, run_async):
    """DM parity: only a mentioned command gets the "on its own" answer.
    (A command text reaches the text router only through a mention in real
    use; called directly, the router keeps its old reading.)"""
    hbot.scopes = None
    hbot._authorize = AsyncMock(return_value=True)
    u = _as_mock_update(mk_update, _msg("/stop x1", chat_id=DM, chat_type="private"),
                        chat_id=DM)
    run_async(hbot._handle_message(u, MagicMock()))
    assert not any("on its own" in r for r in _replies(u))
