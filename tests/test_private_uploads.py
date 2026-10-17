"""Files sent from Telegram go to a private folder per chat
(``aipager/uploads.py``): ``<download dir>/<chat key>/``, folders 0700,
files 0600, made without following a symlink or using another user's
folder. A restricted turn is kept off every other chat's folder, and off
the files every chat shared before, by its policy snapshot; the owner
and admins are unchanged."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config, member_root, policy, safety, uploads
from aipager import policy_snapshot as ps
from aipager.dtach import enforce
from aipager.scope import Member, Scope

CHAT_A, CHAT_B, OWNER, ALICE = -1001, -1002, 1, 101
SECRET = "123456:TEST-token"


def _mode(p) -> int:
    return stat.S_IMODE(os.lstat(p).st_mode)


# ── the folder layout ──────────────────────────────────────────────────────

def test_a_chat_gets_its_own_folder_and_key(tmp_path):
    a = uploads.chat_folder(tmp_path, CHAT_A, SECRET)
    assert a == uploads.chat_folder(tmp_path, CHAT_A, SECRET)
    assert a != uploads.chat_folder(tmp_path, CHAT_B, SECRET)
    assert a.parent == tmp_path
    key = a.name
    assert re.fullmatch(r"c[0-9a-f]{24}", key), key
    assert str(abs(CHAT_A)) not in key
    # Keyed by the bot token: the name alone does not give the chat away.
    assert uploads.chat_key(CHAT_A, "another:token") != key


def test_folders_are_0700_and_files_0600(tmp_path):
    base = tmp_path / "aipager-files"
    folder = uploads.ensure_chat_folder(base, CHAT_A, SECRET)
    assert folder == uploads.chat_folder(base, CHAT_A, SECRET)
    assert _mode(base) == 0o700 and _mode(folder) == 0o700
    f = uploads.reserve_file(folder, "1_photo.jpg")
    assert f.parent == folder and f.read_bytes() == b""
    assert _mode(f) == 0o600
    f.write_bytes(b"x")
    os.chmod(f, 0o644)
    uploads.seal_file(f)
    assert _mode(f) == 0o600


def test_a_wide_old_folder_is_narrowed_and_its_files_kept(tmp_path):
    """The folder from before (0775, files 0664) is narrowed to 0700; its
    files stay where they are, unchanged: nothing is moved or deleted."""
    base = tmp_path / "aipager-files"
    base.mkdir()
    os.chmod(base, 0o775)
    old = base / "1700000000_photo.jpg"
    old.write_bytes(b"old")
    os.chmod(old, 0o664)
    uploads.ensure_chat_folder(base, CHAT_A, SECRET)
    assert _mode(base) == 0o700
    assert old.read_bytes() == b"old" and _mode(old) == 0o664


def test_a_second_upload_of_the_same_name_gets_its_own_file(tmp_path):
    folder = uploads.ensure_chat_folder(tmp_path / "b", CHAT_A, SECRET)
    one = uploads.reserve_file(folder, "1_photo.jpg")
    two = uploads.reserve_file(folder, "1_photo.jpg")
    assert one.name == "1_photo.jpg" and two.name == "1_photo-2.jpg"


def test_a_symlink_where_the_file_goes_is_never_written_through(tmp_path):
    folder = uploads.ensure_chat_folder(tmp_path / "b", CHAT_A, SECRET)
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep")
    (folder / "1_photo.jpg").symlink_to(victim)
    got = uploads.reserve_file(folder, "1_photo.jpg")
    assert got.name == "1_photo-2.jpg"
    assert victim.read_bytes() == b"keep"


@pytest.mark.parametrize("which", ["base", "chat"])
def test_a_symlinked_folder_is_refused(tmp_path, which):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.chmod(elsewhere, 0o755)
    base = tmp_path / "aipager-files"
    if which == "base":
        base.symlink_to(elsewhere)
    else:
        base.mkdir(mode=0o700)
        uploads.chat_folder(base, CHAT_A, SECRET).symlink_to(elsewhere)
    with pytest.raises(uploads.UnsafeUploadFolder):
        uploads.ensure_chat_folder(base, CHAT_A, SECRET)
    assert list(elsewhere.iterdir()) == []
    assert _mode(elsewhere) == 0o755  # not narrowed through the link


def test_a_file_where_the_folder_goes_is_refused(tmp_path):
    base = tmp_path / "aipager-files"
    base.write_text("not a folder")
    with pytest.raises(uploads.UnsafeUploadFolder):
        uploads.ensure_chat_folder(base, CHAT_A, SECRET)


@pytest.mark.parametrize("which", ["base", "chat"])
def test_a_folder_owned_by_another_user_is_refused(tmp_path, monkeypatch, which):
    base = tmp_path / "aipager-files"
    base.mkdir(mode=0o777)
    os.chmod(base, 0o777)
    me = os.geteuid()
    chat = uploads.chat_folder(base, CHAT_A, SECRET)
    if which == "base":
        monkeypatch.setattr(uploads, "_owner_uid", lambda: me + 1)
    else:
        chat.mkdir(mode=0o700)
        # The check asks the seam who must own each folder, the base
        # first: ours, then someone else's for the chat's folder.
        expected = iter([me, me + 1])
        monkeypatch.setattr(uploads, "_owner_uid", lambda: next(expected))
    with pytest.raises(uploads.UnsafeUploadFolder):
        uploads.ensure_chat_folder(base, CHAT_A, SECRET)
    if which == "base":
        assert _mode(base) == 0o777  # never narrowed: not ours to touch
        assert not chat.exists()


# ── the daemon's download paths ────────────────────────────────────────────

def _file_update(mk_update, chat_id, written):
    update = mk_update(None, chat_id=chat_id)
    msg = update.message
    msg.media_group_id = None
    msg.caption = None
    msg.document = None
    tg_file = MagicMock()

    async def _download(custom_path=None, **_kw):
        Path(custom_path).write_bytes(b"img")
        written.append(custom_path)
    tg_file.download_to_drive = AsyncMock(side_effect=_download)
    photo = MagicMock()
    photo.file_size = 10
    photo.get_file = AsyncMock(return_value=tg_file)
    msg.photo = [photo]
    return update


def test_an_upload_lands_in_its_chats_folder(mk_bot, mk_update, run_async, monkeypatch):
    from aipager.bot import handlers
    from aipager.bot.transport import calling_chat_id
    bot = mk_bot()
    bot._authorize = AsyncMock(return_value=True)
    bot._inject_file_prompt = AsyncMock()
    written: list = []
    update = _file_update(mk_update, CHAT_A, written)
    run_async(bot._handle_file(update, MagicMock()))
    base = Path(handlers.FILE_DOWNLOAD_DIR)
    path = Path(written[0])
    assert path.parent == uploads.chat_folder(base, calling_chat_id(update))
    assert _mode(path) == 0o600 and _mode(path.parent) == 0o700 and _mode(base) == 0o700
    assert bot._inject_file_prompt.await_args.args[3] == [path]


def test_an_unsafe_folder_refuses_the_upload(mk_bot, mk_update, run_async, tmp_path,
                                             monkeypatch):
    from aipager.bot import handlers
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "linked-files"
    link.symlink_to(elsewhere)
    monkeypatch.setattr(handlers, "FILE_DOWNLOAD_DIR", link)
    bot = mk_bot()
    bot._authorize = AsyncMock(return_value=True)
    bot._inject_file_prompt = AsyncMock()
    replies = AsyncMock()
    monkeypatch.setattr(handlers, "reply_text", replies)
    written: list = []
    run_async(bot._handle_file(_file_update(mk_update, CHAT_A, written), MagicMock()))
    assert written == [] and list(elsewhere.iterdir()) == []
    bot._inject_file_prompt.assert_not_awaited()
    replies.assert_awaited()


def test_a_failed_download_leaves_no_empty_file(mk_bot, mk_update, run_async,
                                                monkeypatch):
    from aipager.bot import handlers
    from telegram.error import BadRequest
    monkeypatch.setattr(handlers, "reply_text", AsyncMock())
    bot = mk_bot()
    bot._inject_file_prompt = AsyncMock()
    update = _file_update(mk_update, CHAT_A, [])
    update.message.photo[0].get_file = AsyncMock(side_effect=BadRequest("gone"))
    run_async(bot._handle_file(update, MagicMock()))
    folder = uploads.chat_folder(handlers.FILE_DOWNLOAD_DIR, CHAT_A)
    assert folder.is_dir() and list(folder.iterdir()) == []
    bot._inject_file_prompt.assert_not_awaited()


def test_a_voice_note_goes_to_its_chats_folder_and_is_removed(
        mk_bot, mk_update, run_async, monkeypatch):
    from aipager.bot import handlers
    bot = mk_bot()
    update = mk_update("", chat_id=CHAT_A)
    update.message.voice = MagicMock(file_size=1000)
    seen: list = []

    async def _download(custom_path=None, **_kw):
        Path(custom_path).write_bytes(b"ogg")
        seen.append((custom_path, _mode(custom_path)))
    fake_file = MagicMock()
    fake_file.download_to_drive = AsyncMock(side_effect=_download)
    update.message.voice.get_file = AsyncMock(return_value=fake_file)
    ack = MagicMock()
    ack.edit_text = AsyncMock()
    update.message.reply_text = AsyncMock(return_value=ack)
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    monkeypatch.setattr("aipager.bot.voice.transcribe", AsyncMock(return_value=""))
    run_async(bot._handle_voice(update, MagicMock()))
    path, mode = seen[0]
    assert Path(path).parent == uploads.chat_folder(handlers.FILE_DOWNLOAD_DIR, CHAT_A)
    assert mode == 0o600
    assert not Path(path).exists()


def test_a_failed_voice_download_leaves_no_empty_file(mk_bot, mk_update, run_async,
                                                      monkeypatch):
    from aipager.bot import handlers
    bot = mk_bot()
    update = mk_update("", chat_id=CHAT_A)
    update.message.voice = MagicMock(file_size=1000)
    update.message.voice.get_file = AsyncMock(side_effect=RuntimeError("io"))
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    run_async(bot._handle_voice(update, MagicMock()))
    folder = uploads.chat_folder(handlers.FILE_DOWNLOAD_DIR, CHAT_A)
    assert folder.is_dir() and list(folder.iterdir()) == []


def test_an_upload_with_no_chat_is_refused(mk_bot, mk_update, run_async, monkeypatch):
    from aipager.bot import handlers
    monkeypatch.setattr(handlers, "reply_text", AsyncMock())
    bot = mk_bot()
    bot._inject_file_prompt = AsyncMock()
    written: list = []
    update = _file_update(mk_update, CHAT_A, written)
    update.effective_chat = None
    update.message.chat = None
    run_async(bot._handle_file(update, MagicMock()))
    assert written == []
    bot._inject_file_prompt.assert_not_awaited()
    assert not Path(handlers.FILE_DOWNLOAD_DIR).exists()


def test_the_daemon_closes_the_download_folder_when_it_starts(mk_bot, run_async):
    """An older aipager left the folder and its files open (0775, 0664):
    the start narrows the folder at once, without waiting for an upload,
    and leaves the files where they are."""
    base = Path(config.FILE_DOWNLOAD_DIR)
    base.mkdir(parents=True)
    os.chmod(base, 0o775)
    (base / "1700000000_photo.jpg").write_bytes(b"old")
    bot = mk_bot()

    class _Stop(Exception):
        pass

    def _builder():
        raise _Stop
    bot._make_builder = _builder
    with pytest.raises(_Stop):
        run_async(bot.start())
    assert _mode(base) == 0o700
    assert (base / "1700000000_photo.jpg").read_bytes() == b"old"


# ── the policy floor ───────────────────────────────────────────────────────

SESSION = "claude-proj"
SID = "5b1d7363-aaaa-bbbb-cccc-0000000000aa"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home" / "op"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    return h


def _scopes(root_a=None):
    def chat(chat_id, label, root=None):
        return Scope(chat_id=chat_id, kind="group", label=label, members=(
            Member(id=OWNER, label="owner", role="owner"),
            Member(id=7, label="admin", role="admin"),
            Member(id=ALICE, label="alice", role="user"),
        ), root=root)
    return [chat(CHAT_A, "frontend", root_a), chat(CHAT_B, "backend")]


@pytest.fixture
def files(home):
    """Uploads of both chats, and an old shared file, under the
    download folder."""
    base = Path(config.FILE_DOWNLOAD_DIR)
    a = uploads.ensure_chat_folder(base, CHAT_A)
    b = uploads.ensure_chat_folder(base, CHAT_B)
    (a / "1_mine.png").write_bytes(b"a")
    (b / "1_theirs.png").write_bytes(b"b")
    gone = base / "c000000000000000000000000"   # a chat no longer configured
    gone.mkdir(mode=0o700)
    (gone / "1_old.png").write_bytes(b"g")
    (base / "1700000000_photo.jpg").write_bytes(b"old")
    return {"mine": a / "1_mine.png", "theirs": b / "1_theirs.png",
            "gone": gone / "1_old.png", "legacy": base / "1700000000_photo.jpg",
            "base": base, "b": b}


def _use(role_name, scope_chat=CHAT_A, pol=None, root_a=None):
    pol = pol or policy.load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))
    scopes = _scopes(root_a)
    scope = next(s for s in scopes if s.chat_id == scope_chat)
    role = pol.get_role(role_name)
    bound = member_root.snapshot_bound(role, scope, scopes)
    snap = ps.resolve_snapshot(role, scope, None, root_bound=bound)
    if bound is not None:
        # What the hook reads: the snapshot, not the bound.
        assert set(bound.deny_bash) <= set(snap["deny_bash_patterns"])
        assert set(bound.deny_globs) <= set(snap["deny_paths_no_access"])
    ps.write_merged_snapshot(SESSION, snap)
    return bound


def _decide(tmp_path, tool, tool_input, cwd):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @alice · role:user]\ndo it"}}) + "\n")
    out = enforce.decide({
        "hook_event_name": "PreToolUse", "session": SESSION, "session_id": SID,
        "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input,
        "transcript_path": str(t)})
    return (out or {}).get("reason")


def _project(home):
    p = home / "aipager-users" / "frontend" / "proj"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.parametrize("role", ["user", "read_only"])
def test_a_restricted_turn_reads_its_own_chats_upload(home, tmp_path, files, role):
    _use(role)
    assert _decide(tmp_path, "Read", {"file_path": str(files["mine"])},
                   _project(home)) is None


@pytest.mark.parametrize("which", ["theirs", "gone", "legacy"])
@pytest.mark.parametrize("role", ["user", "read_only"])
def test_a_restricted_turn_cannot_read_another_chats_upload(home, tmp_path, files,
                                                            role, which):
    _use(role)
    reason = _decide(tmp_path, "Read", {"file_path": str(files[which])}, _project(home))
    assert reason and "protected path" in reason, reason


def test_a_relative_climb_out_of_its_own_folder_is_refused(home, tmp_path, files):
    _use("user")
    climb = files["mine"].parent / ".." / files["b"].name / "1_theirs.png"
    assert _decide(tmp_path, "Read", {"file_path": str(climb)}, _project(home))


@pytest.mark.parametrize("tool,tool_input,mine", [
    ("Write", lambda f: {"file_path": str(f["b"] / "x.txt"), "content": "x"},
     lambda f: {"file_path": str(f["mine"].parent / "x.txt"), "content": "x"}),
    ("Edit", lambda f: {"file_path": str(f["theirs"]), "old_string": "b",
                        "new_string": "c"},
     lambda f: {"file_path": str(f["mine"]), "old_string": "a", "new_string": "c"}),
    ("Grep", lambda f: {"pattern": "x", "path": str(f["b"])},
     lambda f: {"pattern": "x", "path": str(f["mine"].parent)}),
    ("Grep", lambda f: {"pattern": "x", "path": str(f["base"])}, None),
    ("Glob", lambda f: {"pattern": "*", "path": str(f["b"])},
     lambda f: {"pattern": "*", "path": str(f["mine"].parent)}),
], ids=["write", "edit", "grep-chat", "grep-base", "glob"])
def test_a_restricted_turn_cannot_write_or_search_another_chats_uploads(
        home, tmp_path, files, tool, tool_input, mine):
    """The write and search confinement already keep a turn to its session
    folder, so the uploads' own rule decides only when the download folder
    lies inside it: a members' root folder that holds it, and a session
    working there. Its own chat's folder stays open."""
    work = files["base"].parent
    _use("user", root_a=str(work))
    reason = _decide(tmp_path, tool, tool_input(files), work)
    assert reason and ("protected path" in reason or "could reach" in reason
                       or "reaches" in reason), reason
    if mine is not None:
        assert _decide(tmp_path, tool, mine(files), work) is None


