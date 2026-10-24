"""Bringing a session back from a Claude Code background job (B1-B4).

A session is parked when its own claude moved the conversation into a
background job (``←``). ``SessionOpsMixin._bring_back_core`` is the one way
back: find the conversation (the job's, else the parked client's own,
never an empty session), refuse a busy job unless forced, ``claude stop``
the job under the restart window and wait until no process holds the
conversation, then kill the parked client and relaunch the conversation
with aipager's own flags. Restart, a restart-type mode switch, Stop and a
safety halt all go through it, and none of them types into the parked
client.

Claude's files are a fake config folder under tmp_path; ``claude stop``
and ``claude agents --json`` run through ``claude_jobs``'s runner seam, the
waits through ``session_ops._sleep``, the dtach calls through the
``inject`` module attributes. Nothing real is spawned.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_jobs, i18n
from aipager.bot import session_ops
from aipager.bot.session_ops import BG_BACK
from aipager.dtach import bg_jobs
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.session_monitor import SessionMonitor
from aipager.state import Status

CHAT = -1001
NAME = "claude-x"
JOB = "ab12cd34"
#: The client's own conversation (moved into the job by ``←``).
X = "aaaaaaaa-0000-4000-8000-000000000001"
#: The job's conversation (its first 8 characters are the job's id).
JS = "ab12cd34-0000-4000-8000-000000000002"
CLIENT, WORKER = 4100, 4200
FAKE_CLAUDE = "/opt/fake/bin/claude"
SESSION_ENV = {"AIPAGER_TEST_SESSION_ENV": "1"}


class World:
    """A parked session, Claude's files and every seam the core reaches.

    ``calls`` records, in order: ``("run", args)`` for each ``claude``
    command, ``("sleep", seconds)``, ``("kill", name)``, ``("keys", key)``,
    ``("launch", kwargs)``."""

    def __init__(self, mk_bot, monkeypatch, tmp_path):
        self.calls: list[tuple] = []
        self._mtimes = 0
        self.cfg = tmp_path / "claude-config"
        (self.cfg / "sessions").mkdir(parents=True)
        self.projects = self.cfg / "projects" / "-home-alice-app"
        self.projects.mkdir(parents=True)
        monkeypatch.setattr(claude_jobs, "config_dir", lambda: self.cfg)
        self.alive = {CLIENT, WORKER}
        monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid in self.alive)
        monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: None)
        monkeypatch.setattr(claude_jobs, "_dtach_pids", lambda name: [])
        # claude stop / claude agents --json
        self.stop_rc = 0
        self.stop_timeout = False
        self.stop_ends_job = True        # the job's process goes with the stop
        self.agents_rc = 1               # no listing unless a test gives one
        self.agents_out = "[]"
        self.during_stop = None          # a coroutine function run inside the stop
        self.env_seen: list[dict] = []
        monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", self._exec)
        monkeypatch.setattr(
            "aipager.claude_resolve.try_resolve_claude_binary",
            lambda: SimpleNamespace(chosen=SimpleNamespace(path=FAKE_CLAUDE)))
        monkeypatch.setattr("aipager.daemon_secrets.build_session_env",
                            lambda: dict(SESSION_ENV))
        # the waits: the holder goes away after this many holder polls
        self.holder_gone_after = None
        self.holder_polls = 0
        monkeypatch.setattr(session_ops, "_sleep", self._sleep)
        self.spawned: list = []
        monkeypatch.setattr(session_ops, "_spawn", self._spawn)
        # dtach
        monkeypatch.setattr(inject, "kill_session", self._kill)
        monkeypatch.setattr(inject, "launch_session", self._launch)
        monkeypatch.setattr(inject, "send_keys", self._keys)
        monkeypatch.setattr(session_ops, "Path", MagicMock(
            return_value=MagicMock(is_socket=MagicMock(return_value=False))))
        self.launch_ok = True
        self.during_launch = None        # a coroutine function run inside the launch
        # the bot and the parked session
        bot = mk_bot()
        bot._app.bot = AsyncMock()
        bot._maybe_update_bot_name = AsyncMock()
        bot._update_bot_commands = AsyncMock()
        self.bot = bot
        sess = bot.registry.get_or_create(NAME)
        sess.label = "x"
        sess.scope_chat_id = CHAT
        sess.cwd = "/home/alice/app"
        sess.perm_mode = "auto"
        sess.launch_model = "opus"
        sess.claude_session_id = X
        sess.transcript_path = str(self.projects / f"{X}.jsonl")
        sess.claude_pid = CLIENT
        sess.parked_job = JOB
        sess.parked_job_session_id = JS
        sess.parked_since = time.time() - 60
        bot.registry.transition(NAME, Status.IDLE)
        self.sess = sess
        # Claude's side: the parked client, the job, both transcripts
        self.reg(CLIENT, kind="interactive", sessionId=X, parkedJobId=JOB)
        self.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="idle")
        self.transcript(X, {"type": "user"},
                        {"type": "continued-in", "continuedInSessionId": JS})
        self.transcript(JS, {"type": "user"}, {"type": "assistant"})
        # aipager's side: the link and the pid map
        bg_jobs.link_path(JOB).parent.mkdir(parents=True, exist_ok=True)
        bg_jobs.write_own_json(bg_jobs.link_path(JOB), {
            "job": JOB, "session": NAME, "job_session_id": JS,
            "client_pid": CLIENT, "at": sess.parked_since, "ended": False})
        bg_jobs.write_own_json(bg_jobs.pidmap_path(), {
            "pids": {str(CLIENT): {"session": NAME, "start": None},
                     "999": {"session": "claude-other", "start": None}}})

    # -- Claude's files --
    def reg(self, pid, **fields):
        """Write ``sessions/<pid>.json`` with a fresh mtime: the reader
        caches by (mtime, size), and ``busy`` and ``idle`` are as long."""
        path = self.cfg / "sessions" / f"{pid}.json"
        path.write_text(json.dumps({"pid": pid, **fields}))
        self._mtimes += 1
        ns = 1_700_000_000_000_000_000 + self._mtimes * 1_000_000
        os.utime(path, ns=(ns, ns))

    def unreg(self, pid):
        (self.cfg / "sessions" / f"{pid}.json").unlink(missing_ok=True)

    def transcript(self, sid, *records):
        (self.projects / f"{sid}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records))

    def drop_transcript(self, sid):
        (self.projects / f"{sid}.jsonl").unlink(missing_ok=True)

    # -- the seams --
    async def _exec(self, *argv, **kw):
        args = list(argv[1:])
        self.calls.append(("run", args))
        self.env_seen.append({"argv0": argv[0], "env": kw.get("env")})
        world = self

        class Proc:
            returncode = None

            async def communicate(self, _stdin=None):
                if args[:1] == ["stop"]:
                    if world.during_stop is not None:
                        await world.during_stop()
                    if world.stop_timeout:
                        raise asyncio.TimeoutError
                    self.returncode = world.stop_rc
                    if world.stop_rc == 0 and world.stop_ends_job:
                        world.unreg(WORKER)
                        world.alive.discard(WORKER)
                    return b"", b""
                self.returncode = world.agents_rc
                return world.agents_out.encode(), b""

            def kill(self):
                pass

        return Proc()

    async def _sleep(self, seconds):
        self.calls.append(("sleep", seconds))
        if seconds == claude_jobs.HOLDER_POLL:
            self.holder_polls += 1
            if self.holder_gone_after is not None and self.holder_polls >= self.holder_gone_after:
                self.unreg(WORKER)
                self.alive.discard(WORKER)

    def _spawn(self, coro):
        self.spawned.append(coro)
        return MagicMock()

    async def _kill(self, name):
        self.calls.append(("kill", name))
        return True

    async def _launch(self, short_name, **kw):
        self.calls.append(("launch", kw))
        if self.during_launch is not None:
            await self.during_launch()
        return (True, "") if self.launch_ok else (False, "dtach not installed")

    async def _keys(self, name, key):
        self.calls.append(("keys", key))
        return True

    # -- reading the log --
    def kinds(self, *, sleeps=False):
        return [c[0] for c in self.calls if sleeps or c[0] != "sleep"]

    def launches(self):
        return [c[1] for c in self.calls if c[0] == "launch"]

    def runs(self):
        return [c[1] for c in self.calls if c[0] == "run"]

    def back_messages(self):
        text = i18n.get("en").html(BG_BACK, session="x")
        return [c for c in self.bot._app.bot.send_message.await_args_list
                if c.kwargs.get("text") == text]


    def close(self):
        """Close what was spawned and never run (no "never awaited")."""
        for coro in self.spawned:
            if asyncio.iscoroutine(coro):
                coro.close()


@pytest.fixture
def world(mk_bot, monkeypatch, tmp_path):
    w = World(mk_bot, monkeypatch, tmp_path)
    yield w
    w.close()


def _untouched(world, before_window=0.0):
    sess = world.sess
    assert sess.parked_job == JOB
    assert sess.parked_job_session_id == JS
    assert sess.claude_session_id == X
    assert sess.restarting_until == before_window
    assert bg_jobs.read_own_json(bg_jobs.link_path(JOB)) is not None
    assert world.back_messages() == []


# ---- T3.1: which conversation comes back ---------------------------------------

def test_the_jobs_conversation_comes_back(world, run_async):
    out = run_async(world.bot._bring_back_core(world.sess))
    assert out.ok and out.reason == "done"
    assert [launch["resume_id"] for launch in world.launches()] == [JS]


def test_the_jobs_conversation_comes_back_when_the_old_one_says_nothing(world,
                                                                        run_async):
    """Parked by the registry alone (the old transcript has no
    ``continued-in`` yet): the job's conversation is the one handed to the
    relaunch, not the session's old id."""
    world.transcript(X, {"type": "user"}, {"type": "assistant"})
    assert run_async(world.bot._bring_back_core(world.sess)).ok
    assert [launch["resume_id"] for launch in world.launches()] == [JS]
    assert world.sess.claude_session_id == JS


