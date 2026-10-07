"""Black-box: the test-only fake Telegram Bot API (spec Part B).

Driven through a real python-telegram-bot ``Bot`` (the daemon's client) and
raw httpx, against the public surface in entrypoints.md. design.md success
criterion 5: "The fake API passes its unit tests in the default suite and maps
errors to the PTB exceptions the daemon distinguishes."
"""

from __future__ import annotations

import asyncio
import datetime as dt
import socket
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.error import BadRequest, ChatMigrated, EndPointNotFound, Forbidden, InvalidToken
from telegram.error import RetryAfter

from tests.e2e.fake_telegram import updates as U
from tests.e2e.fake_telegram.server import FakeBotApiThread, errors

SECRET = "AAFakeBlackBoxSecretTail0123456789"
TOKEN = f"7000000001:{SECRET}"
BOT_ID, BOT_NAME = 7000000001, "aipager_fake_bot"
DM, GROUP, SUPER = 900000001, -4000000001, -1004000000001


@pytest.fixture
def fake():
    t = FakeBotApiThread(TOKEN, BOT_ID, BOT_NAME)
    base = t.start()
    try:
        t.api.add_chat(DM, "private")
        t.api.add_chat(GROUP, "group", "aipager test group")
        yield t.api, base
    finally:
        t.stop()


def alice(api):
    return api.user(900000001, "alice", "Alice")


def bob(api):
    return api.user(900000002, "bob", "Bob")


def run(base: str, fn, token: str = TOKEN):
    async def go():
        async with Bot(token, base_url=f"{base}/bot", base_file_url=f"{base}/file/bot") as bot:
            return await fn(bot)
    return asyncio.run(go())


def start_polling(api, base):
    run(base, lambda b: b.get_updates(timeout=0))
    assert api.polling_started.is_set()


def sent(api, base, text="hello", chat=DM, **kw):
    return run(base, lambda b: b.send_message(chat, text, **kw))


def _secs(v):
    return v.total_seconds() if isinstance(v, dt.timedelta) else v


# ---- start / basics ------------------------------------------------------------------

def test_base_url_is_loopback(fake):
    _, base = fake
    assert urlsplit(base).hostname == "127.0.0.1"


def test_get_me_username(fake):
    _, base = fake
    assert run(base, lambda b: b.get_me()).username == BOT_NAME


def test_get_me_id(fake):
    _, base = fake
    assert run(base, lambda b: b.get_me()).id == BOT_ID


def test_get_me_is_bot(fake):
    _, base = fake
    assert run(base, lambda b: b.get_me()).is_bot is True


def test_get_me_via_get_query(fake):
    _, base = fake
    r = httpx.get(f"{base}/bot{TOKEN}/getMe")
    assert r.json()["result"]["username"] == BOT_NAME


def test_two_fakes_get_distinct_ports():
    a, b = FakeBotApiThread(TOKEN, BOT_ID, BOT_NAME), FakeBotApiThread(TOKEN, BOT_ID, BOT_NAME)
    try:
        assert a.start() != b.start()
    finally:
        a.stop()
        b.stop()


# ---- auth and unknown methods ----------------------------------------------------------

def test_wrong_token_raises_invalid_token(fake):
    _, base = fake
    with pytest.raises(InvalidToken):
        run(base, lambda b: b.get_me(), token="7000000001:WRONGwrongWRONGwrong123")


def test_wrong_token_status_401(fake):
    _, base = fake
    assert httpx.post(f"{base}/bot1:nope/getMe").status_code == 401


def test_wrong_token_body_shape(fake):
    _, base = fake
    assert httpx.post(f"{base}/bot1:nope/getMe").json() == {
        "ok": False, "error_code": 401, "description": "Unauthorized"}


def test_wrong_token_recorded(fake):
    api, base = fake
    httpx.post(f"{base}/bot1:nope/getMe")
    assert len(api.bad_token_calls) == 1


def test_unknown_method_status_404(fake):
    _, base = fake
    assert httpx.post(f"{base}/bot{TOKEN}/fooBarBaz").status_code == 404


