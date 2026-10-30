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


# ---- tester-iter2-002: the restart's quiet window hides no new park -----------------

NEW_PID = 4300
JOB2 = "ef90ab12"


def test_a_new_park_inside_the_restart_window_is_seen(world, run_async):
    """The bring-back's quiet window (``restarting_until``) is still open
    when the relaunched claude moves to the background again: the check
    sees it (only a relaunch in flight is skipped)."""
    sess = world.sess
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert sess.is_restarting(), "precondition: the quiet window still runs"
    world.alive.discard(CLIENT)
    world.alive.add(NEW_PID)
    sess.claude_pid = NEW_PID
    world.reg(NEW_PID, kind="interactive", sessionId=JS, parkedJobId=JOB2)
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    assert monitor.check_parked(sess) is True
    assert sess.parked_job == JOB2


def test_a_relaunch_in_flight_is_not_checked(world, run_async):
    """While the relaunch itself runs the old client is being stopped and
    killed: nothing read then changes the park."""
    sess = world.sess
    sess.parked_job = ""
    sess.relaunch_in_flight = True
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    assert monitor.check_parked(sess) is False
    assert sess.parked_job == ""


def test_the_old_clients_file_inside_the_restart_window_parks_nothing(world, run_async):
    """The old client's registry file, left behind by the kill, is parked
    on the job the session's new id starts with: inside the quiet window
    too, a dead process is never the client."""
    sess = world.sess
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert sess.is_restarting(), "precondition: the quiet window still runs"
    world.alive.discard(CLIENT)          # killed, its file left behind
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    assert monitor.check_parked(sess) is False
    assert sess.parked_job == ""


def test_a_reused_pid_of_the_old_client_parks_nothing(world, run_async, monkeypatch):
    """The old client's pid now runs another process (its start time is
    not the entry's): not the client either."""
    sess = world.sess
    world.reg(CLIENT, kind="interactive", sessionId=X, parkedJobId=JOB, procStart=111)
    assert run_async(world.bot._bring_back_core(sess)).ok
    monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: 222)
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    assert monitor.check_parked(sess) is False
    assert sess.parked_job == ""


# ---- rev-iter3-001: a move already handled never parks the session again ------------

def _fresh_tick(world, monitor):
    """One scan's parked check of the session, with a registry read made now."""
    monitor._tick_read_at = time.monotonic()
    monitor._tick_registry = claude_jobs.read_registry()
    return monitor._check_parked_tick(NAME, world.sess)


def _idle_job_brought_back(world, run_async, monkeypatch):
    """The idle-job case (live-check-1 finding 4): the fork wrote no
    transcript, so the client's own conversation X comes back, and X still
    ends with ``continued-in`` to the job just stopped. The old client is
    dead and the relaunched claude has not written its registry file."""
    world.drop_transcript(JS)
    typed = _drainable(world.bot, monkeypatch)
    world.sess.queue_prompt("held", 31)
    assert run_async(world.bot._bring_back_core(world.sess)).ok
    assert [launch["resume_id"] for launch in world.launches()] == [X]
    world.alive.discard(CLIENT)          # killed, its file left behind
    return typed


def test_an_idle_jobs_bring_back_is_not_parked_again_by_its_own_transcript(
        world, run_async, monkeypatch):
    """The reviewer's probe: in the gap before the relaunched claude's
    registry entry, X's old ``continued-in`` parks nothing, sends no second
    notice, and the SessionStart releases the held message at once."""
    sess = world.sess
    typed = _idle_job_brought_back(world, run_async, monkeypatch)
    notify = AsyncMock()
    monitor = SessionMonitor(world.bot.registry, notify)
    run_async(_fresh_tick(world, monitor))
    assert sess.parked_job == ""
    assert [c.args[1] for c in notify.await_args_list] == []
    assert monitor.check_parked(sess) is False, "the gate's check too"
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    run_async(_session_start(recv, sid=X))
    assert len(typed) == 1 and typed[0].endswith("held"), "released at once"
    assert sess.bg_release_pending is False


