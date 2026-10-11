"""The Mini App server side of the three modes: session-options defaults
(criterion 22), create with mode / legacy skip_perms and the admin rule
(18, 19), the perms route's required target, in-place Ask <-> Auto with
409 while a prompt is open (7), restart for Full access (15), the detail
payload, and resume of a Full access session by a non-admin (19)."""

from __future__ import annotations

from pathlib import Path
import pytest

from aipager import preferences
from aipager.dtach import inject
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

from _tm3 import BACKTAB, BOT_TOKEN, hdr, prepare_bot, quiet_restart, settle_tasks

CHAT = -100
ADMIN, MEMBER, READONLY = 555, 777, 888
SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def server(mk_bot, tmp_path, monkeypatch, pty):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    quiet_restart(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(project))
    registry = SessionRegistry()
    scope = Scope(chat_id=CHAT, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="user"),
        Member(id=READONLY, label="cy", role="read_only"),
    ))
    bot = prepare_bot(mk_bot(registry, scopes=[scope]))
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    seed = registry.get_or_create("claude-seed__g100")
    seed.label, seed.scope_chat_id, seed.status = "seed", CHAT, Status.GONE
    seed.cwd = str(project)
    srv = MiniAppServer(bot, registry, port=8765)
    srv._project_dir = str(project)
    return srv


def _sess(server, label="dev", *, mode="ask", status=Status.IDLE):
    s = server.registry.get_or_create(f"claude-{label}__g100")
    s.label, s.scope_chat_id, s.scope_kind = label, CHAT, "group"
    s.status, s.perm_mode = status, mode
    s.claude_session_id = SID
    s.cwd = server._project_dir
    return s


def _call(server, run_async, method, path, user, body=None):
    from aiohttp.test_utils import TestClient, TestServer

    async def _go():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            kw = {"headers": hdr(user)}
            if body is not None:
                kw["json"] = body
            resp = await getattr(client, method)(path, **kw)
            try:
                data = await resp.json()
            except Exception:
                data = await resp.text()
            await settle_tasks()
            return resp.status, data
        finally:
            await client.close()
    return run_async(_go())


def _chat_lines(server) -> str:
    return "\n".join(str(c.kwargs.get("text", ""))
                     for c in server.bot._app.bot.send_message.await_args_list)


# ---- /api/session-options -----------------------------------------------------

@pytest.mark.parametrize("user", [ADMIN, MEMBER], ids=["admin", "member"])
def test_session_options_default_mode_is_auto(server, run_async, user):
    status, data = _call(server, run_async, "get", "/api/session-options", user)
    assert (status, data.get("default_mode")) == (200, "auto")


@pytest.mark.parametrize("user,expected", [(ADMIN, True), (MEMBER, False)],
                         ids=["admin", "member"])
def test_session_options_can_use_full(server, run_async, user, expected):
    _s, data = _call(server, run_async, "get", "/api/session-options", user)
    assert data.get("can_use_full") is expected


def test_session_options_drops_can_use_auto(server, run_async):
    _s, data = _call(server, run_async, "get", "/api/session-options", ADMIN)
    assert "can_use_auto" not in data


@pytest.mark.parametrize("stored,user,expected", [
    ("ask", ADMIN, "ask"), ("ask", MEMBER, "ask"),
    ("full", ADMIN, "full"), ("full", MEMBER, "auto"),
    ("auto", MEMBER, "auto"),
], ids=["ask-admin", "ask-member", "full-admin", "full-member", "auto-member"])
def test_session_options_follows_the_chat_default(server, run_async, stored, user, expected):
    preferences.set_new_session_default(CHAT, "mode", stored)
    _s, data = _call(server, run_async, "get", "/api/session-options", user)
    assert data.get("default_mode") == expected


# ---- POST /api/sessions ----------------------------------------------------

@pytest.mark.parametrize("body,user,expected", [
    ({}, MEMBER, "auto"),
    ({"mode": "ask"}, MEMBER, "ask"),
    ({"mode": "auto"}, MEMBER, "auto"),
    ({"mode": "full"}, ADMIN, "full"),
    ({"skip_perms": True}, ADMIN, "full"),
    ({"skip_perms": False}, MEMBER, "auto"),
    ({"mode": "ask", "skip_perms": True}, MEMBER, "ask"),
], ids=["none-member", "ask", "auto", "full-admin", "legacy-true-admin",
        "legacy-false-member", "mode-wins-over-legacy"])
def test_create_launches_in_the_requested_mode(server, run_async, pty, body, user, expected):
    status, data = _call(server, run_async, "post", "/api/sessions", user,
                         {"name": "made", **body})
    assert (status, pty.launched_modes) == (200, [expected]), data


@pytest.mark.parametrize("body", [{"mode": "full"}, {"skip_perms": True}],
                         ids=["mode-full", "legacy-skip-perms"])