def test_unknown_method_body_shape(fake):
    _, base = fake
    assert httpx.post(f"{base}/bot{TOKEN}/fooBarBaz").json() == {
        "ok": False, "error_code": 404, "description": "Not Found: method not found"}


def test_unknown_method_recorded(fake):
    api, base = fake
    httpx.post(f"{base}/bot{TOKEN}/unpinChatMessage", data={"chat_id": DM})
    assert "unpinChatMessage" in [getattr(m, "method", m) for m in api.unknown_methods]


def test_unknown_method_through_ptb(fake):
    _, base = fake
    with pytest.raises(EndPointNotFound):
        run(base, lambda b: b.do_api_request("fooBarBaz"))


def test_unknown_methods_empty_after_supported_calls(fake):
    api, base = fake
    sent(api, base)
    run(base, lambda b: b.get_updates(timeout=0))
    assert api.unknown_methods == []


# ---- getUpdates: offset, limit, long-poll, drop_pending ---------------------------------

@pytest.fixture
def two_updates(fake):
    api, base = fake
    start_polling(api, base)
    api.inject_text(DM, alice(api), "one")
    api.inject_text(DM, alice(api), "two")
    return api, base


def test_updates_in_order(two_updates):
    _, base = two_updates
    ups = run(base, lambda b: b.get_updates(timeout=0))
    assert [u.message.text for u in ups] == ["one", "two"]


def test_update_ids_increase(two_updates):
    _, base = two_updates
    ups = run(base, lambda b: b.get_updates(timeout=0))
    assert ups[0].update_id < ups[1].update_id


def test_offset_equal_to_id_is_included(two_updates):
    _, base = two_updates
    first = run(base, lambda b: b.get_updates(timeout=0))[0].update_id
    assert len(run(base, lambda b: b.get_updates(offset=first, timeout=0))) == 2


def test_offset_one_past_first_drops_first(two_updates):
    _, base = two_updates
    first = run(base, lambda b: b.get_updates(timeout=0))[0].update_id
    ups = run(base, lambda b: b.get_updates(offset=first + 1, timeout=0))
    assert [u.message.text for u in ups] == ["two"]


def test_offset_past_last_returns_nothing(two_updates):
    _, base = two_updates
    last = run(base, lambda b: b.get_updates(timeout=0))[-1].update_id
    assert run(base, lambda b: b.get_updates(offset=last + 1, timeout=0)) == ()


def test_confirmed_updates_are_forgotten(two_updates):
    _, base = two_updates
    last = run(base, lambda b: b.get_updates(timeout=0))[-1].update_id
    run(base, lambda b: b.get_updates(offset=last + 1, timeout=0))
    assert run(base, lambda b: b.get_updates(timeout=0)) == ()


def test_limit(two_updates):
    _, base = two_updates
    assert len(run(base, lambda b: b.get_updates(limit=1, timeout=0))) == 1


def test_drop_pending_updates(two_updates):
    _, base = two_updates
    run(base, lambda b: b.delete_webhook(drop_pending_updates=True))
    assert run(base, lambda b: b.get_updates(timeout=0)) == ()


def test_long_poll_empty_returns_empty(fake):
    api, base = fake
    start_polling(api, base)
    assert run(base, lambda b: b.get_updates(timeout=1)) == ()


def test_long_poll_holds_for_timeout(fake):
    api, base = fake
    start_polling(api, base)
    t0 = time.monotonic()
    run(base, lambda b: b.get_updates(timeout=1))
    assert 0.8 <= time.monotonic() - t0 < 6


def test_long_poll_wakes_on_injection(fake):
    api, base = fake
    start_polling(api, base)
    threading.Timer(0.5, lambda: api.inject_text(DM, alice(api), "wake")).start()
    t0 = time.monotonic()
    ups = run(base, lambda b: b.get_updates(timeout=20, read_timeout=30))
    assert ([u.message.text for u in ups], time.monotonic() - t0 < 8) == (["wake"], True)


def test_injection_before_polling_is_refused(fake):
    api, _ = fake
    with pytest.raises(AssertionError):
        api.inject_text(DM, alice(api), "too early")