def test_the_handled_move_survives_a_daemon_restart(world, run_async, monkeypatch,
                                                   tmp_state_file):
    """A deploy restart in that gap: the reloaded session is not parked on
    the stopped job either."""
    from aipager.state import SessionRegistry
    _idle_job_brought_back(world, run_async, monkeypatch)
    world.sess.restarting_until = 0.0
    world.bot.registry.save()
    fresh = SessionRegistry()
    fresh.load()
    again = fresh.get(NAME)
    assert (again.moved_handled_sid, again.moved_handled_job) == (X, JOB)
    notify = AsyncMock()
    monitor = SessionMonitor(fresh, notify)
    monitor._tick_read_at = time.monotonic()
    monitor._tick_registry = claude_jobs.read_registry()
    run_async(monitor._check_parked_tick(NAME, again))
    assert again.parked_job == ""
    assert [c.args[1] for c in notify.await_args_list] == []


@pytest.mark.parametrize("sid, job", [
    ("", JOB), (X, ""), (X, "bad/job"), (123, JOB), ("a" * 65, JOB)])
def test_a_handled_move_that_cannot_be_used_restores_as_none(tmp_state_file, sid, job):
    from aipager.state import SessionRegistry
    reg = SessionRegistry()
    sess = reg.get_or_create(NAME)
    sess.moved_handled_sid, sess.moved_handled_job = sid, job
    reg.save()
    fresh = SessionRegistry()
    fresh.load()
    again = fresh.get(NAME)
    assert (again.moved_handled_sid, again.moved_handled_job) == ("", "")


def test_a_new_move_to_another_job_still_parks(world, run_async, monkeypatch):
    """A real new ``←`` later in the same conversation: a ``continued-in``
    to another job's conversation parks on that job."""
    sess = world.sess
    _idle_job_brought_back(world, run_async, monkeypatch)
    world.transcript(X, {"type": "user"},
                     {"type": "continued-in", "continuedInSessionId": JS},
                     {"type": "user"},
                     {"type": "continued-in", "continuedInSessionId": NEW_ID})
    notify = AsyncMock()
    monitor = SessionMonitor(world.bot.registry, notify)
    run_async(_fresh_tick(world, monitor))
    assert sess.parked_job == NEW_ID[:8]
    assert [c.args[1] for c in notify.await_args_list] == ["bg_parked"]


def test_the_note_is_forgotten_once_the_conversation_goes_on(world, run_async,
                                                             monkeypatch):
    """A prompt typed in X after the bring-back (X no longer ends moved)
    forgets the note: X moved into that same job id again parks."""
    sess = world.sess
    _idle_job_brought_back(world, run_async, monkeypatch)
    world.transcript(X, {"type": "user"},
                     {"type": "continued-in", "continuedInSessionId": JS},
                     {"type": "user"})
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    run_async(_fresh_tick(world, monitor))
    assert sess.parked_job == ""
    assert (sess.moved_handled_sid, sess.moved_handled_job) == ("", "")
    world.transcript(X, {"type": "user"},
                     {"type": "continued-in", "continuedInSessionId": JS},
                     {"type": "user"},
                     {"type": "continued-in", "continuedInSessionId": JS})
    run_async(_fresh_tick(world, monitor))
    assert sess.parked_job == JOB


def test_a_note_for_another_conversation_parks_as_ever(world, run_async):
    """Only the conversation that was launched on purpose is spared."""
    sess = world.sess
    sess.parked_job = ""
    sess.note_moved_handled(NEW_ID, JOB)
    world.alive.discard(CLIENT)
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    assert monitor.check_parked(sess) is True
    assert sess.parked_job == JOB


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
    world.bot.registry.transition(NAME, Status.BUSY)
    world.bot._settle_card_text = AsyncMock(return_value=True)
    run_async(world.bot._halt_for_safety(world.sess, "rm -rf outside the project"))
    assert [c for c in world.calls if c[0] == "keys"] == [("keys", "Escape")] * 2
    assert world.runs() == []


