"""The outbox of files Claude sends (aipager/bot/outbox.py): the copy and
its second check, the private folders, the sorting into Telegram sends,
the caption, the day sweep."""

from __future__ import annotations

import json
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
    real_check = send_check.check_resolved

    def check_then_swap(spelled, real):
        out = real_check(spelled, real)
        src.unlink()
        src.write_bytes(b"another file, written after the check")
        return out
    monkeypatch.setattr(send_check, "check_resolved", check_then_swap)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(str(src), None, CHAT, None)
    assert exc.value.cause == ob.CAUSE_GONE
    assert not [p for p in Path(config.OUTBOX_DIR).rglob("*") if p.is_file()]


def test_copy_refuses_a_file_hard_linked_after_the_check(home, tmp_path, monkeypatch):
    """The same file, given a second hard link after the check: the open
    file's own stat is checked, so it is refused."""
    src = tmp_path / "proj" / "chart.png"
    _file(src, _png_bytes())
    real_check = send_check.check_resolved

    def check_then_link(spelled, real):
        out = real_check(spelled, real)
        os.link(src, tmp_path / "proj" / "second-name.png")
        return out
    monkeypatch.setattr(send_check, "check_resolved", check_then_link)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(str(src), None, CHAT, None)
    assert exc.value.cause == ob.CAUSE_NOT_ALLOWED
    assert not [p for p in Path(config.OUTBOX_DIR).rglob("*") if p.is_file()]


def test_copy_judges_the_path_it_resolved_not_a_second_resolution(
        home, tmp_path, monkeypatch):
    """rev-iter1-001: a link that leads to a key when the copy resolves it,
    swapped for a harmless file right after, must not get the key copied:
    every check is about the one resolution, which is what is opened."""
    from aipager import safety
    proj = tmp_path / "proj"
    secret = Path(_file(proj / "id_rsa", b"PRIVATE KEY"))
    benign = Path(_file(proj / "ok.txt", b"hello"))
    link = proj / "pic.png"
    link.symlink_to(secret)
    real_realpath = safety._realpath
    swapped = []

    def resolve_then_swap(path):
        out = real_realpath(path)
        if path == str(link) and not swapped:
            swapped.append(out)
            link.unlink()
            link.symlink_to(benign)
        return out
    monkeypatch.setattr(safety, "_realpath", resolve_then_swap)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(str(link), None, CHAT, None)
    assert swapped == [str(secret)]
    assert exc.value.cause == ob.CAUSE_NOT_ALLOWED
    copies = [p for p in Path(config.OUTBOX_DIR).rglob("*") if p.is_file()]
    assert not any(p.read_bytes() == b"PRIVATE KEY" for p in copies)


def test_copy_link_swapped_inside_the_check_sends_what_was_checked(
        home, tmp_path, monkeypatch):
    """The reviewer's probe the other way round: a link to a harmless file,
    swapped to a key while the check runs. The copy opens the path the
    check judged (the harmless file), never the key."""
    proj = tmp_path / "proj"
    secret = Path(_file(proj / "id_rsa", b"PRIVATE KEY"))
    benign = Path(_file(proj / "ok.txt", b"hello"))
    link = proj / "pic.png"
    link.symlink_to(benign)
    real_check = send_check.check_resolved

    def racing_check(spelled, real):
        link.unlink()
        link.symlink_to(secret)
        return real_check(spelled, real)
    monkeypatch.setattr(send_check, "check_resolved", racing_check)
    item = ob.copy_one(str(link), None, CHAT, None)
    assert item.path.read_bytes() == b"hello"


def test_copy_refuses_a_folder_swapped_for_a_link_after_the_check(
        home, tmp_path, monkeypatch):
    """The same file (same inode, same size), reached through a folder that
    became a link after the check: the open file is not at the checked
    path, so nothing is copied (the /proc/self/fd compare)."""
    proj = tmp_path / "proj"
    src = Path(_file(proj / "sub" / "chart.png", _png_bytes()))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real_check = send_check.check_resolved

    def check_then_move(spelled, real):
        out = real_check(spelled, real)
        os.rename(proj / "sub", elsewhere / "sub")
        (proj / "sub").symlink_to(elsewhere / "sub")
        return out
    monkeypatch.setattr(send_check, "check_resolved", check_then_move)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(str(src), None, CHAT, None)
    assert exc.value.cause == ob.CAUSE_GONE
    assert not [p for p in Path(config.OUTBOX_DIR).rglob("*") if p.is_file()]


