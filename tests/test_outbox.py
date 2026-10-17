"""The outbox of files Claude sends (aipager/bot/outbox.py): the copy and
its second check, the private folders, the sorting into Telegram sends,
the caption, the day sweep."""

from __future__ import annotations

import os
import stat
import struct
import time
from pathlib import Path

import pytest

from aipager import config, send_check
from aipager.bot import outbox as ob

CHAT = -1001234


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(config, "BOT_TOKEN", "123:TEST")
    return h


def _png_bytes(w=64, h=48):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
            + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00" + b"\x00" * 64)


def _file(path: Path, data=b"hello") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _item(method, size=1000, name="f"):
    return ob.Item(name=name, path=Path("/nonexistent") / name, size=size,
                   kind="", width=None, height=None, method=method)


# ---- the copy ----------------------------------------------------------------

def test_copy_folder_and_file_are_private(home, tmp_path):
    src = _file(tmp_path / "proj" / "chart.png", _png_bytes())
    item = ob.copy_one(src, None, CHAT, None)
    assert item.path.read_bytes() == _png_bytes()
    assert stat.S_IMODE(os.stat(item.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(item.path.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(item.path.parent.parent).st_mode) == 0o700
    assert item.path.parent.parent == Path(config.OUTBOX_DIR)
    assert (item.name, item.method, item.width, item.height) == ("chart.png", "photo", 64, 48)
    # A later change to the original changes nothing that is sent.
    Path(src).write_bytes(b"changed")
    assert item.path.read_bytes() == _png_bytes()


def test_a_name_clash_gets_a_new_name(home, tmp_path):
    a = ob.copy_one(_file(tmp_path / "a" / "x.txt", b"1"), None, CHAT, None)
    b = ob.copy_one(_file(tmp_path / "b" / "x.txt", b"2"), None, CHAT, None)
    assert a.path.name == "x.txt" and b.path.name == "x-2.txt"


def test_copy_rechecks_a_file_swapped_for_a_floor_link(home, tmp_path):
    creds = _file(home / ".claude" / ".credentials.json", b"{}")
    pic = tmp_path / "proj" / "chart.png"
    _file(pic, _png_bytes())
    pic.unlink()
    pic.symlink_to(creds)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(str(pic), None, CHAT, None)
    assert exc.value.cause == ob.CAUSE_NOT_ALLOWED
    assert not list(Path(config.OUTBOX_DIR).rglob("*.json"))


def test_copy_refuses_a_file_replaced_after_the_check(home, tmp_path, monkeypatch):
    src = tmp_path / "proj" / "chart.png"
    _file(src, _png_bytes())
    real_check = send_check.check_entry

    def check_then_swap(entry, cwd):
        out = real_check(entry, cwd)
        src.unlink()
        src.write_bytes(b"another file, written after the check")
        return out
    monkeypatch.setattr(send_check, "check_entry", check_then_swap)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(str(src), None, CHAT, None)
    assert exc.value.cause == ob.CAUSE_GONE
    assert not [p for p in Path(config.OUTBOX_DIR).rglob("*") if p.is_file()]


@pytest.mark.parametrize("make, cause", [
    (lambda d: str(d / "gone.png"), ob.CAUSE_GONE),
    (lambda d: _file(d / "e.txt", b""), ob.CAUSE_NOT_ALLOWED),
    (lambda d: _file(d / ".env", b"X=1"), ob.CAUSE_NOT_ALLOWED),
], ids=["missing", "empty", "secret"])
def test_copy_runs_the_send_checks_again(home, tmp_path, make, cause):
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(make(tmp_path), None, CHAT, None)
    assert exc.value.cause == cause


def test_copy_too_big(home, tmp_path):
    p = tmp_path / "big.bin"
    with open(p, "wb") as fh:
        fh.truncate(send_check.FILE_MAX_BYTES + 1)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(str(p), None, CHAT, None)
    assert exc.value.cause == ob.CAUSE_TOO_BIG


def test_copy_holds_a_member_to_its_root(home, tmp_path):
    root = home / "aipager-users" / "bob"
    inside = _file(root / "a.png", _png_bytes())
    outside = _file(tmp_path / "x.png", _png_bytes())
    bound, allowed = ob.allowed_root_for({"root": str(root)}, str(root))
    assert bound and allowed == os.path.realpath(root)
    assert ob.copy_one(inside, None, CHAT, None, bound=True, allowed_root=allowed)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(outside, None, CHAT, None, bound=True, allowed_root=allowed)
    assert exc.value.cause == ob.CAUSE_NOT_ALLOWED
    assert ob.allowed_root_for({}, "/x") == (False, None)


def test_copy_batch_keeps_order_and_failures(home, tmp_path):
    a = _file(tmp_path / "a.txt", b"a")
    b = str(tmp_path / "missing.txt")
    c = _file(tmp_path / "c.txt", b"c")
    batch = ob.copy_batch("claude-x", CHAT, [a, b, c], None, "cap", None)
    assert [i.name for i in batch.items] == ["a.txt", "c.txt"]
    assert [(f.name, f.cause) for f in batch.failures] == [("missing.txt", ob.CAUSE_GONE)]


def test_a_direction_control_in_a_name_is_shown(home, tmp_path):
    p = _file(tmp_path / "gpj‮.exe", b"x")
    assert ob.copy_one(p, None, CHAT, None).name == "gpj⟨U+202E⟩.exe"


# ---- which files a call names ---------------------------------------------

def test_response_paths_are_never_used():
    box = ob.Outbox()
    box.record_request("claude-x", "t1", {"files": ["/work/a.png"], "status": "normal",
                                          "caption": "hi"}, "/work")
    response = {"attachments": [{"path": "/home/alice/.ssh/id_rsa", "size": 3}]}
    req = box.files_for_ran("claude-x", "t1", {"files": ["/work/b.png"]}, response, "/x")
    assert (req.files, req.caption, req.cwd) == (["/work/a.png"], "hi", "/work")
    # No request (a restart in between): the PostToolUse's own input.
    req = box.files_for_ran("claude-x", "t2", {"files": "/work/c.png"}, response, "/x")
    assert req.files == ["/work/c.png"] and req.cwd == "/x"
    assert box.files_for_ran("claude-x", "t3", {}, response, "/x") is None


def test_requests_expire(monkeypatch):
    now = [1000.0]
    box = ob.Outbox(clock=lambda: now[0])
    box.record_request("s", "t1", {"files": ["a"]}, "/w")
    now[0] += ob.REQUEST_TTL_SECONDS + 1
    box.record_request("s", "t2", {"files": ["b"]}, "/w")
    assert box.take_request("s", "t1") is None
    assert box.take_request("s", "t2") is not None


# ---- sorting ---------------------------------------------------------------------

def test_eleven_photos_go_as_six_and_five():
    plan = ob.plan_sends([_item("photo", name=str(i)) for i in range(11)], "x1", None)
    assert [(p.method, len(p.items)) for p in plan] == [("media_group", 6), ("media_group", 5)]


def test_twenty_one_photos_go_as_seven_each():
    plan = ob.plan_sends([_item("photo") for _ in range(21)], "x1", None)
    assert [len(p.items) for p in plan] == [7, 7, 7]


def test_photos_and_videos_share_an_album():
    plan = ob.plan_sends([_item("photo"), _item("video"), _item("photo")], "x1", None)
    assert [(p.method, [i.method for i in p.items]) for p in plan] == [
        ("media_group", ["photo", "video", "photo"])]


def test_documents_and_audio_never_mix_with_photos():
    items = [_item("photo", name="p1"), _item("document", name="d1"),
             _item("audio", name="a1"), _item("photo", name="p2"),
             _item("document", name="d2"), _item("audio", name="a2")]
    plan = ob.plan_sends(items, "x1", None)
    assert [(p.method, [i.name for i in p.items]) for p in plan] == [
        ("media_group", ["p1", "p2"]), ("media_group", ["d1", "d2"]),
        ("media_group", ["a1", "a2"])]


def test_a_single_file_goes_by_its_own_method():
    assert [p.method for p in ob.plan_sends([_item("document")], "x1", None)] == ["document"]
    plan = ob.plan_sends([_item("photo"), _item("document")], "x1", None)
    assert [p.method for p in plan] == ["photo", "document"]


def test_animation_is_never_in_an_album():
    items = [_item("photo", name="p1"), _item("animation", name="g1"),
             _item("photo", name="p2"), _item("animation", name="g2")]
    plan = ob.plan_sends(items, "x1", None)
    assert [(p.method, [i.name for i in p.items]) for p in plan] == [
        ("media_group", ["p1", "p2"]), ("animation", ["g1"]), ("animation", ["g2"])]


def test_album_total_stays_under_50_mib():
    mib = 1024 * 1024
    items = [_item("document", size=20 * mib, name=str(i)) for i in range(4)]
    plan = ob.plan_sends(items, "x1", None)
    assert all(sum(i.size for i in p.items) <= ob.ALBUM_MAX_BYTES for p in plan)
    assert sum(len(p.items) for p in plan) == 4
    assert [len(p.items) for p in plan] == [2, 2]


# ---- the caption ---------------------------------------------------------------

def test_caption_is_the_session_then_claudes_caption_on_the_first_send():
    plan = ob.plan_sends([_item("photo"), _item("document")], "frontend", "the chart")
    assert [p.caption for p in plan] == ["💬 frontend\nthe chart", "💬 frontend"]
    assert ob.plan_sends([_item("photo")], "frontend", None)[0].caption == "💬 frontend"


def test_caption_cut_at_a_word_with_ellipsis():
    words = " ".join(["word"] * 400)
    cap = ob.caption_for("frontend", words, True)
    assert ob.utf16_len(cap) <= 1024
    assert cap.endswith("word…")
    assert cap.startswith("💬 frontend\nword word")
    # Emoji count as two units each.
    cap = ob.caption_for("x", "😀 " * 600, True)
    assert ob.utf16_len(cap) <= 1024 and cap.endswith("…")
    short = ob.caption_for("x", "fine", True)
    assert short == "💬 x\nfine"


# ---- the day sweep ---------------------------------------------------------------

def test_copies_older_than_a_day_are_swept(home, tmp_path):
    old = ob.copy_one(_file(tmp_path / "old.txt", b"o"), None, CHAT, None)
    new = ob.copy_one(_file(tmp_path / "new.txt", b"n"), None, CHAT, None)
    past = time.time() - ob.KEEP_SECONDS - 60
    os.utime(old.path, (past, past))
    assert ob.sweep_disk() == 1
    assert not old.path.exists() and new.path.exists()
    new.path.unlink()
    os.utime(new.path.parent, (past, past))
    ob.sweep_disk()
    assert not new.path.parent.exists()


def test_sweep_gives_up_old_deliveries(home, tmp_path):
    now = [10_000.0]
    box = ob.Outbox(clock=lambda: now[0])
    item = ob.copy_one(_file(tmp_path / "a.txt", b"a"), None, CHAT, None)
    batch = ob.Batch(session="s", caption=None, display=None, items=[item],
                     agent_id="", created=now[0])
    box.queue(ob.Pending(session="s", chat_id=CHAT, anchor=5, held_key=None,
                         prompt_id=4, batches=[batch], created=now[0]))
    assert box.sweep() == []
    now[0] += ob.KEEP_SECONDS + 1
    expired = box.sweep()
    assert [p.session for p in expired] == ["s"]
    assert not item.path.exists()
    assert box.pending == {}


def test_held_anchor_moves_when_the_answer_lands_or_goes():
    box = ob.Outbox()
    for key in ("k1", "k2"):
        box.queue(ob.Pending(session="s", chat_id=CHAT, anchor=None, held_key=key,
                             prompt_id=3, batches=[], created=0.0))
    assert box.due(CHAT) == []
    box.answer_landed("k1", 77)
    box.answer_gone("k2")
    due = box.due(CHAT)
    assert [p.anchor for p in due] == [77, 3]
    assert box.due(CHAT) == []