def test_a_safety_halt_of_an_idle_session_types_nothing(world, run_async):
    """Roadmap 8.121 F5: Claude's own turn ended (a background agent's call
    was blocked), so Escape would open Claude Code's Rewind menu."""
    world.sess.parked_job = ""
    assert world.sess.status is Status.IDLE
    world.bot._settle_card_text = AsyncMock(return_value=True)
    run_async(world.bot._halt_for_safety(world.sess, "rm -rf outside the project"))
    assert [c for c in world.calls if c[0] == "keys"] == []
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


# ---- tester-iter3-001: a turn end's drain claims a pending release -------------------

def test_a_turn_ends_drain_claims_the_pending_release(world, run_async, monkeypatch):
    """No SessionStart came, a turn typed in the terminal ended, and its
    drain sent the first held message: that was the release, so a later own
    SessionStart (or the backstop) adds nothing to the turn it started."""
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("first held", 31)
    sess.queue_prompt("second held", 32)
    assert run_async(world.bot._bring_back_core(sess)).ok
    assert typed == [] and sess.bg_release_pending is True
    run_async(world.bot._drain_next_queued(sess))      # the terminal turn's end
    assert len(typed) == 1 and typed[0].endswith("first held")
    assert sess.bg_release_pending is False
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    run_async(_session_start(recv))
    _backstop(world, run_async)
    assert len(typed) == 1, "neither the SessionStart nor the backstop adds one"


def test_a_turn_ends_drain_sends_nothing_while_a_halt_blocks_the_release(
        world, run_async, monkeypatch):
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("held", 31)
    assert run_async(world.bot._bring_back_core(sess)).ok
    sess.bg_release_blocks = 1
    run_async(world.bot._drain_next_queued(sess))
    assert typed == [] and sess.bg_release_pending is True
    assert [q[0] for q in sess.pending_queue] == ["held"]


def test_the_backstop_waits_while_the_session_is_busy(world, run_async, monkeypatch):
    """The nit of review 2: a turn runs (typed in the terminal, say, after a
    daemon restart): the backstop releases nothing until the session is
    IDLE again."""
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("held", 31)
    assert run_async(world.bot._bring_back_core(sess)).ok
    world.bot.registry.transition(NAME, Status.BUSY)
    _backstop(world, run_async)
    assert typed == [] and sess.bg_release_pending is True
    world.bot.registry.transition(NAME, Status.IDLE)
    _backstop(world, run_async)
    assert len(typed) == 1 and sess.bg_release_pending is False


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


def test_a_spawned_bring_back_looks_for_a_park_again_before_it_drains(
        world, run_async, monkeypatch, mk_update):
    """Review rev-iter2-001: the park looked gone to this run, but Claude's
    files say parked now: the held message stays held, nothing is typed."""
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.parked_job = ""
    sess.queue_prompt("held", 31)
    looks = []

    def check(s):
        looks.append(s.name)
        s.parked_job = JOB              # Claude's registry: parked after all

    world.bot.parked_check = check
    run_async(world.bot._auto_bring_back(mk_update("held", chat_id=CHAT), sess))
    assert looks == [NAME]
    assert typed == [] and [q[0] for q in sess.pending_queue] == ["held"]
    assert world.calls == []


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

#: The wall clock a young park's age is read against here (``wall``): years
#: before the real time, so a park-age reader that still used
#: ``time.time()`` would see these parks as settled. On the real clock a
#: slow runner can age a park past the window between setup and check.
WALL = 1_700_000_000.0


@pytest.fixture
def wall(monkeypatch):
    """Freeze ``session_ops._now_wall`` at :data:`WALL`; returns it."""
    monkeypatch.setattr(session_ops, "_now_wall", lambda: WALL)
    return WALL


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


def test_a_young_park_asks_with_the_time_left(world, run_async, wall):
    world.sess.parked_since = wall - 1
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert out.retry_after == pytest.approx(claude_jobs.PARK_SETTLE_SECONDS - 1, abs=1e-6)
    assert world.calls == []
    _untouched(world)


def test_a_young_park_waited_out_is_brought_back(world, run_async, wall):
    world.sess.parked_since = wall - 1
    out = run_async(world.bot._bring_back_core(world.sess, settled=JOB))
    assert out.ok and world.calls[0] == ("run", ["stop", JOB])


