"""Resume, Replace, Restart and the mode switch of a session outside the
member's root (design.md success criterion 8, Telegram half;
entrypoints.md "Telegram").

A ``user`` is refused with "This session works outside your folder ...";
nothing is killed or relaunched. The owner and the admin may do each.
Driven through the command handlers and ``bot._handle_callback``; the
PTY seams are recorded, the mode-switch core is a recorder (the gate is
documented as running before it)."""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import member_root
from aipager.bot import session_parity
from aipager.dtach import inject
from aipager.state import SessionRegistry, Status

kit = sys.modules["_userroot_kit"]
OWNER, ADMIN, BOB, HAL = kit.OWNER, kit.ADMIN, kit.BOB, kit.HAL
GROUP = kit.GROUP
NAME = "claude-old__g100"

_IDS = iter(range(7000, 999999))


@pytest.fixture
def outside(home):
    p = home / "aipager"
    p.mkdir()
    return p


def _root(scopes):
    g = next(s for s in scopes if s.chat_id == GROUP)
    return os.path.realpath(member_root.effective_root(g, scopes))


@pytest.fixture
def where(outside, scopes, team_root):
    """Folder kinds a session may work in."""
    def _w(kind):
        if kind == "outside":
            return str(outside)
        if kind == "inside":
            p = team_root / "proj"
            p.mkdir(parents=True, exist_ok=True)
            return str(p)
        if kind == "prefix-sibling":
            p = str(team_root) + "2"
            os.makedirs(p, exist_ok=True)
            return p
        raise AssertionError(kind)
    return _w


def _session(bot, cwd, *, status, skip_perms=False):
    s = bot.registry.get_or_create(NAME)
    s.label = "old"
    s.scope_chat_id = GROUP
    s.scope_kind = "group"
    s.cwd = str(cwd)
    s.status = status
    s.skip_perms = skip_perms
    s.claude_session_id = "uuid-old"
    if status == Status.GONE:
        s.gone_at = time.time() - 30
    return s


@pytest.fixture
def rbot(gbot, seams, daemon_in_home):
    bot = gbot(SessionRegistry())
    bot._inject_prompt = AsyncMock(return_value=True)
    return bot


def _msg(mk_update, text, user, chat=GROUP):
    u = mk_update(text, user_id=user, chat_id=chat, message_id=next(_IDS))
    u.effective_user.username = f"u{user}"
    u.effective_message = u.message
    u.effective_chat.type = "supergroup" if chat < 0 else "private"
    u.message.sender_chat = None
    u.message.from_user = u.effective_user
    u.message.photo = None
    u.message.document = None
    u.message.entities = ()
    u.message.caption_entities = ()
    u.message.parse_entities = MagicMock(return_value={})
    u.message.parse_caption_entities = MagicMock(return_value={})
    reply = MagicMock(message_id=next(_IDS))
    reply.chat = MagicMock(id=chat)
    reply.edit_text = AsyncMock()
    u.message.reply_text = AsyncMock(return_value=reply)
    u.reply = reply
    return u


def _tap(bot, run_async, data, user, chat=GROUP, message_id=None):
    query = MagicMock(data=data)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.from_user = MagicMock(id=user, username=f"u{user}")
    query.message = MagicMock(message_id=message_id or next(_IDS), text="")
    query.message.chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    query.message.chat_id = chat
    query.message.reply_text = AsyncMock()
    update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def _all_text(bot, *extra):
    return kit.texts_of(bot._app.bot.send_message, bot._app.bot.edit_message_text, *extra)


def _query_texts(q):
    return kit.texts_of(q.answer, q.edit_message_text, q.message.reply_text)


def _refused(texts, scopes):
    want = kit.session_outside_msg(_root(scopes))
    return any(want in t for t in texts)


# ---- /resume <name> --------------------------------------------------------------------

def test_typed_resume_outside_root_refused(rbot, run_async, mk_update, scopes, where):
    _session(rbot, where("outside"), status=Status.GONE)
    u = _msg(mk_update, "/resume old", BOB)
    run_async(rbot._handle_resume_cmd(u, MagicMock()))
    assert _refused(_all_text(rbot, u.message.reply_text, u.reply.edit_text), scopes)


