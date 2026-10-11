"""Success criteria 15-17: every switch into or out of Full access restarts
the session (kill + relaunch with the new flag); the confirm card into Full
names the restart and the flag; "Stop task & switch" only for these."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager.state import SessionRegistry, Status

import _tm3
from _tm3 import (DM_CHAT, OWNER, dm_bot, quiet_restart, reply_buttons, reply_texts,
                  settle_tasks, tap, typed)

NAME, LABEL = "claude-ben", "ben"


@pytest.fixture
def world(mk_bot, pty, monkeypatch):
    quiet_restart(monkeypatch)

    def _mk(mode, status=Status.IDLE):
        reg = SessionRegistry()
        bot = dm_bot(mk_bot, reg)
        s = reg.get_or_create(NAME)
        s.label, s.status, s.perm_mode = LABEL, status, mode
        s.claude_session_id = "11111111-2222-3333-4444-555555555555"
        s.cwd = _tm3.group_root()
        reg.last_active_session = NAME
        return bot, s
    return _mk


def _mode(bot, run_async, text):
    u = typed(text, chat_id=DM_CHAT, user_id=OWNER)

    async def _go():
        await bot._handle_mode_cmd(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    return u


def _press(bot, run_async, u, verb):
    cb = next(cb for _t, cb in reply_buttons(u) if cb and cb.endswith(verb))
    uu, q = tap(cb, chat_id=DM_CHAT, user_id=OWNER, msg_id=u.message.message_id + 1)

    async def _go():
        await bot._handle_callback(uu, MagicMock())
        await settle_tasks()
    run_async(_go())
    return q


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_into_full_access_shows_a_confirm_card(world, run_async, pty, start):
    bot, _ = world(start)
    u = _mode(bot, run_async, "/mode full")
    assert any(cb and cb.endswith("perms_confirm") for _t, cb in reply_buttons(u))


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_confirm_card_names_the_flag(world, run_async, pty, start):
    bot, _ = world(start)
    u = _mode(bot, run_async, "/mode full")
    assert "--dangerously-skip-permissions" in "\n".join(reply_texts(u))


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_confirm_card_names_the_restart(world, run_async, pty, start):
    bot, _ = world(start)
    u = _mode(bot, run_async, "/mode full")
    assert "restart" in "\n".join(reply_texts(u)).lower()


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_into_full_access_does_nothing_before_the_confirm(world, run_async, pty, start):
    bot, _ = world(start)
    _mode(bot, run_async, "/mode full")
    assert (pty.killed, pty.launched, pty.runs) == ([], [], [])


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_confirmed_into_full_access_relaunches_with_full(world, run_async, pty, start):
    bot, _ = world(start)
    u = _mode(bot, run_async, "/mode full")
    _press(bot, run_async, u, "perms_confirm")
    assert pty.launched_modes == ["full"]


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_confirmed_into_full_access_kills_the_old_process(world, run_async, pty, start):
    bot, _ = world(start)
    u = _mode(bot, run_async, "/mode full")
    _press(bot, run_async, u, "perms_confirm")
    assert pty.killed == [NAME]


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_confirmed_into_full_access_resumes_the_conversation(world, run_async, pty, start):
    bot, _ = world(start)
    u = _mode(bot, run_async, "/mode full")
    _press(bot, run_async, u, "perms_confirm")
    assert pty.launched[0][1].get("resume_id") == "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("start", ["ask", "auto"], ids=["from-ask", "from-auto"])
def test_into_full_access_presses_no_shift_tab(world, run_async, pty, start):
    bot, _ = world(start)
    u = _mode(bot, run_async, "/mode full")
    _press(bot, run_async, u, "perms_confirm")
    assert b"\x1b[Z" not in pty.stdin_writes


@pytest.mark.parametrize("target", ["ask", "auto"], ids=["to-ask", "to-auto"])
def test_out_of_full_access_relaunches_with_the_target(world, run_async, pty, target):
    bot, s = world("full")
    _mode(bot, run_async, f"/mode {target}")
    assert pty.launched_modes == [target]


@pytest.mark.parametrize("target", ["ask", "auto"], ids=["to-ask", "to-auto"])
def test_out_of_full_access_kills_first(world, run_async, pty, target):
    bot, s = world("full")
    _mode(bot, run_async, f"/mode {target}")
    assert pty.killed == [NAME]


@pytest.mark.parametrize("target", ["ask", "auto"], ids=["to-ask", "to-auto"])
def test_out_of_full_access_records_the_target(world, run_async, pty, target):
    bot, s = world("full")
    _mode(bot, run_async, f"/mode {target}")
    assert s.perm_mode == target


@pytest.mark.parametrize("target", ["ask", "auto"], ids=["to-ask", "to-auto"])
def test_out_of_full_access_presses_no_shift_tab(world, run_async, pty, target):
    bot, s = world("full")
    _mode(bot, run_async, f"/mode {target}")
    assert pty.runs == [] or b"\x1b[Z" not in pty.stdin_writes


def test_out_of_full_access_says_it_restarts(world, run_async, pty):
    bot, s = world("full")
    u = _mode(bot, run_async, "/mode ask")
    said = "\n".join(reply_texts(u)) + "\n" + "\n".join(
        str(c.kwargs.get("text", "")) for c in bot._app.bot.edit_message_text.await_args_list)
    assert "restart" in said.lower(), said


# ---- busy ------------------------------------------------------------------

@pytest.mark.parametrize("start,target", [("ask", "full"), ("auto", "full"),
                                          ("full", "ask"), ("full", "auto")],
                         ids=["ask-to-full", "auto-to-full", "full-to-ask", "full-to-auto"])
def test_busy_full_access_switch_offers_stop_and_switch(world, run_async, pty, start, target):
    bot, _ = world(start, Status.BUSY)
    u = _mode(bot, run_async, f"/mode {target}")
    assert any(cb and cb.endswith("perms_stop_switch") for _t, cb in reply_buttons(u))


@pytest.mark.parametrize("start,target", [("ask", "full"), ("full", "ask")],
                         ids=["ask-to-full", "full-to-ask"])
def test_busy_full_access_switch_waits_for_the_tap(world, run_async, pty, start, target):
    bot, _ = world(start, Status.BUSY)
    _mode(bot, run_async, f"/mode {target}")
    assert (pty.killed, pty.launched) == ([], [])


@pytest.mark.parametrize("start,target", [("ask", "full"), ("full", "auto")],
                         ids=["ask-to-full", "full-to-auto"])
def test_stop_and_switch_relaunches_with_the_target(world, run_async, pty, start, target):
    bot, _ = world(start, Status.BUSY)
    u = _mode(bot, run_async, f"/mode {target}")
    _press(bot, run_async, u, "perms_stop_switch")
    assert pty.launched_modes == [target]