def test_a_young_park_with_a_busy_job_has_no_time_left(world, run_async, wall):
    """Busy for real: waiting would not change the answer."""
    world.sess.parked_since = wall - 1
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.reason, out.retry_after) == ("bg_busy", 0.0)


def test_a_settled_idle_park_is_brought_back_as_before(world, run_async, wall):
    world.sess.parked_since = wall - 10
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
                                                          mk_update, wall):
    sess = world.sess
    sess.parked_since = wall - 1
    sess.queue_prompt("held", 31)
    update = mk_update("held", chat_id=CHAT)
    run_async(world.bot._auto_bring_back(update, sess))
    assert world.calls[0][0] == "sleep", world.calls
    assert world.calls[0][1] == pytest.approx(claude_jobs.PARK_SETTLE_SECONDS - 1, abs=1e-6)
    assert world.calls[1] == ("run", ["stop", JOB]), (
        "decided again after the wait, then brought back")
    assert world.back_messages() and sess.parked_job == ""
    question = i18n.get("en").html(session_ops.BG_BUSY_QUESTION, session="x")
    assert not any(c.kwargs.get("text") == question
                   for c in world.bot._app.bot.send_message.await_args_list)


@pytest.mark.parametrize("mono_age, young", [
    (1.0, True),                  # noticed a second ago: still young
    (10.0, False),                # noticed long ago: settled
    (None, True),                 # not noticed by this daemon: just noticed
])
def test_a_wall_clock_gone_back_does_not_settle_a_park(world, mono_age, young):
    """The nit of review 2: ``parked_since`` lies in the future (the clock
    was set back): the monotonic clock gives the park's age."""
    sess = world.sess
    sess.parked_since = time.time() + 3600
    sess.park_changed_at = time.monotonic() - mono_age if mono_age is not None else 0.0
    wait = world.bot._park_settles_in(sess)
    assert (wait > 0) is young
    assert wait <= claude_jobs.PARK_SETTLE_SECONDS


def test_a_wait_for_another_park_does_not_settle_this_one(world, run_async, wall):
    """The nit of review 2: *settled* names the park that was waited out;
    for a young park on another job it counts for nothing."""
    world.sess.parked_since = wall - 1
    out = run_async(world.bot._bring_back_core(world.sess, settled="cd34ef56"))
    assert out.reason == "bg_busy"
    assert out.retry_after == pytest.approx(claude_jobs.PARK_SETTLE_SECONDS - 1, abs=1e-6)
    assert world.calls == []


def test_the_gate_does_not_carry_its_wait_over_to_a_new_park(world, run_async, mk_update,
                                                            monkeypatch, wall):
    """While the gate waits out a young park, the session is brought back
    and parked on a new job: that new, young park asks again (its own
    window), it is not stopped."""
    sess = world.sess
    sess.parked_since = wall - 1
    sess.queue_prompt("held", 31)
    inner = world._sleep

    async def sleep(seconds):
        await inner(seconds)
        if 0 < seconds <= claude_jobs.PARK_SETTLE_SECONDS:
            sess.parked_job = "cd34ef56"           # a new park, just now
            sess.parked_since = wall

    monkeypatch.setattr(session_ops, "_sleep", sleep)
    run_async(world.bot._auto_bring_back(mk_update("held", chat_id=CHAT), sess))
    assert [r for r in world.runs() if r[:1] == ["stop"]] == [], "nothing was stopped"
    assert "kill" not in world.kinds() and sess.parked_job == "cd34ef56"


def test_the_gate_asks_at_once_when_a_turn_is_in_flight(world, run_async, mk_update,
                                                        wall):
    sess = world.sess
    sess.parked_since = wall - 1
    world.bot.registry.transition(NAME, Status.BUSY)
    sess.queue_prompt("held", 31)
    update = mk_update("held", chat_id=CHAT)
    run_async(world.bot._auto_bring_back(update, sess))
    assert world.calls == [], "no wait, no stop"
    question = i18n.get("en").html(session_ops.BG_BUSY_QUESTION, session="x")
    assert [c.kwargs.get("text") for c in world.bot._app.bot.send_message.await_args_list
            ] == [question]


