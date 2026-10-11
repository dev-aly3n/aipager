"""Fix-iteration-2 ruling: an "already" answer to a NARROWER request
records the narrower mode, so every later launch (a /restart, a /resume of
the ended session, `aipager resume` from the shell) uses the narrower flag.

The scenario the ruling names: a session launched in Auto whose first
main-agent hook reports Claude Code's ``default`` (Manual) mode. `/mode
ask` answers "already", presses nothing, and the next launch is
``--permission-mode default``. A session launched in Full access and
switched down in the terminal is restarted for the switch instead (fix
iteration 3: every switch of a Full-launched session is a restart).

Black-box: driven through /new, the hook receiver, /mode, /restart,
/resume, the Mini App perms route and the `aipager resume` CLI. The launch
argv is captured at the process boundary (``inject._create_subprocess_exec``)
with the real ``launch_session`` in between, so the flag asserted is the
one Claude Code would receive."""

from __future__ import annotations

import argparse
import json
import shlex
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n
from aipager.bot import mode_labels
from aipager.dtach import inject
from aipager.dtach.hook_receiver import HookReceiver
from aipager.state import SessionRegistry, Status

from _tm3 import (BACKTAB, DM_CHAT, FLAGS, OWNER, dm_bot, hook, mode_only_notify,
                  quiet_restart, reply_buttons, reply_texts, settle_tasks, tap, typed)

LABEL = "ben"
SID = "e4f739a9-e19a-4d17-a8c2-12ba1b288907"
_REAL_LAUNCH = inject.launch_session


# ---- the process boundary ---------------------------------------------------

class Argv:
    """The real launch_session runs; only the exec of dtach is captured."""

    def __init__(self, monkeypatch, pty, cwd):
        self.cmds: list[str] = []
        self.cwd = cwd
        self.sock = sock = {"up": False}

        async def _exec(*args, **kwargs):
            self.cmds.append(str(args[-1]))
            sock["up"] = True
            p = AsyncMock()
            p.returncode = 0
            p.communicate = AsyncMock(return_value=(b"", b""))
            return p

        async def _kill(name, *a, **k):
            pty.killed.append(name)
            sock["up"] = False
            return True

        async def _launch(name, **kw):
            pty.launched.append((name, kw))
            return await _REAL_LAUNCH(name, **kw)

        monkeypatch.setattr(inject, "_create_subprocess_exec", _exec)
        monkeypatch.setattr(inject, "_conversation_exists", lambda sid: True)
        monkeypatch.setattr(inject.Path, "is_socket", lambda self: sock["up"])
        monkeypatch.setattr(inject, "kill_session", _kill)
        monkeypatch.setattr(inject, "launch_session", _launch)

    def mode_flags(self, i=-1) -> list[str]:
        try:
            toks = shlex.split(self.cmds[i])
        except ValueError:
            toks = self.cmds[i].split()
        out = []
        for k, t in enumerate(toks):
            if t == "--permission-mode":
                out.append(f"--permission-mode {toks[k + 1] if k + 1 < len(toks) else ''}")
            elif t.startswith("--permission-mode="):
                out.append("--permission-mode " + t.split("=", 1)[1])
            elif t == "--dangerously-skip-permissions":
                out.append(t)
        return out


@pytest.fixture
def argv(monkeypatch, pty, tmp_path):
    quiet_restart(monkeypatch)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    return Argv(monkeypatch, pty, cwd)


# ---- the world ----------------------------------------------------------------

