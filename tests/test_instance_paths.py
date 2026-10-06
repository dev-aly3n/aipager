"""AIPAGER_INSTANCE_DIR: one setting moves every shared runtime path.

A second daemon on the same machine (the fake-Telegram test instance)
must share nothing with the operator's: a daemon scanning the same
``/tmp/claude-dtach-*.sock`` glob ADOPTS the other one's sessions, and
both write the one policy floor file. These tests prove, before any
instance is ever started, that:

1. with the variable unset every path is exactly what it always was
   (written out literally here, not computed);
2. with it set (and HOME pointed elsewhere) every runtime path is under
   the instance folder and every HOME path under the fake HOME;
3. it beats ``AIPAGER_SOCKET_PATH`` and ``XDG_RUNTIME_DIR``;
4. the safety floor protects the relocated control files;
5. the instance never reads a checkout's ``.env``;
6. ``aipager start`` refuses a bad instance (and the real HOME) first.

The resolution checks run ``tests/_instance_paths_probe.py`` in a
subprocess: conftest redirects several of these names in-process, and
many are computed once at import, so only a fresh interpreter shows
what a real daemon or hook would use.
"""

from __future__ import annotations

import json
import os
import pathlib
import pwd
import subprocess
import sys
import tempfile

import pytest

from aipager import instance

REPO = pathlib.Path(__file__).resolve().parent.parent
PROBE = REPO / "tests" / "_instance_paths_probe.py"
SESSION = "claude-probe__d1"
REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def _probe(env_extra: dict, home: pathlib.Path) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(env_extra)
    r = subprocess.run(
        [sys.executable, str(PROBE)], env=env, capture_output=True,
        text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout)


@pytest.fixture
def short_root():
    """A short folder (socket paths stay far below 108 bytes)."""
    root = tempfile.mkdtemp(prefix="apgt-")
    try:
        yield pathlib.Path(root)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (1) Parity: nothing changes for a normal install.
# ---------------------------------------------------------------------------

def _expected_unset(sock_dir: str) -> dict:
    uid = os.getuid()
    return {
        "control_socket": f"{sock_dir}/aipager.sock",
        "default_socket_path": f"{sock_dir}/aipager.sock",
        "flood_mute": f"{sock_dir}/aipager-flood-mute.json",
        "flood_backoff": f"{sock_dir}/aipager-flood-backoff.json",
        "file_download_dir": "/tmp/aipager-files",
        "sock_prefix": "/tmp/claude-dtach-",
        "sock_path": "/tmp/claude-dtach-probe__d1.sock",
        "snapshot": "/tmp/claude-policy-claude-probe__d1.json",
        "notes": "/tmp/claude-notes-claude-probe__d1",
        "floor": f"/tmp/claude-policy-.floor-{uid}.json",
        "reply_context": "/tmp/claude-reply-claude-probe__d1.txt",
        "status_file": "/tmp/claude-status-claude-probe__d1.json",
        "notify_hook_socket": f"{sock_dir}/aipager.sock",
        "statusline_hook_socket": f"{sock_dir}/aipager.sock",
        "notify_hook_status_dir": "/tmp",
        "statusline_hook_status_dir": "/tmp",
        "marker_dir": sock_dir,
        "marker": f"{sock_dir}/aipager-modelswitch-claude-probe__d1.json",
        "reply_socket": f"{sock_dir}/aipager-reply-{'0' * 32}.sock",
        "self_restart_log": "/tmp/aipager.log",
    }


BASE_DENY = [
    "~/.claude/**",
    "~/.config/aipager/**",
    "~/.local/share/aipager/**",
    "~/.local/state/aipager/**",
    "/tmp/claude-policy-*",
    "/tmp/claude-notes-*",
    "/tmp/claude-status-*",
    "/tmp/claude-reply-*",
    "/tmp/claude-dtach-*",
]


@pytest.mark.parametrize("xdg", [None, "/run/user/4242"], ids=["no-xdg", "xdg"])
def test_parity_with_instance_unset(tmp_path, xdg):
    env = {"XDG_RUNTIME_DIR": xdg} if xdg else {}
    out = _probe(env, tmp_path)
    assert out["runtime"] == _expected_unset(xdg or "/tmp")
    assert out["deny_no_access"] == BASE_DENY


