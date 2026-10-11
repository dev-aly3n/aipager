"""Iteration 2: in scope mode, a launch surface that knows no chat (chat id
None or 0) keeps the owner and the admin on today's rule (no root), while
a member without can_manage fails closed (review-1 rev-iter1-005).

Driven through ``bot.create_session`` (the one launch seam every /new,
Mini App create and Replace goes through); the PTY seams are recorded."""

from __future__ import annotations

import sys
import pytest

kit = sys.modules["_userroot_kit"]
OWNER, ADMIN, BOB, CY, HAL, SARA = kit.OWNER, kit.ADMIN, kit.BOB, kit.CY, kit.HAL, kit.SARA

NO_CHAT = [None, 0]
NO_CHAT_IDS = ["none", "zero"]
MANAGERS = [OWNER, ADMIN]
MANAGER_IDS = ["owner", "admin"]
RESTRICTED = [BOB, CY, HAL, SARA]
RESTRICTED_IDS = ["user-in-group", "read_only", "custom-no-manage", "user-dm"]


@pytest.fixture
def outside(home):
    p = home / "aipager"
    p.mkdir()
    return p


@pytest.fixture
def inside(team_root):
    p = team_root / "proj"
    p.mkdir(parents=True)
    return p


# ---- create_session ---------------------------------------------------------------

@pytest.mark.parametrize("chat", NO_CHAT, ids=NO_CHAT_IDS)
@pytest.mark.parametrize("who", MANAGERS, ids=MANAGER_IDS)
def test_manager_without_chat_starts_outside_any_root(gbot, run_async, daemon_in_home,
                                                      seams, outside, who, chat):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(outside),
                                 driver_user_id=who))
    seams.launch.assert_awaited_once()


@pytest.mark.parametrize("chat", NO_CHAT, ids=NO_CHAT_IDS)
@pytest.mark.parametrize("who", MANAGERS, ids=MANAGER_IDS)
def test_manager_without_chat_gets_no_error(gbot, run_async, daemon_in_home, seams,
                                            outside, who, chat):
    bot = gbot()
    _name, err = run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(outside),
                                              driver_user_id=who))
    assert err == ""


@pytest.mark.parametrize("chat", NO_CHAT, ids=NO_CHAT_IDS)
@pytest.mark.parametrize("who", MANAGERS, ids=MANAGER_IDS)
def test_manager_without_chat_creates_no_member_folder(gbot, run_async, daemon_in_home,
                                                       seams, outside, home, who, chat):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(outside),
                                 driver_user_id=who))
    assert not (home / "aipager-users").exists()


@pytest.mark.parametrize("chat", NO_CHAT, ids=NO_CHAT_IDS)
@pytest.mark.parametrize("who", RESTRICTED, ids=RESTRICTED_IDS)
def test_restricted_without_chat_fails_closed_outside(gbot, run_async, daemon_in_home,
                                                      seams, outside, who, chat):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(outside),
                                 driver_user_id=who))
    seams.launch.assert_not_awaited()


@pytest.mark.parametrize("chat", NO_CHAT, ids=NO_CHAT_IDS)
def test_restricted_without_chat_fails_closed_even_inside_its_group_root(
        gbot, run_async, daemon_in_home, seams, inside, chat):
    """With no chat, there is no root to be inside: nothing starts."""
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(inside),
                                 driver_user_id=BOB))
    seams.launch.assert_not_awaited()


@pytest.mark.parametrize("chat", NO_CHAT, ids=NO_CHAT_IDS)
def test_restricted_without_chat_gets_an_error(gbot, run_async, daemon_in_home, seams,
                                               outside, chat):
    bot = gbot()
    name, err = run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(outside),
                                             driver_user_id=BOB))
    assert name == "" and err


@pytest.mark.parametrize("chat", NO_CHAT, ids=NO_CHAT_IDS)
def test_unknown_person_without_chat_fails_closed(gbot, run_async, daemon_in_home, seams,
                                                  outside, chat):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=chat, cwd=str(outside),
                                 driver_user_id=4242))
    seams.launch.assert_not_awaited()


# ---- the known-chat rule is untouched ---------------------------------------------

def test_with_a_chat_the_user_is_still_held_to_the_root(gbot, run_async, daemon_in_home,
                                                        seams, outside, scopes):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=kit.GROUP, cwd=str(outside),
                                 driver_user_id=BOB))
    seams.launch.assert_not_awaited()


def test_with_a_chat_the_user_starts_inside_the_root(gbot, run_async, daemon_in_home,
                                                     seams, inside):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=kit.GROUP, cwd=str(inside),
                                 driver_user_id=BOB))
    seams.launch.assert_awaited_once()
