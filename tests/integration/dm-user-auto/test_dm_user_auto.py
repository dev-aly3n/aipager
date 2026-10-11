"""A person may use Auto mode in their own private chat, whatever their role
(operator, 2026-10-11: "in a group scope, yes, a user cant switch the mode,
but in their dm, they should be able to do it").

One rule (``AuthMixin._may_use_auto``) behind every way into Auto: an admin
in the chat, or the member of that chat's DM scope (a role that may
prompt and approve). Pinned here, with the REAL built-in policy:

- a ``user`` in their own DM can: /mode auto, Switch to Auto + Yes, Stop
  task & switch, ``/new !x``, /new by the DM's New sessions = Auto, /resume
  of an Auto session, the Mini App's mode switch and create;
- a ``user`` (and a ``read_only`` member) in a GROUP is still refused on
  every one of those paths, and nobody gets Auto in another person's DM;
- every other admin-only action stays refused to a ``user`` in their DM
  (/update, the voice Install button, the Mini App's Updates block);
- the hook still applies the ``user`` role's rules (Auto changes nothing
  aipager enforces);
- the Mini App's Settings tab follows /settings: a person changes their
  own DM's settings, a group's need an admin, nobody changes another
  person's DM (``_may_change_chat_settings``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences
from aipager import policy_snapshot as ps
from aipager.bot import new_flow, session_parity, update_flow
from aipager.bot.transport import RESUME_AUTO_NEEDS_ADMIN
from aipager.dtach import enforce, inject
from aipager.miniapp.server import SCOPE_HEADER, MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP = -1001
ALY, BOB, RO, CARA = 1, 2, 3, 5      # owner; user; read_only; user (no DM)
ALY_DM, BOB_DM, RO_DM = ALY, BOB, RO
G_S = "claude-api__g1001"
BOB_S = "claude-mine__d2"
ALY_S = "claude-ops__d1"

NEEDS_ADMIN = "Auto mode needs an admin."
APP_NEEDS_ADMIN = "Auto mode requires admin."     # the Mini App's wording
REFUSED = "Your role can't do that here."
ASK_NOTE = f"in Ask: {RESUME_AUTO_NEEDS_ADMIN}"
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _policy():
    return load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))


def _scopes():
    return [
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=ALY, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=RO, label="ro", role="read_only"),
            Member(id=CARA, label="cara", role="user"),
        )),
        Scope(chat_id=ALY_DM, kind="dm", label="aly DM",
              members=(Member(id=ALY, label="aly", role="owner"),)),
        Scope(chat_id=BOB_DM, kind="dm", label="bob DM",
              members=(Member(id=BOB, label="bob", role="user"),)),
        Scope(chat_id=RO_DM, kind="dm", label="ro DM",
              members=(Member(id=RO, label="ro", role="read_only"),)),
    ]


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=_scopes())
    b.policy = _policy()
    b._app.bot.id = 999
    b._app.bot.username = "aipager_test_bot"
    b._app.bot.delete_message = AsyncMock()
    b._app.bot.edit_message_text = AsyncMock()
    for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                 "_update_bot_commands", "_edit_busy_raw",
                 "_send_busy_and_animate"):
        setattr(b, name, AsyncMock())
    b._start_animation = MagicMock()
    b._stop_animation = MagicMock()
    b._install_voice_extra = AsyncMock()
    b._restart_daemon = AsyncMock()
    return b


@pytest.fixture
def launched(monkeypatch):
    seen: list = []

    async def _launch(short, **kw):
        seen.append((short, kw))
        return True, ""

    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=[]))
    return seen


def _cmd(text, *, user_id, chat_id):
    u = MagicMock()
    u.effective_chat = MagicMock(id=chat_id,
                                 type="private" if chat_id > 0 else "supergroup")
    u.effective_user = MagicMock(id=user_id, username=f"u{user_id}",
                                 first_name="U", last_name="")
    m = MagicMock()
    m.text = text
    m.caption = None
    m.message_id = 400
    m.reply_to_message = None
    m.quote = None
    m.external_reply = None
    m.forward_origin = None
    m.via_bot = None
    m.media_group_id = None
    m.chat = u.effective_chat
    m.from_user = u.effective_user
    m.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    u.message = m
    u.effective_message = m
    u.callback_query = None
    return u


def _replies(u):
    return [c.args[0] if c.args else c.kwargs.get("text", "")
            for c in u.message.reply_text.await_args_list]


def _tap(chat_id, user_id, data, *, msg_id=50):
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=chat_id)
    q.message.chat_id = chat_id
    q.message.message_id = msg_id
    q.message.text = "card"
    q.message.edit_text = AsyncMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    u.message = None
    return u, q


def _answers(q):
    return [c.args[0] for c in q.answer.await_args_list if c.args]


def _edited(q):
    return [c.args[0] if c.args else c.kwargs.get("text", "")
            for c in q.edit_message_text.await_args_list]


def _session(bot, name, chat, *, label=None, status=Status.IDLE, auto=False):
    s = bot.registry.get_or_create(name)
    s.label = label or name.removeprefix("claude-").split("__")[0]
    s.scope_chat_id = chat
    s.scope_kind = "group" if chat < 0 else "dm"
    s.status = status
    s.skip_perms = auto
    return s


def _gone_auto(bot, name, chat):
    s = _session(bot, name, chat, status=Status.GONE, auto=True)
    s.claude_session_id, s.cwd = "sid-1", "/srv/work"
    s.last_assistant_preview = "where we left off"
    return s


# Where each person acts: (user, chat, session name in that chat).
DM_USER = (BOB, BOB_DM, BOB_S)
GROUP_REFUSED = [(BOB, GROUP, G_S), (RO, GROUP, G_S)]
G_IDS = ["group-user", "group-read_only"]


# ===========================================================================
# The rule
# ===========================================================================

@pytest.mark.parametrize("uid,chat,want", [
    (BOB, BOB_DM, True),        # a user, their own DM
    (ALY, ALY_DM, True),        # the owner (admin anyway)
    (ALY, GROUP, True),         # an admin in the group
    (BOB, GROUP, False),        # a user in a group
    (RO, GROUP, False),         # read_only in a group
    (RO, RO_DM, False),         # read_only sends no prompts, even at home
    (BOB, ALY_DM, False),       # someone else's DM
    (ALY, BOB_DM, False),       # the owner is no one in bob's DM
    (CARA, BOB_DM, False),      # not that DM's member
    (BOB, None, False),         # an unknown chat
    (None, BOB_DM, False),      # an unknown person
    (BOB, 4242, False),         # a private chat with no scope
], ids=["user-own-dm", "owner-own-dm", "owner-group", "user-group",
        "ro-group", "ro-own-dm", "user-other-dm", "owner-other-dm",
        "stranger-dm", "no-chat", "no-user", "unscoped-dm"])
def test_may_use_auto(bot, uid, chat, want):
    assert bot._may_use_auto(uid, chat) is want


def test_the_admin_rule_itself_is_unchanged(bot):
    """Only Auto opens up: a user in their DM is still not an admin."""
    assert bot._is_admin_user(BOB, BOB_DM) is False
    assert bot._is_update_admin(BOB, BOB_DM) is False


def test_a_dm_scope_needs_the_dm_kind(mk_bot):
    """A one-member GROUP is not a private chat."""
    b = mk_bot(scopes=[Scope(chat_id=77, kind="group", label="solo",
                             members=(Member(id=BOB, label="bob", role="user"),))])
    b.policy = _policy()
    assert b._may_use_auto(BOB, 77) is False


@pytest.mark.parametrize("prompt,approve,want", [
    ("true", "false", False),    # the sample policy's ``reviewer``
    ("false", "true", False),    # answers prompts, sends none
    ("true", "true", True),      # both: like ``user``
], ids=["prompt-only", "approve-only", "both"])
def test_a_custom_role_needs_prompt_and_approve_for_auto_in_its_dm(
        mk_bot, tmp_path, prompt, approve, want):
    """Auto answers every Claude Code prompt: only a role that may both
    send prompts and answer them gets Auto at home."""
    (tmp_path / "policy.yaml").write_text(
        f"roles:\n  custom:\n    can_prompt: {prompt}\n    can_approve: {approve}\n")
    b = mk_bot(scopes=[Scope(chat_id=9, kind="dm", label="c DM",
                             members=(Member(id=9, label="c", role="custom"),))])
    b.policy = load_policy(tmp_path / "policy.yaml", tmp_path / "policy.d")
    assert b._may_use_auto(9, 9) is want


def test_a_dm_scope_on_a_group_id_is_not_a_private_chat(mk_bot):
    """A hand-edited ``kind: dm`` on a group's (negative) id counts for
    neither Auto nor the settings rule."""
    b = mk_bot(scopes=[Scope(chat_id=-88, kind="dm", label="odd",
                             members=(Member(id=BOB, label="bob", role="user"),))])
    b.policy = _policy()
    assert b._may_use_auto(BOB, -88) is False
    assert b._may_change_chat_settings(BOB, -88) is False


def test_personal_and_legacy_modes_keep_the_admin_rule(mk_bot):
    from aipager.team import Role, Team, User as TeamUser
    personal = mk_bot(scopes=None)
    assert personal._may_use_auto(12345, 12345) is True
    legacy = mk_bot(scopes=None)
    legacy.team = Team(group_id=-5, users={
        7: TeamUser(id=7, label="u", role=Role.DEVELOPER),
        8: TeamUser(id=8, label="a", role=Role.ADMIN)})
    assert legacy._may_use_auto(7, 7) is False
    assert legacy._may_use_auto(8, 8) is True


# ===========================================================================
# /mode auto (typed)
# ===========================================================================

def test_mode_auto_typed_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat)
    u = _cmd("/mode mine auto", user_id=uid, chat_id=chat)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    replies = _replies(u)
    assert replies and NEEDS_ADMIN not in replies
    assert "Auto mode?" in replies[0]           # the confirm step
    assert bot._perms_pending[name]["target_skip_perms"] is True
    assert sess.skip_perms is False             # nothing yet without Yes


@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_mode_auto_typed_in_a_group_is_refused(bot, run_async, uid, chat, name):
    _session(bot, name, chat)
    u = _cmd("/mode api auto", user_id=uid, chat_id=chat)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    if uid == BOB:
        assert _replies(u) == [NEEDS_ADMIN]
    else:
        # read_only is stopped before the mode gate (it drives nothing).
        assert len(_replies(u)) == 1 and "read_only" in _replies(u)[0]
    assert name not in bot._perms_pending


def test_perms_alias_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    _session(bot, name, chat)
    u = _cmd("/perms mine auto", user_id=uid, chat_id=chat)
    run_async(bot._handle_perms_cmd(u, MagicMock()))
    assert NEEDS_ADMIN not in _replies(u)
    assert bot._perms_pending[name]["target_skip_perms"] is True


# ===========================================================================
# The mode card: Switch to Auto, then Yes / Stop task & switch
# ===========================================================================

def _switch_tap(bot, run_async, uid, chat, sess):
    data = session_parity.session_cb(bot, chat, sess, f"modeauto{sess.turn_key}")
    u, q = _tap(chat, uid, data, msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    return q


def test_switch_to_auto_then_yes_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat)
    bot._do_perms_switch_via_fn = AsyncMock()
    q = _switch_tap(bot, run_async, uid, chat, sess)
    assert NEEDS_ADMIN not in _answers(q)
    assert bot._perms_pending[name]["target_skip_perms"] is True
    u, q2 = _tap(chat, uid, f"{name}:perms_confirm",
                 msg_id=bot._perms_pending[name]["msg_id"] or 90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert REFUSED not in _answers(q2)
    bot._do_perms_switch_via_fn.assert_awaited_once()
    assert bot._do_perms_switch_via_fn.await_args.args[:2] == (sess, True)


@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_switch_to_auto_tap_in_a_group_is_refused(bot, run_async, uid, chat, name):
    sess = _session(bot, name, chat)
    bot._do_perms_switch_via_fn = AsyncMock()
    q = _switch_tap(bot, run_async, uid, chat, sess)
    # A user reaches the handler, which refuses; read_only is stopped at
    # the tap gate (no prompting). Either way: no confirm, no switch.
    assert _answers(q) == ([NEEDS_ADMIN] if uid == BOB else [REFUSED])
    assert name not in bot._perms_pending
    bot._do_perms_switch_via_fn.assert_not_called()


@pytest.mark.parametrize("verb", ["perms_confirm", "perms_stop_switch"])
def test_yes_and_stop_and_switch_in_own_dm(bot, run_async, verb):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat,
                    status=Status.BUSY if verb == "perms_stop_switch" else Status.IDLE)
    bot._perms_pending[name] = {"target_skip_perms": True, "msg_id": 90,
                                "label": sess.label, "turn": sess.turn_key}
    bot._do_perms_switch_via_fn = AsyncMock()
    u, q = _tap(chat, uid, f"{name}:{verb}", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert REFUSED not in _answers(q)
    bot._do_perms_switch_via_fn.assert_awaited_once()
    assert bot._do_perms_switch_via_fn.await_args.args[1] is True


@pytest.mark.parametrize("verb", ["perms_confirm", "perms_stop_switch"])
@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_yes_and_stop_and_switch_in_a_group_are_refused(
        bot, run_async, uid, chat, name, verb):
    sess = _session(bot, name, chat,
                    status=Status.BUSY if verb == "perms_stop_switch" else Status.IDLE)
    record = {"target_skip_perms": True, "msg_id": 90, "label": sess.label,
              "turn": sess.turn_key}
    bot._perms_pending[name] = record
    bot._do_perms_switch_via_fn = AsyncMock()
    u, q = _tap(chat, uid, f"{name}:{verb}", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    bot._do_perms_switch_via_fn.assert_not_called()
    assert bot._perms_pending[name] is record


def test_another_persons_dm_card_is_not_theirs(bot, run_async):
    """bob taps a Yes that names aly's DM session, from aly's DM: he is
    no member there, so nothing switches."""
    sess = _session(bot, ALY_S, ALY_DM)
    bot._perms_pending[ALY_S] = {"target_skip_perms": True, "msg_id": 90,
                                 "label": sess.label, "turn": sess.turn_key}
    bot._do_perms_switch_via_fn = AsyncMock()
    for uid in (BOB, CARA):
        u, q = _tap(ALY_DM, uid, f"{ALY_S}:perms_confirm", msg_id=90)
        run_async(bot._handle_callback(u, MagicMock()))
    bot._do_perms_switch_via_fn.assert_not_called()
    assert sess.skip_perms is False


def test_own_dm_auto_does_not_reach_a_group_session(bot, run_async):
    """bob's Yes for the GROUP's session, tapped in his own DM: the button
    belongs to another chat."""
    sess = _session(bot, G_S, GROUP)
    bot._perms_pending[G_S] = {"target_skip_perms": True, "msg_id": 90,
                               "label": sess.label, "turn": sess.turn_key}
    bot._do_perms_switch_via_fn = AsyncMock()
    u, q = _tap(BOB_DM, BOB, f"{G_S}:perms_confirm", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    bot._do_perms_switch_via_fn.assert_not_called()


# ===========================================================================
# /new
# ===========================================================================

def _new(bot, run_async, text, uid, chat):
    u = _cmd(text, user_id=uid, chat_id=chat)
    run_async(bot._handle_new_cmd(u, MagicMock()))
    return u


def test_new_bang_in_own_dm_starts_in_auto(bot, run_async, launched):
    uid, chat, _ = DM_USER
    _new(bot, run_async, "/new !x7", uid, chat)
    sess = bot.registry.find_by_label("x7", chat)
    assert sess is not None and sess.skip_perms is True
    assert launched and launched[-1][1]["skip_perms"] is True


@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_new_bang_in_a_group_gets_no_auto(bot, run_async, launched, uid, chat, name):
    _new(bot, run_async, "/new !x7", uid, chat)
    sess = bot.registry.find_by_label("x7", chat)
    assert sess is None or sess.skip_perms is False
    assert all(kw["skip_perms"] is False for _s, kw in launched)


def test_new_bang_in_a_group_says_auto_needs_an_admin(bot, run_async, launched):
    u = _new(bot, run_async, "/new !x7", BOB, GROUP)
    sess = bot.registry.find_by_label("x7", GROUP)
    assert sess is not None and sess.skip_perms is False
    b = bot._app.bot
    shown = "\n".join(_replies(u) + [
        str(c.kwargs.get("text", c.args[0] if c.args else ""))
        for m in (b.edit_message_text, b.send_message, u.message.reply_text.return_value.edit_text)
        for c in m.await_args_list])
    assert str(new_flow.NOTE_AUTO_NEEDS_ADMIN) in shown


def test_new_without_a_setting_stays_ask_in_own_dm(bot, run_async, launched):
    uid, chat, _ = DM_USER
    st = new_flow.resolve_new_session_settings(bot, chat, uid)
    assert st["can_auto"] is True and st["skip_perms"] is False
    _new(bot, run_async, "/new x8", uid, chat)
    assert bot.registry.find_by_label("x8", chat).skip_perms is False


def _ns_tap(bot, run_async, uid, chat, token):
    u, q = _tap(chat, uid, f"_:set:ns:mode:{token}", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    return q


def test_new_follows_the_dms_new_sessions_auto(bot, run_async, launched):
    uid, chat, _ = DM_USER
    q = _ns_tap(bot, run_async, uid, chat, "auto")
    assert preferences.get_new_session_defaults(chat).mode == "auto"
    # The page shows what /new now gives them: Auto, ticked.
    edits = (q.edit_message_text.await_args_list
             + bot._app.bot.edit_message_text.await_args_list)
    kb = edits[-1].kwargs["reply_markup"]
    first_row = [b.text for b in kb.inline_keyboard[0]]
    assert first_row[0].endswith("✅") and not first_row[1].endswith("✅")
    page = edits[-1].kwargs.get("text") or edits[-1].args[0]
    assert "Auto is for admins" not in page and "Auto mode" in page
    st = new_flow.resolve_new_session_settings(bot, chat, uid)
    assert st["skip_perms"] is True
    _new(bot, run_async, "/new x9", uid, chat)
    assert bot.registry.find_by_label("x9", chat).skip_perms is True
    # And back: Ask is stored, /new gives Ask.
    _ns_tap(bot, run_async, uid, chat, "ask")
    assert new_flow.resolve_new_session_settings(bot, chat, uid)["skip_perms"] is False


def test_the_dm_page_ticks_ask_with_no_setting(bot):
    text, kb = new_flow.render_new_session_defaults(bot, BOB_DM, viewer=BOB)
    first_row = [b.text for b in kb.inline_keyboard[0]]
    assert first_row[1].endswith("✅") and not first_row[0].endswith("✅")
    assert "Auto is for admins" not in text


def test_an_admin_still_stores_auto_as_the_default(bot, run_async):
    _ns_tap(bot, run_async, ALY, ALY_DM, "auto")
    assert preferences.get_new_session_defaults(ALY_DM).mode == ""
    assert new_flow.resolve_new_session_settings(bot, ALY_DM, ALY)["skip_perms"] is True


@pytest.mark.parametrize("uid", [BOB, RO], ids=["user", "read_only"])
def test_a_groups_auto_default_is_not_theirs(bot, uid):
    preferences.set_new_session_default(GROUP, "mode", "auto")
    st = new_flow.resolve_new_session_settings(bot, GROUP, uid)
    assert st["skip_perms"] is False and st["can_auto"] is False


def test_another_dms_auto_default_does_not_apply_to_a_visitor(bot):
    preferences.set_new_session_default(BOB_DM, "mode", "auto")
    assert new_flow.resolve_new_session_settings(bot, BOB_DM, CARA)["skip_perms"] is False


def test_name_card_mode_auto_tap_in_own_dm(bot, run_async):
    uid, chat, _ = DM_USER
    _new(bot, run_async, "/new", uid, chat)
    pending = new_flow._pending_store(bot)[(chat, uid)]
    assert pending["can_auto"] is True and pending["skip_perms"] is False
    u, q = _tap(chat, uid, "_:nw:mode:auto", msg_id=pending.get("msg_id") or 901)
    run_async(bot._handle_callback(u, MagicMock()))
    assert NEEDS_ADMIN not in _answers(q)
    assert pending["skip_perms"] is True


def test_name_card_mode_auto_tap_in_a_group_is_refused(bot, run_async):
    _new(bot, run_async, "/new", BOB, GROUP)
    pending = new_flow._pending_store(bot)[(GROUP, BOB)]
    u, q = _tap(GROUP, BOB, "_:nw:mode:auto", msg_id=pending.get("msg_id") or 901)
    run_async(bot._handle_callback(u, MagicMock()))
    assert NEEDS_ADMIN in _answers(q)
    assert pending["skip_perms"] is False


def _ready_tap(bot, run_async, uid, chat, name):
    sess = _session(bot, name, chat)
    sess.tap_is_for_this_turn = lambda *a, **k: True
    bot._perms_switch_core = AsyncMock(return_value=MagicMock(ok=True, err=""))
    data = session_parity.session_cb(bot, chat, sess, "rdy_auto")
    u, q = _tap(chat, uid, data, msg_id=95)
    run_async(bot._handle_callback(u, MagicMock()))
    return sess, q


def test_ready_card_switch_to_auto_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess, q = _ready_tap(bot, run_async, uid, chat, name)
    assert NEEDS_ADMIN not in _answers(q)
    bot._perms_switch_core.assert_awaited_once_with(sess, True)


def test_ready_card_switch_to_auto_in_a_group_is_refused(bot, run_async):
    _sess, q = _ready_tap(bot, run_async, BOB, GROUP, G_S)
    assert NEEDS_ADMIN in _answers(q)
    bot._perms_switch_core.assert_not_called()


# ===========================================================================
# /resume of a session that was in Auto
# ===========================================================================

def test_typed_resume_in_own_dm_comes_back_in_auto(bot, run_async, launched):
    uid, chat, name = DM_USER
    sess = _gone_auto(bot, name, chat)
    u = _cmd("/resume mine", user_id=uid, chat_id=chat)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    assert launched[0][1]["skip_perms"] is True and sess.skip_perms is True
    assert ASK_NOTE not in "\n".join(_replies(u))


@pytest.mark.parametrize("uid", [BOB], ids=["group-user"])
def test_typed_resume_in_a_group_comes_back_in_ask(bot, run_async, launched, uid):
    sess = _gone_auto(bot, G_S, GROUP)
    u = _cmd("/resume api", user_id=uid, chat_id=GROUP)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    assert launched[0][1]["skip_perms"] is False and sess.skip_perms is False
    assert ASK_NOTE in "\n".join(_replies(u))


def test_picker_resume_auto_in_own_dm(bot, run_async, launched):
    uid, chat, name = DM_USER
    sess = _gone_auto(bot, name, chat)
    data = session_parity.session_cb(bot, chat, sess, "resume")
    u, q = _tap(chat, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    labels = [b.text for row in
              q.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
              for b in row]
    assert "🤖 Auto mode (default)" in labels
    sess.skip_perms = False
    data = session_parity.session_cb(bot, chat, sess, "resume-auto")
    u, q = _tap(chat, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert REFUSED not in _answers(q)
    assert launched[0][1]["skip_perms"] is True


@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_picker_resume_auto_in_a_group_is_refused(bot, run_async, launched,
                                                  uid, chat, name):
    sess = _gone_auto(bot, name, chat)
    sess.skip_perms = False
    data = session_parity.session_cb(bot, chat, sess, "resume-auto")
    u, q = _tap(chat, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    assert launched == []


def test_legacy_resume_mode_auto_in_own_dm(bot, run_async, launched):
    uid, chat, name = DM_USER
    _gone_auto(bot, name, chat)
    u, q = _tap(chat, uid, f"{name}:resume_mode_auto", msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched and launched[0][1]["skip_perms"] is True


def test_new_conflict_card_resume_in_own_dm(bot, run_async, launched):
    uid, chat, name = DM_USER
    sess = _gone_auto(bot, name, chat)
    bot._new_conflict_pending[name] = {"user_id": uid, "prompt": "",
                                       "skip_perms": False, "msg_id": 0}
    u, q = _tap(chat, uid, f"{name}:new_resume", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched[0][1]["skip_perms"] is True and sess.skip_perms is True
    assert not any(ASK_NOTE in t for t in _edited(q))


def test_new_conflict_card_replace_keeps_auto_in_own_dm(bot, run_async, launched):
    """Replace re-checks Auto against the card's author at creation."""
    uid, chat, name = DM_USER
    _gone_auto(bot, name, chat)
    bot._new_conflict_pending[name] = {"user_id": uid, "prompt": "",
                                       "skip_perms": True, "msg_id": 0}
    u, q = _tap(chat, uid, f"{name}:new_replace", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched and launched[-1][1]["skip_perms"] is True


def test_new_conflict_card_replace_in_a_group_drops_auto(bot, run_async, launched):
    _gone_auto(bot, G_S, GROUP)
    bot._new_conflict_pending[G_S] = {"user_id": BOB, "prompt": "",
                                      "skip_perms": True, "msg_id": 0}
    u, q = _tap(GROUP, BOB, f"{G_S}:new_replace", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched and launched[-1][1]["skip_perms"] is False


# ===========================================================================
# The Mini App: mode switch, create, resume, session actions
# ===========================================================================

def _srv(bot, sess, chat, uid):
    srv = MiniAppServer(bot, bot.registry, port=8765)
    srv._resolve_own_scope_session = AsyncMock(return_value=(sess, chat, uid))
    srv._allow_write = lambda _uid: True
    return srv


def test_miniapp_mode_switch_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat)
    bot._perms_switch_core = AsyncMock(return_value=MagicMock(ok=True, reason="ok"))
    srv = _srv(bot, sess, chat, uid)
    srv._restart_outcome_response = MagicMock(return_value=MagicMock(status=200))
    resp = run_async(srv._handle_session_perms(MagicMock()))
    assert resp.status == 200
    bot._perms_switch_core.assert_awaited_once_with(sess, True)


@pytest.mark.parametrize("uid,chat,name", [(BOB, GROUP, G_S)], ids=["group-user"])
def test_miniapp_mode_switch_in_a_group_is_refused(bot, run_async, uid, chat, name):
    sess = _session(bot, name, chat)
    bot._perms_switch_core = AsyncMock()
    resp = run_async(_srv(bot, sess, chat, uid)._handle_session_perms(MagicMock()))
    assert resp.status == 403
    assert json.loads(resp.body)["detail"] == APP_NEEDS_ADMIN
    bot._perms_switch_core.assert_not_called()


def test_miniapp_resume_in_own_dm_comes_back_in_auto(bot, run_async, launched):
    uid, chat, name = DM_USER
    sess = _gone_auto(bot, name, chat)
    resp = run_async(_srv(bot, sess, chat, uid)._handle_session_resume(MagicMock()))
    body = json.loads(resp.body)
    assert body["status"] == "resumed" and "detail" not in body
    assert launched[0][1]["skip_perms"] is True


def test_miniapp_resume_in_a_group_comes_back_in_ask(bot, run_async, launched):
    sess = _gone_auto(bot, G_S, GROUP)
    resp = run_async(_srv(bot, sess, GROUP, BOB)._handle_session_resume(MagicMock()))
    body = json.loads(resp.body)
    assert body["mode"] == "ask" and launched[0][1]["skip_perms"] is False


def test_miniapp_session_detail_offers_the_switch_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat)
    body = json.loads(run_async(
        _srv(bot, sess, chat, uid)._handle_session_detail(MagicMock())).body)
    assert body["actions"]["perms"]["available"] is True
    gsess = _session(bot, G_S, GROUP)
    body = json.loads(run_async(
        _srv(bot, gsess, GROUP, BOB)._handle_session_detail(MagicMock())).body)
    assert body["actions"]["perms"]["available"] is False