def test_parity_socket_override_still_wins_without_instance(tmp_path):
    out = _probe({"AIPAGER_SOCKET_PATH": "/x/y.sock",
                  "XDG_RUNTIME_DIR": "/run/user/4242"}, tmp_path)
    assert out["runtime"]["control_socket"] == "/x/y.sock"
    assert out["runtime"]["notify_hook_socket"] == "/x/y.sock"
    assert out["runtime"]["sock_prefix"] == "/tmp/claude-dtach-"


def test_resolver_defaults_in_process(monkeypatch):
    for key in ("AIPAGER_INSTANCE_DIR", "AIPAGER_SOCKET_PATH", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(key, raising=False)
    assert instance.instance_dir() is None
    assert instance.runtime_tmp_dir() == "/tmp"
    assert instance.control_socket_path() == "/tmp/aipager.sock"
    assert instance.dtach_sock_prefix() == "/tmp/claude-dtach-"
    assert instance.file_download_dir() == "/tmp/aipager-files"
    assert instance.self_restart_log_path() == "/tmp/aipager.log"
    assert instance.protected_globs() == ()
    assert instance.start_check() == []
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", "   ")
    assert instance.instance_dir() is None
    assert instance.start_check() == []


# ---------------------------------------------------------------------------
# (2) + (3) + (4) Isolation.
# ---------------------------------------------------------------------------

def _under(path: str, root: pathlib.Path) -> bool:
    return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) \
        == os.path.realpath(root)


@pytest.fixture
def isolated(short_root):
    inst = short_root / "i"
    home = short_root / "h"
    inst.mkdir()
    home.mkdir()
    # A plain file standing in for a live session socket (the probe
    # answers is_socket for paths inside the instance folder).
    (inst / "claude-dtach-probe__d1.sock").write_text("")
    out = _probe({
        # Trailing slash and padding: normalised.
        "AIPAGER_INSTANCE_DIR": f"  {inst}/ ",
        # Both decoys must lose to the instance folder.
        "AIPAGER_SOCKET_PATH": "/tmp/aipager-decoy.sock",
        "XDG_RUNTIME_DIR": "/run/user/4242",
    }, home)
    return inst, home, out


def test_isolation_every_runtime_path_is_under_the_instance(isolated):
    inst, _home, out = isolated
    bad = {k: v for k, v in out["runtime"].items() if not _under(v, inst)}
    assert not bad, f"runtime paths outside the instance folder: {bad}"
    for k, v in out["runtime"].items():
        assert not v.startswith(("/tmp/claude-", "/tmp/aipager")), (k, v)
        assert not v.startswith("/run/user/4242"), (k, v)
        assert not _under(v, pathlib.Path(REAL_HOME)), (k, v)
    assert out["runtime"]["control_socket"] == f"{inst}/aipager.sock"
    assert out["runtime"]["floor"] == f"{inst}/claude-policy-.floor-{out['uid']}.json"
    assert out["runtime"]["sock_prefix"] == f"{inst}/claude-dtach-"


def test_isolation_every_home_path_is_under_the_fake_home(isolated):
    _inst, home, out = isolated
    bad = {k: v for k, v in out["home"].items() if not _under(v, home)}
    assert not bad, f"HOME paths outside the fake HOME: {bad}"


def test_isolation_session_scans_read_the_instance_folder(isolated):
    _inst, _home, out = isolated
    assert out["scans"]["list_sessions"] == [SESSION]
    assert out["scans"]["status_live"] == [SESSION]


def test_isolation_safety_floor_protects_relocated_control_files(isolated):
    inst, _home, out = isolated
    deny = out["deny_no_access"]
    assert deny[:len(BASE_DENY)] == BASE_DENY
    for kind in ("policy", "notes", "status", "reply", "dtach"):
        assert f"{inst}/claude-{kind}-*" in deny, kind
    assert out["restricted_write_denied"] == dict.fromkeys(
        ("policy", "notes", "status", "reply", "dtach"), True)


def test_protected_globs_cover_every_tmp_control_prefix(monkeypatch, tmp_path):
    """The instance list names the same five kinds as the /tmp list."""
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    tmp_kinds = {g.split("/tmp/claude-")[1].split("-")[0]
                 for g in BASE_DENY if g.startswith("/tmp/claude-")}
    inst_kinds = {g.rsplit("/claude-", 1)[1].split("-")[0]
                  for g in instance.protected_globs()}
    assert inst_kinds == tmp_kinds


