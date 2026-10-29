"""Every launch surface holds a role without ``can_manage`` to its
members' root folder: the /new folder page and New folder, the /settings
defaults, ``create_session`` (so /new, Replace and the Mini App), and the
relaunch of a session outside the root (resume, the /new conflict card,
Replace, the session menu's Restart, every mode switch, and the Mini
App's resume, restart and perms routes). Owner and admin keep today's
behaviour. A throwaway HOME; the daemon runs in it, as under systemd."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy, preferences
from aipager.bot import new_flow, session_parity
from aipager.dtach import inject
from aipager.miniapp import launch
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

GROUP = -1001
DM = 1
ALICE, ADA, BOB, RO = 1, 2, 3, 4
OUT = "claude-x__g1001"
OUTSIDE = ("This session works outside your folder ~/aipager-users/group-team, so "
           "your role can't resume, restart or switch it. Ask an admin.")
START = ("Your role can only start sessions in your folder ~/aipager-users/group-team "
         "or a folder inside it.")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home" / "op"
    (h / "aipager").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(h))
    launch._created.clear()
    yield h
    launch._created.clear()


@pytest.fixture
def root(home):
    return home / "aipager-users" / "group-team"


def _scopes():
    return [Scope(chat_id=GROUP, kind="group", label="team", members=(
                Member(id=ALICE, label="alice", role="owner"),
                Member(id=ADA, label="ada", role="admin"),
                Member(id=BOB, label="bob", role="user"),
                Member(id=RO, label="ro", role="read_only"))),
            Scope(chat_id=DM, kind="dm", label="alice DM",
                  members=(Member(id=ALICE, label="alice", role="owner"),))]


@pytest.fixture
def gbot(mk_bot, home):
    def _mk(registry=None):
        bot = mk_bot(registry or SessionRegistry(), scopes=_scopes())
        bot.policy = policy.load_policy(Path("/nonexistent/p.yaml"),
                                        Path("/nonexistent/p.d"))
        bot._app.bot.edit_message_text = AsyncMock()
        bot._update_bot_commands = AsyncMock()
        bot._maybe_update_bot_name = AsyncMock()
        return bot
    return _mk


@pytest.fixture
def fx(monkeypatch):
    """inject's process calls, recorded (nothing real runs)."""
    rec = {"launched": [], "killed": []}

    async def _launch(short, **kw):
        rec["launched"].append((short, kw))
        return True, ""

    async def _kill(name, *a, **k):
        rec["killed"].append(name)
        return True
    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr(inject, "kill_session", _kill)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "send_keys", AsyncMock(return_value=True))
    return rec


def _outside(bot, home, status=Status.IDLE):
    """The group's session in the operator's own repository."""
    s = bot.registry.get_or_create(OUT)
    s.label, s.scope_chat_id, s.scope_kind = "x", GROUP, "group"
    s.cwd, s.status, s.claude_session_id = str(home / "aipager"), status, "sid-1"
    return s


def _tap(user, data, *, chat=GROUP, msg_id=900):
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock(id=user)
    q.message = MagicMock(message_id=msg_id, chat=MagicMock(id=chat), chat_id=chat)
    q.message.edit_text = AsyncMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = MagicMock(id=chat, type="supergroup")
    u.effective_user = q.from_user
    u.message = None
    return u, q


def _answers(q):
    return [c.args[0] for c in q.answer.await_args_list if c.args]


# ---- the /new card ------------------------------------------------------------------

def _open(bot, run_async, uid):
    send = AsyncMock(return_value=MagicMock(message_id=900))
    run_async(new_flow._open_card(bot, GROUP, uid, send))
    return bot._new_wizard_pending[(GROUP, uid)]


def _nw(bot, run_async, uid, action):
    u, q = _tap(uid, f"_:{action}")
    run_async(new_flow.handle_callback(bot, u, q, "_", action))
    return q


def _labels(bot):
    kb = bot._app.bot.edit_message_text.await_args.kwargs["reply_markup"]
    return [b.text.removesuffix(" ✅") for row in kb.inline_keyboard for b in row]


def test_new_card_defaults_to_root(gbot, run_async, root):
    bot = gbot()
    preferences.set_new_session_default(GROUP, "cwd", str(root.parent.parent))
    pending = _open(bot, run_async, BOB)
    assert pending["cwd"] == str(root)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    # The owner keeps the chat's stored default.
    assert _open(bot, run_async, ALICE)["cwd"] == str(root.parent.parent)


def test_user_picker_lists_only_root(gbot, run_async, home, root):
    (root / "proj").mkdir(parents=True)
    (root / ".hidden").mkdir()
    reg = SessionRegistry()
    bot = gbot(reg)
    _outside(bot, home)
    _open(bot, run_async, BOB)
    _nw(bot, run_async, BOB, "nw:opt:path")
    labels = _labels(bot)
    assert labels[:2] == [new_flow._short_path(str(root)),
                          new_flow._short_path(str(root / "proj"))]
    assert new_flow._short_path(str(home / "aipager")) not in labels
    assert bot._new_wizard_pending[(GROUP, BOB)]["path_options"] == [
        str(root), str(root / "proj")]


