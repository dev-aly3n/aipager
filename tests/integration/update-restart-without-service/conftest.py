"""Scenario harness: finish a self-update without a service unit.

``env`` fakes every boundary through the seams only:
``self_update._run_command`` answers the installer, ``systemctl show``,
``systemd-run``, the post-install probe (``-I``) and the restart probe
(``-c``, no ``-I``); ``self_update._http_get`` answers PyPI;
``self_restart._execve``/``_popen`` record instead of replacing pytest.
The restart plan runs FOR REAL against a tmp ``/proc/self/cgroup``, tmp
container markers and a faked ``systemctl show``. Nothing here can run an
installer, reach systemd, exec or respawn the test process.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_resolve, install_source, self_restart, self_update
from aipager.bot import update_flow
from aipager.install_source import InstallSource
from aipager.state import SessionRegistry, Status, TrackedSession

OPERATOR_ID = 256113222  # tests/conftest.py pins config.CHAT_ID to this
UNIT_CGROUP = "/user.slice/user-1000.slice/user@1000.service/app.slice/aipager.service"


class Env:
    def __init__(self, monkeypatch, tmp_path, mk_bot):
        self.mp = monkeypatch
        self.tmp = tmp_path
        self.registry = SessionRegistry()
        self.bot = mk_bot(self.registry)
        self.chat_id = OPERATOR_ID
        self.calls: list[list[str]] = []
        self.probe_envs: list[dict] = []
        # aipager
        self.running = "1.0.1"
        self.pypi_latest: str | None = "1.1.0"
        self.install_probe = ("1.1.0", True)      # the update job's (-I) probe
        self.restart_probe = ("1.1.0", True)      # what a restart would run
        self.restart_probes: list[tuple[str | None, bool]] | None = None
        self.upgrade_rc = 0
        self.source = InstallSource(
            kind="uv", prefix=str(tmp_path / "uv" / "tools" / "aipager"),
            python=str(tmp_path / "uv" / "tools" / "aipager" / "bin" / "python"),
            origin="index", upgradable=True)
        # how the daemon runs
        self.under_unit = False
        self.killmode = "process"
        self.main_pid = os.getpid()
        self.schedule_rc = 0
        # in place
        self.stops: list[str] = []
        self.requests: list[dict] = []
        self.execs: list[tuple] = []

        mp = monkeypatch
        mp.setattr(self_update, "_run_command", self._run)
        mp.setattr(self_update, "_http_get", self._get)
        mp.setattr(self_update, "running_version", lambda: self.running)
        mp.setattr(self_update, "installed_version", lambda: self.running)
        mp.setattr(install_source, "detect_install_source", lambda **k: self.source)
        mp.setattr(install_source, "resolve_tool", lambda n: f"/abs/bin/{n}")
        mp.setattr(self_update, "_system", lambda: "Linux")
        mp.setattr(claude_resolve, "try_resolve_claude_binary", lambda *a, **k: None)
        from aipager import claude_bootstrap
        claude_bootstrap._CLAUDE_JSON.parent.mkdir(parents=True, exist_ok=True)
        claude_bootstrap._CLAUDE_JSON.write_text(json.dumps({"installMethod": "native"}))
        for name, value in (
            ("GATE_POLL_SECONDS", 0.01), ("GATE_SETTLE_POLLS", 2),
            ("GATE_MAX_WAIT_SECONDS", 30), ("GATE_DECISION_MAX_SECONDS", 30),
            ("PROGRESS_EDIT_MIN_SECONDS", 0.0), ("MARKER_SETTLE_SECONDS", 0),
            ("STATUS_CACHE_SECONDS", 0),
        ):
            mp.setattr(self_update, name, value)
        mp.setattr(update_flow, "RESTART_WATCHDOG_SECONDS", 3600.0)
        self._cgroup = tmp_path / "proc-self-cgroup"
        self._write_cgroup()
        mp.setattr(self_update, "_PROC_SELF_CGROUP", str(self._cgroup))
        mp.setattr(self_update, "_CGROUP_ROOT", str(tmp_path / "no-cgroupfs"))
        self.dockerenv = tmp_path / "dockerenv"
        self.containerenv = tmp_path / "containerenv"
        mp.setattr(self_update, "_DOCKERENV_PATH", str(self.dockerenv))
        mp.setattr(self_update, "_CONTAINERENV_PATH", str(self.containerenv))
        # The launch this "daemon" was started with.
        self_restart._launch = {"argv": [sys.executable, "-m", "aipager", "start"],
                                "env": {"HOME": "/home/alice", "PATH": "/usr/bin",
                                        "CLAUDE_TG_BOT_TOKEN": "123456:secret-token-value"},
                                "cwd": str(tmp_path)}
        self_restart.attach(self._stop)
        mp.setattr(self_restart, "_execve",
                   lambda exe, argv, envp: self.execs.append((exe, list(argv), dict(envp))))

        # One status message, recording every edit.
        self.edits: list[dict] = []
        self.message = self._mk_message(self.chat_id)
        self.sent: list[dict] = []
        self.bot._app.bot.send_message = AsyncMock(side_effect=self._on_send)

    # ---- setup ----------------------------------------------------------

    def _write_cgroup(self):
        self._cgroup.write_text(f"0::{UNIT_CGROUP}\n" if self.under_unit else "0::/\n")

    def set_under_unit(self, value: bool):
        self.under_unit = value
        self._write_cgroup()

    def _stop(self):
        # What the card said when the daemon was asked to stop.
        self.stops.append(self.last_text())

    def _mk_message(self, chat_id, message_id=4711):
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
        return self._mk_message(chat_id, 900 + len(self.sent))

    def add_session(self, name="claude-dev", label="dev", status=Status.IDLE,
                    **fields) -> TrackedSession:
        sess = TrackedSession(name=name, label=label, status=status)
        sess.scope_chat_id = self.chat_id
        for k, v in fields.items():
            setattr(sess, k, v)
        self.registry._sessions[name] = sess
        return sess

    # ---- fakes -------------------------------------------------------------

    def _get(self, url, *, timeout, max_bytes):
        if url == self_update.PYPI_URL:
            if self.pypi_latest is None:
                return None
            return json.dumps({"info": {"version": self.pypi_latest}}).encode()
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
        if len(argv) == 4 and argv[1:3] == ["-I", "-c"]:
            version, ok = self.install_probe
            return (R(0, f"AIPAGER_VERSION={version}", False, None) if ok
                    else R(1, "ImportError: cannot import name 'x'", False, None))
        if len(argv) == 3 and argv[1] == "-c" and "AIPAGER_VERSION" in argv[2]:
            self.probe_envs.append(dict(env or {}))
            version, ok = (self.restart_probes.pop(0) if self.restart_probes
                           else self.restart_probe)
            return (R(0, f"AIPAGER_VERSION={version}", False, None) if ok
                    else R(1, "ImportError: cannot import name 'x'", False, None))
        if argv[0].startswith("/abs/bin/"):
            return R(self.upgrade_rc, "upgraded", False, None)
        raise AssertionError(f"unexpected spawn {argv!r}")

    # ---- inspection ------------------------------------------------------------

    @property
    def manager(self) -> update_flow.UpdateManager:
        return self.bot.updates

    def restart_probe_calls(self):
        return [c for c in self.calls if len(c) == 3 and c[1] == "-c"]

    def schedule_calls(self):
        return [c for c in self.calls if c[0].endswith("systemd-run")]

    def last_text(self) -> str:
        return self.edits[-1]["text"] if self.edits else ""

    def all_texts(self) -> list[str]:
        return [e["text"] for e in self.edits]

    def last_markup_data(self) -> list[str]:
        if not self.edits:
            return []
        markup = self.edits[-1].get("reply_markup")
        if markup is None:
            return []
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    async def start_restart(self):
        return await self.manager.start_restart(chat_id=self.chat_id, user_id=OPERATOR_ID,
                                                origin="chat", status_message=self.message)

    async def start_update(self):
        return await self.manager.start("aipager", chat_id=self.chat_id,
                                        user_id=OPERATOR_ID, origin="chat",
                                        status_message=self.message)

    async def finish(self, timeout=10.0):
        task = self.manager._job_task
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout)

    async def until(self, predicate, timeout=5.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                raise AssertionError("condition never became true; job="
                                     f"{self.manager.snapshot()} text={self.last_text()!r}")
            await asyncio.sleep(0.01)

    async def close(self):
        for task in list(self.manager._tasks):
            task.cancel()
        await asyncio.sleep(0)
        self.manager._lock.release()


@pytest.fixture(autouse=True)
def _no_hook_notice_grace(monkeypatch):
    """The hooks notice tests here send at once; the grace before the
    first notice has its own tests (test_hook_notice_finished.py)."""
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_GRACE_SECONDS", 0.0)


@pytest.fixture
def env(monkeypatch, tmp_path, mk_bot):
    return Env(monkeypatch, tmp_path, mk_bot)


@pytest.fixture
def run(env):
    """Run an async scenario on a fresh loop and tidy the manager after."""
    def _run(coro_fn):
        async def _wrapped():
            try:
                return await coro_fn()
            finally:
                await env.close()
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_wrapped())
        finally:
            loop.close()
    return _run
