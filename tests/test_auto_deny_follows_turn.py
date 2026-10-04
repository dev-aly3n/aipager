"""The permission-dialog auto-deny follows the RUNNING turn (roadmap 8.94a).

Before: scope mode decided from the session's last Telegram sender
(``_driver_user``), so the operator typing in the terminal of a session a
``user`` had driven last got "⛔ x1 · Edit blocked for @bob (role user)".
Now: a terminal turn is never denied by a Telegram member's rules; one
known sender's turn uses that sender's rules; a mixed turn is denied when
any known author's rules deny (naming that author); an unknown turn keeps
the old fail-closed fallback (last sender's rules, else the scope-wide
list). Legacy team.yaml mode is unchanged. The operator's own install (one
DM, owner) sees no auto-deny either way.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import (
    TURN_SENDER_MIXED, TURN_SENDER_TERMINAL, Status, TrackedSession,
)

GROUP = -1001
DM = 555
# GONE_ID (not a member) sorts first, so a mixed turn has to prefer the
# author it can name over an earlier one it cannot.
ALY, BOB, CAROL, GONE_ID = 10, 20, 30, 5

POLICY = load_policy(Path("/nonexistent/aipager-policy.yaml"),
                     Path("/nonexistent/policy.d"))


def _group(deny=("Edit",)):
    return Scope(chat_id=GROUP, kind="group", label="team",
                 members=(Member(id=ALY, label="aly", role="owner"),
                          Member(id=BOB, label="bob", role="user"),
                          Member(id=CAROL, label="carol", role="user",
                                 deny_tools=("WebFetch",))),
                 deny_tools=tuple(deny))


def _dm(deny=()):
    return Scope(chat_id=DM, kind="dm", label="owner DM",
                 members=(Member(id=ALY, label="owner", role="owner"),),
                 deny_tools=tuple(deny))


def _bot(mk_bot, scopes=None):
    bot = mk_bot(scopes=scopes if scopes is not None else [_group(), _dm()])
    bot.policy = POLICY
    return bot


def _sess(*, chat=GROUP, kind="group", last=BOB, turn=None, mixed=()):
    s = TrackedSession(name="claude-x1__g1001", label="x1",
                       status=Status.INTERACTIVE)
    s.scope_chat_id, s.scope_kind = chat, kind
    s.last_driver_user_id = last
    s.turn_sender_id = turn
    s.turn_mixed_authors = tuple(mixed)
    s.busy_msg_id = 42
    return s


def _snap(sess, **fields):
    path = policy_snapshot.snapshot_path(sess.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    policy_snapshot.write_merged_snapshot(sess.name, dict(fields))
    assert policy_snapshot.read_snapshot(sess.name) == fields


def _decide(bot, sess, tool="Edit"):
    denied, whose = bot._auto_deny_decision(sess, tool)
    return denied, (whose.label if whose is not None else None)


# ---- the decision ---------------------------------------------------------

def test_terminal_turn_in_a_session_a_user_drove_last_is_not_denied(mk_bot):
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=TURN_SENDER_TERMINAL)
    assert _decide(bot, sess) == (False, None)
    assert bot._tool_auto_denied(sess, "Edit") is False


def test_unknown_turn_whose_snapshot_says_terminal_is_not_denied(mk_bot):
    """After a restart ``turn_sender_id`` is None; the hook's snapshot for
    the turn still says it was typed in the terminal."""
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=None)
    _snap(sess, turn_origin="terminal")
    assert _decide(bot, sess) == (False, None)


def test_unknown_turn_whose_snapshot_says_telegram_falls_back(mk_bot):
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=None)
    _snap(sess, turn_origin="telegram")
    assert _decide(bot, sess) == (True, "bob")


@pytest.mark.parametrize("raw", ["[]", "5", "null", '"terminal"', "{bad"])
def test_unknown_turn_with_a_snapshot_that_is_not_an_object_falls_back(
        mk_bot, raw):
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=None)
    path = policy_snapshot.snapshot_path(sess.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    assert _decide(bot, sess) == (True, "bob")


@pytest.mark.parametrize("turn", [TURN_SENDER_TERMINAL, None])
def test_terminal_turn_a_telegram_message_joined_falls_back(mk_bot, turn):
    """A joined terminal turn runs with the joiner's rules in the hook, so
    the daemon keeps the fail-closed fallback for it."""
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=turn)
    _snap(sess, turn_origin="terminal", joined_from_telegram=True)
    assert _decide(bot, sess) == (True, "bob")


def test_a_users_telegram_turn_is_denied_naming_that_user(mk_bot):
    """The running turn is bob's even though aly sent the last message."""
    bot = _bot(mk_bot)
    sess = _sess(last=ALY, turn=BOB)
    assert _decide(bot, sess) == (True, "bob")