def test_user_folder_page_has_no_default_row(gbot, run_async, home, root, monkeypatch):
    # The daemon in a project folder: the home folder rule alone (8.79)
    # would offer it.
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(home / "aipager"))
    bot = gbot()
    _open(bot, run_async, BOB)
    _nw(bot, run_async, BOB, "nw:opt:path")
    assert not any(b.startswith("Default folder") for b in _labels(bot))
    # A forged "default" tap is the root itself.
    bot._new_wizard_pending[(GROUP, BOB)]["cwd"] = None
    _nw(bot, run_async, BOB, "nw:path:default")
    assert bot._new_wizard_pending[(GROUP, BOB)]["cwd"] == str(root)


@pytest.mark.parametrize("uid", [ALICE, ADA])
def test_admin_picker_unchanged(gbot, run_async, home, uid):
    bot = gbot()
    _outside(bot, home)
    _open(bot, run_async, uid)
    _nw(bot, run_async, uid, "nw:opt:path")
    labels = _labels(bot)
    assert new_flow._short_path(str(home / "aipager")) in labels
    assert any(b.startswith("Default folder") for b in labels)
    assert not (home / "aipager-users").exists()


def test_new_folder_is_made_inside_the_root(gbot, run_async, mk_update, root):
    bot = gbot()
    _open(bot, run_async, BOB)
    _nw(bot, run_async, BOB, "nw:path:new")
    pending = bot._new_wizard_pending[(GROUP, BOB)]
    assert pending["new_folder_parent"] == str(root)
    run_async(new_flow.maybe_handle_text(
        bot, mk_update("myproj", chat_id=GROUP, user_id=BOB), MagicMock(), "myproj"))
    assert (root / "myproj").is_dir() and pending["cwd"] == str(root / "myproj")


def test_new_folder_outside_root_refused(gbot, run_async, mk_update, home, root):
    bot = gbot()
    _open(bot, run_async, BOB)
    _nw(bot, run_async, BOB, "nw:path:new")
    pending = bot._new_wizard_pending[(GROUP, BOB)]
    pending["new_folder_parent"] = str(home)            # a forged parent
    run_async(new_flow.maybe_handle_text(
        bot, mk_update("evil", chat_id=GROUP, user_id=BOB), MagicMock(), "evil"))
    assert not (home / "evil").exists()
    assert pending["cwd"] == str(root)


def test_defaults_screen_lists_root_for_user(gbot, run_async, home, root):
    bot = gbot()
    _outside(bot, home)

    def labels(viewer):
        _text, kb = new_flow.render_new_session_defaults(bot, GROUP, view="cwd",
                                                         viewer=viewer)
        return [b.text.removesuffix(" ✅") for row in kb.inline_keyboard for b in row]
    assert labels(BOB)[0] == new_flow._short_path(str(root))
    assert new_flow._short_path(str(home / "aipager")) not in labels(BOB)
    assert new_flow._short_path(str(home / "aipager")) in labels(ALICE)
    text, _kb = new_flow.render_new_session_defaults(bot, GROUP, viewer=BOB)
    assert new_flow._short_path(str(root)) in text


# ---- the one launch seam ------------------------------------------------------------

@pytest.mark.parametrize("where", ["home", "repo", "none"])
def test_create_session_outside_root_refused(gbot, run_async, home, fx, where):
    cwd = {"home": str(home), "repo": str(home / "aipager"), "none": None}[where]
    name, err = run_async(gbot().create_session(
        "x", scope_chat_id=GROUP, cwd=cwd, driver_user_id=BOB, mode="auto"))
    assert (name, err) == ("", START)
    assert fx["launched"] == []


def test_create_session_inside_root_starts(gbot, run_async, root, fx):
    (root / "p").mkdir(parents=True)
    name, err = run_async(gbot().create_session(
        "x", scope_chat_id=GROUP, cwd=str(root / "p"), driver_user_id=BOB, mode="auto"))
    assert err == "" and fx["launched"][0][1]["cwd"] == str(root / "p")


@pytest.mark.parametrize("uid", [ALICE, ADA])
def test_create_session_unchanged_for_owner_and_admin(gbot, run_async, home, fx, uid):
    run_async(gbot().create_session("x", scope_chat_id=GROUP, cwd=str(home / "aipager"),
                                    driver_user_id=uid, mode="auto"))
    assert len(fx["launched"]) == 1


# ---- relaunch refusals (Telegram) -----------------------------------------------------

