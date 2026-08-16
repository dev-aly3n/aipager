"""Phase C: scope-aware authorization."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession


def _scopes():
    return [
        Scope(chat_id=-100, kind="group", label="dev",
              members=(Member(id=1, label="aly", role="owner"),
                       Member(id=2, label="bob", role="user"),
                       Member(id=3, label="ro", role="read_only"))),
        Scope(chat_id=555, kind="dm", label="aly DM",
              members=(Member(id=1, label="aly", role="owner"),)),
    ]


def _bot(mk_bot):
    bot = mk_bot(scopes=_scopes())
    bot.policy = load_policy()  # built-in role defaults
    return bot


def _update(chat_id, user_id):
    u = MagicMock()
    u.effective_chat = MagicMock()
    u.effective_chat.id = chat_id
    u.effective_user = MagicMock()
    u.effective_user.id = user_id
    u.effective_user.username = "x"
    u.effective_user.first_name = "X"
    u.effective_user.last_name = ""
    u.effective_message = MagicMock()
    u.effective_message.reply_text = AsyncMock()
    return u


def test_member_authorized(mk_bot, run_async):
    bot = _bot(mk_bot)
    assert run_async(bot._authorize(_update(-100, 2))) is True


def test_non_member_rejected(mk_bot, run_async):
    bot = _bot(mk_bot)
    upd = _update(-100, 999)
    assert run_async(bot._authorize(upd)) is False
    upd.effective_message.reply_text.assert_awaited()


def test_unknown_group_chat_silent(mk_bot, run_async):
    bot = _bot(mk_bot)
    upd = _update(-9999, 2)  # negative chat not in scopes → silent
    assert run_async(bot._authorize(upd)) is False
    upd.effective_message.reply_text.assert_not_awaited()


def test_read_only_blocked_unless_allowed(mk_bot, run_async):
    bot = _bot(mk_bot)
    assert run_async(bot._authorize(_update(-100, 3))) is False
    assert run_async(bot._authorize(_update(-100, 3), allow_read_only=True)) is True


def test_same_user_different_scopes(mk_bot, run_async):
    bot = _bot(mk_bot)
    # user 1 is owner in both the group and their DM
    assert run_async(bot._authorize(_update(-100, 1))) is True
    assert run_async(bot._authorize(_update(555, 1))) is True
    # user 2 is only in the group, not the DM scope (chat 555 is user 1's)
    assert run_async(bot._authorize(_update(555, 2))) is False


def test_mark_and_resolve_driver(mk_bot):
    bot = _bot(mk_bot)
    sess = TrackedSession(name="claude-x__g100", label="x", status=Status.IDLE)
    member = bot._mark_driver(sess, _update(-100, 2))
    assert member.label == "bob"
    assert sess.last_driver_user_id == 2
    assert bot._driver_user(sess).label == "bob"


# ---- _can_prompt_user (batch 4: session-scoped preference writes) --------

def test_can_prompt_user_owner_and_user_true(mk_bot):
    bot = _bot(mk_bot)
    assert bot._can_prompt_user(1, -100) is True  # owner
    assert bot._can_prompt_user(2, -100) is True  # user


def test_can_prompt_user_read_only_false(mk_bot):
    bot = _bot(mk_bot)
    assert bot._can_prompt_user(3, -100) is False  # read_only


def test_can_prompt_user_non_member_false(mk_bot):
    bot = _bot(mk_bot)
    assert bot._can_prompt_user(999, -100) is False


def test_can_prompt_user_unknown_chat_false(mk_bot):
    bot = _bot(mk_bot)
    assert bot._can_prompt_user(1, -9999) is False


def test_can_prompt_user_respects_scope_boundary_like_is_admin_user(mk_bot):
    """Same identity, two scopes: user 2 is a member of the group but not
    of user 1's DM — must not be able to prompt a session scoped there."""
    bot = _bot(mk_bot)
    assert bot._can_prompt_user(2, -100) is True   # group: member
    assert bot._can_prompt_user(2, 555) is False   # DM: not a member


def test_can_prompt_user_true_but_is_admin_user_false_for_ordinary_member(mk_bot):
    """The asymmetry the whole per-session design decision rests on: an
    ordinary "user"-role member may set a session override (can_prompt)
    but the built-in "user" role does not bypass_safety, so the SAME
    identity is refused the scope-wide write. Two deliberately different
    gates, not one gate reused."""
    bot = _bot(mk_bot)
    assert bot._can_prompt_user(2, -100) is True
    assert bot._is_admin_user(2, -100) is False


def test_authorize_callback_scoped(mk_bot, run_async):
    bot = _bot(mk_bot)
    q = MagicMock()
    q.from_user = MagicMock()
    q.from_user.id = 2
    q.message = MagicMock()
    q.message.chat = MagicMock()
    q.message.chat.id = -100
    q.answer = AsyncMock()
    assert run_async(bot._authorize_callback(q)).label == "bob"
    # non-member → None + toast
    q.from_user.id = 999
    assert run_async(bot._authorize_callback(q)) is None
    q.answer.assert_awaited()
