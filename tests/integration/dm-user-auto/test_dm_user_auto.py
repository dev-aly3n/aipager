"""A person may use Full access mode in their own private chat, whatever
their role (operator, 2026-10-11: "in a group scope, yes, a user cant switch
the mode, but in their dm, they should be able to do it"). Written for the
bypass mode when it was called Auto (fix/dm-user-auto); since the three
modes (ship/three-modes) that mode is Full access, and Auto is Claude
Code's own safety check, open to everyone who may prompt.

One rule (``AuthMixin._may_use_full_access``) behind every way into Full
access: an admin in the chat, or the member of that chat's DM scope (a role
that may prompt and approve). Pinned here, with the REAL built-in policy:

- a ``user`` in their own DM can: /mode full, Switch to Full access + Yes,
  Stop task & switch, ``/new !x``, /new by the DM's New sessions = Full
  access, /resume of a Full access session, the Ready card, the Mini App's
  mode switch and create; and Auto, like everyone;
- a ``user`` (and a ``read_only`` member) in a GROUP is still refused Full
  access on every one of those paths (a ``user`` there may pick Auto), a
  ``read_only`` member is refused it in their own DM too, and nobody gets
  it in another person's DM;
- a member whose role lacks ``can_manage`` cannot switch a session working
  outside their root folder, in place (Shift+Tab, Ask <-> Auto) or with a
  restart (Full access);
- every other admin-only action stays refused to a ``user`` in their DM
  (/update, the voice Install button, the Mini App's Updates block);
- the hook still applies the ``user`` role's rules in every mode (the mode
  Claude Code reports changes nothing aipager enforces);
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
from aipager.bot.transport import RESUME_FULL_NEEDS_ADMIN
from aipager.dtach import enforce, inject
from aipager.miniapp.server import SCOPE_HEADER, MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP = -1001
ALICE, BOB, RO, CARA = 1, 2, 3, 5      # owner; user; read_only; user (no DM)
ALICE_DM, BOB_DM, RO_DM = ALICE, BOB, RO
G_S = "claude-api__g1001"
BOB_S = "claude-mine__d2"
ALICE_S = "claude-ops__d1"

NEEDS_ADMIN = "Full access mode needs an admin."
APP_NEEDS_ADMIN = "Full access mode requires admin."     # the Mini App's wording
APP_SWITCH_NEEDS_ADMIN = "Switching to Full access mode requires admin."
REFUSED = "Your role can't do that here."
AUTO_NOTE = f"in Auto mode: {RESUME_FULL_NEEDS_ADMIN}"
OUTSIDE = "This session works outside your folder"
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _policy():
    return load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    """``~`` is a throwaway folder: the chats' root folders live under it
    and /new may create them (the real home is never written)."""
    home = tmp_path / "home" / "op"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))


# Each chat's root folder (the root-folders rule): every session in these
# tests works inside its chat's root, as a member's sessions must.
ROOTS = {}


def _scopes():
    base = Path.home() / "work"  # the conftest's throwaway home
    ROOTS.update({GROUP: str(base / "team"), ALICE_DM: str(base / "alice"),
                  BOB_DM: str(base / "bob"), RO_DM: str(base / "ro")})
    return [
        Scope(chat_id=GROUP, kind="group", label="team", root=ROOTS[GROUP], members=(
            Member(id=ALICE, label="alice", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=RO, label="ro", role="read_only"),
            Member(id=CARA, label="cara", role="user"),
        )),
        Scope(chat_id=ALICE_DM, kind="dm", label="alice DM", root=ROOTS[ALICE_DM],
              members=(Member(id=ALICE, label="alice", role="owner"),)),
        Scope(chat_id=BOB_DM, kind="dm", label="bob DM", root=ROOTS[BOB_DM],
              members=(Member(id=BOB, label="bob", role="user"),)),
        Scope(chat_id=RO_DM, kind="dm", label="ro DM", root=ROOTS[RO_DM],
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


def _session(bot, name, chat, *, label=None, status=Status.IDLE, mode="ask", cwd=None):
    s = bot.registry.get_or_create(name)
    s.label = label or name.removeprefix("claude-").split("__")[0]
    s.scope_chat_id = chat
    s.scope_kind = "group" if chat < 0 else "dm"
    s.status = status
    s.perm_mode = mode
    if cwd is not None:
        s.cwd = cwd
    elif not s.cwd and chat in ROOTS:
        s.cwd = ROOTS[chat]
    return s


def _gone_full(bot, name, chat):
    s = _session(bot, name, chat, status=Status.GONE, mode="full")
    s.claude_session_id, s.cwd = "sid-1", ROOTS.get(chat, "/srv/work")
    s.last_assistant_preview = "where we left off"
    return s


def _outside(chat):
    """A folder outside *chat*'s root folder (next to it, in the same
    throwaway home)."""
    return str(Path(ROOTS[chat]).parent / "elsewhere")


# Where each person acts: (user, chat, session name in that chat).
DM_USER = (BOB, BOB_DM, BOB_S)
GROUP_REFUSED = [(BOB, GROUP, G_S), (RO, GROUP, G_S)]
G_IDS = ["group-user", "group-read_only"]


# ===========================================================================
# The rule
# ===========================================================================

@pytest.mark.parametrize("uid,chat,want", [
    (BOB, BOB_DM, True),        # a user, their own DM
    (ALICE, ALICE_DM, True),        # the owner (admin anyway)
    (ALICE, GROUP, True),         # an admin in the group
    (BOB, GROUP, False),        # a user in a group
    (RO, GROUP, False),         # read_only in a group
    (RO, RO_DM, False),         # read_only sends no prompts, even at home
    (BOB, ALICE_DM, False),       # someone else's DM
    (ALICE, BOB_DM, False),       # the owner is no one in bob's DM
    (CARA, BOB_DM, False),      # not that DM's member
    (BOB, None, False),         # an unknown chat
    (None, BOB_DM, False),      # an unknown person
    (BOB, 4242, False),         # a private chat with no scope
], ids=["user-own-dm", "owner-own-dm", "owner-group", "user-group",
        "ro-group", "ro-own-dm", "user-other-dm", "owner-other-dm",
        "stranger-dm", "no-chat", "no-user", "unscoped-dm"])
def test_may_use_full_access(bot, uid, chat, want):
    assert bot._may_use_full_access(uid, chat) is want


def test_the_admin_rule_itself_is_unchanged(bot):
    """Only Full access opens up: a user in their DM is still not an admin."""
    assert bot._is_admin_user(BOB, BOB_DM) is False
    assert bot._is_update_admin(BOB, BOB_DM) is False


def test_a_dm_scope_needs_the_dm_kind(mk_bot):
    """A one-member GROUP is not a private chat."""
    b = mk_bot(scopes=[Scope(chat_id=77, kind="group", label="solo",
                             members=(Member(id=BOB, label="bob", role="user"),))])
    b.policy = _policy()
    assert b._may_use_full_access(BOB, 77) is False


@pytest.mark.parametrize("prompt,approve,want", [
    ("true", "false", False),    # the sample policy's ``reviewer``
    ("false", "true", False),    # answers prompts, sends none
    ("true", "true", True),      # both: like ``user``
], ids=["prompt-only", "approve-only", "both"])
def test_a_custom_role_needs_prompt_and_approve_for_full_access_in_its_dm(
        mk_bot, tmp_path, prompt, approve, want):
    """Full access answers every Claude Code prompt: only a role that may
    both send prompts and answer them gets it at home."""
    (tmp_path / "policy.yaml").write_text(
        f"roles:\n  custom:\n    can_prompt: {prompt}\n    can_approve: {approve}\n")
    b = mk_bot(scopes=[Scope(chat_id=9, kind="dm", label="c DM",
                             members=(Member(id=9, label="c", role="custom"),))])
    b.policy = load_policy(tmp_path / "policy.yaml", tmp_path / "policy.d")
    assert b._may_use_full_access(9, 9) is want


def test_a_dm_scope_on_a_group_id_is_not_a_private_chat(mk_bot):
    """A hand-edited ``kind: dm`` on a group's (negative) id counts for
    neither Full access nor the settings rule."""
    b = mk_bot(scopes=[Scope(chat_id=-88, kind="dm", label="odd",
                             members=(Member(id=BOB, label="bob", role="user"),))])
    b.policy = _policy()
    assert b._may_use_full_access(BOB, -88) is False
    assert b._may_change_chat_settings(BOB, -88) is False


def test_personal_and_legacy_modes_keep_the_admin_rule(mk_bot):
    from aipager.team import Role, Team, User as TeamUser
    personal = mk_bot(scopes=None)
    assert personal._may_use_full_access(12345, 12345) is True
    legacy = mk_bot(scopes=None)
    legacy.team = Team(group_id=-5, users={
        7: TeamUser(id=7, label="u", role=Role.DEVELOPER),
        8: TeamUser(id=8, label="a", role=Role.ADMIN)})
    assert legacy._may_use_full_access(7, 7) is False
    assert legacy._may_use_full_access(8, 8) is True


@pytest.mark.parametrize("cap_name", ["MANAGE"])
def test_the_tap_gates_manage_is_the_full_access_rule(bot, cap_name):
    """The tap gate's MANAGE (resume-full, rdy_full, modefull, a /mode card
    switching to Full access) asks the same rule."""
    from aipager.bot import tap_gate
    cap = getattr(tap_gate, cap_name)
    member = bot._member_in_scope(bot._scope_for(BOB_DM), BOB)
    assert bot._member_can(member, cap, BOB, BOB_DM) is True
    gmember = bot._member_in_scope(bot._scope_for(GROUP), BOB)
    assert bot._member_can(gmember, cap, BOB, GROUP) is False
    romember = bot._member_in_scope(bot._scope_for(RO_DM), RO)
    assert bot._member_can(romember, cap, RO, RO_DM) is False


# ===========================================================================
# /mode full (typed)
# ===========================================================================

def test_mode_full_typed_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat)
    u = _cmd("/mode mine full", user_id=uid, chat_id=chat)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    replies = _replies(u)
    assert replies and NEEDS_ADMIN not in replies
    assert "Full access mode?" in replies[0]    # the confirm step
    assert bot._perms_pending[name]["target_mode"] == "full"
    assert sess.perm_mode == "ask"              # nothing yet without Yes


@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_mode_full_typed_in_a_group_is_refused(bot, run_async, uid, chat, name):
    _session(bot, name, chat)
    u = _cmd("/mode api full", user_id=uid, chat_id=chat)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    if uid == BOB:
        assert _replies(u) == [NEEDS_ADMIN]
    else:
        # read_only is stopped before the mode gate (it drives nothing).
        assert len(_replies(u)) == 1 and "read_only" in _replies(u)[0]
    assert name not in bot._perms_pending


def test_mode_full_typed_by_read_only_in_own_dm_is_refused(bot, run_async):
    name = "claude-ro1__d3"
    sess = _session(bot, name, RO_DM)
    bot._mode_switch_core = AsyncMock()
    u = _cmd("/mode ro1 full", user_id=RO, chat_id=RO_DM)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    assert len(_replies(u)) == 1 and NEEDS_ADMIN not in _replies(u)[0]
    assert name not in bot._perms_pending
    bot._mode_switch_core.assert_not_called()
    assert sess.perm_mode == "ask"


def test_perms_alias_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    _session(bot, name, chat)
    u = _cmd("/perms mine full", user_id=uid, chat_id=chat)
    run_async(bot._handle_perms_cmd(u, MagicMock()))
    assert NEEDS_ADMIN not in _replies(u)
    assert bot._perms_pending[name]["target_mode"] == "full"


# ---- Auto (Claude Code's safety check) is everyone's who may prompt -------

@pytest.mark.parametrize("uid,chat,name", [(BOB, BOB_DM, BOB_S), (BOB, GROUP, G_S)],
                         ids=["user-own-dm", "user-group"])
def test_mode_auto_typed_is_a_users_in_a_dm_and_a_group(bot, run_async, uid, chat, name):
    sess = _session(bot, name, chat)
    bot._mode_switch_core = AsyncMock(return_value=MagicMock(ok=True, reason="sent", label=sess.label))
    u = _cmd(f"/mode {sess.label} auto", user_id=uid, chat_id=chat)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    assert NEEDS_ADMIN not in _replies(u)
    bot._mode_switch_core.assert_awaited_once_with(sess, "auto")


def test_mode_auto_typed_by_read_only_in_own_dm_is_refused(bot, run_async):
    sess = _session(bot, "claude-ro1__d3", RO_DM)
    bot._mode_switch_core = AsyncMock()
    u = _cmd("/mode ro1 auto", user_id=RO, chat_id=RO_DM)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    bot._mode_switch_core.assert_not_called()
    assert sess.perm_mode == "ask"


# ===========================================================================
# The mode card: Switch to Full access, then Yes / Stop task & switch
# ===========================================================================

def _switch_tap(bot, run_async, uid, chat, sess, verb="modefull"):
    data = session_parity.session_cb(bot, chat, sess, f"{verb}{sess.turn_key}")
    u, q = _tap(chat, uid, data, msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    return q


def test_switch_to_full_then_yes_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat)
    bot._do_perms_switch_via_fn = AsyncMock()
    q = _switch_tap(bot, run_async, uid, chat, sess)
    assert NEEDS_ADMIN not in _answers(q) and REFUSED not in _answers(q)
    assert bot._perms_pending[name]["target_mode"] == "full"
    u, q2 = _tap(chat, uid, f"{name}:perms_confirm",
                 msg_id=bot._perms_pending[name]["msg_id"] or 90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert REFUSED not in _answers(q2)
    bot._do_perms_switch_via_fn.assert_awaited_once()
    assert bot._do_perms_switch_via_fn.await_args.args[:2] == (sess, "full")


@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_switch_to_full_tap_in_a_group_is_refused(bot, run_async, uid, chat, name):
    sess = _session(bot, name, chat)
    bot._do_perms_switch_via_fn = AsyncMock()
    q = _switch_tap(bot, run_async, uid, chat, sess)
    # modefull needs MANAGE at the tap gate (the same rule): refused there
    # for both. No confirm, no switch.
    assert _answers(q) == [REFUSED]
    assert name not in bot._perms_pending
    bot._do_perms_switch_via_fn.assert_not_called()


def test_switch_to_full_tap_by_read_only_in_own_dm_is_refused(bot, run_async):
    name = "claude-ro1__d3"
    sess = _session(bot, name, RO_DM)
    bot._do_perms_switch_via_fn = AsyncMock()
    q = _switch_tap(bot, run_async, RO, RO_DM, sess)
    assert _answers(q) == [REFUSED]
    assert name not in bot._perms_pending
    bot._do_perms_switch_via_fn.assert_not_called()


@pytest.mark.parametrize("uid,chat,name", [(BOB, BOB_DM, BOB_S), (BOB, GROUP, G_S)],
                         ids=["user-own-dm", "user-group"])
def test_switch_to_auto_tap_is_a_users(bot, run_async, uid, chat, name):
    sess = _session(bot, name, chat)
    bot._mode_switch_core = AsyncMock(return_value=MagicMock(ok=True, reason="sent", label=sess.label))
    q = _switch_tap(bot, run_async, uid, chat, sess, verb="modeauto")
    assert REFUSED not in _answers(q) and NEEDS_ADMIN not in _answers(q)
    bot._mode_switch_core.assert_awaited_once_with(sess, "auto")


@pytest.mark.parametrize("verb", ["perms_confirm", "perms_stop_switch"])
def test_yes_and_stop_and_switch_in_own_dm(bot, run_async, verb):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat,
                    status=Status.BUSY if verb == "perms_stop_switch" else Status.IDLE)
    bot._perms_pending[name] = {"target_mode": "full", "msg_id": 90,
                                "label": sess.label, "turn": sess.turn_key}
    bot._do_perms_switch_via_fn = AsyncMock()
    u, q = _tap(chat, uid, f"{name}:{verb}", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert REFUSED not in _answers(q)
    bot._do_perms_switch_via_fn.assert_awaited_once()
    assert bot._do_perms_switch_via_fn.await_args.args[1] == "full"


@pytest.mark.parametrize("verb", ["perms_confirm", "perms_stop_switch"])
@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_yes_and_stop_and_switch_in_a_group_are_refused(
        bot, run_async, uid, chat, name, verb):
    sess = _session(bot, name, chat,
                    status=Status.BUSY if verb == "perms_stop_switch" else Status.IDLE)
    record = {"target_mode": "full", "msg_id": 90, "label": sess.label,
              "turn": sess.turn_key}
    bot._perms_pending[name] = record
    bot._do_perms_switch_via_fn = AsyncMock()
    u, q = _tap(chat, uid, f"{name}:{verb}", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    bot._do_perms_switch_via_fn.assert_not_called()
    assert bot._perms_pending[name] is record


def test_another_persons_dm_card_is_not_theirs(bot, run_async):
    """bob taps a Yes that names alice's DM session, from alice's DM: he is
    no member there, so nothing switches."""
    sess = _session(bot, ALICE_S, ALICE_DM)
    bot._perms_pending[ALICE_S] = {"target_mode": "full", "msg_id": 90,
                                 "label": sess.label, "turn": sess.turn_key}
    bot._do_perms_switch_via_fn = AsyncMock()
    for uid in (BOB, CARA):
        u, q = _tap(ALICE_DM, uid, f"{ALICE_S}:perms_confirm", msg_id=90)
        run_async(bot._handle_callback(u, MagicMock()))
    bot._do_perms_switch_via_fn.assert_not_called()
    assert sess.perm_mode == "ask"


def test_own_dm_full_access_does_not_reach_a_group_session(bot, run_async):
    """bob's Yes for the GROUP's session, tapped in his own DM: the button
    belongs to another chat."""
    sess = _session(bot, G_S, GROUP)
    bot._perms_pending[G_S] = {"target_mode": "full", "msg_id": 90,
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


def test_new_bang_in_own_dm_starts_in_full_access(bot, run_async, launched):
    uid, chat, _ = DM_USER
    _new(bot, run_async, "/new !x7", uid, chat)
    sess = bot.registry.find_by_label("x7", chat)
    assert sess is not None and sess.perm_mode == "full"
    assert launched and launched[-1][1]["mode"] == "full"


@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_new_bang_in_a_group_gets_no_full_access(bot, run_async, launched, uid, chat, name):
    _new(bot, run_async, "/new !x7", uid, chat)
    sess = bot.registry.find_by_label("x7", chat)
    assert sess is None or sess.perm_mode == "auto"
    assert all(kw["mode"] != "full" for _s, kw in launched)


def test_new_bang_by_read_only_in_own_dm_gets_no_full_access(bot, run_async, launched):
    _new(bot, run_async, "/new !x7", RO, RO_DM)
    sess = bot.registry.find_by_label("x7", RO_DM)
    assert sess is None or sess.perm_mode != "full"
    assert all(kw["mode"] != "full" for _s, kw in launched)


def test_new_bang_in_a_group_says_full_access_needs_an_admin(bot, run_async, launched):
    u = _new(bot, run_async, "/new !x7", BOB, GROUP)
    sess = bot.registry.find_by_label("x7", GROUP)
    assert sess is not None and sess.perm_mode == "auto"
    b = bot._app.bot
    shown = "\n".join(_replies(u) + [
        str(c.kwargs.get("text", c.args[0] if c.args else ""))
        for m in (b.edit_message_text, b.send_message, u.message.reply_text.return_value.edit_text)
        for c in m.await_args_list])
    assert str(new_flow.NOTE_FULL_NEEDS_ADMIN) in shown


def test_new_without_a_setting_starts_in_auto_in_own_dm(bot, run_async, launched):
    """Like everyone's: Auto unless the chat chose otherwise."""
    uid, chat, _ = DM_USER
    st = new_flow.resolve_new_session_settings(bot, chat, uid)
    assert st["can_full"] is True and st["mode"] == "auto"
    _new(bot, run_async, "/new x8", uid, chat)
    assert bot.registry.find_by_label("x8", chat).perm_mode == "auto"