def test_without_the_jobs_transcript_the_clients_own_comes_back(world, run_async):
    """A fork that never ran a prompt has no transcript yet: the client's
    own conversation comes back, never an empty one, and the hop to the
    job's id (its holder is alive until the stop) is not taken."""
    world.drop_transcript(JS)
    out = run_async(world.bot._bring_back_core(world.sess))
    assert out.ok
    assert [launch["resume_id"] for launch in world.launches()] == [X]


def test_with_no_saved_conversation_nothing_is_touched(world, run_async):
    world.drop_transcript(JS)
    world.drop_transcript(X)
    out = run_async(world.bot._bring_back_core(world.sess, force=True))
    assert (out.ok, out.reason) == (False, "bg_no_conversation")
    assert world.calls == [], "no stop, kill or launch"
    _untouched(world)


def test_where_the_jobs_conversation_moved_on_is_followed(world, run_async):
    """The job's own conversation moved on again (a second ``←``): the
    newest saved one comes back."""
    newer = "ef56ab78-0000-4000-8000-000000000009"
    world.transcript(JS, {"type": "user"},
                     {"type": "continued-in", "continuedInSessionId": newer})
    world.transcript(newer, {"type": "user"})
    assert run_async(world.bot._bring_back_core(world.sess)).ok
    assert [launch["resume_id"] for launch in world.launches()] == [newer]


