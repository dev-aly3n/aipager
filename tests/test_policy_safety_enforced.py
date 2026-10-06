"""The ``safety:`` section of policy.yaml is enforced (roadmap 8.96).

``load_policy`` has always read ``safety: deny_paths_no_access /
deny_paths_no_write / deny_bash_patterns`` and ``aipager doctor
--safety-check`` has always shown them as active, but every snapshot was
built from the built-in ``safety.DENY_*`` lists only: an operator who
protected ``/srv/secrets/**`` there was not protected. Now:

- every non-owner turn's rules hold the built-in floor plus the policy's
  ``safety:`` section (notes, merged, carried and floor snapshots), from
  the daemon's live policy; a live reload changes it for notes written
  after it;
- the hook's fallbacks (no snapshot, a failed pick-up, an unattributed
  turn) read the effective floor from a small file the daemon writes,
  and fall back to the built-in floor when that file is missing or
  corrupt (never less);
- owners stay exempt;
- with no ``safety:`` section, every snapshot is what it was before
  (parity, against frozen copies of the old code below).

Everything goes through the real daemon and hook code paths
(``_inject_prompt``, ``_match_and_promote``, ``enforce.decide``). The
home folder is a fake one under ``tmp_path``; nothing reads the real
``~/.config/aipager`` or writes a real ``/tmp/claude-*`` file (conftest
redirects the floor file, the snapshots and the notes).
"""

from __future__ import annotations

import argparse
import builtins
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import policy, safety
from aipager import policy_snapshot as ps
from aipager.dtach import enforce, inject, notify_hook
from aipager.scope import Member, Scope

GROUP = -1001
S = "claude-api__g1001"
ALY, BOB, ADA = 1, 2, 4

SAFETY_YAML = """\
safety:
  deny_paths_no_access: [/srv/secrets/**]
  deny_paths_no_write: [~/notes/**]
  deny_bash_patterns: ['\\bcurl\\b']
"""

SECRET = "/srv/secrets/x"

# The real floor file's path, read at import (collection), before
# conftest redirects ``floor_path`` for each test. Never opened here.
_REAL_FLOOR_PATH = str(ps.floor_path())
CURL = "curl x"


# ---- builders ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def _snapshot_dir():
    ps.snapshot_path(S).parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home folder with a fake aipager config folder and a
    ``notes`` project. Nothing here reads the real ones."""
    h = tmp_path / "home" / "op"
    (h / ".config" / "aipager").mkdir(parents=True)
    (h / ".config" / "aipager" / "aipager.yaml").write_text("bot_token: FAKE\n")
    (h / "notes").mkdir()
    (h / "proj").mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    assert os.path.expanduser("~") == str(h)
    return h


def _policy(home, text: str | None):
    """``load_policy`` over a policy.yaml in the fake home (none when
    *text* is None)."""
    cfg = home / ".config" / "aipager"
    f = cfg / "policy.yaml"
    if text is None:
        f.unlink(missing_ok=True)
    else:
        f.write_text(text)
    return policy.load_policy(f, cfg / "policy.d")


@pytest.fixture
def with_safety(home):
    return _policy(home, SAFETY_YAML)


@pytest.fixture
def without_safety(home):
    return _policy(home, None)


def _group():
    return Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=ADA, label="ada", role="admin"),
    ))


@pytest.fixture
def typed(monkeypatch):
    out: list[tuple[str, str]] = []

    async def _send(name, text, *a, **kw):
        out.append((name, text))
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    return out


@pytest.fixture
def gbot(mk_bot):
    def _mk(pol):
        bot = mk_bot(scopes=[_group()])
        bot.policy = pol
        bot._app.bot.id = 999
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_send_busy_and_animate"):
            setattr(bot, name, AsyncMock())
        return bot
    return _mk


def _session(bot, name=S):
    from aipager.state import Status
    s = bot.registry.get_or_create(name)
    s.label = name.removeprefix("claude-").split("__")[0]
    s.scope_chat_id, s.scope_kind = GROUP, "group"
    s.status = Status.IDLE
    return s


def _send(bot, run_async, typed, uid, text="go") -> str:
    """A Telegram message of *uid* into the group session, as the daemon
    sends it; returns the body typed into the session."""
    sess = _session(bot)
    before = len(typed)
    assert run_async(bot._inject_prompt(sess, text, chat_id=GROUP,
                                        driver_user_id=uid))
    assert len(typed) == before + 1
    return typed[-1][1]


def _transcript(tmp_path, body) -> str:
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {
        "role": "user", "content": body}}) + "\n", encoding="utf-8")
    return str(p)


def _decide(transcript, tool, tool_input, cwd):
    return enforce.decide({
        "hook_event_name": "PreToolUse", "session": S,
        "session_id": "5b1d7363-aaaa-bbbb-cccc-000000000896",
        "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input,
        "transcript_path": transcript})


def _turn(bot, run_async, typed, tmp_path, uid) -> str:
    """Send a message of *uid*, let the hook pick it up; returns the
    transcript path of that turn."""
    body = _send(bot, run_async, typed, uid)
    ps.clear_turn_open(S)  # the previous turn ended (its Stop)
    notify_hook._match_and_promote(S, body)
    return _transcript(tmp_path, body)


def _reason(res):
    return res["reason"] if res else None


def _as_hook():
    """This process from now on reads the floor as the hook does: no live
    policy, the daemon-written file, read afresh."""
    ps._live_floor_lists = None
    ps._file_floor_lists = None


# ---- (1) every non-owner turn is held to the safety section ----------------

def test_a_user_turn_is_held_to_the_safety_section(
        gbot, typed, run_async, tmp_path, home, with_safety):
    bot = gbot(with_safety)
    t = _turn(bot, run_async, typed, tmp_path, BOB)
    notes = home / "notes"

    assert _reason(_decide(t, "Read", {"file_path": SECRET}, home / "proj")) \
        == "Read on protected path /srv/secrets/**"
    # Inside the session's own folder, so only the no-write rule stops it.
    assert _reason(_decide(t, "Write", {"file_path": str(notes / "x"),
                                        "content": "x"}, notes)) \
        == "Write write to protected path ~/notes/**"
    # The pattern answers before the role's own Bash deny.
    assert _reason(_decide(t, "Bash", {"command": CURL}, home / "proj")) \
        == "Bash command blocked by safety policy"


def test_without_the_section_the_same_user_calls_are_not_blocked_by_it(
        gbot, typed, run_async, tmp_path, home, without_safety):
    """The control: what the tests above see denied is the section's."""
    bot = gbot(without_safety)
    t = _turn(bot, run_async, typed, tmp_path, BOB)
    notes = home / "notes"

    assert _decide(t, "Read", {"file_path": SECRET}, home / "proj") is None
    assert _decide(t, "Write", {"file_path": str(notes / "x"),
                                "content": "x"}, notes) is None
    assert _reason(_decide(t, "Bash", {"command": CURL}, home / "proj")) \
        == "Bash is in this role's deny_tools"


