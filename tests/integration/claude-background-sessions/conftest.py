"""Fixtures for the black-box timeline of "Claude Code background
sessions" (ship/claude-background-sessions, T5.5).

One aipager daemon stack, wired the way ``cli/daemon.py`` wires it: a real
``TelegramBot`` (Telegram mocked), the ``HookReceiver`` feeding the bot's
``notify``, the ``SessionMonitor``, and ``_wire_parked_state`` (the parked
probe for ``inject``, the receiver's and the gate's ``parked_check``).
Datagrams go in through ``HookReceiver._on_datagram`` (the
``receiver``/``send_hook`` pattern of
``tests/integration/model-background-agent-jobs/conftest.py``).

Claude's side is a fake config folder built with
``tests/integration/claude-config-dir/_kit.py``'s kit: a folder outside the
(redirected) home, named to aipager through daemon.env's
``CLAUDE_CONFIG_DIR``, so ``claude_jobs.config_dir()`` resolves it the real
way. In it: ``sessions/<pid>.json`` (Claude's registry) and
``projects/<slug>/<id>.jsonl`` (transcripts).

Nothing real runs: ``claude stop``/``claude agents --json`` go through
``claude_jobs._create_subprocess_exec``; the pid checks through
``claude_jobs``' pid seams; every dtach call through ``inject``'s module
attributes; the waits through ``session_ops._sleep``; the bring-back the
message gate spawns through ``session_ops._spawn``. No socket, no real
home, no real claude or dtach.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

#: The session, as aipager names it, and its chat.
NAME = "claude-frontend"
LABEL = "frontend"
CHAT = -1001
#: The job ``←`` made, and the conversation it holds (its first 8
#: characters are the job's id), and the client's own conversation.
JOB = "ab12cd34"
JS = "ab12cd34-0000-4000-8000-000000000002"
X = "aaaaaaaa-0000-4000-8000-000000000001"
#: A spare Claude Code keeps warm for the next job (another job id).
SPARE_JOB = "ef56ab78"
SPARE_ID = "ef56ab78-0000-4000-8000-000000000003"
#: The session's own claude, the job's worker, the relaunched claude.
CLIENT, WORKER, RELAUNCHED = 4100, 4200, 4300
FAKE_CLAUDE = "/opt/fake/bin/claude"


def _load_kit():
    name = "_cfgdir_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent.parent / "claude-config-dir" / "_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _load_kit()


@pytest.fixture(autouse=True)
def _mock_rich_message(monkeypatch):
    """No rich-message call reaches the network (the suite-wide guard
    already refuses the transport; this makes the call succeed)."""
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", AsyncMock(return_value={}))


@pytest.fixture
def cc(tmp_path, monkeypatch):
    """claude-config-dir's ``cc``: the redirected home, a config folder
    outside it (``cc.x``), daemon.env."""
    from aipager import claude_paths, daemon_secrets

    for var in kit.SCRUB:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    home = Path(claude_paths.DEFAULT_GLOBAL_DIR)
    dot_claude = Path(claude_paths.DEFAULT_CONFIG_HOME)
    assert tmp_path in home.parents, "conftest must redirect the home folder"
    dot_claude.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    x = tmp_path / "elsewhere" / "cfg"
    x.mkdir(parents=True)
    work = tmp_path / "work" / "frontend"
    work.mkdir(parents=True)
    return kit.CC(tmp=tmp_path, home=home, dot_claude=dot_claude,
                  managed=Path(claude_paths.MANAGED_SETTINGS_DIR),
                  daemon_env=Path(daemon_secrets.DAEMON_ENV_PATH),
                  x=x, bin=bin_dir, work=work)


class Claude:
    """Claude Code's side, as files and a fake ``claude`` command.

    ``runs`` records each ``claude`` command line; ``alive`` is the set of
    pids that answer ``kill(pid, 0)``."""

    def __init__(self, cfg: Path, cwd: Path):
        self.cfg = cfg
        self.sessions = cfg / "sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.projects = cfg / "projects" / str(cwd).replace("/", "-")
        self.projects.mkdir(parents=True, exist_ok=True)
        self.alive: set[int] = set()
        self.runs: list[list[str]] = []
        self.stop_ends = {}            # job -> the pid its stop ends
        self._tick = 0

    def reg(self, pid: int, **fields) -> None:
        """``sessions/<pid>.json`` with a fresh mtime (the reader caches by
        (mtime, size), and ``busy``/``idle`` are as long)."""
        path = self.sessions / f"{pid}.json"
        kit.wjson(path, {"pid": pid, **fields})
        self._tick += 1
        ns = 1_700_000_000_000_000_000 + self._tick * 1_000_000
        os.utime(path, ns=(ns, ns))
        self.alive.add(pid)

    def unreg(self, pid: int) -> None:
        (self.sessions / f"{pid}.json").unlink(missing_ok=True)
        self.alive.discard(pid)

    def transcript(self, sid: str, *records) -> Path:
        path = self.projects / f"{sid}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        return path

    def append(self, sid: str, *records) -> None:
        with (self.projects / f"{sid}.jsonl").open("a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in records))

    async def exec(self, *argv, **_kw):
        args = list(argv[1:])
        self.runs.append(args)
        claude = self

        class Proc:
            returncode = None

            async def communicate(self, _stdin=None):
                if args[:1] == ["stop"]:
                    pid = claude.stop_ends.get(args[1])
                    if pid is not None:
                        claude.unreg(pid)
                    self.returncode = 0
                    return b"", b""
                self.returncode = 1          # no `claude agents` listing
                return b"", b""

            def kill(self):
                pass

        return Proc()


class Stack:
    """The daemon's parts, wired as cli/daemon.py wires them."""

    def __init__(self, mk_bot, monkeypatch, cc, run_async):
        from aipager import claude_jobs
        from aipager.bot import session_ops
        from aipager.cli import daemon
        from aipager.dtach import hook_receiver as hr
        from aipager.dtach import inject
        from aipager.session_monitor import SessionMonitor

        self.run = run_async
        self.ids = SimpleNamespace(
            NAME=NAME, LABEL=LABEL, CHAT=CHAT, JOB=JOB, JS=JS, X=X, SPARE_JOB=SPARE_JOB,
            SPARE_ID=SPARE_ID, CLIENT=CLIENT, WORKER=WORKER, RELAUNCHED=RELAUNCHED)
        self.cc = cc
        # aipager's runtime folder (conftest-redirected under tmp_path; /tmp
        # in a real install, which always exists).
        from aipager.dtach import bg_jobs
        bg_jobs.link_path(JOB).parent.mkdir(parents=True, exist_ok=True)
        cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
        self.claude = Claude(cc.x, cc.work)
        monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid in self.claude.alive)
        monkeypatch.setattr(claude_jobs, "_proc_start", lambda pid: None)
        monkeypatch.setattr(claude_jobs, "_dtach_pids", lambda name: [])
        monkeypatch.setattr(claude_jobs, "_pid_tagged", lambda pid, session: True)
        monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", self.claude.exec)
        monkeypatch.setattr(
            "aipager.claude_resolve.try_resolve_claude_binary",
            lambda: SimpleNamespace(chosen=SimpleNamespace(path=FAKE_CLAUDE)))
        # The waits and the gate's spawned bring-back.
        self.spawned: list = []
        monkeypatch.setattr(session_ops, "_sleep", self._sleep)
        monkeypatch.setattr(session_ops, "_spawn", self._spawn)
        monkeypatch.setattr(session_ops, "Path", MagicMock(
            return_value=MagicMock(is_socket=MagicMock(return_value=False))))
        # dtach: what is typed, killed and launched is kept.
        self.typed: list[str] = []
        self.dtach: list[tuple] = []
        self.live: set[str] = {NAME}
        monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(inject, "list_sessions", self._list_sessions)
        monkeypatch.setattr(inject, "send_text_and_enter", self._type)
        monkeypatch.setattr(inject, "send_keys", self._keys)
        monkeypatch.setattr(inject, "kill_session", self._kill)
        monkeypatch.setattr(inject, "launch_session", self._launch)
        # The bot, with Telegram mocked.
        bot = mk_bot()
        bot._app.bot = AsyncMock()
        bot._app.bot.send_message = AsyncMock(
            side_effect=lambda *a, **k: MagicMock(message_id=500 + len(self.sent())))
        bot._maybe_update_bot_name = AsyncMock()
        bot._update_bot_commands = AsyncMock()
        bot._mark_driver = MagicMock()
        bot._react = AsyncMock()
        bot._send_busy_and_animate = AsyncMock()
        bot._build_reply_context = MagicMock(return_value="")
        self.bot = bot
        self.registry = bot.registry
        self.events: list[str] = []

        async def notify(sess, event, context=None):
            self.events.append(event)
            await bot.notify(sess, event, context or {})
        self.receiver = hr.HookReceiver(self.registry, notify)
        self.monitor = SessionMonitor(self.registry, notify)
        daemon._wire_parked_state(self.registry, self.receiver, self.monitor, bot)
        self._n = 0

    # -- the seams --
    async def _sleep(self, seconds):
        self.dtach.append(("sleep", seconds))

    def _spawn(self, coro):
        self.spawned.append(coro)
        return MagicMock()

    async def _list_sessions(self):
        return sorted(self.live)

    async def _type(self, name, body):
        from aipager.dtach import inject
        if inject.is_parked(name):
            return False
        self.typed.append(body)
        return True

    async def _keys(self, name, key):
        self.dtach.append(("keys", key))
        return True

    async def _kill(self, name):
        self.dtach.append(("kill", name))
        self.claude.unreg(CLIENT)
        return True

    async def _launch(self, short_name, **kw):
        self.dtach.append(("launch", kw))
        return True, ""

    # -- driving it --
    def hook(self, **fields) -> None:
        """One datagram, as the hook binary sends it."""
        self._n += 1
        fields.setdefault("session", NAME)
        fields["n"] = self._n            # never a duplicate of the last one
        self.run(self.receiver._on_datagram(json.dumps(fields).encode()))

    def tick(self) -> None:
        self.run(self.monitor.tick())

    def run_spawned(self) -> None:
        while self.spawned:
            self.run(self.spawned.pop(0))

    def message(self, mk_update, text, *, msg_id, user_id=12345):
        update = mk_update(text, message_id=msg_id, user_id=user_id, chat_id=CHAT)
        ctx = MagicMock()
        ctx.bot = MagicMock(id=87654321)
        self.run(self.bot._handle_message(update, ctx))
        return update

    def tap(self, verb, *, user_id=12345):
        from aipager.bot import session_parity
        sess = self.registry.get(NAME)
        if verb in ("bgback", "bgforce") and sess is not None and sess.parked_job:
            # The button carries the job it was shown for (review rev-iter1-011).
            verb += sess.parked_job[:8]
        q = MagicMock()
        q.from_user = MagicMock(id=user_id)
        q.message = MagicMock(chat=MagicMock(id=CHAT), chat_id=CHAT, message_id=77)
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        u = MagicMock(callback_query=q, effective_chat=MagicMock(id=CHAT),
                      effective_user=MagicMock(id=user_id))
        assert self.run(session_parity.handle_callback(self.bot, u, q, NAME, verb)) is True
        return q

    def sent(self) -> list:
        return list(self.bot._app.bot.send_message.await_args_list)

    def texts(self) -> list[str]:
        return [c.kwargs.get("text") or (c.args[1] if len(c.args) > 1 else "")
                for c in self.sent()]

    def kinds(self) -> list[str]:
        return [d[0] for d in self.dtach if d[0] != "sleep"]

    def launches(self) -> list[dict]:
        return [d[1] for d in self.dtach if d[0] == "launch"]

    def close(self) -> None:
        for coro in self.spawned:
            if asyncio.iscoroutine(coro):
                coro.close()


@pytest.fixture
def stack(mk_bot, monkeypatch, cc, run_async):
    """The wired daemon, with one live session ``frontend`` whose own claude
    (pid ``CLIENT``) runs a turn a Telegram message started, on the
    conversation ``X``."""
    s = Stack(mk_bot, monkeypatch, cc, run_async)
    from aipager.state import Status
    sess = s.registry.get_or_create(NAME)
    sess.label = LABEL
    sess.scope_chat_id = CHAT
    sess.cwd = str(cc.work)
    sess.perm_mode = "auto"
    sess.claude_session_id = X
    sess.transcript_path = str(s.claude.transcript(X, {"type": "user"}))
    s.registry.transition(NAME, Status.BUSY)
    sess.busy_started_at = time.monotonic()
    s.registry.last_active_session = NAME
    s.claude.reg(CLIENT, kind="interactive", sessionId=X, status="busy")
    s.sess = sess
    yield s
    s.close()