def test_polling_started_flag_unset_before_poll(fake):
    api, _ = fake
    assert not api.polling_started.is_set()


# ---- sending and editing ----------------------------------------------------------------

def test_send_message_echoes_text(fake):
    api, base = fake
    assert sent(api, base, "hi there").text == "hi there"


def test_send_message_chat_id(fake):
    api, base = fake
    assert sent(api, base, chat=GROUP).chat.id == GROUP


def test_send_message_from_bot(fake):
    api, base = fake
    assert sent(api, base).from_user.id == BOT_ID


def test_message_ids_increase_per_chat(fake):
    api, base = fake
    a, b = sent(api, base), sent(api, base)
    assert b.message_id > a.message_id


def test_send_message_echoes_markup(fake):
    api, base = fake
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Allow", callback_data="cb:allow")]])
    m = sent(api, base, reply_markup=kb)
    assert m.reply_markup.inline_keyboard[0][0].callback_data == "cb:allow"


def test_send_message_json_body(fake):
    _, base = fake
    r = httpx.post(f"{base}/bot{TOKEN}/sendMessage", json={"chat_id": DM, "text": "j"})
    assert r.json()["result"]["text"] == "j"


def test_send_message_form_body(fake):
    _, base = fake
    r = httpx.post(f"{base}/bot{TOKEN}/sendMessage", data={"chat_id": str(DM), "text": "f"})
    assert r.json()["result"]["text"] == "f"


def test_send_message_recorded_with_params(fake):
    api, base = fake
    sent(api, base, "rec")
    assert api.calls("sendMessage")[-1].params["text"] == "rec"


def test_concurrent_sends_get_unique_ids(fake):
    _, base = fake

    async def many(b):
        return await asyncio.gather(*(b.send_message(DM, f"m{i}") for i in range(20)))
    ids = [m.message_id for m in run(base, many)]
    assert len(set(ids)) == 20


def test_edit_changed_text(fake):
    api, base = fake
    m = sent(api, base, "v1")
    assert run(base, lambda b: b.edit_message_text("v2", DM, m.message_id)).text == "v2"


def test_edit_identical_text_not_modified(fake):
    api, base = fake
    m = sent(api, base, "same")
    with pytest.raises(BadRequest, match="(?i)message is not modified"):
        run(base, lambda b: b.edit_message_text("same", DM, m.message_id))


def test_edit_unknown_message_not_found(fake):
    _, base = fake
    with pytest.raises(BadRequest, match="(?i)message to edit not found"):
        run(base, lambda b: b.edit_message_text("x", DM, 987654))


def test_edit_deleted_message_not_found(fake):
    api, base = fake
    m = sent(api, base)
    run(base, lambda b: b.delete_message(DM, m.message_id))
    with pytest.raises(BadRequest, match="(?i)message to edit not found"):
        run(base, lambda b: b.edit_message_text("x", DM, m.message_id))


def test_edit_reply_markup(fake):
    api, base = fake
    m = sent(api, base)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Go", callback_data="go")]])
    out = run(base, lambda b: b.edit_message_reply_markup(DM, m.message_id, reply_markup=kb))
    assert out.reply_markup.inline_keyboard[0][0].text == "Go"


def test_delete_message(fake):
    api, base = fake
    m = sent(api, base)
    assert run(base, lambda b: b.delete_message(DM, m.message_id)) is True


def test_deleted_message_not_visible(fake):
    api, base = fake
    m = sent(api, base, "gone")
    run(base, lambda b: b.delete_message(DM, m.message_id))
    assert "gone" not in [v.get("text") for v in api.visible_messages(DM)]


def test_delete_unknown_not_found(fake):
    _, base = fake
    with pytest.raises(BadRequest, match="(?i)message to delete not found"):
        run(base, lambda b: b.delete_message(DM, 987654))


def test_double_delete_not_found(fake):
    api, base = fake
    m = sent(api, base)
    run(base, lambda b: b.delete_message(DM, m.message_id))
    with pytest.raises(BadRequest, match="(?i)message to delete not found"):
        run(base, lambda b: b.delete_message(DM, m.message_id))


