"""Iteration 2: a root in Claude Code's temp folder (``/tmp/claude-*``) is
refused, and ``~/aipager-users`` itself is made with mode 0700
(entrypoints.md "Config file" and "Side effects"; review-1 fixes).

Black-box: ``member_root.root_problem``, ``scope.load_scopes``, the /new
card and the Mini App session-options route. Nothing is created under the
real ``/tmp``: the paths are only judged, never made."""

from __future__ import annotations

import os
import stat
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from aipager import member_root
from aipager import scope as _scope
from aipager.bot import new_flow
from aipager.scope import Member, Scope, ScopeConfigError

kit = sys.modules["_userroot_kit"]
SARA, GROUP, BOB = kit.SARA, kit.GROUP, kit.BOB


def _one_dm_file(path, root):
    sl = [Scope(chat_id=SARA, kind="dm", label="Sara DM",
                members=(Member(id=SARA, label="Sara", role="user"),))]
    _scope.dump_scopes(sl, "TOK", path)
    data = yaml.safe_load(path.read_text())
    data["scopes"][0]["root"] = root
    path.write_text(yaml.safe_dump(data))


# ---- /tmp/claude-* -----------------------------------------------------------------

TMP_CLAUDE_REFUSED = {
    "uid-folder-itself": "/tmp/claude-1000",
    "inside-uid-folder": "/tmp/claude-1000/x",
    "deep-inside": "/tmp/claude-1000/-home-op-proj/abc/scratchpad",
    "other-uid": "/tmp/claude-0/x",
    "trailing-slash": "/tmp/claude-1000/",
    "dotdot-into-it": "/tmp/other/../claude-1000/x",
    "double-slash": "/tmp//claude-1000/x",
    "holds-it": "/tmp",
    "holds-it-trailing": "/tmp/",
}


@pytest.mark.parametrize("case", list(TMP_CLAUDE_REFUSED))
def test_root_problem_refuses_claude_temp_folder(home, case):
    assert member_root.root_problem(TMP_CLAUDE_REFUSED[case])


@pytest.mark.parametrize("case", list(TMP_CLAUDE_REFUSED))
def test_parser_refuses_claude_temp_folder(tmp_path, home, case):
    p = tmp_path / "aipager.yaml"
    _one_dm_file(p, TMP_CLAUDE_REFUSED[case])
    with pytest.raises(ScopeConfigError, match="root"):
        _scope.load_scopes(p)


TMP_LOOKALIKES = {
    "no-dash": "/tmp/claude",
    "no-dash-suffix": "/tmp/claudex/y",
    "elsewhere-in-tmp": "/tmp/members/sara",
    "claude-deeper-not-at-tmp": "/tmp/members/claude-1000",
}


@pytest.mark.parametrize("case", list(TMP_LOOKALIKES))
def test_root_problem_accepts_a_tmp_lookalike(home, case):
    assert member_root.root_problem(TMP_LOOKALIKES[case]) is None


def test_a_symlink_under_home_into_claude_temp_is_refused(home):
    """A root that resolves into /tmp/claude-* (dangling link: nothing is
    made in /tmp) is refused like the folder itself."""
    link = home / "sneaky"
    os.symlink("/tmp/claude-1000/zz-not-made", link)
    assert member_root.root_problem(str(link))


def test_tilde_relative_climb_into_claude_temp_is_refused(home):
    depth = len(home.parts) - 1
    climb = "/".join([".."] * depth)
    assert member_root.root_problem(f"~/{climb}/tmp/claude-1000/x")


# ---- ~/aipager-users mode ----------------------------------------------------------

def _open_new(bot, run_async, uid, chat):
    send = AsyncMock(return_value=MagicMock(message_id=900))
    run_async(new_flow._open_card(bot, chat, uid, send))


@pytest.mark.parametrize("mask", [0o022, 0o002, 0o000], ids=["022", "002", "000"])
def test_aipager_users_is_0700_whatever_the_umask(gbot, run_async, scopes, daemon_in_home,
                                                   home, mask):
    old = os.umask(mask)
    try:
        _open_new(gbot(), run_async, SARA, SARA)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(home / "aipager-users").st_mode) == 0o700


@pytest.mark.parametrize("mask", [0o022, 0o000], ids=["022", "000"])
def test_member_folder_is_0700_whatever_the_umask(gbot, run_async, scopes, daemon_in_home,
                                                  mask):
    root = member_root.effective_root(scopes[2], scopes)
    old = os.umask(mask)
    try:
        _open_new(gbot(), run_async, SARA, SARA)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700


def test_aipager_users_0700_from_the_mini_app(app, call, home):
    old = os.umask(0o002)
    try:
        call(app, "GET", "/api/session-options", SARA, chat=SARA)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(home / "aipager-users").st_mode) == 0o700


def test_aipager_users_0700_when_a_second_member_arrives(gbot, run_async, scopes,
                                                         daemon_in_home, home):
    """The second member's first use (folder already there) leaves it 0700."""
    bot = gbot()
    old = os.umask(0o002)
    try:
        _open_new(bot, run_async, SARA, SARA)
        _open_new(bot, run_async, kit.SARA2, kit.SARA2)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(home / "aipager-users").st_mode) == 0o700


def test_no_world_or_group_bits_on_aipager_users(gbot, run_async, scopes, daemon_in_home,
                                                 home):
    old = os.umask(0o000)
    try:
        _open_new(gbot(), run_async, SARA, SARA)
    finally:
        os.umask(old)
    assert os.stat(home / "aipager-users").st_mode & 0o077 == 0