def test_an_owners_telegram_turn_is_not_denied(mk_bot):
    """The running turn is aly's even though bob sent the last message."""
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=ALY)
    assert _decide(bot, sess) == (False, None)


def test_a_users_own_deny_tools_apply_to_their_turn(mk_bot):
    bot = _bot(mk_bot)
    assert _decide(bot, _sess(last=ALY, turn=CAROL), "WebFetch") == (
        True, "carol")
    assert _decide(bot, _sess(last=CAROL, turn=BOB), "WebFetch") == (
        False, None)


def test_a_sender_who_left_the_chat_gets_the_scope_wide_list(mk_bot):
    bot = _bot(mk_bot)
    assert _decide(bot, _sess(last=ALY, turn=GONE_ID)) == (True, None)
    assert _decide(bot, _sess(last=ALY, turn=GONE_ID), "Bash") == (
        False, None)


def test_mixed_owner_and_user_is_denied_naming_the_user(mk_bot):
    bot = _bot(mk_bot)
    sess = _sess(last=ALY, turn=TURN_SENDER_MIXED, mixed=(ALY, BOB))
    assert _decide(bot, sess) == (True, "bob")


def test_mixed_names_the_known_author_over_one_who_left(mk_bot):
    bot = _bot(mk_bot)
    sess = _sess(last=ALY, turn=TURN_SENDER_MIXED, mixed=(BOB, GONE_ID))
    assert _decide(bot, sess) == (True, "bob")


def test_mixed_with_only_someone_who_left_uses_the_scope_wide_list(mk_bot):
    bot = _bot(mk_bot)
    sess = _sess(last=ALY, turn=TURN_SENDER_MIXED, mixed=(ALY, GONE_ID))
    assert _decide(bot, sess) == (True, None)


def test_mixed_strictest_wins_on_any_authors_own_rule(mk_bot):
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=TURN_SENDER_MIXED, mixed=(BOB, CAROL))
    assert _decide(bot, sess, "WebFetch") == (True, "carol")


def test_mixed_of_owners_only_is_not_denied(mk_bot):
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=TURN_SENDER_MIXED, mixed=(ALY,))
    assert _decide(bot, sess) == (False, None)


def test_mixed_with_no_known_authors_falls_back(mk_bot):
    bot = _bot(mk_bot)
    sess = _sess(last=BOB, turn=TURN_SENDER_MIXED, mixed=())
    assert _decide(bot, sess) == (True, "bob")


@pytest.mark.parametrize("odd", [0, -7, True, "someone"])
def test_a_turn_sender_that_is_no_person_falls_back(mk_bot, odd):
    bot = _bot(mk_bot)
    assert _decide(bot, _sess(last=BOB, turn=odd)) == (True, "bob")


def test_unknown_turn_uses_the_last_senders_rules(mk_bot):
    """Today's fail-closed fallback, kept for a turn whose sender is not
    known."""
    bot = _bot(mk_bot)
    assert _decide(bot, _sess(last=BOB, turn=None)) == (True, "bob")
    assert _decide(bot, _sess(last=ALY, turn=None)) == (False, None)


def test_unknown_turn_and_unknown_sender_use_the_scope_wide_list(mk_bot):
    bot = _bot(mk_bot)
    assert _decide(bot, _sess(last=None, turn=None)) == (True, None)
    assert _decide(bot, _sess(last=None, turn=None), "Read") == (False, None)


def test_role_is_read_in_the_sessions_chat(mk_bot):
    """bob is an owner in another group and a user in this one: his turn
    here uses this chat's role."""
    other = Scope(chat_id=-1002, kind="group", label="other",
                  members=(Member(id=BOB, label="bob", role="owner"),))
    bot = _bot(mk_bot, scopes=[_group(), _dm(), other])
    assert _decide(bot, _sess(last=ALY, turn=BOB)) == (True, "bob")


