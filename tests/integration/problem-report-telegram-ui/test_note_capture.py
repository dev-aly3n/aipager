"""SC-7 and SC-8: Add a note takes the owner's next DM text as the note,
and ANY other action closes the capture (design.md Success criteria 7,
8; spec.md "note capture (and that any other action closes it)";
memory: capture flows close by default).

Methods: equivalence partitioning over the next update (a plain text in
the DM, a command, another button, a photo, a voice note, a reply to
another message, a keyboard word, a Mini App action, opening another
card); boundary-value analysis on the note length (500 kept, 501 cut)
and emptiness; error guessing: the capture's edit muted, a text in
another chat, another person's text."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.bot.flood import MUTE

NOTE_CLOSED = "Note not added: you did something else. Tap Add a note to try again."
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _note_of(bot, h, cid, mid):
    block = h.preview_block(bot.tg.text_of(cid, mid))
    return json.loads(block).get("note") if block is not None else "<no block>"


async def _armed(bot, d, h):
    h.record_exc()
    cid, mid = await h.open_card(bot, d)
    await d.tap("_:rp:note", chat=cid, message_id=mid)
    return cid, mid


def _with_note(bot, drive, h, run_async, text, *, send=False):
    d = drive(bot)

    async def go():
        cid, mid = await _armed(bot, d, h)
        await d.text(text)
        if send:
            await d.tap("_:rp:send", chat=cid, message_id=mid)
        return cid, mid
    return run_async(go())


# ---- SC-7: the next DM text becomes the note ------------------------------------

def test_note_tap_shows_the_capture_prompt(bot, drive, h, run_async):
    d = drive(bot)
    cid, mid = run_async(_armed(bot, d, h))
    assert "Type your note as one message (at most 500 characters)" in bot.tg.text_of(cid, mid)


def test_capture_card_buttons_are_back_and_cancel(bot, drive, h, run_async):
    d = drive(bot)
    cid, mid = run_async(_armed(bot, d, h))
    assert [data for _, data in bot.tg.buttons_of(cid, mid)] == ["_:rp:back", "_:rp:cancel"]


def test_note_text_appears_in_preview(bot, drive, h, run_async):
    cid, mid = _with_note(bot, drive, h, run_async, "the card froze after a photo")
    assert _note_of(bot, h, cid, mid) == "the card froze after a photo"


def test_note_added_line(bot, drive, h, run_async):
    cid, mid = _with_note(bot, drive, h, run_async, "the card froze")
    assert "Note added." in bot.tg.text_of(cid, mid)


def test_note_restores_the_preview_buttons(bot, drive, h, run_async):
    cid, mid = _with_note(bot, drive, h, run_async, "the card froze")
    assert "_:rp:send" in [data for _, data in bot.tg.buttons_of(cid, mid)]


def test_preview_with_note_still_equals_render_preview(bot, drive, h, run_async):
    from aipager.report import builder
    cid, mid = _with_note(bot, drive, h, run_async, "Ça gèle <après> une photo & 日本")
    block = h.preview_block(bot.tg.text_of(cid, mid))
    assert block == builder.render_preview(json.loads(block))


def test_sent_note_is_the_typed_text(bot, drive, h, run_async, net):
    _with_note(bot, drive, h, run_async, "the card froze after a photo", send=True)
    assert json.loads(h.attachment_of(net.posts[0]))["note"] == "the card froze after a photo"


def test_sent_bytes_equal_preview_with_note(bot, drive, h, run_async, net):
    cid, mid = _with_note(bot, drive, h, run_async, "a note & <tags>", send=True)
    assert h.attachment_of(net.posts[0]) == h.preview_block(
        bot.tg.text_of(cid, mid)).encode("utf-8")


def test_note_of_exactly_500_is_kept_whole(bot, drive, h, run_async):
    text = "a" * 500
    cid, mid = _with_note(bot, drive, h, run_async, text)
    assert _note_of(bot, h, cid, mid) == text


def test_note_of_exactly_500_is_not_called_cut(bot, drive, h, run_async):
    cid, mid = _with_note(bot, drive, h, run_async, "a" * 500)
    assert "cut to 500" not in bot.tg.text_of(cid, mid)


def test_note_of_501_is_cut_to_500(bot, drive, h, run_async):
    cid, mid = _with_note(bot, drive, h, run_async, "b" * 501)
    assert len(_note_of(bot, h, cid, mid)) == 500


def test_note_of_501_says_cut(bot, drive, h, run_async):
    cid, mid = _with_note(bot, drive, h, run_async, "b" * 501)
    assert "Note added, cut to 500 characters." in bot.tg.text_of(cid, mid)


# Telegram trims whitespace-only messages itself; zero-width spaces do
# arrive, and normalize to nothing.
INVISIBLE = "\u200b\u200b"


def test_blank_note_says_empty(bot, drive, h, run_async):
    cid, mid = _with_note(bot, drive, h, run_async, INVISIBLE)
    assert "That note was empty, so nothing was added." in bot.tg.text_of(cid, mid)


def test_blank_note_leaves_note_null(bot, drive, h, run_async):
    cid, mid = _with_note(bot, drive, h, run_async, INVISIBLE)
    assert _note_of(bot, h, cid, mid) is None


def test_second_text_after_note_is_not_a_note(bot, drive, h, run_async):
    """One message: the capture closes once it took its note."""
    d = drive(bot)

    async def go():
        cid, mid = await _armed(bot, d, h)
        await d.text("first")
        await d.text("second")
        return cid, mid
    cid, mid = run_async(go())
    assert _note_of(bot, h, cid, mid) == "first"


def test_back_closes_the_capture(bot, drive, h, run_async):
    d = drive(bot)

    async def go():
        cid, mid = await _armed(bot, d, h)
        await d.tap("_:rp:back", chat=cid, message_id=mid)
        await d.text("not a note")
        return cid, mid
    cid, mid = run_async(go())
    assert _note_of(bot, h, cid, mid) is None


def test_back_restores_the_preview(bot, drive, h, run_async):
    d = drive(bot)

    async def go():
        cid, mid = await _armed(bot, d, h)
        await d.tap("_:rp:back", chat=cid, message_id=mid)
        return cid, mid
    cid, mid = run_async(go())
    assert "_:rp:send" in [data for _, data in bot.tg.buttons_of(cid, mid)]


def test_text_in_a_group_is_not_the_note(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    d = drive(bot)

    async def go():
        cid, mid = await _armed(bot, d, h)
        await d.text("group chatter", chat=h.GROUP)
        await d.text("the real note")
        return cid, mid
    cid, mid = run_async(go())
    assert _note_of(bot, h, cid, mid) != "group chatter"


def test_capture_not_armed_when_its_edit_was_muted(bot, drive, h, run_async):
    """Error guessing: the capture card never appeared (the DM is muted),
    so the next text must not silently become a note."""
    d = drive(bot)

    async def go():
        h.record_exc()
        cid, mid = await h.open_card(bot, d)
        MUTE.mute(h.OWNER, 600.0)
        await d.tap("_:rp:note", chat=cid, message_id=mid)
        MUTE.clear()
        await d.text("this is not a note")
        return cid, mid
    cid, mid = run_async(go())
    assert _note_of(bot, h, cid, mid) is None


# ---- SC-8: any other action closes the capture -----------------------------------

def _hdr(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


async def _mini_app_action(bot, h):
    """A Mini App action that goes through (clear a session's queue)."""
    from aipager.miniapp.server import MiniAppServer
    sess = h.add_session(bot.registry, "dev")
    sess.queue_prompt("later", 1)
    srv = MiniAppServer(bot, bot.registry, port=8791)
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    try:
        resp = await client.post("/api/sessions/dev/clearqueue", headers=_hdr(h.OWNER))
        assert resp.status < 400, resp.status
    finally:
        await client.close()


ACTIONS = ["command", "other_button", "photo", "voice", "reply_to_other", "keyboard_word",
           "mini_app_action", "another_preview", "settings_card"]


async def _do(action, bot, d, h, cid, mid):
    if action == "command":
        await d.command("/help")
    elif action == "other_button":
        await d.tap("_:set:close", message_id=777)
    elif action == "photo":
        await d.media("photo")
    elif action == "voice":
        await d.media("voice")
    elif action == "reply_to_other":
        await d.text("this answers something else", reply_to=4242)
    elif action == "keyboard_word":
        bot._handle_status = AsyncMock()
        await d.text("status")
    elif action == "mini_app_action":
        await _mini_app_action(bot, h)
    elif action == "another_preview":
        await d.tap("_:rp:open")
    elif action == "settings_card":
        await d.command("/settings")
    else:
        raise AssertionError(action)
    await h.settle(10)


@pytest.fixture
def miniapp_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _close_then(bot, drive, h, run_async, action, then_text=None):
    d = drive(bot)

    async def go():
        cid, mid = await _armed(bot, d, h)
        await _do(action, bot, d, h, cid, mid)
        if then_text is not None:
            await d.text(then_text)
        return cid, mid
    return run_async(go())


@pytest.mark.parametrize("action", ACTIONS)
def test_other_action_closes_with_a_line(bot, drive, h, run_async, miniapp_token, action):
    cid, mid = _close_then(bot, drive, h, run_async, action)
    assert NOTE_CLOSED in bot.tg.text_of(cid, mid)


@pytest.mark.parametrize("action", ACTIONS)
def test_after_other_action_next_text_is_not_a_note(bot, drive, h, run_async, miniapp_token,
                                                     action):
    cid, mid = _close_then(bot, drive, h, run_async, action, then_text="routed normally")
    assert _note_of(bot, h, cid, mid) is None


@pytest.mark.parametrize("action", ACTIONS)
def test_after_other_action_card_can_still_send(bot, drive, h, run_async, miniapp_token, net,
                                                action):
    """Closing the capture puts the preview back; it is not a cancel."""
    d = drive(bot)

    async def go():
        cid, mid = await _armed(bot, d, h)
        await _do(action, bot, d, h, cid, mid)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert len(net.posts) == 1


def test_someone_elses_update_keeps_the_capture(make_bot, drive, h, run_async):
    """Only the capture's own person closes it."""
    bot = make_bot("scope")
    d = drive(bot)

    async def go():
        cid, mid = await _armed(bot, d, h)
        await d.command("/help", user=h.ADMIN, chat=h.GROUP)
        await d.text("my note")
        return cid, mid
    cid, mid = run_async(go())
    assert _note_of(bot, h, cid, mid) == "my note"
