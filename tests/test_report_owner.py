"""Problem reports (roadmap 8.112 step 3): who "the owner" is.

One rule, ``report_flow.resolve_owner``, decides it for every surface
(the /help button, every ``_:rp:`` tap, the Mini App row, the memory-cap
button, the automatic offer, the /settings row). Its answer is the
owner's user id, which is also their private chat's id.
"""

from __future__ import annotations

import pytest

from aipager.bot import report_flow
from aipager.scope import Member, Scope
from aipager.team import Role as TeamRole, Team, User as TeamUser

OWNER = 256113222  # tests/conftest.py pins config.CHAT_ID to this


def _dm(chat_id, role="owner", member_id=None):
    return Scope(chat_id=chat_id, kind="dm", label="dm",
                 members=(Member(id=chat_id if member_id is None else member_id,
                                 label="me", role=role),))


def _group(chat_id=-1001):
    return Scope(chat_id=chat_id, kind="group", label="team", members=(
        Member(id=OWNER, label="aly", role="owner"),
        Member(id=7, label="bob", role="admin"),
    ))


def test_personal_mode_owner_is_the_positive_chat_id(mk_bot):
    bot = mk_bot()
    assert report_flow.resolve_owner(bot) == OWNER
    assert report_flow.is_owner(bot, OWNER)
    assert not report_flow.is_owner(bot, OWNER + 1)


@pytest.mark.parametrize("chat_id", ["-100123", "", "abc", "0"])
def test_personal_mode_without_a_positive_chat_id_has_no_owner(mk_bot, monkeypatch, chat_id):
    monkeypatch.setattr("aipager.config.CHAT_ID", chat_id)
    bot = mk_bot()
    assert report_flow.resolve_owner(bot) is None
    assert not report_flow.is_owner(bot, OWNER)


def test_the_chat_id_is_read_at_call_time(mk_bot, monkeypatch):
    bot = mk_bot()
    monkeypatch.setattr("aipager.config.CHAT_ID", "4242")
    assert report_flow.resolve_owner(bot) == 4242


def test_legacy_team_mode_owner_is_the_chat_id(mk_bot):
    team = Team(group_id=-1001, users={
        OWNER: TeamUser(id=OWNER, label="aly", role=TeamRole.ADMIN),
        7: TeamUser(id=7, label="bob", role=TeamRole.ADMIN)})
    bot = mk_bot(team=team)
    assert report_flow.resolve_owner(bot) == OWNER
    assert not report_flow.is_owner(bot, 7)


def test_scope_mode_owner_is_the_owner_dm(mk_bot):
    bot = mk_bot(scopes=[_group(), _dm(OWNER)])
    assert report_flow.resolve_owner(bot) == OWNER
    assert not report_flow.is_owner(bot, 7)


def test_scope_mode_admin_role_self_dm_is_still_the_owner(mk_bot):
    """An operator who chose ``admin`` for their own DM at first run."""
    bot = mk_bot(scopes=[_group(), _dm(555, role="admin")])
    assert report_flow.resolve_owner(bot) == 555


def test_scope_mode_owner_dm_wins_over_another_self_dm(mk_bot):
    bot = mk_bot(scopes=[_dm(555, role="admin"), _dm(OWNER)])
    assert report_flow.resolve_owner(bot) == OWNER


def test_scope_mode_two_owner_dms_is_ambiguous(mk_bot):
    bot = mk_bot(scopes=[_dm(555), _dm(OWNER)])
    assert report_flow.resolve_owner(bot) is None
    assert not report_flow.is_owner(bot, OWNER)


def test_scope_mode_without_a_self_dm_has_no_owner(mk_bot):
    """A DM scope whose member is someone else is not a self-DM, and a
    group is never the owner's chat: personal CHAT_ID is not consulted."""
    bot = mk_bot(scopes=[_group(), _dm(555, member_id=OWNER)])
    assert report_flow.resolve_owner(bot) is None


def test_a_bool_user_id_is_never_the_owner(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.config.CHAT_ID", "1")
    bot = mk_bot()
    assert report_flow.resolve_owner(bot) == 1
    assert report_flow.is_owner(bot, 1)
    assert not report_flow.is_owner(bot, True)
    assert not report_flow.is_owner(bot, None)
    assert not report_flow.is_owner(bot, "1")
