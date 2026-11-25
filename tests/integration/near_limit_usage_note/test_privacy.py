"""design.md success criteria "Privacy": ``usage_note.windows_for`` hands
windows only to a private chat whose person may run /usage with plan limits,
only for the session's own sign-in, and never raises."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from aipager import config, usage_store
from aipager import team as team_mod
from aipager.bot import usage_cmd, usage_note
from aipager.scope import Member, Scope
from aipager.state import TrackedSession
from aipager.statusline_file import Window

from ._kit import (
    ADMIN,
    DAY,
    GROUP,
    LOGIN_A,
    LOGIN_B,
    NOW,
    OWNER,
    READER,
    UNKNOWN,
    USER,
    scopes,
)

FAR = int(NOW) + 2 * DAY
NEAR = {"seven_day": Window(95, FAR)}


@pytest.fixture
def scoped(mk_bot):
    return mk_bot(scopes=scopes())


@pytest.fixture
def near_a():
    usage_store.note_plan("frontend", NEAR, LOGIN_A, scope=OWNER, now=NOW)


def _sess(chat, login=LOGIN_A):
    sess = TrackedSession(name="claude-frontend", label="frontend")
    if chat is not None:
        sess.scope_chat_id = chat
    if login is not None:
        sess.usage_plan_key = usage_store.plan_key(login)
    return sess


def _for(bot, sess):
    return usage_note.windows_for(bot, sess, now=NOW)


# ---- who may see it (scope mode) --------------------------------------------------------

def test_owner_dm_gets_windows(scoped, near_a):
    assert _for(scoped, _sess(OWNER)) == NEAR


def test_admin_dm_gets_windows(scoped, near_a):
    assert _for(scoped, _sess(ADMIN)) == NEAR


def test_plain_user_dm_gets_nothing(scoped, near_a):
    assert _for(scoped, _sess(USER)) is None


def test_read_only_dm_gets_nothing(scoped, near_a):
    assert _for(scoped, _sess(READER)) is None


def test_group_session_gets_nothing_even_for_owner(scoped, near_a):
    assert _for(scoped, _sess(GROUP)) is None


def test_chat_not_in_any_scope_gets_nothing(scoped, near_a):
    """Error guessing: a stranger's positive chat id is not a reader."""
    assert _for(scoped, _sess(123456789)) is None


def test_unstamped_scope_session_lands_in_owners_dm_and_gets_windows(scoped, near_a,
                                                                    monkeypatch):
    """Error guessing: a scope-mode session with no chat stamped is delivered
    to the owner's DM (whatever CHAT_ID says), so the owner may see it."""
    monkeypatch.setattr(config, "CHAT_ID", str(GROUP))
    assert _for(scoped, _sess(0)) == NEAR


def test_access_is_judged_per_call(mk_bot, near_a):
    """A role change takes effect at the next check (design.md section 3)."""
    bot = mk_bot(scopes=scopes())
    sess = _sess(ADMIN)
    assert _for(bot, sess) == NEAR
    demoted = [s for s in scopes() if s.chat_id != ADMIN]
    demoted.append(Scope(chat_id=ADMIN, kind="dm", label="dave", members=(
        Member(id=ADMIN, label="dave", role="user"),)))
    bot.scopes = demoted
    assert _for(bot, sess) is None


# ---- personal mode and legacy team ------------------------------------------------------

def test_personal_mode_operator_dm(mk_bot, monkeypatch, near_a):
    monkeypatch.setattr(config, "CHAT_ID", str(OWNER))
    assert _for(mk_bot(), _sess(None)) == NEAR


def test_personal_mode_operator_dm_with_explicit_chat(mk_bot, monkeypatch, near_a):
    monkeypatch.setattr(config, "CHAT_ID", str(OWNER))
    assert _for(mk_bot(), _sess(OWNER)) == NEAR


def test_personal_mode_group_chat_id(mk_bot, monkeypatch, near_a):
    monkeypatch.setattr(config, "CHAT_ID", str(GROUP))
    assert _for(mk_bot(), _sess(None)) is None


def test_legacy_team_group_gets_nothing(mk_bot, monkeypatch, near_a):
    team = team_mod.Team(group_id=GROUP, users={
        OWNER: team_mod.User(OWNER, "alice", team_mod.Role.ADMIN)})
    monkeypatch.setattr(config, "CHAT_ID", str(GROUP))
    assert _for(mk_bot(team=team), _sess(GROUP)) is None


# ---- whose numbers ----------------------------------------------------------------------

def test_other_login_session_never_shows_newest_reading(scoped):
    usage_store.note_plan("backend", {"seven_day": Window(99, FAR)}, LOGIN_B, scope=OWNER,
                          now=NOW)
    assert _for(scoped, _sess(OWNER, login=LOGIN_A)) is None


def test_other_login_older_reading_is_the_one_shown(scoped):
    usage_store.note_plan("frontend", NEAR, LOGIN_A, scope=OWNER, now=NOW - 600)
    usage_store.note_plan("backend", {"seven_day": Window(99, FAR)}, LOGIN_B, scope=OWNER,
                          now=NOW)
    assert _for(scoped, _sess(OWNER, login=LOGIN_A)) == NEAR


def test_unknown_key_shows_nothing(scoped):
    usage_store.note_plan("frontend", NEAR, UNKNOWN, scope=OWNER, now=NOW)
    sess = _sess(OWNER, login=None)
    sess.usage_plan_key = "unknown"
    assert _for(scoped, sess) is None


def test_empty_key_shows_nothing(scoped, near_a):
    assert _for(scoped, _sess(OWNER, login=None)) is None


def test_stale_reading_shows_nothing(scoped):
    usage_store.note_plan("frontend", NEAR, LOGIN_A, scope=OWNER, now=NOW - 9 * DAY)
    assert _for(scoped, _sess(OWNER)) is None


def test_reading_from_a_group_session_still_shows_in_owner_dm(scoped):
    """The limits are the sign-in's, not the chat's: a reading taken in a group
    session of the same sign-in shows on the owner's DM card."""
    usage_store.note_plan("team-work", NEAR, LOGIN_A, scope=GROUP, now=NOW)
    assert _for(scoped, _sess(OWNER)) == NEAR


# ---- never raises, no Telegram request --------------------------------------------------

def test_access_error_fails_closed(scoped, near_a, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("scope table broken")
    monkeypatch.setattr(usage_cmd, "usage_access", boom)
    assert _for(scoped, _sess(OWNER)) is None


def test_store_error_fails_closed(scoped, near_a, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("store down")
    monkeypatch.setattr(usage_store, "plan_windows", boom)
    assert _for(scoped, _sess(OWNER)) is None


def test_garbage_session_never_raises(scoped, near_a):
    assert _for(scoped, SimpleNamespace()) is None


def test_garbage_chat_id_never_raises(scoped, near_a):
    sess = _sess(OWNER)
    sess.scope_chat_id = "not-a-chat"
    assert _for(scoped, sess) is None


def test_windows_for_sends_nothing_to_telegram(scoped, near_a):
    _for(scoped, _sess(OWNER))
    assert scoped._app.bot.method_calls == []


def test_windows_for_default_now_uses_the_clock(scoped):
    """``now`` is optional; a fresh reading is found with the real clock."""
    later = int(time.time()) + 2 * DAY
    usage_store.note_plan("frontend", {"seven_day": Window(95, later)}, LOGIN_A, scope=OWNER)
    assert usage_note.windows_for(scoped, _sess(OWNER)) == {"seven_day": Window(95, later)}