# ---------------------------------------------------------------------------
# In-process seams: list_sessions, status, updater follow the resolver.
# ---------------------------------------------------------------------------

def _fake_sockets(monkeypatch, root: pathlib.Path):
    real = pathlib.Path.is_socket

    def _is_socket(self):
        if str(self).startswith(str(root) + os.sep):
            return self.exists()
        return real(self)

    monkeypatch.setattr(pathlib.Path, "is_socket", _is_socket)


def test_list_sessions_scans_the_instance_folder(monkeypatch, tmp_path, run_async):
    from aipager.dtach import inject
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    (tmp_path / "claude-dtach-ls1__d1.sock").write_text("")
    _fake_sockets(monkeypatch, tmp_path)
    assert run_async(inject.list_sessions()) == ["claude-ls1__d1"]


def test_status_live_sessions_scan_the_instance_folder(monkeypatch, tmp_path):
    from aipager import status
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    (tmp_path / "claude-dtach-st1__d1.sock").write_text("")
    assert status._live_sessions() == {"claude-st1__d1"}


def test_updater_cleanup_stays_inside_the_instance(monkeypatch, tmp_path):
    """Uninstall cleanup of an instance globs only its folder and never
    the legacy /tmp socket. Path is replaced by a recorder, so nothing on
    disk is touched even if the code regresses."""
    from aipager import updater
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(tmp_path / "aipager.sock"))
    seen: list[tuple[str, str]] = []

    class _Rec:
        def __init__(self, p):
            self.p = str(p)
            seen.append(("path", self.p))

        def glob(self, pattern):
            seen.append(("glob", f"{self.p}/{pattern}"))
            return []

        def unlink(self, missing_ok=False):
            seen.append(("unlink", self.p))

    monkeypatch.setattr(updater, "Path", _Rec)
    monkeypatch.setattr(updater, "_unlink_quietly", lambda p: seen.append(("unlink", p.p)))
    updater._remove_tmp_sockets()
    globs = [v for k, v in seen if k == "glob"]
    assert globs == [f"{tmp_path}/claude-dtach-*.sock", f"{tmp_path}/claude-status-*.json"]
    unlinks = [v for k, v in seen if k == "unlink"]
    assert unlinks == [str(tmp_path / "aipager.sock")]


# ---------------------------------------------------------------------------
# (5) The instance never reads a checkout's .env.
# ---------------------------------------------------------------------------

def test_load_env_file_skips_repo_dotenv_for_an_instance(monkeypatch, tmp_path):
    from aipager import config
    dotenv = tmp_path / "repo.env"
    dotenv.write_text("APG_TEST_DOTENV_KEY=from-dotenv\n")
    monkeypatch.setattr(config, "_XDG_CONFIG", tmp_path / "missing-config.env")
    monkeypatch.setattr(config, "_PROJECT_DOTENV", dotenv)
    monkeypatch.delenv("APG_TEST_DOTENV_KEY", raising=False)

    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    config._load_env_file()
    assert "APG_TEST_DOTENV_KEY" not in os.environ

    # Parity: without the instance the checkout .env is still read.
    monkeypatch.delenv("AIPAGER_INSTANCE_DIR")
    config._load_env_file()
    assert os.environ.get("APG_TEST_DOTENV_KEY") == "from-dotenv"


def test_load_env_file_instance_still_reads_its_home_config(monkeypatch, tmp_path):
    from aipager import config
    home_env = tmp_path / "config.env"
    home_env.write_text("APG_TEST_HOME_KEY=from-home\n")
    monkeypatch.setattr(config, "_XDG_CONFIG", home_env)
    monkeypatch.setattr(config, "_PROJECT_DOTENV", tmp_path / "missing.env")
    monkeypatch.delenv("APG_TEST_HOME_KEY", raising=False)
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path))
    config._load_env_file()
    assert os.environ.get("APG_TEST_HOME_KEY") == "from-home"


# ---------------------------------------------------------------------------
# (6) start_check.
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_real_home(monkeypatch, tmp_path):
    real = tmp_path / "realhome"
    real.mkdir()
    monkeypatch.setattr(instance, "_real_home", lambda: str(real))
    other = tmp_path / "otherhome"
    other.mkdir()
    monkeypatch.setenv("HOME", str(other))
    return real