def test_delete_messages(fake):
    api, base = fake
    ids = [sent(api, base).message_id, sent(api, base).message_id]
    assert run(base, lambda b: b.delete_messages(DM, ids)) is True


def test_set_reaction_recorded(fake):
    api, base = fake
    m = sent(api, base)
    run(base, lambda b: b.set_message_reaction(DM, m.message_id, "👍"))
    assert api.reactions(DM, m.message_id) == ["👍"]


def test_pin(fake):
    api, base = fake
    m = sent(api, base)
    assert run(base, lambda b: b.pin_chat_message(DM, m.message_id)) is True


def test_chat_action(fake):
    _, base = fake
    assert run(base, lambda b: b.send_chat_action(DM, "typing")) is True


def test_send_document_bytes_recorded(fake):
    api, base = fake
    run(base, lambda b: b.send_document(DM, b"PAYLOAD-123", filename="log.txt"))
    files = api.calls("sendDocument")[-1].files
    assert [v[1] for v in files.values()] == [b"PAYLOAD-123"]


def test_send_document_filename_recorded(fake):
    api, base = fake
    run(base, lambda b: b.send_document(DM, b"x", filename="log.txt"))
    assert [v[0] for v in api.calls("sendDocument")[-1].files.values()] == ["log.txt"]


def test_send_rich_message_raw_json(fake):
    _, base = fake
    r = httpx.post(f"{base}/bot{TOKEN}/sendRichMessage",
                   json={"chat_id": DM, "rich_message": {"markdown": "**hi**"}})
    assert r.json()["ok"] is True


def test_send_rich_message_recorded(fake):
    api, base = fake
    httpx.post(f"{base}/bot{TOKEN}/sendRichMessage",
               json={"chat_id": DM, "rich_message": {"markdown": "**hi**"}})
    assert len(api.calls("sendRichMessage", chat_id=DM)) == 1


def test_my_commands_round_trip(fake):
    _, base = fake

    async def go(b):
        await b.set_my_commands([("status", "Show status")])
        return await b.get_my_commands()
    assert [c.command for c in run(base, go)] == ["status"]


def test_delete_my_commands(fake):
    _, base = fake
    assert run(base, lambda b: b.delete_my_commands()) is True


def test_set_chat_menu_button(fake):
    _, base = fake
    assert run(base, lambda b: b.set_chat_menu_button()) is True


def test_get_chat_known(fake):
    _, base = fake
    assert run(base, lambda b: b.get_chat(GROUP)).title == "aipager test group"


def test_get_chat_unknown(fake):
    _, base = fake
    with pytest.raises(BadRequest, match="(?i)chat not found"):
        run(base, lambda b: b.get_chat(-999))


# ---- injected errors -> PTB exceptions ---------------------------------------------------

def test_blocked_maps_to_forbidden(fake):
    api, base = fake
    api.fail_next("sendMessage", errors.blocked())
    with pytest.raises(Forbidden, match="(?i)bot was blocked by the user"):
        sent(api, base)


def test_retry_after_maps_to_retry_after(fake):
    api, base = fake
    api.fail_next("sendMessage", errors.retry_after(7))
    with pytest.raises(RetryAfter) as ei:
        sent(api, base)
    assert _secs(ei.value.retry_after) == 7


def test_retry_after_status_429(fake):
    api, base = fake
    api.fail_next("sendMessage", errors.retry_after(3))
    with pytest.raises(RetryAfter):
        sent(api, base)
    assert api.calls("sendMessage")[-1].status == 429


def test_migrated_maps_to_chat_migrated(fake):
    api, base = fake
    api.fail_next("sendMessage", errors.migrated(SUPER))
    with pytest.raises(ChatMigrated) as ei:
        sent(api, base, chat=GROUP)
    assert ei.value.new_chat_id == SUPER