# ---- T3.2: a busy job is asked about first (S5) ---------------------------------

def test_a_busy_job_is_not_touched(world, run_async):
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert world.calls == []
    _untouched(world)


@pytest.mark.parametrize("how", ["odd-status", "nothing-readable"])
def test_a_job_whose_state_is_unknown_counts_as_busy(world, run_async, how):
    if how == "odd-status":
        world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="starting")
    else:
        for path in (world.cfg / "sessions").iterdir():
            path.unlink()
        (world.cfg / "sessions").rmdir()     # the registry cannot be read
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert [k for k in world.kinds() if k != "run"] == []
    assert all(args[:1] != ["stop"] for args in world.runs())


# ---- T3.3: the order, forced -----------------------------------------------------

def test_a_forced_bring_back_stops_waits_kills_then_launches(world, run_async):
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    world.stop_ends_job = False
    world.holder_gone_after = 2          # the process goes on the 2nd look
    out = run_async(world.bot._bring_back_core(world.sess, force=True))
    assert out.ok
    order = [c[0] if c[0] != "sleep" else f"sleep-{c[1]}" for c in world.calls]
    poll = f"sleep-{claude_jobs.HOLDER_POLL}"
    assert order[:5] == ["run", poll, poll, "kill", order[4]]
    assert world.calls[0] == ("run", ["stop", JOB])
    assert world.kinds()[-2:] == ["kill", "launch"]
    assert "keys" not in world.kinds(), "nothing typed into the parked client"
    seen = world.env_seen[0]
    assert seen["argv0"] == FAKE_CLAUDE and seen["env"] == SESSION_ENV
    launch = world.launches()[0]
    assert launch["resume_id"] == JS and launch["mode"] == "auto"
    assert launch["model"] == "opus" and launch["is_relaunch"] is True
    assert launch["cwd"] == "/home/alice/app"
    assert "system_prompt_extra" in launch


def test_the_target_mode_is_kept(world, run_async):
    out = run_async(world.bot._bring_back_core(world.sess, target_mode="ask"))
    assert out.ok and out.mode == "ask"
    assert world.launches()[0]["mode"] == "ask"
    assert world.sess.perm_mode == "ask"


# ---- T3.4 / T3.5: a stop that fails changes nothing ------------------------------

@pytest.mark.parametrize("how, err", [("rc1", ("stop_exit", {"code": 1})),
                                      ("timeout", ("stop_timeout", {}))])
def test_a_failed_stop_changes_nothing(world, run_async, how, err):
    if how == "rc1":
        world.stop_rc = 1
    else:
        world.stop_timeout = True
    world.sess.restarting_until = 0.0
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.ok, out.reason, out.bg_err) == (False, "bg_stop_failed", err)
    assert world.kinds() == ["run"], "no kill, no launch"
    _untouched(world)
    assert world.sess.relaunch_in_flight is False


def test_a_job_still_holding_the_conversation_after_the_stop_fails(world, run_async):
    world.stop_ends_job = False          # rc 0, but the process stays
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.reason, out.bg_err) == ("bg_stop_failed", ("still_running", {}))
    polls = [c for c in world.calls if c == ("sleep", claude_jobs.HOLDER_POLL)]
    assert len(polls) == int(claude_jobs.HOLDER_WAIT / claude_jobs.HOLDER_POLL)
    assert "kill" not in world.kinds() and "launch" not in world.kinds()
    _untouched(world)


