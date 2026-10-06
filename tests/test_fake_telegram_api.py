"""The fake Bot API (tests/e2e/fake_telegram) behaves like Telegram where
aipager depends on it.

Driven by a real python-telegram-bot ``Bot`` pointed at the fake on
loopback (and raw httpx for the rich-message JSON calls), so a shape PTB
cannot parse, or an error PTB maps to the wrong exception, fails here
rather than as a confusing daemon log line in the e2e run.
"""

from __future__ import annotations

import io
import threading
import time

import httpx
import pytest
from telegram import Bot, BotCommand, BotCommandScopeChat, InlineKeyboardButton, \
    InlineKeyboardMarkup, ReactionTypeEmoji
from telegram.error import BadRequest, ChatMigrated, Forbidden, InvalidToken, RetryAfter

from tests.e2e.fake_telegram import updates as U
from tests.e2e.fake_telegram.server import FakeBotApiThread, errors

TOKEN = "7000000001:FAKEtokenFAKEtokenFAKEtoken"
BOT_ID = 7000000001
BOT_NAME = "aipager_fake_bot"
GROUP = -4000000001
DM = 900000001
ALICE = U.user(900000001, "alice")
BOB = U.user(900000002, "bob")


@pytest.fixture
def fake():
    t = FakeBotApiThread(TOKEN, BOT_ID, BOT_NAME)
    base = t.start()
    t.api.add_chat(GROUP, "group", "test group")
    t.api.add_chat(DM, "private", who=ALICE)
    try:
        yield t.api, base
    finally:
        t.stop()


def _bot(base: str, token: str = TOKEN) -> Bot:
    return Bot(token, base_url=f"{base}/bot", base_file_url=f"{base}/file/bot")


def _start_polling(api, base, run_async):
    async def go():
        async with _bot(base) as bot:
            return await bot.get_updates(timeout=0)
    assert run_async(go()) == ()
    assert api.polling_started.is_set()


def test_server_binds_loopback_only(fake):
    _api, base = fake
    assert base.startswith("http://127.0.0.1:")


def test_get_me_and_get_chat(fake, run_async):
    api, base = fake

    async def go():
        async with _bot(base) as bot:
            chat = await bot.get_chat(GROUP)
            return bot.bot, chat

    me, chat = run_async(go())
    assert (me.id, me.username, me.is_bot) == (BOT_ID, BOT_NAME, True)
    assert (chat.id, chat.type, chat.title) == (GROUP, "group", "test group")
    assert [c.method for c in api.calls()] == ["getMe", "getChat"]


def test_unknown_chat_is_chat_not_found(fake, run_async):
    _api, base = fake

    async def go():
        async with _bot(base) as bot:
            await bot.get_chat(-1)

    with pytest.raises(BadRequest, match="(?i)chat not found"):
        run_async(go())


def test_wrong_token_is_401_and_recorded(fake, run_async):
    api, base = fake

    async def go():
        async with _bot(base, "7000000001:WRONGwrongWRONGwrongWRONG") as bot:
            return bot

    with pytest.raises(InvalidToken):
        run_async(go())
    assert api.bad_token_calls == ["getMe"]


def test_unknown_method_is_404_and_recorded(fake):
    api, base = fake
    r = httpx.post(f"{base}/bot{TOKEN}/sendPhoto", data={"chat_id": str(GROUP)})
    assert r.status_code == 404
    assert r.json() == {"ok": False, "error_code": 404,
                        "description": "Not Found: method not found"}
    assert api.unknown_methods == ["sendPhoto"]