def test_copy_root_is_judged_on_the_one_resolution(home, tmp_path, monkeypatch):
    """A member's file: a link that leads outside her root when the copy
    resolves it, swapped to lead inside right after, is refused."""
    from aipager import safety
    root = home / "aipager-users" / "bob"
    inside = Path(_file(root / "a.png", _png_bytes()))
    outside = Path(_file(tmp_path / "x.png", _png_bytes()))
    link = root / "pic.png"
    link.symlink_to(outside)
    bound, allowed = ob.allowed_root_for({"root": str(root)}, str(root))
    real_realpath = safety._realpath
    swapped = []

    def resolve_then_swap(path):
        out = real_realpath(path)
        if path == str(link) and not swapped:
            swapped.append(out)
            link.unlink()
            link.symlink_to(inside)
        return out
    monkeypatch.setattr(safety, "_realpath", resolve_then_swap)
    with pytest.raises(ob.CopyFailed) as exc:
        ob.copy_one(str(link), None, CHAT, None, bound=bound, allowed_root=allowed)
    assert swapped == [str(outside)]
    assert exc.value.cause == ob.CAUSE_NOT_ALLOWED


def test_a_long_name_is_cut_by_bytes_on_disk(home, tmp_path):
    """200 three-byte letters are 600 UTF-8 bytes: the copy's name on disk
    is cut to fit the file system (on a letter boundary), its suffix kept;
    the name shown in the chat stays whole."""
    long = "\u0641" * 150 + ".png"       # Arabic letter feh, 2 bytes each
    long3 = "\u4e2d" * 150 + ".png"      # a CJK letter, 3 bytes each
    for name in (long, long3, "a" * 300, "\u4e2d" * 300):
        cut = ob.disk_name(name)
        assert len(cut.encode("utf-8")) <= ob.DISK_NAME_MAX_BYTES
        stem = cut[:-4] if name.endswith(".png") else cut
        assert stem and name.startswith(stem)
    assert ob.disk_name(long3).endswith(".png")
    assert ob.disk_name("short.png") == "short.png"
    # A clash suffix still fits in 255 bytes.
    folder = tmp_path / "clash"
    folder.mkdir()
    from aipager import uploads
    first = uploads.reserve_file(folder, ob.disk_name(long3))
    second = uploads.reserve_file(folder, ob.disk_name(long3))
    assert first != second and len(second.name.encode("utf-8")) <= 255


def test_copy_of_a_long_name_reaches_the_disk(home, tmp_path, monkeypatch):
    """The copy itself: a shown name of 200 three-byte letters (600 bytes)
    gets a disk name that the file system takes."""
    src = _file(tmp_path / "n" / "x.png", _png_bytes())
    monkeypatch.setattr(ob, "shown_name", lambda entry: "\u4e2d" * 200 + ".png")
    item = ob.copy_one(src, None, CHAT, None)
    assert item.name == "\u4e2d" * 200 + ".png"
    assert len(item.path.name.encode("utf-8")) <= 255
    assert item.path.read_bytes() == _png_bytes()


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
    p = _file(tmp_path / "gpj\u202e.exe", b"x")
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


# ---- the delivery ---------------------------------------------------------------

class _Bot:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def _m(*args, **kw):
            self.calls.append((name, args, kw))
            return None
        return _m


def _pending(home, tmp_path, names, caption=None):
    items = [ob.copy_one(_file(tmp_path / n, _png_bytes() if n.endswith(".png") else b"x"),
                         None, CHAT, None) for n in names]
    batch = ob.Batch(session="claude-x", caption=caption, display=None, items=list(items),
                     agent_id="", created=0.0)
    ctx = {"session": "claude-x", "label": "x", "user_id": 7, "username": "alice",
           "scope_label": "team", "scope_chat_id": CHAT}
    return ob.Pending(session="claude-x", chat_id=CHAT, anchor=42, held_key=None,
                      prompt_id=41, batches=[batch], created=0.0, audit=ctx), items


def test_each_sent_file_adds_one_audit_record(home, tmp_path, run_async):
    import json as _json
    from aipager import audit, i18n
    pending, items = _pending(home, tmp_path, ["a.png", "b.png", "c.txt"])
    bot = _Bot()
    assert run_async(ob.deliver(bot, pending, "x", i18n.get("en"))) is True
    assert [c[0] for c in bot.calls] == ["send_media_group", "send_document"]
    records = [_json.loads(x) for x in audit.AUDIT_LOG_PATH.read_text().splitlines()]
    assert [(r["action"], r["tool"], r["file_name"], r["file_size"], r["username"])
            for r in records] == [
        ("FileSent", "SendUserFile", "a.png", items[0].size, "alice"),
        ("FileSent", "SendUserFile", "b.png", items[1].size, "alice"),
        ("FileSent", "SendUserFile", "c.txt", 1, "alice")]