# ---- review rev-iter1-005: a job left out of a partial read is not gone ----------------

def test_a_job_left_out_of_a_partial_registry_read_is_still_stopped(world, run_async,
                                                                    monkeypatch):
    """The listing fails and the job's file was not parsed (the parse
    budget went to newer files): its state is unknown, not gone, so a
    forced bring-back still runs ``claude stop`` before the kill."""
    monkeypatch.setattr(claude_jobs, "REGISTRY_SCAN_LIMIT", 1)
    world.reg(CLIENT, kind="interactive", sessionId=X, parkedJobId=JOB)  # the newest
    out = run_async(world.bot._bring_back_core(world.sess, force=True))
    assert out.ok
    assert ("run", ["stop", JOB]) in world.calls
    assert world.calls.index(("run", ["stop", JOB])) < world.kinds(sleeps=True).index("kill")


def test_a_job_left_out_of_a_partial_registry_read_is_asked_about(world, run_async,
                                                                  monkeypatch):
    monkeypatch.setattr(claude_jobs, "REGISTRY_SCAN_LIMIT", 1)
    world.reg(CLIENT, kind="interactive", sessionId=X, parkedJobId=JOB)
    out = run_async(world.bot._bring_back_core(world.sess))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert "kill" not in world.kinds()


# ---- review rev-iter1-009: a mode switch refused by a background job says why --------

def _mode_card_switch(world, run_async, target="full"):
    """The /mode card's restart-type switch (``_do_perms_switch_via_fn``),
    returning the card's last text and keyboard."""
    edits = []

    async def edit(text, **kw):
        edits.append((text, kw.get("reply_markup")))

    world.bot._mode_card = MagicMock(return_value=("MODE CARD", "MODE KB"))
    run_async(world.bot._do_perms_switch_via_fn(
        world.sess, target, edit, turn=world.sess.turn_key, chat_id=CHAT, announce=False))
    return edits[-1]


def test_a_mode_switch_into_a_busy_job_shows_the_question_and_its_buttons(world,
                                                                          run_async):
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    text, kb = _mode_card_switch(world, run_async)
    assert text == i18n.get("en").html(session_ops.BG_BUSY_QUESTION, session="x")
    # "Stop and bring back" carries the mode asked for (review rev-iter2-002).
    assert [b.callback_data.rsplit(":", 1)[1] for row in kb.inline_keyboard
            for b in row] == [f"bgmodefull{JOB}", "bgleave"]
    assert world.calls == []


def _tap_question(world, run_async, kb, *, user_id=12345):
    """Tap the question's "Stop and bring back" (its first button)."""
    from aipager.bot import session_parity
    verb = kb.inline_keyboard[0][0].callback_data.rsplit(":", 1)[1]
    q = MagicMock()
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock(chat=MagicMock(id=CHAT), chat_id=CHAT, message_id=77)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock(callback_query=q, effective_chat=MagicMock(id=CHAT),
                  effective_user=MagicMock(id=user_id))
    assert run_async(session_parity.handle_callback(world.bot, u, q, NAME, verb)) is True
    return q


def test_the_mode_switchs_stop_and_bring_back_comes_back_in_the_mode_asked_for(
        world, run_async):
    """Review rev-iter2-002: a switch to Full access meets a busy job; its
    question's "Stop and bring back" stops the job and the session comes
    back in Full access, as the Mini App's ``{mode, force}`` does."""
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    world.bot._may_use_full_access = MagicMock(return_value=True)
    assert world.sess.perm_mode == "auto"
    _text, kb = _mode_card_switch(world, run_async, target="full")
    _tap_question(world, run_async, kb)
    assert world.runs()[0] == ["stop", JOB]
    assert [lc["mode"] for lc in world.launches()] == ["full"]
    assert world.sess.perm_mode == "full" and world.sess.parked_job == ""


