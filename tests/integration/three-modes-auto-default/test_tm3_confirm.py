"""Success criteria 9-14: the next main-agent hook's permission_mode
confirms an in-place switch; agent_id hooks, internal events and hooks
inside the settle window never do; a mismatch presses exactly once more,
counted from the reported mode, then tells the truth; a mismatch on a
dialog event keeps the switch pending; a hook never widens perm_mode;
"Auto isn't available here" after an Auto launch that reports Manual."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from aipager import i18n, perm_modes
from aipager.bot import mode_labels
from aipager.dtach.hook_receiver import HookReceiver
from aipager.state import SessionRegistry, Status

from _tm3 import (BACKTAB, DM_CHAT, OWNER, dm_bot, hook, mode_only_notify,
                  quiet_restart, reply_buttons, settle_tasks, tap, typed)

NAME, LABEL = "claude-ben", "ben"
PENDING_LINE = "Waiting for Claude Code to confirm the switch."
NO_AUTO_TEXT = ("Auto mode isn't available for ben (model or plan), "
                "so it stays in Ask mode")
NO_AUTO_LABEL = "💬 Ask mode (Auto isn't available here)"
REPLY_ID = 101   # typed() replies with message_id + 1


@pytest.fixture
def no_settle(monkeypatch):
    monkeypatch.setattr(perm_modes, "MODE_SETTLE_SECONDS", 0.0)


class World:
    def __init__(self, mk_bot, *, mode="ask", status=Status.IDLE):
        self.registry = SessionRegistry()
        self.bot = dm_bot(mk_bot, self.registry)
        s = self.registry.get_or_create(NAME)
        s.label = LABEL
        s.status = status
        s.perm_mode = mode
        s.claude_session_id = "11111111-2222-3333-4444-555555555555"
        s.cwd = "/home/aly/proj"
        self.registry.last_active_session = NAME
        self.sess = s
        self.notify = mode_only_notify(self.bot)
        self.recv = HookReceiver(self.registry, self.notify)

    async def mode(self, text):
        u = typed(text, chat_id=DM_CHAT, user_id=OWNER)
        await self.bot._handle_mode_cmd(u, MagicMock())
        await settle_tasks()
        return u

    async def hook(self, **kw):
        event = kw.pop("event", "PostToolUse")
        await self.recv._on_datagram(hook(NAME, event, **kw))
        await settle_tasks()

    def edits(self):
        return [c.kwargs for c in self.bot._app.bot.edit_message_text.await_args_list]

    def reply_edits(self):
        return [e for e in self.edits() if e.get("message_id") == REPLY_ID]

    def everything_said(self) -> str:
        out = [str(e.get("text", "")) for e in self.edits()]
        for c in self.bot._app.bot.send_message.await_args_list:
            out.append(str(c.kwargs.get("text", "")))
        return "\n".join(out)


def _switch(w, run_async, text, *hooks):
    async def _go():
        await w.mode(text)
        for h in hooks:
            await w.hook(**h)
    run_async(_go())


# ---- confirmation ----------------------------------------------------------

def test_matching_hook_edits_the_reply(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "auto"})
    assert w.reply_edits(), w.edits()


def test_matching_hook_edit_drops_the_waiting_line(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "auto"})
    assert PENDING_LINE not in str(w.reply_edits()[-1].get("text"))


def test_matching_hook_edit_names_the_new_mode(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "auto"})
    assert "🤖 Auto mode" in str(w.reply_edits()[-1].get("text"))


def test_matching_hook_clears_the_pending_record(mk_bot, run_async, pty, no_settle):
    """After the confirmation a later mismatching hook presses nothing."""
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "auto"}, {"mode": "default"})
    assert len(pty.presses) == 3


def test_confirmed_mode_is_shown_as_reported(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "auto"})
    assert w.sess.shown_mode() == ("auto", "auto")


def test_confirmation_of_auto_to_ask(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot, mode="auto")
    _switch(w, run_async, "/mode ask", {"mode": "default"})
    assert "💬 Ask mode" in str(w.reply_edits()[-1].get("text"))


def test_mode_reported_event_fires_only_while_a_switch_is_pending(mk_bot, run_async, pty):
    w = World(mk_bot)
    run_async(w.hook(mode="default"))
    assert [e for e, _c in w.notify.seen if e == "mode_reported"] == []


def test_mode_reported_event_fires_for_a_pending_switch(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "auto"})
    assert [e for e, _c in w.notify.seen if e == "mode_reported"] == ["mode_reported"]


# ---- what never confirms or retries ----------------------------------------

IGNORED = [
    pytest.param({"mode": "auto", "agent_id": "a1b2c3"}, id="agent-id"),
    pytest.param({"mode": "auto", "event": "queue_pickup"}, id="queue-pickup"),
    pytest.param({"mode": "auto", "event": "safety_blocked"}, id="safety-blocked"),
    pytest.param({"mode": "auto", "event": "permission_reply_timeout"},
                 id="permission-reply-timeout"),
]


@pytest.mark.parametrize("h", IGNORED)
def test_ignored_hook_does_not_confirm(mk_bot, run_async, pty, no_settle, h):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", dict(h))
    assert w.sess.shown_mode() == ("auto", "")


@pytest.mark.parametrize("h", IGNORED)
def test_ignored_hook_edits_nothing(mk_bot, run_async, pty, no_settle, h):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", dict(h))
    assert w.reply_edits() == []


MISMATCH_IGNORED = [
    pytest.param({"mode": "default", "agent_id": "a1b2c3"}, id="agent-id"),
    pytest.param({"mode": "default", "event": "queue_pickup"}, id="queue-pickup"),
    pytest.param({"mode": "default", "event": "safety_blocked"}, id="safety-blocked"),
    pytest.param({"mode": "default", "event": "permission_reply_timeout"},
                 id="permission-reply-timeout"),
]


@pytest.mark.parametrize("h", MISMATCH_IGNORED)
def test_ignored_mismatching_hook_presses_nothing(mk_bot, run_async, pty, no_settle, h):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", dict(h))
    assert len(pty.presses) == 3


def test_agent_hook_does_not_change_the_shown_mode(mk_bot, run_async, pty):
    """A subagent's (or the prompt-suggestion helper's) mode is not the
    session's, even with no switch pending."""
    w = World(mk_bot, mode="ask")
    run_async(w.hook(mode="bypassPermissions", agent_id="helper-1"))
    assert w.sess.current_mode() == "ask"


