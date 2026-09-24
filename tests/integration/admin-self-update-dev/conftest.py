"""Developer scenario harness for the admin self-update (roadmap 8.36).

``env`` fakes every boundary the update path crosses, through the two
seams only: ``self_update._run_command`` answers the installer, ``claude
update``/``--version``, ``systemctl show``, ``systemd-run`` and the
version probe; ``self_update._http_get`` answers PyPI and the Claude
release channel. The restart plan runs FOR REAL against a tmp
``/proc/self/cgroup`` and a faked ``systemctl show`` — so a test sets
``env.killmode`` / ``env.under_unit`` rather than replacing the plan.

Nothing here can reach a real installer, systemd or the network: anything
the fake does not recognise raises, and conftest's own guard is only
superseded, never weakened.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_resolve, install_source, self_update
from aipager.bot import update_flow
from aipager.install_source import InstallSource
from aipager.state import SessionRegistry, Status, TrackedSession

OPERATOR_ID = 256113222  # tests/conftest.py pins config.CHAT_ID to this
UNIT_CGROUP = "/user.slice/user-1000.slice/user@1000.service/app.slice/aipager.service"


class Env:
    def __init__(self, monkeypatch, tmp_path, mk_bot):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.registry = SessionRegistry()
        self.bot = mk_bot(self.registry)
        self.chat_id = OPERATOR_ID
        self.calls: list[list[str]] = []
        self.fetches: list[str] = []
        # aipager
        self.running = "0.7.13"
        self.probe_version = "0.7.14"
        self.probe_ok = True
        self.pypi_latest: str | None = "0.7.14"
        self.upgrade_rc = 0
        self.upgrade_timeout = False
        self.upgrade_output = "Successfully installed aipager"
        self.upgrade_hook = None          # callable run inside the upgrade
        self.upgrade_release: threading.Event | None = None
        self.source = InstallSource(
            kind="pipx", prefix=str(tmp_path / "pipx" / "venvs" / "aipager"),
            python=str(tmp_path / "pipx" / "venvs" / "aipager" / "bin" / "python"),
            origin="index", upgradable=True)
        # restart
        self.under_unit = True
        self.killmode = "process"
        self.killmodes: list[str] | None = None   # per-call sequence
        self.main_pid = os.getpid()
        self.schedule_rc = 0
        self.schedule_hook = None
        self.timer_stop_rc = 0
        # claude
        self.claude_path = "/home/op/.local/bin/claude"
        self.claude_version = "2.1.281"
        self.claude_new = "2.1.290"
        self.claude_rc = 0
        self.claude_timeout = False
        self.claude_latest: str | None = "2.1.290"
        self.claude_found = True
        self.claude_output = "update output"

        mp = monkeypatch
        mp.setattr(self_update, "_run_command", self._run)
        mp.setattr(self_update, "_http_get", self._get)
        mp.setattr(self_update, "running_version", lambda: self.running)
        mp.setattr(self_update, "installed_version", lambda: self.running)
        mp.setattr(install_source, "detect_install_source", lambda **k: self.source)
        mp.setattr(install_source, "resolve_tool", lambda n: f"/abs/bin/{n}")
        mp.setattr(self_update, "_system", lambda: "Linux")
        mp.setattr(claude_resolve, "try_resolve_claude_binary", self._resolve)
        # A native Claude Code install (the redirected ~/.claude.json).
        from aipager import claude_bootstrap
        claude_bootstrap._CLAUDE_JSON.parent.mkdir(parents=True, exist_ok=True)
        claude_bootstrap._CLAUDE_JSON.write_text(json.dumps({"installMethod": "native"}))
        for name, value in (
            ("GATE_POLL_SECONDS", 0.01), ("GATE_SETTLE_POLLS", 2),
            ("GATE_MAX_WAIT_SECONDS", 30), ("GATE_DECISION_MAX_SECONDS", 30),
            ("PROGRESS_EDIT_MIN_SECONDS", 0.05), ("MARKER_SETTLE_SECONDS", 0),
            ("HEARTBEAT_GRANULARITY_SECONDS", 0.05),
        ):
            mp.setattr(self_update, name, value)
        mp.setattr(update_flow, "RESTART_WATCHDOG_SECONDS", 3600.0)
        self._cgroup = tmp_path / "proc-self-cgroup"
        self._write_cgroup()
        mp.setattr(self_update, "_PROC_SELF_CGROUP", str(self._cgroup))
        mp.setattr(self_update, "_CGROUP_ROOT", str(tmp_path / "no-cgroupfs"))

        # One status message, recording every edit.
        self.edits: list[dict] = []
        self.message = self._mk_message(self.chat_id)
        self.bot._app.bot.send_message = AsyncMock(side_effect=self._on_send)
        self.sent: list[dict] = []

    # ---- setup helpers ---------------------------------------------------

    def _write_cgroup(self):
        self._cgroup.write_text(f"0::{UNIT_CGROUP}\n" if self.under_unit else "0::/\n")

    def set_under_unit(self, value: bool):
        self.under_unit = value
        self._write_cgroup()

    def _mk_message(self, chat_id):
        msg = MagicMock()
        msg.chat = MagicMock()
        msg.chat.id = chat_id
        msg.chat_id = chat_id
        msg.message_id = 4711

        async def _edit(text, **kw):
            self.edits.append({"text": text, **kw})
            return msg
        msg.edit_text = AsyncMock(side_effect=_edit)
        return msg

    async def _on_send(self, *args, **kw):
        chat_id = kw.get("chat_id", args[0] if args else None)
        text = kw.get("text", args[1] if len(args) > 1 else None)
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return self.message

    def add_session(self, name="claude-dev", label="dev", status=Status.IDLE,
                    scope_chat_id=0, **fields) -> TrackedSession:
        sess = TrackedSession(name=name, label=label, status=status)
        sess.scope_chat_id = scope_chat_id
        for k, v in fields.items():
            setattr(sess, k, v)
        self.registry._sessions[name] = sess
        return sess

    # ---- fakes -----------------------------------------------------------

    def _resolve(self, *a, **k):
        if not self.claude_found:
            return None
        inst = claude_resolve.ClaudeInstall(path=self.claude_path,
                                            realpath=self.claude_path,
                                            version=self.claude_version)
        return claude_resolve.ResolvedClaude(chosen=inst)

    def _get(self, url, *, timeout, max_bytes):
        self.fetches.append(url)
        if url == self_update.PYPI_URL:
            if self.pypi_latest is None:
                return None
            return json.dumps({"info": {"version": self.pypi_latest}}).encode()
        if url.startswith(self_update.CLAUDE_RELEASES_BASE):
            return self.claude_latest.encode() if self.claude_latest else None
        raise AssertionError(f"unexpected fetch {url}")

    def _run(self, argv, *, timeout, env=None, capture=True):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        R = self_update.CommandResult
        if argv[1:3] == ["--user", "show"]:
            km = self.killmode
            if self.killmodes:
                km = self.killmodes.pop(0)
            return R(0, f"MainPID={self.main_pid}\nKillMode={km}\n"
                        f"ControlGroup={UNIT_CGROUP}\n", False, None)
        if argv[1:3] == ["--user", "stop"]:
            return R(self.timer_stop_rc, "", False, None)
        if argv[0].endswith("systemd-run"):
            if self.schedule_hook:
                self.schedule_hook(argv)
            return R(self.schedule_rc, "" if self.schedule_rc == 0 else "boom",
                     False, None)
        if "-I" in argv:
            if not self.probe_ok:
                return R(1, "ImportError: cannot import name 'x'", False, None)
            return R(0, f"AIPAGER_VERSION={self.probe_version}", False, None)
        if argv == [self.claude_path, "--version"]:
            return R(0, f"{self.claude_version} (Claude Code)", False, None)
        if argv == [self.claude_path, "update"]:
            if self.claude_timeout:
                return R(None, "downloading…", True, f"timed out after {timeout}s")
            if self.claude_rc == 0:
                self.claude_version = self.claude_new
            return R(self.claude_rc, self.claude_output, False, None)
        if argv[0].startswith("/abs/bin/") or argv[1:3] == ["-m", "pip"]:
            if self.upgrade_hook:
                self.upgrade_hook()
            if self.upgrade_release is not None:
                self.upgrade_release.wait(10)
            if self.upgrade_timeout:
                return R(None, "still resolving", True, f"timed out after {timeout}s")
            return R(self.upgrade_rc, self.upgrade_output, False, None)
        raise AssertionError(f"unexpected spawn {argv!r}")

    # ---- inspection --------------------------------------------------------

    @property
    def manager(self) -> update_flow.UpdateManager:
        return self.bot.updates

    def upgrade_calls(self):
        return [c for c in self.calls if c[0].startswith("/abs/bin/pipx")
                or c[0].startswith("/abs/bin/uv") or c[1:3] == ["-m", "pip"]]

    def schedule_calls(self):
        return [c for c in self.calls if c[0].endswith("systemd-run")]

    def timer_stop_calls(self):
        return [c for c in self.calls if c[1:3] == ["--user", "stop"]]

    def last_text(self) -> str:
        return self.edits[-1]["text"] if self.edits else ""

    def last_markup_data(self) -> list[str]:
        if not self.edits:
            return []
        markup = self.edits[-1].get("reply_markup")
        if markup is None:
            return []
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    async def start(self, kind="aipager", **kw):
        return await self.manager.start(kind, chat_id=self.chat_id,
                                        user_id=OPERATOR_ID, origin="chat",
                                        status_message=self.message, **kw)

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