def test_an_admin_turn_is_held_to_the_safety_section(
        gbot, typed, run_async, tmp_path, home, with_safety):
    """Admins bypass role denies, not the safety floor (8.83): the
    section holds for their reads, writes, searches (8.61) and Bash."""
    bot = gbot(with_safety)
    t = _turn(bot, run_async, typed, tmp_path, ADA)
    proj = home / "proj"

    assert _reason(_decide(t, "Read", {"file_path": SECRET}, proj)) \
        == "Read on protected path /srv/secrets/**"
    assert _reason(_decide(t, "Write", {"file_path": str(home / "notes/x"),
                                        "content": "x"}, proj)) \
        == "Write write to protected path ~/notes/**"
    assert "would reach protected files (/srv/secrets)" in _reason(
        _decide(t, "Grep", {"path": "/srv", "pattern": "TOKEN"}, proj))
    assert "would reach protected files (/srv/secrets)" in _reason(
        _decide(t, "Glob", {"pattern": "/srv/secrets/*"}, proj))
    assert _reason(_decide(t, "Bash", {"command": CURL}, proj)) \
        == "Bash command blocked by safety policy"


def test_without_the_section_the_same_admin_calls_are_allowed(
        gbot, typed, run_async, tmp_path, home, without_safety):
    bot = gbot(without_safety)
    t = _turn(bot, run_async, typed, tmp_path, ADA)
    proj = home / "proj"

    assert _decide(t, "Read", {"file_path": SECRET}, proj) is None
    assert _decide(t, "Write", {"file_path": str(home / "notes/x"),
                                "content": "x"}, proj) is None
    assert _decide(t, "Grep", {"path": "/srv", "pattern": "TOKEN"},
                   proj) is None
    assert _decide(t, "Glob", {"pattern": "/srv/secrets/*"}, proj) is None
    assert _decide(t, "Bash", {"command": CURL}, proj) is None


def test_an_owner_turn_is_not_held_to_it(
        gbot, typed, run_async, tmp_path, home, with_safety):
    bot = gbot(with_safety)
    t = _turn(bot, run_async, typed, tmp_path, ALY)
    proj = home / "proj"

    assert _decide(t, "Read", {"file_path": SECRET}, proj) is None
    assert _decide(t, "Write", {"file_path": str(home / "notes/x"),
                                "content": "x"}, proj) is None
    assert _decide(t, "Grep", {"path": "/srv", "pattern": "x"}, proj) is None
    assert _decide(t, "Bash", {"command": CURL}, proj) is None