def test_settle_window_constant():
    assert perm_modes.MODE_SETTLE_SECONDS == 1.5


def test_hook_inside_the_settle_window_does_not_confirm(mk_bot, run_async, pty):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "auto"})
    assert w.reply_edits() == []


def test_hook_inside_the_settle_window_does_not_retry(mk_bot, run_async, pty):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"})
    assert len(pty.presses) == 3


def test_hook_inside_the_settle_window_keeps_the_target_shown(mk_bot, run_async, pty):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"})
    assert w.sess.shown_mode() == ("auto", "")


@pytest.mark.parametrize("dt,verdict", [(1.49, "settling"), (1.5, "recorded"),
                                        (1.51, "recorded")],
                         ids=["just-inside", "boundary", "just-outside"])
def test_note_reported_mode_settle_boundary(mk_bot, run_async, pty, dt, verdict):
    """The window is measured from when the presses were sent."""
    import time
    w = World(mk_bot)
    before = time.monotonic()
    run_async(w.mode("/mode auto"))
    # The switch stamped its send time at or after `before`; probing with
    # `before + dt` is therefore never later than `sent_at + dt`.
    after = time.monotonic()
    if verdict == "settling":
        assert w.sess.note_reported_mode("default", before + dt) == "settling"
    else:
        assert w.sess.note_reported_mode("default", after + dt) == "recorded"


@pytest.mark.parametrize("raw", [None, "", 7, "nonsense"],
                         ids=["none", "empty", "non-string", "unknown"])
def test_note_reported_mode_ignores_junk(mk_bot, raw):
    w = World(mk_bot)
    assert w.sess.note_reported_mode(raw, 10.0) == "ignored"


