"""An isolated aipager daemon against the fake Bot API (test harness).

``TestInstance`` builds a throwaway root ``/tmp/apg-XXXXXXXX`` holding the
instance folder (``i``, ``AIPAGER_INSTANCE_DIR`` and the daemon's cwd), a
fake HOME (``h``, with the project folder ``h/proj``) and a shim folder
(``b``: ``aipager``, ``aipager-hook``, ``aipager-statusline`` and, in
stand-in mode, ``claude``), starts a :class:`FakeBotApiThread` on
loopback, writes the instance's config, and starts ``python -m aipager
start`` with an environment built from scratch. Every guard here is
about the operator's real install on the same machine:

- nothing is written outside the root (the daemon refuses to start in
  instance mode with the real HOME, and the env is asserted first);
- the Bot API base is the fake's loopback URL (asserted before start);
- the hooks Claude runs are this repo's (the shims are first on PATH,
  asserted from the instance's settings.json after start);
- teardown kills by PID only, and only a PID whose environment names
  this instance's folder exactly, or a dtach attached to one of its
  session sockets (see :func:`pid_references`).

Test ids are fake and never the operator's real chat id.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket as _socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

import yaml

from tests.e2e.fake_telegram import updates as U
from tests.e2e.fake_telegram.redaction import redact
from tests.e2e.fake_telegram.server import FakeBotApi, FakeBotApiThread

BOT_ID = 7000000001
BOT_USERNAME = "aipager_fake_bot"
FAKE_TOKEN = f"{BOT_ID}:AAFakeTokenForTheLocalTestServer0"
DM_ID = 900000001
GROUP_ID = -4000000001
SUPERGROUP_ID = -1004000000001
GROUP_TITLE = "aipager test group"
ALICE, BOB, CAROL, DAVE = 900000001, 900000002, 900000003, 900000004
#: The operator's real DM id. No test id may ever equal it.
REAL_CHAT_ID = 256113222

MEMBERS = {
    ALICE: ("alice", "owner"),
    BOB: ("bob", "user"),
    CAROL: ("carol", "read_only"),
    DAVE: ("dave", "admin"),
}

REPO = Path(__file__).resolve().parents[3]
STANDIN = Path(__file__).resolve().parent / "standin_claude.py"

_SUN_PATH_MAX = 107


def user(uid: int) -> dict:
    name, _role = MEMBERS[uid]
    return U.user(uid, name)


# ---------------------------------------------------------------------------
# Pure builders (unit-tested in tests/test_faketg_harness_tools.py).
# ---------------------------------------------------------------------------

def make_root() -> Path:
    """``/tmp/apg-XXXXXXXX``: never a ``/tmp/claude-*`` name (the real
    daemon's files), never under the real home."""
    root = Path(tempfile.mkdtemp(prefix="apg-", dir="/tmp"))
    assert not root.name.startswith("claude-"), root
    assert root.parent == Path("/tmp"), root
    real_home = os.path.realpath(os.path.expanduser("~" + _login_name()))
    assert not str(root).startswith(real_home + os.sep), root
    return root


def _login_name() -> str:
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def real_home() -> str:
    import pwd
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def build_config(claude_path: str, members: dict | None = None, *,
                 dm_id: int = DM_ID, group_id: int = GROUP_ID) -> dict:
    """The instance's aipager.yaml (schema v3): the owner's DM and the test
    group with alice=owner, bob=user, carol=read_only, dave=admin."""
    members = MEMBERS if members is None else members
    ids = {dm_id, group_id, *members}
    assert REAL_CHAT_ID not in ids and -REAL_CHAT_ID not in ids, (
        "a test id equals the operator's real chat id")
    owner_id = next(uid for uid, (_n, role) in members.items() if role == "owner")
    return {
        "schema_version": 3,
        "bot_token": FAKE_TOKEN,
        "default_mode": "ask",
        "claude_path": claude_path,
        "miniapp": {"enabled": False, "port": 8765, "public_url": ""},
        "scopes": [
            {"kind": "dm", "chat_id": dm_id, "label": "alice DM",
             "members": [{"id": owner_id, "label": members[owner_id][0],
                          "role": "owner"}]},
            {"kind": "group", "chat_id": group_id, "label": GROUP_TITLE,
             "members": [{"id": uid, "label": name, "role": role}
                         for uid, (name, role) in members.items()]},
        ],
    }


def shim_text(module_fn: str, python: str, repo: Path) -> str:
    mod, fn = module_fn.split(":")
    return ("#!/bin/sh\n"
            f"PYTHONPATH={_sh(str(repo))}; export PYTHONPATH\n"
            f"exec {_sh(python)} -c 'from {mod} import {fn}; {fn}()' \"$@\"\n")


def _sh(s: str) -> str:
    import shlex
    return shlex.quote(s)


SHIMS = {
    "aipager": "aipager.cli:main",
    "aipager-hook": "aipager.dtach.notify_hook:main",
    "aipager-statusline": "aipager.dtach.statusline_notify:main",
}


def write_shims(bin_dir: Path, python: str, repo: Path, claude_mode: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, target in SHIMS.items():
        p = bin_dir / name
        p.write_text(shim_text(target, python, repo))
        p.chmod(0o755)
    if claude_mode == "standin":
        p = bin_dir / "claude"
        p.write_text("#!/bin/sh\n"
                     f"PYTHONPATH={_sh(str(repo))}; export PYTHONPATH\n"
                     f"exec {_sh(python)} {_sh(str(STANDIN))} \"$@\"\n")
        p.chmod(0o755)


# ANTHROPIC_*: a real-mode session authenticates only from the token the
# harness copies into the instance's daemon.env, never from an API key,
# base URL or model setting in the operator's shell.
_STRIP_PREFIXES = ("CLAUDE", "ANTHROPIC_", "AIPAGER_", "MINIAPP_")
_STRIP_EXACT = {"OBSERVER_BOTS", "CREDENTIALS_DIRECTORY", "PYTHONPATH",
                "XDG_RUNTIME_DIR"}


def daemon_env(base_env: dict, *, root: Path, base_url: str, claude_bin: str,
               repo: Path, python: str, claude_mode: str) -> dict:
    """The daemon's environment, built from scratch: nothing of the
    operator's aipager, Claude or credential settings, the instance
    folder, the fake HOME, the shims first on PATH."""
    env = {k: v for k, v in base_env.items()
           if not k.startswith(_STRIP_PREFIXES) and k not in _STRIP_EXACT}
    bin_dir = root / "b"
    rest = [d for d in base_env.get("PATH", "/usr/bin:/bin").split(os.pathsep)
            if d and d != str(bin_dir)]
    if claude_mode == "standin":
        # No real claude anywhere on PATH in a plumbing run.
        rest = [d for d in rest if not os.access(os.path.join(d, "claude"), os.X_OK)]
    venv_bin = str(Path(python).parent)
    path = [str(bin_dir), venv_bin] + [d for d in rest if d != venv_bin]
    env.update({
        "HOME": str(root / "h"),
        "AIPAGER_INSTANCE_DIR": str(root / "i"),
        "AIPAGER_WORK_DIR": str(root / "h" / "proj"),
        "AIPAGER_TELEGRAM_API_BASE": base_url,
        "CLAUDE_TG_BOT_TOKEN": FAKE_TOKEN,
        "CLAUDE_TG_CHAT_ID": "",
        "OBSERVER_BOTS": "",
        "MINIAPP_ENABLED": "0",
        "PATH": os.pathsep.join(path),
        "PYTHONPATH": str(repo),
        "AIPAGER_CLAUDE_BIN": claude_bin,
        "AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS": "120",
        "PYTEST_CURRENT_TEST": "faketg-daemon",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def env_problems(env: dict, root: Path) -> list[str]:
    """Why *env* would not keep a daemon isolated in *root* (empty = ok).
    Checked before start on the env built, and after start on the
    running daemon's ``/proc/<pid>/environ``."""
    problems = []
    rh = real_home()
    home = env.get("HOME", "")
    inst = env.get("AIPAGER_INSTANCE_DIR", "")
    if home != str(root / "h"):
        problems.append(f"HOME is not the instance home: {home!r}")
    if inst != str(root / "i"):
        problems.append(f"AIPAGER_INSTANCE_DIR is not the instance folder: {inst!r}")
    for name, value in (("HOME", home), ("AIPAGER_INSTANCE_DIR", inst)):
        rv = os.path.realpath(value) if value else ""
        if rv == rh or rv.startswith(rh + os.sep):
            problems.append(f"{name} is under the real home")
    if str(root).startswith("/tmp/claude-") or Path(root).name.startswith("claude-"):
        problems.append("the root is a /tmp/claude-* name")
    path0 = (env.get("PATH") or "").split(os.pathsep)[0]
    if path0 != str(root / "b"):
        problems.append(f"the shim folder is not first on PATH: {path0!r}")
    for key in ("CLAUDE_TG_CHAT_ID", "OBSERVER_BOTS"):
        if env.get(key, None) != "":
            problems.append(f"{key} is not set to empty")
    if "CREDENTIALS_DIRECTORY" in env:
        problems.append("CREDENTIALS_DIRECTORY leaked into the daemon env")
    base = env.get("AIPAGER_TELEGRAM_API_BASE", "")
    if not re.fullmatch(r"http://127\.0\.0\.1:\d+", base):
        problems.append(f"the API base is not the fake's loopback URL: {base!r}")
    if env.get("MINIAPP_ENABLED") != "0":
        problems.append("the Mini App is not off")
    return problems


def assert_env_isolated(env: dict, root: Path) -> None:
    problems = env_problems(env, root)
    assert not problems, "daemon env not isolated: " + "; ".join(problems)


def assert_socket_lengths(inst_dir: Path, labels: list[str]) -> None:
    """Every socket the instance creates fits a Unix socket path."""
    paths = [str(inst_dir / "aipager.sock"),
             str(inst_dir / ("aipager-reply-" + "0" * 32 + ".sock"))]
    for label in labels:
        for suffix in (f"__g{abs(GROUP_ID)}", f"__g{abs(SUPERGROUP_ID)}", f"__d{DM_ID}"):
            paths.append(str(inst_dir / f"claude-dtach-{label}{suffix}.sock"))
    for p in paths:
        n = len(os.fsencode(p))
        assert n <= _SUN_PATH_MAX, f"socket path too long ({n} bytes): {p}"


# ---------------------------------------------------------------------------
# Process discovery and kill (PIDs only, never by pattern).
# ---------------------------------------------------------------------------

def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


_SESSION_SOCK_RE = re.compile(rb"claude-dtach-[^/\0]+\.sock")


def pid_references(pid: int, inst_dir: Path) -> bool:
    """True only for one of the instance's own processes.

    - Environment: an element exactly ``AIPAGER_INSTANCE_DIR=<inst_dir>``.
      The daemon has it and everything it spawns inherits it (dtach,
      claude, the hooks).
    - Command line: a ``dtach`` process (``argv[0]`` basename) with an
      argument that is a session socket directly inside *inst_dir*. These
      are the harness's own attaches (``screen()``, ``send_keys()``), which
      run with the test process's environment.

    A process that merely names the folder (``tail -f`` on the daemon log,
    an editor, a grep, an strace) is never ours: no substring match."""
    needle = os.fsencode(str(inst_dir))
    environ = _read(f"/proc/{pid}/environ").split(b"\0")
    if b"AIPAGER_INSTANCE_DIR=" + needle in environ:
        return True
    argv = _read(f"/proc/{pid}/cmdline").split(b"\0")
    if not argv or os.path.basename(argv[0]) != b"dtach":
        return False
    for arg in argv[1:]:
        folder, base = os.path.split(arg)
        if folder == needle and _SESSION_SOCK_RE.fullmatch(base):
            return True
    return False


def _own_pids() -> list[int]:
    uid = os.getuid()
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid != uid:
                continue
        except OSError:
            continue
        out.append(int(entry))
    return out


def pids_referencing(inst_dir: Path) -> list[int]:
    me = os.getpid()
    return [p for p in _own_pids() if p != me and pid_references(p, inst_dir)]


def kill_pid_if_ours(pid: int, inst_dir: Path, sig: int = signal.SIGTERM) -> bool:
    """Signal *pid* only if it still references *inst_dir*; never the
    test process. Returns whether a signal was sent."""
    if pid in (os.getpid(), os.getppid()) or pid <= 1:
        return False
    if not pid_references(pid, inst_dir):
        return False
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return False
    return True


def pids_with_cwd_under(root: Path) -> list[int]:
    """This uid's processes (not the test process) whose working directory
    is *root* or inside it. Report only, never used to kill: a process
    that dropped ``AIPAGER_INSTANCE_DIR`` from its environment is not
    provably ours, but it would make ``rmtree`` pull the folder out from
    under it, so teardown refuses and names it instead."""
    base = os.path.realpath(root)
    me = os.getpid()
    out = []
    for p in _own_pids():
        if p == me:
            continue
        try:
            cwd = os.readlink(f"/proc/{p}/cwd")
        except OSError:
            continue
        if cwd == base or cwd.startswith(base + os.sep):
            out.append(p)
    return out


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = _read(f"/proc/{pid}/stat").split(b") ")
    return not (len(st) > 1 and st[1][:1] == b"Z")


def kill_pids_referencing(inst_dir: Path, timeout: float = 10) -> list[int]:
    """SIGTERM then SIGKILL every process of ours that references the
    instance folder. Returns the PIDs found."""
    pids = pids_referencing(inst_dir)
    for p in pids:
        kill_pid_if_ours(p, inst_dir)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.1)
    for p in pids:
        if _alive(p):
            kill_pid_if_ours(p, inst_dir, signal.SIGKILL)
    return pids


def wait_until(cond: Callable[[], object], timeout: float, what: str,
               detail: Callable[[], str] | None = None, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while True:
        v = cond()
        if v:
            return v
        if time.monotonic() >= deadline:
            extra = ("\n" + detail()) if detail else ""
            raise AssertionError(redact(f"timed out after {timeout}s waiting for {what}{extra}"))
        time.sleep(interval)


# ---------------------------------------------------------------------------
# The instance.
# ---------------------------------------------------------------------------

class TestInstance:
    __test__ = False  # not a pytest class

    def __init__(self, repo: Path | None = None, claude_mode: str = "standin",
                 members: dict | None = None, labels: list[str] | None = None):
        assert claude_mode in ("real", "standin"), claude_mode
        self.repo = Path(repo or os.environ.get("AIPAGER_E2E_REPO") or REPO)
        self.claude_mode = claude_mode
        self.members = dict(MEMBERS if members is None else members)
        self.labels = labels or ["ft1", "ft2"]
        self.python = sys.executable
        self.root: Path | None = None
        self.fake_thread: FakeBotApiThread | None = None
        self.fake: FakeBotApi | None = None
        self.proc: subprocess.Popen | None = None
        self.daemon_pid: int | None = None
        self.env: dict = {}
        self._log_fh = None
        self.sessions: dict[str, dict] = {}
        self._transcripts: dict[str, list[str]] = {}

    # -- layout --------------------------------------------------------------

    @property
    def inst_dir(self) -> Path:
        return self.root / "i"

    @property
    def home(self) -> Path:
        return self.root / "h"

    @property
    def project(self) -> Path:
        return self.root / "h" / "proj"

    @property
    def bin_dir(self) -> Path:
        return self.root / "b"

    @property
    def yaml_path(self) -> Path:
        return self.home / ".config" / "aipager" / "aipager.yaml"

    @property
    def log_path(self) -> Path:
        return self.inst_dir / "daemon.log"

    @property
    def claude_bin(self) -> str:
        if self.claude_mode == "standin":
            return str(self.bin_dir / "claude")
        from tests.e2e import harness
        found = harness.claude_bin()
        assert found, "no real claude binary"
        return found

    def build(self) -> TestInstance:
        self.root = make_root()
        for d in (self.inst_dir, self.project, self.bin_dir,
                  self.home / ".config" / "aipager", self.home / ".claude"):
            d.mkdir(parents=True, exist_ok=True)
        assert_socket_lengths(self.inst_dir, self.labels)
        write_shims(self.bin_dir, self.python, self.repo, self.claude_mode)
        cfg = self.home / ".config" / "aipager"
        self.yaml_path.write_text(yaml.safe_dump(build_config(self.claude_bin, self.members),
                                                 sort_keys=False))
        (cfg / "config.env").write_text("# isolated aipager test instance\n")
        (cfg / "preferences.json").write_text(json.dumps({
            str(DM_ID): {"new_session_mode": "ask"},
            str(GROUP_ID): {"new_session_mode": "ask"},
        }))
        if self.claude_mode == "real":
            token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
            assert token, "real mode needs CLAUDE_CODE_OAUTH_TOKEN"
            denv = cfg / "daemon.env"
            fd = os.open(denv, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(f"CLAUDE_CODE_OAUTH_TOKEN={token}\n")
        (self.home / ".claude" / "settings.json").write_text(json.dumps(
            {"model": "haiku", "skipDangerousModePermissionPrompt": True}))
        (self.home / ".claude.json").write_text(json.dumps({
            "hasCompletedOnboarding": True, "theme": "dark",
            "bypassPermissionsModeAccepted": True,
            "projects": {str(self.project): {"hasTrustDialogAccepted": True}},
        }))
        cmds = self.project / ".claude" / "commands"
        cmds.mkdir(parents=True, exist_ok=True)
        (cmds / "ftcmd.md").write_text("Reply with exactly: FTCMD\n")
        self.fake_thread = FakeBotApiThread(FAKE_TOKEN, BOT_ID, BOT_USERNAME)
        base = self.fake_thread.start()
        self.fake = self.fake_thread.api
        self.fake.add_chat(DM_ID, "private", who=user(ALICE))
        self.fake.add_chat(GROUP_ID, "group", GROUP_TITLE)
        for uid in self.members:
            if uid != DM_ID:
                self.fake.add_chat(uid, "private", who=user(uid))
        self.env = daemon_env(dict(os.environ), root=self.root, base_url=base,
                              claude_bin=self.claude_bin, repo=self.repo,
                              python=self.python, claude_mode=self.claude_mode)
        assert_env_isolated(self.env, self.root)
        return self

    # -- daemon --------------------------------------------------------------

    def start(self, timeout: float = 90) -> TestInstance:
        assert self.root is not None and self.fake is not None, "build() first"
        from aipager import telegram_endpoint
        old = os.environ.get(telegram_endpoint.BASE_ENV)
        os.environ[telegram_endpoint.BASE_ENV] = self.env["AIPAGER_TELEGRAM_API_BASE"]
        try:
            assert telegram_endpoint.check() == []
            assert telegram_endpoint.api_base().startswith("http://127.0.0.1:")
        finally:
            if old is None:
                os.environ.pop(telegram_endpoint.BASE_ENV, None)
            else:
                os.environ[telegram_endpoint.BASE_ENV] = old
        assert_env_isolated(self.env, self.root)
        self._log_fh = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [self.python, "-m", "aipager", "start"], cwd=str(self.inst_dir),
            env=self.env, stdin=subprocess.DEVNULL, stdout=self._log_fh,
            stderr=subprocess.STDOUT, start_new_session=True)
        self.daemon_pid = self.proc.pid
        wait_until(lambda: (self.fake.polling_started.is_set()
                            and "AIPager running" in self.log_text())
                   or self.proc.poll() is not None,
                   timeout, "the daemon to poll the fake", self.log_tail)
        assert self.proc.poll() is None, "daemon exited:\n" + self.log_tail()
        return self

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:
            return ""

    def log_tail(self, n: int = 40) -> str:
        return redact("\n".join(self.log_text().splitlines()[-n:]))

    def log_mark(self) -> int:
        try:
            return self.log_path.stat().st_size
        except OSError:
            return 0

    def log_lines(self, since: int = 0) -> list[str]:
        try:
            with open(self.log_path, "rb") as f:
                f.seek(since)
                return f.read().decode(errors="replace").splitlines()
        except OSError:
            return []

    def wait_log(self, *needles: str, since: int = 0, timeout: float = 60) -> str:
        """The first log line after *since* containing every needle."""
        def _find():
            for line in self.log_lines(since):
                if all(n in line for n in needles):
                    return line
            return None
        return wait_until(_find, timeout, f"a daemon log line with {needles!r}",
                          lambda: self.log_tail(30))

    def sigusr1(self) -> None:
        """Live reload: SIGUSR1 to the TEST daemon's own PID only."""
        assert self.daemon_pid and pid_references(self.daemon_pid, self.inst_dir)
        os.kill(self.daemon_pid, signal.SIGUSR1)

    def rewrite_yaml(self, mutator: Callable[[dict], None]) -> None:
        data = yaml.safe_load(self.yaml_path.read_text())
        mutator(data)
        self.yaml_path.write_text(yaml.safe_dump(data, sort_keys=False))

    # -- isolation checks ----------------------------------------------------

    def proc_env(self) -> dict:
        raw = _read(f"/proc/{self.daemon_pid}/environ")
        out = {}
        for item in raw.split(b"\0"):
            if b"=" in item:
                k, _, v = item.partition(b"=")
                out[k.decode(errors="replace")] = v.decode(errors="replace")
        return out

    def assert_isolated(self) -> None:
        fake = self.fake
        assert fake.calls("getMe"), "the fake never saw getMe"
        assert fake.bad_token_calls == [], fake.bad_token_calls
        self.wait_log(f"connected as @{BOT_USERNAME}, will message chat {DM_ID}", timeout=5)
        sock = self.inst_dir / "aipager.sock"
        assert sock.exists() and stat.S_ISSOCK(sock.stat().st_mode), sock
        floor = self.inst_dir / f"claude-policy-.floor-{os.getuid()}.json"
        wait_until(floor.exists, 10, "the instance floor file")
        settings = json.loads((self.home / ".claude" / "settings.json").read_text())
        hook = str(self.bin_dir / "aipager-hook")
        events = settings.get("hooks") or {}
        assert events, "no hooks wired into the instance settings.json"
        for event, blocks in events.items():
            cmds = [h.get("command") for b in blocks for h in b.get("hooks", [])]
            assert cmds and all(c == hook for c in cmds), (event, cmds)
        assert (settings.get("statusLine") or {}).get("command") == \
            str(self.bin_dir / "aipager-statusline")
        penv = self.proc_env()
        assert_env_isolated(penv, self.root)
        assert os.readlink(f"/proc/{self.daemon_pid}/cwd") == str(self.inst_dir)
        assert "Mini App server" not in self.log_text()

    # -- sessions ------------------------------------------------------------

    def session_name(self, chat_id: int, label: str) -> str:
        kind = "d" if chat_id > 0 else "g"
        return f"claude-{label}__{kind}{abs(chat_id)}"

    def socket_for(self, name: str) -> Path:
        return self.inst_dir / f"claude-dtach-{name.removeprefix('claude-')}.sock"

    def live_sockets(self) -> list[Path]:
        return sorted(self.inst_dir.glob("claude-dtach-*.sock"))

    def new_session(self, chat_id: int, uid: int, label: str, timeout: float = 90) -> str:
        """``/new <label>`` from *uid* in *chat_id*, as a member would type
        it; waits for the socket and the session's IDLE."""
        assert len(self.live_sockets()) < 2, "at most 2 live sessions per daemon"
        since = self.log_mark()
        self.fake.inject_text(chat_id, user(uid), f"/new {label}")
        return self.wait_session(chat_id, label, since=since, by=uid, timeout=timeout)

    def wait_session(self, chat_id: int, label: str, *, since: int, by: int | None = None,
                     timeout: float = 90) -> str:
        """Wait for a session the daemon is launching: its socket, its
        IDLE, and Claude ready (the stand-in started, or a settle pause)."""
        name = self.session_name(chat_id, label)
        sock = self.socket_for(name)
        wait_until(sock.exists, timeout, f"the socket of {name}", self.log_tail)
        self.wait_log(f"[{label}]", "→ IDLE", since=since, timeout=timeout)
        if self.claude_mode == "real":
            time.sleep(3)
        else:
            wait_until(lambda: any(e.get("event") == "start" for e in self.standin_log(name)),
                       30, f"the stand-in of {name}")
        self.sessions[name] = {"chat_id": chat_id, "label": label, "by": by}
        assert len(self.live_sockets()) <= 2, "at most 2 live sessions per daemon"
        return name

    def kill_session(self, name: str, label: str, timeout: float = 30) -> None:
        """End one session from outside (its dtach, by the socket in its
        command line) and wait for the daemon to mark it GONE."""
        since = self.log_mark()
        needle = os.fsencode(str(self.socket_for(name)))
        for pid in _own_pids():
            if needle in _read(f"/proc/{pid}/cmdline"):
                kill_pid_if_ours(pid, self.inst_dir)
        self.wait_log(f"[{label}]", "→ GONE", since=since, timeout=timeout)

    def kill_sessions(self, timeout: float = 20) -> None:
        """End every live session of this instance (dtach by its socket)."""
        for sock in self.live_sockets():
            needle = os.fsencode(str(sock))
            for pid in _own_pids():
                if needle in _read(f"/proc/{pid}/cmdline"):
                    kill_pid_if_ours(pid, self.inst_dir)
        wait_until(lambda: not any(
            needle_in_any(os.fsencode(str(s))) for s in self.live_sockets()),
            timeout, "the sessions to end")
        for sock in self.live_sockets():
            try:
                sock.unlink()
            except OSError:
                pass

    def hold_tools(self, name: str) -> None:
        """Stand-in only: hold the session's next tool call (see
        standin_claude); a no-op with real Claude."""
        if self.claude_mode == "standin":
            (self.inst_dir / f"standin-hold-{name}").write_text("")

    def release_tools(self, name: str) -> None:
        (self.inst_dir / f"standin-hold-{name}").unlink(missing_ok=True)

    def send_keys(self, name: str, data: bytes) -> None:
        """Type *data* into this instance's session *name* (``dtach -p`` on
        the instance socket; never through the test process's own
        ``aipager.dtach.inject``, whose socket folder is ``/tmp``)."""
        sock = self.socket_for(name)
        assert sock.exists() and str(sock).startswith(str(self.inst_dir) + os.sep), sock
        dtach = shutil.which("dtach") or "/usr/bin/dtach"
        subprocess.run([dtach, "-p", str(sock)], input=data, timeout=10, check=True)

    def standin_log(self, name: str) -> list[dict]:
        p = self.inst_dir / f"standin-{name}.jsonl"
        try:
            return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        except (OSError, ValueError):
            return []

    def prompts_seen(self, name: str) -> list[str]:
        """Prompts the session's Claude received, in order.

        Stand-in: its own event log. Real Claude: the session's own
        transcripts (every ``transcript_path`` the instance registry has
        named for *name*, in the order first seen, so an Auto restart's
        new transcript adds to the list instead of replacing it), read
        with :func:`transcript_prompts`. Never every transcript of the
        project: two sessions share the project folder."""
        if self.claude_mode == "standin":
            return [e["prompt"] for e in self.standin_log(name) if e.get("event") == "prompt"]
        paths = self._transcripts.setdefault(name, [])
        current = self._registry_transcript(name)
        if current and current not in paths:
            paths.append(current)
        out: list[str] = []
        for path in paths:
            try:
                out += transcript_prompts(Path(path).read_text(errors="replace").splitlines())
            except OSError:
                continue
        return out

    def _registry_transcript(self, name: str) -> str:
        try:
            data = json.loads((self.home / ".claude" / "aipager-sessions.json").read_text())
            return str(data["sessions"][name].get("transcript_path") or "")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return ""

    def notes(self, name: str) -> list[dict]:
        """The session's outstanding notes (not yet picked up)."""
        d = self.inst_dir / f"claude-notes-{name}"
        out = []
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                pass
        return out

    def file_in_project(self, name: str) -> Path:
        return self.project / name

    def screen(self, name: str, seconds: float = 2.0) -> str:
        """The session's visible screen, redacted (diagnostics only).

        Attaches with dtach on a private pty, asks for a redraw, reads for
        *seconds* and kills the attach by PID. Nothing is ever written to
        the pty, so not a single key reaches the session."""
        import pty
        import select
        sock = self.socket_for(name)
        dtach = shutil.which("dtach") or "/usr/bin/dtach"
        master, slave = pty.openpty()
        try:
            p = subprocess.Popen([dtach, "-a", str(sock), "-E", "-r", "winch", "-z"],
                                 stdin=slave, stdout=slave, stderr=slave,
                                 start_new_session=True)
        except OSError:
            os.close(master)
            os.close(slave)
            return ""
        chunks = []
        deadline = time.monotonic() + seconds
        try:
            while time.monotonic() < deadline:
                r, _, _ = select.select([master], [], [], 0.2)
                if r:
                    try:
                        chunks.append(os.read(master, 65536))
                    except OSError:
                        break
        finally:
            kill_pid_if_ours(p.pid, self.inst_dir, signal.SIGKILL)
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                pass
            os.close(master)
            os.close(slave)
        text = b"".join(chunks).decode(errors="replace")
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]", "", text)
        return redact("\n".join(text.replace("\r", "\n").splitlines()[-25:]))

    # -- teardown ------------------------------------------------------------

    def stop(self) -> None:
        """Idempotent; kills by PID only. Always runs every step."""
        errors: list[str] = []
        if self.proc is not None and self.proc.poll() is None:
            if pid_references(self.proc.pid, self.inst_dir):
                try:
                    os.kill(self.proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                kill_pid_if_ours(self.proc.pid, self.inst_dir, signal.SIGKILL)
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    errors.append("daemon did not die")
        if self.root is not None:
            try:
                kill_pids_referencing(self.inst_dir)
            except Exception as e:  # noqa: BLE001
                errors.append(f"sweep: {e!r}")
        if self.fake_thread is not None:
            try:
                self.fake_thread.stop()
            except Exception as e:  # noqa: BLE001
                errors.append(f"fake: {e!r}")
            self.fake_thread = None
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None
        keep = os.environ.get("AIPAGER_E2E_FAKETG_KEEP_LOG", "").strip()
        if keep and self.root is not None and self.log_path.exists():
            # Debugging aid: a redacted copy of the daemon log.
            os.makedirs(keep, exist_ok=True)
            Path(keep, f"{self.root.name}-daemon.log").write_text(redact(self.log_text()))
        if self.root is not None and self.root.exists():
            denv = self.home / ".config" / "aipager" / "daemon.env"
            try:
                denv.unlink()
            except OSError:
                pass
            left = pids_referencing(self.inst_dir)
            if left:
                errors.append(f"processes still reference the instance: {left}")
            # Report only (never killed): see pids_with_cwd_under.
            inside = [p for p in pids_with_cwd_under(self.root) if p not in left]
            if inside:
                errors.append(f"processes have a working directory inside the "
                              f"instance (not killed): {inside}")
            if not left and not inside:
                shutil.rmtree(self.root, ignore_errors=True)
        if errors:
            raise AssertionError("teardown: " + "; ".join(errors))


def _user_record_text(entry: dict) -> str:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # text blocks only: a tool result has none
        return "\n".join(str(b.get("text", "")) for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def transcript_prompts(lines) -> list[str]:
    """The user prompts a real Claude transcript shows, in order, once each.

    A prompt does not always become its own ``type=user`` record when it
    arrives. One typed while a turn is running is queued first (a
    ``queue-operation`` line, ``operation=enqueue``): later it is either
    dequeued into a turn of its own (a user record follows) or absorbed
    by the running turn (``remove`` / ``absorbed_mid_turn``) and never
    gets a user record. Counting user records only would miss absorbed
    messages and every wait on them would time out, so the enqueue
    counts as the arrival and the user record a dequeue later writes for
    the same text is not counted again. Tool results and meta records
    are not prompts."""
    out: list[str] = []
    queued: list[str] = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        kind = e.get("type")
        if kind == "queue-operation":
            content = e.get("content")
            if e.get("operation") == "enqueue" and isinstance(content, str) and content:
                out.append(content)
                queued.append(content.strip())
            continue
        if kind != "user" or e.get("isMeta"):
            continue
        text = _user_record_text(e)
        if not text:
            continue
        if text.strip() in queued:
            queued.remove(text.strip())
            continue
        out.append(text)
    return out


def needle_in_any(needle: bytes) -> bool:
    return any(needle in _read(f"/proc/{p}/cmdline") for p in _own_pids())


def unix_socket_alive(path: Path) -> bool:
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        s.settimeout(1)
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()
