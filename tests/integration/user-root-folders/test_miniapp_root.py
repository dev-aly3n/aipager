"""The Mini App's folder, create, resume, restart and perms routes for a
restricted member (design.md success criteria 7 and 8, Mini App half;
entrypoints.md "HTTP routes").

Driven over HTTP (aiohttp TestServer, signed init data) against the
documented route table. Launches and kills are recorded, never run."""

from __future__ import annotations

import os
import stat
import sys
import time
from unittest.mock import AsyncMock

import pytest

from aipager import member_root
from aipager.dtach import inject
from aipager.state import Status

kit = sys.modules["_userroot_kit"]
OWNER, ADMIN, SARA, BOB, CY, HAL = kit.OWNER, kit.ADMIN, kit.SARA, kit.BOB, kit.CY, kit.HAL
GROUP = kit.GROUP


def _root(scopes, chat):
    s = next(x for x in scopes if x.chat_id == chat)
    return os.path.realpath(member_root.effective_root(s, scopes))


@pytest.fixture
def outside(home):
    p = home / "aipager"
    p.mkdir()
    return p


@pytest.fixture
def made(scopes):
    def _made(chat):
        root = _root(scopes, chat)
        for sub in ("alpha", "Beta", ".hidden"):
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        return root
    return _made


def _session(app, label, cwd, *, status, chat=GROUP, skip_perms=False):
    s = app.registry.get_or_create(f"claude-{label}")
    s.label = label
    s.scope_chat_id = chat
    s.cwd = str(cwd)
    s.status = status
    s.skip_perms = skip_perms
    s.claude_session_id = "uuid-1"
    if status == Status.GONE:
        s.gone_at = time.monotonic()
    return s


# ---- GET /api/session-options ---------------------------------------------------------

@pytest.mark.parametrize("who,chat", [(BOB, GROUP), (SARA, SARA), (HAL, HAL), (HAL, GROUP)],
                         ids=["user-group", "user-dm", "helper-dm", "helper-group"])
def test_miniapp_options_root_only(app, call, scopes, made, outside, who, chat):
    root = made(chat)
    _session(app, "prev", outside, status=Status.GONE, chat=chat)
    _st, body = call(app, "GET", "/api/session-options", who, chat=chat)
    assert sorted(body["directories"]) == sorted(
        [root, os.path.join(root, "alpha"), os.path.join(root, "Beta")])


def test_miniapp_options_default_directory_is_root(app, call, scopes, made):
    root = made(GROUP)
    _st, body = call(app, "GET", "/api/session-options", BOB, chat=GROUP)
    assert body["default_directory"] == root


def test_miniapp_options_creates_the_default_root_0700(app, call, scopes):
    root = _root(scopes, SARA)
    call(app, "GET", "/api/session-options", SARA, chat=SARA)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700


def test_miniapp_options_never_lists_another_members_root(app, call, scopes, made, hal_root):
    made(GROUP)
    os.makedirs(hal_root, exist_ok=True)
    _st, body = call(app, "GET", "/api/session-options", BOB, chat=GROUP)
    assert str(hal_root) not in body["directories"]


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_miniapp_options_unchanged_for_managers(app, call, scopes, made, outside, who):
    made(GROUP)
    _session(app, "prev", outside, status=Status.GONE)
    _st, body = call(app, "GET", "/api/session-options", who, chat=GROUP)
    assert str(outside) in body["directories"]


# ---- POST /api/sessions ----------------------------------------------------------------

def test_miniapp_create_outside_root_400(app, call, scopes, outside):
    status, _b = call(app, "POST", "/api/sessions", BOB,
                      {"name": "dev", "cwd": str(outside)}, chat=GROUP)
    assert status == 400


def test_miniapp_create_outside_root_error_body(app, call, scopes, outside):
    _s, body = call(app, "POST", "/api/sessions", BOB,
                    {"name": "dev", "cwd": str(outside)}, chat=GROUP)
    assert body == {"error": "bad_request",
                    "detail": kit.outside_root_msg(_root(scopes, GROUP))}


def test_miniapp_create_outside_root_launches_nothing(app, call, scopes, outside):
    call(app, "POST", "/api/sessions", BOB, {"name": "dev", "cwd": str(outside)}, chat=GROUP)
    app.launched.assert_not_awaited()


@pytest.mark.parametrize("kind", ["home", "slash", "prefix-sibling", "dotdot", "other-root",
                                  "trailing-dotdot"])
def test_miniapp_create_refuses_every_outside_shape(app, call, scopes, home, team_root,
                                                    hal_root, kind):
    os.makedirs(str(team_root) + "2", exist_ok=True)
    os.makedirs(hal_root, exist_ok=True)
    cwd = {"home": str(home), "slash": "/", "prefix-sibling": str(team_root) + "2",
           "dotdot": str(team_root / ".." / "team2"), "other-root": str(hal_root),
           "trailing-dotdot": str(team_root) + "/.."}[kind]
    status, _b = call(app, "POST", "/api/sessions", BOB, {"name": "dev", "cwd": cwd}, chat=GROUP)
    assert status == 400