# ---- over HTTP, with the real authentication ------------------------------

@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Test"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _hdr(user_id, chat):
    return {"X-Telegram-Init-Data": _init_data(user_id), SCOPE_HEADER: str(chat)}


def _http(bot, run_async, fn):
    srv = MiniAppServer(bot, bot.registry, port=8765)

    async def _go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            return await fn(client)
        finally:
            await client.close()
    return run_async(_go())


@pytest.mark.parametrize("uid,chat,want", [
    (BOB, BOB_DM, True), (BOB, GROUP, False), (RO, GROUP, False),
    (ALY, GROUP, True)], ids=["user-own-dm", "user-group", "ro-group", "owner-group"])
def test_miniapp_create_form_says_who_may_use_auto(bot, run_async, uid, chat, want):
    async def _fn(client):
        resp = await client.get("/api/session-options", headers=_hdr(uid, chat))
        assert resp.status == 200, await resp.text()
        return await resp.json()
    assert _http(bot, run_async, _fn)["can_use_auto"] is want


@pytest.mark.parametrize("uid,chat,want", [
    (BOB, BOB_DM, 201), (BOB, GROUP, 403)], ids=["user-own-dm", "user-group"])
def test_miniapp_create_in_auto(bot, run_async, launched, uid, chat, want):
    async def _fn(client):
        resp = await client.post("/api/sessions", headers=_hdr(uid, chat),
                                 json={"name": "web1", "skip_perms": True})
        return resp.status, await resp.json()
    status, body = _http(bot, run_async, _fn)
    if want == 403:
        assert status == 403 and body["detail"] == APP_NEEDS_ADMIN
        assert launched == []
    else:
        assert status in (200, 201), body
        assert launched and launched[-1][1]["skip_perms"] is True


