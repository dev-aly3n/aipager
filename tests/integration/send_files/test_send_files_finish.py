"""Files Claude sends (SendUserFile), end to end through the daemon's finish
path with a recording bot: they arrive after the answer, as replies to it
(to the merged card, or to the prompt when there is no answer), silent, in
the session's own chat, held behind a held answer or a mute, and a file
that fails gets one line in the chat's language."""

from __future__ import annotations

import json
import os
import struct
import threading
import time
from pathlib import Path
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


def _default_transcript() -> Path:
    return (Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-proj"
            / "5b1d7363-0000-0000-0000-000000000001.jsonl")


def _new_turn(bot, sess):
    """A turn starts as a prompt starts one: BUSY, and the turn files may
    come from opened (what a UserPromptSubmit or a typed prompt does)."""
    bot.registry.transition(sess.name, Status.BUSY)
    sess.files_turn_opened(time.time())


def _sess(bot, name="dev", chat=CHAT, kind="dm", tmp_path=None):
    sess = bot.registry.get_or_create(f"claude-{name}")
    sess.label = name
    sess.scope_kind = kind
    sess.scope_chat_id = chat
    sess.trigger_msg_id = TRIGGER
    if tmp_path is not None:
        sess.cwd = str(tmp_path)
    if not sess.transcript_path:
        sess.transcript_path = str(_default_transcript())
    _new_turn(bot, sess)
    return sess


def _claude_line(obj, cwd):
    """*obj* as Claude Code writes a transcript line: with its cwd and a
    UTC timestamp."""
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return {**obj, "cwd": cwd, "timestamp": stamp.replace("+00:00", "Z")}


def _pic(tmp_path, name="chart.png", w=64, h=48):
    p = tmp_path / name
    p.write_bytes(png_bytes(w, h))
    return p


def _claude_writes_the_call(sess, tool_use_id, inp, cwd=None):
    """What Claude Code writes in the session's transcript for the call:
    the assistant's SendUserFile tool_use, with its cwd and timestamp (the
    daemon sends nothing it cannot find there, from this turn and folder)."""
    tp = Path(sess.files_transcript or sess.transcript_path
              or _default_transcript())
    tp.parent.mkdir(parents=True, exist_ok=True)
    with open(tp, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_claude_line({"type": "assistant", "message": {
            "role": "assistant", "content": [{
                "type": "tool_use", "id": tool_use_id, "name": "SendUserFile",
                "input": inp}]}}, cwd or sess.cwd)) + "\n")


async def _send_files(bot, sess, paths, *, tool_use_id="toolu_1", caption=None,
                      display=None):
    inp = {"files": [str(p) for p in paths], "status": "normal"}
    if caption:
        inp["caption"] = caption
    if display:
        inp["display"] = display
    cwd = str(paths[0].parent)
    _claude_writes_the_call(sess, tool_use_id, inp, cwd)
    await bot.notify(sess, "send_files_requested",
                     {"tool_use_id": tool_use_id, "tool_input": inp, "cwd": cwd})
    await bot.notify(sess, "send_files_ran", {
        "tool_use_id": tool_use_id, "tool_input": inp, "cwd": cwd,
        "tool_response": {"attachments": [{"path": str(p), "size": 1} for p in paths]}})


async def _finish(bot, sess, answer):
    bot.registry.transition(sess.name, Status.IDLE)
    await bot.notify(sess, "idle_prompt", {"summary": answer})
    await outbox.OUTBOX.settled(sess.name)


async def _settled(bot, sess, coro):
    """*coro* (a flush), then the session's delivery task it started."""
    out = await coro
    await outbox.OUTBOX.settled(sess.name)
    return out


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
        await outbox.OUTBOX.settled(sess.name)
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
    run_async(_settled(bot, sess, bot.flush_turn_files(sess)))
    assert files_bot.calls == []
    MUTE.clear()
    # Lifted, but the held answer has not gone yet: the files wait for it.
    run_async(_settled(bot, sess, bot.flush_turn_files(sess)))
    assert files_bot.file_calls() == []
    run_async(_settled(bot, sess, bot.flush_held_answers(sess)))
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
    # Not even an attempt into the mute: the files were queued at once.
    assert files_bot.refused == []
    assert outbox.OUTBOX.has_pending(sess.name)
    MUTE.clear()
    run_async(_settled(bot, sess, bot.notify(sess, "turn_files_flush", {})))
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == TRIGGER
    assert not outbox.OUTBOX.has_pending(sess.name)