def test_send_edit_delete_round_trip(fake, run_async):
    api, base = fake
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Allow", callback_data="a:1")]])

    async def go():
        async with _bot(base) as bot:
            m = await bot.send_message(GROUP, "Hi <b>there</b> &amp; you",
                                       parse_mode="HTML", reply_markup=kb)
            out = {"sent": m}
            try:
                await bot.edit_message_text("Hi <b>there</b> &amp; you", GROUP,
                                            m.message_id, parse_mode="HTML",
                                            reply_markup=kb)
            except BadRequest as e:
                out["same"] = str(e)
            out["edited"] = await bot.edit_message_text("changed", GROUP, m.message_id)
            out["markup"] = await bot.edit_message_reply_markup(GROUP, m.message_id,
                                                                reply_markup=kb)
            assert await bot.pin_chat_message(GROUP, m.message_id, disable_notification=True)
            assert await bot.delete_message(GROUP, m.message_id)
            for coro, key in (
                (bot.delete_message(GROUP, m.message_id), "delete_again"),
                (bot.edit_message_text("x", GROUP, m.message_id), "edit_deleted"),
            ):
                try:
                    await coro
                except BadRequest as e:
                    out[key] = str(e)
            m2 = await bot.send_message(GROUP, "second")
            out["second"] = m2
            return out

    out = run_async(go())
    sent = out["sent"]
    assert sent.message_id == 1 and sent.chat.id == GROUP
    assert sent.text == "Hi there & you"
    assert sent.from_user.id == BOT_ID
    assert sent.reply_markup.inline_keyboard[0][0].callback_data == "a:1"
    assert "not modified" in out["same"].lower()
    assert out["edited"].text == "changed" and out["edited"].reply_markup is None
    assert out["markup"].reply_markup.inline_keyboard[0][0].text == "✅ Allow"
    assert "message to delete not found" in out["delete_again"].lower()
    assert "message to edit not found" in out["edit_deleted"].lower()
    assert out["second"].message_id == 2
    assert api.visible_messages(GROUP)[-1]["text"] == "second"
    assert [m["message_id"] for m in api.visible_messages(GROUP)] == [2]


def test_reaction_on_a_user_message_and_reply_to(fake, run_async):
    api, base = fake
    _start_polling(api, base, run_async)
    user_msg = api.inject_text(GROUP, ALICE, "hello")

    async def go():
        async with _bot(base) as bot:
            await bot.set_message_reaction(GROUP, user_msg["message_id"],
                                           [ReactionTypeEmoji("👀")])
            return await bot.send_message(GROUP, "answer",
                                          reply_to_message_id=user_msg["message_id"])

    reply = run_async(go())
    assert api.reactions(GROUP, user_msg["message_id"]) == ["👀"]
    assert reply.reply_to_message.message_id == user_msg["message_id"]
    assert reply.reply_to_message.from_user.id == ALICE["id"]


def test_document_upload_and_file_download(fake, run_async):
    api, base = fake
    _start_polling(api, base, run_async)
    inbound = api.inject_document(DM, ALICE, "notes.txt", b"from alice")

    async def go():
        async with _bot(base) as bot:
            m = await bot.send_document(DM, io.BytesIO(b"payload"), filename="out.txt",
                                        caption="the log")
            f = await bot.get_file(inbound["document"]["file_id"])
            got = await f.download_as_bytearray()
            return m, f, bytes(got)

    m, f, got = run_async(go())
    assert m.document.file_name == "out.txt" and m.caption == "the log"
    call = api.calls("sendDocument")[0]
    assert call.files["document"] == ("out.txt", b"payload")
    assert not f.file_path.startswith(("/", "file:"))
    assert got == b"from alice"


def test_rich_message_json_calls(fake):
    api, base = fake
    payload = {"chat_id": GROUP, "rich_message": {"markdown": "**hi**", "is_rtl": False},
               "reply_markup": {"inline_keyboard": [[{"text": "Stop",
                                                      "callback_data": "s"}]]}}
    r = httpx.post(f"{base}/bot{TOKEN}/sendRichMessage", json=payload).json()
    assert r["ok"] and r["result"]["message_id"] == 1
    edit = {"chat_id": GROUP, "message_id": 1,
            "rich_message": {"markdown": "**hi**", "is_rtl": False},
            "reply_markup": payload["reply_markup"]}
    same = httpx.post(f"{base}/bot{TOKEN}/editMessageText", json=edit)
    assert same.status_code == 400 and "not modified" in same.json()["description"]
    edit["rich_message"]["markdown"] = "**bye**"
    assert httpx.post(f"{base}/bot{TOKEN}/editMessageText", json=edit).json()["ok"]
    assert api.calls("sendRichMessage")[0].text == "**hi**"
    assert api.find_button(GROUP, "Stop")[1] == "s"