OWN_MSG = "AIPAGER_INSTANCE_DIR must be an absolute path to an existing folder you own."


def test_start_check_refuses_a_relative_path(monkeypatch, fake_real_home, short_root):
    # An EXISTING relative folder: only the absolute-path rule refuses it.
    (short_root / "rel").mkdir()
    monkeypatch.chdir(short_root)
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", "rel")
    assert instance.start_check() == [OWN_MSG]


def test_start_check_refuses_a_file(monkeypatch, fake_real_home, short_root):
    f = short_root / "afile"
    f.write_text("")
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(f))
    assert instance.start_check() == [OWN_MSG]


def test_start_check_refuses_a_missing_folder(monkeypatch, fake_real_home, tmp_path):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path / "nope"))
    assert instance.start_check() == [OWN_MSG]


def test_start_check_refuses_a_folder_owned_by_someone_else(monkeypatch, fake_real_home):
    if os.stat("/").st_uid == os.getuid():
        pytest.skip("running as the owner of /")
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", "/")
    assert instance.start_check() == [OWN_MSG]


def test_start_check_refuses_the_real_home(monkeypatch, fake_real_home, short_root):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(short_root))
    monkeypatch.setenv("HOME", str(fake_real_home) + "/")
    lines = instance.start_check()
    assert lines == [
        f"AIPAGER_INSTANCE_DIR is set but HOME is your real home folder ({fake_real_home}).",
        "Point HOME at a separate folder so this instance cannot touch your real settings.",
    ]


def test_start_check_refuses_an_unset_home(monkeypatch, fake_real_home, short_root):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(short_root))
    monkeypatch.delenv("HOME")
    assert instance.start_check()[0].startswith("AIPAGER_INSTANCE_DIR is set but HOME")


def test_start_check_refuses_a_folder_too_long_for_sockets(monkeypatch, fake_real_home,
                                                           short_root):
    deep = short_root / ("x" * 60)
    deep.mkdir()
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(deep))
    n = len(os.fsencode(f"{deep}/aipager-reply-{'0' * 32}.sock"))
    assert n > 107
    assert instance.start_check() == [
        f"AIPAGER_INSTANCE_DIR is too long for a socket path ({n} bytes, "
        "the limit is 107). Use a shorter folder."
    ]


def test_start_check_accepts_a_good_instance(monkeypatch, fake_real_home, short_root):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(short_root))
    assert instance.start_check() == []


# ---------------------------------------------------------------------------
# (7) `aipager start` runs both checks before anything else.
# ---------------------------------------------------------------------------

class _Stop(Exception):
    pass


def _stop_at_require_config(monkeypatch) -> list:
    """Record a call of require_config and stop _cmd_start right there
    (exit 99), so a regressed check can never run the rest of start-up
    (migrations, lock, network) inside the test process."""
    from aipager import preflight
    called: list = []

    def _require():
        called.append(1)
        raise SystemExit(99)

    monkeypatch.setattr(preflight, "require_config", _require)
    return called


def test_cmd_start_checks_run_first_in_order(monkeypatch):
    from aipager import preflight, telegram_endpoint
    from aipager.cli import daemon
    order: list[str] = []
    monkeypatch.setattr(instance, "start_check",
                        lambda: order.append("instance") or [])
    monkeypatch.setattr(telegram_endpoint, "check",
                        lambda: order.append("endpoint") or None)

    def _require():
        order.append("require_config")
        raise _Stop

    monkeypatch.setattr(preflight, "require_config", _require)
    with pytest.raises(_Stop):
        daemon._cmd_start(None)
    assert order == ["instance", "endpoint", "require_config"]


def test_cmd_start_exits_2_when_home_is_the_real_home(monkeypatch, fake_real_home,
                                                      short_root, capsys):
    from aipager.cli import daemon
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(short_root))
    monkeypatch.setenv("HOME", str(fake_real_home))
    monkeypatch.delenv("AIPAGER_TELEGRAM_API_BASE", raising=False)
    called = _stop_at_require_config(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        daemon._cmd_start(None)
    assert exc.value.code == 2
    assert called == []
    err = capsys.readouterr().err
    assert "HOME is your real home folder" in err
    assert list(short_root.iterdir()) == []
