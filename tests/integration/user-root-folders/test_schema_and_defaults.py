"""aipager.yaml's ``root`` key and the default roots (design.md success
criteria 2 and 4; entrypoints.md "Config file" and "Default root").

Black-box: ``load_scopes``/``dump_scopes``, ``member_root.root_problem``,
``default_roots`` and ``effective_root`` only."""

from __future__ import annotations

import os
import stat
import sys

import pytest
import yaml

from aipager import member_root
from aipager import scope as _scope
from aipager.scope import Member, Scope, ScopeConfigError

kit = sys.modules["_userroot_kit"]  # loaded by this folder's conftest
GROUP, SARA, SARA2 = kit.GROUP, kit.SARA, kit.SARA2


def _write_cfg(path, scopes, root=None, *, idx=0):
    """Dump *scopes*, then set ``scopes[idx].root`` to *root* in the raw
    YAML (bypassing any validation on the dump side)."""
    _scope.dump_scopes(scopes, "TOK", path)
    data = yaml.safe_load(path.read_text())
    if root is not None:
        data["scopes"][idx]["root"] = root
    path.write_text(yaml.safe_dump(data))


def _one_dm(root=None):
    return [Scope(chat_id=SARA, kind="dm", label="Sara DM",
                  members=(Member(id=SARA, label="Sara", role="user"),), root=root)]


# ---- parse / dump round trip -----------------------------------------------------

