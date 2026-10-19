"""Credential-probe cleanup, the logged-in source, the transcript
allow-list and the update-channel lookup follow ``CLAUDE_CONFIG_DIR``
(design success criteria 8 and 9; amendment A3).

The probe's ``claude -p`` goes through the documented seam
``aipager.claude_resolve._run_probe``: a fake that writes the transcript
folder real Claude Code would write (``<projects>/<cwd with every
non-alphanumeric character as "-">``) and answers ok.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from aipager import claude_resolve, self_update
from aipager.bot import outbox

from _cfgdir_kit import wjson

CCD = "CLAUDE_CONFIG_DIR"


def _slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _fake_binary(cc) -> str:
    p = cc.tmp / "bin" / "claude"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(0)\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.fixture
def probe(cc, monkeypatch):
    """Record where the fake probe wrote its transcript folder."""
    made: dict[str, Path] = {}

    def _run_probe(claude_path, env, cwd, timeout):
        value = env.get(CCD)
        home = Path(value) if value else cc.dot_claude
        folder = home / "projects" / _slug(str(cwd))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "probe.jsonl").write_text(json.dumps({"type": "user"}) + "\n")
        made["folder"] = folder
        made["cwd"] = Path(cwd)
        return 0, "ok\n"

    monkeypatch.setattr(claude_resolve, "_run_probe", _run_probe)
    return made


# ---- validate_credential cleanup ------------------------------------------------------

def test_the_probe_reports_valid(cc, probe):
    """Guards the cleanup tests: the fake probe ran."""
    check = claude_resolve.validate_credential(_fake_binary(cc), {CCD: str(cc.x)})
    assert check.state == "valid"


def test_the_probe_transcript_folder_in_the_config_dir_is_removed(cc, probe):
    claude_resolve.validate_credential(_fake_binary(cc), {CCD: str(cc.x)})
    assert not probe["folder"].exists()


def test_the_probe_transcript_folder_was_written_in_the_config_dir(cc, probe):
    claude_resolve.validate_credential(_fake_binary(cc), {CCD: str(cc.x)})
    assert probe["folder"].parent == cc.x / "projects"


def test_a_same_named_folder_in_the_home_is_kept(cc, probe):
    claude_resolve.validate_credential(_fake_binary(cc), {CCD: str(cc.x)})
    twin = cc.dot_claude / "projects" / probe["folder"].name
    twin.mkdir(parents=True)
    (twin / "keep.jsonl").write_text("{}\n")
    claude_resolve.validate_credential(_fake_binary(cc), {CCD: str(cc.x)})
    assert (twin / "keep.jsonl").exists()


def test_other_transcripts_in_the_config_dir_are_kept(cc, probe):
    other = cc.x / "projects" / "-home-alice-work"
    other.mkdir(parents=True)
    (other / "a.jsonl").write_text("{}\n")
    claude_resolve.validate_credential(_fake_binary(cc), {CCD: str(cc.x)})
    assert (other / "a.jsonl").exists()


def test_without_a_config_dir_the_home_probe_folder_is_removed(cc, probe):
    claude_resolve.validate_credential(_fake_binary(cc), {})
    assert not probe["folder"].exists()


# ---- detect_auth: a login file in /x is "file" ---------------------------------------

def _auth_binary(cc) -> str:
    payload = json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                          "apiProvider": "firstParty"})
    p = cc.tmp / "authbin" / "claude"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1:3] == ['auth', 'status']:\n"
        f"    sys.stdout.write({payload!r})\n    sys.exit(0)\n"
        "sys.exit(0)\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _login_file(path: Path):
    wjson(path, {"claudeAiOauth": {"accessToken": "sk-ant-oat01-" + "f" * 20,
                                   "expiresAt": 9_999_999_999_999}})


def test_a_login_file_in_the_config_dir_is_the_source(cc):
    _login_file(cc.x / ".credentials.json")
    env = {CCD: str(cc.x), "PATH": os.environ["PATH"]}
    status = claude_resolve.detect_auth(_auth_binary(cc), "2.1.303", env)
    assert status.source == "file"


def test_a_login_file_only_in_the_home_is_not_the_source_with_a_config_dir(cc):
    _login_file(cc.dot_claude / ".credentials.json")
    env = {CCD: str(cc.x), "PATH": os.environ["PATH"]}
    status = claude_resolve.detect_auth(_auth_binary(cc), "2.1.303", env)
    assert status.source != "file"


# ---- transcript_allowed ---------------------------------------------------------------

def _jsonl(root: Path, name="t.jsonl") -> str:
    p = root / "projects" / "-home-alice-work" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}\n")
    return str(p)


def test_a_transcript_under_daemon_envs_config_dir_is_allowed(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    assert outbox.transcript_allowed(_jsonl(cc.x)) is True


def test_a_transcript_under_the_process_config_dir_is_allowed(cc, monkeypatch):
    monkeypatch.setenv(CCD, str(cc.x))
    assert outbox.transcript_allowed(_jsonl(cc.x)) is True


def test_a_transcript_under_the_default_projects_is_still_allowed(cc, monkeypatch):
    """`~/.claude/projects` with `~` the (tmp) HOME."""
    monkeypatch.setenv("HOME", str(cc.home))
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    assert outbox.transcript_allowed(_jsonl(cc.dot_claude)) is True


def test_a_transcript_under_a_relative_config_dir_is_rejected(cc, monkeypatch):
    monkeypatch.chdir(cc.work)
    cc.set_daemon_env(CLAUDE_CONFIG_DIR="relcfg")
    assert outbox.transcript_allowed(_jsonl(cc.work / "relcfg")) is False


def test_a_transcript_under_an_unconfigured_folder_is_rejected(cc):
    assert outbox.transcript_allowed(_jsonl(cc.x)) is False


def test_a_non_jsonl_file_under_the_config_dir_is_rejected(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    assert outbox.transcript_allowed(_jsonl(cc.x, "t.txt")) is False


def test_a_folder_named_jsonl_under_the_config_dir_is_rejected(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    d = cc.x / "projects" / "-p" / "d.jsonl"
    d.mkdir(parents=True)
    assert outbox.transcript_allowed(str(d)) is False


def test_a_symlink_out_of_the_config_dir_is_rejected(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    outside = cc.tmp / "outside" / "secret.jsonl"
    outside.parent.mkdir(parents=True)
    outside.write_text("{}\n")
    link = cc.x / "projects" / "-p" / "l.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    assert outbox.transcript_allowed(str(link)) is False


def test_a_dotdot_escape_from_the_config_dir_is_rejected(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    outside = cc.tmp / "outside" / "secret.jsonl"
    outside.parent.mkdir(parents=True)
    outside.write_text("{}\n")
    (cc.x / "projects").mkdir(parents=True)
    sneaky = str(cc.x / "projects" / ".." / ".." / ".." / "outside" / "secret.jsonl")
    assert outbox.transcript_allowed(sneaky) is False


def test_the_config_dir_itself_outside_projects_is_rejected(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    p = cc.x / "loose.jsonl"
    p.write_text("{}\n")
    assert outbox.transcript_allowed(str(p)) is False


# ---- claude_channel_info ----------------------------------------------------------------

def test_the_channel_comes_from_the_config_dirs_settings(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wjson(cc.x / "settings.json", {"autoUpdatesChannel": "stable"})
    wjson(cc.home_settings, {"autoUpdatesChannel": "rc"})
    assert self_update.claude_channel_info()[1] == "stable"


def test_the_install_method_comes_from_the_config_dirs_global_config(cc):
    cc.set_daemon_env(CLAUDE_CONFIG_DIR=str(cc.x))
    wjson(cc.x / ".claude.json", {"installMethod": "native"})
    wjson(cc.home_json, {"installMethod": "npm-global"})
    assert self_update.claude_channel_info()[0] == "native"


def test_without_a_config_dir_the_channel_comes_from_the_home(cc):
    wjson(cc.home_settings, {"autoUpdatesChannel": "rc"})
    assert self_update.claude_channel_info()[1] == "rc"
