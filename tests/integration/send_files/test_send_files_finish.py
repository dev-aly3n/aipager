"""Files Claude sends (SendUserFile), end to end through the daemon's finish
path with a recording bot: they arrive after the answer, as replies to it
(to the merged card, or to the prompt when there is no answer), silent, in
the session's own chat, held behind a held answer or a mute, and a file
that fails gets one line in the chat's language."""

from __future__ import annotations

import json
import struct
import threading
import time
from unittest.mock import AsyncMock

from telegram import InputMediaPhoto
from telegram.error import BadRequest

from aipager import i18n, preferences
from aipager.bot import outbox
from aipager.bot.flood import MUTE
from aipager.bot.held import HELD
from aipager.state import Status

CHAT = 256113222          # the chat `_pin_single_chat_config` pins
TRIGGER = 11


def png_bytes(w=64, h=48) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
            + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00" + b"\x00" * 64)


def _bot(mk_bot, files_bot):
    bot = mk_bot()
    bot._app.bot = files_bot
    bot.refresh_pinned = AsyncMock()
    bot.pinned_tick = AsyncMock()
    return bot


def _sess(bot, name="dev", chat=CHAT, kind="dm", tmp_path=None):
    sess = bot.registry.get_or_create(f"claude-{name}")
    sess.label = name
    sess.scope_kind = kind
    sess.scope_chat_id = chat
    sess.trigger_msg_id = TRIGGER
    if tmp_path is not None:
        sess.cwd = str(tmp_path)
    bot.registry.transition(sess.name, Status.BUSY)
    return sess


def _pic(tmp_path, name="chart.png", w=64, h=48):
    p = tmp_path / name
    p.write_bytes(png_bytes(w, h))
    return p


async def _send_files(bot, sess, paths, *, tool_use_id="toolu_1", caption=None,
                      display=None):
    inp = {"files": [str(p) for p in paths], "status": "normal"}
    if caption:
        inp["caption"] = caption
    if display:
        inp["display"] = display
    cwd = str(paths[0].parent)
    await bot.notify(sess, "send_files_requested",
                     {"tool_use_id": tool_use_id, "tool_input": inp, "cwd": cwd})
    await bot.notify(sess, "send_files_ran", {
        "tool_use_id": tool_use_id, "tool_input": inp, "cwd": cwd,
        "tool_response": {"attachments": [{"path": str(p), "size": 1} for p in paths]}})


async def _finish(bot, sess, answer):
    bot.registry.transition(sess.name, Status.IDLE)
    await bot.notify(sess, "idle_prompt", {"summary": answer})


def _rich_ids(files_bot):
    return [c[2]["message_id"] for c in files_bot.calls if c[0] == "sendRichMessage"]


def _index(files_bot, name):
    return [i for i, c in enumerate(files_bot.calls) if c[0] == name]


# ---- where the files go ---------------------------------------------------------

def test_files_reply_to_the_answer(mk_bot, files_bot, rich, run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "Here is the chart.")
    run_async(go())
    [answer_id] = _rich_ids(files_bot)
    [photo] = files_bot.file_calls()
    assert photo[0] == "send_photo"
    assert photo[1] == CHAT
    assert photo[2]["reply_to_message_id"] == answer_id
    assert photo[2]["caption"] == "💬 dev"
    assert _index(files_bot, "sendRichMessage")[0] < _index(files_bot, "send_photo")[0]
    # The copy is gone once sent.
    assert not [p for p in outbox.outbox_dir().rglob("*") if p.is_file()]


def test_file_send_kwargs(mk_bot, files_bot, rich, run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "Done.")
    run_async(go())
    [photo] = files_bot.file_calls()
    kw = photo[2]
    assert kw["allow_sending_without_reply"] is True
    assert kw["write_timeout"] == outbox.FILE_SEND_WRITE_TIMEOUT == 300.0
    assert kw["read_timeout"] == 60.0


def test_files_are_silent(mk_bot, files_bot, rich, run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    doc = tmp_path / "notes.txt"
    doc.write_text("hello")

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path), doc])
        await _finish(bot, sess, "Done.")
    run_async(go())
    calls = files_bot.file_calls()
    assert [c[0] for c in calls] == ["send_photo", "send_document"]
    assert all(c[2]["disable_notification"] is True for c in calls)


def test_files_go_to_the_sessions_own_chat(mk_bot, files_bot, rich, run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    group = -1009876543
    sess = _sess(bot, name="frontend", chat=group, kind="group", tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "Done.")
    run_async(go())
    [photo] = files_bot.file_calls()
    assert photo[1] == group


def test_an_album_carries_the_caption_on_its_first_item(mk_bot, files_bot, rich,
                                                        run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path, "a.png"), _pic(tmp_path, "b.png")],
                          caption="Two charts")
        await _finish(bot, sess, "Done.")
    run_async(go())
    [album] = files_bot.file_calls()
    assert album[0] == "send_media_group"
    media = album[2]["media"]
    assert [type(m) for m in media] == [InputMediaPhoto, InputMediaPhoto]
    assert media[0].caption == "💬 dev\nTwo charts"
    assert media[1].caption is None