class World:
    """A DM (the owner is an admin) with one session `ben`, created
    through /new or seeded in a given launch mode."""

    def __init__(self, mk_bot, run_async, argv, *, launched="auto", via_new=True):
        self.run = run_async
        self.argv = argv
        self.registry = SessionRegistry()
        self.bot = dm_bot(mk_bot, self.registry)
        self.recv = HookReceiver(self.registry, mode_only_notify(self.bot))
        if via_new:
            assert launched == "auto"
            u = typed(f"/new {LABEL}", chat_id=DM_CHAT, user_id=OWNER)
            run_async(self.bot._handle_new_cmd(u, MagicMock()))
            self.sess = next(s for s in self.registry.all_sessions().values()
                             if s.label == LABEL)
        else:
            self.sess = self.registry.get_or_create(f"claude-{LABEL}")
            self.sess.label = LABEL
            self.sess.perm_mode = launched
        self.sess.status = Status.IDLE
        self.sess.claude_session_id = SID
        self.sess.cwd = str(argv.cwd)
        self.registry.last_active_session = self.sess.name

    def report(self, raw):
        async def _go():
            await self.recv._on_datagram(hook(self.sess.name, mode=raw))
            await settle_tasks()
        self.run(_go())

    def say(self, text, handler="_handle_mode_cmd"):
        u = typed(text, chat_id=DM_CHAT, user_id=OWNER)

        async def _go():
            await getattr(self.bot, handler)(u, MagicMock())
            await settle_tasks()
        self.run(_go())
        return u

    def tap(self, data, msg_id=50):
        u, q = tap(data, chat_id=DM_CHAT, user_id=OWNER, msg_id=msg_id)

        async def _go():
            await self.bot._handle_callback(u, MagicMock())
            await settle_tasks()
        self.run(_go())
        return q

    def restart(self):
        from aipager.bot import session_parity
        u = typed(f"/restart {LABEL}", chat_id=DM_CHAT, user_id=OWNER)
        self.run(session_parity.handle_restart_cmd(self.bot, u, MagicMock()))
        cb = next(cb for _t, cb in reply_buttons(u) if cb and "restartok" in cb)
        self.tap(cb, msg_id=u.message.message_id + 1)

    def end(self):
        """The session ended (Claude exited, its dtach socket is gone)."""
        self.sess.status = Status.GONE
        self.argv.sock["up"] = False

    def resume(self):
        """`/resume ben` resumes in the saved mode; if a picker is shown
        instead, take the row marked as the saved one."""
        self.end()
        u = self.say(f"/resume {LABEL}", "_handle_resume_cmd")
        cb = next((cb for t, cb in reply_buttons(u) if "(default)" in t), None)
        if cb is not None:
            self.tap(cb, msg_id=u.message.message_id + 1)
        return u


def _ruling_world(mk_bot, run_async, argv):
    """Launched in Auto through /new; Claude Code reports Manual; /mode ask."""
    w = World(mk_bot, run_async, argv)
    w.report("default")
    w.reply = w.say("/mode ask")
    return w


# ---- the scenario the ruling names --------------------------------------------

def test_the_session_was_launched_in_auto(mk_bot, run_async, pty, argv):
    w = World(mk_bot, run_async, argv)
    assert w.argv.mode_flags(0) == [FLAGS["auto"]]


def test_mode_ask_on_a_reported_manual_session_answers_already(mk_bot, run_async, pty,
                                                               argv):
    w = _ruling_world(mk_bot, run_async, argv)
    assert "already" in "\n".join(reply_texts(w.reply)).lower()


def test_mode_ask_on_a_reported_manual_session_presses_nothing(mk_bot, run_async, pty,
                                                               argv):
    _ruling_world(mk_bot, run_async, argv)
    assert pty.presses == []


def test_mode_ask_on_a_reported_manual_session_does_not_restart(mk_bot, run_async, pty,
                                                                argv):
    _ruling_world(mk_bot, run_async, argv)
    assert (pty.killed, len(argv.cmds)) == ([], 1)


def test_already_ask_is_saved_to_the_state_file(mk_bot, run_async, pty, argv,
                                                tmp_state_file):
    w = _ruling_world(mk_bot, run_async, argv)
    w.registry.save()
    rec = json.loads(tmp_state_file.read_text())["sessions"][w.sess.name]
    assert (rec.get("perm_mode"), rec.get("skip_perms")) == ("ask", False)


def test_already_ask_survives_a_daemon_reload(mk_bot, run_async, pty, argv, tmp_state_file):
    w = _ruling_world(mk_bot, run_async, argv)
    w.registry.save()
    fresh = SessionRegistry()
    fresh.load()
    assert fresh.get(w.sess.name).perm_mode == "ask"