def test_miniapp_other_persons_dm_is_out_of_reach(bot, run_async):
    _session(bot, ALY_S, ALY_DM)

    async def _fn(client):
        r1 = await client.post("/api/sessions/ops/perms", headers=_hdr(BOB, ALY_DM))
        r2 = await client.put("/api/preferences/layout", headers=_hdr(BOB, ALY_DM),
                              json={"value": "merged"})
        return r1.status, r2.status
    assert _http(bot, run_async, _fn) == (403, 403)
    assert bot.registry.get(ALY_S).skip_perms is False
    assert preferences.get_preferences(ALY_DM).layout != "merged"


# ===========================================================================
# Every other admin-only action stays refused in a user's own DM
# ===========================================================================

def test_update_is_still_refused_in_own_dm(bot, run_async):
    u = _cmd("/update", user_id=BOB, chat_id=BOB_DM)
    run_async(update_flow.handle_update_cmd(bot, u, MagicMock()))
    assert _replies(u) == [update_flow.DENIED_TEXT]


@pytest.mark.parametrize("verb", ["install", "restart"])
def test_voice_install_is_still_refused_in_own_dm(bot, run_async, verb):
    u, q = _tap(BOB_DM, BOB, f"__voice__:{verb}", msg_id=91)
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [update_flow.DENIED_TEXT]
    assert bot._install_voice_extra.call_count == 0
    assert bot._restart_daemon.call_count == 0