def test_a_failed_stop_reads_in_the_chats_words(world, run_async):
    world.stop_rc = 2
    out = run_async(world.bot._bring_back_core(world.sess))
    from aipager.bot.session_parity import _restart_outcome_text
    assert _restart_outcome_text(out) == (
        "Could not bring <b>x</b> back from Claude Code's background: "
        "claude stop ended with exit code 2")
    assert out.err == "claude stop ended with exit code 2"


# ---- T3.6: a job that is gone is not stopped (B4) ---------------------------------

def test_a_gone_job_is_not_stopped(world, run_async):
    world.unreg(WORKER)
    world.alive.discard(WORKER)
    world.agents_rc, world.agents_out = 0, "[]"
    out = run_async(world.bot._bring_back_core(world.sess))
    assert out.ok
    assert world.runs() == [["agents", "--json"]], "no claude stop"
    assert world.kinds()[-2:] == ["kill", "launch"]


# ---- T3.7: what a bring-back leaves ------------------------------------------------

def test_a_bring_back_forgets_the_park(world, run_async):
    sess = world.sess
    sess.park_told.update({7, ("busy", 7)})
    out = run_async(world.bot._bring_back_core(sess))
    assert out.ok
    assert sess.claude_session_id == JS
    assert sess.transcript_path == str(world.projects / f"{JS}.jsonl")
    assert (sess.parked_job, sess.parked_job_session_id, sess.parked_since) == ("", "", 0.0)
    assert sess.park_told == set() and sess.claude_pid == 0
    assert bg_jobs.read_own_json(bg_jobs.link_path(JOB)) is None
    pids = bg_jobs.read_own_json(bg_jobs.pidmap_path())["pids"]
    assert str(CLIENT) not in pids and "999" in pids
    assert len(world.back_messages()) == 1
    assert sess.bg_release_pending is True
    assert sess.status == Status.IDLE and sess.relaunch_in_flight is False


def _drainable(bot, monkeypatch):
    typed: list[str] = []
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "send_text_and_enter",
                        AsyncMock(side_effect=lambda n, body: typed.append(body) or True))
    bot._mark_driver = MagicMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot._build_reply_context = MagicMock(return_value="")
    return typed


def test_the_first_own_session_start_releases_one_held_message(world, run_async,
                                                                monkeypatch):
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("first held", 31)
    sess.queue_prompt("second held", 32)
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert typed == [], "nothing goes out before the relaunched claude is up"
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "SessionStart", "source": "resume", "session": NAME,
        "session_id": JS}).encode()))
    assert len(typed) == 1 and typed[0].endswith("first held")
    assert [q[0] for q in sess.pending_queue] == ["second held"]
    assert sess.bg_release_pending is False
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "SessionStart", "source": "startup", "session": NAME,
        "session_id": JS, "n": 2}).encode()))
    assert len(typed) == 1, "released once; the rest drain at turn ends"


def test_a_release_waits_while_the_session_is_still_parked(world, run_async,
                                                           monkeypatch):
    typed = _drainable(world.bot, monkeypatch)
    sess = world.sess
    sess.queue_prompt("held", 31)
    sess.bg_release_pending = True       # parked again before it went out
    run_async(world.bot.notify(sess, "bg_release", {}))
    assert typed == [] and sess.bg_release_pending is True


def test_a_release_without_a_bring_back_drains_nothing(world, run_async, monkeypatch):
    typed = _drainable(world.bot, monkeypatch)
    sess = world.sess
    sess.parked_job = ""
    sess.queue_prompt("held for something else", 31)
    run_async(world.bot.notify(sess, "bg_release", {}))
    assert typed == [] and len(sess.pending_queue) == 1


def test_the_monitors_backstop_releases_after_its_time(world, run_async, monkeypatch):
    sess = world.sess
    assert run_async(world.bot._bring_back_core(sess)).ok
    sess.restarting_until = 0.0
    world.unreg(CLIENT)                  # the old client's file went with it
    notify = AsyncMock()
    monitor = SessionMonitor(world.bot.registry, notify)
    sess.bg_released_at = time.monotonic() - (claude_jobs.RELEASE_BACKSTOP - 2)
    run_async(monitor._check_parked_tick(NAME, sess))
    assert [c.args[1] for c in notify.await_args_list] == []
    sess.bg_released_at = time.monotonic() - (claude_jobs.RELEASE_BACKSTOP + 1)
    run_async(monitor._check_parked_tick(NAME, sess))
    assert [c.args[1] for c in notify.await_args_list] == ["bg_release"]
    sess.bg_release_pending = False
    run_async(monitor._check_parked_tick(NAME, sess))
    assert len(notify.await_args_list) == 1


def test_the_old_clients_file_does_not_park_the_session_again(world, run_async):
    """Claude Code can leave a killed claude's registry file behind: it is
    parked on the very job the session's new id starts with."""
    sess = world.sess
    assert run_async(world.bot._bring_back_core(sess)).ok
    sess.restarting_until = 0.0
    world.alive.discard(CLIENT)          # killed, its file left behind
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    assert monitor.check_parked(sess) is False
    assert sess.parked_job == ""


