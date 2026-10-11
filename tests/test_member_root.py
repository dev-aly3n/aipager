"""Members' root folders: the pure parts of ``aipager.member_root``
(defaults, the root rule, the folder itself, who is held to a root and
the bound the hook gets). Every test runs with ``HOME`` pointed at a
throwaway folder under ``tmp_path``."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from aipager import member_root, policy, safety
from aipager.scope import Member, Scope

DM_SARA, DM_SARA2, DM_OWNER, GROUP = 101, 102, 1, -1001


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home" / "op"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


def _dm(chat_id, label, role="user", root=None):
    return Scope(chat_id=chat_id, kind="dm", label=f"{label} DM",
                 members=(Member(id=chat_id, label=label, role=role),), root=root)


def _group(label="team", root=None, chat_id=GROUP):
    return Scope(chat_id=chat_id, kind="group", label=label, members=(
        Member(id=DM_OWNER, label="aly", role="owner"),
        Member(id=7, label="ada", role="admin"),
        Member(id=DM_SARA, label="sara", role="user"),
        Member(id=9, label="ro", role="read_only"),
    ), root=root)


def _pol():
    return policy.load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))


def _bot(scopes):
    """The four things member_root reads off the daemon."""
    def scope_for(chat_id):
        return next((s for s in scopes or () if s.chat_id == chat_id), None)

    def member_in(scope, uid):
        return next((m for m in scope.members if m.id == uid), None) if scope else None

    def confined(uid, chat):
        return False
    return SimpleNamespace(scopes=scopes, policy=_pol(), _scope_for=scope_for,
                           _member_in_scope=member_in, _is_confined_user=confined)


# ---- defaults ------------------------------------------------------------

@pytest.mark.parametrize("label,want", [
    ("Sara", "sara"), ("sara!", "sara"), ("Dev Team #2", "dev-team-2"),
    ("..hidden", "hidden"), ("-_x-", "x"), ("../../etc", "etc"),
    ("علی", "member"), ("", "member"), ("a" * 80, "a" * 48),
])
def test_unsafe_label_sanitised(label, want):
    assert member_root.sanitise_label(label) == want


def test_defaults_for_a_dm_and_a_group(home):
    scopes = [_dm(DM_SARA, "Sara"), _group("Dev Team")]
    roots = member_root.default_roots(scopes)
    assert roots == {DM_SARA: str(home / "aipager-users" / "sara"),
                     GROUP: str(home / "aipager-users" / "group-dev-team")}


def test_two_scopes_never_share_default(home):
    """Labels that sanitise to the same name (case too) each get their
    chat id; a third one stays plain."""
    scopes = [_dm(DM_SARA, "Sara"), _dm(DM_SARA2, "sara!"), _dm(DM_OWNER, "aly", "owner"),
              _group("x", chat_id=-5), _group("X", chat_id=-6)]
    roots = member_root.default_roots(scopes)
    assert roots[DM_SARA].endswith("/sara-101")
    assert roots[DM_SARA2].endswith("/sara-102")
    assert roots[DM_OWNER].endswith("/aly")
    assert roots[-5].endswith("/group-x-n5") and roots[-6].endswith("/group-x-n6")
    assert len(set(roots.values())) == len(roots)


def test_a_default_never_lands_on_a_configured_root(home):
    taken = str(home / "aipager-users" / "sara")
    scopes = [_dm(DM_SARA, "Sara"), _group("team", root=taken)]
    assert member_root.default_roots(scopes)[DM_SARA].endswith("/sara-101")


def test_effective_root_prefers_the_configured_one(home):
    g = _group(root="/srv/team")
    assert member_root.effective_root(g, [g]) == "/srv/team"
    d = _dm(DM_SARA, "sara")
    # A scope not yet in the list (the wizard adding it) gets its default.
    assert member_root.effective_root(d, [g]) == str(home / "aipager-users" / "sara")


# ---- the root rule ---------------------------------------------------------

@pytest.mark.parametrize("path,word", [
    ("/", "home folder"), ("~", "home folder"), ("HOME/..", "home folder"),
    ("relative/x", "absolute"), ("~/x*", "wildcard"), ("~/a[b]", "wildcard"),
    ("~/{a,b}", "wildcard"), ("~/.claude/x", "protected"),
    ("~/.config/aipager", "protected"), ("~/.config", "protected"),
    ("~/.ssh/x", "protected"), ("~/.git-credentials", "protected"),
    ("/tmp/claude-policy-x", "protected"), ("", "empty"), ("~/a\x00b", "NUL"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_wizard_refuses_root(home, path, word):
    path = path.replace("HOME", str(home))
    problem = member_root.root_problem(path)
    assert problem and word in problem


@pytest.mark.parametrize("path", ["~/aipager-users/sara", "~/not/there/yet", "/srv/team",
                                  "/tmp/team"])
def test_a_root_that_does_not_exist_yet_is_fine(home, path):
    assert member_root.root_problem(path) is None


def test_a_symlink_into_a_protected_folder_is_refused(home):
    (home / ".claude").mkdir()
    (home / "link").symlink_to(home / ".claude")
    assert "protected" in member_root.root_problem(str(home / "link"))


# ---- the folder itself -------------------------------------------------------

def test_default_root_created_0700_on_first_use(home):
    root = str(home / "aipager-users" / "sara")
    assert not os.path.exists(root)
    assert member_root.ensure_root(root) is True
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert member_root.ensure_root(root) is True        # again: still fine


@pytest.mark.parametrize("root", [member_root.NOWHERE, "", None, "/", "relative"])
def test_ensure_root_refuses_what_is_no_root(home, root):
    assert member_root.ensure_root(root) is False


def test_ensure_root_reports_a_file_in_the_way(home):
    (home / "aipager-users").write_text("x")
    assert member_root.ensure_root(str(home / "aipager-users" / "sara")) is False


def test_picker_hides_dotfolders(home):
    root = home / "aipager-users" / "sara"
    for name in ("b", "A", ".git", "c"):
        (root / name).mkdir(parents=True)
    (root / "file.txt").write_text("x")
    assert member_root.folder_choices(str(root)) == [
        str(root), str(root / "A"), str(root / "b"), str(root / "c")]


def test_picker_drops_symlink_out_of_root(home):
    root = home / "aipager-users" / "sara"
    (root / "in").mkdir(parents=True)
    (home / "secret").mkdir()
    (root / "out").symlink_to(home / "secret")
    (root / "back").symlink_to(root / "in")
    assert member_root.folder_choices(str(root)) == [str(root), str(root / "in")]


def test_picker_is_capped(home):
    root = home / "aipager-users" / "sara"
    for i in range(30):
        (root / f"d{i:02d}").mkdir(parents=True)
    out = member_root.folder_choices(str(root), cap=5)
    assert out[0] == str(root) and len(out) == 6


def test_picker_offers_nothing_without_a_root(home):
    assert member_root.folder_choices(member_root.NOWHERE) == []


def test_allowed_roots_with_a_root_ignores_the_chat_folders(home):
    from aipager.miniapp import launch
    from aipager.state import SessionRegistry
    reg = SessionRegistry()
    s = reg.get_or_create("claude-x")
    s.scope_chat_id, s.cwd = GROUP, str(home)
    root = str(home / "aipager-users" / "group-team")
    assert launch.allowed_roots(reg, GROUP, root=root) == [root]
    assert str(home) in launch.allowed_roots(reg, GROUP)


# ---- who is held to a root ----------------------------------------------------

def test_restricted_root_per_role(home):
    scopes = [_group(), _dm(DM_SARA, "sara")]
    bot = _bot(scopes)
    group_root = str(home / "aipager-users" / "group-team")
    assert member_root.restricted_root(bot, DM_OWNER, GROUP) is None
    assert member_root.restricted_root(bot, 7, GROUP) is None
    assert member_root.restricted_root(bot, DM_SARA, GROUP) == group_root
    assert member_root.restricted_root(bot, 9, GROUP) == group_root
    # In her own DM, her DM's root.
    assert member_root.restricted_root(bot, DM_SARA, DM_SARA) == str(
        home / "aipager-users" / "sara")
    # Not a member, no scope, unknown role: nowhere.
    assert member_root.restricted_root(bot, 555, GROUP) == member_root.NOWHERE
    assert member_root.restricted_root(bot, DM_SARA, -999) == member_root.NOWHERE
    assert member_root.restricted_root(bot, None, GROUP) == member_root.NOWHERE


def test_an_unknown_role_is_nowhere(home):
    g = Scope(chat_id=GROUP, kind="group", label="t",
              members=(Member(id=5, label="x", role="ghost"),))
    assert member_root.restricted_root(_bot([g]), 5, GROUP) == member_root.NOWHERE


def test_nobody_is_held_outside_scope_mode(home):
    assert member_root.restricted_root(_bot(None), DM_SARA, GROUP) is None
    assert member_root.relaunch_refusal(_bot(None), DM_SARA, GROUP, "/x") == ""


def test_launch_refusal(home):
    bot = _bot([_group()])
    root = home / "aipager-users" / "group-team"
    assert member_root.launch_refusal(bot, DM_SARA, GROUP, str(root)) == ""
    assert root.is_dir()                              # created on first use
    (root / "p").mkdir()
    assert member_root.launch_refusal(bot, DM_SARA, GROUP, str(root / "p")) == ""
    out = member_root.launch_refusal(bot, DM_SARA, GROUP, str(home))
    assert out == ("Your role can only start sessions in your folder "
                   "~/aipager-users/group-team or a folder inside it.")
    # A look-alike sibling is outside.
    (home / "aipager-users" / "group-team-x").mkdir()
    assert member_root.launch_refusal(
        bot, DM_SARA, GROUP, str(home / "aipager-users" / "group-team-x"))
    # The owner: today's home folder rule only (not confined here).
    assert member_root.launch_refusal(bot, DM_OWNER, GROUP, str(home)) == ""
    assert member_root.launch_refusal(bot, 555, GROUP, str(root)) == member_root.NO_ROOT


def test_launch_refusal_when_the_root_cannot_be_made(home):
    (home / "aipager-users").write_text("x")
    out = member_root.launch_refusal(_bot([_group()]), DM_SARA, GROUP, None)
    assert out.startswith("Your folder ~/aipager-users/group-team could not be prepared")


def test_relaunch_refusal(home, monkeypatch):
    from aipager.dtach import inject
    bot = _bot([_group(root="/srv/team")])
    assert member_root.relaunch_refusal(bot, DM_SARA, GROUP, "/srv/team/a") == ""
    out = member_root.relaunch_refusal(bot, DM_SARA, GROUP, "/home/aly/aipager")
    assert out.startswith("This session works outside your folder /srv/team")
    # No folder known: the daemon's own.
    monkeypatch.setattr(inject, "_PROJECT_DIR", "/srv/team")
    assert member_root.relaunch_refusal(bot, DM_SARA, GROUP, "") == ""
    for uid in (DM_OWNER, 7):
        assert member_root.relaunch_refusal(bot, uid, GROUP, "/home/aly/aipager") == ""


# ---- the hook's bound -----------------------------------------------------------

def test_snapshot_bound_only_without_can_manage(home):
    scopes = [_group(), _dm(DM_SARA, "sara")]
    pol = _pol()
    g = scopes[0]
    assert member_root.snapshot_bound(pol.get_role("owner"), g, scopes) is None
    assert member_root.snapshot_bound(pol.get_role("admin"), g, scopes) is None
    assert member_root.snapshot_bound(None, g, scopes) is None
    assert member_root.snapshot_bound(pol.get_role("user"), None, scopes) is None
    assert member_root.snapshot_bound(pol.get_role("user"), g, None) is None
    bound = member_root.snapshot_bound(pol.get_role("read_only"), g, scopes)
    assert bound.root == str(home / "aipager-users" / "group-team")
    assert bound.deny == (str(home / "aipager-users" / "sara"),)


def test_snapshot_bound_never_denies_its_own_or_a_nested_root(home):
    mine = _group(root="/srv/team")
    nested = _dm(DM_SARA, "sara", root="/srv/team/sara")
    same = _dm(DM_SARA2, "lee", root="/srv/team")
    other = _dm(DM_OWNER, "aly", root="/srv/other")
    bound = member_root.snapshot_bound(_pol().get_role("user"), mine,
                                       [mine, nested, same, other])
    assert bound.deny == ("/srv/other",)


def test_snapshot_bound_names_both_spellings_of_a_linked_root(home, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    mine = _group(root="/srv/team")
    other = _dm(DM_SARA, "sara", root=str(tmp_path / "link"))
    bound = member_root.snapshot_bound(_pol().get_role("user"), mine, [mine, other])
    assert set(bound.deny) == {str(tmp_path / "link"), str(real)}


def test_safety_root_helpers():
    assert safety.normalise_root(None) is None
    assert safety.normalise_root("/a/b/../c") == "/a/c"
    for bad in ("rel", "", 5, ["/a"], "/a\x00b"):
        assert safety.normalise_root(bad) == safety.ROOT_SENTINEL
    assert member_root.inside("/a/b", "/a") and not member_root.inside("/ab", "/a")
    assert not member_root.inside("/a", member_root.NOWHERE)


# ---- review iter 1 fixes ---------------------------------------------------------------

@pytest.mark.parametrize("path", ["/tmp/claude-1000", "/tmp/claude-1000/x",
                                  "/tmp/claude-1000/-home-op/abc/scratchpad"])
def test_claude_code_temp_folder_is_no_root(home, path):
    assert "Claude Code's temp folder" in member_root.root_problem(path)


def test_claude_code_temp_folder_under_its_env_tmpdir_is_no_root(home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(home / "ctmp"))
    monkeypatch.setenv("TMPDIR", "/var/tmp/x")
    assert member_root.root_problem(str(home / "ctmp" / "claude-1000" / "a"))
    assert member_root.root_problem("/var/tmp/x/claude-1000")
    # Beside them, a plain folder is fine.
    assert member_root.root_problem(str(home / "ctmp" / "work")) is None
    assert member_root.root_problem("/tmp/claudex") is None


def test_aipager_users_created_0700_too(home):
    root = home / "aipager-users" / "sara"
    assert member_root.ensure_root(str(root)) is True
    assert stat.S_IMODE(os.stat(home / "aipager-users").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700


def test_every_folder_ensure_root_creates_is_0700(home):
    root = home / "srv" / "teams" / "dev"
    assert member_root.ensure_root(str(root)) is True
    for p in (home / "srv", home / "srv" / "teams", root):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o700


def test_ensure_root_keeps_an_existing_parents_mode(home):
    (home / "aipager-users").mkdir()
    os.chmod(home / "aipager-users", 0o755)
    assert member_root.ensure_root(str(home / "aipager-users" / "sara")) is True
    assert stat.S_IMODE(os.stat(home / "aipager-users").st_mode) == 0o755


@pytest.mark.parametrize("chat", [None, 0])
def test_no_chat_keeps_owner_and_admin_unrestricted(home, chat):
    """rev-iter1-005: a caller with no chat id. An owner or admin keeps the
    rule they had before root folders; a restricted member gets nowhere."""
    bot = _bot([_group(), _dm(DM_SARA, "sara")])
    for uid in (DM_OWNER, 7):
        assert member_root.restricted_root(bot, uid, chat) is None
        assert member_root.relaunch_refusal(bot, uid, chat, "/srv/anything") == ""
        assert member_root.launch_refusal(bot, uid, chat, "/srv/anything") == ""
    for uid in (DM_SARA, 9, 555, None):
        assert member_root.restricted_root(bot, uid, chat) == member_root.NOWHERE
        assert member_root.relaunch_refusal(bot, uid, chat, "/srv/x") == \
            member_root.NO_ROOT


def test_an_unknown_chat_is_still_nowhere_for_the_owner(home):
    # Only "no chat" falls back: a chat that has no scope stays closed.
    bot = _bot([_group()])
    assert member_root.restricted_root(bot, DM_OWNER, -999) == member_root.NOWHERE


def test_pin_moved_defaults_skips_a_folder_that_cannot_be_a_root(home, monkeypatch):
    # A home folder with glob characters: its default is no valid root, so
    # pinning it would make aipager.yaml unloadable. Left unpinned.
    globby = home.parent / "op[1]"
    globby.mkdir()
    monkeypatch.setenv("HOME", str(globby))
    before = [_dm(DM_SARA, "Sara"), _dm(DM_SARA2, "sara!"), _dm(DM_OWNER, "aly", "owner")]
    assert member_root.root_problem(member_root.effective_root(before[1], before))
    after = member_root.pin_moved_defaults(before, before[:1] + before[2:])
    assert [s.root for s in after] == [None, None]


def test_pin_moved_defaults(home):
    users = home / "aipager-users"
    before = [_dm(DM_SARA, "Sara"), _dm(DM_SARA2, "sara!"), _dm(DM_OWNER, "aly", "owner")]
    after = member_root.pin_moved_defaults(before, before[:1] + before[2:])
    assert [s.root for s in after] == [str(users / "sara-101"), None]
    # Nothing moves: the list comes back as it was.
    assert member_root.pin_moved_defaults(before, before) == before
    # A new scope is never pinned; a set root is never changed.
    new = _dm(5, "aly")
    after = member_root.pin_moved_defaults(before, [*before, new])
    assert after[-1].root is None
    assert after[2].root == str(users / "aly")
    set_ = [replace_root(before[0], "/srv/s"), *before[1:]]
    assert member_root.pin_moved_defaults(before, set_)[0].root == "/srv/s"


def test_a_root_set_on_another_scopes_default_pins_it(home):
    """Another chat given the folder Sara's default is: her default would
    get her chat id; the pin keeps her where she was."""
    users = home / "aipager-users"
    before = [_dm(DM_SARA, "Sara"), _dm(DM_SARA2, "lee")]
    after = member_root.pin_moved_defaults(
        before, [before[0], replace_root(before[1], str(users / "sara"))])
    assert after[0].root == str(users / "sara")


def replace_root(scope, root):
    from dataclasses import replace
    return replace(scope, root=root)