def test_no_tool_name_is_never_denied(mk_bot):
    bot = _bot(mk_bot)
    assert _decide(bot, _sess(last=BOB, turn=BOB), "") == (False, None)


# ---- through the permission prompt ----------------------------------------

def _prompt(bot, sess, run_async, tool="Edit"):
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._maybe_update_bot_name = AsyncMock()
    bot._auto_deny = AsyncMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": tool, "summary": "a.py",
                      "input": {"file_path": "a.py"}},
    }))
    return bot._auto_deny


def test_prompt_terminal_turn_shows_the_prompt(mk_bot, run_async):
    bot = _bot(mk_bot)
    auto = _prompt(bot, _sess(last=BOB, turn=TURN_SENDER_TERMINAL), run_async)
    auto.assert_not_awaited()


def test_prompt_users_turn_is_denied_naming_the_user(mk_bot, run_async):
    bot = _bot(mk_bot)
    sess = _sess(last=ALY, turn=BOB)
    auto = _prompt(bot, sess, run_async)
    auto.assert_awaited_once()
    assert auto.await_args.args[2].label == "bob"


def test_prompt_mixed_turn_is_denied_naming_the_user(mk_bot, run_async):
    bot = _bot(mk_bot)
    sess = _sess(last=ALY, turn=TURN_SENDER_MIXED, mixed=(ALY, BOB))
    auto = _prompt(bot, sess, run_async)
    auto.assert_awaited_once()
    assert auto.await_args.args[2].label == "bob"


def test_prompt_owners_turn_shows_the_prompt(mk_bot, run_async):
    bot = _bot(mk_bot)
    auto = _prompt(bot, _sess(last=BOB, turn=ALY), run_async)
    auto.assert_not_awaited()


def test_prompt_unknown_turn_keeps_the_fallback(mk_bot, run_async):
    bot = _bot(mk_bot)
    auto = _prompt(bot, _sess(last=BOB, turn=None), run_async)
    auto.assert_awaited_once()
    assert auto.await_args.args[2].label == "bob"


def test_notice_names_the_turns_sender_not_the_last_sender(
        mk_bot, run_async, monkeypatch):
    """End to end: the posted line blames bob, whose turn it is, not aly,
    who sent the last message. (asyncio.sleep is never patched through a
    module path: the 0.1 s pause between the two keys is real.)"""
    bot = _bot(mk_bot)
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.audit.append", lambda **k: None)
    sess = _sess(last=ALY, turn=BOB)
    bot.registry._sessions[sess.name] = sess
    bot._stop_animation = MagicMock()
    run_async(bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "Edit", "summary": "a.py",
                      "input": {"file_path": "a.py"}},
    }))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text == "⛔ <b>x1</b> · Edit blocked for @bob (role user)\n<i>a.py</i>"


# ---- DM parity: the operator's own install --------------------------------

@pytest.mark.parametrize("turn", [TURN_SENDER_TERMINAL, ALY, None])
def test_dm_owner_is_never_auto_denied(mk_bot, run_async, turn):
    bot = _bot(mk_bot, scopes=[_dm(deny=("Edit",))])
    sess = _sess(chat=DM, kind="dm", last=ALY, turn=turn)
    assert _decide(bot, sess) == (False, None)
    _prompt(bot, sess, run_async).assert_not_awaited()


# ---- legacy team.yaml mode: unchanged -------------------------------------

def _team_bot(mk_bot):
    from aipager.team import Role, Rules, Team, User as TeamUser
    bot = mk_bot()
    bot.team = Team(
        group_id=-100,
        users={1: TeamUser(id=1, label="admin", role=Role.ADMIN),
               2: TeamUser(id=2, label="dev", role=Role.DEVELOPER)},
        rules=Rules(deny_tools=["Edit"]),
    )
    return bot


@pytest.mark.parametrize("turn", [TURN_SENDER_TERMINAL, 1, None])
def test_legacy_team_mode_still_uses_the_last_sender(mk_bot, run_async, turn):
    bot = _team_bot(mk_bot)
    assert bot._auto_deny_decision(_sess(last=2, turn=turn), "Edit") == (
        False, None)  # scope-mode decision only
    auto = _prompt(bot, _sess(last=2, turn=turn), run_async)
    auto.assert_awaited_once()
    assert auto.await_args.args[2].label == "dev"
    auto = _prompt(bot, _sess(last=1, turn=turn), run_async)
    auto.assert_not_awaited()