# ---- T3.8: concurrency and the restart window (B3) ----------------------------------

def test_a_bring_back_in_flight_refuses_a_second(world, run_async):
    world.sess.relaunch_in_flight = True
    out = run_async(world.bot._bring_back_core(world.sess, force=True))
    assert (out.ok, out.reason) == (False, "already_restarting")
    assert world.calls == []


def test_not_parked_is_not_brought_back(world, run_async):
    world.sess.parked_job = ""
    out = run_async(world.bot._bring_back_core(world.sess, force=True))
    assert (out.ok, out.reason) == (False, "not_parked")
    assert world.calls == []


def test_the_restart_window_is_open_before_the_stop(world, run_async):
    """The job's SessionEnd (from ``claude stop``) and the parked client's
    exit are expected: neither may say "Session exited"."""
    seen = {}
    sess = world.sess
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)

    async def during():
        seen["restarting"] = sess.is_restarting()
        seen["in_flight"] = sess.relaunch_in_flight
        # the linked job's SessionEnd arrives while it stops
        await recv._on_datagram(json.dumps({
            "hook_event_name": "SessionEnd", "session": NAME, "bg_job": JOB,
            "reason": "other", "session_id": JS}).encode())
        seen["status"] = sess.status
        # and the parked client's own exit, as the kill would bring it
        world.bot.registry.transition(NAME, Status.GONE)
        await world.bot.notify(sess, "session_end", {"source": "hook"})
        world.bot.registry.transition(NAME, Status.IDLE)

    world.during_stop = during
    sess.queue_prompt("held", 41)
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert seen["restarting"] is True and seen["in_flight"] is True
    assert seen["status"] != Status.GONE
    texts = [c.kwargs.get("text", "") for c in world.bot._app.bot.send_message.await_args_list]
    assert not any("exited" in t or "ended" in t.lower() for t in texts), texts
    assert [q[0] for q in sess.pending_queue] == ["held"], "kept for the session"


# ---- T3.9: B1, who goes through the core --------------------------------------------

def test_restart_of_a_parked_session_brings_it_back_unforced(world, run_async):
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    out = run_async(world.bot._restart_session_core(world.sess))
    assert out.reason == "bg_busy"
    assert world.calls == []
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="idle")
    out = run_async(world.bot._restart_session_core(world.sess))
    assert out.ok and world.calls[0] == ("run", ["stop", JOB])
    assert "keys" not in world.kinds()
    # the bring-back's own ending, not a plain restart's
    assert world.sess.parked_job == "" and len(world.back_messages()) == 1
    assert bg_jobs.read_own_json(bg_jobs.link_path(JOB)) is None


def test_a_forced_restart_stops_a_busy_job(world, run_async):
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    out = run_async(world.bot._restart_session_core(world.sess, force=True))
    assert out.ok and world.calls[0] == ("run", ["stop", JOB])


def test_stop_of_a_parked_session_is_a_forced_bring_back(world, run_async):
    sess = world.sess
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    world.bot.registry.transition(NAME, Status.BUSY)   # the job's turn runs
    out = run_async(world.bot._stop_session_core(sess))
    assert out.ok
    assert world.calls[0] == ("run", ["stop", JOB])
    assert "keys" not in world.kinds(), "no Escape into the parked client"
    assert sess.status == Status.IDLE and sess.parked_job == ""


def test_a_failed_stop_of_a_parked_session_says_why(world, run_async):
    world.stop_rc = 1
    world.bot.registry.transition(NAME, Status.BUSY)
    out = run_async(world.bot._stop_session_core(world.sess))
    assert (out.ok, out.reason, out.bg_err) == (False, "bg_stop_failed",
                                                ("stop_exit", {"code": 1}))
    assert "keys" not in world.kinds()
    from aipager import bg_text
    assert bg_text.stop_refused_text(i18n.get("en"), out) == (
        "Could not bring x back from Claude Code's background: "
        "claude stop ended with exit code 1")


def test_a_failed_stop_command_says_why(world, run_async, mk_update):
    world.stop_rc = 1
    world.bot.registry.transition(NAME, Status.BUSY)
    update = mk_update("/stop", chat_id=CHAT)
    world.bot._react = AsyncMock()
    out = run_async(world.bot._stop_session(world.sess, update=update))
    assert out.ok is False
    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.await_args.args[0] == (
        "Could not bring <b>x</b> back from Claude Code's background: "
        "claude stop ended with exit code 1")


