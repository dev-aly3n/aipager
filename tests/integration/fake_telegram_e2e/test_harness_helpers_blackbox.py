"""Black-box tests of the harness's public helpers (entrypoints.md, test-only
``tests/e2e/fake_telegram/instance.py``): the constants, ``make_root()``,
``daemon_env()``, ``write_shims()``, ``assert_socket_lengths()`` and the
``TestInstance`` build/stop lifecycle. No daemon is started here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.e2e.fake_telegram import instance as H
from tests.integration.fake_telegram_e2e.conftest import PYTHON, REAL_HOME, REPO, scrubbed_env

REAL_CHAT_ID = "256113222"
BASE_URL = "http://127.0.0.1:41234"


# ---- constants ------------------------------------------------------------------------------

@pytest.mark.parametrize("name,value", [
    ("BOT_ID", 7000000001),
    ("BOT_USERNAME", "aipager_fake_bot"),
    ("DM_ID", 900000001),
    ("GROUP_ID", -4000000001),
    ("SUPERGROUP_ID", -1004000000001),
    ("ALICE", 900000001),
    ("BOB", 900000002),
    ("CAROL", 900000003),
    ("DAVE", 900000004),
])
def test_constant_value(name, value):
    assert getattr(H, name) == value


def test_fake_token_shape():
    # `<digits>:<20+ chars>` so the production redaction recognises it.
    assert re.fullmatch(r"7000000001:[A-Za-z0-9_-]{20,}", H.FAKE_TOKEN)


def test_no_fake_id_equals_the_real_chat():
    ids = {H.DM_ID, H.GROUP_ID, H.SUPERGROUP_ID, H.ALICE, H.BOB, H.CAROL, H.DAVE}
    assert int(REAL_CHAT_ID) not in ids


# ---- make_root ------------------------------------------------------------------------------

@pytest.fixture
def made_root():
    roots: list[Path] = []

    def make() -> Path:
        r = H.make_root()
        roots.append(Path(r))
        return Path(r)
    try:
        yield make
    finally:
        for r in roots:
            if str(r).startswith("/tmp/apg-"):
                shutil.rmtree(r, ignore_errors=True)


def test_make_root_shape(made_root):
    assert re.fullmatch(r"/tmp/apg-[A-Za-z0-9_]{8}", str(made_root()))


def test_make_root_never_claude_prefix(made_root):
    assert not str(made_root()).startswith("/tmp/claude-")


def test_make_root_not_under_real_home(made_root):
    r = os.path.realpath(made_root())
    assert not (r == REAL_HOME or r.startswith(REAL_HOME + os.sep))


def test_make_root_not_under_slash_home_aly(made_root):
    assert not str(made_root()).startswith("/home/aly")


def test_make_root_exists_as_dir(made_root):
    assert made_root().is_dir()


def test_make_root_distinct_each_call(made_root):
    assert made_root() != made_root()


def test_make_root_short_enough_for_sockets(made_root):
    # The longest documented socket name under <root>/i must fit 107 bytes.
    longest = f"{made_root()}/i/aipager-reply-{'0' * 32}.sock"
    assert len(longest.encode()) <= 107


# ---- daemon_env -----------------------------------------------------------------------------

DIRTY = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "CLAUDE_CODE_OAUTH_TOKEN": "dummy-not-a-secret",
    "CLAUDE_TG_BOT_TOKEN": "1:dummy-not-a-real-token-xxxxxx",
    "CLAUDE_TG_CHAT_ID": REAL_CHAT_ID,
    "CLAUDE_CONFIG_DIR": "/home/aly/.claude",
    "ANTHROPIC_API_KEY": "dummy-not-a-secret",
    "AIPAGER_SOCKET_PATH": "/tmp/aipager.sock",
    "AIPAGER_INSTANCE_DIR": "/somewhere/else",
    "AIPAGER_TELEGRAM_API_ALLOW_REMOTE": "1",
    "OBSERVER_BOTS": "dummy",
    "MINIAPP_PORT": "8765",
    "CREDENTIALS_DIRECTORY": "/run/credentials/x",
    "PYTHONPATH": "/home/aly/aipager",
}


@pytest.fixture
def denv(scratch):
    root = scratch
    base = dict(DIRTY)
    env = H.daemon_env(base, root=root, base_url=BASE_URL, claude_bin=str(root / "b" / "claude"),
                       repo=REPO, python=PYTHON, claude_mode="standin")
    return env, root, base


@pytest.mark.parametrize("key,rel", [
    ("HOME", "h"),
    ("AIPAGER_INSTANCE_DIR", "i"),
    ("AIPAGER_WORK_DIR", "h/proj"),
])
def test_daemon_env_paths(denv, key, rel):
    env, root, _ = denv
    assert Path(env[key]) == root / rel


def test_daemon_env_api_base(denv):
    assert denv[0]["AIPAGER_TELEGRAM_API_BASE"] == BASE_URL


def test_daemon_env_fake_token(denv):
    assert denv[0]["CLAUDE_TG_BOT_TOKEN"] == H.FAKE_TOKEN


@pytest.mark.parametrize("key,value", [
    ("CLAUDE_TG_CHAT_ID", ""),
    ("OBSERVER_BOTS", ""),
    ("MINIAPP_ENABLED", "0"),
    ("PYTEST_CURRENT_TEST", "faketg-daemon"),
    ("PYTHONDONTWRITEBYTECODE", "1"),
])
def test_daemon_env_fixed_values(denv, key, value):
    assert denv[0][key] == value


def test_daemon_env_pythonpath_is_repo_under_test(denv):
    assert denv[0]["PYTHONPATH"] == str(REPO)


def test_daemon_env_claude_bin(denv):
    env, root, _ = denv
    assert env["AIPAGER_CLAUDE_BIN"] == str(root / "b" / "claude")


def test_daemon_env_path_starts_with_shim_dir(denv):
    env, root, _ = denv
    assert env["PATH"].split(os.pathsep)[0] == str(root / "b")


@pytest.mark.parametrize("key", [
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", "AIPAGER_SOCKET_PATH",
    "AIPAGER_TELEGRAM_API_ALLOW_REMOTE", "MINIAPP_PORT", "CREDENTIALS_DIRECTORY",
])
def test_daemon_env_strips_operator_setting(denv, key):
    assert key not in denv[0]


def test_daemon_env_no_value_from_the_operator_leaks(denv):
    leaked = [k for k, v in denv[0].items() if "dummy" in v or v == REAL_CHAT_ID]
    assert leaked == []


def test_daemon_env_keeps_unrelated_vars(denv):
    assert denv[0]["LANG"] == "C.UTF-8"


def test_daemon_env_does_not_mutate_base(denv):
    assert denv[2] == DIRTY


# ---- write_shims ----------------------------------------------------------------------------

STANDIN_SHIMS = ["aipager", "aipager-hook", "aipager-statusline", "claude"]


@pytest.fixture
def shims(scratch):
    b = scratch / "b"
    H.write_shims(b, PYTHON, REPO, "standin")
    return b


@pytest.mark.parametrize("name", STANDIN_SHIMS)
def test_shim_written(shims, name):
    assert (shims / name).is_file()


@pytest.mark.parametrize("name", STANDIN_SHIMS)
def test_shim_executable(shims, name):
    assert (shims / name).stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize("name", STANDIN_SHIMS)
def test_shim_is_sh_script(shims, name):
    assert (shims / name).read_text().startswith("#!/bin/sh")


@pytest.mark.parametrize("name,target", [
    ("aipager", "aipager.cli"),
    ("aipager-hook", "aipager.dtach.notify_hook"),
    ("aipager-statusline", "aipager.dtach.statusline_notify"),
])
def test_shim_runs_documented_module(shims, name, target):
    assert target in (shims / name).read_text()


@pytest.mark.parametrize("name", ["aipager", "aipager-hook", "aipager-statusline"])
def test_shim_uses_given_python(shims, name):
    assert PYTHON in (shims / name).read_text()


def test_real_mode_writes_no_claude_shim(scratch):
    b = scratch / "b"
    H.write_shims(b, PYTHON, REPO, "real")
    assert not (b / "claude").exists()


def _standin(shims: Path, scratch: Path, *args: str) -> subprocess.CompletedProcess:
    env = scrubbed_env(HOME=str(scratch / "h"), AIPAGER_INSTANCE_DIR=str(scratch / "i"))
    return subprocess.run([str(shims / "claude"), *args], env=env, cwd=str(scratch / "i"),
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def test_standin_version(shims, scratch):
    assert _standin(shims, scratch, "--version").stdout.strip() == "2.1.291 (Claude Code)"


def test_standin_auth_status_exit_zero(shims, scratch):
    assert _standin(shims, scratch, "auth", "status").returncode == 0


def test_standin_print_mode_exit_zero(shims, scratch):
    assert _standin(shims, scratch, "-p", "hello").returncode == 0


# ---- assert_socket_lengths ------------------------------------------------------------------

def test_socket_lengths_short_root_ok():
    H.assert_socket_lengths(Path("/tmp/apg-abcdefgh/i"), ["ft1", "ft2"])


def test_socket_lengths_long_dir_refused():
    with pytest.raises(AssertionError):
        H.assert_socket_lengths(Path("/var/tmp/" + "d" * 100), ["ft1"])


def test_socket_lengths_long_label_refused():
    with pytest.raises(AssertionError):
        H.assert_socket_lengths(Path("/tmp/apg-abcdefgh/i"), ["ft" + "9" * 100])


# ---- TestInstance build / stop (no daemon) ---------------------------------------------------

@pytest.fixture
def built():
    before = set(threading.enumerate())
    inst = H.TestInstance(REPO, "standin", dict(H.MEMBERS))
    try:
        inst.build()
        yield inst, before
    finally:
        inst.stop()
        if inst.root is not None and str(inst.root).startswith("/tmp/apg-"):
            shutil.rmtree(inst.root, ignore_errors=True)


def test_build_root_is_a_make_root(built):
    assert re.fullmatch(r"/tmp/apg-[A-Za-z0-9_]{8}", str(built[0].root))


@pytest.mark.parametrize("attr,rel", [("inst_dir", "i"), ("home", "h"), ("project", "h/proj")])
def test_build_layout(built, attr, rel):
    inst = built[0]
    assert Path(getattr(inst, attr)) == Path(inst.root) / rel


@pytest.mark.parametrize("attr", ["inst_dir", "home", "project"])
def test_build_layout_dirs_exist(built, attr):
    assert Path(getattr(built[0], attr)).is_dir()


def test_build_yaml_under_fake_home(built):
    inst = built[0]
    assert Path(inst.yaml_path) == Path(inst.home) / ".config/aipager/aipager.yaml"


def test_build_yaml_has_fake_token(built):
    assert H.FAKE_TOKEN in Path(built[0].yaml_path).read_text()


def test_build_yaml_never_names_the_real_chat(built):
    assert REAL_CHAT_ID not in Path(built[0].yaml_path).read_text()


def test_build_settings_model_haiku(built):
    data = json.loads((Path(built[0].home) / ".claude/settings.json").read_text())
    assert data.get("model") == "haiku"


def test_build_onboarding_done(built):
    data = json.loads((Path(built[0].home) / ".claude.json").read_text())
    assert data.get("hasCompletedOnboarding") is True


def test_build_standin_writes_no_daemon_env(built):
    assert not (Path(built[0].home) / ".config/aipager/daemon.env").exists()


def test_build_starts_no_daemon(built):
    assert built[0].daemon_pid is None


def test_stop_removes_root():
    inst = H.TestInstance(REPO, "standin", dict(H.MEMBERS))
    inst.build()
    root = Path(inst.root)
    inst.stop()
    assert not root.exists()


def test_stop_is_idempotent():
    inst = H.TestInstance(REPO, "standin", dict(H.MEMBERS))
    inst.build()
    inst.stop()
    inst.stop()


def test_stop_without_build_is_safe():
    H.TestInstance(REPO, "standin", dict(H.MEMBERS)).stop()


def test_stop_leaves_no_thread():
    before = set(threading.enumerate())
    inst = H.TestInstance(REPO, "standin", dict(H.MEMBERS))
    inst.build()
    inst.stop()
    time.sleep(0.2)
    assert [t for t in threading.enumerate() if t not in before and t.is_alive()] == []
