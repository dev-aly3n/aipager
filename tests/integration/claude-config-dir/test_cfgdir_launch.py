"""``inject.launch_session`` with ``CLAUDE_CONFIG_DIR=/x`` in the session
env (entrypoints "launch_session ... Observable effects"; design success
criteria 1, 3, 8).

The spawn goes through the documented seam
``aipager.dtach.inject._create_subprocess_exec``: a fake process that
exits at once, so nothing real is ever started, and the recorded argv is
the command line that would have run.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from aipager.dtach.inject import launch_session

from _cfgdir_kit import has_hooks, rjson, wjson

CCD = "CLAUDE_CONFIG_DIR"
SID = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"


class _FakeProc:
    returncode = 1

    async def communicate(self):
        return b"", b"fake dtach: not run"

    async def wait(self):
        return 1


@pytest.fixture
def spawned(monkeypatch):
    seen: list[str] = []

    async def _fake(*args, **kwargs):
        seen.append(" ".join(str(a) for a in args))
        return _FakeProc()

    monkeypatch.setattr("aipager.dtach.inject._create_subprocess_exec", _fake)
    return seen


def _launch(cc, run_async, **kw):
    kw.setdefault("cwd", str(cc.work))
    return run_async(launch_session("cfgdirtest", mode="ask", **kw))


def _creds(path, *, fresh: bool):
    ms = int(time.time() * 1000) + (3_600_000 if fresh else -3_600_000)
    wjson(path, {"claudeAiOauth": {"accessToken": "sk-ant-oat01-" + "f" * 20,
                                   "refreshToken": "sk-ant-ort01-" + "r" * 20,
                                   "expiresAt": ms, "scopes": ["user:inference"]}})


# ---- hooks and answers before the spawn ------------------------------------------------

def test_launch_wires_hooks_into_the_config_dir(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _launch(cc, run_async)
    assert has_hooks(cc.x / "settings.json")


def test_launch_reaches_the_spawn(cc, run_async, spawned):
    """Guards the other launch tests: they assert on a real spawn attempt."""
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _launch(cc, run_async)
    assert spawned


def test_launch_leaves_the_default_settings_absent(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _launch(cc, run_async)
    assert not cc.home_settings.exists()


def test_launch_trusts_the_folder_in_the_config_dirs_file(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _launch(cc, run_async)
    projects = rjson(cc.x / ".claude.json").get("projects", {})
    assert projects.get(os.path.realpath(str(cc.work)), {}).get("hasTrustDialogAccepted") is True


def test_launch_never_writes_the_home_global_config(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _launch(cc, run_async)
    assert not cc.home_json.exists()


def test_launch_wires_a_value_changed_in_daemon_env_since_the_last_launch(
        cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _launch(cc, run_async)
    y = cc.tmp / "elsewhere" / "y"
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(y))
    _launch(cc, run_async)
    assert has_hooks(y / "settings.json")


# ---- the OAuth-token unset decision reads /x/.credentials.json -----------------------

def test_a_fresh_login_in_the_config_dir_unsets_the_env_token(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _creds(cc.x / ".credentials.json", fresh=True)
    _launch(cc, run_async)
    assert "unset CLAUDE_CODE_OAUTH_TOKEN;" in spawned[-1]


def test_a_fresh_login_only_in_the_home_does_not_unset_with_a_config_dir(
        cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _creds(cc.dot_claude / ".credentials.json", fresh=True)
    _launch(cc, run_async)
    assert "unset CLAUDE_CODE_OAUTH_TOKEN;" not in spawned[-1]


def test_an_expired_login_in_the_config_dir_does_not_unset(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _creds(cc.x / ".credentials.json", fresh=False)
    _launch(cc, run_async)
    assert "unset CLAUDE_CODE_OAUTH_TOKEN;" not in spawned[-1]


def test_a_fresh_login_in_the_default_home_unsets_without_a_config_dir(
        cc, run_async, spawned):
    """Control: unchanged behaviour with nothing set."""
    _creds(cc.dot_claude / ".credentials.json", fresh=True)
    _launch(cc, run_async)
    assert "unset CLAUDE_CODE_OAUTH_TOKEN;" in spawned[-1]


# ---- the expired-file stash: /x only, gate on the daemon's own env -------------------

def test_an_expired_config_dir_login_is_stashed_when_the_daemon_has_a_token(
        cc, run_async, spawned, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "d" * 20)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _creds(cc.x / ".credentials.json", fresh=False)
    _launch(cc, run_async)
    assert (cc.x / ".credentials.json.stale").exists()


def test_an_expired_home_login_is_not_stashed_with_a_config_dir(
        cc, run_async, spawned, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "d" * 20)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _creds(cc.dot_claude / ".credentials.json", fresh=False)
    _launch(cc, run_async)
    assert (cc.dot_claude / ".credentials.json").exists()


def test_an_expired_config_dir_login_stays_without_a_daemon_token(
        cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _creds(cc.x / ".credentials.json", fresh=False)
    _launch(cc, run_async)
    assert (cc.x / ".credentials.json").exists()


def test_a_daemon_env_token_alone_does_not_open_the_stash_gate(
        cc, run_async, spawned):
    """The gate is the daemon PROCESS env, not daemon.env (unchanged)."""
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x),
                      CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-" + "e" * 20)
    _creds(cc.x / ".credentials.json", fresh=False)
    _launch(cc, run_async)
    assert (cc.x / ".credentials.json").exists()


# ---- resume looks in /x/projects --------------------------------------------------------

def _transcript(root, sid=SID):
    p = root / "projects" / "-some-folder" / f"{sid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"type": "user"}) + "\n")
    return p


def test_resume_is_kept_when_the_transcript_is_in_the_config_dir(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _transcript(cc.x)
    _launch(cc, run_async, resume_id=SID)
    assert f"--resume {SID}" in spawned[-1]


def test_resume_is_dropped_when_the_transcript_is_only_in_the_home(cc, run_async, spawned):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    _transcript(cc.dot_claude)
    _launch(cc, run_async, resume_id=SID)
    assert "--resume" not in spawned[-1]


def test_resume_follows_the_last_config_dir_of_a_chain(cc, run_async, spawned):
    """P7: transcripts land in the final CLAUDE_CONFIG_DIR."""
    c2 = cc.tmp / "chain" / "c2"
    wjson(cc.home_settings, {"env": {CCD: str(cc.x)}})
    wjson(cc.x / "settings.json", {"env": {CCD: str(c2)}})
    _transcript(c2)
    _launch(cc, run_async, resume_id=SID)
    assert f"--resume {SID}" in spawned[-1]


def test_resume_is_kept_from_the_default_home_without_a_config_dir(cc, run_async, spawned):
    _transcript(cc.dot_claude)
    _launch(cc, run_async, resume_id=SID)
    assert f"--resume {SID}" in spawned[-1]