def test_the_mode_switchs_question_into_full_access_needs_an_admin_at_tap_time(
        world, run_async):
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    world.bot._may_use_full_access = MagicMock(return_value=True)
    _text, kb = _mode_card_switch(world, run_async, target="full")
    world.bot._may_use_full_access = MagicMock(return_value=False)
    q = _tap_question(world, run_async, kb)
    assert world.calls == [] and world.sess.parked_job == JOB
    assert q.answer.await_args.kwargs.get("show_alert") is True


def test_the_restart_question_still_keeps_the_sessions_mode(world, run_async):
    """Restart's question has the plain "Stop and bring back": the session
    comes back in its own mode."""
    out = session_ops.RestartOutcome(ok=False, reason="bg_busy", label="x", mode="full")
    from aipager.bot import session_parity
    kb = session_parity.bg_busy_keyboard(world.bot, CHAT, world.sess)
    assert kb.inline_keyboard[0][0].callback_data.endswith(f"bgforce{JOB}")
    assert out.mode == "full"


@pytest.mark.parametrize("how, expected", [
    ("stop-failed", "Could not bring <b>x</b> back from Claude Code's background: "
                    "claude stop ended with exit code 1"),
    ("no-conversation", "Could not bring <b>x</b> back because Claude Code has no "
                        "saved conversation for it."),
])
def test_a_mode_switch_a_background_job_refused_says_why(world, run_async, how, expected):
    from aipager.bot import callbacks
    if how == "stop-failed":
        world.stop_rc = 1
    else:
        world.drop_transcript(JS)
        world.drop_transcript(X)
    text, kb = _mode_card_switch(world, run_async)
    assert (text, kb) == ("MODE CARD", "MODE KB")
    note = world.bot._mode_card.call_args.args[2]
    assert note == expected
    out = session_ops.RestartOutcome(ok=False, reason="not_live", label="x")
    assert callbacks._switch_refused(out) != expected, "the not-running text is another"


@pytest.mark.parametrize("how", ["busy", "stop-failed"])
def test_the_ready_cards_mode_switch_says_why(world, run_async, monkeypatch, how):
    """new_flow's Ready card, switching into Full access: a busy job turns
    the card into the question with its buttons; a failed stop is said in
    the card's note."""
    from aipager.bot import new_flow
    if how == "busy":
        world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    else:
        world.stop_rc = 1
    edits = []

    async def edit_at(bot, chat_id, message_id, text, kb=None):
        edits.append((text, kb))

    monkeypatch.setattr(new_flow, "_edit_at", edit_at)
    notes = []
    monkeypatch.setattr(new_flow, "render_ready",
                        lambda bot, update, sess, note="", changed="", **kw:
                        notes.append(note) or ("READY", None))
    world.bot._may_use_full_access = MagicMock(return_value=True)
    world.sess.tap_is_for_this_turn = lambda *a, **k: True
    query = MagicMock()
    query.from_user = MagicMock(id=12345)
    query.message = MagicMock(message_id=88)
    query.answer = AsyncMock()
    update = MagicMock(callback_query=query, effective_chat=MagicMock(id=CHAT),
                       effective_user=MagicMock(id=12345))
    run_async(new_flow._handle_ready_callback(world.bot, update, query, NAME, "rdy_full"))
    if how == "busy":
        text, kb = edits[-1]
        assert text == i18n.get("en").html(session_ops.BG_BUSY_QUESTION, session="x")
        assert [b.callback_data.rsplit(":", 1)[1] for row in kb.inline_keyboard
                for b in row] == [f"bgmodefull{JOB}", "bgleave"]
    else:
        assert notes[-1] == ("Could not bring x back from Claude Code's background: "
                             "claude stop ended with exit code 1")


# ---- review rev-iter1-010: a safety halt that could not stop the job says so -------------

def _halt(world, run_async):
    world.bot.registry.transition(NAME, Status.BUSY)
    world.bot._settle_card_text = AsyncMock(return_value=True)
    world.bot._mark_not_delivered = AsyncMock()
    run_async(world.bot._halt_for_safety(world.sess, "rm -rf outside the project"))
    return world.bot._settle_card_text.await_args.args[1]