def test_miniapp_updates_block_stays_hidden_in_own_dm(bot, run_async):
    async def _fn(client):
        resp = await client.get("/api/preferences", headers=_hdr(BOB, BOB_DM))
        return await resp.json()
    assert _http(bot, run_async, _fn)["can_update"] is False


def test_admin_slash_commands_are_still_refused_in_own_dm(bot, run_async):
    """A slash command a ``user`` may not send stays refused at home."""
    u = _cmd("/permissions", user_id=BOB, chat_id=BOB_DM)
    assert run_async(bot._refuse_admin_command(u, "/permissions")) is True


# ===========================================================================
# Safety: the hook applies the user's role in Auto exactly as in Ask
# ===========================================================================

def test_the_hook_still_applies_the_user_role(tmp_path, monkeypatch, bot):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    project = tmp_path / "work" / "proj"
    project.mkdir(parents=True)
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @bob · role:user]\ndo it"}}) + "\n")
    ps.write_merged_snapshot(BOB_S, ps.resolve_snapshot(
        bot.policy.get_role("user"), None, None))
    # decide() reads no permission mode: what it denies in Ask it denies
    # in Auto. A user's Bash call is denied by the role's own rule.
    out = enforce.decide({
        "hook_event_name": "PreToolUse", "session": BOB_S,
        "session_id": "5b1d7363-aaaa-bbbb-cccc-000000000002",
        "cwd": str(project), "tool_name": "Bash",
        "tool_input": {"command": "ls"}, "transcript_path": str(tp),
        "permission_mode": "bypassPermissions",
    })
    assert out is not None and out["tool"] == "Bash"
    assert "deny_tools" in out["reason"]