def test_typed_resume_outside_root_launches_nothing(rbot, run_async, mk_update, where, seams):
    _session(rbot, where("outside"), status=Status.GONE)
    run_async(rbot._handle_resume_cmd(_msg(mk_update, "/resume old", BOB), MagicMock()))
    seams.launch.assert_not_awaited()


def test_typed_resume_of_a_prefix_sibling_is_refused(rbot, run_async, mk_update, where, seams):
    _session(rbot, where("prefix-sibling"), status=Status.GONE)
    run_async(rbot._handle_resume_cmd(_msg(mk_update, "/resume old", BOB), MagicMock()))
    seams.launch.assert_not_awaited()


def test_typed_resume_inside_root_is_allowed(rbot, run_async, mk_update, where, seams):
    _session(rbot, where("inside"), status=Status.GONE)
    run_async(rbot._handle_resume_cmd(_msg(mk_update, "/resume old", BOB), MagicMock()))
    seams.launch.assert_awaited_once()


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_typed_resume_outside_root_allowed_for_managers(rbot, run_async, mk_update, where,
                                                       seams, who):
    _session(rbot, where("outside"), status=Status.GONE)
    run_async(rbot._handle_resume_cmd(_msg(mk_update, "/resume old", who), MagicMock()))
    seams.launch.assert_awaited_once()


# ---- the /resume picker ---------------------------------------------------------------

def _picker_button(bot, run_async, mk_update, user):
    u = _msg(mk_update, "/resume", user)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    kb = u.message.reply_text.await_args.kwargs.get("reply_markup")
    for row in kb.inline_keyboard:
        for b in row:
            if "old" in b.text:
                return b.callback_data
    raise AssertionError("no picker row for the session")


def _picker_resume(bot, run_async, mk_update, user):
    """The picker row, then the "Resume as" card's first mode button."""
    data = _picker_button(bot, run_async, mk_update, user)
    card = next(_IDS)
    q1 = _tap(bot, run_async, data, user, message_id=card)
    kb = None
    for c in q1.edit_message_text.await_args_list:
        kb = c.kwargs.get("reply_markup") or kb
    out = [q1]
    if kb is not None:
        buttons = [b for row in kb.inline_keyboard for b in row]
        first = next((b for b in buttons if "Ask" in b.text), buttons[0])
        out.append(_tap(bot, run_async, first.callback_data, user, message_id=card))
    return out


def test_picker_resume_outside_root_refused(rbot, run_async, mk_update, where, seams, scopes):
    _session(rbot, where("outside"), status=Status.GONE)
    qs = _picker_resume(rbot, run_async, mk_update, BOB)
    assert _refused(sum((_query_texts(q) for q in qs), []) + _all_text(rbot), scopes)


def test_picker_resume_outside_root_launches_nothing(rbot, run_async, mk_update, where, seams):
    _session(rbot, where("outside"), status=Status.GONE)
    _picker_resume(rbot, run_async, mk_update, BOB)
    seams.launch.assert_not_awaited()


def test_picker_still_lists_the_outside_session(rbot, run_async, mk_update, where):
    _session(rbot, where("outside"), status=Status.GONE)
    assert _picker_button(rbot, run_async, mk_update, BOB)


def test_picker_resume_outside_root_allowed_for_owner(rbot, run_async, mk_update, where, seams):
    _session(rbot, where("outside"), status=Status.GONE)
    _picker_resume(rbot, run_async, mk_update, OWNER)
    seams.launch.assert_awaited_once()


# ---- the /new conflict card: Resume (GONE and running) and Replace -----------------------

def _conflict(bot, sess, user):
    bot._new_conflict_pending[sess.name] = {
        "user_id": user, "msg_id": 0, "prompt": "", "cwd": None,
        "skip_perms": False, "model": None, "was_working": False}
    sess.tap_is_for_this_turn = lambda *_a, **_k: True


@pytest.mark.parametrize("status", [Status.GONE, Status.IDLE], ids=["gone", "running"])
def test_conflict_resume_outside_root_refused(rbot, run_async, where, scopes, status):
    sess = _session(rbot, where("outside"), status=status)
    _conflict(rbot, sess, BOB)
    q = _tap(rbot, run_async, f"{NAME}:new_resume", BOB)
    assert _refused(_query_texts(q) + _all_text(rbot), scopes)


