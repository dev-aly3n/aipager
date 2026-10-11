"""Fix iteration 2 of the three-modes ship.

- An "already in" answer to a narrower request records it, on every way
  in (typed /mode, the /mode card's tap, the Ready card, the Mini App
  through the shared core), so a later restart never launches the wider
  mode (review rev-iter1-001). It never widens.
- `/mode full ask` and `/mode ask full` reach a live session named `full`
  (design Decision 10, any word order); `/mode full` alone is the mode.
- The /new conflict card's Replace re-checks Full access at the tap
  (tester-iter1-005).
- The legacy `resume_mode_ask` / `resume_mode_auto` / `resume_mode_cancel`
  buttons on cards from before Full access existed (tester-iter1-005).

Real group scope with real roles, as in test_tm3_admin_gate.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aipager.state import SessionRegistry, Status

from _tm3 import (BACKTAB, BOB, DM_CHAT, GROUP, OWNER, RO, answers, dm_bot,
                  quiet_restart, reply_buttons, reply_texts, scoped_bot, settle_tasks, tap,
                  typed)

S, LABEL = "claude-api__g1001", "api"
SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def world(mk_bot, pty, monkeypatch):
    quiet_restart(monkeypatch)

    def _mk(mode="auto", status=Status.IDLE, reported=""):
        reg = SessionRegistry()
        bot = scoped_bot(mk_bot, reg)
        s = reg.get_or_create(S)
        s.label = LABEL
        s.scope_chat_id, s.scope_kind = GROUP, "group"
        s.status, s.perm_mode = status, mode
        s.reported_mode = reported
        s.claude_session_id = SID
        s.cwd = "/home/aly/proj"
        return bot, s
    return _mk


def _say(bot, run_async, text, user):
    u = typed(text, chat_id=GROUP, user_id=user, chat_type="group")

    async def _go():
        await bot._handle_mode_cmd(u, MagicMock())
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


# ---- "already" to a narrower request is recorded ---------------------------

@pytest.mark.parametrize("launched", ["auto", "full"], ids=["auto-launch", "full-launch"])
def test_typed_mode_ask_already_in_ask_records_it(world, run_async, pty, launched):
    # Launched wider, switched to Manual in the terminal: "already in Ask
    # mode", and a restart now launches Ask, never the wider mode.
    bot, s = world(launched, reported="default")
    u = _say(bot, run_async, f"/mode {LABEL} ask", BOB)
    assert "already" in "\n".join(reply_texts(u)).lower()
    assert s.perm_mode == "ask"
    assert pty.runs == [] and pty.killed == [] and pty.launched == []


def test_typed_mode_auto_already_in_auto_never_widens(world, run_async, pty):
    # Launched in Ask, switched to Auto in the terminal (or a forged
    # report): "already", but the launch flag stays Ask.
    bot, s = world("ask", reported="auto")
    u = _say(bot, run_async, f"/mode {LABEL} auto", BOB)
    assert "already" in "\n".join(reply_texts(u)).lower()
    assert s.perm_mode == "ask"


def test_mode_card_ask_tap_already_in_ask_records_it(world, run_async, pty):
    bot, s = world("auto")
    card = _say(bot, run_async, f"/mode {LABEL}", BOB)
    cb = next(cb for _t, cb in reply_buttons(card)
              if cb and cb.rsplit(":", 1)[-1].startswith("modeask"))
    s.reported_mode = "default"   # the terminal switched it meanwhile
    _tap(bot, run_async, cb, BOB)
    assert s.perm_mode == "ask"
    assert pty.runs == []


def test_ready_card_ask_tap_already_in_ask_records_it(world, run_async, pty):
    bot, s = world("auto", reported="default")
    _tap(bot, run_async, f"{S}:rdy_ask", BOB)
    assert s.perm_mode == "ask"
    assert pty.runs == []


def test_a_restart_after_already_launches_the_narrower_mode(world, run_async, pty):
    bot, s = world("auto", reported="default")
    _say(bot, run_async, f"/mode {LABEL} ask", BOB)
    run_async(bot._restart_session_core(s))
    assert pty.launched_modes == ["ask"]


# ---- /mode with two mode words: the live session is the name ---------------

def _dm_two(mk_bot, name):
    reg = SessionRegistry()
    bot = dm_bot(mk_bot, reg)
    for n, mode in ((name, "auto"), ("ben", "ask")):
        s = reg.get_or_create(f"claude-{n}")
        s.label, s.status, s.perm_mode = n, Status.IDLE, mode
        s.claude_session_id = SID
    reg.last_active_session = "claude-ben"
    return bot


@pytest.mark.parametrize("text", ["/mode full ask", "/mode ask full"],
                         ids=["mode-last", "mode-first"])
def test_two_mode_words_switch_the_session_named_full(mk_bot, run_async, pty, text):
    bot = _dm_two(mk_bot, "full")
    u = typed(text, chat_id=DM_CHAT, user_id=OWNER)

    async def _go():
        await bot._handle_mode_cmd(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    assert pty.presses == [BACKTAB]           # Auto -> Ask: one press
    assert bot.registry.get("claude-full").perm_mode == "ask"
    assert bot.registry.get("claude-ben").perm_mode == "ask"


def test_mode_full_alone_is_still_the_mode(mk_bot, run_async, pty):
    bot = _dm_two(mk_bot, "full")
    u = typed("/mode full", chat_id=DM_CHAT, user_id=OWNER)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    # The current session (ben) gets the Full access confirm.
    assert any(cb and cb.endswith("perms_confirm") for _t, cb in reply_buttons(u))
    assert "ben" in "\n".join(reply_texts(u))


# ---- /new conflict: Replace re-checks Full access at the tap ----------------

def _conflict(world, author, mode="full"):
    bot, s = world("ask", status=Status.GONE)
    bot._new_conflict_pending[S] = {"prompt": "", "mode": mode, "user_id": author,
                                    "msg_id": 5}
    return bot, s


def test_replace_by_a_member_not_an_admin_starts_auto(world, run_async, pty):
    # The card asked for Full access (its author was an admin then, or it
    # was stashed from the chat's default); the author is a user now.
    bot, _s = _conflict(world, BOB)
    _tap(bot, run_async, f"{S}:new_replace", BOB)
    assert pty.launched_modes == ["auto"]


def test_replace_by_an_admin_keeps_full_access(world, run_async, pty):
    bot, _s = _conflict(world, OWNER)
    _tap(bot, run_async, f"{S}:new_replace", OWNER)
    assert pty.launched_modes == ["full"]


# ---- legacy resume_mode_* buttons -------------------------------------------

def _gone(world, saved="full"):
    bot, s = world(saved, status=Status.GONE)
    bot._resume_mode_pending[S] = LABEL
    return bot, s


@pytest.mark.parametrize("verb, launched", [("resume_mode_ask", "ask"),
                                            ("resume_mode_auto", "auto")],
                         ids=["ask", "auto"])
def test_legacy_resume_buttons_never_resume_wider(world, run_async, pty, verb, launched):
    # A session saved in Full access, resumed by a user from an old card:
    # the old Ask is Ask, the old Auto is the new Auto, never Full access.
    bot, _s = _gone(world)
    _tap(bot, run_async, f"{S}:{verb}", BOB)
    assert pty.launched_modes == [launched]


def test_legacy_resume_cancel_launches_nothing(world, run_async, pty):
    bot, _s = _gone(world)
    q = _tap(bot, run_async, f"{S}:resume_mode_cancel", BOB)
    assert pty.launched == []
    texts = [str(c.args[0] if c.args else c.kwargs.get("text"))
             for c in q.edit_message_text.await_args_list]
    assert any("Cancelled" in t for t in texts), texts
    assert S not in bot._resume_mode_pending


def test_legacy_resume_ask_needs_someone_who_may_prompt(world, run_async, pty):
    bot, _s = _gone(world)
    q = _tap(bot, run_async, f"{S}:resume_mode_ask", RO)
    assert pty.launched == []
    assert any(a for a in answers(q))


def test_legacy_auto_button_is_auto_even_for_an_admin(world, run_async, pty):
    # The old Auto meant bypass; on an old card it is now the new Auto,
    # also for an admin, who would be allowed Full access (so the admin
    # gate further down cannot be what keeps it narrow).
    bot, _s = _gone(world)
    _tap(bot, run_async, f"{S}:resume_mode_auto", OWNER)
    assert pty.launched_modes == ["auto"]