def test_an_unanchored_safety_path_denies_every_admin_and_user_search(
        gbot, typed, run_async, tmp_path, home):
    """The documented rule for a protected path with no leading / or ~,
    now reached from the safety section: any search could read such a
    file, so none is allowed (owners keep theirs)."""
    pol = _policy(home, "safety:\n  deny_paths_no_access: ['**/*.pem']\n")
    bot = gbot(pol)
    proj = home / "proj"
    for uid in (BOB, ADA):
        t = _turn(bot, run_async, typed, tmp_path, uid)
        assert _decide(t, "Grep", {"path": str(proj), "pattern": "x"},
                       proj) is not None, uid
    t = _turn(bot, run_async, typed, tmp_path, ALY)
    assert _decide(t, "Grep", {"path": str(proj), "pattern": "x"},
                   proj) is None


def test_role_level_lists_keep_their_meaning(home):
    """The section is the floor; a role's own lists still add to it, and
    an admin's ``bypass_role_denies`` still skips the role's (only)."""
    pol = _policy(home, SAFETY_YAML + """\
roles:
  admin:
    deny_paths_no_access: [/srv/admin-only/**]
  user:
    deny_paths_no_access: [/srv/user-only/**]
""")
    user = ps.resolve_snapshot(pol.get_role("user"), None, None, policy=pol)
    admin = ps.resolve_snapshot(pol.get_role("admin"), None, None, policy=pol)
    assert {"/srv/secrets/**", "/srv/user-only/**"} <= set(
        user["deny_paths_no_access"])
    assert "/srv/secrets/**" in admin["deny_paths_no_access"]
    assert "/srv/admin-only/**" not in admin["deny_paths_no_access"]
    # The user role's own list replaced its credential-file default, as
    # before; the floor's built-ins are still there.
    assert set(safety.DENY_PATHS_NO_ACCESS) <= set(user["deny_paths_no_access"])


# ---- (2) a live reload: notes written after it carry the new section -------

def _write_config():
    import aipager.scope as scope_mod
    scope_mod.dump_scopes([_group()], "123456:FAKE", path=scope_mod.CONFIG_PATH)


def _note_lists(name=S) -> list[dict]:
    return [n for n in ps.list_outstanding_notes(name)]


def test_a_reload_that_removes_the_section_drops_it_from_later_notes(
        gbot, typed, run_async, monkeypatch, home, with_safety,
        without_safety):
    bot = gbot(with_safety)
    ps.set_live_policy(with_safety)
    _send(bot, run_async, typed, BOB, "before")
    before = _note_lists()[-1]
    assert "/srv/secrets/**" in before["deny_paths_no_access"]

    _write_config()
    monkeypatch.setattr("aipager.policy.load_policy",
                        lambda *a, **k: without_safety)
    run_async(bot.reload_team())
    assert bot.policy is without_safety

    _send(bot, run_async, typed, BOB, "after")
    after = [n for n in _note_lists() if n["raw_text"] == "after"][0]
    assert "/srv/secrets/**" not in after["deny_paths_no_access"]
    assert "~/notes/**" not in after["deny_paths_no_write"]
    assert "\\bcurl\\b" not in after["deny_bash_patterns"]
    # The hook's floor follows too.
    _as_hook()
    assert "/srv/secrets/**" not in ps.floor_snapshot()["deny_paths_no_access"]


def test_a_reload_that_adds_the_section_puts_it_in_later_notes_and_the_floor(
        gbot, typed, run_async, monkeypatch, home, with_safety,
        without_safety):
    bot = gbot(without_safety)
    ps.set_live_policy(without_safety)
    _write_config()
    monkeypatch.setattr("aipager.policy.load_policy",
                        lambda *a, **k: with_safety)
    run_async(bot.reload_team())

    _send(bot, run_async, typed, BOB, "after")
    assert "/srv/secrets/**" in _note_lists()[-1]["deny_paths_no_access"]
    # Not passed a policy: the daemon's live one.
    assert "/srv/secrets/**" in ps.resolve_snapshot(
        None, None, None)["deny_paths_no_access"]
    _as_hook()
    floor = ps.floor_snapshot()
    assert "/srv/secrets/**" in floor["deny_paths_no_access"]
    assert "~/notes/**" in floor["deny_paths_no_write"]
    assert "\\bcurl\\b" in floor["deny_bash_patterns"]


def test_a_safety_only_reload_drops_no_waiting_note(
        gbot, typed, run_async, monkeypatch, home, with_safety,
        without_safety):
    """A change to the safety section alone takes no role's rights away:
    nobody's waiting note is deleted (an owner's included, whose message
    would otherwise run on the floor); the section holds from the next
    message."""
    bot = gbot(without_safety)
    ps.set_live_policy(without_safety)
    from aipager.state import Status
    sess = _session(bot)
    _send(bot, run_async, typed, ALY, "owner text")
    _send(bot, run_async, typed, BOB, "user text")
    sess.status = Status.BUSY
    sess.turn_sender_id = BOB
    waiting = sorted(n["raw_text"] for n in _note_lists())

    _write_config()
    monkeypatch.setattr("aipager.policy.load_policy",
                        lambda *a, **k: with_safety)
    run_async(bot.reload_team())

    assert sorted(n["raw_text"] for n in _note_lists()) == waiting


