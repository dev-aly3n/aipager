"""Black-box plumbing for the admin self-update contract (roadmap 8.36).

Written against design.md "Success criteria" and entrypoints.md only.
Everything the flow touches outside the process goes through the seams
entrypoints.md names, and every one of them is faked by :class:`World`:

* ``self_update._run_command``   -- installers, ``claude update``,
  ``systemctl show``, ``systemd-run`` (argv recorded, never spawned);
* ``self_update._http_get``      -- PyPI / downloads.claude.ai / npm;
* ``self_update.running_version`` and ``probe_installed_version``;
* ``self_update._PROC_SELF_CGROUP`` / ``_CGROUP_ROOT`` / ``_PROC_ROOT``;
* ``install_source.detect_install_source`` (real function, keyword
  overrides) and ``install_source.resolve_tool``;
* ``claude_resolve.resolve_claude_binary`` (the resolver's own core, so
  ``try_resolve``/``refresh`` stay real).

Nothing in ``asyncio`` is patched. Time is shrunk through the module
constants entrypoints.md lists as patchable.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_resolve, install_source, self_update
from aipager.state import SessionRegistry, Status

OPERATOR = 256113222          # conftest pins config.CHAT_ID to this
DM = OPERATOR
STRANGER = 999_001
TERMINAL = {"done", "failed", "cancelled", "restart_scheduled"}

RUNNING = "0.7.13"
LATEST = "0.8.0"
CLAUDE_OLD = "2.1.250"
CLAUDE_NEW = "2.1.260"


@pytest.fixture
def run_async():
    """Loop-closing override: jobs spawn background tasks, and a leaked
    loop would leak them into the next test."""
    loops: list[asyncio.AbstractEventLoop] = []

    def _run(coro):
        loop = asyncio.new_event_loop()
        loops.append(loop)
        return loop.run_until_complete(coro)

    yield _run
    for loop in loops:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            loop.close()


class World:
    """The fake outside world, one knob per equivalence class."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.mp = monkeypatch
        self.calls: list[dict] = []
        self.urls: list[str] = []
        # aipager
        self.running = RUNNING
        self.latest_pypi: str | None = LATEST
        self.probe = (LATEST, True, None)
        self.upgrade_rc = 0
        self.upgrade_timed_out = False
        self.upgrade_output = "upgraded aipager"
        self.upgrade_hook = None          # callable run when the installer runs
        # claude
        self.claude_path: str | None = "/opt/fake/claude/bin/claude"
        self.claude_version = CLAUDE_OLD
        self.claude_after = CLAUDE_NEW
        self.claude_rc = 0
        self.claude_timed_out = False
        self.claude_latest: str | None = CLAUDE_NEW
        # systemd
        self.under_unit = True
        self.killmodes = ["process"]      # consumed per `systemctl show`; last repeats
        self.main_pid = os.getpid()
        self.control_group = "/user.slice/user-1000.slice/user@1000.service/app.slice/aipager.service"
        self.schedule_rc = 0
        # install
        self.source_kind = "pipx"
        self.origin = "index"             # index | local | editable
        self.local_path = "/src/checkouts/aipager-local"
        self.tool_missing = False

    # ---- seams -----------------------------------------------------------
    def install(self):
        mp = self.mp
        mp.setattr(self_update, "_run_command", self._run_command)
        mp.setattr(self_update, "_http_get", self._http_get)
        mp.setattr(self_update, "running_version", lambda: self.running)
        mp.setattr(self_update, "probe_installed_version",
                   lambda python: self.probe)
        cg = self.tmp / "proc-self-cgroup"
        mp.setattr(self_update, "_PROC_SELF_CGROUP", str(cg))
        mp.setattr(self_update, "_CGROUP_ROOT", str(self.tmp / "cgroot"))
        mp.setattr(self_update, "_PROC_ROOT", str(self.tmp / "procroot"))
        (self.tmp / "cgroot").mkdir(exist_ok=True)
        (self.tmp / "procroot").mkdir(exist_ok=True)
        self._write_cgroup()
        mp.setattr(install_source, "resolve_tool", self._resolve_tool)
        real_detect = install_source.detect_install_source
        mp.setattr(install_source, "detect_install_source",
                   lambda **kw: real_detect(**{**self._detect_kwargs(), **kw}))
        mp.setattr(claude_resolve, "resolve_claude_binary", self._resolve_claude)
        # Claude Code's own settings (redirected to tmp by the root conftest).
        from aipager import claude_bootstrap
        cj = claude_bootstrap._CLAUDE_JSON
        cj.parent.mkdir(parents=True, exist_ok=True)
        cj.write_text(json.dumps({"installMethod": "native"}))
        # Short, deterministic timings.
        for name, value in (("GATE_POLL_SECONDS", 0.01),
                            ("GATE_SETTLE_POLLS", 2),
                            ("GATE_MAX_WAIT_SECONDS", 30),
                            ("GATE_DECISION_MAX_SECONDS", 30),
                            ("PROGRESS_EDIT_MIN_SECONDS", 0),
                            ("MARKER_SETTLE_SECONDS", 0),
                            ("STATUS_CACHE_SECONDS", 0)):
            mp.setattr(self_update, name, value)
        return self

    def _write_cgroup(self):
        line = ("0::" + self.control_group) if self.under_unit else "0::/user.slice/session-2.scope"
        (self.tmp / "proc-self-cgroup").write_text(line + "\n")

    def set_under_unit(self, value: bool):
        self.under_unit = value
        self._write_cgroup()

    def _prefix(self):
        root = self.tmp / "installs"
        if self.source_kind == "pipx":
            p = root / "pipx" / "venvs" / "aipager"
            p.mkdir(parents=True, exist_ok=True)
            (p / "pipx_metadata.json").write_text("{}")
        elif self.source_kind == "uv":
            p = root / "uv" / "tools" / "aipager"
            p.mkdir(parents=True, exist_ok=True)
            (p / "uv-receipt.toml").write_text("[tool]\n")
        elif self.source_kind == "pip":
            p = root / "venv"
            p.mkdir(parents=True, exist_ok=True)
        else:
            raise AssertionError(self.source_kind)
        return p

    def _detect_kwargs(self):
        prefix = self._prefix()
        if self.origin == "editable":
            du = {"url": "file:///src/aipager", "dir_info": {"editable": True}}
        elif self.origin == "local":
            du = {"url": "file://" + self.local_path, "dir_info": {}}
        else:
            du = None
        return {
            "prefix": str(prefix),
            "executable": str(prefix / "bin" / "python"),
            "base_prefix": "/usr",
            "direct_url": du,
            "env": {"PATH": "/usr/bin:/bin"},
        }

    @property
    def prefix(self):
        return str(self._prefix())

    def _resolve_tool(self, name):
        if self.tool_missing:
            return None
        return f"/abs/tools/{name}"

    def _resolve_claude(self, *, force=False):
        if self.claude_path is None:
            raise claude_resolve.ClaudeNotFoundError("no claude")
        inst = claude_resolve.ClaudeInstall(
            path=self.claude_path, realpath=self.claude_path,
            version=self.claude_version)
        return claude_resolve.ResolvedClaude(chosen=inst)

    def _http_get(self, url, *, timeout, max_bytes):
        self.urls.append(url)
        if "pypi.org" in url:
            if self.latest_pypi is None:
                return None
            return json.dumps({"info": {"version": self.latest_pypi}}).encode()
        if "downloads.claude.ai" in url:
            return None if self.claude_latest is None else self.claude_latest.encode()
        if "npmjs" in url:
            if self.claude_latest is None:
                return None
            return json.dumps({"latest": self.claude_latest,
                               "stable": self.claude_latest,
                               "next": self.claude_latest}).encode()
        return None

    def _res(self, rc=0, out="", timed_out=False, error=None):
        return self_update.CommandResult(returncode=rc, output_tail=out,
                                         timed_out=timed_out, error=error)

    def _run_command(self, argv, *, timeout, env=None, capture=True):
        argv = [str(a) for a in argv]
        self.calls.append({"argv": argv, "timeout": timeout, "env": env})
        head = argv[0].rsplit("/", 1)[-1]
        if self.claude_path and argv[0] == self.claude_path:
            if argv[1:] == ["update"]:
                if self.claude_timed_out:
                    return self._res(None, "still downloading", True)
                if self.claude_rc:
                    return self._res(self.claude_rc, "update blew up")
                self.claude_version = self.claude_after
                return self._res(0, f"Successfully updated to {self.claude_after}")
            if "--version" in argv:
                return self._res(0, f"{self.claude_version} (Claude Code)\n")
            return self._res(1, "unexpected claude argv")
        if head == "systemctl" and "show" in argv:
            km = self.killmodes[0]
            if len(self.killmodes) > 1:
                self.killmodes.pop(0)
            return self._res(0, f"MainPID={self.main_pid}\nKillMode={km}\n"
                                f"ControlGroup={self.control_group}\n")
        if head == "systemd-run":
            return self._res(self.schedule_rc, "" if not self.schedule_rc else "Failed to start transient unit")
        if self.is_upgrade(argv):
            if self.upgrade_hook:
                self.upgrade_hook()
            if self.upgrade_timed_out:
                return self._res(None, "Collecting aipager ...", True)
            return self._res(self.upgrade_rc, self.upgrade_output)
        return self._res(127, "unexpected argv in fake world")

    # ---- observations ------------------------------------------------------
    @staticmethod
    def is_upgrade(argv):
        joined = " ".join(argv)
        return (("upgrade" in argv or "--upgrade" in argv)
                and "aipager" in joined and "systemctl" not in joined)

    def argvs(self):
        return [c["argv"] for c in self.calls]

    def upgrade_calls(self):
        return [c for c in self.calls if self.is_upgrade(c["argv"])]

    def schedule_calls(self):
        return [c for c in self.calls
                if c["argv"][0].rsplit("/", 1)[-1] == "systemd-run"]

    def claude_update_calls(self):
        return [c for c in self.calls if c["argv"][1:] == ["update"]]


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch).install()


