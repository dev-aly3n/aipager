"""Black-box: `aipager start` refuses unsafe isolated setups with exit 2.

design.md success criterion 4: with the instance var and HOME = real home,
`aipager start` exits 2 with the documented message and writes nothing; the
same for an invalid / too-long instance dir and a non-loopback API base
without allow-remote. entrypoints.md: the refusal comes "before reading
config, migrating, locking or touching the network".

Each case runs `python -m aipager start` (via runpy) under _guarded_start.py,
whose audit-hook tripwire kills the child on any write, network call, spawn
or read of the real config, so no case can ever start a daemon.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from aipager import instance

from .conftest import PYTHON, REAL_HOME, scrubbed_env

GUARD = Path(__file__).with_name("_guarded_start.py")
REMOTE = "http://example.com:8443"

MSG_DIR = "AIPAGER_INSTANCE_DIR must be an absolute path to an existing folder you own."
MSG_HOME_1 = "AIPAGER_INSTANCE_DIR is set but HOME is your real home folder"
MSG_HOME_2 = "Point HOME at a separate folder so this instance cannot touch your real settings."
MSG_LONG = "AIPAGER_INSTANCE_DIR is too long for a socket path ({n} bytes, the limit is 107)."
MSG_LONG_2 = "Use a shorter folder."
MSG_URL = "AIPAGER_TELEGRAM_API_BASE is not a valid http(s) URL."
MSG_REMOTE = "AIPAGER_TELEGRAM_API_BASE points at example.com, which is not this machine."
MSG_ALLOW = "Set AIPAGER_TELEGRAM_API_ALLOW_REMOTE=1 to allow it."

# len("<dir>/aipager-reply-" + 32 hex + ".sock") = len(dir) + 52; refused at >= 108.
LONGEST_SUFFIX = len("/aipager-reply-" + "0" * 32 + ".sock")


def _dir_of_len(root: Path, n: int) -> Path:
    pad = n - len(str(root)) - 1
    assert pad > 0
    d = root / ("d" * pad)
    d.mkdir()
    assert len(str(d)) == n
    return d


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _case_env(root: Path, case: str) -> dict:
    """Every case keeps at least one refusal in force, and a fake HOME unless the
    case is about HOME itself (then the API base is ALSO remote: two refusals)."""
    i, h = root / "i", root / "h"
    if case == "relative":
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR="i")
    if case == "missing":
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(root / "nope"))
    if case == "is_a_file":
        f = root / "afile"
        f.write_text("")
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(f))
    if case == "not_owned":
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR="/usr/share")
    if case == "home_real":
        return scrubbed_env(HOME=REAL_HOME, AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE=REMOTE)
    if case == "home_real_slash":
        return scrubbed_env(HOME=REAL_HOME + "/", AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE=REMOTE)
    if case == "home_real_symlink":
        link = root / "hl"
        link.symlink_to(REAL_HOME)
        return scrubbed_env(HOME=str(link), AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE=REMOTE)
    if case == "home_unset":
        return scrubbed_env(HOME=None, AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE=REMOTE)
    if case == "home_blank":
        return scrubbed_env(HOME="", AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE=REMOTE)
    if case == "too_long":
        d = _dir_of_len(root, 108 - LONGEST_SUFFIX)
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(d))
    if case == "too_long_by_far":
        d = _dir_of_len(root, 200)
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(d))
    if case == "api_ftp":
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE="ftp://127.0.0.1:8081")
    if case == "api_no_scheme":
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE="127.0.0.1:8081")
    if case == "api_remote":
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE=REMOTE)
    if case == "api_remote_allow_true":
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE=REMOTE,
                            AIPAGER_TELEGRAM_API_ALLOW_REMOTE="true")
    if case == "api_remote_no_instance":
        return scrubbed_env(HOME=str(h), AIPAGER_TELEGRAM_API_BASE=REMOTE)
    if case == "api_lookalike":
        return scrubbed_env(HOME=str(h), AIPAGER_INSTANCE_DIR=str(i),
                            AIPAGER_TELEGRAM_API_BASE="http://127.0.0.1.evil.com")
    raise AssertionError(case)


CASES = {
    "relative": [MSG_DIR],
    "missing": [MSG_DIR],
    "is_a_file": [MSG_DIR],
    "not_owned": [MSG_DIR],
    "home_real": [f"{MSG_HOME_1} ({REAL_HOME}).", MSG_HOME_2],
    "home_real_slash": [MSG_HOME_1, MSG_HOME_2],
    "home_real_symlink": [MSG_HOME_1, MSG_HOME_2],
    "home_unset": [MSG_HOME_1, MSG_HOME_2],
    "home_blank": [MSG_HOME_1, MSG_HOME_2],
    "too_long": [MSG_LONG.format(n=108), MSG_LONG_2],
    "too_long_by_far": [MSG_LONG.format(n=200 + LONGEST_SUFFIX), MSG_LONG_2],
    "api_ftp": [MSG_URL],
    "api_no_scheme": [MSG_URL],
    "api_remote": [MSG_REMOTE, MSG_ALLOW],
    "api_remote_allow_true": [MSG_REMOTE, MSG_ALLOW],
    "api_remote_no_instance": [MSG_REMOTE, MSG_ALLOW],
    "api_lookalike": [MSG_ALLOW],
}


@pytest.fixture(scope="module")
def results():
    out = {}
    for case in CASES:
        root = Path(tempfile.mkdtemp(prefix="aft-", dir="/var/tmp"))
        try:
            (root / "i").mkdir()
            (root / "h").mkdir()
            env = _case_env(root, case)
            before = _tree(root)
            proc = subprocess.run([PYTHON, str(GUARD), "start"], env=env, cwd=str(root),
                                  capture_output=True, text=True, timeout=60)
            out[case] = {
                "rc": proc.returncode,
                "text": proc.stdout + proc.stderr,
                "created": sorted(_tree(root) - before),
            }
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return out


@pytest.mark.parametrize("case", list(CASES))
def test_start_exits_2(results, case):
    r = results[case]
    assert r["rc"] == 2, r["text"][-600:]


@pytest.mark.parametrize("case", list(CASES))
def test_start_prints_documented_message(results, case):
    # friendly_error wraps long lines: compare with whitespace collapsed.
    text = " ".join(results[case]["text"].split())
    assert [m for m in CASES[case] if " ".join(m.split()) not in text] == [], text[-600:]


@pytest.mark.parametrize("case", list(CASES))
def test_start_creates_nothing(results, case):
    assert results[case]["created"] == []


@pytest.mark.parametrize("case", list(CASES))
def test_start_no_write_network_or_spawn(results, case):
    assert "TRIPWIRE" not in results[case]["text"]


@pytest.mark.parametrize("case", list(CASES))
def test_start_refusal_has_no_traceback(results, case):
    assert "Traceback" not in results[case]["text"]


def test_instance_refusal_comes_before_api_refusal(results):
    # Order (design.md A3): instance.start_check() -> telegram_endpoint.check().
    assert MSG_HOME_1 in " ".join(results["home_real"]["text"].split())


# ---- the tripwire itself must work, or the checks above prove nothing ---------------

@pytest.mark.parametrize("kind,code", [("write", 97), ("net", 98), ("spawn", 99), ("read", 97)])
def test_tripwire_selftest(scratch, kind, code):
    env = scrubbed_env(AFT_SCRATCH=str(scratch))
    proc = subprocess.run([PYTHON, str(GUARD), "--selftest", kind], env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == code


# ---- start_check boundaries in-process (no CLI run: an OK result would start a daemon) --

@pytest.fixture
def fake_home(scratch, monkeypatch):
    monkeypatch.setenv("HOME", str(scratch / "h"))
    return scratch


def test_start_check_socket_length_just_inside(fake_home, monkeypatch):
    d = _dir_of_len(fake_home, 107 - LONGEST_SUFFIX)
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(d))
    assert instance.start_check() == []


def test_start_check_socket_length_at_limit(fake_home, monkeypatch):
    d = _dir_of_len(fake_home, 108 - LONGEST_SUFFIX)
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(d))
    assert any("108 bytes" in line for line in instance.start_check())


def test_start_check_unset_is_empty(monkeypatch):
    monkeypatch.delenv("AIPAGER_INSTANCE_DIR", raising=False)
    monkeypatch.setenv("HOME", REAL_HOME)
    assert instance.start_check() == []


def test_start_check_trailing_slash_instance_ok(fake_home, monkeypatch):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(fake_home / "i") + "/")
    assert instance.start_check() == []


def test_start_check_home_inside_real_home_is_allowed_shape(fake_home, monkeypatch):
    # Only HOME == the real home is refused; a different folder is fine.
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(fake_home / "i"))
    monkeypatch.setenv("HOME", str(fake_home / "h"))
    assert instance.start_check() == []


def test_start_check_relative_dir_refused(fake_home, monkeypatch):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", "relative/dir")
    assert MSG_DIR in instance.start_check()


def test_start_check_symlink_to_real_home_refused(fake_home, monkeypatch):
    link = fake_home / "hl"
    link.symlink_to(REAL_HOME)
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(fake_home / "i"))
    monkeypatch.setenv("HOME", str(link))
    assert any(MSG_HOME_1 in line for line in instance.start_check())


def test_start_check_never_touches_the_instance_dir(fake_home, monkeypatch):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(fake_home / "i"))
    instance.start_check()
    assert os.listdir(fake_home / "i") == []