def test_a_mute_that_starts_during_the_send_keeps_the_files(mk_bot, files_bot, rich,
                                                           run_async, tmp_path):
    """The chat was not muted when the files went out, but Telegram's ban
    arrived with the first one: they are kept and sent once it lifts."""
    from aipager.bot.flood import FloodMuted
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    files_bot.fail["send_photo"] = FloodMuted(600, CHAT)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert files_bot.file_calls() == []
    assert outbox.OUTBOX.has_pending(sess.name)
    del files_bot.fail["send_photo"]
    run_async(_settled(bot, sess, bot.flush_turn_files(sess)))
    [answer_id] = _rich_ids(files_bot)
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == answer_id


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
        _claude_writes_the_call(sess, "t", inp)
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


# ---- the finish never waits for an upload (rev-iter1-003) ---------------------

def test_the_finish_returns_and_opens_its_gate_before_the_upload_ends(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """An upload that takes long: the turn's finish is done (its gate open,
    the next turn's card free to go up) while the file is still uploading,
    and the file still goes, after the answer, once the upload ends."""
    import asyncio
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        files_bot.hold["send_photo"] = asyncio.Event()
        await _send_files(bot, sess, [_pic(tmp_path)])
        bot.registry.transition(sess.name, Status.IDLE)
        finish = asyncio.ensure_future(
            bot.notify(sess, "idle_prompt", {"summary": "Here it is."}))
        done, _ = await asyncio.wait([finish], timeout=3.0)
        finished_first = finish in done
        gate_open = sess.finish_gate is None
        uploading = outbox.OUTBOX.delivering(sess.name)
        files_bot.hold["send_photo"].set()
        await finish
        await outbox.OUTBOX.settled(sess.name)
        return finished_first, gate_open, uploading
    finished_first, gate_open, uploading = run_async(go())
    assert finished_first, "the finish waited for the upload"
    assert gate_open and uploading
    [answer_id] = _rich_ids(files_bot)
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == answer_id


def test_a_sessions_deliveries_go_one_at_a_time_in_order(mk_bot, files_bot, rich,
                                                         run_async, tmp_path):
    """Two turns: the first one's call shows in the transcript late, the
    second one's at once. The second turn's file still waits for the
    first's: one delivery at a time per session, in order."""
    import asyncio
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    first, second = _pic(tmp_path, "first.png"), _pic(tmp_path, "second.png")

    async def go():
        inp = {"files": [str(first)], "status": "normal"}
        _claude_writes_the_call(sess, "toolu_seed", {"files": ["/seed"]})
        await bot.notify(sess, "send_files_requested",
                         {"tool_use_id": "toolu_a", "tool_input": inp,
                          "cwd": str(tmp_path)})
        await bot.notify(sess, "send_files_ran",
                         {"tool_use_id": "toolu_a", "tool_input": inp,
                          "cwd": str(tmp_path)})
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": "The first."})
        _new_turn(bot, sess)
        await _send_files(bot, sess, [second], tool_use_id="toolu_b")
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": "The second."})
        await asyncio.sleep(0.4)
        # Now Claude Code flushes the first turn's call.
        _claude_writes_the_call(sess, "toolu_a", inp)
        await outbox.OUTBOX.settled(sess.name)
    run_async(go())
    names = [c[2]["photo"].filename for c in files_bot.file_calls()]
    assert names == ["first.png", "second.png"]


# ---- a call the transcript does not hold is never sent (rev-iter1-002) ----------

def _audit_records():
    from aipager import audit
    if not audit.AUDIT_LOG_PATH.exists():
        return []
    return [json.loads(line) for line in audit.AUDIT_LOG_PATH.read_text().splitlines()]


async def _forged_pair(bot, sess, pic, tool_use_id="toolu_forged", files=None):
    inp = {"files": files or [str(pic)], "status": "normal"}
    await bot.notify(sess, "send_files_requested",
                     {"tool_use_id": tool_use_id, "tool_input": inp, "cwd": str(pic.parent)})
    await bot.notify(sess, "send_files_ran",
                     {"tool_use_id": tool_use_id, "tool_input": inp, "cwd": str(pic.parent)})


def test_a_call_not_in_the_transcript_is_never_sent(mk_bot, files_bot, rich,
                                                    run_async, tmp_path):
    """A PreToolUse + PostToolUse pair with no such call in the session's
    transcript (a forged pair, during a real turn): the copy is made, then
    dropped after the bounded wait; nothing in the chat, one FileFailed
    record (unverified)."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)

    async def go():
        _claude_writes_the_call(sess, "toolu_other", {"files": [str(pic)]})
        await _forged_pair(bot, sess, pic)
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert files_bot.file_calls() == []
    assert [c for c in files_bot.calls if c[0] == "send_message"] == []
    failed = [r for r in _audit_records() if r["action"] == "FileFailed"]
    assert [(r["file_name"], r["reason"]) for r in failed] == [
        ("chart.png", outbox.CAUSE_UNVERIFIED)]
    assert not [p for p in outbox.outbox_dir().rglob("*") if p.is_file()]


def test_a_call_naming_other_files_in_the_transcript_is_not_sent(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """The id is Claude's, the file list is not: nothing is sent."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic, other = _pic(tmp_path), _pic(tmp_path, "other.png")

    async def go():
        _claude_writes_the_call(sess, "toolu_1", {"files": [str(other)]})
        await _forged_pair(bot, sess, pic, tool_use_id="toolu_1")
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert files_bot.file_calls() == []


def test_a_transcript_outside_claudes_folder_is_never_read(mk_bot, files_bot, rich,
                                                          run_async, tmp_path):
    """A datagram names the transcript too: one planted elsewhere, with the
    call in it, sends nothing."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    planted = tmp_path / "planted" / "x.jsonl"
    planted.parent.mkdir()
    # As if the turn had started with the planted path tracked.
    sess.transcript_path = sess.files_transcript = str(planted)

    async def go():
        _claude_writes_the_call(sess, "toolu_1", {"files": [str(pic)], "status": "normal"})
        await _forged_pair(bot, sess, pic, tool_use_id="toolu_1")
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert planted.exists()
    assert files_bot.file_calls() == []


def test_a_call_flushed_late_is_still_sent(mk_bot, files_bot, rich, run_async,
                                           tmp_path):
    """Claude Code writes its transcript lazily: a call that shows up within
    the wait is sent."""
    import asyncio
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        _claude_writes_the_call(sess, "toolu_seed", {"files": ["/seed"]})
        await _forged_pair(bot, sess, pic, tool_use_id="toolu_late")
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": "Done."})
        await asyncio.sleep(0.3)
        _claude_writes_the_call(sess, "toolu_late", inp)
        await outbox.OUTBOX.settled(sess.name)
    run_async(go())
    assert [c[0] for c in files_bot.file_calls()] == ["send_photo"]


def test_a_subagents_call_is_found_in_its_own_transcript(mk_bot, files_bot, rich,
                                                         run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        _claude_writes_the_call(sess, "toolu_seed", {"files": ["/seed"]})
        main = Path(sess.transcript_path)
        sub = main.with_suffix("") / "subagents" / "agent-a1b2c3.jsonl"
        sub.parent.mkdir(parents=True)
        sub.write_text(json.dumps(_claude_line({"type": "assistant", "message": {
            "content": [{"type": "tool_use", "id": "toolu_sub",
                         "name": "SendUserFile", "input": inp}]}},
            str(tmp_path))) + "\n")
        await bot.notify(sess, "send_files_requested",
                         {"tool_use_id": "toolu_sub", "tool_input": inp,
                          "cwd": str(tmp_path), "agent_id": "a1b2c3"})
        await bot.notify(sess, "send_files_ran",
                         {"tool_use_id": "toolu_sub", "tool_input": inp,
                          "cwd": str(tmp_path), "agent_id": "a1b2c3"})
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert [c[0] for c in files_bot.file_calls()] == ["send_photo"]


def test_a_request_with_no_turn_running_is_not_recorded(mk_bot, files_bot, rich,
                                                        run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    bot.registry.transition(sess.name, Status.IDLE)
    pic = _pic(tmp_path)

    async def go():
        await bot.notify(sess, "send_files_requested", {
            "tool_use_id": "toolu_x", "tool_input": {"files": [str(pic)]},
            "cwd": str(tmp_path)})
    run_async(go())
    assert outbox.OUTBOX.requests == {}


def test_a_send_with_no_request_and_no_turn_is_dropped(mk_bot, files_bot, rich,
                                                       run_async, tmp_path):
    """A lone PostToolUse for an idle session (no PreToolUse seen, no turn
    or job running): nothing is copied, one FileFailed record per file."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    bot.registry.transition(sess.name, Status.IDLE)
    pic = _pic(tmp_path)

    async def go():
        _claude_writes_the_call(sess, "toolu_x", {"files": [str(pic)]})
        await bot.notify(sess, "send_files_ran", {
            "tool_use_id": "toolu_x", "tool_input": {"files": [str(pic)]},
            "cwd": str(tmp_path)})
    run_async(go())
    assert not outbox.OUTBOX.has_batches(sess.name)
    assert not outbox.outbox_dir().exists() or not [
        p for p in outbox.outbox_dir().rglob("*") if p.is_file()]
    failed = [r for r in _audit_records() if r["action"] == "FileFailed"]
    assert [(r["file_name"], r["reason"]) for r in failed] == [
        ("chart.png", outbox.CAUSE_UNVERIFIED)]


def test_a_send_with_no_request_during_a_turn_is_still_sent(mk_bot, files_bot, rich,
                                                            run_async, tmp_path):
    """The PreToolUse datagram was lost (a restart in between), the turn
    runs and the call is in the transcript: the PostToolUse's input goes."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        _claude_writes_the_call(sess, "toolu_x", inp)
        await bot.notify(sess, "send_files_ran", {
            "tool_use_id": "toolu_x", "tool_input": inp, "cwd": str(tmp_path)})
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert [c[0] for c in files_bot.file_calls()] == ["send_photo"]


# ---- orphan copies reply to their turn's prompt ------------------------------

def test_orphan_files_reply_to_the_prompt_of_their_turn(mk_bot, files_bot, rich,
                                                        run_async, tmp_path):
    """A copy no finish took (the turn ended with no finish of its own): the
    monitor's flush sends it as a reply to that turn's prompt."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        await outbox.OUTBOX.wait_in_flight(sess.name)
        bot.registry.transition(sess.name, Status.IDLE)
        sess.trigger_msg_id = 999      # a later prompt: not the reply target
        for b in outbox.OUTBOX.batches[sess.name]:
            b.created -= 120
        await bot.flush_turn_files(sess)
        await outbox.OUTBOX.settled(sess.name)
    run_async(go())
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == TRIGGER


def test_a_slow_copy_stays_with_its_own_turn(mk_bot, files_bot, rich, run_async,
                                             tmp_path, monkeypatch):
    """Turn one's copy is still running when turn two's file is copied and
    its turn ends: each file replies to its own turn's answer, once."""
    import asyncio
    real = outbox.copy_batch
    slow = threading.Event()

    def copy(session, chat_id, files, *a, **k):
        if any(f.endswith("first.png") for f in files):
            slow.wait(1.0)      # never set: a copy that takes a second
        return real(session, chat_id, files, *a, **k)
    monkeypatch.setattr(outbox, "copy_batch", copy)
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    first, second = _pic(tmp_path, "first.png"), _pic(tmp_path, "second.png")

    async def go():
        await _send_files(bot, sess, [first], tool_use_id="toolu_a")
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": "The first."})
        _new_turn(bot, sess)
        await _send_files(bot, sess, [second], tool_use_id="toolu_b")
        await asyncio.sleep(0.2)            # turn two's copy is done
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": "The second."})
        await outbox.OUTBOX.settled(sess.name)
    run_async(go())
    answers = _rich_ids(files_bot)
    sent = [(c[2]["photo"].filename, c[2]["reply_to_message_id"])
            for c in files_bot.file_calls()]
    assert sent == [("first.png", answers[0]), ("second.png", answers[1])]
    # Turn one's batch went to turn one's delivery only, not into the store.
    assert not outbox.OUTBOX.batches.get(sess.name)


def test_a_slow_copy_held_by_a_mute_is_sent_once(mk_bot, files_bot, rich, run_async,
                                                 tmp_path, monkeypatch):
    """The same, with both answers held by a flood mute: turn one's files
    wait behind its answer, turn two's behind its own, and once the mute
    lifts each file goes once, as a reply to its own answer."""
    import asyncio
    real = outbox.copy_batch
    slow = threading.Event()

    def copy(session, chat_id, files, *a, **k):
        if any(f.endswith("first.png") for f in files):
            slow.wait(1.0)
        return real(session, chat_id, files, *a, **k)
    monkeypatch.setattr(outbox, "copy_batch", copy)
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    first, second = _pic(tmp_path, "first.png"), _pic(tmp_path, "second.png")

    async def turns():
        MUTE.mute(CHAT, 600, source="test")
        await _send_files(bot, sess, [first], tool_use_id="toolu_a")
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": "The first."})
        _new_turn(bot, sess)
        await _send_files(bot, sess, [second], tool_use_id="toolu_b")
        await asyncio.sleep(0.2)
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": "The second."})
        await outbox.OUTBOX.settled(sess.name)
    run_async(turns())
    assert files_bot.file_calls() == []
    MUTE.clear()
    run_async(_settled(bot, sess, bot.flush_held_answers(sess)))
    answers = _rich_ids(files_bot)
    sent = [(c[2]["photo"].filename, c[2]["reply_to_message_id"])
            for c in files_bot.file_calls()]
    assert sorted(sent) == [("first.png", answers[0]), ("second.png", answers[1])]
    assert not outbox.OUTBOX.batches.get(sess.name)


def test_a_job_interim_answer_does_not_wait_for_the_upload(mk_bot, files_bot, rich,
                                                           run_async, tmp_path):
    import asyncio
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        files_bot.hold["send_photo"] = asyncio.Event()
        await _send_files(bot, sess, [_pic(tmp_path)])
        bot.registry.transition(sess.name, Status.IDLE)
        interim = asyncio.ensure_future(bot._handle_job_interim(
            sess, {"summary": "An agent still runs."}, time.time(), reply_to=TRIGGER))
        done, _ = await asyncio.wait([interim], timeout=3.0)
        files_bot.hold["send_photo"].set()
        await interim
        await outbox.OUTBOX.settled(sess.name)
        return interim in done
    assert run_async(go()), "the interim answer waited for the upload"
    [photo] = files_bot.file_calls()
    assert photo[2]["reply_to_message_id"] == _rich_ids(files_bot)[0]


def test_a_mute_during_a_flush_keeps_every_waiting_delivery(mk_bot, files_bot, rich,
                                                            run_async, tmp_path):
    """Two turns' files wait for a mute. The ban comes back with the first
    send of the flush: both deliveries are kept, and go out in order once
    it lifts."""
    from aipager.bot.flood import FloodMuted
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    sess.busy_msg_id = 700
    bot._edit_busy_rich = AsyncMock(return_value=True)
    first, second = _pic(tmp_path, "first.png"), _pic(tmp_path, "second.png")

    async def turns():
        MUTE.mute(CHAT, 600, source="test")
        await _send_files(bot, sess, [first], tool_use_id="toolu_a")
        await _finish(bot, sess, "")
        _new_turn(bot, sess)
        sess.busy_msg_id = 701
        await _send_files(bot, sess, [second], tool_use_id="toolu_b")
        await _finish(bot, sess, "")
    run_async(turns())
    assert files_bot.file_calls() == []
    MUTE.clear()
    files_bot.fail["send_photo"] = FloodMuted(600, CHAT)
    run_async(_settled(bot, sess, bot.flush_turn_files(sess)))
    assert files_bot.file_calls() == []
    del files_bot.fail["send_photo"]
    run_async(_settled(bot, sess, bot.flush_turn_files(sess)))
    assert [c[2]["photo"].filename for c in files_bot.file_calls()] == [
        "first.png", "second.png"]


# ---- what a forged datagram cannot create (rev-iter2-001) ----------------------

def test_a_send_after_a_hook_alone_made_the_session_busy_is_dropped(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """An idle session that a tool call's hook moved to BUSY (a forged
    PreToolUse does this by itself) has no turn opened by a prompt: the
    pair is dropped, even with the call in the transcript."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        await _finish(bot, sess, "Done.")
        bot.registry.transition(sess.name, Status.BUSY)   # no prompt
        _claude_writes_the_call(sess, "toolu_x", inp)
        await _forged_pair(bot, sess, pic, tool_use_id="toolu_x")
        await _finish(bot, sess, "")
    run_async(go())
    assert files_bot.file_calls() == []
    assert not [p for p in outbox.outbox_dir().rglob("*") if p.is_file()]
    failed = [r for r in _audit_records() if r["action"] == "FileFailed"]
    assert [(r["file_name"], r["reason"]) for r in failed] == [
        ("chart.png", outbox.CAUSE_UNVERIFIED)]


def test_a_call_already_copied_is_never_copied_again(mk_bot, files_bot, rich,
                                                     run_async, tmp_path):
    """The same call's datagrams again, inside the same turn (a replay that
    the turn rule does not stop): one send, the second pair dropped."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)

    async def go():
        await _send_files(bot, sess, [pic], tool_use_id="toolu_1")
        await _forged_pair(bot, sess, pic, tool_use_id="toolu_1")
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert [c[0] for c in files_bot.file_calls()] == ["send_photo"]
    failed = [r for r in _audit_records() if r["action"] == "FileFailed"]
    assert [(r["file_name"], r["reason"]) for r in failed] == [
        ("chart.png", outbox.CAUSE_UNVERIFIED)]
    assert sess.files_sent_ids == ["toolu_1"]


def test_a_call_written_before_the_turn_began_is_not_sent(mk_bot, files_bot, rich,
                                                          run_async, tmp_path):
    """A real call of an earlier turn (an hour old, never delivered): played
    again in this turn, it is not from this turn."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        tp = Path(sess.files_transcript)
        tp.parent.mkdir(parents=True, exist_ok=True)
        line = _claude_line({"type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": "toolu_old", "name": "SendUserFile",
            "input": inp}]}}, str(tmp_path))
        line["timestamp"] = "2026-01-01T00:00:00.000Z"
        with open(tp, "a") as fh:
            fh.write(json.dumps(line) + "\n")
        await _forged_pair(bot, sess, pic, tool_use_id="toolu_old")
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert files_bot.file_calls() == []


def test_a_call_from_another_folder_is_not_sent(mk_bot, files_bot, rich, run_async,
                                               tmp_path):
    """The call is Claude's, but the pair names another folder (where a
    relative name is another file): nothing is sent."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    pic = _pic(other)
    inp = {"files": ["chart.png"], "status": "normal"}

    async def go():
        _claude_writes_the_call(sess, "toolu_rel", inp, str(tmp_path))
        await bot.notify(sess, "send_files_requested",
                         {"tool_use_id": "toolu_rel", "tool_input": inp,
                          "cwd": str(other)})
        await bot.notify(sess, "send_files_ran",
                         {"tool_use_id": "toolu_rel", "tool_input": inp,
                          "cwd": str(other)})
        await _finish(bot, sess, "Done.")
    run_async(go())
    assert pic.exists()
    assert files_bot.file_calls() == []


