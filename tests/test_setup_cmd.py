"""Unit tests for `aipager setup` (aipager/setup_cmd.py).

Every Telegram call goes through faked seams (``telegram_api._http_json``
for getMe, ``telegram_api._send_message`` for the test send); deps,
daemon detection, live reload and the service installer are stubbed.
The command runs in-process through ``aipager.cli.main``.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

from aipager import scope as scope_mod
from aipager.scope import Member, Scope
from aipager.wizard import daemon_io as _daemon_io


@pytest.fixture(autouse=True)
def _no_live_daemon_socket(tmp_path, monkeypatch):
    """Never let a check here reach the operator's live daemon socket."""
    no_daemon = str(tmp_path / "no-daemon.sock")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", no_daemon)
    monkeypatch.setattr("aipager.status.SOCKET_PATH", no_daemon)


# The real reload path, saved at import (before any fixture stubs it).
REAL_LIVE_RELOAD = _daemon_io._live_reload

TOKEN = "123456789:AAHf3kLmQ9zXwV7bN2pR8sT4uY6cE1dG0jK"
SECRET = TOKEN.split(":", 1)[1]
TOKEN2 = "987654321:BBQw8eRt5yU1iO9pAs3dFg7hJk2lZx4cV6b"
CHAT = 424242
BOT = "example_bot"

SETUP_KEYS = {
    "command", "status", "ok", "exit_code", "error", "message", "fix",
    "dry_run", "bot_username", "chat_id", "role", "changed", "test_message",
    "deps", "settings_json", "daemon", "service", "warnings", "next_step",
}


class FakeTelegram:
    def __init__(self):
        self.get_me = ({"ok": True, "result": {"username": BOT}}, 200, "")
        self.send_result = (True, "", 200)
        self.urls: list[str] = []
        self.sends: list[tuple[int, str]] = []

    def http_json(self, url):
        self.urls.append(url)
        if url.endswith("/getMe"):
            return self.get_me
        return {"ok": True, "result": []}, 200, ""

    def send_message(self, token, chat_id, text):
        self.sends.append((chat_id, text))
        return self.send_result