def test_a_halt_whose_stop_failed_says_the_job_may_still_run(world, run_async):
    world.stop_rc = 1
    notice = _halt(world, run_async)
    assert notice.endswith(
        "\n⚠️ The Claude Code background job of <b>x</b> could not be stopped, so the "
        "blocked work may still be running there: claude stop ended with exit code 1")
    assert world.sess.parked_job == JOB


@pytest.mark.parametrize("how", ["stop-failed", "still-running-bring-back"])
def test_a_halt_that_could_not_stop_the_job_does_not_say_stopped(world, run_async, how):
    """Tester-iter2-003: the first line says "(stopped)" only when the work
    was stopped; the next line says it may still run."""
    if how == "stop-failed":
        world.stop_rc = 1
    else:
        world.sess.relaunch_in_flight = True      # never ends
    first, second = _halt(world, run_async).split("\n")
    assert first == ("🛑 <b>x</b> · Blocked by safety policy: "
                     "rm -rf outside the project")
    assert "could not be stopped" in second


def test_a_halt_that_stopped_the_job_says_stopped(world, run_async):
    assert _halt(world, run_async) == ("🛑 <b>x</b> · Blocked by safety policy: "
                                       "rm -rf outside the project (stopped)")


def test_the_not_stopped_notice_in_another_language():
    from aipager.bot import safety_notice
    fa = i18n.get("fa")
    stopped = safety_notice.notice_html(fa, "x", "rm -rf outside the project")
    not_stopped = safety_notice.notice_html(fa, "x", "rm -rf outside the project",
                                            stopped=False)
    # The Persian "(stopped)" is gone; the rest of the line is the same.
    assert "(" in stopped and "(" not in not_stopped
    assert "rm -rf outside the project" in not_stopped


def test_a_halt_that_stopped_the_job_says_nothing_more(world, run_async):
    notice = _halt(world, run_async)
    assert "could not be stopped" not in notice
    assert world.runs()[0] == ["stop", JOB]


def test_a_halt_waits_for_a_bring_back_already_running_then_stops_the_job(world,
                                                                         run_async,
                                                                         monkeypatch):
    """The gate's (unforced) bring-back was running: the halt waits for it,
    finds the session still parked and stops the job itself."""
    sess = world.sess
    sess.relaunch_in_flight = True
    polls = []
    inner = world._sleep

    async def sleep(seconds):
        await inner(seconds)
        if seconds == session_ops._HALT_WAIT_POLL:
            polls.append(seconds)
            if len(polls) == 3:
                sess.relaunch_in_flight = False      # it ended: the job was busy

    monkeypatch.setattr(session_ops, "_sleep", sleep)
    notice = _halt(world, run_async)
    assert len(polls) == 3
    assert world.runs()[0] == ["stop", JOB]
    assert "could not be stopped" not in notice and sess.parked_job == ""


def test_a_halt_that_cannot_wait_out_a_bring_back_says_so(world, run_async):
    sess = world.sess
    sess.relaunch_in_flight = True             # never ends
    notice = _halt(world, run_async)
    assert world.runs() == []
    assert notice.endswith(
        "\n⚠️ The Claude Code background job of <b>x</b> could not be stopped, so the "
        "blocked work may still be running there.")
    polls = [c for c in world.calls if c == ("sleep", session_ops._HALT_WAIT_POLL)]
    assert len(polls) == int(session_ops._HALT_WAIT_SECONDS / session_ops._HALT_WAIT_POLL)