@pytest.mark.parametrize("factory,needle", [
    (errors.not_modified, "message is not modified"),
    (errors.message_not_found, "not found"),
    (errors.chat_not_found, "chat not found"),
])
def test_bad_request_factories(fake, factory, needle):
    api, base = fake
    api.fail_next("sendMessage", factory())
    with pytest.raises(BadRequest, match="(?i)" + needle):
        sent(api, base)


def test_fail_next_once_then_ok(fake):
    api, base = fake
    api.fail_next("sendMessage", errors.blocked())
    with pytest.raises(Forbidden):
        sent(api, base)
    assert sent(api, base, "after").text == "after"


def test_fail_next_times_two(fake):
    api, base = fake
    api.fail_next("sendMessage", errors.blocked(), times=2)
    hits = 0
    for _ in range(3):
        try:
            sent(api, base)
        except Forbidden:
            hits += 1
    assert hits == 2


def test_fail_next_scoped_to_chat(fake):
    api, base = fake
    api.fail_next("sendMessage", errors.blocked(), chat_id=GROUP)
    sent(api, base, "dm ok", chat=DM)
    with pytest.raises(Forbidden):
        sent(api, base, chat=GROUP)


def test_fail_next_scoped_to_method(fake):
    api, base = fake
    api.fail_next("editMessageText", errors.blocked())
    assert sent(api, base, "send ok").text == "send ok"


def test_failed_call_still_recorded(fake):
    api, base = fake
    api.fail_next("sendMessage", errors.blocked())
    with pytest.raises(Forbidden):
        sent(api, base)
    assert api.calls("sendMessage")[-1].status == 403


# ---- migration ----------------------------------------------------------------------------

@pytest.fixture
def migrated(fake):
    api, base = fake
    start_polling(api, base)
    api.inject_migration(GROUP, SUPER)
    return api, base


def test_migration_old_chat_message(migrated):
    _, base = migrated
    ups = run(base, lambda b: b.get_updates(timeout=0))
    assert [(u.message.chat.id, u.message.migrate_to_chat_id) for u in ups
            if u.message.migrate_to_chat_id] == [(GROUP, SUPER)]


def test_migration_new_chat_message(migrated):
    _, base = migrated
    ups = run(base, lambda b: b.get_updates(timeout=0))
    assert [(u.message.chat.id, u.message.migrate_from_chat_id) for u in ups
            if u.message.migrate_from_chat_id] == [(SUPER, GROUP)]


def test_send_to_migrated_chat_raises_chat_migrated(migrated):
    api, base = migrated
    with pytest.raises(ChatMigrated) as ei:
        sent(api, base, chat=GROUP)
    assert ei.value.new_chat_id == SUPER


def test_send_to_new_supergroup_ok(migrated):
    api, base = migrated
    assert sent(api, base, chat=SUPER).chat.id == SUPER


# ---- injection shapes ---------------------------------------------------------------------

def _one(api, base, inject):
    start_polling(api, base)
    inject()
    ups = run(base, lambda b: b.get_updates(timeout=0))
    assert len(ups) == 1
    return ups[0]


def test_command_entity_with_botname(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), f"/x1@{BOT_NAME} do it"))
    e = u.message.entities[0]
    assert (e.type, e.offset, e.length) == (MessageEntity.BOT_COMMAND, 0, len(f"/x1@{BOT_NAME}"))


def test_command_entity_plain(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), "/x1 hello"))
    assert u.message.parse_entities([MessageEntity.BOT_COMMAND]).popitem()[1] == "/x1"


def test_plain_text_has_no_entities(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), "just chatting"))
    assert tuple(u.message.entities) == ()


def test_mention_after_astral_emoji_utf16(fake):
    api, base = fake
    text = f"😀👍 hi @{BOT_NAME} please"
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), text))
    assert list(u.message.parse_entities([MessageEntity.MENTION]).values()) == [f"@{BOT_NAME}"]


def test_two_mentions(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), "@bob and é @carol"))
    assert sorted(u.message.parse_entities([MessageEntity.MENTION]).values()) == [
        "@bob", "@carol"]


def test_explicit_entities_used(fake):
    api, base = fake
    ents = [{"type": "bold", "offset": 0, "length": 4}]
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), "bold text", entities=ents))
    assert [e.type for e in u.message.entities] == [MessageEntity.BOLD]