def test_an_admin_keeps_reading_every_upload(home, tmp_path, files):
    """Admins, like owners, are not held to a chat (``can_manage``), as
    for the members' root folders."""
    assert _use("admin") is None
    assert _decide(tmp_path, "Read", {"file_path": str(files["theirs"])},
                   _project(home)) is None


def test_the_other_chat_is_kept_off_this_one_too(home, tmp_path, files):
    _use("user", scope_chat=CHAT_B)
    assert _decide(tmp_path, "Read", {"file_path": str(files["mine"])}, _project(home))
    assert _decide(tmp_path, "Read", {"file_path": str(files["theirs"])},
                   _project(home)) is None


def test_a_configured_chat_without_uploads_yet_is_kept_off_too(home):
    """A chat whose folder does not exist yet is denied all the same: its
    first upload during a running turn must not open it."""
    bound = _use("user")
    later = uploads.chat_folder(config.FILE_DOWNLOAD_DIR, CHAT_B)
    assert str(later) in bound.deny


@pytest.mark.parametrize("cmd,denied", [
    ("cat {mine}", False),
    ("cat {theirs}", True),
    ("ls {base}", True),
    ("cat {mine_dir}/../{b_name}/1_theirs.png", True),
    ("cat \"{mine_dir}/..\"/{b_name}/1_theirs.png", True),
    ("cd {mine_dir} && cat ../{b_name}/1_theirs.png", True),
    ("cat {upper_other}", True),
    ("cp {mine_dir}/x..png .", False),
    ("cd {mine_dir};cd ..;ls", True),
    ("(cd {mine_dir}&&cd ..&&cat {b_name}/x)", True),
], ids=["own", "other", "base", "climb", "quoted-climb", "cd-climb", "upper-case",
        "dots-in-a-name", "semicolon-climb", "subshell-climb"])
def test_bash_naming_another_chats_uploads_is_refused(home, files, cmd, denied):
    """Best effort, like every Bash pattern: the built-in restricted roles
    have no shell at all."""
    bound = _use("user")
    upper = str(files["theirs"]).replace("aipager-files", "AIPAGER-FILES")
    command = cmd.format(mine=files["mine"], theirs=files["theirs"], base=files["base"],
                         mine_dir=files["mine"].parent, b_name=files["b"].name,
                         upper_other=upper)
    got = safety.bash_violation(command, tuple(bound.deny_bash))
    assert bool(got) is denied, (command, got)


def test_the_bash_patterns_stay_linear(home, files):
    """Every Bash call of a restricted turn runs them."""
    import time
    bound = _use("user")
    command = f"{files['mine'].parent}/x " * 4000 + "x" * 50000
    t0 = time.perf_counter()
    safety.bash_violation(command, tuple(bound.deny_bash))
    assert time.perf_counter() - t0 < 0.5