def test_member_create_in_full_access_is_403(server, run_async, pty, body):
    status, _d = _call(server, run_async, "post", "/api/sessions", MEMBER,
                       {"name": "made", **body})
    assert status == 403


@pytest.mark.parametrize("body", [{"mode": "full"}, {"skip_perms": True}],
                         ids=["mode-full", "legacy-skip-perms"])
def test_member_create_in_full_access_launches_nothing(server, run_async, pty, body):
    _call(server, run_async, "post", "/api/sessions", MEMBER, {"name": "made", **body})
    assert pty.launched == []


def test_member_create_in_full_access_names_the_reason(server, run_async, pty):
    _s, data = _call(server, run_async, "post", "/api/sessions", MEMBER,
                     {"name": "made", "mode": "full"})
    assert "Full access mode requires admin." in str(data)


@pytest.mark.parametrize("mode", ["bypass", "", True, "AUTO", "default", None],
                         ids=["unknown", "empty", "bool", "wrong-case", "claude-id", "null"])
def test_create_with_an_invalid_mode_is_400(server, run_async, pty, mode):
    status, _d = _call(server, run_async, "post", "/api/sessions", ADMIN,
                       {"name": "made", "mode": mode})
    assert (status, pty.launched) == (400, [])


@pytest.mark.parametrize("stored,user,expected", [
    ("ask", MEMBER, "ask"), ("full", MEMBER, "auto"), ("full", ADMIN, "full"),
], ids=["ask-member", "full-member", "full-admin"])
def test_create_without_a_mode_follows_the_chat_default(server, run_async, pty, stored,
                                                        user, expected):
    preferences.set_new_session_default(CHAT, "mode", stored)
    _call(server, run_async, "post", "/api/sessions", user, {"name": "made"})
    assert pty.launched_modes == [expected]


def test_create_chat_line_names_the_mode(server, run_async, pty):
    _call(server, run_async, "post", "/api/sessions", MEMBER, {"name": "made"})
    assert "🤖 Auto mode" in _chat_lines(server)


# ---- POST /api/sessions/{label}/perms ------------------------------------------

@pytest.mark.parametrize("body", [{}, {"mode": None}, {"mode": "bypass"}, {"mode": True},
                                  {"mode": ""}],
                         ids=["missing", "null", "unknown", "bool", "empty"])
def test_perms_without_a_valid_mode_is_400(server, run_async, pty, body):
    _sess(server, mode="ask")
    status, _d = _call(server, run_async, "post", "/api/sessions/dev/perms", ADMIN, body)
    assert status == 400


def test_perms_without_a_body_is_400(server, run_async, pty):
    _sess(server, mode="ask")
    status, _d = _call(server, run_async, "post", "/api/sessions/dev/perms", ADMIN)
    assert status == 400


def test_perms_without_a_mode_changes_nothing(server, run_async, pty):
    s = _sess(server, mode="ask")
    _call(server, run_async, "post", "/api/sessions/dev/perms", ADMIN, {})
    assert (pty.runs, pty.launched, s.perm_mode) == ([], [], "ask")


def test_perms_ask_to_auto_is_sent_in_place(server, run_async, pty):
    _sess(server, mode="ask")
    status, data = _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER,
                         {"mode": "auto"})
    assert (status, data.get("status"), data.get("mode"), data.get("pending")) == (
        200, "sent", "auto", True), data


def test_perms_ask_to_auto_presses_three(server, run_async, pty):
    _sess(server, mode="ask")
    _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER, {"mode": "auto"})
    assert (pty.presses, pty.launched, pty.killed) == ([BACKTAB] * 3, [], [])


def test_perms_auto_to_ask_presses_one(server, run_async, pty):
    _sess(server, mode="auto")
    _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER, {"mode": "ask"})
    assert pty.presses == [BACKTAB]


def test_perms_in_place_while_busy_is_sent(server, run_async, pty):
    _sess(server, mode="ask", status=Status.BUSY)
    status, data = _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER,
                         {"mode": "auto"})
    assert (status, data.get("status")) == (200, "sent")


def test_perms_in_place_while_a_prompt_is_open_is_409(server, run_async, pty):
    _sess(server, mode="ask", status=Status.INTERACTIVE)
    status, _d = _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER,
                       {"mode": "auto"})
    assert status == 409


def test_perms_in_place_while_a_prompt_is_open_writes_nothing(server, run_async, pty):
    _sess(server, mode="ask", status=Status.INTERACTIVE)
    _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER, {"mode": "auto"})
    assert pty.runs == []


def test_perms_already_in_the_mode(server, run_async, pty):
    _sess(server, mode="auto")
    status, data = _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER,
                         {"mode": "auto"})
    assert (status, data.get("status"), pty.runs) == (200, "already", [])


def test_member_perms_to_full_is_403(server, run_async, pty):
    _sess(server, mode="ask")
    status, data = _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER,
                         {"mode": "full"})
    assert status == 403


