"""Iteration-3 rulings of the three-modes ship (black-box).

(a) A session launched in Full access, whose parent hooks report a
    narrower mode (Claude Code's ``default`` or ``auto``), is not answered
    "already" when someone asks for that narrower mode: it restarts into
    the target with the target's flag, so the bypass flag is dropped. No
    Shift+Tab ever reaches the Full access process, and nothing after it
    launches with ``--dangerously-skip-permissions``.
(b) Any session whose parent hooks reported ``bypassPermissions`` since
    its launch restarts for every mode switch instead of pressing in
    place. This resets on relaunch. A forged bypass report can only turn a
    switch into a restart; it never widens the launch flag and never lets
    a non-admin into Full access.

Driven through /mode (typed and the card's taps), the Ready card, the Mini
App perms route, /restart and the hook receiver. Group cases use a real
scope with real roles (bob is a plain user, not an admin)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipager import perm_modes
from aipager.dtach import inject
from aipager.dtach.hook_receiver import HookReceiver
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

import _tm3
from _tm3 import (BACKTAB, BOB, BOT_TOKEN, DM_CHAT, FLAGS, GROUP, OWNER, dm_bot, hdr, hook,
                  mode_only_notify, prepare_bot, quiet_restart, reply_buttons, reply_texts,
                  scoped_bot, settle_tasks, tap, typed)
from test_tm3_already_narrower import Argv
from test_tm3_already_narrower import World as ArgvWorld

S, LABEL = "claude-api__g1001", "api"
SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def no_settle(monkeypatch):
    """A report right after a launch or a switch counts at once."""
    monkeypatch.setattr(perm_modes, "MODE_SETTLE_SECONDS", 0.0)


# ---- a group session with a plain user (Pty-level process boundary) ---------

class Group:
    def __init__(self, mk_bot, run_async, *, mode, status=Status.IDLE):
        self.run = run_async
        self.registry = SessionRegistry()
        self.bot = scoped_bot(mk_bot, self.registry)
        s = self.registry.get_or_create(S)
        s.label = LABEL
        s.scope_chat_id, s.scope_kind = GROUP, "group"
        s.status, s.perm_mode = status, mode
        s.claude_session_id = SID
        s.cwd = _tm3.group_root()
        self.sess = s
        self.recv = HookReceiver(self.registry, mode_only_notify(self.bot))

    def report(self, raw, **kw):
        async def _go():
            await self.recv._on_datagram(hook(S, mode=raw, **kw))
            await settle_tasks()
        self.run(_go())

    def say(self, text, user=BOB):
        u = typed(text, chat_id=GROUP, user_id=user, chat_type="group")

        async def _go():
            await self.bot._handle_mode_cmd(u, MagicMock())
            await settle_tasks()
        self.run(_go())
        return u

    def tap(self, data, user=BOB, msg_id=50):
        u, q = tap(data, chat_id=GROUP, user_id=user, msg_id=msg_id)

        async def _go():
            await self.bot._handle_callback(u, MagicMock())
            await settle_tasks()
        self.run(_go())
        return q

    def card_cb(self, verb_prefix, user=BOB):
        u = self.say(f"/mode {LABEL}", user)
        return next(cb for _t, cb in reply_buttons(u)
                    if cb and cb.rsplit(":", 1)[-1].startswith(verb_prefix))


@pytest.fixture
def group(mk_bot, run_async, pty, monkeypatch):
    quiet_restart(monkeypatch)

    def _mk(mode, status=Status.IDLE):
        return Group(mk_bot, run_async, mode=mode, status=status)
    return _mk


# ============================================================================
# (a) Full access launch, Claude Code reports a narrower mode
# ============================================================================

NARROWER = [
    pytest.param("default", "ask", id="reported-default-ask"),
    pytest.param("auto", "auto", id="reported-auto-auto"),
]


@pytest.mark.parametrize("raw, target", NARROWER)
def test_a_user_asking_the_reported_narrower_mode_is_not_told_already(group, pty, raw, target):
    g = group("full")
    g.report(raw)
    u = g.say(f"/mode {LABEL} {target}")
    assert "already" not in "\n".join(reply_texts(u)).lower(), reply_texts(u)


@pytest.mark.parametrize("raw, target", NARROWER)
def test_a_user_asking_the_reported_narrower_mode_relaunches_in_it(group, pty, raw, target):
    g = group("full")
    g.report(raw)
    g.say(f"/mode {LABEL} {target}")
    assert pty.launched_modes == [target]


@pytest.mark.parametrize("raw, target", NARROWER)
def test_a_user_asking_the_reported_narrower_mode_kills_the_full_process(group, pty, raw,
                                                                        target):
    g = group("full")
    g.report(raw)
    g.say(f"/mode {LABEL} {target}")
    assert pty.killed == [S]


@pytest.mark.parametrize("raw, target", NARROWER)
def test_a_user_asking_the_reported_narrower_mode_presses_nothing(group, pty, raw, target):
    g = group("full")
    g.report(raw)
    g.say(f"/mode {LABEL} {target}")
    assert pty.presses == []


@pytest.mark.parametrize("raw, target", NARROWER)
def test_a_user_asking_the_reported_narrower_mode_records_it(group, pty, raw, target):
    g = group("full")
    g.report(raw)
    g.say(f"/mode {LABEL} {target}")
    assert g.sess.perm_mode == target


@pytest.mark.parametrize("raw, verb, target", [
    pytest.param("default", "modeauto", "auto", id="reported-default-tap-auto"),
    pytest.param("auto", "modeask", "ask", id="reported-auto-tap-ask"),
])
def test_the_mode_card_on_a_full_process_reported_narrower_relaunches(group, pty, raw, verb,
                                                                     target):
    """The /mode card shows the reported mode and offers the other two. A
    tap on either Ask or Auto, seen from a Full access process, is a
    restart into it, never presses into the bypass cycle."""
    g = group("full")
    g.report(raw)
    g.tap(g.card_cb(verb))
    assert (pty.launched_modes, pty.presses) == ([target], [])


def test_reported_auto_on_full_then_ask_still_restarts(group, pty):
    """A Full access process cycled to Auto: a request for Ask is a
    restart into Ask, not one press from Auto."""
    g = group("full")
    g.report("auto")
    g.say(f"/mode {LABEL} ask")
    assert (pty.launched_modes, pty.presses) == (["ask"], [])


def test_busy_full_session_reported_narrower_offers_stop_and_switch(group, pty):
    """Busy: the narrower request on a Full access process takes the
    restart path (Stop task & switch), not "already" and not a press."""
    g = group("full", Status.BUSY)
    g.report("default")
    u = g.say(f"/mode {LABEL} ask")
    assert any(cb and cb.endswith("perms_stop_switch") for _t, cb in reply_buttons(u)), \
        reply_buttons(u)


def test_busy_full_session_reported_narrower_waits_for_the_tap(group, pty):
    g = group("full", Status.BUSY)
    g.report("default")
    g.say(f"/mode {LABEL} ask")
    assert (pty.launched, pty.killed, pty.presses) == ([], [], [])


def test_full_reported_default_ask_then_auto_never_relaunches_full(group, pty, no_settle):
    g = group("full")
    g.report("default")
    g.say(f"/mode {LABEL} ask")
    g.say(f"/mode {LABEL} auto")
    assert "full" not in pty.launched_modes, pty.launched_modes


def test_full_reported_default_ask_then_auto_presses_only_after_the_relaunch(group, pty,
                                                                           no_settle):
    """Any Shift+Tab for the second request goes to the process launched
    in Ask, never to the Full access one: nothing is pressed before the
    first relaunch."""
    g = group("full")
    g.report("default")
    g.say(f"/mode {LABEL} ask")
    presses_before_relaunch = list(pty.presses)
    g.say(f"/mode {LABEL} auto")
    assert (presses_before_relaunch, pty.launched_modes[:1]) == ([], ["ask"])


def test_full_reported_default_ask_saved_to_the_state_file(group, pty, tmp_state_file):
    g = group("full")
    g.report("default")
    g.say(f"/mode {LABEL} ask")
    g.registry.save()
    rec = json.loads(tmp_state_file.read_text())["sessions"][S]
    assert (rec.get("perm_mode"), rec.get("skip_perms")) == ("ask", False)


# ---- the launched argv (real launch_session, exec captured) -------------------

@pytest.fixture
def argv(monkeypatch, pty, tmp_path):
    quiet_restart(monkeypatch)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    return Argv(monkeypatch, pty, cwd)


@pytest.mark.parametrize("raw, target", NARROWER)
def test_full_reported_narrower_launch_argv_has_no_bypass_flag(mk_bot, run_async, pty, argv,
                                                               raw, target):
    w = ArgvWorld(mk_bot, run_async, argv, launched="full", via_new=False)
    argv.sock["up"] = True
    w.report(raw)
    w.say(f"/mode {target}")
    assert FLAGS["full"] not in " ".join(argv.cmds), argv.cmds


def test_full_reported_default_ask_then_auto_argv_never_bypass(mk_bot, run_async, pty, argv,
                                                               no_settle):
    w = ArgvWorld(mk_bot, run_async, argv, launched="full", via_new=False)
    argv.sock["up"] = True
    w.report("default")
    w.say("/mode ask")
    w.say("/mode auto")
    w.restart()
    assert FLAGS["full"] not in " ".join(argv.cmds), argv.cmds


# ---- the Ready card ----------------------------------------------------------

def _ready_full(mk_bot, run_async):
    """An admin's `/new !x1` (Full access) and its Ready card buttons."""
    bot = dm_bot(mk_bot, SessionRegistry(), admin=True)
    u = typed("/new !x1", chat_id=DM_CHAT, user_id=OWNER)

    async def _go():
        await bot._handle_new_cmd(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    sess = next(s for s in bot.registry.all_sessions().values() if s.label == "x1")
    sess.status = Status.IDLE
    sess.claude_session_id = SID
    card = bot._app.bot.edit_message_text.await_args_list[-1].kwargs
    btns = [(b.text, b.callback_data) for row in card["reply_markup"].inline_keyboard
            for b in row]
    return bot, sess, card, btns


def test_ready_card_ask_on_a_full_session_reported_default_relaunches(mk_bot, run_async, pty,
                                                                      monkeypatch):
    quiet_restart(monkeypatch)
    bot, sess, card, btns = _ready_full(mk_bot, run_async)
    assert pty.launched_modes == ["full"]       # the precondition: launched in Full access
    recv = HookReceiver(bot.registry, mode_only_notify(bot))

    async def _go():
        await recv._on_datagram(hook(sess.name, mode="default"))
        await settle_tasks()
        cb = next(cb for _t, cb in btns if cb and cb.endswith("rdy_ask"))
        u, _q = tap(cb, chat_id=DM_CHAT, user_id=OWNER, msg_id=card.get("message_id", 50))
        await bot._handle_callback(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    assert (pty.launched_modes, pty.presses) == (["full", "ask"], [])


# ---- the Mini App perms route ---------------------------------------------------

CHAT, MEMBER = -100, 777


@pytest.fixture
def server(mk_bot, tmp_path, monkeypatch, pty):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    quiet_restart(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(project))
    registry = SessionRegistry()
    scope = Scope(chat_id=CHAT, kind="group", label="team", root=str(project),
                  members=(Member(id=MEMBER, label="bob", role="user"),))
    bot = prepare_bot(mk_bot(registry, scopes=[scope]))
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    srv = MiniAppServer(bot, registry, port=8765)
    srv._project_dir = str(project)
    srv.recv = HookReceiver(registry, mode_only_notify(bot))
    s = registry.get_or_create("claude-dev__g100")
    s.label, s.scope_chat_id, s.scope_kind = "dev", CHAT, "group"
    s.status, s.claude_session_id, s.cwd = Status.IDLE, SID, str(project)
    srv.sess = s
    return srv


def _perms(server, run_async, *steps):
    """Run steps in order: ("hook", raw[, kw]) or ("perms", mode). Returns
    the (status, body) of every perms call."""
    from aiohttp.test_utils import TestClient, TestServer

    async def _go():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        out = []
        try:
            for step in steps:
                if step[0] == "hook":
                    kw = step[2] if len(step) > 2 else {}
                    await server.recv._on_datagram(hook(server.sess.name, mode=step[1], **kw))
                else:
                    resp = await client.post("/api/sessions/dev/perms", headers=hdr(MEMBER),
                                             json={"mode": step[1]})
                    out.append((resp.status, (await resp.json()).get("status")))
                await settle_tasks()
            return out
        finally:
            await client.close()
    return run_async(_go())


@pytest.mark.parametrize("raw, target", NARROWER)
def test_miniapp_full_reported_narrower_is_switched_not_already(server, run_async, pty,
                                                                raw, target):
    server.sess.perm_mode = "full"
    out = _perms(server, run_async, ("hook", raw), ("perms", target))
    assert out == [(200, "switched")]


@pytest.mark.parametrize("raw, target", NARROWER)
def test_miniapp_full_reported_narrower_relaunches_without_pressing(server, run_async, pty,
                                                                    raw, target):
    server.sess.perm_mode = "full"
    _perms(server, run_async, ("hook", raw), ("perms", target))
    assert (pty.launched_modes, pty.presses) == ([target], [])


def test_miniapp_full_reported_default_ask_then_auto_never_full(server, run_async, pty,
                                                                no_settle):
    server.sess.perm_mode = "full"
    _perms(server, run_async, ("hook", "default"), ("perms", "ask"), ("perms", "auto"))
    assert "full" not in pty.launched_modes, pty.launched_modes


# ============================================================================
# (b) a parent hook reported bypassPermissions since the launch
# ============================================================================

def test_bypass_reported_then_auto_asking_ask_restarts(group, pty):
    """Every switch restarts, including Auto -> Ask (which would otherwise
    be one press)."""
    g = group("ask")
    g.report("bypassPermissions")
    g.report("auto")
    g.say(f"/mode {LABEL} ask")
    assert (pty.launched_modes, pty.presses) == (["ask"], [])


def test_bypass_reported_then_manual_asking_auto_restarts(group, pty):
    g = group("ask")
    g.report("bypassPermissions")
    g.report("default")
    g.say(f"/mode {LABEL} auto")
    assert (pty.launched_modes, pty.presses) == (["auto"], [])


def test_bypass_still_reported_asking_auto_restarts_into_auto(group, pty):
    g = group("ask")
    g.report("bypassPermissions")
    g.say(f"/mode {LABEL} auto")
    assert (pty.launched_modes, pty.presses) == (["auto"], [])


def test_bypass_reported_on_an_auto_session_asking_ask_restarts(group, pty):
    g = group("auto")
    g.report("bypassPermissions")
    g.report("auto")
    g.say(f"/mode {LABEL} ask")
    assert (pty.launched_modes, pty.presses) == (["ask"], [])


def test_bypass_reported_mode_card_tap_restarts(group, pty):
    g = group("ask")
    g.report("bypassPermissions")
    g.report("default")
    g.tap(g.card_cb("modeauto"))
    assert (pty.launched_modes, pty.presses) == (["auto"], [])


def test_forged_bypass_never_lets_a_user_into_full_access(group, pty):
    g = group("ask")
    g.report("bypassPermissions")
    g.say(f"/mode {LABEL} full")
    assert (pty.launched, pty.killed, g.sess.perm_mode) == ([], [], "ask")


def test_forged_bypass_mode_full_tap_by_a_user_changes_nothing(group, pty):
    g = group("ask")
    cb = g.card_cb("modefull", user=OWNER)     # a real button, from the owner's card
    g.report("bypassPermissions")
    g.tap(cb, user=BOB)
    assert (pty.launched, pty.killed, g.sess.perm_mode) == ([], [], "ask")


def test_forged_bypass_restart_of_an_ask_session_never_launches_full(group, pty):
    """The switch restart lands in the asked mode, never wider."""
    g = group("ask")
    g.report("bypassPermissions")
    g.say(f"/mode {LABEL} auto")
    assert "full" not in pty.launched_modes, pty.launched_modes


def test_forged_bypass_then_restart_launches_the_ask_flag(mk_bot, run_async, pty, argv):
    w = ArgvWorld(mk_bot, run_async, argv, launched="ask", via_new=False)
    argv.sock["up"] = True
    w.report("bypassPermissions")
    w.restart()
    assert argv.mode_flags() == [FLAGS["ask"]], argv.cmds


def test_forged_bypass_switch_launch_argv_is_the_target_flag(mk_bot, run_async, pty, argv):
    w = ArgvWorld(mk_bot, run_async, argv, launched="ask", via_new=False)
    argv.sock["up"] = True
    w.report("bypassPermissions")
    w.report("default")
    w.say("/mode auto")
    assert argv.mode_flags() == [FLAGS["auto"]], argv.cmds


# ---- what does not count as a bypass report -------------------------------------

NOT_A_PARENT_REPORT = [
    pytest.param({"agent_id": "a1b2c3"}, id="agent-id"),
    pytest.param({"event": "queue_pickup"}, id="queue-pickup"),
    pytest.param({"event": "safety_blocked"}, id="safety-blocked"),
    pytest.param({"event": "permission_reply_timeout"}, id="permission-reply-timeout"),
]


@pytest.mark.parametrize("kw", NOT_A_PARENT_REPORT)
def test_bypass_outside_a_parent_hook_keeps_the_in_place_switch(group, pty, kw):
    g = group("ask")
    kw = dict(kw)
    event = kw.pop("event", "PostToolUse")

    async def _go():
        await g.recv._on_datagram(hook(S, event, mode="bypassPermissions", **kw))
        await settle_tasks()
    g.run(_go())
    g.report("default")
    g.say(f"/mode {LABEL} auto")
    assert (pty.presses, pty.launched) == ([BACKTAB] * 3, [])


# ---- resets on relaunch -----------------------------------------------------------

def test_after_the_bypass_restart_the_next_switch_is_in_place(group, pty, no_settle):
    g = group("ask")
    g.report("bypassPermissions")
    g.report("default")
    g.say(f"/mode {LABEL} auto")          # restart into Auto
    g.report("auto")                       # the new process reports Auto
    g.say(f"/mode {LABEL} ask")
    assert (pty.launched_modes, pty.presses) == (["auto"], [BACKTAB])


def test_a_restart_clears_the_bypass_report(group, pty, no_settle):
    from aipager.bot import session_parity
    g = group("ask")
    g.report("bypassPermissions")
    u = typed(f"/restart {LABEL}", chat_id=GROUP, user_id=OWNER, chat_type="group")
    g.run(session_parity.handle_restart_cmd(g.bot, u, MagicMock()))
    cb = next(cb for _t, cb in reply_buttons(u) if cb and "restartok" in cb)
    g.tap(cb, user=OWNER, msg_id=u.message.message_id + 1)
    assert pty.launched_modes == ["ask"]   # the precondition: relaunched
    g.report("default")
    g.say(f"/mode {LABEL} auto")
    assert (pty.launched_modes, pty.presses) == (["ask"], [BACKTAB] * 3)


# ---- the Mini App -------------------------------------------------------------------

def test_miniapp_bypass_reported_switch_is_switched_not_sent(server, run_async, pty):
    server.sess.perm_mode = "ask"
    out = _perms(server, run_async, ("hook", "bypassPermissions"), ("hook", "default"),
                 ("perms", "auto"))
    assert out == [(200, "switched")]


def test_miniapp_bypass_reported_switch_presses_nothing(server, run_async, pty):
    server.sess.perm_mode = "ask"
    _perms(server, run_async, ("hook", "bypassPermissions"), ("hook", "default"),
           ("perms", "auto"))
    assert (pty.launched_modes, pty.presses) == (["auto"], [])


def test_miniapp_forged_bypass_never_lets_a_member_into_full(server, run_async, pty):
    server.sess.perm_mode = "ask"
    out = _perms(server, run_async, ("hook", "bypassPermissions"), ("perms", "full"))
    assert (out[0][0], pty.launched, server.sess.perm_mode) == (403, [], "ask")
