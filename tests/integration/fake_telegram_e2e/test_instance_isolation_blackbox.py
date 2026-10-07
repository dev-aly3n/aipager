"""Black-box: AIPAGER_INSTANCE_DIR relocates every shared runtime path.

design.md success criteria 1 (unset parity), 2 (isolated instance resolves
every runtime path under the instance dir and every HOME path under the fake
HOME) and 3 (restricted turns cannot touch the relocated control files, here
at the level of the deny-glob list). Each probe runs in a fresh subprocess so
import-time constants are computed from the env under test.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from pathlib import Path

import pytest

from .conftest import PYTHON, REAL_HOME, scrubbed_env

PROBE = Path(__file__).with_name("_probe.py")

RUNTIME_KEYS = (
    "runtime_tmp_dir", "control_socket_path", "dtach_sock_prefix", "file_download_dir",
    "self_restart_log_path", "config.SOCKET_PATH", "config._default_socket_path",
    "config.FLOOD_MUTE_FILE", "config.FLOOD_BACKOFF_FILE", "config.FILE_DOWNLOAD_DIR",
    "inject.SOCK_PREFIX", "inject._sock_path", "snapshot_path", "notes_dir", "floor_path",
    "reply_context_path", "statusline_file.STATUS_DIR", "status_file_path",
    "notify_hook.SOCKET_PATH", "statusline_notify.SOCKET_PATH",
)
SOCKET_KEYS = ("control_socket_path", "config.SOCKET_PATH", "config._default_socket_path",
               "notify_hook.SOCKET_PATH", "statusline_notify.SOCKET_PATH")


def probe(env: dict, *args: str) -> dict:
    proc = subprocess.run([PYTHON, str(PROBE), *args], env=env, capture_output=True,
                          text=True, timeout=60, cwd="/")
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _isolated(scratch, **extra) -> dict:
    env = scrubbed_env(HOME=str(scratch / "h"), AIPAGER_INSTANCE_DIR=str(scratch / "i"),
                       **extra)
    return probe(env)


# ---- (2) isolation: every runtime path under the instance dir -----------------------

@pytest.fixture
def iso(scratch):
    return scratch, _isolated(scratch)


@pytest.mark.parametrize("key", RUNTIME_KEYS)
def test_isolated_runtime_path_is_under_instance_dir(iso, key):
    scratch, out = iso
    inst = str(scratch / "i")
    value = os.path.normpath(out[key])
    assert value == inst or value.startswith(inst + "/"), f"{key} escaped the instance dir"


@pytest.mark.parametrize("key", RUNTIME_KEYS)
def test_isolated_runtime_path_not_shared_tmp(iso, key):
    _, out = iso
    value = out[key]
    assert not value.startswith(("/tmp/claude-", "/tmp/aipager", "/run/user")), key


@pytest.mark.parametrize("key", [
    "aipager.config.SESSION_STATE_FILE", "aipager.scope.CONFIG_PATH",
    "aipager.daemon_secrets.DAEMON_ENV_PATH",
])
def test_isolated_home_path_is_under_fake_home(iso, key):
    scratch, out = iso
    value = out["home_paths"][key]
    if value.startswith("<missing"):
        pytest.skip(f"{key} not present under that name ({value})")
    assert value.startswith(str(scratch / "h") + "/")


def test_isolated_nothing_under_real_home(iso):
    _, out = iso
    flat = [out[k] for k in RUNTIME_KEYS] + [
        v for v in out["home_paths"].values() if not v.startswith("<missing")]
    assert [v for v in flat if v.startswith(REAL_HOME)] == []


def test_isolated_control_socket_exact_name(iso):
    scratch, out = iso
    assert out["control_socket_path"] == str(scratch / "i" / "aipager.sock")


@pytest.mark.parametrize("key", SOCKET_KEYS)
def test_isolated_daemon_and_hooks_agree_on_socket(iso, key):
    scratch, out = iso
    assert out[key] == str(scratch / "i" / "aipager.sock")


@pytest.mark.parametrize("key,name", [
    ("inject._sock_path", "claude-dtach-ftprobe.sock"),
    ("snapshot_path", "claude-policy-ftprobe.json"),
    ("notes_dir", "claude-notes-ftprobe"),
    ("reply_context_path", "claude-reply-ftprobe.txt"),
    ("status_file_path", "claude-status-ftprobe.json"),
    ("file_download_dir", "aipager-files"),
    ("self_restart_log_path", "aipager.log"),
    ("config.FLOOD_MUTE_FILE", "aipager-flood-mute.json"),
    ("config.FLOOD_BACKOFF_FILE", "aipager-flood-backoff.json"),
])
def test_isolated_file_names_unchanged(iso, key, name):
    scratch, out = iso
    assert os.path.normpath(out[key]) == str(scratch / "i" / name)


def test_isolated_floor_is_per_uid_in_instance(iso):
    scratch, out = iso
    assert out["floor_path"] == str(scratch / "i" / f"claude-policy-.floor-{out['uid']}.json")


def test_isolated_start_check_ok_for_valid_setup(iso):
    _, out = iso
    assert out["start_check"] == []


def test_isolated_list_sessions_sees_only_instance_sockets(scratch):
    env = scrubbed_env(HOME=str(scratch / "h"), AIPAGER_INSTANCE_DIR=str(scratch / "i"))
    out = probe(env, "list")
    seen = out["list_sessions"]
    # Never print the entries: a regression would list the operator's sessions.
    assert (len(seen), sum("ftlist" in s for s in seen)) == (1, 1)


# ---- (3) precedence over AIPAGER_SOCKET_PATH and XDG_RUNTIME_DIR ----------------------

@pytest.mark.parametrize("key", SOCKET_KEYS)
def test_instance_beats_socket_path_override(scratch, key):
    out = _isolated(scratch, AIPAGER_SOCKET_PATH="/var/tmp/aft-elsewhere/custom.sock")
    assert out[key] == str(scratch / "i" / "aipager.sock")


@pytest.mark.parametrize("key", SOCKET_KEYS)
def test_instance_beats_xdg_runtime_dir(scratch, key):
    out = _isolated(scratch, XDG_RUNTIME_DIR="/var/tmp/aft-elsewhere-xdg")
    assert out[key] == str(scratch / "i" / "aipager.sock")


# ---- value normalisation: blank, whitespace, trailing slash, padding ------------------

@pytest.mark.parametrize("raw", ["", "   ", "\t"])
def test_blank_instance_is_unset(scratch, raw):
    env = scrubbed_env(HOME=str(scratch / "h"), AIPAGER_INSTANCE_DIR=raw)
    out = probe(env)
    assert (out["instance_dir"], out["runtime_tmp_dir"]) == (None, "/tmp")


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_instance_falls_through_to_xdg(scratch, raw):
    env = scrubbed_env(HOME=str(scratch / "h"), AIPAGER_INSTANCE_DIR=raw,
                       XDG_RUNTIME_DIR="/var/tmp/aft-xdg")
    out = probe(env)
    assert [out[k] for k in SOCKET_KEYS] == ["/var/tmp/aft-xdg/aipager.sock"] * len(SOCKET_KEYS)


@pytest.mark.parametrize("decorate", [
    lambda p: p + "/", lambda p: p + "//", lambda p: "  " + p + "  ", lambda p: p + "/./",
])
@pytest.mark.parametrize("key", SOCKET_KEYS + ("inject._sock_path", "floor_path"))
def test_instance_value_is_normalised(scratch, decorate, key):
    inst = str(scratch / "i")
    env = scrubbed_env(HOME=str(scratch / "h"), AIPAGER_INSTANCE_DIR=decorate(inst))
    out = probe(env)
    assert out[key].startswith(inst + "/") and "//" not in out[key]


def test_instance_dir_reports_normalised_value(scratch):
    inst = str(scratch / "i")
    env = scrubbed_env(HOME=str(scratch / "h"), AIPAGER_INSTANCE_DIR=f"  {inst}/ ")
    assert probe(env)["instance_dir"] == inst


# ---- (1) parity with today's literals when unset ---------------------------------------

@pytest.fixture
def unset(scratch):
    return probe(scrubbed_env(HOME=str(scratch / "h")))


@pytest.mark.parametrize("key,expected", [
    ("instance_dir", None),
    ("runtime_tmp_dir", "/tmp"),
    ("dtach_sock_prefix", "/tmp/claude-dtach-"),
    ("inject.SOCK_PREFIX", "/tmp/claude-dtach-"),
    ("inject._sock_path", "/tmp/claude-dtach-ftprobe.sock"),
    ("snapshot_path", "/tmp/claude-policy-ftprobe.json"),
    ("reply_context_path", "/tmp/claude-reply-ftprobe.txt"),
    ("status_file_path", "/tmp/claude-status-ftprobe.json"),
    ("statusline_file.STATUS_DIR", "/tmp"),
    ("file_download_dir", "/tmp/aipager-files"),
    ("config.FILE_DOWNLOAD_DIR", "/tmp/aipager-files"),
    ("self_restart_log_path", "/tmp/aipager.log"),
    ("protected_globs", []),
    ("start_check", []),
])
def test_unset_parity(unset, key, expected):
    assert unset[key] == expected


def test_unset_parity_notes_dir(unset):
    assert os.path.normpath(unset["notes_dir"]) == "/tmp/claude-notes-ftprobe"


def test_unset_parity_floor(unset):
    assert unset["floor_path"] == f"/tmp/claude-policy-.floor-{unset['uid']}.json"


@pytest.mark.parametrize("key", SOCKET_KEYS)
def test_unset_no_xdg_socket_is_tmp(unset, key):
    assert unset[key] == "/tmp/aipager.sock"


@pytest.mark.parametrize("key", SOCKET_KEYS)
def test_unset_xdg_socket(scratch, key):
    out = probe(scrubbed_env(HOME=str(scratch / "h"), XDG_RUNTIME_DIR="/var/tmp/aft-xdg"))
    assert out[key] == "/var/tmp/aft-xdg/aipager.sock"


@pytest.mark.parametrize("key", SOCKET_KEYS)
def test_unset_socket_path_override_beats_xdg(scratch, key):
    out = probe(scrubbed_env(HOME=str(scratch / "h"), XDG_RUNTIME_DIR="/var/tmp/aft-xdg",
                             AIPAGER_SOCKET_PATH="/var/tmp/aft-o/custom.sock"))
    assert out[key] == "/var/tmp/aft-o/custom.sock"


# ---- (3) safety deny globs ---------------------------------------------------------------

GLOB_KINDS = ("policy", "notes", "status", "reply", "dtach")


def test_protected_globs_are_the_five_control_kinds(iso):
    scratch, out = iso
    inst = str(scratch / "i")
    assert sorted(out["protected_globs"]) == sorted(f"{inst}/claude-{k}-*" for k in GLOB_KINDS)


def test_deny_list_gains_exactly_protected_globs(scratch):
    with_inst = _isolated(scratch)
    without = probe(scrubbed_env(HOME=str(scratch / "h")))
    gained = set(with_inst["safety.DENY_PATHS_NO_ACCESS"]) - set(
        without["safety.DENY_PATHS_NO_ACCESS"])
    assert gained == set(with_inst["protected_globs"])


def test_deny_list_keeps_todays_entries_when_isolated(scratch):
    with_inst = _isolated(scratch)
    without = probe(scrubbed_env(HOME=str(scratch / "h")))
    assert set(without["safety.DENY_PATHS_NO_ACCESS"]) <= set(
        with_inst["safety.DENY_PATHS_NO_ACCESS"])


@pytest.mark.parametrize("name", [
    "claude-policy-x1__g4000000001.json", "claude-policy-.floor-1000.json",
    "claude-notes-x1/note.json", "claude-status-x1.json", "claude-reply-x1.txt",
    "claude-dtach-x1.sock",
])
def test_relocated_control_file_matches_a_deny_glob(iso, name):
    scratch, out = iso
    target = str(scratch / "i" / name)
    assert any(fnmatch.fnmatch(target, g) for g in out["safety.DENY_PATHS_NO_ACCESS"])


@pytest.mark.parametrize("name", ["daemon.log", "aipager.sock", "standin-x.jsonl"])
def test_non_control_instance_file_not_denied_by_instance_globs(iso, name):
    scratch, out = iso
    target = str(scratch / "i" / name)
    assert not any(fnmatch.fnmatch(target, g) for g in out["protected_globs"])