def test_miniapp_create_refuses_a_symlink_out_of_the_root(app, call, scopes, made, outside):
    root = made(GROUP)
    link = os.path.join(root, "escape")
    os.symlink(outside, link)
    call(app, "POST", "/api/sessions", BOB, {"name": "dev", "cwd": link}, chat=GROUP)
    app.launched.assert_not_awaited()


def test_miniapp_create_empty_cwd_is_the_root(app, call, scopes):
    call(app, "POST", "/api/sessions", BOB, {"name": "dev", "cwd": ""}, chat=GROUP)
    assert os.path.realpath(app.launched.await_args.kwargs["cwd"]) == _root(scopes, GROUP)


def test_miniapp_create_missing_cwd_is_the_root(app, call, scopes):
    call(app, "POST", "/api/sessions", BOB, {"name": "dev"}, chat=GROUP)
    assert os.path.realpath(app.launched.await_args.kwargs["cwd"]) == _root(scopes, GROUP)


def test_miniapp_create_in_a_subfolder_launches_there(app, call, scopes, made):
    root = made(GROUP)
    call(app, "POST", "/api/sessions", BOB,
         {"name": "dev", "cwd": os.path.join(root, "alpha")}, chat=GROUP)
    assert app.launched.await_args.kwargs["cwd"] == os.path.join(root, "alpha")


def test_miniapp_create_in_the_dm_uses_the_dm_root(app, call, scopes):
    call(app, "POST", "/api/sessions", HAL, {"name": "dev", "cwd": ""}, chat=HAL)
    assert os.path.realpath(app.launched.await_args.kwargs["cwd"]) == _root(scopes, HAL)


def test_miniapp_create_dm_root_from_the_group_refused(app, call, scopes, hal_root):
    os.makedirs(hal_root, exist_ok=True)
    status, _b = call(app, "POST", "/api/sessions", HAL,
                      {"name": "dev", "cwd": str(hal_root)}, chat=GROUP)
    assert status == 400


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_miniapp_create_outside_root_allowed_for_managers(app, call, outside, who):
    call(app, "POST", "/api/sessions", who, {"name": "dev", "cwd": str(outside)}, chat=GROUP)
    app.launched.assert_awaited_once()


# ---- POST /api/directories -----------------------------------------------------------

def test_miniapp_mkdir_outside_root_400(app, call, scopes, outside):
    status, _b = call(app, "POST", "/api/directories", BOB,
                      {"parent": str(outside), "name": "x"}, chat=GROUP)
    assert status == 400


def test_miniapp_mkdir_outside_root_error_kind(app, call, scopes, outside):
    _s, body = call(app, "POST", "/api/directories", BOB,
                    {"parent": str(outside), "name": "x"}, chat=GROUP)
    assert body.get("error") == "bad_request"


def test_miniapp_mkdir_outside_root_makes_nothing(app, call, scopes, outside):
    call(app, "POST", "/api/directories", BOB, {"parent": str(outside), "name": "x"}, chat=GROUP)
    assert not (outside / "x").exists()


def test_miniapp_mkdir_in_a_prefix_sibling_makes_nothing(app, call, scopes, team_root):
    sib = str(team_root) + "2"
    os.makedirs(sib)
    call(app, "POST", "/api/directories", BOB, {"parent": sib, "name": "x"}, chat=GROUP)
    assert not os.path.exists(os.path.join(sib, "x"))


@pytest.mark.parametrize("name", ["../escape", "a/../../escape", "/abs"])
def test_miniapp_mkdir_name_never_climbs_out(app, call, scopes, made, team_root, name):
    root = made(GROUP)
    call(app, "POST", "/api/directories", BOB, {"parent": root, "name": name}, chat=GROUP)
    assert not os.path.exists(os.path.join(os.path.dirname(root), "escape")) and \
        not os.path.exists("/abs")


def test_miniapp_mkdir_inside_root(app, call, scopes, made):
    root = made(GROUP)
    status, body = call(app, "POST", "/api/directories", BOB,
                        {"parent": root, "name": "newproj"}, chat=GROUP)
    assert (status, body.get("path")) == (200, os.path.join(root, "newproj"))


def test_miniapp_mkdir_in_a_subfolder(app, call, scopes, made):
    root = made(GROUP)
    status, body = call(app, "POST", "/api/directories", BOB,
                        {"parent": os.path.join(root, "alpha"), "name": "deep"}, chat=GROUP)
    assert (status, body.get("path")) == (200, os.path.join(root, "alpha", "deep"))