def test_a_failed_stop_tap_says_why(world, run_async):
    world.stop_rc = 1
    sess = world.sess
    world.bot.registry.transition(NAME, Status.BUSY)
    query = MagicMock()
    query.from_user = MagicMock(id=12345)
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.effective_chat = MagicMock(id=CHAT)
    update.effective_user = MagicMock(id=12345)
    looked, out = run_async(world.bot._stop_the_turn_shown(
        update, query, NAME, sess.turn_key, again=session_ops.TOAST_STOP_STALE))
    assert looked is True and out.reason == "bg_stop_failed"
    assert query.answer.await_args.args[0] == (
        "Could not bring x back from Claude Code's background: "
        "claude stop ended with exit code 1")


def test_a_safety_halt_of_a_parked_session_is_forced_without_escape(world, run_async):
    sess = world.sess
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    world.bot.registry.transition(NAME, Status.BUSY)
    world.bot._settle_card_text = AsyncMock(return_value=True)
    world.bot._mark_not_delivered = AsyncMock()
    sess.queue_prompt("held", 51)
    run_async(world.bot._halt_for_safety(sess, "rm -rf outside the project"))
    assert world.calls[0] == ("run", ["stop", JOB])
    assert "keys" not in world.kinds()
    assert sess.parked_job == "" and sess.bg_release_pending is False
    assert sess.pending_queue == [], "held messages dropped, as for any halt"
    dropped = [e for c in world.bot._mark_not_delivered.await_args_list for e in c.args[1]]
    assert [e.get("msg_id") for e in dropped] == [51]


def test_a_safety_halt_of_an_ordinary_session_still_sends_escape(world, run_async):
    world.sess.parked_job = ""
    world.bot._settle_card_text = AsyncMock(return_value=True)
    run_async(world.bot._halt_for_safety(world.sess, "rm -rf outside the project"))
    assert [c for c in world.calls if c[0] == "keys"] == [("keys", "Escape")] * 2
    assert world.runs() == []


def test_a_parked_busy_mode_switch_never_types(world, run_async):
    """A restart-type /mode (into Full access) on a parked BUSY session:
    the Ctrl-C courtesy would be typed into the parked client. A turn in
    flight asks first (rev-iter1-003); nothing is typed or run."""
    sess = world.sess
    world.bot.registry.transition(NAME, Status.BUSY)
    out = run_async(world.bot._mode_switch_core(sess, "full"))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert world.calls == []


def test_a_parked_idle_mode_switch_brings_it_back_in_the_new_mode(world, run_async):
    out = run_async(world.bot._mode_switch_core(world.sess, "full"))
    assert out.ok, out
    assert "keys" not in world.kinds()
    assert world.calls[0] == ("run", ["stop", JOB])
    assert world.launches()[0]["mode"] == "full"


# ---- review rev-iter1-002: the release race ----------------------------------------
# The relaunched claude's own SessionStart can arrive while the bring-back
# is still finishing (live check 1: it did, and the held message waited for
# the 10 s backstop). The release is armed before the launch; whichever of
# the bring-back's end, the SessionStart and the backstop comes, one held
# message goes out, once.

#: The id the relaunched claude's SessionStart carries: not compared with
#: the resumed one (a resume may be given a new id).
NEW_ID = "cd34ef56-0000-4000-8000-000000000007"


def _session_start(recv, sid=NEW_ID, n=1):
    return recv._on_datagram(json.dumps({
        "hook_event_name": "SessionStart", "source": "resume", "session": NAME,
        "session_id": sid, "n": n}).encode())


def _backstop(world, run_async):
    """The monitor's backstop, its time long past."""
    world.sess.restarting_until = 0.0
    world.unreg(CLIENT)
    world.sess.bg_released_at = time.monotonic() - (claude_jobs.RELEASE_BACKSTOP + 1)
    monitor = SessionMonitor(world.bot.registry, world.bot.notify)
    run_async(monitor._check_parked_tick(NAME, world.sess))


def test_a_session_start_during_the_relaunch_releases_at_the_bring_backs_end(
        world, run_async, monkeypatch):
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("first held", 31)
    sess.queue_prompt("second held", 32)
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    seen = {}

    async def up():
        await _session_start(recv)
        seen["typed"] = list(typed)
        seen["start_seen"] = sess.bg_start_seen

    world.during_launch = up
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert seen == {"typed": [], "start_seen": True}, "nothing typed mid-way"
    assert len(typed) == 1 and typed[0].endswith("first held"), "no backstop wait"
    assert sess.bg_release_pending is False and sess.bg_start_seen is False
    _backstop(world, run_async)
    assert len(typed) == 1, "the backstop adds nothing"
    run_async(_session_start(recv, n=2))
    assert len(typed) == 1, "a later SessionStart adds nothing"


def test_a_session_start_after_the_bring_back_releases_once(world, run_async,
                                                            monkeypatch):
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("first held", 31)
    sess.queue_prompt("second held", 32)
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert typed == []
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    run_async(_session_start(recv))
    assert len(typed) == 1
    _backstop(world, run_async)
    assert len(typed) == 1, "the backstop adds nothing"