def test_copy_deleted_after_send(home, tmp_path, run_async):
    from aipager import i18n
    pending, items = _pending(home, tmp_path, ["a.png"])
    assert items[0].path.exists()
    run_async(ob.deliver(_Bot(), pending, "x", i18n.get("en")))
    assert not items[0].path.exists()
    assert pending.batches == []


def test_a_mute_keeps_the_rest(home, tmp_path, run_async):
    from aipager import i18n
    from aipager.bot.flood import FloodMuted
    pending, items = _pending(home, tmp_path, ["a.png", "b.txt"], caption="cap")

    class _Muting(_Bot):
        async def send_document(self, *a, **k):
            raise FloodMuted(60, CHAT)
    bot = _Muting()
    assert run_async(ob.deliver(bot, pending, "x", i18n.get("en"))) is False
    assert [c[0] for c in bot.calls] == ["send_photo"]
    assert bot.calls[0][2]["caption"] == "💬 x\ncap"
    assert [i.name for i in pending.batches[0].items] == ["b.txt"]
    assert items[1].path.exists() and not items[0].path.exists()
    # Claude's caption went with the first send only.
    assert pending.batches[0].caption is None


def test_newest_held_key():
    from aipager.bot.held import HeldAnswers
    held = HeldAnswers()
    assert held.newest_key(CHAT, "s") is None
    a = held.hold(chat_id=CHAT, session="s", label="s", rich_text="a", plain_text="a")
    held.hold(chat_id=CHAT, session="t", label="t", rich_text="b", plain_text="b")
    assert held.newest_key(CHAT, "s") == a.key
    b = held.hold(chat_id=CHAT, session="s", label="s", rich_text="c", plain_text="c")
    assert held.newest_key(CHAT, "s") == b.key and held.has_key(CHAT, a.key)


def test_each_refused_file_adds_one_audit_record(mk_bot, run_async):
    import json as _json
    from aipager import audit
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-x")
    run_async(bot.notify(sess, "send_files_refused", {"tool_use_id": "t", "files": [
        {"path": "/home/alice/.ssh/id_rsa", "code": "floor", "reason": "a protected file"},
        {"path": "b.png", "code": "missing", "reason": "no such file"}]}))
    records = [_json.loads(x) for x in audit.AUDIT_LOG_PATH.read_text().splitlines()]
    assert [(r["action"], r["tool"], r["file_name"], r["denied"], r["reason"])
            for r in records] == [
        ("FileRefused", "SendUserFile", "id_rsa", True, "floor: a protected file"),
        ("FileRefused", "SendUserFile", "b.png", True, "missing: no such file")]
    assert bot._app.bot.send_message.await_count == 0


# ---- the call in the transcript (rev-iter1-002) --------------------------------

def _transcript(home, name="5b1d.jsonl", lines=()):
    tp = home / ".claude" / "projects" / "-proj" / name
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return tp


#: The turn the calls below are from: it started a minute ago, from /w.
CWD = "/w"


def _since():
    return time.time() - 60


def _iso(wall):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(wall, timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _call(tid, files, name="SendUserFile", cwd=CWD, wall=None):
    """A line as Claude Code writes it: with its cwd and timestamp."""
    line = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Here."},
        {"type": "tool_use", "id": tid, "name": name, "input": {"files": files}}]}}
    if cwd is not None:
        line["cwd"] = cwd
    if wall is not False:
        line["timestamp"] = _iso(time.time() if wall is None else wall)
    return line


def _in(tp, tid, files, cwd=CWD, since=None):
    return ob.call_in_transcript(str(tp), tid, files, cwd,
                                 _since() if since is None else since)


