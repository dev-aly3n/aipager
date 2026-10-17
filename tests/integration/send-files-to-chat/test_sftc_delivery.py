"""Delivery after the answer, driven through the daemon's control-socket
entry (HookReceiver datagrams into bot.notify) with a recording fake bot
(design.md criteria "After the answer lands ...", albums, caption, the
failure line, audit, copies; entrypoints.md Events and Side effects)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys

import pytest
from telegram.error import BadRequest

from aipager import audit

kit = sys.modules["_sftc_kit"]


@pytest.fixture(autouse=True)
def _owner():
    kit.use_role("owner")


@pytest.fixture
def pics(proj):
    for i in range(12):
        (proj / f"p{i}.png").write_bytes(kit.png(64 + i, 48))
    (proj / "a.gif").write_bytes(kit.gif())
    (proj / "a.mp4").write_bytes(kit.mp4())
    (proj / "a.mp3").write_bytes(kit.mp3())
    (proj / "b.pdf").write_bytes(b"%PDF-1.4 second")
    return proj


def _one(t, method):
    calls = t.calls(method)
    assert len(calls) == 1, (method, [c[0] for c in t.log])
    return calls[0]


def _photo_turn(turn, pics, **kw):
    return turn([{"files": [str(pics / "p0.png")], **kw}])


# ---- the right method -------------------------------------------------------

@pytest.mark.parametrize("name,method", [
    ("p0.png", "send_photo"), ("a.gif", "send_animation"), ("a.mp4", "send_video"),
    ("a.mp3", "send_audio"), ("report.pdf", "send_document")],
    ids=["png", "gif", "mp4", "mp3", "pdf"])
def test_file_goes_by_its_type(turn, pics, name, method):
    t = turn([{"files": [str(pics / name)]}])
    assert [c[0] for c in t.sends()] == [method]


def test_attach_sends_a_photo_as_a_document(turn, pics):
    t = turn([{"files": [str(pics / "p0.png")], "display": "attach"}])
    assert [c[0] for c in t.sends()] == ["send_document"]


def test_files_as_a_string_are_delivered(turn, pics):
    t = turn([{"files": str(pics / "p0.png")}])
    assert [c[0] for c in t.sends()] == ["send_photo"]


def test_three_photos_go_as_one_album(turn, pics):
    t = turn([{"files": [str(pics / f"p{i}.png") for i in range(3)]}])
    _m, a, k, _ = _one(t, "send_media_group")
    assert len(k.get("media", a[1] if len(a) > 1 else [])) == 3


def test_eleven_photos_go_as_two_albums(turn, pics):
    t = turn([{"files": [str(pics / f"p{i}.png") for i in range(11)]}])
    sizes = [len(k.get("media", a[1] if len(a) > 1 else []))
             for _m, a, k, _ in t.calls("send_media_group")]
    assert sizes == [6, 5]


def test_photo_and_document_go_separately(turn, pics):
    t = turn([{"files": [str(pics / "p0.png"), str(pics / "report.pdf")]}])
    assert [c[0] for c in t.sends()] == ["send_photo", "send_document"]


def test_files_of_two_calls_in_one_turn_are_all_delivered(turn, pics):
    t = turn([{"files": [str(pics / "report.pdf")]},
              {"files": [str(pics / "b.pdf")]}])
    assert len(t.sends()) >= 1 and sum(
        len(k.get("media", [])) if m == "send_media_group" else 1
        for m, _a, k, _ in t.sends()) == 2


# ---- where, when, how --------------------------------------------------------

def _chat(call):
    _m, a, k, _ = call
    return k.get("chat_id", a[0] if a else None)


def test_files_go_to_the_sessions_own_chat(turn, pics):
    t = _photo_turn(turn, pics)
    assert _chat(_one(t, "send_photo")) == 4242


def test_files_are_silent(turn, pics):
    t = _photo_turn(turn, pics)
    assert _one(t, "send_photo")[2].get("disable_notification") is True


def test_files_reply_to_the_answer(turn, pics):
    t = _photo_turn(turn, pics)
    k = _one(t, "send_photo")[2]
    target = k.get("reply_to_message_id")
    if target is None and k.get("reply_parameters") is not None:
        target = k["reply_parameters"].message_id
    assert target is not None and target == t.answer_id()


def test_files_come_after_the_answer(turn, pics):
    t = _photo_turn(turn, pics)
    order = [c[3] for c in t.log]
    photo = _one(t, "send_photo")[3]
    assert t.answer_id() is not None and order.index(t.answer_id()) < order.index(photo)


def test_file_send_allows_sending_without_reply(turn, pics):
    k = _one(_photo_turn(turn, pics), "send_photo")[2]
    allowed = k.get("allow_sending_without_reply")
    if allowed is None and k.get("reply_parameters") is not None:
        allowed = k["reply_parameters"].allow_sending_without_reply
    assert allowed is True


def test_file_send_uses_a_300_second_write_timeout(turn, pics):
    assert _one(_photo_turn(turn, pics), "send_photo")[2].get("write_timeout") == 300


def test_album_send_is_silent_with_the_long_timeout(turn, pics):
    t = turn([{"files": [str(pics / f"p{i}.png") for i in range(2)]}])
    k = _one(t, "send_media_group")[2]
    assert (k.get("disable_notification"), k.get("write_timeout")) == (True, 300)


def test_caption_is_the_session_label(turn, pics):
    assert _one(_photo_turn(turn, pics), "send_photo")[2].get("caption") == "💬 frontend"


def test_claude_caption_follows_the_label(turn, pics):
    t = _photo_turn(turn, pics, caption="the chart")
    assert _one(t, "send_photo")[2].get("caption") == "💬 frontend\nthe chart"


def test_caption_is_plain_text(turn, pics):
    t = turn([{"files": [str(pics / "p0.png")], "caption": "<b>bold</b> & co"}],
             label="front<end>")
    k = _one(t, "send_photo")[2]
    assert (k.get("parse_mode"), k.get("caption")) == (
        None, "💬 front<end>\n<b>bold</b> & co")


def test_album_caption_is_on_the_first_item_only(turn, pics):
    t = turn([{"files": [str(pics / f"p{i}.png") for i in range(3)],
               "caption": "three"}])
    _m, a, k, _ = _one(t, "send_media_group")
    media = k.get("media", a[1] if len(a) > 1 else [])
    assert [getattr(x, "caption", None) for x in media] == [
        "💬 frontend\nthree", None, None]


# ---- what is sent is what was checked ---------------------------------------

def _sent_bytes(call):
    k = call[2]
    f = k.get("photo") or k.get("document")
    content = getattr(f, "input_file_content", None)
    if content is None and hasattr(f, "read"):
        content = f.read()
    return content


def test_a_later_edit_does_not_change_what_is_sent(turn, pics):
    original = (pics / "report.pdf").read_bytes()
    t = turn([{"files": [str(pics / "report.pdf")],
               "after": lambda: (pics / "report.pdf").write_bytes(b"%PDF-1.4 EDITED")}])
    assert _sent_bytes(_one(t, "send_document")) == original


def test_response_paths_are_never_sent(turn, pics, home):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "config").write_text("Host x")
    t = turn([{"files": [str(pics / "p0.png")],
               "response_paths": [str(home / ".ssh" / "config")]}])
    assert [c[0] for c in t.sends()] == ["send_photo"]


def test_floor_file_in_a_forged_post_is_never_sent(turn, pics, home):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("-----BEGIN KEY-----")
    t = turn([{"files": [str(home / ".ssh" / "id_rsa")]}])
    assert t.sends() == []


def test_file_swapped_for_a_floor_link_after_the_check_is_not_sent(turn, pics, home):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "config").write_text("Host x")
    target = pics / "report.pdf"

    def swap():
        target.unlink()
        target.symlink_to(home / ".ssh" / "config")
    t = turn([{"files": [str(target)], "between": swap}])
    assert t.sends() == []


def test_member_file_outside_her_root_is_not_sent_at_copy(turn, root, outside):
    kit.use_role("user", root)
    t = turn([{"files": [str(outside / "x.png")]}], cwd=root / "proj")
    assert t.sends() == []


# ---- failures ------------------------------------------------------------------

def test_failed_file_gets_one_line(turn, pics):
    t = turn([{"files": [str(pics / "p0.png")]}], send_error=BadRequest("Wrong file"))
    assert len([x for x in t.texts() if "Couldn't send" in x]) == 1


def test_failed_file_line_text(turn, pics):
    t = turn([{"files": [str(pics / "p0.png")]}], send_error=BadRequest("Wrong file"))
    assert "⚠️ Couldn't send p0.png: Telegram refused it." in t.texts()


def test_refused_file_gets_no_chat_line(turn, pics):
    # A refused call never runs, so Claude Code sends no PostToolUse; the
    # hook sends the refused datagram instead.
    t = turn([{"files": [str(pics / "missing.png")], "refused": [
        {"path": str(pics / "missing.png"), "code": "missing",
         "reason": "no such file"}]}])
    assert [x for x in t.texts() if "Couldn't send" in x] == []


# ---- audit and copies -----------------------------------------------------------

def _audit():
    p = audit.AUDIT_LOG_PATH
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_each_sent_file_adds_one_file_sent_record(turn, pics):
    turn([{"files": [str(pics / "p0.png"), str(pics / "report.pdf")]}])
    assert sorted(r.get("file_name") for r in _audit()
                  if r.get("action") == "FileSent") == ["p0.png", "report.pdf"]


def test_file_sent_record_has_size_and_tool(turn, pics):
    turn([{"files": [str(pics / "report.pdf")]}])
    rec = next(r for r in _audit() if r.get("action") == "FileSent")
    assert (rec.get("tool"), rec.get("file_size")) == (
        "SendUserFile", (pics / "report.pdf").stat().st_size)


def test_failed_file_adds_one_file_failed_record(turn, pics):
    turn([{"files": [str(pics / "p0.png")]}], send_error=BadRequest("Wrong file"))
    assert len([r for r in _audit() if r.get("action") == "FileFailed"]) == 1


def _outbox(_home):
    from aipager import config
    return config.OUTBOX_DIR


def _files_under(p):
    return [os.path.join(d, f) for d, _s, fs in os.walk(p) for f in fs]


def test_copy_was_made_in_the_outbox(turn, pics, home):
    seen = []

    async def look():
        for _ in range(40):
            if _files_under(_outbox(home)):
                seen.append(True)
                return
            await asyncio.sleep(0.05)
    turn([{"files": [str(pics / "report.pdf")], "after": look}])
    assert seen == [True]


def test_copy_is_deleted_once_sent(turn, pics, home):
    turn([{"files": [str(pics / "report.pdf")]}])
    assert _files_under(_outbox(home)) == []


def test_copy_is_deleted_once_failed(turn, pics, home):
    turn([{"files": [str(pics / "p0.png")]}], send_error=BadRequest("Wrong file"))
    assert _files_under(_outbox(home)) == []


def test_copy_folder_and_file_are_private(turn, pics, home):
    seen = {}

    async def look():
        for _ in range(40):
            files = _files_under(_outbox(home))
            if files:
                f = files[0]
                seen["file"] = stat.S_IMODE(os.stat(f).st_mode)
                seen["dir"] = stat.S_IMODE(os.stat(os.path.dirname(f)).st_mode)
                return
            await asyncio.sleep(0.05)
    turn([{"files": [str(pics / "report.pdf")], "after": look}])
    assert seen == {"file": 0o600, "dir": 0o700}


def test_source_file_is_left_in_place(turn, pics):
    turn([{"files": [str(pics / "report.pdf")]}])
    assert (pics / "report.pdf").exists()


def test_files_arrive_in_a_turn_without_an_answer(turn, pics):
    t = turn([{"files": [str(pics / "report.pdf")]}], answer=None)
    assert [c[0] for c in t.sends()] == ["send_document"]


def test_file_without_an_answer_still_goes_to_the_own_chat(turn, pics):
    t = turn([{"files": [str(pics / "report.pdf")]}], answer=None)
    assert _chat(_one(t, "send_document")) == 4242


def test_member_file_inside_her_root_is_sent(turn, root):
    kit.use_role("user", root)
    t = turn([{"files": [str(root / "other" / "notes.md")]}], cwd=root / "proj")
    assert [c[0] for c in t.sends()] == ["send_document"]