def test_conflict_resume_gone_outside_root_launches_nothing(rbot, run_async, where, seams):
    sess = _session(rbot, where("outside"), status=Status.GONE)
    _conflict(rbot, sess, BOB)
    _tap(rbot, run_async, f"{NAME}:new_resume", BOB)
    seams.launch.assert_not_awaited()


def test_conflict_resume_live_outside_root_refused(rbot, run_async, where, scopes, monkeypatch):
    """The running case only re-targets the chat: refused before that."""
    sess = _session(rbot, where("outside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _conflict(rbot, sess, BOB)
    q = _tap(rbot, run_async, f"{NAME}:new_resume", BOB)
    assert _refused(_query_texts(q) + _all_text(rbot), scopes)


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_conflict_resume_gone_outside_root_allowed_for_managers(rbot, run_async, where,
                                                               seams, who):
    sess = _session(rbot, where("outside"), status=Status.GONE)
    _conflict(rbot, sess, who)
    _tap(rbot, run_async, f"{NAME}:new_resume", who)
    seams.launch.assert_awaited_once()


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_conflict_resume_live_outside_root_not_refused_for_managers(rbot, run_async, where,
                                                                   scopes, monkeypatch, who):
    sess = _session(rbot, where("outside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _conflict(rbot, sess, who)
    q = _tap(rbot, run_async, f"{NAME}:new_resume", who)
    assert not _refused(_query_texts(q) + _all_text(rbot), scopes)


def test_replace_outside_root_refused_before_kill(rbot, run_async, where, seams, monkeypatch):
    sess = _session(rbot, where("outside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _conflict(rbot, sess, BOB)
    _tap(rbot, run_async, f"{NAME}:new_replace", BOB)
    seams.kill.assert_not_awaited()


def test_replace_outside_root_says_why(rbot, run_async, where, scopes, monkeypatch):
    sess = _session(rbot, where("outside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _conflict(rbot, sess, BOB)
    q = _tap(rbot, run_async, f"{NAME}:new_replace", BOB)
    assert _refused(_query_texts(q) + _all_text(rbot), scopes)


def test_replace_outside_root_allowed_for_owner(rbot, run_async, where, seams, monkeypatch):
    sess = _session(rbot, where("outside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _conflict(rbot, sess, OWNER)
    _tap(rbot, run_async, f"{NAME}:new_replace", OWNER)
    seams.kill.assert_awaited()


# ---- the session menu's Restart -------------------------------------------------------------

def _restart(bot, run_async, user):
    sess = bot.registry._sessions[NAME]
    card = next(_IDS)
    q1 = _tap(bot, run_async, session_parity.session_cb(bot, GROUP, sess, "restart"), user,
              message_id=card)
    q2 = _tap(bot, run_async,
              session_parity.session_cb(bot, GROUP, sess, f"restartok{sess.turn_key}"),
              user, message_id=card)
    return q1, q2


def test_menu_restart_outside_root_refused(rbot, run_async, where, scopes, seams, monkeypatch):
    _session(rbot, where("outside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    q1, q2 = _restart(rbot, run_async, BOB)
    assert _refused(_query_texts(q1) + _query_texts(q2) + _all_text(rbot), scopes)


def test_menu_restart_outside_root_kills_nothing(rbot, run_async, where, seams, monkeypatch):
    _session(rbot, where("outside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _restart(rbot, run_async, BOB)
    seams.kill.assert_not_awaited()


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_menu_restart_outside_root_allowed_for_managers(rbot, run_async, where, seams,
                                                        monkeypatch, who):
    _session(rbot, where("outside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _restart(rbot, run_async, who)
    seams.kill.assert_awaited()


def test_menu_restart_inside_root_allowed_for_user(rbot, run_async, where, seams, monkeypatch):
    _session(rbot, where("inside"), status=Status.IDLE)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _restart(rbot, run_async, BOB)
    seams.kill.assert_awaited()


# ---- the Ask/Auto mode switch -----------------------------------------------------------

def _switch_core(bot):
    async def _switch(s, target):
        s.skip_perms = target
        return SimpleNamespace(ok=True, reason="", err="", label=s.label)
    bot._perms_switch_core = AsyncMock(side_effect=_switch)
    return bot._perms_switch_core


def _typed_mode(bot, run_async, mk_update, user):
    u = _msg(mk_update, "/mode old ask", user)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    return u


def _ready_ask(bot, run_async, user):
    sess = bot.registry._sessions[NAME]
    return _tap(bot, run_async, session_parity.session_cb(bot, GROUP, sess, "rdy_ask"), user,
                message_id=sess.busy_msg_id or 600)


def _stop_switch(bot, run_async, user):
    sess = bot.registry._sessions[NAME]
    card = 503
    bot._perms_pending[NAME] = {"target_skip_perms": False, "msg_id": card,
                                "label": sess.label, "turn": sess.turn_key}
    return _tap(bot, run_async,
                session_parity.session_cb(bot, GROUP, sess, "perms_stop_switch"), user,
                message_id=card)


def _menu_perms(bot, run_async, user):
    sess = bot.registry._sessions[NAME]
    return _tap(bot, run_async,
                session_parity.session_cb(bot, GROUP, sess, f"modeask{sess.turn_key}"), user)


SWITCHES = {
    "typed": lambda b, r, mk, u: _typed_mode(b, r, mk, u),
    "new_flow": lambda b, r, mk, u: _ready_ask(b, r, u),
    "callbacks": lambda b, r, mk, u: _stop_switch(b, r, u),
    "menu": lambda b, r, mk, u: _menu_perms(b, r, u),
}


def _mode_session(bot, cwd, how):
    status = Status.BUSY if how == "callbacks" else Status.IDLE
    return _session(bot, cwd, status=status, skip_perms=True)


@pytest.mark.parametrize("how", list(SWITCHES))
def test_mode_switch_outside_root_refused(rbot, run_async, mk_update, where, monkeypatch,
                                          how):
    _mode_session(rbot, where("outside"), how)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    core = _switch_core(rbot)
    SWITCHES[how](rbot, run_async, mk_update, BOB)
    core.assert_not_awaited()


@pytest.mark.parametrize("how", list(SWITCHES))
def test_mode_switch_outside_root_says_why(rbot, run_async, mk_update, where, scopes,
                                           monkeypatch, how):
    _mode_session(rbot, where("outside"), how)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    _switch_core(rbot)
    out = SWITCHES[how](rbot, run_async, mk_update, BOB)
    extra = (_query_texts(out) if how != "typed"
             else kit.texts_of(out.message.reply_text, out.reply.edit_text))
    assert _refused(extra + _all_text(rbot), scopes)


@pytest.mark.parametrize("how", list(SWITCHES))
def test_mode_switch_inside_root_goes_ahead_for_a_user(rbot, run_async, mk_update, where,
                                                       monkeypatch, how):
    _mode_session(rbot, where("inside"), how)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    core = _switch_core(rbot)
    SWITCHES[how](rbot, run_async, mk_update, BOB)
    core.assert_awaited()


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
@pytest.mark.parametrize("how", list(SWITCHES))
def test_mode_switch_outside_root_allowed_for_managers(rbot, run_async, mk_update, where,
                                                       monkeypatch, how, who):
    _mode_session(rbot, where("outside"), how)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    core = _switch_core(rbot)
    SWITCHES[how](rbot, run_async, mk_update, who)
    core.assert_awaited()


# ---- prompts to such a running session stay allowed -----------------------------------------

def test_a_prompt_to_an_outside_session_is_not_refused(rbot, run_async, where, monkeypatch):
    """design.md resolution 2: prompts to a running session outside the
    root stay allowed; the hook holds the turn."""
    sess = _session(rbot, where("outside"), status=Status.IDLE)
    del rbot._inject_prompt
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    typed = []

    async def _send(name, text, *a, **kw):
        typed.append(text)
        return True
    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    run_async(rbot._inject_prompt(sess, "hello", chat_id=GROUP, driver_user_id=BOB))
    assert any("hello" in t for t in typed)