def test_transcript_allowed_only_under_claudes_projects(home, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    good = _transcript(home)
    assert ob.transcript_allowed(str(good))
    planted = tmp_path / "x.jsonl"
    planted.write_text("")
    assert not ob.transcript_allowed(str(planted))
    # A link inside the folder that leads out of it.
    link = good.parent / "link.jsonl"
    link.symlink_to(planted)
    assert not ob.transcript_allowed(str(link))
    other = good.parent / "notes.txt"
    other.write_text("")
    assert not ob.transcript_allowed(str(other))
    assert not ob.transcript_allowed("")
    assert not ob.transcript_allowed(str(good.parent / "missing.jsonl"))
    # $CLAUDE_CONFIG_DIR is Claude's folder too.
    cfg = tmp_path / "cfg"
    alt = cfg / "projects" / "-p" / "a.jsonl"
    alt.parent.mkdir(parents=True)
    alt.write_text("")
    assert not ob.transcript_allowed(str(alt))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    assert ob.transcript_allowed(str(alt))


def test_transcript_owned_by_another_user_is_not_read(home, monkeypatch):
    tp = _transcript(home)
    monkeypatch.setattr(ob, "_own_uid", lambda: os.geteuid() + 1)
    assert not ob.transcript_allowed(str(tp))


def test_call_in_transcript_matches_id_name_and_files(home):
    tp = _transcript(home, lines=[
        {"type": "user", "message": {"content": "send it"}},
        _call("toolu_a", ["/w/a.png"]),
        _call("toolu_r", ["/w/r.png"], name="Read"),
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_u", "content": "x"}]}},
        # A tool_use-shaped block in a line that is not the assistant's.
        {"type": "user", "message": {"content": [
            {"type": "tool_use", "id": "toolu_q", "name": "SendUserFile",
             "input": {"files": ["/w/q.png"]}}]}},
    ])
    assert _in(tp, "toolu_a", ["/w/a.png"])
    assert not _in(tp, "toolu_a", ["/w/b.png"])
    assert not _in(tp, "toolu_a", ["/w/a.png", "/w/b.png"])
    assert not _in(tp, "toolu_r", ["/w/r.png"])
    # Named only in a user line (a tool result, or a prompt quoting it).
    assert not _in(tp, "toolu_u", ["/w/a.png"])
    assert not _in(tp, "toolu_q", ["/w/q.png"])
    assert not _in(tp, "", ["/w/a.png"])


def test_call_in_transcript_must_be_written_during_the_turn(home):
    """A call written before the batch's turn began (an old call, replayed)
    matches nothing; the clock slack is the only allowance."""
    turn = time.time() - 30
    slack = ob.TRANSCRIPT_CLOCK_SLACK_SECONDS
    tp = _transcript(home, lines=[
        _call("toolu_old", ["/w/a.png"], wall=turn - 3600),
        _call("toolu_edge", ["/w/a.png"], wall=turn - slack + 0.5),
        _call("toolu_late", ["/w/a.png"], wall=turn - slack - 0.5),
        _call("toolu_now", ["/w/a.png"], wall=turn + 5),
        _call("toolu_none", ["/w/a.png"], wall=False),
    ])
    assert not _in(tp, "toolu_old", ["/w/a.png"], since=turn)
    assert _in(tp, "toolu_edge", ["/w/a.png"], since=turn)
    assert not _in(tp, "toolu_late", ["/w/a.png"], since=turn)
    assert _in(tp, "toolu_now", ["/w/a.png"], since=turn)
    # No timestamp, or no turn start known: never a match.
    assert not _in(tp, "toolu_none", ["/w/a.png"], since=turn)
    assert not _in(tp, "toolu_now", ["/w/a.png"], since=0.0)
    with open(tp, "a") as fh:
        bad = _call("toolu_bad", ["/w/a.png"])
        bad["timestamp"] = "yesterday"
        fh.write(json.dumps(bad) + "\n")
    assert not _in(tp, "toolu_bad", ["/w/a.png"], since=turn)


def test_call_in_transcript_must_be_from_the_batchs_folder(home):
    """A call made from another folder (where a relative name means another
    file) matches nothing."""
    tp = _transcript(home, lines=[
        _call("toolu_a", ["a.png"], cwd="/w"),
        _call("toolu_n", ["a.png"], cwd=None),
    ])
    assert _in(tp, "toolu_a", ["a.png"], cwd="/w")
    assert _in(tp, "toolu_a", ["a.png"], cwd="/w/")
    assert not _in(tp, "toolu_a", ["a.png"], cwd="/home/alice")
    assert not _in(tp, "toolu_a", ["a.png"], cwd=None)
    assert not _in(tp, "toolu_n", ["a.png"], cwd="/w")


def test_line_wall_reads_claude_codes_timestamps():
    assert ob.line_wall({"timestamp": "2026-10-18T01:33:54.123Z"}) == pytest.approx(1792287234.123)
    assert ob.line_wall({"timestamp": "2026-10-18T03:33:54+02:00"}) == 1792287234.0
    assert ob.line_wall({"timestamp": "2026-10-18T01:33:54"}) == 1792287234.0
    for raw in (None, "", 5, "x" * 65, "not a time"):
        assert ob.line_wall({"timestamp": raw}) is None
    assert ob.line_wall({}) is None


def test_call_in_transcript_reads_only_the_tail(home, monkeypatch):
    tp = _transcript(home, lines=[_call("toolu_a", ["/w/a.png"])])
    with open(tp, "a") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "x" * 5000}}) + "\n")
    assert _in(tp, "toolu_a", ["/w/a.png"])
    monkeypatch.setattr(ob, "TRANSCRIPT_TAIL_BYTES", 1000)
    assert not _in(tp, "toolu_a", ["/w/a.png"])