def test_typed_resume_outside_root_refused(gbot, run_async, home, fx):
    bot = gbot()
    sess = _outside(bot, home, Status.GONE)
    replies = []

    async def _reply(text, **kw):
        replies.append(text)
    u, q = _tap(BOB, "x")
    run_async(bot._do_resume(label="x", reply_fn=_reply, update=u, query=q, sess=sess))
    assert replies == [OUTSIDE] and fx["launched"] == []
    replies.clear()
    u, q = _tap(ALICE, "x")
    run_async(bot._do_resume(label="x", reply_fn=_reply, update=u, query=q, sess=sess))
    assert len(fx["launched"]) == 1


def _conflict(bot, sess, author):
    bot._new_conflict_pending[sess.name] = {
        "user_id": author, "msg_id": 0, "prompt": "", "cwd": None,
        "mode": "ask", "model": None, "was_working": False}
    sess.tap_is_for_this_turn = lambda *_a, **_k: True


def test_conflict_resume_live_outside_root_refused(gbot, run_async, home, fx):
    bot = gbot()
    sess = _outside(bot, home)
    _conflict(bot, sess, BOB)
    bot.registry.set_target = MagicMock()
    u, q = _tap(BOB, f"{OUT}:new_resume")
    run_async(bot._handle_callback(u, None))
    assert OUTSIDE in _answers(q)
    bot.registry.set_target.assert_not_called()
    assert OUT in bot._new_conflict_pending          # the card can still Cancel


def test_replace_outside_root_refused_before_kill(gbot, run_async, home, fx):
    bot = gbot()
    sess = _outside(bot, home)
    _conflict(bot, sess, BOB)
    u, q = _tap(BOB, f"{OUT}:new_replace")
    run_async(bot._handle_callback(u, None))
    assert OUTSIDE in _answers(q)
    assert fx["killed"] == [] and fx["launched"] == []
    assert sess.cwd == str(home / "aipager")


def test_replace_inside_root_into_a_folder_outside_refused(gbot, run_async, home, root, fx):
    bot = gbot()
    sess = _outside(bot, home)
    root.mkdir(parents=True)
    sess.cwd = str(root)
    _conflict(bot, sess, BOB)
    bot._new_conflict_pending[OUT]["cwd"] = str(home / "aipager")
    u, q = _tap(BOB, f"{OUT}:new_replace")
    run_async(bot._handle_callback(u, None))
    assert START in _answers(q) and fx["killed"] == []


def test_menu_restart_outside_root_refused(gbot, run_async, home, fx):
    bot = gbot()
    sess = _outside(bot, home)
    bot._restart_session_core = AsyncMock()
    data = session_parity.session_cb(bot, GROUP, sess, f"restartok{sess.turn_key}")
    u, q = _tap(BOB, data)
    run_async(bot._handle_callback(u, None))
    assert OUTSIDE in _answers(q)
    bot._restart_session_core.assert_not_awaited()
    u, q = _tap(ADA, data)
    run_async(bot._handle_callback(u, None))
    bot._restart_session_core.assert_awaited_once()


@pytest.mark.parametrize("current,target", [("full", "ask"), ("auto", "ask"), ("ask", "auto")],
                         ids=["restart-out-of-full", "shift-tab-to-ask", "shift-tab-to-auto"])
@pytest.mark.parametrize("surface", ["callbacks", "new_flow", "mode_cmd"])
def test_mode_switch_outside_root_refused(gbot, run_async, home, fx, surface, current, target):
    """Every mode switch (a Full access restart, and the in-place Ask <->
    Auto one) of a session outside the member's root is refused."""
    bot = gbot()
    sess = _outside(bot, home)
    sess.perm_mode = current                         # none of these needs an admin
    sess.tap_is_for_this_turn = lambda *_a, **_k: True
    bot._perms_switch_core = AsyncMock()
    bot._mode_switch_core = AsyncMock()
    if surface == "callbacks":
        bot._perms_pending[OUT] = {"target_mode": target, "msg_id": 900,
                                   "label": "x", "turn": sess.turn_key}
        u, q = _tap(BOB, f"{OUT}:perms_confirm")
        run_async(bot._handle_callback(u, None))
    elif surface == "new_flow":
        u, q = _tap(BOB, "x")
        run_async(new_flow._handle_ready_callback(bot, u, q, OUT, f"rdy_{target}"))
    else:
        u, q = _tap(BOB, "x")
        run_async(bot._mode_flow(sess, target, q.message, may_full=False,
                                 chat_id=GROUP, query=q, requester=BOB))
    assert OUTSIDE in _answers(q)
    bot._perms_switch_core.assert_not_awaited()
    bot._mode_switch_core.assert_not_awaited()


# ---- the Mini App ----------------------------------------------------------------------

_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _init_data(user_id):
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