def test_files_reply_to_the_prompt_without_answer(mk_bot, files_bot, rich, run_async,
                                                  tmp_path):
    """The card is kept as the finished card and the turn has no answer
    text: no answer message, so the files reply to the prompt."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    sess.busy_msg_id = 700
    bot._edit_busy_rich = AsyncMock(return_value=True)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "")
    run_async(go())
    assert _rich_ids(files_bot) == []
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == TRIGGER


def test_files_reply_to_the_merged_card(mk_bot, files_bot, rich, run_async, tmp_path):
    preferences.set_preference(CHAT, "layout", "merged")
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    sess.busy_msg_id = 700
    bot._send_merged_final = AsyncMock(return_value=True)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "The answer, merged into the card.")
    run_async(go())
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == 700


def test_files_reply_to_the_prompt_after_an_api_error(mk_bot, files_bot, rich,
                                                      run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "API Error: 500 internal server error")
    run_async(go())
    error = _index(files_bot, "send_message")
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == TRIGGER
    assert error and error[0] < files_bot.calls.index(photo)


def test_a_job_interim_answer_takes_its_turns_files(mk_bot, files_bot, rich, run_async,
                                                    tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        bot.registry.transition(sess.name, Status.IDLE)
        await bot._handle_job_interim(sess, {"summary": "An agent still runs."},
                                      time.time(), reply_to=TRIGGER)
    run_async(go())
    [interim_id] = _rich_ids(files_bot)
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == interim_id


def test_finish_waits_for_a_copy_in_flight(mk_bot, files_bot, rich, run_async, tmp_path,
                                           monkeypatch):
    real = outbox.copy_batch
    slow = threading.Event()

    def slow_copy(*a, **k):
        slow.wait(0.5)   # never set: a copy that takes half a second
        return real(*a, **k)
    monkeypatch.setattr(outbox, "copy_batch", slow_copy)
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert [c[0] for c in files_bot.file_calls()] == ["send_photo"]


# ---- a flood mute -----------------------------------------------------------------

def test_files_follow_the_held_answer_never_before(mk_bot, files_bot, rich, run_async,
                                                   tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def turn():
        await _send_files(bot, sess, [_pic(tmp_path)])
        MUTE.mute(CHAT, 600, source="test")
        await _finish(bot, sess, "The answer, held.")
    run_async(turn())
    assert files_bot.calls == []
    assert HELD.count(CHAT) == 1
    # Still muted: the monitor's flush sends nothing.
    run_async(bot.flush_turn_files(sess))
    assert files_bot.calls == []
    MUTE.clear()
    # Lifted, but the held answer has not gone yet: the files wait for it.
    run_async(bot.flush_turn_files(sess))
    assert files_bot.file_calls() == []
    run_async(bot.flush_held_answers(sess))
    [answer_id] = _rich_ids(files_bot)
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == answer_id
    assert _index(files_bot, "sendRichMessage")[0] < files_bot.calls.index(photo)


def test_files_wait_while_muted_without_answer(mk_bot, files_bot, rich, run_async,
                                               tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    sess.busy_msg_id = 700
    bot._edit_busy_rich = AsyncMock(return_value=True)

    async def turn():
        await _send_files(bot, sess, [_pic(tmp_path)])
        MUTE.mute(CHAT, 600, source="test")
        await _finish(bot, sess, "")
    run_async(turn())
    assert files_bot.file_calls() == []
    assert outbox.OUTBOX.has_pending(sess.name)
    MUTE.clear()
    run_async(bot.notify(sess, "turn_files_flush", {}))
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == TRIGGER
    assert not outbox.OUTBOX.has_pending(sess.name)


# ---- a failure ------------------------------------------------------------------------

def test_failed_file_gets_one_translated_line(mk_bot, files_bot, rich, run_async,
                                              tmp_path):
    from aipager import audit
    preferences.set_ui_language(CHAT, "fa")
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    files_bot.fail["send_photo"] = BadRequest("Bad Request: wrong file identifier")

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "Done.")
    run_async(go())
    lines = [c for c in files_bot.calls if c[0] == "send_message"]
    fa = i18n.get("fa").text(outbox.FAILED_LINES[outbox.CAUSE_REFUSED], name="chart.png")
    assert fa != i18n.get("en").text(outbox.FAILED_LINES[outbox.CAUSE_REFUSED],
                                     name="chart.png")
    assert [c[2]["_args"][1] for c in lines] == [fa]
    assert lines[0][2]["disable_notification"] is True
    records = [json.loads(line) for line in audit.AUDIT_LOG_PATH.read_text().splitlines()]
    failed = [r for r in records if r["action"] == "FileFailed"]
    assert [(r["file_name"], r["reason"]) for r in failed] == [("chart.png", "refused")]


def test_a_file_gone_before_the_copy_gets_its_line(mk_bot, files_bot, rich, run_async,
                                                   tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)

    async def go():
        inp = {"files": [str(pic)], "status": "normal"}
        await bot.notify(sess, "send_files_requested",
                         {"tool_use_id": "t", "tool_input": inp, "cwd": str(tmp_path)})
        pic.unlink()
        await bot.notify(sess, "send_files_ran",
                         {"tool_use_id": "t", "tool_input": inp, "cwd": str(tmp_path)})
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert files_bot.file_calls() == []
    lines = [c for c in files_bot.calls if c[0] == "send_message"]
    assert len(lines) == 1