def test_miniapp_mkdir_creates_the_default_root(app, call, scopes):
    root = _root(scopes, SARA)
    call(app, "POST", "/api/directories", SARA, {"parent": root, "name": "p"}, chat=SARA)
    assert os.path.isdir(os.path.join(root, "p"))


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_miniapp_mkdir_unchanged_for_managers(app, call, home, who):
    status, _b = call(app, "POST", "/api/directories", who,
                      {"parent": str(home), "name": "mine"}, chat=GROUP)
    assert status == 200


# ---- resume / restart / perms of a session outside the root ------------------------------

def test_miniapp_resume_outside_root_403(app, call, scopes, outside):
    _session(app, "old", outside, status=Status.GONE)
    status, _b = call(app, "POST", "/api/sessions/old/resume", BOB, chat=GROUP)
    assert status == 403


def test_miniapp_resume_outside_root_body(app, call, scopes, outside):
    _session(app, "old", outside, status=Status.GONE)
    _s, body = call(app, "POST", "/api/sessions/old/resume", BOB, chat=GROUP)
    assert body == {"error": "forbidden",
                    "detail": kit.session_outside_msg(_root(scopes, GROUP))}


def test_miniapp_resume_outside_root_launches_nothing(app, call, outside):
    _session(app, "old", outside, status=Status.GONE)
    call(app, "POST", "/api/sessions/old/resume", BOB, chat=GROUP)
    app.launched.assert_not_awaited()


def test_miniapp_resume_inside_root_allowed(app, call, made):
    root = made(GROUP)
    _session(app, "old", os.path.join(root, "alpha"), status=Status.GONE)
    status, _b = call(app, "POST", "/api/sessions/old/resume", BOB, chat=GROUP)
    assert status == 200


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_miniapp_resume_outside_root_allowed_for_managers(app, call, outside, who):
    _session(app, "old", outside, status=Status.GONE)
    status, _b = call(app, "POST", "/api/sessions/old/resume", who, chat=GROUP)
    assert status == 200


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))


def test_miniapp_restart_outside_root_403(app, call, scopes, outside, live):
    _session(app, "old", outside, status=Status.IDLE)
    status, _b = call(app, "POST", "/api/sessions/old/restart", BOB, chat=GROUP)
    assert status == 403


def test_miniapp_restart_outside_root_body(app, call, scopes, outside, live):
    _session(app, "old", outside, status=Status.IDLE)
    _s, body = call(app, "POST", "/api/sessions/old/restart", BOB, chat=GROUP)
    assert body == {"error": "forbidden",
                    "detail": kit.session_outside_msg(_root(scopes, GROUP))}


def test_miniapp_restart_outside_root_kills_nothing(app, call, outside, live, seams):
    _session(app, "old", outside, status=Status.IDLE)
    call(app, "POST", "/api/sessions/old/restart", BOB, chat=GROUP)
    seams.kill.assert_not_awaited()


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_miniapp_restart_outside_root_allowed_for_managers(app, call, outside, live, who):
    _session(app, "old", outside, status=Status.IDLE)
    status, _b = call(app, "POST", "/api/sessions/old/restart", who, chat=GROUP)
    assert status == 200


def test_miniapp_perms_outside_root_403(app, call, scopes, outside, live):
    # Auto -> Ask: a switch a user may make (Ask -> Auto needs an admin,
    # which would be a 403 for another reason).
    _session(app, "old", outside, status=Status.IDLE, skip_perms=True)
    status, _b = call(app, "POST", "/api/sessions/old/perms", BOB, chat=GROUP)
    assert status == 403


def test_miniapp_perms_outside_root_body(app, call, scopes, outside, live):
    _session(app, "old", outside, status=Status.IDLE, skip_perms=True)
    _s, body = call(app, "POST", "/api/sessions/old/perms", BOB, chat=GROUP)
    assert body == {"error": "forbidden",
                    "detail": kit.session_outside_msg(_root(scopes, GROUP))}


def test_miniapp_perms_outside_root_keeps_the_mode(app, call, outside, live):
    sess = _session(app, "old", outside, status=Status.IDLE, skip_perms=True)
    call(app, "POST", "/api/sessions/old/perms", BOB, chat=GROUP)
    assert sess.skip_perms is True


def test_miniapp_perms_inside_root_allowed_for_user(app, call, made, live):
    root = made(GROUP)
    _session(app, "old", os.path.join(root, "alpha"), status=Status.IDLE, skip_perms=True)
    status, _b = call(app, "POST", "/api/sessions/old/perms", BOB, chat=GROUP)
    assert status == 200


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_miniapp_perms_outside_root_allowed_for_managers(app, call, outside, live, who):
    _session(app, "old", outside, status=Status.IDLE, skip_perms=True)
    status, _b = call(app, "POST", "/api/sessions/old/perms", who, chat=GROUP)
    assert status == 200