def test_the_transcript_is_the_one_tracked_when_the_turn_began(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """A later datagram names another transcript of the same user (the
    daemon's tracked path follows it): the call there is not looked for."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}
    began = sess.files_transcript
    other = Path(began).parent / "0000aaaa-other.jsonl"

    async def go():
        sess.transcript_path = str(other)
        with open(other, "a") as fh:
            fh.write(json.dumps(_claude_line({"type": "assistant", "message": {
                "content": [{"type": "tool_use", "id": "toolu_o",
                             "name": "SendUserFile", "input": inp}]}},
                str(tmp_path))) + "\n")
        await _forged_pair(bot, sess, pic, tool_use_id="toolu_o")
        await _finish(bot, sess, "Done.")
    Path(began).parent.mkdir(parents=True, exist_ok=True)
    run_async(go())
    assert sess.files_transcript == began
    assert files_bot.file_calls() == []


# ---- a background agent's call the transcript has not caught up with -----------
# (rev-iter2-002)

def _job_open(sess, agent="a1b2c3"):
    sess.active_subagents[agent] = {"started_at": time.monotonic(),
                                    "agent_type": "general-purpose"}


def _sub_line(sess, agent, tid, inp, cwd):
    sub = (Path(sess.files_transcript).with_suffix("") / "subagents"
           / f"agent-{agent}.jsonl")
    sub.parent.mkdir(parents=True, exist_ok=True)
    with open(sub, "a") as fh:
        fh.write(json.dumps(_claude_line({"type": "assistant", "message": {
            "content": [{"type": "tool_use", "id": tid, "name": "SendUserFile",
                         "input": inp}]}}, cwd)) + "\n")


def test_a_background_agents_late_call_is_kept_while_its_job_runs(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """The agent's call is not in its transcript when the interim answer's
    delivery looks (longer than the wait): the batch is kept, not dropped,
    and goes once the call shows and the job finishes."""
    import asyncio
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        _job_open(sess)
        await bot.notify(sess, "send_files_requested",
                         {"tool_use_id": "toolu_sub", "tool_input": inp,
                          "cwd": str(tmp_path), "agent_id": "a1b2c3"})
        await bot.notify(sess, "send_files_ran",
                         {"tool_use_id": "toolu_sub", "tool_input": inp,
                          "cwd": str(tmp_path), "agent_id": "a1b2c3"})
        bot.registry.transition(sess.name, Status.IDLE)
        assert sess.files_turn_open    # the job keeps the turn open
        await bot._handle_job_interim(sess, {"summary": "An agent still runs."},
                                      time.time(), reply_to=TRIGGER)
        await outbox.OUTBOX.settled(sess.name)
        kept = outbox.OUTBOX.has_batches(sess.name)
        # Claude Code writes the agent's transcript; orphans are still not
        # adopted while the job runs.
        _sub_line(sess, "a1b2c3", "toolu_sub", inp, str(tmp_path))
        for b in outbox.OUTBOX.batches.get(sess.name, ()):
            b.created -= 120
        await _settled(bot, sess, bot.flush_turn_files(sess))
        sent_early = list(files_bot.file_calls())
        # The agent finishes.
        sess.active_subagents.clear()
        await _settled(bot, sess, bot.flush_turn_files(sess))
        await asyncio.sleep(0)
        return kept, sent_early
    kept, sent_early = run_async(go())
    assert kept and sent_early == []
    assert [c[0] for c in files_bot.file_calls()] == ["send_photo"]
    assert [r for r in _audit_records() if r["action"] == "FileFailed"] == []


def test_a_late_call_is_dropped_once_nothing_runs(mk_bot, files_bot, rich,
                                                  run_async, tmp_path):
    """Kept while the job ran; the job is over and the call never showed:
    dropped then, with its FileFailed record."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        _job_open(sess)
        await bot.notify(sess, "send_files_ran",
                         {"tool_use_id": "toolu_sub", "tool_input": inp,
                          "cwd": str(tmp_path), "agent_id": "a1b2c3"})
        bot.registry.transition(sess.name, Status.IDLE)
        await bot._handle_job_interim(sess, {"summary": "An agent still runs."},
                                      time.time(), reply_to=TRIGGER)
        await outbox.OUTBOX.settled(sess.name)
        sess.active_subagents.clear()
        for b in outbox.OUTBOX.batches.get(sess.name, ()):
            b.created -= 120
        await _settled(bot, sess, bot.flush_turn_files(sess))
    run_async(go())
    assert files_bot.file_calls() == []
    assert not outbox.OUTBOX.has_batches(sess.name)
    failed = [r for r in _audit_records() if r["action"] == "FileFailed"]
    assert [(r["file_name"], r["reason"]) for r in failed] == [
        ("chart.png", outbox.CAUSE_UNVERIFIED)]


# ---- a newer turn's files never overtake older waiting ones (rev-iter2-003) -------

def test_a_newer_turns_files_wait_behind_older_ones_after_a_mute(
        mk_bot, files_bot, rich, run_async, tmp_path):
    """Turn 1's files wait out a mute; the mute lifts and turn 2 finishes
    before the monitor's flush: turn 1's go first."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    first, second = _pic(tmp_path, "first.png"), _pic(tmp_path, "second.png")

    async def go():
        await _send_files(bot, sess, [first], tool_use_id="toolu_a")
        MUTE.mute(CHAT, 600, source="test")
        await _finish(bot, sess, "")
        assert outbox.OUTBOX.has_pending(sess.name)
        MUTE.clear()
        _new_turn(bot, sess)
        await _send_files(bot, sess, [second], tool_use_id="toolu_b")
        await _finish(bot, sess, "")
    run_async(go())
    names = [c[2]["photo"].filename for c in files_bot.file_calls()]
    assert names == ["first.png", "second.png"]
    assert not outbox.OUTBOX.has_pending(sess.name)


# ---- orphans: each turn's own prompt ----------------------------------------------

def test_orphans_of_two_turns_each_reply_to_their_own_prompt(
        mk_bot, files_bot, rich, run_async, tmp_path):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    first, second = _pic(tmp_path, "first.png"), _pic(tmp_path, "second.png")

    async def go():
        sess.trigger_msg_id = 21
        await _send_files(bot, sess, [first], tool_use_id="toolu_a")
        await outbox.OUTBOX.wait_in_flight(sess.name)
        sess.trigger_msg_id = 22
        await _send_files(bot, sess, [second], tool_use_id="toolu_b")
        await outbox.OUTBOX.wait_in_flight(sess.name)
        bot.registry.transition(sess.name, Status.IDLE)
        for b in outbox.OUTBOX.batches[sess.name]:
            b.created -= 120
        await _settled(bot, sess, bot.flush_turn_files(sess))
    run_async(go())
    got = [(c[2]["photo"].filename, c[2]["reply_to_message_id"])
           for c in files_bot.file_calls()]
    assert got == [("first.png", 21), ("second.png", 22)]


def test_a_send_whose_turn_ended_before_it_ran_is_dropped(mk_bot, files_bot, rich,
                                                          run_async, tmp_path):
    """The request came during the turn; the turn was over by its
    PostToolUse (a forged Stop in between, or a replay of the pair): not
    copied."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        _claude_writes_the_call(sess, "toolu_x", inp)
        await bot.notify(sess, "send_files_requested",
                         {"tool_use_id": "toolu_x", "tool_input": inp,
                          "cwd": str(tmp_path)})
        bot.registry.transition(sess.name, Status.IDLE)
        bot.registry.transition(sess.name, Status.BUSY)   # a hook, no prompt
        await bot.notify(sess, "send_files_ran",
                         {"tool_use_id": "toolu_x", "tool_input": inp,
                          "cwd": str(tmp_path)})
        await _finish(bot, sess, "")
    run_async(go())
    assert files_bot.file_calls() == []
    assert sess.files_sent_ids == []


def test_a_late_call_is_kept_while_a_tool_still_runs(mk_bot, files_bot, rich,
                                                     run_async, tmp_path):
    """No job, but a tool call of the session is still in flight when the
    delivery looks: the batch is kept, then sent once the call shows and
    the tool is done."""
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)
    pic = _pic(tmp_path)
    inp = {"files": [str(pic)], "status": "normal"}

    async def go():
        await _forged_pair(bot, sess, pic, tool_use_id="toolu_t")
        sess.pending_tool_started_at = time.monotonic()
        await _finish(bot, sess, "Done.")
        kept = outbox.OUTBOX.has_batches(sess.name)
        sess.pending_tool_started_at = None
        _claude_writes_the_call(sess, "toolu_t", inp)
        for b in outbox.OUTBOX.batches.get(sess.name, ()):
            b.created -= 120
        await _settled(bot, sess, bot.flush_turn_files(sess))
        return kept
    assert run_async(go()) is True
    assert [c[0] for c in files_bot.file_calls()] == ["send_photo"]