def test_root_round_trips_through_the_file(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    want = str(home / "work" / "sara")
    _scope.dump_scopes(_one_dm(want), "TOK", p)
    assert _scope.load_scopes(p)[0][0].root == want


def test_unset_root_is_absent_from_the_file(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    _scope.dump_scopes(_one_dm(), "TOK", p)
    assert "root" not in yaml.safe_load(p.read_text())["scopes"][0]


def test_unset_root_loads_as_none(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    _scope.dump_scopes(_one_dm(), "TOK", p)
    assert _scope.load_scopes(p)[0][0].root is None


def test_tilde_root_is_expanded_on_load(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    _write_cfg(p, _one_dm(), "~/work/sara")
    assert _scope.load_scopes(p)[0][0].root == str(home / "work" / "sara")


def test_trailing_slash_root_is_accepted(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    _write_cfg(p, _one_dm(), str(home / "work" / "sara") + "/")
    assert os.path.normpath(_scope.load_scopes(p)[0][0].root) == str(home / "work" / "sara")


def test_nonexistent_root_under_home_is_accepted(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    _write_cfg(p, _one_dm(), str(home / "not" / "yet" / "there"))
    assert _scope.load_scopes(p)[0][0].root == str(home / "not" / "yet" / "there")


def test_root_outside_home_is_accepted(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    _write_cfg(p, _one_dm(), str(tmp_path / "srv" / "users" / "u1"))
    assert _scope.load_scopes(p)[0][0].root == str(tmp_path / "srv" / "users" / "u1")


# ---- refusals (parser) -----------------------------------------------------------

def _bad_roots(home):
    return {
        "slash": "/",
        "home": str(home),
        "home-trailing-slash": str(home) + "/",
        "above-home": str(home.parent),
        "relative": "work/sara",
        "dot-relative": "./work",
        "glob-star": str(home / "w" / "*"),
        "glob-question": str(home / "w?"),
        "glob-bracket": str(home / "[ab]"),
        "glob-brace": str(home / "{a,b}"),
        "claude-inside": str(home / ".claude" / "x"),
        "claude": str(home / ".claude"),
        "config-aipager": str(home / ".config" / "aipager"),
        "config-holds-protected": str(home / ".config"),
        "local-share-aipager": str(home / ".local" / "share" / "aipager"),
        "local-state-aipager": str(home / ".local" / "state" / "aipager"),
        "local-holds-protected": str(home / ".local"),
        "ssh-inside": str(home / ".ssh" / "x"),
        "ssh": str(home / ".ssh"),
        "tmp-claude": "/tmp/claude-1000/x",
        "tmp-claude-policy": "/tmp/claude-policy-x",
        "dotdot-to-home": str(home / "a" / ".."),
    }


BAD_IDS = ["slash", "home", "home-trailing-slash", "above-home", "relative",
           "dot-relative", "glob-star", "glob-question", "glob-bracket",
           "glob-brace", "claude-inside", "claude", "config-aipager",
           "config-holds-protected", "local-share-aipager",
           "local-state-aipager", "local-holds-protected", "ssh-inside", "ssh",
           "tmp-claude", "tmp-claude-policy", "dotdot-to-home"]


@pytest.mark.parametrize("case", BAD_IDS)
def test_parser_refuses_wide_root(tmp_path, home, case):
    p = tmp_path / "aipager.yaml"
    _write_cfg(p, _one_dm(), _bad_roots(home)[case])
    with pytest.raises(ScopeConfigError, match="root"):
        _scope.load_scopes(p)


def test_parser_refuses_a_non_string_root(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    _write_cfg(p, _one_dm(), 42)
    with pytest.raises(ScopeConfigError, match="root"):
        _scope.load_scopes(p)


def test_parser_refuses_a_bad_root_in_a_group(tmp_path, home):
    p = tmp_path / "aipager.yaml"
    grp = [Scope(chat_id=GROUP, kind="group", label="team",
                 members=(Member(id=5, label="bob", role="user"),))]
    _write_cfg(p, grp, "/")
    with pytest.raises(ScopeConfigError, match="root"):
        _scope.load_scopes(p)


@pytest.mark.parametrize("case", BAD_IDS)
def test_root_problem_names_a_reason(home, case):
    path = _bad_roots(home)[case]
    assert member_root.root_problem(path)


@pytest.mark.parametrize("rel", ["work/sara", "projects/a/b", "aipager-users/x"])
def test_root_problem_accepts_a_plain_folder_under_home(home, rel):
    assert member_root.root_problem(str(home / rel)) is None


def test_root_problem_accepts_a_folder_named_like_a_protected_one(home):
    # ~/.claudex is not ~/.claude (prefix string, not a path component).
    assert member_root.root_problem(str(home / ".claudex")) is None


# ---- defaults ---------------------------------------------------------------------

def test_dm_default_root_is_under_aipager_users(home):
    sl = _one_dm()
    assert member_root.effective_root(sl[0], sl) == str(home / "aipager-users" / "sara")


def test_group_default_root_is_group_label(home):
    sl = [Scope(chat_id=GROUP, kind="group", label="team",
                members=(Member(id=5, label="bob", role="user"),))]
    assert member_root.effective_root(sl[0], sl) == str(home / "aipager-users" / "group-team")


def test_configured_root_wins_over_the_default(home):
    sl = _one_dm(str(home / "mine"))
    assert member_root.effective_root(sl[0], sl) == str(home / "mine")


@pytest.mark.parametrize("label,name", [
    ("../../etc", "etc"),
    ("Sara Jones", "sara-jones"),
    ("a/b", "a-b"),
    (".hidden", "hidden"),
])
def test_unsafe_label_sanitised(home, label, name):
    sl = [Scope(chat_id=SARA, kind="dm", label=f"{label} DM",
                members=(Member(id=SARA, label=label, role="user"),))]
    assert member_root.effective_root(sl[0], sl) == str(home / "aipager-users" / name)


@pytest.mark.parametrize("label", ["../..", "..", "/", "...", "---", "😀"])
def test_degenerate_label_stays_one_folder_under_aipager_users(home, label):
    sl = [Scope(chat_id=SARA, kind="dm", label="x",
                members=(Member(id=SARA, label=label, role="user"),))]
    got = member_root.effective_root(sl[0], sl)
    assert os.path.dirname(got) == str(home / "aipager-users") and os.path.basename(got) not in ("", ".", "..")


def test_two_scopes_never_share_default(scopes):
    by_chat = member_root.default_roots(scopes)
    assert by_chat[SARA] != by_chat[SARA2]


def test_colliding_defaults_carry_the_chat_id(scopes, home):
    by_chat = member_root.default_roots(scopes)
    assert {os.path.basename(by_chat[SARA]), os.path.basename(by_chat[SARA2])} == {
        "sara-3", "sara-4"}


def test_negative_group_id_is_written_n(home):
    sl = [Scope(chat_id=-100, kind="group", label="Team",
                members=(Member(id=5, label="bob", role="user"),)),
          Scope(chat_id=-200, kind="group", label="team",
                members=(Member(id=6, label="cy", role="user"),))]
    by_chat = member_root.default_roots(sl)
    assert sorted(os.path.basename(v) for v in by_chat.values()) == [
        "group-team-n100", "group-team-n200"]


def test_a_dm_default_never_lands_on_another_scopes_configured_root(home):
    taken = str(home / "aipager-users" / "sara")
    sl = [Scope(chat_id=SARA, kind="dm", label="Sara DM",
                members=(Member(id=SARA, label="Sara", role="user"),)),
          Scope(chat_id=99, kind="dm", label="zed DM",
                members=(Member(id=99, label="zed", role="user"),), root=taken)]
    assert member_root.effective_root(sl[0], sl) != taken


def test_every_scope_gets_a_default(scopes):
    assert set(member_root.default_roots(scopes)) >= {s.chat_id for s in scopes
                                                      if s.root is None}


def test_default_root_is_not_created_by_resolving_it(scopes, home):
    member_root.default_roots(scopes)
    member_root.effective_root(scopes[2], scopes)
    assert not (home / "aipager-users").exists()


def test_default_root_created_0700_on_first_use(gbot, run_async, scopes, home, daemon_in_home):
    from aipager.bot import new_flow
    from unittest.mock import AsyncMock, MagicMock
    root = member_root.effective_root(scopes[2], scopes)
    bot = gbot()
    send = AsyncMock(return_value=MagicMock(message_id=900))
    run_async(new_flow._open_card(bot, SARA, SARA, send))
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700


def test_default_root_does_not_exist_before_first_use(gbot, scopes, home, daemon_in_home):
    gbot()
    assert not os.path.exists(member_root.effective_root(scopes[2], scopes))