def test_the_reload_rules_for_someone_who_may_no_longer_prompt_hold_it(
        home, with_safety):
    """``live_reload``'s floor for a removed member (or one whose role
    cannot prompt) is the effective floor too."""
    from aipager.bot import live_reload
    ps.set_live_policy(with_safety)
    for uid in (777, BOB):
        pol = with_safety
        if uid == BOB:
            from dataclasses import replace
            pol = replace(with_safety, roles={
                **with_safety.roles,
                "user": replace(with_safety.roles["user"], can_prompt=False)})
        rules = live_reload.sender_rules([_group()], pol, GROUP, uid)
        assert all("/srv/secrets/**" in r["deny_paths_no_access"]
                   for r in rules), uid
    gone = live_reload.narrowed_rules([_group()], with_safety, [], with_safety,
                                      None, BOB)
    assert gone and "/srv/secrets/**" in gone[0]["deny_paths_no_access"]


# ---- (3) the hook's fallbacks read the daemon-written floor ----------------

def _floor_file() -> Path:
    return ps.floor_path()


def test_the_daemon_writes_the_effective_floor_for_the_hook(
        home, with_safety, _isolate_safety_floor):
    assert ps.floor_path() == _isolate_safety_floor
    ps.set_live_policy(with_safety)
    data = json.loads(_floor_file().read_text())
    assert data["deny_paths_no_access"] == [
        *safety.DENY_PATHS_NO_ACCESS, "/srv/secrets/**"]
    assert data["deny_paths_no_write"] == [
        *safety.DENY_PATHS_NO_WRITE, "~/notes/**"]
    assert data["deny_bash_patterns"] == [
        *safety.DENY_BASH_PATTERNS, "\\bcurl\\b"]
    assert (_floor_file().stat().st_mode & 0o777) == 0o600


def test_the_bot_registers_its_policy_at_start(mk_bot, monkeypatch, home,
                                               with_safety):
    monkeypatch.setattr("aipager.policy.load_policy",
                        lambda *a, **k: with_safety)
    mk_bot()
    assert "/srv/secrets/**" in ps.resolve_snapshot(
        None, None, None)["deny_paths_no_access"]
    assert "/srv/secrets/**" in json.loads(
        _floor_file().read_text())["deny_paths_no_access"]


def test_no_snapshot_the_hook_applies_the_daemon_written_floor(
        tmp_path, home, with_safety):
    ps.set_live_policy(with_safety)
    _as_hook()
    assert not ps.snapshot_path(S).exists()
    t = _transcript(tmp_path, "[via Telegram · @bob · role:user]\ngo")

    assert _reason(_decide(t, "Read", {"file_path": SECRET}, home / "proj")) \
        == "Read on protected path /srv/secrets/**"
    assert _reason(_decide(t, "Bash", {"command": CURL}, home / "proj")) \
        == "Bash command blocked by safety policy"


def _hook_floor_variants(home):
    """What the hook falls back to with no snapshot: a Read of a built-in
    protected file is still denied, the section's is not."""
    return home / ".config" / "aipager" / "aipager.yaml"


@pytest.mark.parametrize("content", [
    None,                                    # missing
    b"{not json",                            # corrupt
    b"[]",                                   # not a mapping
    b'{"deny_paths_no_access": [1, 2]}',     # wrong types
    b'{"deny_paths_no_access": []}',         # tries to drop the built-ins
    b'{"deny_paths_no_access": "/srv/secrets/**"}',
    b"[" * 100000,                           # the parser raises RecursionError
    # too big (over 1 MB), even though it is valid JSON
    json.dumps({"deny_paths_no_access": ["/srv/secrets/**"]}).encode()
    + b" " * (1 << 20),
])
def test_a_missing_or_corrupt_floor_file_gives_the_built_in_floor(
        tmp_path, home, content):
    if content is not None:
        _floor_file().write_bytes(content)
    _as_hook()
    t = _transcript(tmp_path, "[via Telegram · @bob · role:user]\ngo")
    proj = home / "proj"

    # Through the hook first: a floor that raised would read as a
    # fail-closed deny here, not as an error.
    assert _decide(t, "Read", {"file_path": SECRET}, proj) is None
    assert _reason(_decide(t, "Read", {"file_path": str(
        _hook_floor_variants(home))}, proj)) \
        == "Read on protected path ~/.config/aipager/**"
    assert ps.floor_snapshot() == ps.FLOOR_SNAPSHOT
    # The paths that read the lists directly never raise on it either.
    builtin = _builtin_policy()
    user = ps.resolve_snapshot(builtin.get_role("user"), None, None,
                               policy=builtin)
    assert _outcome(ps.carried_snapshot, user) == _old_carried_snapshot(user)
    assert _outcome(ps.resolve_snapshot, None, None, None) == \
        _old_resolve_snapshot(None, None, None)