def test_member_perms_to_full_names_the_reason(server, run_async, pty):
    _sess(server, mode="ask")
    _s, data = _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER,
                     {"mode": "full"})
    assert "Switching to Full access mode requires admin." in str(data)


def test_member_perms_to_full_changes_nothing(server, run_async, pty):
    s = _sess(server, mode="ask")
    _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER, {"mode": "full"})
    assert (pty.killed, pty.launched, pty.runs, s.perm_mode) == ([], [], [], "ask")


def test_readonly_perms_is_403(server, run_async, pty):
    _sess(server, mode="ask")
    status, _d = _call(server, run_async, "post", "/api/sessions/dev/perms", READONLY,
                       {"mode": "auto"})
    assert (status, pty.runs) == (403, [])


def test_admin_perms_to_full_restarts_in_full(server, run_async, pty):
    _sess(server, mode="ask")
    status, data = _call(server, run_async, "post", "/api/sessions/dev/perms", ADMIN,
                         {"mode": "full"})
    assert (status, data.get("status"), pty.launched_modes) == (200, "switched", ["full"]), data


@pytest.mark.parametrize("target", ["ask", "auto"], ids=["to-ask", "to-auto"])
def test_member_may_leave_full_access(server, run_async, pty, target):
    _sess(server, mode="full")
    status, data = _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER,
                         {"mode": target})
    assert (status, data.get("status"), pty.launched_modes) == (200, "switched", [target]), data


def test_perms_switch_posts_a_chat_line_naming_the_mode(server, run_async, pty):
    _sess(server, mode="ask")
    _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER, {"mode": "auto"})
    assert "🤖 Auto mode" in _chat_lines(server)


# ---- GET /api/sessions/{label} ----------------------------------------------

def test_detail_has_no_skip_perms(server, run_async):
    _sess(server, mode="ask")
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert "skip_perms" not in data


@pytest.mark.parametrize("mode", ["ask", "auto", "full"], ids=["ask", "auto", "full"])
def test_detail_reports_the_mode(server, run_async, mode):
    _sess(server, mode=mode)
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert data.get("mode") == mode


def test_detail_lists_three_modes(server, run_async):
    _sess(server, mode="ask")
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert [m.get("id") for m in data.get("modes", [])] == ["ask", "auto", "full"]


def test_detail_marks_the_current_mode(server, run_async):
    _sess(server, mode="auto")
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert [m.get("current") for m in data["modes"]] == [False, True, False]


@pytest.mark.parametrize("user,available", [(ADMIN, True), (MEMBER, False)],
                         ids=["admin", "member"])
def test_detail_full_access_availability(server, run_async, user, available):
    _sess(server, mode="ask")
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", user)
    full = next(m for m in data["modes"] if m["id"] == "full")
    assert full["available"] is available


def test_detail_restarts_flags(server, run_async):
    _sess(server, mode="ask")
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", ADMIN)
    assert {m["id"]: m["restarts"] for m in data["modes"]} == {
        "ask": False, "auto": False, "full": True}


def test_detail_restarts_flags_from_full(server, run_async):
    _sess(server, mode="full")
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", ADMIN)
    restarts = {m["id"]: m["restarts"] for m in data["modes"]}
    assert (restarts["ask"], restarts["auto"]) == (True, True)


def test_detail_mode_pending_after_a_sent_switch(server, run_async, pty):
    _sess(server, mode="ask")
    _call(server, run_async, "post", "/api/sessions/dev/perms", MEMBER, {"mode": "auto"})
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert (data.get("mode"), data.get("mode_pending")) == ("auto", True)


def test_detail_not_pending_at_rest(server, run_async):
    _sess(server, mode="ask")
    _s, data = _call(server, run_async, "get", "/api/sessions/dev", MEMBER)
    assert data.get("mode_pending") is False


# ---- resume ------------------------------------------------------------------

def _gone(s):
    import time
    s.status = Status.GONE
    s.gone_at = time.time() - 60


def test_member_resuming_a_full_access_session_gets_auto(server, run_async, pty):
    _gone(_sess(server, mode="full"))
    status, _d = _call(server, run_async, "post", "/api/sessions/dev/resume", MEMBER, {})
    assert (status, pty.launched_modes) == (200, ["auto"])


def test_member_resuming_a_full_access_session_chat_line_says_why(server, run_async, pty):
    _gone(_sess(server, mode="full"))
    _call(server, run_async, "post", "/api/sessions/dev/resume", MEMBER, {})
    assert "needs an admin" in _chat_lines(server), _chat_lines(server)


def test_admin_resuming_a_full_access_session_keeps_full(server, run_async, pty):
    _gone(_sess(server, mode="full"))
    _call(server, run_async, "post", "/api/sessions/dev/resume", ADMIN, {})
    assert pty.launched_modes == ["full"]