@pytest.mark.parametrize("factory,exc,check", [
    (errors.blocked, Forbidden, lambda e: "blocked" in str(e)),
    (lambda: errors.retry_after(7), RetryAfter, lambda e: e.retry_after == 7),
    (lambda: errors.migrated(-1004000000001), ChatMigrated,
     lambda e: e.new_chat_id == -1004000000001),
    (errors.not_modified, BadRequest, lambda e: "not modified" in str(e).lower()),
    (errors.message_not_found, BadRequest, lambda e: "not found" in str(e).lower()),
    (errors.chat_not_found, BadRequest, lambda e: "chat not found" in str(e).lower()),
])
def test_injected_errors_map_to_ptb_exceptions(fake, run_async, factory, exc, check):
    api, base = fake
    api.fail_next("sendMessage", factory(), chat_id=GROUP)

    async def go():
        async with _bot(base) as bot:
            try:
                await bot.send_message(GROUP, "x")
            except exc as e:
                caught = e
            else:
                caught = None
            ok = await bot.send_message(GROUP, "after")  # used up: works again
            return caught, ok

    caught, ok = run_async(go())
    assert caught is not None and check(caught)
    assert ok.text == "after"


def test_fail_next_respects_chat_and_times(fake, run_async):
    api, base = fake
    api.fail_next("sendMessage", errors.blocked(), chat_id=DM, times=2)

    async def go():
        async with _bot(base) as bot:
            await bot.send_message(GROUP, "group is fine")
            res = []
            for _ in range(3):
                try:
                    await bot.send_message(DM, "dm")
                    res.append("ok")
                except Forbidden:
                    res.append("blocked")
            return res

    assert run_async(go()) == ["blocked", "blocked", "ok"]


def test_get_updates_offset_timeout_and_drop_pending(fake, run_async):
    api, base = fake
    _start_polling(api, base, run_async)
    api.inject_text(GROUP, ALICE, "one")
    api.inject_text(GROUP, BOB, "two")

    async def go():
        async with _bot(base) as bot:
            first = await bot.get_updates(timeout=0)
            again = await bot.get_updates(offset=first[0].update_id, timeout=0)
            after = await bot.get_updates(offset=first[-1].update_id + 1, timeout=0)
            t0 = time.monotonic()
            held = await bot.get_updates(offset=first[-1].update_id + 1, timeout=1)
            waited = time.monotonic() - t0
            return first, again, after, held, waited

    first, again, after, held, waited = run_async(go())
    assert [u.message.text for u in first] == ["one", "two"]
    assert [u.message.text for u in again] == ["one", "two"]
    assert after == () and held == ()
    assert waited >= 0.9

    api.inject_text(GROUP, ALICE, "three")

    async def drop():
        async with _bot(base) as bot:
            await bot.delete_webhook(drop_pending_updates=True)
            return await bot.get_updates(timeout=0)

    assert run_async(drop()) == ()


def test_long_poll_wakes_on_inject(fake, run_async):
    api, base = fake
    _start_polling(api, base, run_async)
    timer = threading.Timer(0.3, lambda: api.inject_text(GROUP, ALICE, "wake"))

    async def go():
        async with _bot(base) as bot:
            timer.start()
            t0 = time.monotonic()
            got = await bot.get_updates(timeout=10, read_timeout=20)
            return got, time.monotonic() - t0

    got, took = run_async(go())
    timer.join()
    assert [u.message.text for u in got] == ["wake"]
    assert took < 5


def test_inject_before_polling_is_refused(fake):
    api, _base = fake
    with pytest.raises(AssertionError, match="first getUpdates"):
        api.inject_text(GROUP, ALICE, "too early")


def test_injected_updates_parse_in_ptb(fake, run_async):
    api, base = fake
    _start_polling(api, base, run_async)

    async def send_bot_message():
        async with _bot(base) as bot:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("x1", callback_data="t:x1")]])
            return await bot.send_message(GROUP, "Which session?", reply_markup=kb)

    bot_msg = run_async(send_bot_message())
    bot_dict = api.message(GROUP, bot_msg.message_id)
    cmd = api.inject_text(GROUP, ALICE, "/x1@aipager_fake_bot do it")
    api.inject_text(GROUP, BOB, "😀 hi @aipager_fake_bot there")
    api.inject_text(GROUP, ALICE, "a reply", reply_to=bot_dict, thread_id=5)
    api.inject_edited(GROUP, ALICE, cmd["message_id"], "/x1 edited")
    cb = api.inject_callback(BOB, bot_dict, "t:x1")
    api.inject_sender_chat(GROUP, "@aipager_fake_bot hi", {"id": GROUP, "type": "group",
                                                          "title": "test group"})
    api.inject_migration(GROUP, -1004000000001)

    async def go():
        async with _bot(base) as bot:
            got = await bot.get_updates(timeout=0)
            await bot.answer_callback_query(cb, text="Your role can't do that here.",
                                            show_alert=True)
            return got

    ups = run_async(go())
    c, m, r, e, q, s, mig_old, mig_new = ups
    assert c.message.entities[0].type == "bot_command"
    assert c.message.entities[0].length == len("/x1@aipager_fake_bot")
    ment = m.message.entities[0]
    assert ment.type == "mention" and ment.offset == 6  # emoji = 2 UTF-16 units
    assert m.message.parse_entity(ment) == "@aipager_fake_bot"
    assert r.message.reply_to_message.from_user.id == BOT_ID
    assert r.message.message_thread_id == 5
    assert e.edited_message.text == "/x1 edited" and e.message is None
    assert q.callback_query.data == "t:x1" and q.callback_query.from_user.id == BOB["id"]
    assert q.callback_query.message.chat.id == GROUP
    assert s.message.sender_chat.id == GROUP
    assert s.message.from_user.id == U.GROUP_ANONYMOUS_BOT_ID
    assert mig_old.message.migrate_to_chat_id == -1004000000001
    assert mig_old.message.chat.type == "group"
    assert mig_new.message.migrate_from_chat_id == GROUP
    assert mig_new.message.chat.type == "supergroup"
    assert api.wait_answer(cb, timeout=5)["text"] == "Your role can't do that here."