@pytest.fixture
def app(gbot, home, fx, monkeypatch):
    from aipager.miniapp.server import MiniAppServer
    monkeypatch.setattr("aipager.config.BOT_TOKEN", _TOKEN)
    bot = gbot()
    srv = MiniAppServer(bot, bot.registry, port=8765)
    srv.fx = fx
    return srv


def _call(srv, run_async, method, path, uid, body=None):
    from aiohttp.test_utils import TestClient, TestServer

    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=_init_data(uid), json=body)
            try:
                return resp.status, await resp.json()
            except Exception:          # a stubbed core's reply is not JSON
                return resp.status, None
        finally:
            await client.close()
    return run_async(_run())


def test_miniapp_options_root_only(app, run_async, home, root):
    _outside(app.bot, home)
    status, body = _call(app, run_async, "GET", "/api/session-options", BOB)
    assert status == 200
    assert body["directories"] == [str(root)] and body["default_directory"] == str(root)
    status, body = _call(app, run_async, "GET", "/api/session-options", ADA)
    assert str(home / "aipager") in body["directories"]


def test_miniapp_create_outside_root_400(app, run_async, home, root):
    status, body = _call(app, run_async, "POST", "/api/sessions", BOB,
                         {"name": "dev", "cwd": str(home / "aipager")})
    assert (status, body["detail"]) == (400, START)
    assert app.fx["launched"] == []
    # No folder chosen: the root.
    status, _body = _call(app, run_async, "POST", "/api/sessions", BOB, {"name": "dev"})
    assert status == 200 and app.fx["launched"][0][1]["cwd"] == str(root)


def test_miniapp_create_unchanged_for_the_admin(app, run_async, home):
    _outside(app.bot, home)
    status, _body = _call(app, run_async, "POST", "/api/sessions", ADA,
                          {"name": "dev", "cwd": str(home / "aipager")})
    assert status == 200


def test_miniapp_mkdir_outside_root_400(app, run_async, home, root):
    _outside(app.bot, home)
    status, body = _call(app, run_async, "POST", "/api/directories", BOB,
                         {"parent": str(home / "aipager"), "name": "evil"})
    assert (status, body["detail"]) == (400, START)
    assert not (home / "aipager" / "evil").exists()
    status, body = _call(app, run_async, "POST", "/api/directories", BOB,
                         {"parent": str(root), "name": "mine"})
    assert (status, body["path"]) == (200, str(root / "mine"))


@pytest.mark.parametrize("route,current,target", [
    ("resume", "full", None), ("restart", "full", None),
    ("perms", "full", "ask"),        # a restart, out of Full access
    ("perms", "auto", "ask"),        # in place (Shift+Tab)
    ("perms", "ask", "auto"),        # in place (Shift+Tab)
], ids=["resume", "restart", "perms-restart", "perms-shift-tab-ask", "perms-shift-tab-auto"])
def test_miniapp_relaunch_outside_root_403(app, run_async, home, route, current, target):
    sess = _outside(app.bot, home, Status.GONE if route == "resume" else Status.IDLE)
    sess.perm_mode = current
    cores = {"resume": "_do_resume_core", "restart": "_restart_session_core",
             "perms": "_mode_switch_core"}
    core = AsyncMock()
    setattr(app.bot, cores[route], core)
    body_in = {"mode": target} if target else None
    status, body = _call(app, run_async, "POST", f"/api/sessions/x/{route}", BOB, body_in)
    assert (status, body) == (403, {"error": "forbidden", "detail": OUTSIDE})
    core.assert_not_awaited()
    _call(app, run_async, "POST", f"/api/sessions/x/{route}", ADA, body_in)
    core.assert_awaited_once()


# ---- the turn's note ----------------------------------------------------------------------

def _notes(monkeypatch):
    from aipager import policy_snapshot
    seen = []

    def _fake(name, role, scope, member, **kw):
        seen.append((role, kw.get("root_bound")))
        return None
    monkeypatch.setattr(policy_snapshot, "write_note", _fake)
    return seen


def test_member_message_note_carries_group_root(gbot, run_async, home, root, fx,
                                                monkeypatch):
    """The root comes from the chat the member's role is read in (the
    group), not from the session's own chat (the owner's DM)."""
    bot = gbot()
    sess = bot.registry.get_or_create("claude-d1")
    sess.label, sess.scope_chat_id, sess.scope_kind = "d1", DM, "dm"
    sess.status = Status.IDLE
    seen = _notes(monkeypatch)
    run_async(bot._inject_prompt(sess, "hi", driver_user_id=BOB, chat_id=GROUP))
    role, bound = seen[-1]
    assert role.name == "user" and bound.root == str(root)
    assert str(home / "aipager-users" / "alice") in bound.deny
    run_async(bot._inject_prompt(sess, "hi", driver_user_id=ALICE, chat_id=GROUP))
    assert seen[-1][1] is None