def test_already_ask_is_flushed_by_the_dirty_save(mk_bot, run_async, pty, argv,
                                                  tmp_state_file):
    """The daemon persists through ``save_if_dirty``: the recorded Ask must
    reach the state file on that path too, or a daemon restart before any
    other change would relaunch the wider Auto."""
    w = World(mk_bot, run_async, argv)
    w.report("default")
    w.registry.save()            # everything before the answer is on disk
    w.say("/mode ask")
    w.registry.save_if_dirty()
    rec = json.loads(tmp_state_file.read_text())["sessions"][w.sess.name]
    assert rec.get("perm_mode") == "ask"

def test_restart_after_already_launches_ask(mk_bot, run_async, pty, argv):
    w = _ruling_world(mk_bot, run_async, argv)
    w.restart()
    assert (len(argv.cmds), argv.mode_flags()) == (2, [FLAGS["ask"]]), argv.cmds


def test_restart_after_already_never_launches_auto(mk_bot, run_async, pty, argv):
    w = _ruling_world(mk_bot, run_async, argv)
    w.restart()
    assert FLAGS["auto"] not in " ".join(argv.cmds[1:])


def test_resume_after_already_launches_ask(mk_bot, run_async, pty, argv):
    w = _ruling_world(mk_bot, run_async, argv)
    w.resume()
    assert (len(argv.cmds), argv.mode_flags()) == (2, [FLAGS["ask"]]), argv.cmds


def test_cli_resume_after_already_prints_ask(mk_bot, run_async, pty, argv, tmp_state_file,
                                             monkeypatch, capsys):
    w = _ruling_world(mk_bot, run_async, argv)
    w.end()
    w.registry.save()
    monkeypatch.setattr("aipager.status._live_sessions", lambda: set())
    cli_launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(inject, "launch_session", cli_launch)
    from aipager.cli import resume as cli_resume
    capsys.readouterr()
    cli_resume._cmd_resume(argparse.Namespace(name=LABEL))
    out = capsys.readouterr()
    flat = " ".join((out.out + out.err).split())
    assert "mode=Ask (--permission-mode default)" in flat, flat


def test_cli_resume_after_already_launches_ask(mk_bot, run_async, pty, argv,
                                               tmp_state_file, monkeypatch):
    w = _ruling_world(mk_bot, run_async, argv)
    w.end()
    w.registry.save()
    monkeypatch.setattr("aipager.status._live_sessions", lambda: set())
    cli_launch = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(inject, "launch_session", cli_launch)
    from aipager.cli import resume as cli_resume
    cli_resume._cmd_resume(argparse.Namespace(name=LABEL))
    assert cli_launch.await_args.kwargs.get("mode") == "ask"


def test_already_ask_shows_ask_mode(mk_bot, run_async, pty, argv):
    w = _ruling_world(mk_bot, run_async, argv)
    shown = mode_labels.shown_label(i18n.get("en"), w.sess, html=False)
    assert shown.startswith("💬 Ask mode"), shown


# ---- Full access switched down in the terminal ---------------------------------

@pytest.mark.parametrize("raw, word, flag", [
    ("default", "ask", FLAGS["ask"]),
    ("auto", "auto", FLAGS["auto"]),
], ids=["full-to-ask", "full-to-auto"])
def test_full_session_reported_narrower_restarts_narrower(mk_bot, run_async, pty, argv,
                                                          raw, word, flag):
    w = World(mk_bot, run_async, argv, launched="full", via_new=False)
    w.report(raw)
    u = w.say(f"/mode {word}")
    # Fix iteration 3 (rev-iter2-001): not "already" but a restart into the
    # narrower mode, so the bypass flag is dropped from the live process.
    assert "already" not in "\n".join(reply_texts(u)).lower()
    assert argv.mode_flags() == [flag], argv.cmds
    w.restart()
    assert argv.mode_flags() == [flag], argv.cmds