class Env:
    def __init__(self, tmp_path, monkeypatch, capsys):
        from aipager import service
        from aipager.wizard import daemon_io, settings_patch, telegram_api

        self.tmp = tmp_path
        self.mp = monkeypatch
        self.capsys = capsys
        self.tg = FakeTelegram()
        monkeypatch.setattr(telegram_api, "_http_json", self.tg.http_json)
        monkeypatch.setattr(telegram_api, "_send_message", self.tg.send_message)
        self.deps_missing: set[str] = set()

        def _check_deps():
            return [settings_patch.DepStatus(
                n, None if n in self.deps_missing else f"/usr/bin/{n}", f"fix-{n}")
                for n in ("dtach", "claude", "aipager-hook", "aipager-statusline")]

        monkeypatch.setattr(settings_patch, "check_deps", _check_deps)
        monkeypatch.setattr(settings_patch.shutil, "which",
                            lambda name: f"/usr/bin/{name}")
        self.daemon_pid = None
        monkeypatch.setattr(daemon_io, "_detect_daemon_running",
                            lambda: self.daemon_pid)
        self.reloads: list[int] = []
        self.reload_outcome = ("reloaded", None)

        def _live_reload():
            self.reloads.append(1)
            return self.reload_outcome

        monkeypatch.setattr(daemon_io, "_live_reload", _live_reload)
        self.installs: list[bool] = []
        self.install_rc = 0

        def _install(*, yes):
            self.installs.append(yes)
            return self.install_rc

        monkeypatch.setattr(service, "install_service", _install)
        monkeypatch.setattr(service, "_platform", lambda: "linux")
        # A detect-chat that a broken flag check let through must end at
        # once (a timeout), never really wait.
        from aipager import setup_detect
        clock = [0.0]
        monkeypatch.setattr(setup_detect, "_monotonic", lambda: clock[0])
        monkeypatch.setattr(setup_detect, "_sleep",
                            lambda s: clock.__setitem__(0, clock[0] + s))
        self.token_file = tmp_path / "token.txt"
        self.token_file.write_text(TOKEN + "\n")
        os.chmod(self.token_file, 0o600)

    @property
    def yaml_path(self) -> Path:
        return scope_mod.CONFIG_PATH

    @property
    def settings_path(self) -> Path:
        from aipager.wizard import settings_patch
        return settings_patch.CLAUDE_SETTINGS

    def run(self, *argv, stdin=None):
        self.capsys.readouterr()
        self.mp.setattr(sys, "argv", ["aipager", *argv])
        if stdin is not None:
            self.mp.setattr(sys, "stdin", stdin)
        from aipager import cli
        try:
            cli.main()
            code = "returned"
        except SystemExit as e:
            code = e.code
        except Exception as e:  # recorded, so a leak fails by assertion
            code = f"raised {type(e).__name__}"
        out = self.capsys.readouterr()
        return code, out.out, out.err

    def run_json(self, *argv, stdin=None):
        code, out, err = self.run(*argv, "--json", stdin=stdin)
        try:
            doc = json.loads(out)
        except ValueError:
            doc = None
        return code, doc, out, err

    def setup(self, *extra, chat=CHAT, json_out=True):
        argv = ["setup", "--token-file", str(self.token_file),
                "--chat-id", str(chat), *extra]
        if json_out:
            return self.run_json(*argv)
        return self.run(*argv)

    def snapshot(self):
        snap = {}
        for p in sorted(self.tmp.rglob("*")):
            if p.is_file():
                snap[str(p)] = (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
        return snap

    def audit_lines(self):
        from aipager import audit
        p = Path(audit.AUDIT_LOG_PATH)
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    return Env(tmp_path, monkeypatch, capsys)


def _write_yaml(scopes, token=TOKEN):
    scope_mod.dump_scopes(scopes, token, scope_mod.CONFIG_PATH)


def _dm(cid, role="owner", label="owner DM"):
    return Scope(chat_id=cid, kind="dm", label=label,
                 members=(Member(id=cid, label="owner", role=role),))


GROUP = Scope(chat_id=-1001234, kind="group", label="team",
              members=(Member(id=555, label="bob", role="user"),))


# ----- fresh install -----

def test_fresh_install_writes_the_wizard_result(env):
    code, doc, out, err = env.setup()
    assert code == 0, "unexpected exit code"
    assert doc["status"] == "installed" and doc["ok"] is True
    assert doc["changed"] == ["bot_token", "owner_dm", "settings_json"]
    data = yaml.safe_load(env.yaml_path.read_text())
    assert data["bot_token"] == TOKEN
    assert data["scopes"] == [{
        "chat_id": CHAT, "kind": "dm", "label": "owner DM",
        "members": [{"id": CHAT, "label": "owner", "role": "owner"}]}]
    assert stat.S_IMODE(env.yaml_path.stat().st_mode) == 0o600
    settings = json.loads(env.settings_path.read_text())
    assert settings["statusLine"]["command"] == "/usr/bin/aipager-statusline"
    assert len(env.tg.sends) == 1
    assert env.tg.sends[0][1].startswith("aipager is set up for you.")
    assert [a["action"] for a in env.audit_lines()] == ["grant-owner"]
    assert doc["test_message"] == "sent"
    assert doc["bot_username"] == BOT and doc["chat_id"] == CHAT


def test_fresh_install_via_stdin(env):
    code, doc, out, err = env.run_json(
        "setup", "--token-stdin", "--chat-id", str(CHAT),
        stdin=io.StringIO(TOKEN + "\n"))
    assert code == 0, "unexpected exit code"
    assert yaml.safe_load(env.yaml_path.read_text())["bot_token"] == TOKEN


def test_json_document_has_every_key_on_success_and_error(env):
    code, doc, _o, _e = env.setup()
    assert set(doc) == SETUP_KEYS and doc["exit_code"] == 0
    code, doc, _o, _e = env.run_json("setup", "--chat-id", "5")
    assert code == 2 and set(doc) == SETUP_KEYS
    assert doc["status"] == "error" and doc["error"] == "missing_token_source"
    assert "--token-file PATH or --token-stdin" in doc["fix"]


def test_plain_unchanged_rerun_says_nothing_to_change(env):
    assert env.setup()[0] == 0
    code, out, err = env.setup(json_out=False)
    assert code == 0
    assert "Nothing to change" in out


# ----- token hygiene -----

def _assert_no_token(*texts):
    # pytest.fail with a fixed message: an `assert TOKEN not in t` would
    # print t (which then holds the token) in the failure report.
    for t in texts:
        if TOKEN in t or SECRET in t:
            pytest.fail("token leaked", pytrace=False)


def test_token_never_in_argv_output_or_json(env, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    code, doc, out, err = env.setup()
    assert code == 0
    _assert_no_token(" ".join(sys.argv), out, err, caplog.text, json.dumps(doc))
    for p in env.tmp.rglob("*"):
        if p.is_file() and p not in (env.yaml_path, env.token_file):
            _assert_no_token(p.read_text(errors="replace"))


@pytest.mark.parametrize("flag", ["--token", "--bot-token"])
def test_token_on_command_line_refused_without_echo(env, flag):
    code, doc, out, err = env.run_json("setup", flag, TOKEN, "--chat-id", "5")
    assert code == 2
    assert doc["error"] == "token_on_command_line"
    _assert_no_token(out, err)
    assert env.tg.urls == [] and not env.yaml_path.exists()


def test_token_equals_form_refused(env):
    code, doc, out, err = env.run_json("setup", f"--token={TOKEN}",
                                       "--chat-id", "5")
    assert code == 2 and doc["error"] == "token_on_command_line"
    _assert_no_token(out, err)


def test_abbreviated_flags_are_not_accepted(env):
    code, out, err = env.run("setup", "--token-f", str(env.token_file),
                             "--chat-id", str(CHAT))
    assert code == 2
    assert env.tg.urls == [] and not env.yaml_path.exists()
    code, out, err = env.run("setup", "--token-file", str(env.token_file),
                             "--chat-id", str(CHAT), "--dry")
    assert code == 2
    assert env.tg.urls == []


def test_argparse_errors_never_echo_a_token(env):
    code, out, err = env.run("setup", TOKEN)
    assert code == 2
    assert "invalid choice" in err
    _assert_no_token(out, err)
    code, out, err = env.run("version", TOKEN)
    assert code == 2
    assert "unrecognized arguments" in err
    _assert_no_token(out, err)


def test_garbage_token_file_is_refused_before_any_http(env):
    env.token_file.write_text("hello this is not a token\n")
    code, doc, out, err = env.setup()
    assert code == 2 and doc["error"] == "token_malformed"
    assert env.tg.urls == []
    assert "hello" not in out + err


def test_token_with_text_around_it_is_accepted(env):
    env.token_file.write_text(f'# my bot\nTOKEN="{TOKEN}"\n')
    code, doc, _o, err = env.setup()
    assert code == 0, "unexpected exit code"


def test_token_file_must_be_a_regular_file(env):
    d = env.tmp / "adir"
    d.mkdir()
    code, doc, _o, _e = env.run_json("setup", "--token-file", str(d),
                                     "--chat-id", str(CHAT))
    assert code == 2 and doc["error"] == "token_file_unreadable"
    assert env.tg.urls == []


def test_token_file_device_is_not_a_regular_file(env):
    """A readable non-regular file (a character device) is refused by the
    regular-file check itself, not by a later read error."""
    code, doc, _o, _e = env.run_json("setup", "--token-file", os.devnull,
                                     "--chat-id", str(CHAT))
    assert code == 2 and doc["error"] == "token_file_unreadable"
    assert "not a regular file" in doc["message"]
    assert env.tg.urls == []


def test_token_file_missing(env):
    code, doc, _o, _e = env.run_json("setup", "--token-file",
                                     str(env.tmp / "nope"), "--chat-id", str(CHAT))
    assert code == 2 and doc["error"] == "token_file_unreadable"


def test_oversize_token_file_is_refused(env):
    env.token_file.write_text(TOKEN + "\n" + "x" * 5000)
    code, doc, _o, _e = env.setup()
    assert code == 2 and doc["error"] == "token_malformed"
    assert env.tg.urls == []


def test_undecodable_token_file(env):
    env.token_file.write_bytes(b"\xff\xfe\x00" + TOKEN.encode())
    code, doc, _o, _e = env.setup()
    assert code == 2 and doc["error"] == "token_file_unreadable"


def test_shared_token_file_warns(env):
    os.chmod(env.token_file, 0o644)
    code, doc, _o, _e = env.setup()
    assert code == 0
    assert [w["code"] for w in doc["warnings"]] == ["token_file_shared"]


class _TtyStdin(io.StringIO):
    def isatty(self):
        return True


def test_token_stdin_refuses_a_terminal(env):
    code, doc, _o, _e = env.run_json(
        "setup", "--token-stdin", "--chat-id", str(CHAT),
        stdin=_TtyStdin(TOKEN))
    assert code == 2 and doc["error"] == "token_stdin_is_tty"
    assert env.tg.urls == []


def test_token_stdin_oversize(env):
    code, doc, _o, _e = env.run_json(
        "setup", "--token-stdin", "--chat-id", str(CHAT),
        stdin=io.StringIO(TOKEN + "y" * 5000))
    assert code == 2 and doc["error"] == "token_malformed"


def test_both_token_sources_conflict(env):
    code, doc, _o, _e = env.run_json(
        "setup", "--token-stdin", "--token-file", str(env.token_file),
        "--chat-id", str(CHAT), stdin=io.StringIO(TOKEN))
    assert code == 2 and doc["error"] == "token_source_conflict"


def test_scrub_removes_token_secret_and_shapes():
    from aipager.setup_cmd import _scrub
    for text, secret in ((f"a {TOKEN} b", TOKEN), (f"secret={SECRET}", SECRET),
                         (f"other {TOKEN2}", TOKEN2)):
        if secret in _scrub(text, TOKEN):
            pytest.fail("_scrub left a token in place", pytrace=False)
    assert _scrub("plain text", TOKEN) == "plain text"


def test_run_repr_hides_token():
    from aipager.setup_cmd import _Run
    r = _Run(command="setup", as_json=True, doc={}, token=TOKEN)
    if TOKEN in repr(r):
        pytest.fail("token in repr", pytrace=False)


def test_plain_error_path_is_scrubbed(env):
    env.tg.send_result = (False, f"Bad Request: weird {TOKEN}", 400)
    code, out, err = env.setup(json_out=False)
    assert code == 1
    assert "Telegram refused the test message" in err
    _assert_no_token(out, err)


def test_json_path_is_scrubbed(env):
    env.tg.send_result = (False, f"Bad Request: weird {TOKEN}", 400)
    code, doc, out, err = env.setup()
    assert code == 1 and doc["error"] == "test_send_failed"
    _assert_no_token(out, err)


def test_internal_error_is_caught_and_scrubbed(env, monkeypatch):
    from aipager.wizard import settings_patch

    def boom():
        raise RuntimeError(f"exploded with {TOKEN}")

    monkeypatch.setattr(settings_patch, "check_deps", boom)
    code, out, err = env.setup(json_out=False)
    assert code == 1
    assert "internal" not in out
    assert "RuntimeError" in err
    _assert_no_token(out, err)


def test_internal_error_json(env, monkeypatch):
    from aipager.wizard import settings_patch

    def boom():
        raise SystemExit(2)

    monkeypatch.setattr(settings_patch, "check_deps", boom)
    code, doc, out, err = env.setup()
    assert code == 1 and doc["error"] == "internal_error"


# ----- Telegram refusals -----

@pytest.mark.parametrize("status", [401, 404])
def test_rejected_token_exits_4_and_writes_nothing(env, status):
    env.tg.get_me = ({"ok": False, "description": "Unauthorized"}, status,
                     "Unauthorized")
    before = env.snapshot()
    code, doc, _o, _e = env.setup()
    assert code == 4 and doc["error"] == "token_rejected"
    assert env.tg.sends == []
    assert env.snapshot() == before


def test_network_failure_on_getme_exits_7(env):
    env.tg.get_me = (None, None, "network: down")
    code, doc, _o, _e = env.setup()
    assert code == 7 and doc["error"] == "telegram_unreachable"


@pytest.mark.parametrize("desc,error", [
    ("Bad Request: chat not found", "chat_not_started"),
    ("Forbidden: bot can't initiate conversation with a user", "chat_not_started"),
    ("Forbidden: bot was blocked by the user", "bot_blocked"),
])
def test_test_send_refusals_exit_5_and_write_nothing(env, desc, error):
    env.tg.send_result = (False, desc, 403)
    before = env.snapshot()
    code, doc, _o, _e = env.setup()
    assert code == 5 and doc["error"] == error
    assert env.snapshot() == before
    if error == "chat_not_started":
        assert f"Open t.me/{BOT} and press Start, then run this again." in doc["fix"]
    else:
        assert "Unblock" in doc["fix"]
        assert "press Start, then run this again." in doc["fix"]


def test_test_send_network_failure_exits_7(env):
    env.tg.send_result = (False, "network: down", None)
    code, doc, _o, _e = env.setup()
    assert code == 7 and not env.yaml_path.exists()


# ----- local checks -----

def test_missing_deps_exit_3_and_write_nothing(env):
    env.deps_missing = {"dtach", "aipager-hook"}
    before = env.snapshot()
    code, doc, _o, _e = env.setup()
    assert code == 3 and doc["error"] == "deps_missing"
    assert "fix-dtach" in doc["fix"] and "fix-aipager-hook" in doc["fix"]
    assert env.tg.sends == []
    assert env.snapshot() == before
    assert doc["deps"]["ok"] is False


def test_invalid_settings_json_blocks_the_yaml_write(env):
    env.settings_path.parent.mkdir(parents=True, exist_ok=True)
    env.settings_path.write_text("{ not json")
    before = env.snapshot()
    code, doc, _o, _e = env.setup()
    assert code == 1 and doc["error"] == "settings_invalid"
    assert env.tg.sends == []
    assert env.snapshot() == before


def test_exit_priority(env):
    _write_yaml([_dm(CHAT)], token=TOKEN2)
    env.deps_missing = {"dtach"}
    code, doc, _o, _e = env.setup()
    assert code == 6 and doc["error"] == "existing_install"
    env.settings_path.parent.mkdir(parents=True, exist_ok=True)
    env.settings_path.write_text("[]x")
    _write_yaml([_dm(CHAT)], token=TOKEN)
    code, doc, _o, _e = env.setup()
    assert code == 1 and doc["error"] == "settings_invalid"
    env.yaml_path.write_text("scopes: [oops\n")
    code, doc, _o, _e = env.setup("--force")
    assert code == 1 and doc["error"] == "config_malformed"


def test_malformed_config_never_overwritten_even_with_force(env):
    env.yaml_path.parent.mkdir(parents=True, exist_ok=True)
    env.yaml_path.write_text("bot_token: x\nscopes: [oops\n")
    before = env.snapshot()
    code, doc, _o, _e = env.setup("--force")
    assert code == 1 and doc["error"] == "config_malformed"
    assert env.snapshot() == before
    assert env.tg.sends == []


# ----- idempotence and --force -----

def test_same_input_rerun_is_unchanged_without_a_send(env):
    assert env.setup()[0] == 0
    env.tg.sends.clear()
    before = env.snapshot()
    code, doc, _o, _e = env.setup()
    assert code == 0 and doc["status"] == "unchanged" and doc["changed"] == []
    assert doc["test_message"] == "not_needed"
    assert env.tg.sends == []
    assert env.snapshot() == before


def test_token_change_needs_force(env):
    _write_yaml([_dm(CHAT), GROUP], token=TOKEN2)
    before = env.yaml_path.read_bytes()
    code, doc, _o, _e = env.setup()
    assert code == 6 and "--force" in doc["fix"]
    assert env.yaml_path.read_bytes() == before
    code, doc, _o, _e = env.setup("--force")
    assert code == 0 and doc["changed"][:1] == ["bot_token"]
    scopes, tok = scope_mod.load_scopes(scope_mod.CONFIG_PATH)
    assert tok == TOKEN and scopes == [_dm(CHAT), GROUP]


def test_chat_change_needs_force_and_keeps_groups(env):
    _write_yaml([_dm(111), GROUP])
    code, doc, _o, _e = env.setup()
    assert code == 6 and "--force" in doc["fix"] and "111" in doc["fix"]
    code, doc, _o, _e = env.setup("--force")
    assert code == 0 and "owner_dm" in doc["changed"]
    scopes, _tok = scope_mod.load_scopes(scope_mod.CONFIG_PATH)
    assert GROUP in scopes
    assert [s.chat_id for s in scopes if s.kind == "dm"] == [CHAT]


def test_role_change_needs_force(env):
    _write_yaml([_dm(CHAT, role="admin"), GROUP])
    code, doc, _o, _e = env.setup()
    assert code == 6
    code, doc, _o, _e = env.setup("--force")
    assert code == 0 and doc["changed"][:1] == ["role"]
    scopes, _tok = scope_mod.load_scopes(scope_mod.CONFIG_PATH)
    assert scopes == [_dm(CHAT, role="owner"), GROUP]
    assert [a["action"] for a in env.audit_lines()] == ["grant-owner"]


def test_ambiguous_operator_dm_refused_even_with_force(env):
    _write_yaml([_dm(111), _dm(222)])
    before = env.snapshot()
    code, doc, _o, _e = env.setup("--force")
    assert code == 1 and doc["error"] == "ambiguous_install"
    assert env.snapshot() == before


def test_grant_owner_audited_only_when_newly_granted(env):
    assert env.setup("--role", "admin")[0] == 0
    assert env.audit_lines() == []
    assert env.setup("--role", "owner", "--force")[0] == 0
    assert len(env.audit_lines()) == 1
    assert env.setup()[0] == 0
    assert len(env.audit_lines()) == 1


# ----- dry run -----

def test_dry_run_writes_and_sends_nothing(env):
    before = env.snapshot()
    code, doc, _o, _e = env.setup("--dry-run", "--service")
    assert code == 0 and doc["status"] == "dry_run" and doc["dry_run"] is True
    assert doc["changed"] == ["bot_token", "owner_dm", "settings_json", "service"]
    assert doc["test_message"] == "skipped_dry_run"
    assert doc["service"]["result"] == "would_install"
    assert env.tg.sends == [] and env.installs == []
    assert env.snapshot() == before
    assert env.audit_lines() == []


def test_dry_run_reports_refusals_with_their_code(env):
    _write_yaml([_dm(111)])
    code, doc, _o, _e = env.setup("--dry-run")
    assert code == 6 and doc["dry_run"] is True and doc["changed"] == [
        "owner_dm", "settings_json"]


def test_dry_run_does_not_migrate_v1(env, monkeypatch):
    _v1(env, monkeypatch)
    before = env.snapshot()
    code, doc, _o, _e = env.setup("--dry-run", "--role", "admin")
    assert code == 0 and "migrated_v1" in doc["changed"]
    assert env.snapshot() == before


# ----- v1 migration -----

def _v1(env, monkeypatch, chat=CHAT, token=TOKEN):
    from aipager.wizard import _constants
    _constants.CONFIG_ENV.parent.mkdir(parents=True, exist_ok=True)
    _constants.CONFIG_ENV.write_text(
        f"CLAUDE_TG_BOT_TOKEN={token}\nCLAUDE_TG_CHAT_ID={chat}\n")
    monkeypatch.setattr("aipager.config.BOT_TOKEN", token)
    monkeypatch.setattr("aipager.config.CHAT_ID", str(chat))
    monkeypatch.setattr("aipager.config._XDG_CONFIG", _constants.CONFIG_ENV)
    monkeypatch.setattr("aipager.team.TEAM_CONFIG_PATH", env.tmp / "no-team.yaml")


def test_v1_install_is_migrated_then_planned(env, monkeypatch):
    _v1(env, monkeypatch)
    code, doc, _o, err = env.setup("--role", "admin")
    assert code == 0, "unexpected exit code"
    assert doc["changed"][:1] == ["migrated_v1"]
    scopes, tok = scope_mod.load_scopes(scope_mod.CONFIG_PATH)
    assert tok == TOKEN and scopes == [_dm(CHAT, role="admin")]


def test_v1_refusal_leaves_only_config_env(env, monkeypatch):
    _v1(env, monkeypatch)
    before = env.snapshot()
    code, doc, _o, _e = env.setup()          # --role owner: a role change
    assert code == 6 and doc["error"] == "existing_install"
    assert env.snapshot() == before
    assert not env.yaml_path.exists()


def test_recomputed_plan_after_migration_must_match(env, monkeypatch):
    from aipager import migrate
    _v1(env, monkeypatch)

    def _other_migration():
        _write_yaml([_dm(999, role="admin")])
        return True

    monkeypatch.setattr(migrate, "migrate_to_v2", _other_migration)
    code, doc, _o, _e = env.setup("--role", "admin")
    assert code == 1 and doc["error"] == "write_failed"
    assert doc["changed"] == ["migrated_v1"]
    scopes, _tok = scope_mod.load_scopes(scope_mod.CONFIG_PATH)
    assert scopes == [_dm(999, role="admin")]


# ----- daemon and service -----

def test_json_stdout_carries_only_the_document(env, monkeypatch):
    from aipager import service

    def _noisy(*, yes):
        print("installer noise on stdout")
        return 0

    monkeypatch.setattr(service, "install_service", _noisy)
    code, doc, out, err = env.setup("--service")
    assert code == 0 and doc is not None
    assert "installer noise" in err and "installer noise" not in out


def test_service_installed_with_yes(env):
    code, doc, _o, _e = env.setup("--service")
    assert code == 0 and env.installs == [True]
    assert doc["service"] == {"requested": True, "result": "installed"}
    assert "service" in doc["changed"]


def test_service_skipped_while_daemon_runs(env):
    env.daemon_pid = 4321
    code, doc, _o, _e = env.setup("--service")
    assert code == 0 and env.installs == []
    assert doc["service"]["result"] == "skipped_daemon_running"


def test_service_failure_exits_1_with_config_written(env):
    env.install_rc = 2
    code, doc, _o, _e = env.setup("--service")
    assert code == 1 and doc["error"] == "service_failed"
    assert env.yaml_path.exists()
    assert "owner_dm" in doc["changed"]


def test_service_install_ignores_the_stale_config_snapshot(env, monkeypatch):
    import aipager.config  # noqa: F401  (imported, unconfigured, before the run)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "")
    monkeypatch.setattr("aipager.config.CHAT_ID", "")
    monkeypatch.setattr("aipager.config.SCOPES", None)
    monkeypatch.setattr("aipager.config.CONFIG_ERROR", None)
    code, doc, _o, err = env.setup("--service")
    assert code == 0, "unexpected exit code"
    assert env.installs == [True]


def test_token_change_needs_restart_and_sends_no_reload(env):
    _write_yaml([_dm(CHAT)], token=TOKEN2)
    env.daemon_pid = 4321
    code, doc, _o, _e = env.setup("--force")
    assert code == 0
    assert env.reloads == []
    assert doc["daemon"]["restart_needed"] is True
    assert "aipager service stop" in doc["next_step"]
    assert "aipager service start" in doc["next_step"]


def test_scope_change_live_reloads_a_running_daemon(env):
    _write_yaml([_dm(CHAT, role="admin")])
    env.daemon_pid = 4321
    code, doc, _o, _e = env.setup("--force")
    assert code == 0 and env.reloads == [1]
    assert doc["daemon"]["reload"] == "reloaded"
    assert doc["daemon"]["restart_needed"] is False


def test_reload_refused_is_a_warning(env):
    _write_yaml([_dm(CHAT, role="admin")])
    env.daemon_pid = 4321
    env.reload_outcome = ("refused", "bad policy")
    code, doc, _o, _e = env.setup("--force")
    assert code == 0
    assert [w["code"] for w in doc["warnings"]] == ["reload_refused"]


# ----- flag validation -----

@pytest.mark.parametrize("value", ["abc", "0", "1.5", "12345678901234567", ""])
def test_bad_chat_id(env, value):
    code, doc, _o, _e = env.run_json("setup", "--token-file", str(env.token_file),
                                     "--chat-id", value)
    assert code == 2 and doc["error"] == "bad_chat_id"
    assert "--chat-id" in doc["fix"]
    assert env.tg.urls == []


def test_negative_chat_id_points_at_config(env):
    code, doc, _o, _e = env.run_json("setup", "--token-file", str(env.token_file),
                                     "--chat-id=-100123")
    assert code == 2 and doc["error"] == "bad_chat_id"
    assert "aipager config" in doc["message"]


def test_missing_chat_id(env):
    code, doc, _o, _e = env.run_json("setup", "--token-file", str(env.token_file))
    assert code == 2 and doc["error"] == "usage" and "--chat-id" in doc["fix"]


def test_bad_role(env):
    code, doc, _o, _e = env.setup("--role", "god")
    assert code == 2 and doc["error"] == "bad_role"


@pytest.mark.parametrize("extra", [["--chat-id", "5"], ["--role", "admin"],
                                   ["--service"], ["--force"], ["--dry-run"]])
def test_install_flags_rejected_with_detect_chat(env, extra):
    code, doc, _o, _e = env.run_json("setup", "detect-chat", "--token-file",
                                     str(env.token_file), *extra)
    assert code == 2 and doc["error"] == "usage"
    assert doc["command"] == "detect-chat"
    assert env.tg.urls == []


def test_timeout_rejected_without_detect_chat(env):
    code, doc, _o, _e = env.setup("--timeout", "5")
    assert code == 2 and doc["error"] == "usage" and "--timeout" in doc["fix"]


# ----- pure planning -----

def test_plan_fresh():
    from aipager.setup_cmd import _plan_config
    p = _plan_config([], "", TOKEN, CHAT, "owner")
    assert p.fresh and p.changes == ["bot_token", "owner_dm"]
    assert p.scopes == [_dm(CHAT)] and p.grants_owner


def test_plan_same_is_no_change():
    from aipager.setup_cmd import _plan_config
    p = _plan_config([_dm(CHAT), GROUP], TOKEN, TOKEN, CHAT, "owner")
    assert p.changes == [] and not p.needs_force
    assert p.scopes == [_dm(CHAT), GROUP]


def test_plan_role_change_keeps_everything_else():
    from aipager.setup_cmd import _plan_config
    dm = Scope(chat_id=CHAT, kind="dm", label="mine", deny_tools=("Bash",),
               members=(Member(id=CHAT, label="me", role="admin"),
                        Member(id=7, label="pal", role="user")))
    p = _plan_config([dm], TOKEN, TOKEN, CHAT, "owner")
    assert p.changes == ["role"] and p.needs_force and p.grants_owner
    assert p.scopes[0].label == "mine" and p.scopes[0].deny_tools == ("Bash",)
    assert [(m.id, m.role) for m in p.scopes[0].members] == [(CHAT, "owner"),
                                                            (7, "user")]


def test_plan_chat_change_replaces_operator_dm_keeps_groups():
    from aipager.setup_cmd import _plan_config
    other = _dm(333, role="user", label="friend")
    other = Scope(chat_id=333, kind="dm", label="friend",
                  members=(Member(id=1, label="x", role="user"),))
    p = _plan_config([_dm(111), GROUP, other], TOKEN, TOKEN, CHAT, "owner")
    assert p.changes == ["owner_dm"] and p.replaced_chat == 111
    assert p.scopes == [GROUP, other, _dm(CHAT)]


def test_plan_token_change():
    from aipager.setup_cmd import _plan_config
    p = _plan_config([_dm(CHAT), GROUP], TOKEN2, TOKEN, CHAT, "owner")
    assert p.changes == ["bot_token"] and p.needs_force
    assert p.scopes == [_dm(CHAT), GROUP]


def test_plan_migrated_admin_vs_owner():
    from aipager.setup_cmd import _plan_config
    p = _plan_config([_dm(CHAT, "admin")], TOKEN, TOKEN, CHAT, "admin",
                     from_v1=True)
    assert p.changes == ["migrated_v1"] and not p.needs_force
    p = _plan_config([_dm(CHAT, "admin")], TOKEN, TOKEN, CHAT, "owner",
                     from_v1=True)
    assert p.changes == ["migrated_v1", "role"] and p.needs_force


def test_operator_dm_rules():
    from aipager.setup_cmd import _operator_dm
    assert _operator_dm([GROUP]) == (None, False)
    assert _operator_dm([_dm(1, "admin")]) == (_dm(1, "admin"), False)
    assert _operator_dm([_dm(1, "admin"), _dm(2)]) == (_dm(2), False)
    assert _operator_dm([_dm(1), _dm(2)]) == (None, True)
    assert _operator_dm([_dm(1, "admin"), _dm(2, "user")]) == (None, True)


def test_plan_ambiguous():
    from aipager.setup_cmd import _plan_config
    p = _plan_config([_dm(1), _dm(2)], TOKEN, TOKEN, CHAT, "owner")
    assert p.error == "ambiguous_install"


def test_no_prompt_and_no_tty_needed(env, monkeypatch):
    """The real TTY guard is restored and every prompt is booby-trapped:
    setup must reach neither."""
    import builtins

    import questionary

    from aipager import errors
    from tests.conftest import REAL_REQUIRE_INTERACTIVE
    calls = []

    def _spy(command=None):
        calls.append(command)
        return REAL_REQUIRE_INTERACTIVE(command)

    monkeypatch.setattr(errors, "require_interactive", _spy)

    def _trap(*a, **k):
        raise AssertionError("prompted")

    for name in ("text", "password", "select", "confirm"):
        monkeypatch.setattr(questionary, name, _trap)
    monkeypatch.setattr(builtins, "input", _trap)
    code, doc, _o, err = env.run_json(
        "setup", "--token-stdin", "--chat-id", str(CHAT),
        stdin=io.StringIO(TOKEN))
    assert code == 0, "unexpected exit code"
    assert calls == []


# ----- fix iteration 2 -----

def _fail_if_token_in(what, *texts):
    """Fail with a fixed message: never echo text that may hold a token."""
    for t in texts:
        if TOKEN in t or SECRET in t:
            pytest.fail(f"token leaked in {what}", pytrace=False)


def test_empty_role_is_a_bad_role_not_the_default(env):
    code, doc, _o, _e = env.setup("--role", "")
    assert code == 2 and doc["error"] == "bad_role" and "--role" in doc["fix"]
    assert env.tg.urls == [] and not env.yaml_path.exists()


def test_empty_token_file_path_is_unreadable(env):
    code, doc, _o, _e = env.run_json("setup", "--token-file", "",
                                     "--chat-id", str(CHAT))
    assert code == 2 and doc["error"] == "token_file_unreadable"
    assert env.tg.urls == []


def test_empty_timeout_without_detect_chat_is_a_usage_error(env):
    code, doc, _o, _e = env.setup("--timeout", "")
    assert code == 2 and doc["error"] == "usage" and "--timeout" in doc["fix"]


@pytest.mark.parametrize("extra", [["--role", ""], ["--chat-id", ""]])
def test_empty_install_flags_rejected_with_detect_chat(env, extra):
    code, doc, _o, _e = env.run_json("setup", "detect-chat", "--token-file",
                                     str(env.token_file), *extra)
    assert code == 2 and doc["error"] == "usage"
    assert env.tg.urls == []


@pytest.mark.parametrize("how", ["none", "raises"])
def test_service_not_installed_when_the_written_yaml_does_not_load(
        env, monkeypatch, how):
    """The fresh load_scopes before the installer is the guard: the yaml
    is broken only for the service step (after the write and reload)."""
    from aipager import setup_cmd

    real_after = setup_cmd._after_yaml_change

    def _broken_load(path):
        if how == "raises":
            raise scope_mod.ScopeConfigError("broken")
        return None

    def _after_then_break(*a, **kw):
        real_after(*a, **kw)
        monkeypatch.setattr(scope_mod, "load_scopes", _broken_load)

    monkeypatch.setattr(setup_cmd, "_after_yaml_change", _after_then_break)
    code, doc, _o, _e = env.setup("--service")
    assert code == 1 and doc["error"] == "service_failed"
    assert doc["service"]["result"] == "failed"
    assert env.installs == []


def test_scope_change_restart_wording_names_no_token(env):
    _write_yaml([_dm(CHAT, role="admin")])
    env.daemon_pid = -1
    env.reload_outcome = ("not_reloaded", None)
    code, doc, _o, _e = env.setup("--force")
    assert code == 0 and doc["changed"][0] == "role"
    assert doc["daemon"]["restart_needed"] is True
    assert "bot token" not in doc["next_step"]
    assert "Restart the daemon to apply the change" in doc["next_step"]


def test_token_change_restart_wording_names_the_token(env):
    _write_yaml([_dm(CHAT)], token=TOKEN2)
    env.daemon_pid = 4321
    code, doc, _o, _e = env.setup("--force")
    assert code == 0
    assert "Restart the daemon to use the new bot token" in doc["next_step"]


def test_daemon_detection_error_counts_as_running_for_the_service(env, monkeypatch):
    from aipager.wizard import daemon_io

    def _boom():
        raise RuntimeError("socket probe failed")

    monkeypatch.setattr(daemon_io, "_detect_daemon_running", _boom)
    code, doc, _o, _e = env.setup("--service")
    assert code == 0 and env.installs == []
    assert doc["service"]["result"] == "skipped_daemon_running"
    assert doc["daemon"]["running"] is True
    assert "daemon_unknown" in [w["code"] for w in doc["warnings"]]


def test_service_already_installed_when_the_unit_is_unchanged(env, monkeypatch):
    from aipager import service
    unit = env.tmp / "aipager.service"
    unit.write_bytes(b"[Unit]\nDescription=aipager\n")
    monkeypatch.setattr(service, "unit_path", lambda: unit)
    code, doc, _o, _e = env.setup("--service")
    assert code == 0 and env.installs == [True]
    assert doc["service"]["result"] == "already_installed"
    assert "service" not in doc["changed"]


def test_service_installed_when_the_installer_rewrites_the_unit(env, monkeypatch):
    from aipager import service
    unit = env.tmp / "aipager.service"
    unit.write_bytes(b"old")
    monkeypatch.setattr(service, "unit_path", lambda: unit)
    monkeypatch.setattr(service, "install_service",
                        lambda *, yes: unit.write_bytes(b"new") and 0)
    code, doc, _o, _e = env.setup("--service")
    assert code == 0 and doc["service"]["result"] == "installed"
    assert "service" in doc["changed"]


def test_token_change_with_no_daemon_needs_no_restart(env):
    _write_yaml([_dm(CHAT)], token=TOKEN2)
    env.daemon_pid = None
    code, doc, _o, _e = env.setup("--force")
    assert code == 0 and "bot_token" in doc["changed"]
    assert doc["daemon"] == {"running": False, "reload": "not_needed",
                             "restart_needed": False}
    assert "Restart" not in doc["next_step"]
    assert env.reloads == []


def test_dry_run_service_skipped_while_a_daemon_runs(env):
    env.daemon_pid = 4321
    before = env.snapshot()
    code, doc, _o, _e = env.setup("--dry-run", "--service")
    assert code == 0 and doc["status"] == "dry_run"
    assert doc["service"]["result"] == "skipped_daemon_running"
    assert "service" not in doc["changed"]
    assert env.installs == [] and env.snapshot() == before


def test_plain_warning_lines_are_scrubbed(env):
    shared = env.tmp / f"{TOKEN}.txt"
    shared.write_text(TOKEN + "\n")
    os.chmod(shared, 0o644)
    code, out, err = env.run("setup", "--token-file", str(shared),
                             "--chat-id", str(CHAT))
    _fail_if_token_in("plain output", out, err)
    assert code == 0
    assert "readable by other users" in err and "<redacted>" in err


class _PipeStdin:
    """stdin backed by a real pipe (an fd, no socket), never a TTY."""

    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd

    def isatty(self):
        return False

    def read(self, n=-1):  # a blocking read here would be the bug
        raise AssertionError("stdin read without the bounded fd path")


class _Writer:
    """A pipe's write end with a watchdog: it is closed after *after*
    seconds, so a reader that ignores the deadline reads EOF and the test
    fails by assertion instead of hanging. Closing is guarded (once)."""

    def __init__(self, fd, after):
        import threading
        self._fd = fd
        self._lock = threading.Lock()
        self._timer = threading.Timer(after, self.close)
        self._timer.daemon = True
        self._timer.start()

    def close(self):
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def stop(self):
        self._timer.cancel()
        self.close()


def test_token_stdin_times_out_on_a_pipe_that_never_closes(env, monkeypatch):
    from aipager import setup_cmd
    monkeypatch.setattr(setup_cmd, "STDIN_READ_TIMEOUT", 0.2)
    r, w = os.pipe()
    os.write(w, (TOKEN + "\n").encode())
    writer = _Writer(w, after=3.0)
    try:
        code, doc, _o, _e = env.run_json("setup", "--token-stdin", "--chat-id",
                                         str(CHAT), stdin=_PipeStdin(r))
    finally:
        writer.stop()
        os.close(r)
    _fail_if_token_in("the JSON", json.dumps(doc))
    assert code == 2 and doc["error"] == "token_stdin_timeout"
    assert "--token-file PATH" in doc["fix"]
    assert env.tg.urls == []


def test_token_stdin_reads_a_pipe_that_closes(env, monkeypatch):
    from aipager import setup_cmd
    monkeypatch.setattr(setup_cmd, "STDIN_READ_TIMEOUT", 5.0)
    r, w = os.pipe()
    os.write(w, (TOKEN + "\n").encode())
    os.close(w)
    try:
        code, doc, _o, _e = env.run_json("setup", "--token-stdin", "--chat-id",
                                         str(CHAT), stdin=_PipeStdin(r))
    finally:
        os.close(r)
    assert code == 0 and doc["status"] == "installed"


def test_token_stdin_pipe_oversize_is_refused(env, monkeypatch):
    from aipager import setup_cmd
    monkeypatch.setattr(setup_cmd, "STDIN_READ_TIMEOUT", 5.0)
    r, w = os.pipe()
    os.write(w, (TOKEN + "\n").encode().ljust(4097, b" "))
    os.close(w)
    try:
        code, doc, _o, _e = env.run_json("setup", "--token-stdin", "--chat-id",
                                         str(CHAT), stdin=_PipeStdin(r))
    finally:
        os.close(r)
    assert code == 2 and doc["error"] == "token_malformed"


def test_help_topic_never_echoes_a_token(env):
    code, out, err = env.run("help", TOKEN)
    _fail_if_token_in("help output", out, err)
    assert code == 2 and "Unknown subcommand" in out + err


def test_building_the_document_is_inside_the_json_quarantine(env, monkeypatch):
    from aipager import setup_cmd
    real = setup_cmd._base_doc

    def _noisy(command, args):
        print("import-time noise")
        return real(command, args)

    monkeypatch.setattr(setup_cmd, "_base_doc", _noisy)
    code, doc, _out, err = env.setup()
    assert code == 0 and doc is not None, "stdout must be one JSON object"
    assert "import-time noise" in err


# ----- after the first write, nothing may hide what was written -----

def _real_reload_counted(monkeypatch):
    """The REAL ``_live_reload`` (config check, ``_signal_reload``, the
    detector), counted."""
    calls: list[int] = []

    def _counted():
        calls.append(1)
        return REAL_LIVE_RELOAD()

    monkeypatch.setattr(_daemon_io, "_live_reload", _counted)
    return calls


def test_unknown_daemon_scope_change_asks_for_a_restart_without_reloading(
        env, monkeypatch):
    """rev-iter2-001: detection raises, the change is a scope only. The
    real reload would detect again (and raise) after the yaml is written."""
    _write_yaml([_dm(CHAT, role="admin")])
    calls = _real_reload_counted(monkeypatch)

    def _boom():
        raise PermissionError("cannot read the daemon socket")

    monkeypatch.setattr(_daemon_io, "_detect_daemon_running", _boom)
    code, doc, _o, _e = env.setup("--force")
    assert code == 0, f"exit {code}, error {doc and doc.get('error')}"
    assert doc["changed"] == ["role", "settings_json"]
    assert doc["daemon"] == {"running": True, "reload": "not_reloaded",
                             "restart_needed": True}
    assert calls == [], "no reload may be tried while the daemon is unknown"
    assert "daemon_unknown" in [w["code"] for w in doc["warnings"]]
    assert doc["next_step"].startswith(
        "If an aipager daemon is running, restart it to apply the change")
    assert scope_mod.load_scopes(scope_mod.CONFIG_PATH)[0][0].members[0].role \
        == "owner"


def test_reload_that_raises_after_the_write_asks_for_a_restart(
        env, monkeypatch):
    """Detection answers once (a running daemon), then raises inside the
    real reload: the yaml is written, so the run still succeeds."""
    _write_yaml([_dm(CHAT, role="admin")])
    calls = _real_reload_counted(monkeypatch)
    seen: list[int] = []

    def _then_boom():
        seen.append(1)
        if len(seen) > 1:
            raise PermissionError("the daemon socket went away")
        return 4321

    monkeypatch.setattr(_daemon_io, "_detect_daemon_running", _then_boom)
    code, doc, _o, _e = env.setup("--force")
    assert code == 0, f"exit {code}, error {doc and doc.get('error')}"
    assert calls == [1] and len(seen) == 2
    assert doc["changed"] == ["role", "settings_json"]
    assert doc["daemon"] == {"running": True, "reload": "not_reloaded",
                             "restart_needed": True}
    assert doc["warnings"] == []
    assert doc["next_step"].startswith("Restart the daemon to apply the change")


def test_unexpected_error_after_the_first_write_still_lists_it(
        env, monkeypatch):
    from aipager.wizard import settings_patch

    def _boom(plan):
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(settings_patch, "apply_settings", _boom)
    code, doc, _o, _e = env.setup()
    assert code == 1 and doc["error"] == "internal_error"
    assert doc["changed"] == ["bot_token", "owner_dm"]
    assert scope_mod.CONFIG_PATH.exists()


def test_unknown_daemon_fresh_install_next_step_is_conditional(
        env, monkeypatch):
    """tester-iter2-002: a first install with an unknown daemon must not
    say there is a daemon to restart."""
    def _boom():
        raise PermissionError("cannot read the daemon socket")

    monkeypatch.setattr(_daemon_io, "_detect_daemon_running", _boom)
    code, doc, _o, _e = env.setup()
    assert code == 0 and doc["status"] == "installed"
    assert doc["next_step"].startswith(
        "If an aipager daemon is running, restart it to use the new bot "
        "token: run `aipager service stop`")