# ---- one retry, then the truth ---------------------------------------------

def test_mismatch_presses_once_more_from_the_reported_mode(mk_bot, run_async, pty,
                                                           no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"})
    assert len(pty.presses) == 3 + 3


def test_mismatch_retry_counts_from_plan(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "plan"})
    assert len(pty.presses) == 3 + 1


def test_mismatch_retry_counts_from_accept_edits(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "acceptEdits"})
    assert len(pty.presses) == 3 + 2


def test_second_mismatch_presses_nothing(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"}, {"mode": "default"})
    assert len(pty.presses) == 6


def test_third_hook_after_the_report_presses_nothing(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"}, {"mode": "default"},
            {"mode": "plan"})
    assert len(pty.presses) == 6


def test_second_mismatch_to_auto_says_auto_is_not_available(mk_bot, run_async, pty,
                                                           no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"}, {"mode": "default"})
    assert NO_AUTO_TEXT in w.everything_said(), w.everything_said()


def test_second_mismatch_to_auto_shows_the_no_auto_label(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"}, {"mode": "default"})
    assert mode_labels.shown_label(i18n.get("en"), w.sess, html=False) == NO_AUTO_LABEL


def test_second_mismatch_to_auto_keeps_perm_mode_auto(mk_bot, run_async, pty, no_settle):
    """perm_mode stays what aipager asked for, so a restart retries Auto."""
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"}, {"mode": "default"})
    assert w.sess.perm_mode == "auto"


def test_second_mismatch_to_ask_names_the_real_mode(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot, mode="auto")
    _switch(w, run_async, "/mode ask", {"mode": "auto"}, {"mode": "auto"})
    said = w.everything_said()
    assert "didn't switch" in said and "🤖 Auto mode" in said, said


def test_mismatch_then_match_confirms(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"}, {"mode": "auto"})
    assert w.sess.shown_mode() == ("auto", "auto")


# ---- dialogs keep the switch pending ---------------------------------------

DIALOG_HOOKS = [
    pytest.param({"mode": "default", "event": "PermissionRequest", "tool_name": "Bash",
                  "tool_input": {"command": "rm -rf x"}}, id="permission-request"),
    pytest.param({"mode": "default", "event": "PreToolUse", "tool_name": "AskUserQuestion",
                  "tool_input": {"questions": [{"question": "Pick?", "options": []}]}},
                 id="ask-user-question"),
    pytest.param({"mode": "default", "event": "Notification",
                  "notification_type": "permission_prompt",
                  "message": "Claude needs your permission to use Bash"},
                 id="permission-prompt"),
]


@pytest.mark.parametrize("h", DIALOG_HOOKS)
def test_mismatch_on_a_dialog_event_presses_nothing(mk_bot, run_async, pty, no_settle, h):
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", dict(h))
    assert len(pty.presses) == 3


@pytest.mark.parametrize("h", DIALOG_HOOKS)
def test_mismatch_on_a_dialog_event_keeps_the_switch_pending(mk_bot, run_async, pty,
                                                             no_settle, h):
    """Pending means the next ordinary mismatch still gets its one retry."""
    w = World(mk_bot)

    async def _go():
        await w.mode("/mode auto")
        await w.hook(**dict(h))
        w.sess.status = Status.BUSY
        await w.hook(mode="default")
    run_async(_go())
    assert len(pty.presses) == 6


def test_mismatch_while_interactive_presses_nothing(mk_bot, run_async, pty, no_settle):
    w = World(mk_bot)

    async def _go():
        await w.mode("/mode auto")
        w.sess.status = Status.INTERACTIVE
        await w.recv._on_datagram(hook(NAME, "PostToolUse", mode="default"))
        await settle_tasks()
    run_async(_go())
    assert len(pty.presses) == 3


# ---- a hook never widens perm_mode ------------------------------------------

def test_forged_bypass_on_an_ask_session_keeps_perm_mode(mk_bot, run_async, pty):
    w = World(mk_bot, mode="ask")
    run_async(w.hook(mode="bypassPermissions"))
    assert w.sess.perm_mode == "ask"


def test_forged_bypass_is_shown_honestly(mk_bot, run_async, pty):
    w = World(mk_bot, mode="ask")
    run_async(w.hook(mode="bypassPermissions"))
    assert w.sess.current_mode() == "full"


def test_forged_bypass_is_logged_as_a_warning(mk_bot, run_async, pty, caplog):
    w = World(mk_bot, mode="ask")
    with caplog.at_level(logging.WARNING):
        run_async(w.hook(mode="bypassPermissions"))
    assert any(r.levelno >= logging.WARNING and "bypass" in r.getMessage().lower()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_bypass_on_a_full_session_is_not_a_warning(mk_bot, run_async, pty, caplog):
    w = World(mk_bot, mode="full")
    with caplog.at_level(logging.WARNING):
        run_async(w.hook(mode="bypassPermissions"))
    assert not any(r.levelno >= logging.WARNING and "bypass" in r.getMessage().lower()
                   for r in caplog.records)


def test_forged_bypass_is_not_persisted(mk_bot, run_async, pty, tmp_state_file):
    import json
    w = World(mk_bot, mode="ask")
    run_async(w.hook(mode="bypassPermissions"))
    w.registry.save()
    rec = json.loads(tmp_state_file.read_text())["sessions"][NAME]
    assert (rec.get("perm_mode"), rec.get("skip_perms")) == ("ask", False)


def test_forged_bypass_then_restart_relaunches_in_ask(mk_bot, run_async, pty, monkeypatch):
    """After a forged bypass report, the next launch (a resume once the
    session ended) still uses Ask's flag."""
    quiet_restart(monkeypatch)
    w = World(mk_bot, mode="ask")
    run_async(w.hook(mode="bypassPermissions"))
    w.sess.status = Status.GONE
    u = typed(f"/resume {LABEL}", chat_id=DM_CHAT, user_id=OWNER)

    async def _go():
        await w.bot._handle_resume_cmd(u, MagicMock())
        await settle_tasks()
        # The resume picker: pick the "(default)" row if one is shown.
        for text, cb in reply_buttons(u):
            if "(default)" in text or "default" in text.lower():
                uu, _q = tap(cb, chat_id=DM_CHAT, user_id=OWNER)
                await w.bot._handle_callback(uu, MagicMock())
                await settle_tasks()
                break
    run_async(_go())
    assert pty.launched_modes == ["ask"], (pty.launched, reply_buttons(u))


@pytest.mark.parametrize("raw", ["auto", "bypassPermissions", "plan"],
                         ids=["auto", "bypass", "plan"])
def test_no_hook_changes_perm_mode_of_an_ask_session(mk_bot, run_async, pty, raw):
    w = World(mk_bot, mode="ask")
    run_async(w.hook(mode=raw))
    assert w.sess.perm_mode == "ask"


# ---- Auto not available ----------------------------------------------------

def _created_in(mk_bot, run_async, pty, mode_word):
    """Create a session through /new (the launch faked), then let it be
    discovered IDLE the way the monitor would."""
    from aipager import preferences
    registry = SessionRegistry()
    bot = dm_bot(mk_bot, registry)
    if mode_word != "auto":
        preferences.set_new_session_default(DM_CHAT, "mode", mode_word)
    u = typed("/new ben", chat_id=DM_CHAT, user_id=OWNER)
    run_async(bot._handle_new_cmd(u, MagicMock()))
    sess = next(s for s in registry.all_sessions().values() if s.label == "ben")
    sess.status = Status.IDLE
    return bot, sess, HookReceiver(registry, mode_only_notify(bot))


def test_auto_launch_reporting_default_shows_auto_not_available(mk_bot, run_async, pty):
    bot, sess, recv = _created_in(mk_bot, run_async, pty, "auto")
    run_async(recv._on_datagram(hook(sess.name, mode="default")))
    assert mode_labels.shown_label(i18n.get("en"), sess, html=False) == NO_AUTO_LABEL


def test_ask_launch_reporting_default_shows_plain_ask(mk_bot, run_async, pty):
    bot, sess, recv = _created_in(mk_bot, run_async, pty, "ask")
    run_async(recv._on_datagram(hook(sess.name, mode="default")))
    assert mode_labels.shown_label(i18n.get("en"), sess, html=False) == "💬 Ask mode"


def test_auto_launch_reporting_auto_shows_auto(mk_bot, run_async, pty):
    bot, sess, recv = _created_in(mk_bot, run_async, pty, "auto")
    run_async(recv._on_datagram(hook(sess.name, mode="auto")))
    assert mode_labels.shown_label(i18n.get("en"), sess, html=False) == "🤖 Auto mode"


def test_only_the_first_hook_after_launch_decides(mk_bot, run_async, pty):
    """A later Manual (a person cycled in the terminal) is plain Ask."""
    bot, sess, recv = _created_in(mk_bot, run_async, pty, "auto")

    async def _go():
        await recv._on_datagram(hook(sess.name, mode="auto"))
        await recv._on_datagram(hook(sess.name, mode="default"))
    run_async(_go())
    assert mode_labels.shown_label(i18n.get("en"), sess, html=False) == "💬 Ask mode"


def test_auto_not_available_uses_the_three_cycle(mk_bot, run_async, pty):
    """With Auto off the cycle is default -> acceptEdits -> plan, so from
    plan to Ask is one press, not two."""
    bot, sess, recv = _created_in(mk_bot, run_async, pty, "auto")

    async def _go():
        await recv._on_datagram(hook(sess.name, mode="default"))
        await recv._on_datagram(hook(sess.name, mode="plan"))
        sess.status = Status.IDLE
        u = typed("/mode ask", chat_id=DM_CHAT, user_id=OWNER)
        await bot._handle_mode_cmd(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    assert pty.presses == [BACKTAB]


def test_a_relaunch_clears_auto_not_available(mk_bot, run_async, pty, monkeypatch):
    """Into Full access and back out to Auto: two relaunches. The session
    is in Auto again, with no stale "not available"."""
    quiet_restart(monkeypatch)
    bot, sess, recv = _created_in(mk_bot, run_async, pty, "auto")
    run_async(recv._on_datagram(hook(sess.name, mode="default")))

    async def _go():
        u = typed("/mode full", chat_id=DM_CHAT, user_id=OWNER)
        await bot._handle_mode_cmd(u, MagicMock())
        await settle_tasks()
        for _text, cb in reply_buttons(u):
            if cb and cb.endswith("perms_confirm"):
                uu, _q = tap(cb, chat_id=DM_CHAT, user_id=OWNER, msg_id=u.message.message_id + 1)
                await bot._handle_callback(uu, MagicMock())
                await settle_tasks()
        sess.status = Status.IDLE
        u2 = typed("/mode auto", chat_id=DM_CHAT, user_id=OWNER)
        await bot._handle_mode_cmd(u2, MagicMock())
        await settle_tasks()
    run_async(_go())
    assert (pty.launched_modes[-2:],
            mode_labels.shown_label(i18n.get("en"), sess, html=False)) == (
        ["full", "auto"], "🤖 Auto mode")


def test_second_mismatch_to_auto_marks_auto_unavailable(mk_bot, run_async, pty, no_settle):
    """The shown id behind the label (the label text has its own test)."""
    w = World(mk_bot)
    _switch(w, run_async, "/mode auto", {"mode": "default"}, {"mode": "default"})
    assert w.sess.shown_mode()[0] == "ask_no_auto"


def test_auto_launch_reporting_default_marks_auto_unavailable(mk_bot, run_async, pty):
    bot, sess, recv = _created_in(mk_bot, run_async, pty, "auto")
    run_async(recv._on_datagram(hook(sess.name, mode="default")))
    assert sess.shown_mode()[0] == "ask_no_auto"


def test_ask_launch_reporting_default_is_plain_ask(mk_bot, run_async, pty):
    bot, sess, recv = _created_in(mk_bot, run_async, pty, "ask")
    run_async(recv._on_datagram(hook(sess.name, mode="default")))
    assert sess.shown_mode()[0] == "ask"