def test_the_backstop_releases_once_when_no_session_start_came(world, run_async,
                                                               monkeypatch):
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("first held", 31)
    sess.queue_prompt("second held", 32)
    assert run_async(world.bot._bring_back_core(sess)).ok
    _backstop(world, run_async)
    assert len(typed) == 1
    _backstop(world, run_async)
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    run_async(_session_start(recv))
    assert len(typed) == 1, "neither a second backstop nor a late SessionStart"


def test_the_release_is_armed_before_the_launch(world, run_async):
    """And the old client's pid is forgotten there, before the new claude
    can report its own."""
    sess = world.sess
    seen = {}

    async def at_launch():
        seen["pending"] = sess.bg_release_pending
        seen["pid"] = sess.claude_pid

    world.during_launch = at_launch
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert seen == {"pending": True, "pid": 0}


def test_the_new_claudes_pid_survives_the_end_of_the_bring_back(world, run_async):
    sess = world.sess
    new_pid = 4777

    async def up():
        sess.claude_pid = new_pid        # what its own SessionStart recorded (D1)

    world.during_launch = up
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert sess.claude_pid == new_pid


@pytest.mark.parametrize("how", ["launch_failed", "launch_raises", "still_stopping"])
def test_a_failed_relaunch_leaves_nothing_armed(world, run_async, monkeypatch, how):
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("held", 31)
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)

    async def up_then(fail):
        await _session_start(recv)
        if fail:
            raise RuntimeError("dtach went away")

    if how == "launch_failed":
        world.launch_ok = False
        world.during_launch = lambda: up_then(False)
        assert run_async(world.bot._bring_back_core(sess)).reason == "launch_failed"
    elif how == "launch_raises":
        world.during_launch = lambda: up_then(True)
        with pytest.raises(RuntimeError):
            run_async(world.bot._bring_back_core(sess))
    else:
        monkeypatch.setattr(session_ops, "Path", MagicMock(
            return_value=MagicMock(is_socket=MagicMock(return_value=True))))
        assert run_async(world.bot._bring_back_core(sess)).reason == "still_stopping"
    assert (sess.bg_release_pending, sess.bg_start_seen,
            sess.bg_release_armed_at) == (False, False, 0.0)
    sess.parked_job = ""                 # e.g. a manual /resume later
    run_async(_session_start(recv, n=3))
    _backstop(world, run_async)
    assert typed == [], "no stale release"


def test_a_pending_release_survives_a_daemon_restart(world, run_async, monkeypatch,
                                                     tmp_state_file):
    """Saved between a bring-back and its release: the first tick after
    the restart releases one held message."""
    from aipager.state import SessionRegistry
    sess = world.sess
    sess.queue_prompt("first held", 31)
    sess.queue_prompt("second held", 32)
    assert run_async(world.bot._bring_back_core(sess)).ok
    sess.restarting_until = 0.0
    world.bot.registry.save()
    fresh = SessionRegistry()
    fresh.load()
    again = fresh.get(NAME)
    assert again.bg_release_pending is True
    world.bot.registry = fresh
    typed = _drainable(world.bot, monkeypatch)
    world.unreg(CLIENT)

    async def live():
        return [NAME]

    monkeypatch.setattr(inject, "list_sessions", live)
    monitor = SessionMonitor(fresh, world.bot.notify)
    run_async(monitor._scan())          # seen live again, then the backstop
    run_async(monitor._scan())
    assert len(typed) == 1 and typed[0].endswith("first held")
    assert again.bg_release_pending is False


# ---- review rev-iter1-008: the gate's bring-back that finds the park gone ----------

@pytest.mark.parametrize("state, drains", [
    ("release-pending", False),          # another bring-back's release drains
    ("turn-running", False),             # the released message's turn drains
    ("idle", True),                      # a park that ended with no bring-back
])
def test_a_spawned_bring_back_that_finds_no_park(world, run_async, monkeypatch,
                                                 mk_update, state, drains):
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.parked_job = ""
    sess.queue_prompt("held", 31)
    if state == "release-pending":
        sess.arm_bg_release()
    elif state == "turn-running":
        world.bot.registry.transition(NAME, Status.BUSY)
    update = mk_update("held", chat_id=CHAT)
    run_async(world.bot._auto_bring_back(update, sess))
    assert (len(typed) == 1) is drains
    assert world.calls == [], "nothing stopped, killed or launched"


def test_an_own_fork_session_start_releases_nothing(world, run_async, monkeypatch):
    """A fork's SessionStart is not the relaunched claude coming up."""
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("held", 31)
    assert run_async(world.bot._bring_back_core(sess)).ok
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "SessionStart", "source": "fork", "session": NAME,
        "session_id": NEW_ID}).encode()))
    assert typed == [] and sess.bg_release_pending is True


# ---- review rev-iter1-003: the fork window -------------------------------------------
# A just-forked job's entry reads unknown, then idle, then busy when a turn
# moved with it (live check 2). An unforced bring-back in the idle moment
# would stop the job about to continue the user's turn.