def test_a_waiting_delivery_that_raises_leaves_no_copy(mk_bot, files_bot, rich,
                                                       run_async, tmp_path,
                                                       monkeypatch):
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path)])
        MUTE.mute(CHAT, 600, source="test")
        await _finish(bot, sess, "")
        MUTE.clear()

        async def boom(*a, **k):
            raise RuntimeError("unexpected")
        monkeypatch.setattr(type(bot), "_send_turn_files", boom)
        await _settled(bot, sess, bot.flush_turn_files(sess))
    run_async(go())
    assert files_bot.file_calls() == []
    assert not [p for p in outbox.outbox_dir().rglob("*") if p.is_file()]


def test_a_cancelled_delivery_leaves_no_copy_claimed(mk_bot, files_bot, rich,
                                                    run_async, tmp_path,
                                                    monkeypatch):
    """Cancelled (a shutdown) while it waits for two claimed copies, one of
    which has ended by then: neither stays claimed."""
    import asyncio
    real = outbox.copy_batch
    gates = {"first": threading.Event(), "second": threading.Event()}

    def gated_copy(*a, **k):
        name = "first" if any("first" in f for f in a[2]) else "second"
        gates[name].wait(5)
        return real(*a, **k)
    monkeypatch.setattr(outbox, "copy_batch", gated_copy)
    bot = _bot(mk_bot, files_bot)
    sess = _sess(bot, tmp_path=tmp_path)

    async def go():
        await _send_files(bot, sess, [_pic(tmp_path, "first.png")],
                          tool_use_id="toolu_a")
        await _send_files(bot, sess, [_pic(tmp_path, "second.png")],
                          tool_use_id="toolu_b")
        bot.registry.transition(sess.name, Status.IDLE)
        await bot.notify(sess, "idle_prompt", {"summary": "Done."})
        task = outbox.OUTBOX.deliveries[sess.name]
        await asyncio.sleep(0.05)
        claimed = len(outbox.OUTBOX.claimed)
        gates["first"].set()
        for _ in range(250):            # the first copy ends (at most 5 s)
            if len(outbox.OUTBOX.in_flight.get(sess.name, ())) <= 1:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.wait([task])
        gates["second"].set()
        await outbox.OUTBOX.wait_in_flight(sess.name)
        return claimed
    assert run_async(go()) == 2
    assert outbox.OUTBOX.claimed == set()