def test_batch_in_transcript_looks_in_the_subagents_own_file(home):
    tp = _transcript(home, lines=[])
    sub = tp.with_suffix("") / "subagents" / "agent-a1.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_text(json.dumps(_call("toolu_s", ["/w/a.png"])) + "\n")
    batch = ob.Batch(session="s", caption=None, display=None, items=[], agent_id="a1",
                     created=0.0, tool_use_id="toolu_s", files=["/w/a.png"],
                     transcript_path=str(tp), cwd=CWD, turn_wall=_since())
    assert ob.batch_in_transcript(batch)
    batch.agent_id = "../a1"
    assert not ob.batch_in_transcript(batch)
    # An agent id that climbs out of subagents/ to a file holding the call.
    (sub.parent / "agent-x").mkdir()
    planted = tp.with_suffix("") / "evil.jsonl"
    planted.write_text(json.dumps(_call("toolu_s", ["/w/a.png"])) + "\n")
    batch.agent_id = "x/../../evil"
    assert not ob.batch_in_transcript(batch)
    batch.agent_id = ""
    assert not ob.batch_in_transcript(batch)


def test_verify_batch_waits_a_bounded_time(home, monkeypatch):
    import asyncio
    tp = _transcript(home, lines=[])
    batch = ob.Batch(session="s", caption=None, display=None, items=[], agent_id="",
                     created=0.0, tool_use_id="toolu_late", files=["/w/a.png"],
                     transcript_path=str(tp), cwd=CWD, turn_wall=_since())
    monkeypatch.setattr(ob, "TRANSCRIPT_POLL_SECONDS", 0.05)

    async def late():
        await asyncio.sleep(0.2)
        with open(tp, "a") as fh:
            fh.write(json.dumps(_call("toolu_late", ["/w/a.png"])) + "\n")

    async def go(wait):
        writer = asyncio.ensure_future(late())
        ok = await ob.verify_batch(batch, wait=wait)
        await writer
        return ok
    assert asyncio.run(go(2.0)) is True
    tp.write_text("")
    t0 = time.monotonic()
    assert asyncio.run(go(0.1)) is False
    assert time.monotonic() - t0 < 1.5


# ---- one delivery at a time per session (rev-iter1-003) -----------------------

def test_run_delivery_runs_a_sessions_tasks_in_order():
    import asyncio
    box = ob.Outbox()
    order = []

    async def go():
        gate = asyncio.Event()

        async def first():
            await gate.wait()
            order.append("first")

        async def second():
            order.append("second")

        async def other():
            order.append("other")
        box.run_delivery("s", first)
        box.run_delivery("s", second)
        box.run_delivery("t", other)
        await asyncio.sleep(0.05)
        assert order == ["other"] and box.delivering("s")
        gate.set()
        await box.settled("s")
        assert not box.delivering("s")
    asyncio.run(go())
    assert order == ["other", "first", "second"]


def test_a_running_copy_is_claimed_by_one_delivery_only():
    """A copy a delivery waits for is that delivery's: a later delivery of
    the same session never claims it again (its batch is not shared), and
    the copy's batch stays out of the store while it is claimed."""
    import asyncio
    box = ob.Outbox()

    async def go():
        gate = asyncio.Event()
        batch = ob.Batch(session="s", caption=None, display=None,
                         items=[_item("photo")], agent_id="", created=0.0)

        async def copy():
            await gate.wait()
            box.finish_copy(batch)
            return batch
        task = asyncio.ensure_future(copy())
        box.track("s", task)
        first = box.claim_copies("s")
        second = box.claim_copies("s")
        gate.set()
        await task
        return first, second, task
    first, second, task = asyncio.run(go())
    assert first == [task] and second == []
    assert ob.task_batch(task) is not None
    assert not box.batches.get("s")


def test_requeue_after_keeps_the_order():
    box = ob.Outbox()

    def p(name):
        return ob.Pending(session=name, chat_id=1, anchor=None, held_key=None,
                          prompt_id=None, batches=[], created=0.0)
    a, b, c, d = p("a"), p("b"), p("c"), p("d")
    box.queue(d)
    box.requeue_front(a)
    box.requeue_after(a, [b, c])
    assert [x.session for x in box.pending[1]] == ["a", "b", "c", "d"]
    box.pending.clear()
    box.requeue_after(a, [b, c])        # a is not there: at the front
    assert [x.session for x in box.pending[1]] == ["b", "c"]