def _ns_tap(bot, run_async, uid, chat, token):
    u, q = _tap(chat, uid, f"_:set:ns:mode:{token}", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    return q


def _mode_rows(kb):
    return [row[0].text for row in kb.inline_keyboard
            if row[0].callback_data.startswith("_:set:ns:mode:")]


def test_new_follows_the_dms_new_sessions_full_access(bot, run_async, launched):
    uid, chat, _ = DM_USER
    q = _ns_tap(bot, run_async, uid, chat, "full")
    assert preferences.get_new_session_defaults(chat).mode == "full"
    # The page shows what /new now gives them: Full access, ticked.
    edits = (q.edit_message_text.await_args_list
             + bot._app.bot.edit_message_text.await_args_list)
    kb = edits[-1].kwargs["reply_markup"]
    rows = _mode_rows(kb)
    assert rows[2].endswith("✅") and not rows[0].endswith("✅")
    page = edits[-1].kwargs.get("text") or edits[-1].args[0]
    assert "Full access is for admins" not in page and "Full access mode" in page
    st = new_flow.resolve_new_session_settings(bot, chat, uid)
    assert st["mode"] == "full"
    _new(bot, run_async, "/new x9", uid, chat)
    assert bot.registry.find_by_label("x9", chat).perm_mode == "full"
    # And back: Auto is stored, /new gives Auto.
    _ns_tap(bot, run_async, uid, chat, "auto")
    assert new_flow.resolve_new_session_settings(bot, chat, uid)["mode"] == "auto"


def test_the_dm_page_offers_full_access_to_its_member(bot):
    text, kb = new_flow.render_new_session_defaults(bot, BOB_DM, viewer=BOB)
    rows = _mode_rows(kb)
    assert len(rows) == 3 and rows[0].endswith("✅")
    assert "Full access is for admins" not in text


def test_the_read_only_dm_page_does_not_offer_full_access(bot):
    """Their own private chat, but a role that may not use Full access:
    the row is not shown."""
    _text, kb = new_flow.render_new_session_defaults(bot, RO_DM, viewer=RO)
    assert [cb.callback_data for row in kb.inline_keyboard for cb in row
            if cb.callback_data.startswith("_:set:ns:mode:")] == [
        "_:set:ns:mode:auto", "_:set:ns:mode:ask"]


def test_a_groups_page_keeps_the_full_access_row(bot):
    """A group's page is shared: the row stays, and a tap is refused below."""
    _text, kb = new_flow.render_new_session_defaults(bot, GROUP, viewer=BOB)
    assert len(_mode_rows(kb)) == 3


def test_read_only_in_own_dm_cannot_store_full_access(bot, run_async):
    q = _ns_tap(bot, run_async, RO, RO_DM, "full")
    assert NEEDS_ADMIN in _answers(q)
    assert preferences.get_new_session_defaults(RO_DM).mode == "auto"


def test_read_only_in_own_dm_may_store_ask(bot, run_async):
    """Their own DM's settings are theirs (the /settings rule)."""
    _ns_tap(bot, run_async, RO, RO_DM, "ask")
    assert preferences.get_new_session_defaults(RO_DM).mode == "ask"


def test_a_user_in_a_group_cannot_store_full_access(bot, run_async):
    _ns_tap(bot, run_async, BOB, GROUP, "full")
    assert preferences.get_new_session_defaults(GROUP).mode == "auto"


@pytest.mark.parametrize("uid", [BOB, RO], ids=["user", "read_only"])
def test_a_groups_full_access_default_is_not_theirs(bot, uid):
    preferences.set_new_session_default(GROUP, "mode", "full")
    st = new_flow.resolve_new_session_settings(bot, GROUP, uid)
    assert st["mode"] == "auto" and st["can_full"] is False and st["full_refused"] is True


def test_another_dms_full_access_default_does_not_apply_to_a_visitor(bot):
    preferences.set_new_session_default(BOB_DM, "mode", "full")
    assert new_flow.resolve_new_session_settings(bot, BOB_DM, CARA)["mode"] == "auto"


def test_name_card_mode_full_tap_in_own_dm(bot, run_async):
    uid, chat, _ = DM_USER
    _new(bot, run_async, "/new", uid, chat)
    pending = new_flow._pending_store(bot)[(chat, uid)]
    assert pending["can_full"] is True and pending["mode"] == "auto"
    u, q = _tap(chat, uid, "_:nw:mode:full", msg_id=pending.get("msg_id") or 901)
    run_async(bot._handle_callback(u, MagicMock()))
    assert NEEDS_ADMIN not in _answers(q)
    assert pending["mode"] == "full"


def test_name_card_mode_full_tap_in_a_group_is_refused(bot, run_async):
    _new(bot, run_async, "/new", BOB, GROUP)
    pending = new_flow._pending_store(bot)[(GROUP, BOB)]
    u, q = _tap(GROUP, BOB, "_:nw:mode:full", msg_id=pending.get("msg_id") or 901)
    run_async(bot._handle_callback(u, MagicMock()))
    assert NEEDS_ADMIN in _answers(q)
    assert pending["mode"] == "auto"


def _ready_tap(bot, run_async, uid, chat, name, verb="rdy_full", **kw):
    sess = _session(bot, name, chat, **kw)
    sess.tap_is_for_this_turn = lambda *a, **k: True
    bot._mode_switch_core = AsyncMock(return_value=MagicMock(ok=True, err="", reason="ok"))
    data = session_parity.session_cb(bot, chat, sess, verb)
    u, q = _tap(chat, uid, data, msg_id=95)
    run_async(bot._handle_callback(u, MagicMock()))
    return sess, q


def test_ready_card_switch_to_full_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess, q = _ready_tap(bot, run_async, uid, chat, name)
    assert NEEDS_ADMIN not in _answers(q) and REFUSED not in _answers(q)
    bot._mode_switch_core.assert_awaited_once_with(sess, "full")


def test_ready_card_switch_to_full_in_a_group_is_refused(bot, run_async):
    _sess, q = _ready_tap(bot, run_async, BOB, GROUP, G_S)
    assert _answers(q) == [REFUSED]
    bot._mode_switch_core.assert_not_called()


@pytest.mark.parametrize("uid,chat,name", [(BOB, BOB_DM, BOB_S), (BOB, GROUP, G_S)],
                         ids=["user-own-dm", "user-group"])
def test_ready_card_switch_to_auto_is_a_users(bot, run_async, uid, chat, name):
    sess, q = _ready_tap(bot, run_async, uid, chat, name, verb="rdy_auto")
    assert REFUSED not in _answers(q) and NEEDS_ADMIN not in _answers(q)
    bot._mode_switch_core.assert_awaited_once_with(sess, "auto")


def _ready_buttons(bot, chat, name, author):
    sess = _session(bot, name, chat, mode="auto")
    sess.created_by_user_id = author
    _text, kb = new_flow.render_ready(bot, None, sess)
    return [b.text for row in kb.inline_keyboard for b in row]


def test_ready_card_offers_full_access_to_a_user_in_own_dm(bot):
    assert "🔓 Switch to Full access mode" in _ready_buttons(bot, BOB_DM, BOB_S, BOB)


@pytest.mark.parametrize("chat,name,author", [(GROUP, G_S, BOB), (RO_DM, "claude-ro1__d3", RO)],
                         ids=["user-group", "read_only-own-dm"])
def test_ready_card_offers_no_full_access_to_others(bot, chat, name, author):
    buttons = _ready_buttons(bot, chat, name, author)
    assert "🔓 Switch to Full access mode" not in buttons
    assert "💬 Switch to Ask mode" in buttons


# ===========================================================================
# /resume of a session that was in Full access
# ===========================================================================

def test_typed_resume_in_own_dm_comes_back_in_full_access(bot, run_async, launched):
    uid, chat, name = DM_USER
    sess = _gone_full(bot, name, chat)
    u = _cmd("/resume mine", user_id=uid, chat_id=chat)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    assert launched[0][1]["mode"] == "full" and sess.perm_mode == "full"
    assert AUTO_NOTE not in "\n".join(_replies(u))


@pytest.mark.parametrize("uid", [BOB], ids=["group-user"])
def test_typed_resume_in_a_group_comes_back_in_auto(bot, run_async, launched, uid):
    sess = _gone_full(bot, G_S, GROUP)
    u = _cmd("/resume api", user_id=uid, chat_id=GROUP)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    assert launched[0][1]["mode"] == "auto" and sess.perm_mode == "auto"
    assert AUTO_NOTE in "\n".join(_replies(u))


def test_a_session_a_dm_member_put_in_bypass_before_comes_back_in_full_access(
        bot, run_async, launched):
    """Migration: a session saved by the dm-user-auto build in its bypass
    "Auto" (skip_perms True) is Full access now, which its member may
    still use in their own DM."""
    from aipager import perm_modes
    uid, chat, name = DM_USER
    assert perm_modes.mode_from_saved({"skip_perms": True}) == "full"
    sess = _gone_full(bot, name, chat)
    u = _cmd("/resume mine", user_id=uid, chat_id=chat)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    assert launched[0][1]["mode"] == "full" and sess.perm_mode == "full"


def test_picker_resume_full_in_own_dm(bot, run_async, launched):
    uid, chat, name = DM_USER
    sess = _gone_full(bot, name, chat)
    data = session_parity.session_cb(bot, chat, sess, "resume")
    u, q = _tap(chat, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    labels = [b.text for row in
              q.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
              for b in row]
    assert "🔓 Full access mode (default)" in labels
    sess.perm_mode = "ask"
    data = session_parity.session_cb(bot, chat, sess, "resume-full")
    u, q = _tap(chat, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert REFUSED not in _answers(q)
    assert launched[0][1]["mode"] == "full"


@pytest.mark.parametrize("uid,chat,name", GROUP_REFUSED, ids=G_IDS)
def test_picker_resume_full_in_a_group_is_refused(bot, run_async, launched,
                                                  uid, chat, name):
    sess = _gone_full(bot, name, chat)
    sess.perm_mode = "ask"
    data = session_parity.session_cb(bot, chat, sess, "resume-full")
    u, q = _tap(chat, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert _answers(q) == [REFUSED]
    assert launched == []


def test_legacy_resume_mode_auto_in_own_dm_is_auto(bot, run_async, launched):
    """A button from before the three modes said Auto for bypass: it now
    means Auto, never wider, in a DM too."""
    uid, chat, name = DM_USER
    _gone_full(bot, name, chat)
    u, q = _tap(chat, uid, f"{name}:resume_mode_auto", msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched and launched[0][1]["mode"] == "auto"


def test_new_conflict_card_resume_in_own_dm(bot, run_async, launched):
    uid, chat, name = DM_USER
    sess = _gone_full(bot, name, chat)
    bot._new_conflict_pending[name] = {"user_id": uid, "prompt": "",
                                       "mode": "auto", "msg_id": 0}
    u, q = _tap(chat, uid, f"{name}:new_resume", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched[0][1]["mode"] == "full" and sess.perm_mode == "full"
    assert not any(AUTO_NOTE in t for t in _edited(q))


def test_new_conflict_card_replace_keeps_full_access_in_own_dm(bot, run_async, launched):
    """Replace re-checks Full access against the card's author at creation."""
    uid, chat, name = DM_USER
    _gone_full(bot, name, chat)
    bot._new_conflict_pending[name] = {"user_id": uid, "prompt": "",
                                       "mode": "full", "msg_id": 0,
                                       "cwd": ROOTS[chat]}
    u, q = _tap(chat, uid, f"{name}:new_replace", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched and launched[-1][1]["mode"] == "full"


def test_new_conflict_card_replace_in_a_group_drops_full_access(bot, run_async, launched):
    _gone_full(bot, G_S, GROUP)
    bot._new_conflict_pending[G_S] = {"user_id": BOB, "prompt": "",
                                      "mode": "full", "msg_id": 0,
                                      "cwd": ROOTS[GROUP]}
    u, q = _tap(GROUP, BOB, f"{G_S}:new_replace", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched and launched[-1][1]["mode"] == "auto"


# ===========================================================================
# The Mini App: mode switch, create, resume, session actions
# ===========================================================================

def _srv(bot, sess, chat, uid):
    srv = MiniAppServer(bot, bot.registry, port=8765)
    srv._resolve_own_scope_session = AsyncMock(return_value=(sess, chat, uid))
    srv._allow_write = lambda _uid: True
    return srv


def _perms_request(mode):
    req = MagicMock()
    req.json = AsyncMock(return_value={"mode": mode})
    return req


def test_miniapp_mode_switch_to_full_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat)
    bot._mode_switch_core = AsyncMock(return_value=MagicMock(ok=True, reason="ok"))
    srv = _srv(bot, sess, chat, uid)
    srv._restart_outcome_response = MagicMock(return_value=MagicMock(status=200))
    resp = run_async(srv._handle_session_perms(_perms_request("full")))
    assert resp.status == 200
    bot._mode_switch_core.assert_awaited_once_with(sess, "full")


@pytest.mark.parametrize("uid,chat,name", [(BOB, GROUP, G_S)], ids=["group-user"])
def test_miniapp_mode_switch_to_full_in_a_group_is_refused(bot, run_async, uid, chat, name):
    sess = _session(bot, name, chat)
    bot._mode_switch_core = AsyncMock()
    resp = run_async(_srv(bot, sess, chat, uid)._handle_session_perms(_perms_request("full")))
    assert resp.status == 403
    assert json.loads(resp.body)["detail"] == APP_SWITCH_NEEDS_ADMIN
    bot._mode_switch_core.assert_not_called()


@pytest.mark.parametrize("uid,chat,name", [(BOB, BOB_DM, BOB_S), (BOB, GROUP, G_S)],
                         ids=["user-own-dm", "user-group"])
def test_miniapp_mode_switch_to_auto_is_a_users(bot, run_async, uid, chat, name):
    sess = _session(bot, name, chat)
    bot._mode_switch_core = AsyncMock(return_value=MagicMock(ok=True, reason="ok"))
    srv = _srv(bot, sess, chat, uid)
    srv._restart_outcome_response = MagicMock(return_value=MagicMock(status=200))
    resp = run_async(srv._handle_session_perms(_perms_request("auto")))
    assert resp.status == 200
    bot._mode_switch_core.assert_awaited_once_with(sess, "auto")


def test_miniapp_resume_in_own_dm_comes_back_in_full_access(bot, run_async, launched):
    uid, chat, name = DM_USER
    sess = _gone_full(bot, name, chat)
    resp = run_async(_srv(bot, sess, chat, uid)._handle_session_resume(MagicMock()))
    body = json.loads(resp.body)
    assert body["status"] == "resumed" and "detail" not in body
    assert launched[0][1]["mode"] == "full"


def test_miniapp_resume_in_a_group_comes_back_in_auto(bot, run_async, launched):
    sess = _gone_full(bot, G_S, GROUP)
    resp = run_async(_srv(bot, sess, GROUP, BOB)._handle_session_resume(MagicMock()))
    body = json.loads(resp.body)
    assert body["mode"] == "auto" and launched[0][1]["mode"] == "auto"


def _full_option(body):
    return next(m for m in body["modes"] if m["id"] == "full")


def test_miniapp_session_detail_offers_full_access_in_own_dm(bot, run_async):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat)
    body = json.loads(run_async(
        _srv(bot, sess, chat, uid)._handle_session_detail(MagicMock())).body)
    assert _full_option(body)["available"] is True
    gsess = _session(bot, G_S, GROUP)
    body = json.loads(run_async(
        _srv(bot, gsess, GROUP, BOB)._handle_session_detail(MagicMock())).body)
    assert _full_option(body)["available"] is False
    assert next(m for m in body["modes"] if m["id"] == "auto")["available"] is True


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
    (BOB, BOB_DM, True), (BOB, GROUP, False), (RO, GROUP, False), (RO, RO_DM, False),
    (ALICE, GROUP, True)], ids=["user-own-dm", "user-group", "ro-group", "ro-own-dm",
                              "owner-group"])
def test_miniapp_create_form_says_who_may_use_full_access(bot, run_async, uid, chat, want):
    async def _fn(client):
        resp = await client.get("/api/session-options", headers=_hdr(uid, chat))
        assert resp.status == 200, await resp.text()
        return await resp.json()
    assert _http(bot, run_async, _fn)["can_use_full"] is want


def test_miniapp_create_form_default_follows_the_dms_full_access(bot, run_async):
    preferences.set_new_session_default(BOB_DM, "mode", "full")

    async def _fn(client):
        resp = await client.get("/api/session-options", headers=_hdr(BOB, BOB_DM))
        return await resp.json()
    assert _http(bot, run_async, _fn)["default_mode"] == "full"


@pytest.mark.parametrize("uid,chat,want", [
    (BOB, BOB_DM, 201), (BOB, GROUP, 403)], ids=["user-own-dm", "user-group"])
def test_miniapp_create_in_full_access(bot, run_async, launched, uid, chat, want):
    async def _fn(client):
        resp = await client.post("/api/sessions", headers=_hdr(uid, chat),
                                 json={"name": "web1", "mode": "full"})
        return resp.status, await resp.json()
    status, body = _http(bot, run_async, _fn)
    if want == 403:
        assert status == 403 and body["detail"] == APP_NEEDS_ADMIN
        assert launched == []
    else:
        assert status in (200, 201), body
        assert launched and launched[-1][1]["mode"] == "full"


@pytest.mark.parametrize("uid,chat", [(BOB, BOB_DM), (BOB, GROUP)],
                         ids=["user-own-dm", "user-group"])
def test_miniapp_create_in_auto_is_a_users(bot, run_async, launched, uid, chat):
    async def _fn(client):
        resp = await client.post("/api/sessions", headers=_hdr(uid, chat),
                                 json={"name": "web2", "mode": "auto"})
        return resp.status, await resp.json()
    status, body = _http(bot, run_async, _fn)
    assert status in (200, 201), body
    assert launched and launched[-1][1]["mode"] == "auto"


def test_miniapp_other_persons_dm_is_out_of_reach(bot, run_async):
    _session(bot, ALICE_S, ALICE_DM)

    async def _fn(client):
        r1 = await client.post("/api/sessions/ops/perms", headers=_hdr(BOB, ALICE_DM),
                               json={"mode": "full"})
        r2 = await client.put("/api/preferences/layout", headers=_hdr(BOB, ALICE_DM),
                              json={"value": "merged"})
        return r1.status, r2.status
    assert _http(bot, run_async, _fn) == (403, 403)
    assert bot.registry.get(ALICE_S).perm_mode == "ask"
    assert preferences.get_preferences(ALICE_DM).layout != "merged"


# ===========================================================================
# Root folders: no mode switch of a session outside the member's root
# ===========================================================================

@pytest.mark.parametrize("target", ["auto", "full"], ids=["shift-tab-auto", "restart-full"])
def test_typed_mode_switch_outside_the_root_is_refused(bot, run_async, target):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat, cwd=_outside(chat))
    bot._mode_switch_core = AsyncMock()
    u = _cmd(f"/mode mine {target}", user_id=uid, chat_id=chat)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    assert any(OUTSIDE in r for r in _replies(u)), _replies(u)
    assert name not in bot._perms_pending
    bot._mode_switch_core.assert_not_called()
    assert sess.perm_mode == "ask"


@pytest.mark.parametrize("verb", ["modeauto", "modefull"])
def test_card_mode_switch_outside_the_root_is_refused(bot, run_async, verb):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat, cwd=_outside(chat))
    bot._mode_switch_core = AsyncMock()
    bot._do_perms_switch_via_fn = AsyncMock()
    q = _switch_tap(bot, run_async, uid, chat, sess, verb=verb)
    assert any(OUTSIDE in a for a in _answers(q)), _answers(q)
    assert name not in bot._perms_pending
    bot._mode_switch_core.assert_not_called()
    bot._do_perms_switch_via_fn.assert_not_called()


@pytest.mark.parametrize("verb", ["perms_confirm", "perms_stop_switch"])
def test_full_access_confirm_outside_the_root_is_refused(bot, run_async, verb):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat, cwd=_outside(chat),
                    status=Status.BUSY if verb == "perms_stop_switch" else Status.IDLE)
    bot._perms_pending[name] = {"target_mode": "full", "msg_id": 90,
                                "label": sess.label, "turn": sess.turn_key}
    bot._do_perms_switch_via_fn = AsyncMock()
    u, q = _tap(chat, uid, f"{name}:{verb}", msg_id=90)
    run_async(bot._handle_callback(u, MagicMock()))
    assert any(OUTSIDE in a for a in _answers(q)), _answers(q)
    bot._do_perms_switch_via_fn.assert_not_called()


@pytest.mark.parametrize("verb", ["rdy_auto", "rdy_full"])
def test_ready_card_switch_outside_the_root_is_refused(bot, run_async, verb):
    uid, chat, name = DM_USER
    _sess, q = _ready_tap(bot, run_async, uid, chat, name, verb=verb,
                          cwd=_outside(chat))
    assert any(OUTSIDE in a for a in _answers(q)), _answers(q)
    bot._mode_switch_core.assert_not_called()


@pytest.mark.parametrize("target", ["auto", "full"], ids=["shift-tab-auto", "restart-full"])
def test_miniapp_mode_switch_outside_the_root_is_refused(bot, run_async, target):
    uid, chat, name = DM_USER
    sess = _session(bot, name, chat, cwd=_outside(chat))
    bot._mode_switch_core = AsyncMock()
    resp = run_async(_srv(bot, sess, chat, uid)._handle_session_perms(_perms_request(target)))
    assert resp.status == 403
    assert OUTSIDE in json.loads(resp.body)["detail"]
    bot._mode_switch_core.assert_not_called()


def test_an_admin_switches_a_session_outside_the_root(bot, run_async):
    """The root rule holds members without can_manage only."""
    sess = _session(bot, ALICE_S, ALICE_DM, cwd=_outside(ALICE_DM))
    bot._mode_switch_core = AsyncMock(return_value=MagicMock(ok=True, reason="sent", label=sess.label))
    u = _cmd("/mode ops auto", user_id=ALICE, chat_id=ALICE_DM)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    bot._mode_switch_core.assert_awaited_once_with(sess, "auto")


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
# Safety: the hook applies the user's role in every mode
# ===========================================================================

@pytest.mark.parametrize("reported", ["default", "auto", "bypassPermissions"],
                         ids=["ask", "auto", "full-access"])
def test_the_hook_still_denies_a_users_bash_in_every_mode(tmp_path, monkeypatch, bot, reported):
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
    # in Auto and in Full access. A user's Bash call is denied by the
    # role's own rule.
    out = enforce.decide({
        "hook_event_name": "PreToolUse", "session": BOB_S,
        "session_id": "5b1d7363-aaaa-bbbb-cccc-000000000002",
        "cwd": str(project), "tool_name": "Bash",
        "tool_input": {"command": "ls"}, "transcript_path": str(tp),
        "permission_mode": reported,
    })
    assert out is not None and out["tool"] == "Bash"
    assert "deny_tools" in out["reason"]


# ===========================================================================
# The Mini App's Settings tab follows /settings
# ===========================================================================

@pytest.mark.parametrize("uid,chat,want", [
    (BOB, BOB_DM, True), (RO, RO_DM, True), (ALICE, ALICE_DM, True),
    (ALICE, GROUP, True), (BOB, GROUP, False), (RO, GROUP, False),
    (BOB, ALICE_DM, False), (CARA, BOB_DM, False), (BOB, None, False),
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