def test_sender_is_user(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_text(GROUP, bob(api), "hey"))
    assert (u.message.from_user.id, u.message.from_user.username) == (900000002, "bob")


def test_reply_to_bot_message(fake):
    api, base = fake
    m = sent(api, base, "card", chat=GROUP)
    bot_msg = api.calls("sendMessage")[-1].response["result"]
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), "re", reply_to=bot_msg))
    assert u.message.reply_to_message.message_id == m.message_id


def test_reply_to_bot_message_from_is_bot(fake):
    api, base = fake
    sent(api, base, "card", chat=GROUP)
    bot_msg = api.calls("sendMessage")[-1].response["result"]
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), "re", reply_to=bot_msg))
    assert u.message.reply_to_message.from_user.id == BOT_ID


def test_thread_id(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_text(GROUP, alice(api), "t", thread_id=77))
    assert u.message.message_thread_id == 77


def test_inject_edited(fake):
    api, base = fake
    start_polling(api, base)
    orig = api.inject_text(GROUP, alice(api), "before")
    last = run(base, lambda b: b.get_updates(timeout=0))[-1].update_id
    run(base, lambda b: b.get_updates(offset=last + 1, timeout=0))
    mid = orig.get("message_id") or orig["message"]["message_id"]
    api.inject_edited(GROUP, alice(api), mid, "after")
    ups = run(base, lambda b: b.get_updates(offset=last + 1, timeout=0))
    assert [(u.edited_message.message_id, u.edited_message.text) for u in ups] == [(mid, "after")]


def test_inject_callback_shape(fake):
    api, base = fake
    sent(api, base, "card", chat=GROUP)
    bot_msg = api.calls("sendMessage")[-1].response["result"]
    u = _one(api, base, lambda: api.inject_callback(bob(api), bot_msg, "cb:x"))
    q = u.callback_query
    assert (q.data, q.from_user.id, q.message.message_id) == (
        "cb:x", 900000002, bot_msg["message_id"])


def test_answer_callback_seen_by_wait_answer(fake):
    api, base = fake
    sent(api, base, "card", chat=GROUP)
    bot_msg = api.calls("sendMessage")[-1].response["result"]
    start_polling(api, base)
    cb = api.inject_callback(bob(api), bot_msg, "cb:x")
    run(base, lambda b: b.answer_callback_query(cb, text="Nope"))
    assert api.wait_answer(cb, timeout=5)["text"] == "Nope"


def test_inject_sender_chat(fake):
    api, base = fake
    sc = {"id": GROUP, "type": "group", "title": "aipager test group"}
    u = _one(api, base, lambda: api.inject_sender_chat(GROUP, "anon", sc))
    assert (u.message.sender_chat.id, u.message.from_user.id) == (GROUP, 1087968824)


def test_inject_document_downloadable(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_document(DM, alice(api), "a.txt", b"DOC-BYTES"))

    async def dl(b):
        f = await b.get_file(u.message.document.file_id)
        return bytes(await f.download_as_bytearray())
    assert run(base, dl) == b"DOC-BYTES"


def test_inject_document_filename_and_caption(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_document(DM, alice(api), "a.txt", b"x", caption="c"))
    assert (u.message.document.file_name, u.message.caption) == ("a.txt", "c")


def test_inject_voice_downloadable(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_voice(DM, alice(api), b"OGG", duration=3))

    async def dl(b):
        f = await b.get_file(u.message.voice.file_id)
        return bytes(await f.download_as_bytearray())
    assert (u.message.voice.duration, run(base, dl)) in ((3, b"OGG"), (dt.timedelta(seconds=3),
                                                                          b"OGG"))


def test_unknown_file_404(fake):
    _, base = fake
    assert httpx.get(f"{base}/file/bot{TOKEN}/documents/nope.bin").status_code == 404