def _outcome(fn, *args):
    """*fn*'s result, or the exception it raised (so a raise fails an
    assertion instead of erroring the test)."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        return e


def test_a_floor_file_another_user_owns_is_ignored(tmp_path, home,
                                                  monkeypatch):
    """Another OS user could create the name in /tmp before the daemon
    does: the hook then keeps the built-in floor."""
    _floor_file().write_text(json.dumps(
        {"deny_paths_no_access": ["/srv/secrets/**"]}))
    monkeypatch.setattr(ps, "_own_uid", lambda: os.getuid() + 1)
    _as_hook()
    assert ps.floor_snapshot() == ps.FLOOR_SNAPSHOT
    monkeypatch.setattr(ps, "_own_uid", os.getuid)
    _as_hook()
    assert "/srv/secrets/**" in ps.floor_snapshot()["deny_paths_no_access"]


def test_a_failing_floor_still_gives_the_built_in_floor(monkeypatch):
    """The last-resort answer never raises: the hook's failure path
    (``snapshot_after_failure``) must always write a floor, or the
    previous turn's snapshot (an owner's) would stay in force."""
    def _boom(policy=None):
        raise RuntimeError("floor exploded")
    monkeypatch.setattr(ps, "safety_floor_lists", _boom)
    assert _outcome(ps.floor_snapshot) == ps.FLOOR_SNAPSHOT
    assert _outcome(ps.floor_snapshot) is not ps.FLOOR_SNAPSHOT
    snap = _outcome(lambda: ps.snapshot_after_failure(
        S, "[via Telegram]\nx", turn_open=False))
    assert snap == ps.FLOOR_SNAPSHOT
    assert snap["bypass_safety"] is False


def test_a_failed_floor_write_leaves_no_temp_file(tmp_path, monkeypatch,
                                                  home, with_safety):
    target = tmp_path / "floor-is-a-dir"
    target.mkdir()
    monkeypatch.setattr(ps, "floor_path", lambda: target)
    ps.set_live_policy(with_safety)  # os.replace onto a directory fails
    assert list(tmp_path.glob("floor-is-a-dir.*.tmp")) == []
    # The daemon itself keeps the lists.
    assert "/srv/secrets/**" in ps.resolve_snapshot(
        None, None, None)["deny_paths_no_access"]


def test_a_floor_file_only_ever_adds(home):
    _floor_file().write_text(json.dumps({
        "deny_paths_no_access": ["/srv/secrets/**"],
        "deny_paths_no_write": "nope",
        "deny_bash_patterns": [None, "\\bcurl\\b"]}))
    _as_hook()
    floor = ps.floor_snapshot()
    assert floor["deny_paths_no_access"] == [
        *ps.FLOOR_SNAPSHOT["deny_paths_no_access"], "/srv/secrets/**"]
    assert floor["deny_paths_no_write"] == ps.FLOOR_SNAPSHOT[
        "deny_paths_no_write"]
    assert floor["deny_bash_patterns"] == [
        *ps.FLOOR_SNAPSHOT["deny_bash_patterns"], "\\bcurl\\b"]
    assert floor["deny_tools"] == ps.FLOOR_SNAPSHOT["deny_tools"]
    assert floor["bypass_safety"] is False
    assert floor["confine_writes"] is True


def test_an_unattributed_telegram_prompt_gets_the_daemon_written_floor(
        home, with_safety):
    """No note matches a prompt carrying Telegram text: the hook writes
    the floor, now with the section."""
    ps.set_live_policy(with_safety)
    _as_hook()
    notify_hook._match_and_promote(S, "[via Telegram · @bob]\nno note")
    snap = ps.read_snapshot(S)
    assert "/srv/secrets/**" in snap["deny_paths_no_access"]
    assert "\\bcurl\\b" in snap["deny_bash_patterns"]
    assert snap["bypass_safety"] is False


@pytest.mark.parametrize("turn_open", [False, True])
def test_a_failed_pick_up_writes_the_daemon_written_floor(
        home, with_safety, turn_open):
    ps.set_live_policy(with_safety)
    _as_hook()
    snap = ps.snapshot_after_failure(S, "[via Telegram]\nx",
                                     turn_open=turn_open)
    assert "/srv/secrets/**" in snap["deny_paths_no_access"]
    assert "~/notes/**" in snap["deny_paths_no_write"]
    assert "\\bcurl\\b" in snap["deny_bash_patterns"]


@pytest.mark.parametrize("role", ["admin", "user"])
def test_a_carried_running_turn_gets_the_section_back(home, with_safety,
                                                      role):
    """A snapshot written before the section existed (or hand-edited
    without it): a message joining that turn puts it back, for an
    unconfined (admin) and a confined (user) turn alike."""
    ps.set_live_policy(with_safety)
    _as_hook()
    builtin = _builtin_policy()
    old = ps.resolve_snapshot(builtin.get_role(role), None, None,
                              policy=builtin)
    assert "/srv/secrets/**" not in old["deny_paths_no_access"]
    carried = ps.carried_snapshot(old)
    assert "/srv/secrets/**" in carried["deny_paths_no_access"]
    assert "\\bcurl\\b" in carried["deny_bash_patterns"]


def test_a_message_joining_a_turn_with_a_malformed_snapshot_gets_the_section(
        home, with_safety):
    """The running turn's snapshot cannot be carried: the floor stands in
    for it, the section included."""
    ps.set_live_policy(with_safety)
    _as_hook()
    joined = ps._join_running_turn({"deny_tools": "not a list"},
                                   {"bypass_safety": False,
                                    "confine_writes": True}, [], "x")
    assert "/srv/secrets/**" in joined["deny_paths_no_access"]
    assert "\\bcurl\\b" in joined["deny_bash_patterns"]


def test_a_reload_narrowing_an_unreadable_turn_uses_the_section(
        home, with_safety):
    ps.set_live_policy(with_safety)
    narrowed = ps.narrowed_snapshot(None, [{"bypass_safety": False,
                                            "confine_writes": True}])
    assert "/srv/secrets/**" in narrowed["deny_paths_no_access"]
    assert "~/notes/**" in narrowed["deny_paths_no_write"]


# ---- (4) the hook's cost: no extra read on the common path -----------------

def _count_floor_opens(monkeypatch) -> list:
    opened: list = []
    real = builtins.open
    target = str(ps.floor_path())

    def spy(file, *a, **k):
        if str(file) == target:
            opened.append(file)
        return real(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    return opened


def test_the_common_pretooluse_path_never_reads_the_floor_file(
        tmp_path, home, with_safety, monkeypatch):
    ps.set_live_policy(with_safety)
    ps.write_merged_snapshot(S, ps.resolve_snapshot(
        with_safety.get_role("user"), None, None, policy=with_safety))
    _as_hook()
    opened = _count_floor_opens(monkeypatch)
    t = _transcript(tmp_path, "[via Telegram · @bob · role:user]\ngo")
    for _ in range(3):
        assert _decide(t, "Read", {"file_path": SECRET},
                       home / "proj") is not None
        assert _decide(t, "Read", {"file_path": str(home / "proj/a")},
                       home / "proj") is None
    assert opened == []


def test_the_no_snapshot_fallback_reads_the_floor_file_once(
        tmp_path, home, with_safety, monkeypatch):
    ps.set_live_policy(with_safety)
    _as_hook()
    opened = _count_floor_opens(monkeypatch)
    t = _transcript(tmp_path, "[via Telegram · @bob · role:user]\ngo")
    for _ in range(3):
        assert _decide(t, "Read", {"file_path": SECRET},
                       home / "proj") is not None
    assert len(opened) == 1


def test_the_daemon_never_reads_the_floor_file(home, with_safety,
                                               monkeypatch):
    ps.set_live_policy(with_safety)
    opened = _count_floor_opens(monkeypatch)
    ps.floor_snapshot()
    ps.merge_snapshots([])
    ps.resolve_snapshot(None, None, None)
    assert opened == []


# ---- (5) parity: no safety section, every snapshot as before ---------------

# frozen from 7ccfb62 (aipager/policy_snapshot.py), do not edit
def _old_resolve_snapshot(role, scope, member, style_text="",
                          reply_context=""):
    FLOOR_SNAPSHOT = ps.FLOOR_SNAPSHOT
    bypass_safety = bool(role and role.bypass_safety)
    bypass_role_denies = bool(role and role.bypass_role_denies)

    deny_tools: set[str] = set()
    allow_tools: set[str] = set()
    no_access: set[str] = set(safety.DENY_PATHS_NO_ACCESS)
    no_write: set[str] = set(safety.DENY_PATHS_NO_WRITE)
    bash: set[str] = set(safety.DENY_BASH_PATTERNS)

    if role is None:
        deny_tools |= set(FLOOR_SNAPSHOT["deny_tools"])
        no_access |= set(FLOOR_SNAPSHOT["deny_paths_no_access"])
    if not bypass_role_denies:
        if scope:
            deny_tools |= set(scope.deny_tools)
        if role:
            deny_tools |= set(role.deny_tools)
            allow_tools |= set(role.allow_tools)
            no_access |= set(role.deny_paths_no_access)
            no_write |= set(role.deny_paths_no_write)
            bash |= set(role.deny_bash_patterns)
        if member:
            deny_tools |= set(getattr(member, "deny_tools", ()))
            allow_tools |= set(getattr(member, "allow_tools", ()))

    return {
        "origin": "telegram",
        "bypass_safety": bypass_safety,
        "confine_writes": not (bypass_safety or bypass_role_denies),
        "deny_tools": sorted(deny_tools),
        "allow_tools": sorted(allow_tools),
        "deny_paths_no_access": sorted(no_access),
        "deny_paths_no_write": sorted(no_write),
        "deny_bash_patterns": sorted(bash),
        "style_text": style_text,
        "reply_context": reply_context,
    }


# frozen from 7ccfb62 (aipager/policy_snapshot.py), do not edit
def _old_carried_snapshot(current):
    _LIST_FIELDS = ("deny_tools", "allow_tools", "deny_paths_no_access",
                    "deny_paths_no_write", "deny_bash_patterns")
    _BASE_FLOOR_LISTS = {
        "deny_paths_no_access": safety.DENY_PATHS_NO_ACCESS,
        "deny_paths_no_write": safety.DENY_PATHS_NO_WRITE,
        "deny_bash_patterns": safety.DENY_BASH_PATTERNS,
    }
    if not isinstance(current, dict):
        return None
    for f in _LIST_FIELDS:
        v = current.get(f)
        if v is not None and not (
            isinstance(v, list) and all(isinstance(x, str) for x in v)
        ):
            return None
    carried = {f: list(current.get(f) or []) for f in _LIST_FIELDS}
    carried["bypass_safety"] = current.get("bypass_safety") is True
    carried["confine_writes"] = current.get("confine_writes") is not False
    floor = (ps.FLOOR_SNAPSHOT if carried["confine_writes"]
             and not carried["bypass_safety"] else _BASE_FLOOR_LISTS)
    for f in ("deny_paths_no_access", "deny_paths_no_write",
              "deny_bash_patterns"):
        carried[f] = sorted(set(carried[f]) | set(floor[f]))
    carried["queued_at"] = float("-inf")
    carried["style_text"] = ""
    carried["reply_context"] = ""
    return carried


_FROZEN_FLOOR_7CCFB62 = {
    "bypass_safety": False,
    "confine_writes": True,
    "deny_tools": list(safety.RESTRICTED_DENY_TOOLS),
    "allow_tools": [],
    "deny_paths_no_access": [*safety.DENY_PATHS_NO_ACCESS,
                             *safety.CREDENTIAL_PATHS],
    "deny_paths_no_write": list(safety.DENY_PATHS_NO_WRITE),
    "deny_bash_patterns": list(safety.DENY_BASH_PATTERNS),
}


def _builtin_policy():
    return policy.load_policy(Path("/nonexistent/p.yaml"),
                              Path("/nonexistent/p.d"))


def _dumps(x) -> str:
    return json.dumps(x)


_SCOPES = [None, Scope(chat_id=GROUP, kind="group", label="t", members=(),
                       deny_tools=("WebFetch",))]
_MEMBERS = [None, Member(id=BOB, label="bob", role="user",
                         deny_tools=("Edit",))]


@pytest.mark.parametrize("mode", ["live-builtin", "explicit", "hook-no-file",
                                  "hook-file", "same-as-builtin-yaml"])
def test_with_no_safety_section_every_snapshot_is_byte_identical(
        home, mode):
    pol = _builtin_policy()
    kw = {}
    if mode == "live-builtin":
        ps.set_live_policy(pol)
    elif mode == "explicit":
        kw = {"policy": pol}
    elif mode == "hook-file":
        ps.set_live_policy(pol)
        _as_hook()
    elif mode == "same-as-builtin-yaml":
        # A section that only repeats built-ins adds nothing.
        pol = _policy(home, "safety:\n  deny_paths_no_access: ["
                      + safety.DENY_PATHS_NO_ACCESS[0] + "]\n"
                      "  deny_bash_patterns: ['"
                      + safety.DENY_BASH_PATTERNS[0].replace("'", "''")
                      + "']\n")
        ps.set_live_policy(pol)
    roles = [None, *(pol.get_role(n) for n in sorted(pol.roles))]
    for role in roles:
        for scope in _SCOPES:
            for member in _MEMBERS:
                new = ps.resolve_snapshot(role, scope, member, "st", "rc",
                                          **kw)
                old = _old_resolve_snapshot(role, scope, member, "st", "rc")
                assert _dumps(new) == _dumps(old), (role, scope, member)
                assert _dumps(ps.carried_snapshot(new)) == _dumps(
                    _old_carried_snapshot(new))
    assert _dumps(ps.floor_snapshot()) == _dumps(_FROZEN_FLOOR_7CCFB62)
    assert _dumps(ps.FLOOR_SNAPSHOT) == _dumps(_FROZEN_FLOOR_7CCFB62)
    assert _dumps(ps.merge_snapshots([])) == _dumps(_FROZEN_FLOOR_7CCFB62)
    assert _dumps(ps.snapshot_after_failure(S, "x", turn_open=False)) == \
        _dumps(_FROZEN_FLOOR_7CCFB62)
    for bad in ({"deny_tools": []}, {"bypass_safety": True},
                {"confine_writes": False}):
        assert _dumps(ps.carried_snapshot(bad)) == _dumps(
            _old_carried_snapshot(bad))


def test_the_owners_install_writes_the_same_notes_as_before(
        gbot, typed, run_async, home, without_safety):
    """One DM, one owner, no policy.yaml: the note's rules are what the
    old code resolved."""
    bot = gbot(without_safety)
    _send(bot, run_async, typed, ALY)
    note = _note_lists()[-1]
    old = _old_resolve_snapshot(without_safety.get_role("owner"),
                                _group(), _group().members[0])
    for k in ps.SAFETY_FIELDS:
        assert note[k] == old[k], k


# ---- (6) doctor --safety-check ---------------------------------------------

def _doctor_out(monkeypatch, capsys, pol, **kw) -> str:
    import aipager.config as cfg
    from aipager import doctor
    monkeypatch.setattr(cfg, "POLICY", pol)
    assert doctor.cmd_doctor(argparse.Namespace(safety_check=True, **kw)) == 0
    return capsys.readouterr().out


def test_safety_check_prints_the_effective_lists(monkeypatch, capsys, home,
                                                 with_safety):
    out = _doctor_out(monkeypatch, capsys, with_safety)
    flat = " ".join(out.split())
    for p in (*safety.DENY_PATHS_NO_ACCESS, "/srv/secrets/**", "~/notes/**"):
        assert f"• {p}" in out, p
    assert "• /\\bcurl\\b/" in out
    assert ("These apply to every role except owner (built-in, plus the "
            "safety: section of policy.yaml).") in flat
    assert "no leading / or ~" not in flat


def test_safety_check_warns_about_an_unanchored_safety_path(
        monkeypatch, capsys, home):
    pol = _policy(home, "safety:\n  deny_paths_no_access: "
                        "['**/*.pem', /srv/ok/**, ~/ok/**, ~nobody-x/y]\n")
    flat = " ".join(_doctor_out(monkeypatch, capsys, pol).split())
    assert ("safety path **/*.pem in policy.yaml has no leading / or ~, so "
            "it can match in any folder: every Grep and Glob of every role "
            "except owner is denied while it is there.") in flat
    # ~user that is no user expands to nothing: the search check reads it
    # as unanchored too.
    assert "safety path ~nobody-x/y in policy.yaml" in flat
    assert "safety path /srv/ok/**" not in flat
    assert "safety path ~/ok/**" not in flat


def test_the_plain_doctor_run_warns_about_it_too(monkeypatch, capsys, home):
    import aipager.config as cfg
    from aipager import doctor
    monkeypatch.setattr(doctor, "run_all", lambda: [
        doctor.CheckResult(doctor.OK, "config", detail=["ok"])])
    monkeypatch.setattr(cfg, "SCOPES", None, raising=False)
    monkeypatch.setattr(cfg, "POLICY", _policy(
        home, "safety:\n  deny_paths_no_access: ['**/.env']\n"))
    doctor.cmd_doctor(argparse.Namespace())
    assert "safety path **/.env in policy.yaml has no leading" in \
        capsys.readouterr().out

    monkeypatch.setattr(cfg, "POLICY", _builtin_policy())
    doctor.cmd_doctor(argparse.Namespace())
    assert "no leading / or ~" not in capsys.readouterr().out


# ---- (7) the floor file is itself protected ---------------------------------

def test_the_floor_file_is_a_protected_path_for_every_non_owner():
    """``/tmp/claude-policy-.floor-<uid>.json`` sits under the built-in
    ``/tmp/claude-policy-*`` rule: no non-owner turn may read, rewrite or
    remove it."""
    real = _REAL_FLOOR_PATH
    assert real == f"/tmp/claude-policy-.floor-{os.getuid()}.json"
    for tool, inp in (("Read", {"file_path": real}),
                      ("Write", {"file_path": real, "content": "{}"}),
                      ("Edit", {"file_path": real})):
        reason = safety.path_violation(tool, inp,
                                       safety.DENY_PATHS_NO_ACCESS, ())
        assert reason == f"{tool} on protected path /tmp/claude-policy-*"
    assert safety.bash_violation(f"rm {real}", safety.DENY_BASH_PATTERNS)