@pytest.mark.parametrize("raw, word", [("default", "ask"), ("auto", "auto")],
                         ids=["full-to-ask", "full-to-auto"])
def test_full_session_reported_narrower_never_relaunches_full(mk_bot, run_async, pty, argv,
                                                              raw, word):
    w = World(mk_bot, run_async, argv, launched="full", via_new=False)
    w.report(raw)
    w.say(f"/mode {word}")
    w.restart()
    assert FLAGS["full"] not in " ".join(argv.cmds), argv.cmds


# ---- the guard's other side: no "already" without the report -------------------

def test_without_a_manual_report_mode_ask_presses_once(mk_bot, run_async, pty, argv):
    """An Auto session with no report yet: /mode ask is a real switch (one
    Shift+Tab), not an "already". Pins that the narrowing above follows
    the report, not the request alone."""
    w = World(mk_bot, run_async, argv)
    w.say("/mode ask")
    assert pty.presses == [BACKTAB]


def test_agent_reported_manual_is_not_an_already(mk_bot, run_async, pty, argv):
    """A subagent's ``default`` is not the session's mode: /mode ask still
    presses."""
    w = World(mk_bot, run_async, argv)

    async def _go():
        await w.recv._on_datagram(hook(w.sess.name, mode="default", agent_id="a-1"))
        await settle_tasks()
    run_async(_go())
    w.say("/mode ask")
    assert pty.presses == [BACKTAB]


def test_hook_alone_never_narrows_the_saved_mode(mk_bot, run_async, pty, argv):
    """Without anyone asking, a Manual report does not rewrite the launch
    mode: perm_mode stays what aipager launched (so a restart retries
    Auto, as criterion 11 and the Risks section promise)."""
    w = World(mk_bot, run_async, argv)
    w.report("default")
    assert w.sess.perm_mode == "auto"


# ---- the Mini App way in -------------------------------------------------------

def test_miniapp_already_ask_records_ask(mk_bot, run_async, pty, argv, monkeypatch,
                                         tmp_path):
    from pathlib import Path

    from aiohttp.test_utils import TestClient, TestServer

    from aipager.miniapp.server import MiniAppServer
    from aipager.policy import load_policy
    from aipager.scope import Member, Scope

    from _tm3 import BOT_TOKEN, hdr, prepare_bot

    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    project = argv.cwd
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(project))
    chat, member = -100, 777
    registry = SessionRegistry()
    scope = Scope(chat_id=chat, kind="group", label="team", root=str(project),
                  members=(Member(id=member, label="bob", role="user"),))
    bot = prepare_bot(mk_bot(registry, scopes=[scope]))
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    s = registry.get_or_create("claude-dev__g100")
    s.label, s.scope_chat_id, s.scope_kind = "dev", chat, "group"
    s.status, s.perm_mode, s.claude_session_id, s.cwd = Status.IDLE, "auto", SID, str(project)
    recv = HookReceiver(registry, mode_only_notify(bot))
    srv = MiniAppServer(bot, registry, port=8765)
    srv._project_dir = str(project)

    async def _go():
        await recv._on_datagram(hook(s.name, mode="default"))
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.post("/api/sessions/dev/perms", headers=hdr(member),
                                     json={"mode": "ask"})
            data = await resp.json()
            await settle_tasks()
            return resp.status, data.get("status")
        finally:
            await client.close()
    status = run_async(_go())
    assert (status, s.perm_mode, pty.presses) == ((200, "already"), "ask", [])


# ---- "already" never widens -----------------------------------------------------

def test_already_auto_on_an_ask_session_never_restarts_wider(mk_bot, run_async, pty, argv):
    """An Ask session whose hook reports auto (a person cycled it in the
    terminal, or a forged report): /mode auto may answer "already", but
    the next launch is still Ask's flag (criterion 13: a report never
    widens the launch mode)."""
    w = World(mk_bot, run_async, argv, launched="ask", via_new=False)
    w.report("auto")
    w.say("/mode auto")
    w.restart()
    assert argv.mode_flags() == [FLAGS["ask"]], argv.cmds