def status_message(chat_id, message_id=700):
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat_id = chat_id
    msg.message_id = message_id
    msg.edit_text = AsyncMock(return_value=msg)
    msg.reply_text = AsyncMock(return_value=msg)
    return msg


def _strings(obj, out):
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, (list, tuple)):
        for o in obj:
            _strings(o, out)
    elif isinstance(obj, dict):
        for o in obj.values():
            _strings(o, out)
    elif hasattr(obj, "inline_keyboard"):
        for row in obj.inline_keyboard:
            for b in row:
                out.append(str(getattr(b, "text", "")))
                out.append(str(getattr(b, "callback_data", "") or ""))


def texts_of(*mocks) -> list[str]:
    """Every string argument awaited on the given Telegram doubles."""
    out: list[str] = []
    for m in mocks:
        for call in m.mock_calls:
            _strings(list(call.args), out)
            _strings(dict(call.kwargs), out)
    return out


def buttons_of(*mocks) -> list[tuple[str, str]]:
    """(text, callback_data) of every inline button in the LAST markup."""
    last = None
    for m in mocks:
        for call in m.mock_calls:
            mk = call.kwargs.get("reply_markup")
            if mk is not None and hasattr(mk, "inline_keyboard"):
                last = mk
    if last is None:
        return []
    return [(b.text, b.callback_data) for row in last.inline_keyboard for b in row]