def test_migrated_group_rejects_calls(fake, run_async):
    api, base = fake
    _start_polling(api, base, run_async)
    api.inject_migration(GROUP, -1004000000001)

    async def go():
        async with _bot(base) as bot:
            try:
                await bot.send_message(GROUP, "x")
            except ChatMigrated as e:
                return e.new_chat_id

    assert run_async(go()) == -1004000000001


def test_commands_and_menu_button(fake, run_async):
    api, base = fake

    async def go():
        async with _bot(base) as bot:
            scope = BotCommandScopeChat(GROUP)
            await bot.set_my_commands([BotCommand("status", "Sessions")], scope=scope)
            got = await bot.get_my_commands(scope=scope)
            await bot.delete_my_commands(scope=scope)
            gone = await bot.get_my_commands(scope=scope)
            await bot.set_chat_menu_button(chat_id=DM)
            await bot.send_chat_action(DM, "typing")
            return got, gone

    got, gone = run_async(go())
    assert [c.command for c in got] == ["status"] and gone == ()
    assert api.unknown_methods == []


def test_wait_for_times_out_with_the_last_calls_and_no_token(fake, run_async):
    api, base = fake
    run_async(_bot(base).initialize())
    with pytest.raises(AssertionError) as exc:
        api.wait_for(lambda c: c.method == "sendMessage", timeout=0.2)
    assert "getMe" in str(exc.value)
    assert TOKEN not in str(exc.value)


# ---------------------------------------------------------------------------
# Pure builders.
# ---------------------------------------------------------------------------

def test_command_entity_covers_the_bot_suffix():
    assert U.command_entities("/x1@aipager_fake_bot do it") == [
        {"type": "bot_command", "offset": 0, "length": 20}]
    assert U.command_entities("hello") == []
    assert U.mention_entities("/x1@aipager_fake_bot do it") == []


def test_mention_offsets_are_utf16():
    text = "😀 @bob and @aipager_fake_bot"
    ents = U.mention_entities(text)
    assert ents == [
        {"type": "mention", "offset": 3, "length": 4},
        {"type": "mention", "offset": 12, "length": 17},
    ]
    assert U.mention_entities("mail me at a@bob.com") == []


def test_text_message_shapes():
    chat = U.chat(GROUP, "group", "g")
    parent = U.text_message(1, chat, U.user(BOT_ID, BOT_NAME, is_bot=True), "q",
                            reply_to=U.text_message(0, chat, ALICE, "x"))
    m = U.text_message(2, chat, ALICE, "hi", reply_to=parent, thread_id=9)
    assert m["reply_to_message"]["message_id"] == 1
    assert "reply_to_message" not in m["reply_to_message"]
    assert m["message_thread_id"] == 9 and "entities" not in m
    assert U.edited(m, "/new")["entities"][0]["type"] == "bot_command"


def test_thread_stops_cleanly():
    t = FakeBotApiThread(TOKEN, BOT_ID, BOT_NAME)
    base = t.start()
    assert httpx.post(f"{base}/bot{TOKEN}/getMe").json()["ok"]
    t.stop()
    t.stop()  # idempotent
    assert not any(th.name == "fake-bot-api" for th in threading.enumerate())
    with pytest.raises(httpx.ConnectError):
        httpx.post(f"{base}/bot{TOKEN}/getMe", timeout=2)