@pytest.mark.parametrize("how", ["busy", "interactive", "tool-in-flight"])
def test_a_turn_in_flight_asks_whatever_the_job_says(world, run_async, how):
    sess = world.sess                    # the job's entry says "idle"
    if how == "busy":
        world.bot.registry.transition(NAME, Status.BUSY)
    elif how == "interactive":
        world.bot.registry.transition(NAME, Status.INTERACTIVE)
    else:
        sess.pending_tool_started_at = time.monotonic()
    out = run_async(world.bot._bring_back_core(sess))
    assert (out.ok, out.reason, out.retry_after) == (False, "bg_busy", 0.0)
    assert world.calls == []
    _untouched(world)


def test_a_turn_in_flight_does_not_stop_a_forced_bring_back(world, run_async):
    world.bot.registry.transition(NAME, Status.BUSY)
    out = run_async(world.bot._bring_back_core(world.sess, force=True))
    assert out.ok and world.calls[0] == ("run", ["stop", JOB])


def test_a_young_park_asks_with_the_time_left(world, run_async):
    world.sess.parked_since = time.time() - 1
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert 0 < out.retry_after <= claude_jobs.PARK_SETTLE_SECONDS - 0.5
    assert world.calls == []
    _untouched(world)


def test_a_young_park_waited_out_is_brought_back(world, run_async):
    world.sess.parked_since = time.time() - 1
    out = run_async(world.bot._bring_back_core(world.sess, settled=True))
    assert out.ok and world.calls[0] == ("run", ["stop", JOB])


def test_a_young_park_with_a_busy_job_has_no_time_left(world, run_async):
    """Busy for real: waiting would not change the answer."""
    world.sess.parked_since = time.time() - 1
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.reason, out.retry_after) == ("bg_busy", 0.0)


def test_a_settled_idle_park_is_brought_back_as_before(world, run_async):
    world.sess.parked_since = time.time() - 10
    out = run_async(world.bot._bring_back_core(world.sess))
    assert out.ok
    assert world.kinds() == ["run", "kill", "launch"]
    assert world.calls[0] == ("run", ["stop", JOB])


def test_the_jobs_entry_is_read_again_right_before_the_stop(world, run_async,
                                                            monkeypatch):
    """The first answer said idle (as from ``claude agents``, which can take
    seconds); by the stop the job's own entry says busy: nothing is stopped."""
    async def idle_then_busy(job, entries=None):
        world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
        return claude_jobs.IDLE

    monkeypatch.setattr(claude_jobs, "job_state", idle_then_busy)
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert world.calls == []
    _untouched(world)


def test_a_job_that_read_gone_but_has_an_entry_again_is_stopped(world, run_async,
                                                                monkeypatch):
    """The stricter answer: an idle entry beats an earlier "gone", so the
    job is stopped before the kill, never skipped."""
    async def gone(job, entries=None):
        return claude_jobs.GONE

    monkeypatch.setattr(claude_jobs, "job_state", gone)
    assert run_async(world.bot._bring_back_core(world.sess)).ok
    assert world.calls[0] == ("run", ["stop", JOB])


@pytest.mark.parametrize("a, b, stricter", [
    ("idle", "busy", "busy"), ("busy", "idle", "busy"), ("gone", "idle", "idle"),
    ("idle", "unknown", "unknown"), ("unknown", "gone", "unknown"),
    ("gone", "gone", "gone")])
def test_the_stricter_answer(a, b, stricter):
    assert claude_jobs.stricter_state(a, b) == stricter


def test_the_gate_waits_out_a_young_park_instead_of_asking(world, run_async,
                                                          mk_update):
    sess = world.sess
    sess.parked_since = time.time() - 1
    sess.queue_prompt("held", 31)
    update = mk_update("held", chat_id=CHAT)
    run_async(world.bot._auto_bring_back(update, sess))
    assert world.calls[0][0] == "sleep", world.calls
    assert 0 < world.calls[0][1] <= claude_jobs.PARK_SETTLE_SECONDS
    assert world.calls[1] == ("run", ["stop", JOB]), (
        "decided again after the wait, then brought back")
    assert world.back_messages() and sess.parked_job == ""
    question = i18n.get("en").html(session_ops.BG_BUSY_QUESTION, session="x")
    assert not any(c.kwargs.get("text") == question
                   for c in world.bot._app.bot.send_message.await_args_list)


def test_the_gate_asks_at_once_when_a_turn_is_in_flight(world, run_async, mk_update):
    sess = world.sess
    sess.parked_since = time.time() - 1
    world.bot.registry.transition(NAME, Status.BUSY)
    sess.queue_prompt("held", 31)
    update = mk_update("held", chat_id=CHAT)
    run_async(world.bot._auto_bring_back(update, sess))
    assert world.calls == [], "no wait, no stop"
    question = i18n.get("en").html(session_ops.BG_BUSY_QUESTION, session="x")
    assert [c.kwargs.get("text") for c in world.bot._app.bot.send_message.await_args_list
            ] == [question]
