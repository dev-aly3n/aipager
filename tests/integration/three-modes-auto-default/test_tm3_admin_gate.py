"""Success criteria 18-21 in chat: Full access needs an admin on every way
in (typed, taps, /new !x, the chat default, a stale card, resume); Ask and
Auto are for everyone who may prompt, and leaving Full access needs no
admin; the tap gate's capabilities for the new and moved verbs.

A real group scope with real roles: owner (aly), admin (ada), user (bob),
read_only (ro). Nothing about who is an admin is mocked."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager import preferences
from aipager.state import SessionRegistry, Status

import _tm3
from _tm3 import (ADA, BACKTAB, BOB, GROUP, OWNER, RO, answers, quiet_restart, reply_buttons,
                  reply_texts, scoped_bot, settle_tasks, tap, typed)

S, LABEL = "claude-api__g1001", "api"
SID = "11111111-2222-3333-4444-555555555555"
ROLE = {OWNER: "owner", ADA: "admin", BOB: "user", RO: "read_only"}


@pytest.fixture
def world(mk_bot, pty, monkeypatch):
    quiet_restart(monkeypatch)

    def _mk(mode="ask", status=Status.IDLE):
        reg = SessionRegistry()
        bot = scoped_bot(mk_bot, reg)
        s = reg.get_or_create(S)
        s.label = LABEL
        s.scope_chat_id, s.scope_kind = GROUP, "group"
        s.status, s.perm_mode = status, mode
        s.claude_session_id = SID
        s.cwd = _tm3.group_root()
        return bot, s
    return _mk


def _say(bot, run_async, text, user):
    u = typed(text, chat_id=GROUP, user_id=user, chat_type="group")

    async def _go():
        if text.startswith("/mode") or text.startswith("/perms"):
            await bot._handle_mode_cmd(u, MagicMock())
        elif text.startswith("/new"):
            await bot._handle_new_cmd(u, MagicMock())
        elif text.startswith("/resume"):
            await bot._handle_resume_cmd(u, MagicMock())
        else:
            await bot._handle_message(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    return u


def _tap(bot, run_async, data, user, msg_id=50):
    u, q = tap(data, chat_id=GROUP, user_id=user, msg_id=msg_id)

    async def _go():
        await bot._handle_callback(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    return q


def _untouched(pty):
    return (pty.killed, pty.launched, pty.runs) == ([], [], [])


# ---- /mode full typed ------------------------------------------------------

@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_user_typing_mode_full_is_refused(world, run_async, pty, start):
    bot, _ = world(start)
    u = _say(bot, run_async, f"/mode {LABEL} full", BOB)
    assert "needs an admin" in "\n".join(reply_texts(u))


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_user_typing_mode_full_changes_nothing(world, run_async, pty, start):
    bot, s = world(start)
    _say(bot, run_async, f"/mode {LABEL} full", BOB)
    assert (_untouched(pty), s.perm_mode) == (True, start)


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_user_typing_mode_full_gets_no_confirm_card(world, run_async, pty, start):
    bot, _ = world(start)
    u = _say(bot, run_async, f"/mode {LABEL} full", BOB)
    assert not any(cb and cb.endswith("perms_confirm") for _t, cb in reply_buttons(u))


@pytest.mark.parametrize("who", [OWNER, ADA], ids=["owner", "admin"])
def test_admin_typing_mode_full_gets_the_confirm_card(world, run_async, pty, who):
    bot, _ = world("ask")
    u = _say(bot, run_async, f"/mode {LABEL} full", who)
    assert any(cb and cb.endswith("perms_confirm") for _t, cb in reply_buttons(u))


# ---- taps ------------------------------------------------------------------

def _card_cb(bot, run_async, verb_prefix, viewer=BOB):
    u = _say(bot, run_async, f"/mode {LABEL}", viewer)
    return next(cb for _t, cb in reply_buttons(u)
                if cb and cb.rsplit(":", 1)[-1].startswith(verb_prefix))


def test_user_tapping_modefull_changes_nothing(world, run_async, pty):
    bot, s = world("ask")
    cb = _card_cb(bot, run_async, "modefull")
    _tap(bot, run_async, cb, BOB)
    assert (_untouched(pty), s.perm_mode) == (True, "ask")


def test_user_tapping_modefull_gets_an_answer(world, run_async, pty):
    bot, s = world("ask")
    cb = _card_cb(bot, run_async, "modefull")
    q = _tap(bot, run_async, cb, BOB)
    assert any(a for a in answers(q))


def test_user_tapping_modefull_gets_no_confirm_card(world, run_async, pty):
    bot, s = world("ask")
    cb = _card_cb(bot, run_async, "modefull")
    q = _tap(bot, run_async, cb, BOB)
    edits = [c.kwargs.get("reply_markup") for c in q.edit_message_text.await_args_list]
    cbs = [b.callback_data for kb in edits if kb is not None
           for row in kb.inline_keyboard for b in row]
    assert not any(c.endswith("perms_confirm") for c in cbs)


def test_admin_tapping_modefull_reaches_the_confirm_card(world, run_async, pty):
    bot, s = world("ask")
    cb = _card_cb(bot, run_async, "modefull", viewer=ADA)
    q = _tap(bot, run_async, cb, ADA)
    texts = [str(c.args[0] if c.args else c.kwargs.get("text"))
             for c in q.edit_message_text.await_args_list]
    sent = [str(c.kwargs.get("text")) for c in bot._app.bot.send_message.await_args_list]
    assert any("--dangerously-skip-permissions" in t for t in texts + sent), (texts, sent,
                                                                            answers(q))


def test_user_tapping_rdy_full_changes_nothing(world, run_async, pty):
    bot, s = world("auto")
    _tap(bot, run_async, f"{S}:rdy_full", BOB)
    assert (_untouched(pty), s.perm_mode) == (True, "auto")


def test_user_tapping_perms_confirm_for_full_changes_nothing(world, run_async, pty):
    """A Full access confirm card an admin opened; a user taps it."""
    bot, s = world("ask")
    u = _say(bot, run_async, f"/mode {LABEL} full", ADA)
    cb = next(cb for _t, cb in reply_buttons(u) if cb and cb.endswith("perms_confirm"))
    _tap(bot, run_async, cb, BOB, msg_id=u.message.message_id + 1)
    assert (_untouched(pty), s.perm_mode) == (True, "ask")


def test_admin_tapping_perms_confirm_for_full_relaunches(world, run_async, pty):
    bot, s = world("ask")
    u = _say(bot, run_async, f"/mode {LABEL} full", ADA)
    cb = next(cb for _t, cb in reply_buttons(u) if cb and cb.endswith("perms_confirm"))
    _tap(bot, run_async, cb, ADA, msg_id=u.message.message_id + 1)
    assert pty.launched_modes == ["full"]


def test_user_tapping_stop_and_switch_into_full_changes_nothing(world, run_async, pty):
    bot, s = world("ask", Status.BUSY)
    u = _say(bot, run_async, f"/mode {LABEL} full", ADA)
    cb = next(cb for _t, cb in reply_buttons(u) if cb and cb.endswith("perms_stop_switch"))
    _tap(bot, run_async, cb, BOB, msg_id=u.message.message_id + 1)
    assert (_untouched(pty), s.perm_mode) == (True, "ask")


def _gone(s):
    s.status = Status.GONE
    import time
    s.gone_at = time.time() - 60


def test_user_tapping_resume_full_launches_nothing(world, run_async, pty):
    bot, s = world("full")
    _gone(s)
    _tap(bot, run_async, f"{S}:resume-full", BOB)
    assert pty.launched == []


def test_admin_tapping_resume_full_resumes_in_full(world, run_async, pty):
    bot, s = world("ask")
    _gone(s)
    _tap(bot, run_async, f"{S}:resume-full", ADA)
    assert pty.launched_modes == ["full"]


@pytest.mark.parametrize("verb", ["resume-auto", "resume_mode_auto"],
                         ids=["resume-auto", "legacy-resume-mode-auto"])
def test_user_may_resume_in_auto(world, run_async, pty, verb):
    bot, s = world("ask")
    _gone(s)
    _tap(bot, run_async, f"{S}:{verb}", BOB)
    assert pty.launched_modes == ["auto"]


@pytest.mark.parametrize("verb", ["resume-auto", "resume_mode_auto", "resume-ask"],
                         ids=["resume-auto", "legacy-resume-mode-auto", "resume-ask"])
def test_read_only_may_not_resume(world, run_async, pty, verb):
    bot, s = world("ask")
    _gone(s)
    _tap(bot, run_async, f"{S}:{verb}", RO)
    assert pty.launched == []


def test_read_only_may_not_switch_ask_to_auto(world, run_async, pty):
    bot, s = world("ask")
    cb = _card_cb(bot, run_async, "modeauto", viewer=ADA)
    _tap(bot, run_async, cb, RO)
    assert pty.runs == []


# ---- SC19: asking for Full access lands in Auto ----------------------------

def _new_session(bot):
    return next((s for s in bot.registry.all_sessions().values() if s.label == "x1"), None)


def test_user_new_bang_launches_in_auto(world, run_async, pty):
    bot, _ = world()
    _say(bot, run_async, "/new !x1", BOB)
    assert pty.launched_modes == ["auto"]


def test_user_new_bang_is_told_full_access_needs_an_admin(world, run_async, pty):
    bot, _ = world()
    u = _say(bot, run_async, "/new !x1", BOB)
    said = "\n".join(reply_texts(u)) + "\n".join(
        str(c.kwargs.get("text")) for c in bot._app.bot.edit_message_text.await_args_list)
    assert "needs an admin" in said, said


def test_admin_new_bang_launches_in_full(world, run_async, pty):
    bot, _ = world()
    _say(bot, run_async, "/new !x1", ADA)
    assert pty.launched_modes == ["full"]


@pytest.mark.parametrize("who,expected", [(BOB, "auto"), (ADA, "full")],
                         ids=["user", "admin"])
def test_chat_default_full_access(world, run_async, pty, who, expected):
    bot, _ = world()
    preferences.set_new_session_default(GROUP, "mode", "full")
    _say(bot, run_async, "/new x1", who)
    assert pty.launched_modes == [expected]


def test_user_tapping_a_stale_full_choice_on_the_name_card_gets_auto(world, run_async, pty):
    """The Name card's Full access choice is offered to admins only; a
    user tapping one (stale or forged) still starts in Auto."""
    bot, _ = world()
    u = _say(bot, run_async, "/new", BOB)
    _tap(bot, run_async, "_:nw:mode:full", BOB, msg_id=u.message.message_id + 1)
    _say(bot, run_async, "x1", BOB)
    assert pty.launched_modes == ["auto"]


def test_name_card_offers_full_access_to_an_admin_only(world, run_async, pty):
    bot, _ = world()
    u_user = _say(bot, run_async, "/new", BOB)
    user_cbs = [cb for _t, cb in reply_buttons(u_user)]
    bot2, _ = world()
    u_admin = _say(bot2, run_async, "/new", ADA)
    admin_cbs = [cb for _t, cb in reply_buttons(u_admin)]
    assert ("_:nw:mode:full" in user_cbs, "_:nw:mode:full" in admin_cbs) == (False, True)


def test_user_resuming_a_full_access_session_gets_auto(world, run_async, pty):
    bot, s = world("full")
    _gone(s)
    _say(bot, run_async, f"/resume {LABEL}", BOB)
    assert pty.launched_modes == ["auto"]


def test_user_resuming_a_full_access_session_is_told_why(world, run_async, pty):
    bot, s = world("full")
    _gone(s)
    u = _say(bot, run_async, f"/resume {LABEL}", BOB)
    assert "needs an admin" in "\n".join(reply_texts(u)), reply_texts(u)


def test_admin_resuming_a_full_access_session_keeps_full(world, run_async, pty):
    bot, s = world("full")
    _gone(s)
    _say(bot, run_async, f"/resume {LABEL}", ADA)
    assert pty.launched_modes == ["full"]


@pytest.mark.parametrize("saved", ["ask", "auto"], ids=["ask", "auto"])
def test_user_resume_keeps_a_saved_ask_or_auto(world, run_async, pty, saved):
    bot, s = world(saved)
    _gone(s)
    _say(bot, run_async, f"/resume {LABEL}", BOB)
    assert pty.launched_modes == [saved]


def _picker(bot, run_async, user):
    q = _tap(bot, run_async, f"{S}:resume", user)
    kbs = [c.kwargs.get("reply_markup") for c in q.edit_message_text.await_args_list]
    return [(b.text, b.callback_data) for kb in kbs if kb is not None
            for row in kb.inline_keyboard for b in row]


def test_user_resume_picker_has_no_full_access_row(world, run_async, pty):
    bot, s = world("full")
    _gone(s)
    rows = _picker(bot, run_async, BOB)
    assert rows and not any(cb.endswith("resume-full") for _t, cb in rows), rows


def test_user_resume_picker_marks_auto_as_default_for_a_full_session(world, run_async, pty):
    bot, s = world("full")
    _gone(s)
    marked = [cb for t, cb in _picker(bot, run_async, BOB) if "(default)" in t]
    assert len(marked) == 1 and marked[0].endswith("resume-auto"), marked


def test_admin_resume_picker_offers_full_access(world, run_async, pty):
    bot, s = world("full")
    _gone(s)
    rows = _picker(bot, run_async, ADA)
    assert any(cb.endswith("resume-full") for _t, cb in rows), rows


def test_admin_resume_picker_marks_full_as_default_for_a_full_session(world, run_async, pty):
    bot, s = world("full")
    _gone(s)
    marked = [cb for t, cb in _picker(bot, run_async, ADA) if "(default)" in t]
    assert len(marked) == 1 and marked[0].endswith("resume-full"), marked


# ---- SC20: Ask and Auto for everyone who may prompt -----------------------

def test_user_may_switch_ask_to_auto(world, run_async, pty):
    bot, s = world("ask")
    _say(bot, run_async, f"/mode {LABEL} auto", BOB)
    assert pty.presses == [BACKTAB] * 3


def test_user_may_switch_auto_to_ask(world, run_async, pty):
    bot, s = world("auto")
    _say(bot, run_async, f"/mode {LABEL} ask", BOB)
    assert pty.presses == [BACKTAB]


@pytest.mark.parametrize("target", ["ask", "auto"], ids=["to-ask", "to-auto"])
def test_user_may_leave_full_access(world, run_async, pty, target):
    bot, s = world("full")
    _say(bot, run_async, f"/mode {LABEL} {target}", BOB)
    assert pty.launched_modes == [target]


@pytest.mark.parametrize("target", ["ask", "auto"], ids=["to-ask", "to-auto"])
def test_user_may_confirm_stop_and_switch_out_of_full(world, run_async, pty, target):
    bot, s = world("full", Status.BUSY)
    u = _say(bot, run_async, f"/mode {LABEL} {target}", BOB)
    cb = next(cb for _t, cb in reply_buttons(u) if cb and cb.endswith("perms_stop_switch"))
    _tap(bot, run_async, cb, BOB, msg_id=u.message.message_id + 1)
    assert pty.launched_modes == [target]


def test_user_tapping_modeauto_presses(world, run_async, pty):
    bot, s = world("ask")
    cb = _card_cb(bot, run_async, "modeauto")
    _tap(bot, run_async, cb, BOB)
    assert pty.presses == [BACKTAB] * 3


def test_user_may_create_in_ask_or_auto_via_name_card(world, run_async, pty):
    bot, _ = world()
    u = _say(bot, run_async, "/new", BOB)
    _tap(bot, run_async, "_:nw:mode:ask", BOB, msg_id=u.message.message_id + 1)
    _say(bot, run_async, "x1", BOB)
    assert pty.launched_modes == ["ask"]


# ---- re-checked at the moment of action ---------------------------------------

def _demote_ada(bot):
    """ada loses the admin role after opening a card (scopes reloaded)."""
    from aipager.scope import Member, Scope
    from _tm3 import dm_scope
    bot.scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=RO, label="ro", role="read_only"),
        Member(id=ADA, label="ada", role="user"),
    )), dm_scope()]


@pytest.mark.parametrize("status,verb", [(Status.IDLE, "perms_confirm"),
                                         (Status.BUSY, "perms_stop_switch")],
                         ids=["confirm", "stop-and-switch"])
def test_demoted_admin_cannot_confirm_their_own_full_access_card(world, run_async, pty,
                                                                 status, verb):
    bot, s = world("ask", status)
    u = _say(bot, run_async, f"/mode {LABEL} full", ADA)
    cb = next(cb for _t, cb in reply_buttons(u) if cb and cb.endswith(verb))
    _demote_ada(bot)
    _tap(bot, run_async, cb, ADA, msg_id=u.message.message_id + 1)
    assert (pty.launched, pty.killed, s.perm_mode) == ([], [], "ask")


@pytest.mark.parametrize("status,verb", [(Status.IDLE, "perms_confirm"),
                                         (Status.BUSY, "perms_stop_switch")],
                         ids=["confirm", "stop-and-switch"])
def test_admin_confirms_their_own_full_access_card(world, run_async, pty, status, verb):
    """The control for the demotion test: the same tap by a still-admin."""
    bot, s = world("ask", status)
    u = _say(bot, run_async, f"/mode {LABEL} full", ADA)
    cb = next(cb for _t, cb in reply_buttons(u) if cb and cb.endswith(verb))
    _tap(bot, run_async, cb, ADA, msg_id=u.message.message_id + 1)
    assert pty.launched_modes == ["full"]


def test_user_tapping_full_on_the_name_card_is_told_it_needs_an_admin(world, run_async, pty):
    bot, _ = world()
    u = _say(bot, run_async, "/new", BOB)
    q = _tap(bot, run_async, "_:nw:mode:full", BOB, msg_id=u.message.message_id + 1)
    assert any("needs an admin" in a for a in answers(q)), answers(q)