def test_file_route_wrong_token_refused(fake):
    api, base = fake
    u = _one(api, base, lambda: api.inject_document(DM, alice(api), "a.txt", b"SECRETDOC"))
    path = run(base, lambda b: b.get_file(u.message.document.file_id)).file_path
    rel = path.split("/file/bot", 1)[-1].split("/", 1)[-1] if "/file/bot" in path else path
    r = httpx.get(f"{base}/file/bot1:nope/{rel}")
    assert r.status_code != 200 and b"SECRETDOC" not in r.content


# ---- pure builders (updates.py) --------------------------------------------------------

def test_builder_mention_entities_utf16():
    assert U.mention_entities("😀 @bob") == [{"type": "mention", "offset": 3, "length": 4}]


def test_builder_command_entities():
    ents = U.command_entities(f"/x1@{BOT_NAME} go")
    assert [(e["type"], e["offset"], e["length"]) for e in ents] == [
        ("bot_command", 0, len(f"/x1@{BOT_NAME}"))]


def test_builder_command_entities_none_for_plain():
    assert U.command_entities("hello /x1") == []


# ---- queries --------------------------------------------------------------------------

def test_calls_since_mark(fake):
    api, base = fake
    sent(api, base, "old")
    mark = api.mark()
    sent(api, base, "new")
    assert [c.params["text"] for c in api.calls("sendMessage", since=mark)] == ["new"]


def test_calls_filter_by_chat(fake):
    api, base = fake
    sent(api, base, "d", chat=DM)
    sent(api, base, "g", chat=GROUP)
    assert [c.params["text"] for c in api.calls("sendMessage", chat_id=GROUP)] == ["g"]


def test_wait_for_finds_call(fake):
    api, base = fake
    sent(api, base, "needle")
    c = api.wait_for(lambda c: c.method == "sendMessage" and c.params.get("text") == "needle",
                     timeout=5)
    assert c.method == "sendMessage"


def test_wait_for_timeout_fails(fake):
    api, _ = fake
    with pytest.raises((AssertionError, TimeoutError)):
        api.wait_for(lambda c: False, timeout=0.5)


def test_wait_for_timeout_never_prints_token(fake):
    api, base = fake
    sent(api, base, "x")
    try:
        api.wait_for(lambda c: False, timeout=0.5)
    except (AssertionError, TimeoutError) as exc:
        assert SECRET not in str(exc)


def test_find_button(fake):
    api, base = fake
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Allow", callback_data="cb:allow"),
                                InlineKeyboardButton("Deny", callback_data="cb:deny")]])
    sent(api, base, "perm", chat=GROUP, reply_markup=kb)
    assert api.find_button(GROUP, "Deny")[1] == "cb:deny"


def test_find_button_absent(fake):
    api, base = fake
    sent(api, base, "plain", chat=GROUP)
    assert not api.find_button(GROUP, "Allow")


def test_visible_messages_has_sent_text(fake):
    api, base = fake
    sent(api, base, "visible", chat=GROUP)
    assert "visible" in [m.get("text") for m in api.visible_messages(GROUP)]


# ---- teardown ----------------------------------------------------------------------------

def test_stop_leaves_no_thread():
    before = set(threading.enumerate())
    t = FakeBotApiThread(TOKEN, BOT_ID, BOT_NAME)
    t.start()
    t.stop()
    time.sleep(0.2)
    assert [x for x in threading.enumerate() if x not in before and x.is_alive()] == []


def test_stop_closes_port():
    t = FakeBotApiThread(TOKEN, BOT_ID, BOT_NAME)
    port = urlsplit(t.start()).port
    t.stop()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()


def test_stop_releases_pending_long_poll():
    t = FakeBotApiThread(TOKEN, BOT_ID, BOT_NAME)
    base = t.start()
    done = {}

    def poll():
        t0 = time.monotonic()
        try:
            httpx.post(f"{base}/bot{TOKEN}/getUpdates", data={"timeout": 25}, timeout=40)
        except httpx.HTTPError:
            pass
        done["elapsed"] = time.monotonic() - t0
    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.7)
    t0 = time.monotonic()
    t.stop()
    th.join(30)
    assert time.monotonic() - t0 < 12 and done.get("elapsed", 99) < 15
