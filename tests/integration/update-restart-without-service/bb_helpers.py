"""Black-box harness for the ``test_bb_*`` files (written from design.md
and entrypoints.md only).

Every boundary is faked through the seams entrypoints.md names:
``self_update._run_command`` (the restart probe, the update job's ``-I``
probe, ``systemctl show``, ``systemd-run``, the installer),
``self_update._http_get`` (PyPI), ``self_update._PROC_SELF_CGROUP`` /
``_DOCKERENV_PATH`` / ``_CONTAINERENV_PATH`` / ``_system`` (the plan), and
``self_restart._execve`` / ``_popen`` / ``_kill`` (recorders, never the
real calls). The launch is captured through the public
``self_restart.capture_launch()`` after ``sys.orig_argv``/``sys.argv``
are set to a daemon's launch, and the daemon's stop is the public
``self_restart.attach``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

from aipager import claude_resolve, config, install_source, self_restart, self_update
from aipager.install_source import InstallSource
from aipager.state import SessionRegistry, Status, TrackedSession

UNIT_CGROUP = "/user.slice/user-1000.slice/user@1000.service/app.slice/aipager.service"
LAUNCH_MARK = "AIPAGER_BB_LAUNCH_MARK"


def owner_id() -> int:
    """The personal-mode operator (tests/conftest.py pins it)."""
    return int(config.CHAT_ID)


class BBEnv:
    def __init__(self, monkeypatch, tmp_path, mk_bot):
        self.mp = monkeypatch
        self.tmp = tmp_path
        self.registry = SessionRegistry()
        self.bot = mk_bot(self.registry)
        self.chat_id = owner_id()
        self.calls: list[list[str]] = []
        self.probe_envs: list[dict] = []
        self.running = "1.0.1"
        self.disk_metadata = "1.0.1"          # in-process installed_version()
        self.pypi_latest: str | None = "1.1.0"
        self.restart_probe: tuple[str | None, bool] = ("1.1.0", True)
        self.install_probe: tuple[str | None, bool] = ("1.1.0", True)
        self.upgrade_rc = 0
        self.under_unit = False
        self.killmode = "process"
        self.main_pid = os.getpid()
        self.schedule_rc = 0
        self.claude_found = False
        self.claude_version = "2.1.281"
        self.claude_latest: str | None = None
        self.source = InstallSource(
            kind="uv", prefix=str(tmp_path / "uv" / "aipager"),
            python=str(tmp_path / "uv" / "aipager" / "bin" / "python"),
            origin="index", upgradable=True)
        # recorders
        self.stops: list[str] = []
        self.execs: list[tuple] = []
        self.popens: list[tuple] = []
        self.kills: list[tuple] = []
        self.order: list[str] = []

        mp = monkeypatch
        mp.setattr(self_update, "_run_command", self._run)
        mp.setattr(self_update, "_http_get", self._get)
        mp.setattr(self_update, "running_version", lambda: self.running)
        mp.setattr(self_update, "installed_version", lambda: self.disk_metadata)
        mp.setattr(install_source, "detect_install_source", lambda **k: self.source)
        mp.setattr(install_source, "resolve_tool", lambda n: f"/abs/bin/{n}")
        mp.setattr(self_update, "_system", lambda: "Linux")
        mp.setattr(claude_resolve, "try_resolve_claude_binary", self._resolve)
        from aipager import claude_paths
        claude_paths.locate({}).global_config.parent.mkdir(parents=True, exist_ok=True)
        claude_paths.locate({}).global_config.write_text(json.dumps({"installMethod": "native"}))
        for name, value in (
            ("GATE_POLL_SECONDS", 0.01), ("GATE_SETTLE_POLLS", 2),
            ("GATE_MAX_WAIT_SECONDS", 30), ("GATE_DECISION_MAX_SECONDS", 30),
            ("PROGRESS_EDIT_MIN_SECONDS", 0.0), ("MARKER_SETTLE_SECONDS", 0),
            ("STATUS_CACHE_SECONDS", 0),
        ):
            if hasattr(self_update, name):
                mp.setattr(self_update, name, value)
        from aipager.bot import update_flow
        mp.setattr(update_flow, "RESTART_WATCHDOG_SECONDS", 3600.0)
        self.cgroup = tmp_path / "bb-proc-self-cgroup"
        self._write_cgroup()
        mp.setattr(self_update, "_PROC_SELF_CGROUP", str(self.cgroup))
        mp.setattr(self_update, "_CGROUP_ROOT", str(tmp_path / "bb-no-cgroupfs"))
        self.dockerenv = tmp_path / "bb-dockerenv"
        self.containerenv = tmp_path / "bb-containerenv"
        mp.setattr(self_update, "_DOCKERENV_PATH", str(self.dockerenv))
        mp.setattr(self_update, "_CONTAINERENV_PATH", str(self.containerenv))
        mp.setattr(self_restart, "_execve", self._fake_execve)
        mp.setattr(self_restart, "_popen", self._fake_popen)
        mp.setattr(self_restart, "_kill", self._fake_kill)
        self.exec_raises: BaseException | None = None
        self.popen_raises: BaseException | None = None
        self.popen_poll = None                 # None: the waiter keeps running

        self.edits: list[dict] = []
        self.message = self.mk_message(self.chat_id)
        self.sent: list[dict] = []
        self.bot._app.bot.send_message = AsyncMock(side_effect=self._on_send)

    # ---- the launch --------------------------------------------------------

    def launch_as_daemon(self, *, script: str | None = None, cwd=None):
        """Make this process look like ``aipager start`` and capture it
        through the public ``capture_launch``."""
        self.mp.setenv(LAUNCH_MARK, "bb-launch-env")
        if script is None:
            self.mp.setattr(sys, "orig_argv", [sys.executable, "-m", "aipager", "start"])
            self.mp.setattr(sys, "argv", [os.path.join("x", "aipager", "__main__.py"), "start"])
        else:
            self.mp.setattr(sys, "orig_argv", [sys.executable, script, "start"])
            self.mp.setattr(sys, "argv", [script, "start"])
        if cwd is not None:
            self.mp.chdir(cwd)
        self_restart.capture_launch()

    def attach_stop(self):
        self_restart.attach(self._stop)

    def _stop(self):
        self.order.append("stop")
        self.stops.append(self.last_text())

    def _fake_execve(self, exe, argv, envp):
        self.order.append("exec")
        self.execs.append((exe, list(argv), dict(envp)))
        if self.exec_raises is not None:
            raise self.exec_raises

    def _fake_popen(self, *args, **kwargs):
        self.order.append("popen")
        out = kwargs.get("stdout")
        fd = out if isinstance(out, int) else getattr(out, "fileno", lambda: None)()
        if isinstance(fd, int) and fd > 2:
            try:
                kwargs = {**kwargs, "_stdout_path": os.readlink(f"/proc/self/fd/{fd}")}
            except OSError:
                pass
        self.popens.append((args, kwargs))
        if self.popen_raises is not None:
            raise self.popen_raises
        proc = MagicMock()
        proc.pid = 424242
        proc.poll = MagicMock(return_value=self.popen_poll)
        proc.returncode = self.popen_poll
        return proc

    def _fake_kill(self, pid, sig):
        self.order.append("kill")
        self.kills.append((pid, sig))

    # ---- setup -------------------------------------------------------------

    def _write_cgroup(self):
        self.cgroup.write_text(f"0::{UNIT_CGROUP}\n" if self.under_unit else "0::/\n")

    def set_under_unit(self, value: bool):
        self.under_unit = value
        self._write_cgroup()

    def mk_message(self, chat_id, message_id=5150):
        msg = MagicMock()
        msg.chat = MagicMock()
        msg.chat.id = chat_id
        msg.chat_id = chat_id
        msg.message_id = message_id

        async def _edit(text, **kw):
            self.edits.append({"text": text, **kw})
            return msg
        msg.edit_text = AsyncMock(side_effect=_edit)
        return msg

    async def _on_send(self, *args, **kw):
        chat_id = kw.get("chat_id", args[0] if args else None)
        text = kw.get("text", args[1] if len(args) > 1 else None)
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return self.mk_message(chat_id, 7000 + len(self.sent))

    def add_session(self, name="claude-web", label="web", status=Status.IDLE,
                    **fields) -> TrackedSession:
        sess = TrackedSession(name=name, label=label, status=status)
        sess.scope_chat_id = self.chat_id
        for k, v in fields.items():
            setattr(sess, k, v)
        self.registry._sessions[name] = sess
        return sess

    # ---- fakes -------------------------------------------------------------

    def _resolve(self, *a, **k):
        if not self.claude_found:
            return None
        path = "/home/alice/.local/bin/claude"
        inst = claude_resolve.ClaudeInstall(path=path, realpath=path,
                                            version=self.claude_version)
        return claude_resolve.ResolvedClaude(chosen=inst)

    def _get(self, url, *, timeout, max_bytes):
        if url == self_update.PYPI_URL:
            if self.pypi_latest is None:
                return None
            return json.dumps({"info": {"version": self.pypi_latest}}).encode()
        if url.startswith(self_update.CLAUDE_RELEASES_BASE):
            return self.claude_latest.encode() if self.claude_latest else None
        return None

    def _run(self, argv, *, timeout, env=None, capture=True):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        R = self_update.CommandResult
        if argv[1:3] == ["--user", "show"]:
            return R(0, f"MainPID={self.main_pid}\nKillMode={self.killmode}\n"
                        f"ControlGroup={UNIT_CGROUP}\n", False, None)
        if argv[1:3] == ["--user", "stop"]:
            return R(0, "", False, None)
        if argv[0].endswith("systemd-run"):
            return R(self.schedule_rc, "" if self.schedule_rc == 0 else "boom", False, None)
        if "-I" in argv:
            version, ok = self.install_probe
            return (R(0, f"AIPAGER_VERSION={version}", False, None) if ok
                    else R(1, "ImportError: cannot import name 'x'", False, None))
        if len(argv) == 3 and argv[1] == "-c":
            self.probe_envs.append(dict(env or {}))
            version, ok = self.restart_probe
            return (R(0, f"AIPAGER_VERSION={version}", False, None) if ok
                    else R(1, "Traceback\nImportError: cannot import name 'x'", False, None))
        if argv[-1] == "--version":
            return R(0, f"{self.claude_version} (Claude Code)", False, None)
        if argv[0].startswith("/abs/bin/"):
            return R(self.upgrade_rc, "upgraded", False, None)
        raise AssertionError(f"unexpected spawn {argv!r}")

    # ---- inspection --------------------------------------------------------

    @property
    def manager(self):
        return self.bot.updates

    def restart_probe_calls(self):
        return [c for c in self.calls if len(c) == 3 and c[1] == "-c"]

    def schedule_calls(self):
        return [c for c in self.calls if c[0].endswith("systemd-run")]

    def last_text(self) -> str:
        return self.edits[-1]["text"] if self.edits else ""

    def texts(self) -> list[str]:
        return [e["text"] for e in self.edits]

    def last_markup_data(self) -> list[str]:
        if not self.edits:
            return []
        return markup_data(self.edits[-1].get("reply_markup"))

    async def start_restart(self, user_id=None):
        return await self.manager.start_restart(
            chat_id=self.chat_id, user_id=owner_id() if user_id is None else user_id,
            origin="chat", status_message=self.message)

    async def finish(self, timeout=10.0):
        task = self.manager._job_task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except asyncio.CancelledError:
                if not task.cancelled():   # a shutdown cancels the job task
                    raise

    async def until(self, predicate, timeout=5.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                raise AssertionError("condition never became true; job="
                                     f"{self.manager.snapshot()} text={self.last_text()!r}")
            await asyncio.sleep(0.01)

    async def drain(self, rounds=20):
        for _ in range(rounds):
            await asyncio.sleep(0.01)

    async def close(self):
        for task in list(self.manager._tasks):
            task.cancel()
        await asyncio.sleep(0)
        try:
            self.manager._lock.release()
        except Exception:  # noqa: BLE001
            pass


def waiter_ok(res):
    """``spawn_waiter``'s ok flag: entrypoints.md documents a pair."""
    try:
        return res[0]
    except TypeError:
        return getattr(res, "ok")


def markup_data(markup) -> list[str]:
    if markup is None:
        return []
    rows = getattr(markup, "inline_keyboard", None)
    if rows is None and isinstance(markup, (list, tuple)):
        rows = markup
    out = []
    for row in rows or []:
        for b in row:
            out.append(getattr(b, "callback_data", None))
    return out


def run_scenario(env: BBEnv, coro_fn):
    async def _wrapped():
        try:
            return await coro_fn()
        finally:
            await env.close()
            # What the scenario started and left running (the real
            # monitor_tick's pinned-card task) ends here, not at loop close.
            rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in rest:
                t.cancel()
            await asyncio.gather(*rest, return_exceptions=True)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_wrapped())
    finally:
        loop.close()


def tap(env: BBEnv, data: str, user_id: int, chat_id: int):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = env.mk_message(chat_id, 6100)
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, query


def popups(query) -> list[str]:
    out = []
    for c in query.answer.await_args_list:
        if c.args:
            out.append(c.args[0])
        elif "text" in c.kwargs:
            out.append(c.kwargs["text"])
    return out