def test_a_halt_that_waits_out_a_releasing_bring_back_lets_nothing_out(world, run_async,
                                                                       monkeypatch):
    """Review rev-iter2-004: the gate's bring-back (which releases held
    messages) is in its launch when a safety halt comes; the relaunched
    claude's SessionStart arrives there too. The halt waits the bring-back
    out, and nothing held is typed: the halt drops it."""
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("held", 31)
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    world.bot._settle_card_text = AsyncMock(return_value=True)
    world.bot._mark_not_delivered = AsyncMock()
    inner = world._sleep
    tasks = {}
    done = asyncio.Event()

    async def sleep(seconds):
        await inner(seconds)
        if asyncio.current_task() is tasks.get("halt"):
            # The halt's wait lasts until the bring-back is over (its end,
            # where the release would go out, included): its 30 s budget
            # never runs out first, whatever the worker threads take.
            await done.wait()

    monkeypatch.setattr(session_ops, "_sleep", sleep)

    async def during_launch():
        tasks["halt"] = asyncio.ensure_future(
            world.bot._halt_for_safety(sess, "rm -rf outside the project"))
        await asyncio.sleep(0)         # the halt starts and waits
        await _session_start(recv)     # the relaunched claude is up

    world.during_launch = during_launch

    async def scenario():
        out = await world.bot._bring_back_core(sess)
        done.set()
        await tasks["halt"]
        return out

    assert run_async(scenario()).ok
    assert typed == []
    assert sess.pending_queue == [] and sess.bg_release_pending is False
    assert sess.bg_release_blocked is False


def test_two_halts_at_once_each_keep_the_release_blocked(world, run_async, monkeypatch):
    """Review 3 nit 2: two safety halts wait out the same bring-back; the
    first one's wait ends first, and the second still blocks any release
    until its own ends."""
    import contextlib
    sess = world.sess
    sess.relaunch_in_flight = True       # a bring-back runs throughout
    inner = world._sleep

    async def sleep(seconds):            # each poll lets the other halt run
        await inner(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(session_ops, "_sleep", sleep)

    async def scenario():
        first = asyncio.ensure_future(world.bot._halt_bring_back(sess))
        for _ in range(5):
            await asyncio.sleep(0)
        second = asyncio.ensure_future(world.bot._halt_bring_back(sess))
        await asyncio.sleep(0)
        assert sess.bg_release_blocks == 2
        await first                      # its wait ran out first
        sess.arm_bg_release()            # the bring-back's release, armed again
        seen = (sess.bg_release_blocked, sess.claim_bg_release())
        second.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await second
        return seen

    assert run_async(scenario()) == (True, False)
    assert sess.bg_release_blocks == 0 and sess.bg_release_blocked is False


def test_a_failed_bring_back_keeps_an_earlier_release_pending(world, run_async):
    """The nit of review 2: a bring-back that fails after arming undoes
    only its own arming; a release an earlier bring-back left pending (a
    re-park within its backstop) is still pending."""
    sess = world.sess
    sess.arm_bg_release()               # the earlier bring-back's
    earlier = sess.bg_release_armed_at
    world.launch_ok = False
    assert run_async(world.bot._bring_back_core(sess)).reason == "launch_failed"
    assert (sess.bg_release_pending, sess.bg_release_armed_at) == (True, earlier)


def test_a_release_event_while_the_bring_back_runs_is_kept_for_its_end(world, run_async,
                                                                      monkeypatch):
    """``bg_release`` reaching notify while the bring-back still runs is
    not dropped: it is remembered, and nothing is typed yet."""
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.queue_prompt("held", 31)
    sess.arm_bg_release()
    sess.relaunch_in_flight = True
    run_async(world.bot.notify(sess, "bg_release", {}))
    assert typed == [] and sess.bg_start_seen is True and sess.bg_release_pending is True


def test_the_backstop_waits_while_a_bring_back_runs(world, run_async, monkeypatch):
    sess = world.sess
    typed = _drainable(world.bot, monkeypatch)
    sess.parked_job = ""
    world.transcript(X, {"type": "user"})      # nothing parks it again meanwhile
    sess.queue_prompt("held", 31)
    sess.arm_bg_release()
    sess.relaunch_in_flight = True
    sess.bg_released_at = time.monotonic() - (claude_jobs.RELEASE_BACKSTOP + 1)
    events = []

    async def notify(s, event, ctx):
        events.append(event)
        await world.bot.notify(s, event, ctx)

    monitor = SessionMonitor(world.bot.registry, notify)
    run_async(monitor._check_parked_tick(NAME, sess))
    assert "bg_release" not in events, "the backstop does not fire mid bring-back"
    assert typed == [] and sess.bg_release_pending is True