# ===========================================================================
# The Mini App's Settings tab follows /settings
# ===========================================================================

@pytest.mark.parametrize("uid,chat,want", [
    (BOB, BOB_DM, True), (RO, RO_DM, True), (ALY, ALY_DM, True),
    (ALY, GROUP, True), (BOB, GROUP, False), (RO, GROUP, False),
    (BOB, ALY_DM, False), (CARA, BOB_DM, False), (BOB, None, False),
    (BOB, 4242, False)],
    ids=["user-own-dm", "ro-own-dm", "owner-own-dm", "owner-group",
         "user-group", "ro-group", "user-other-dm", "stranger-dm",
         "no-chat", "unscoped-dm"])
def test_may_change_chat_settings(bot, uid, chat, want):
    assert bot._may_change_chat_settings(uid, chat) is want


def test_settings_rule_in_personal_and_legacy_mode(mk_bot):
    from aipager.team import Role, Team, User as TeamUser
    assert mk_bot(scopes=None)._may_change_chat_settings(12345, 12345) is True
    legacy = mk_bot(scopes=None)
    legacy.team = Team(group_id=-5, users={7: TeamUser(id=7, label="u", role=Role.DEVELOPER)})
    assert legacy._may_change_chat_settings(7, 7) is False


def test_miniapp_settings_in_own_dm_are_editable(bot, run_async):
    async def _fn(client):
        get = await client.get("/api/preferences", headers=_hdr(BOB, BOB_DM))
        put = await client.put("/api/preferences/layout", headers=_hdr(BOB, BOB_DM),
                               json={"value": "merged"})
        return (await get.json())["can_edit"], put.status
    can_edit, status = _http(bot, run_async, _fn)
    assert can_edit is True and status == 200
    assert preferences.get_preferences(BOB_DM).layout == "merged"


def test_miniapp_settings_in_a_group_need_an_admin(bot, run_async):
    async def _fn(client):
        get = await client.get("/api/preferences", headers=_hdr(BOB, GROUP))
        put = await client.put("/api/preferences/layout", headers=_hdr(BOB, GROUP),
                               json={"value": "merged"})
        return (await get.json())["can_edit"], put.status
    can_edit, status = _http(bot, run_async, _fn)
    assert can_edit is False and status == 403
    assert preferences.get_preferences(GROUP).layout != "merged"


def test_telegram_settings_in_own_dm_were_already_open(bot, run_async):
    """The rule the Mini App now follows: /settings in a user's own DM."""
    u, q = _tap(BOB_DM, BOB, "_:set:layout:merged")
    run_async(bot._handle_callback(u, MagicMock()))
    assert preferences.get_preferences(BOB_DM).layout == "merged"