def telegram_mocks(bot, msg=None, query=None):
    mocks = [bot._app.bot]
    if msg is not None:
        mocks.append(msg)
    if query is not None:
        mocks.append(query)
    return mocks


def all_text(bot, msg=None, query=None) -> str:
    return "\n".join(texts_of(*telegram_mocks(bot, msg, query)))


@pytest.fixture
def personal_bot(mk_bot):
    """Personal mode: no team, no scopes; the operator is config.CHAT_ID."""
    registry = SessionRegistry()
    bot = mk_bot(registry)
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **kw: status_message(kw.get("chat_id", DM), 801))
    bot._app.bot.edit_message_text = AsyncMock()
    bot._app.bot.edit_message_reply_markup = AsyncMock()
    return bot


def add_session(registry, label, *, status=Status.IDLE, chat_id=DM):
    s = registry.get_or_create(f"claude-{label}")
    s.label = label
    s.status = status
    s.scope_chat_id = chat_id
    return s


async def wait_for(pred, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


async def wait_phase(bot, phases, timeout=5.0):
    phases = {phases} if isinstance(phases, str) else set(phases)
    await wait_for(lambda: (bot.updates.snapshot() or {}).get("phase") in phases,
                   timeout)
    return (bot.updates.snapshot() or {}).get("phase")


async def run_job(bot, kind, *, chat_id=DM, user_id=OPERATOR, msg=None,
                  until=TERMINAL, timeout=5.0):
    msg = msg or status_message(chat_id)
    res = await bot.updates.start(kind, chat_id=chat_id, user_id=user_id,
                                  origin="chat", status_message=msg)
    assert res.ok, f"start refused: {res.error}"
    phase = await wait_phase(bot, until, timeout)
    return res, msg, phase


def lock_is_free() -> bool:
    lock = self_update.UpdateLock()
    ok = lock.try_acquire()
    if ok:
        lock.release()
    return ok


def python_exe():
    return sys.executable


# Captured at import time: pytest may import several modules named
# ``conftest`` (hyphenated scenario dirs are not importable packages), so a
# late ``sys.modules[__name__]`` lookup could hand back a sibling's.
_SELF = sys.modules[__name__]


@pytest.fixture
def h():
    """This module, so test files can reach the helpers above without
    importing a hyphenated package path."""
    return _SELF
