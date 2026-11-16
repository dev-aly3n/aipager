"""Which sign-in a session runs under (roadmap 8.131), so one sign-in's plan
limits are never shown as another's. Worked out from the session's launch
environment and Claude Code's settings layers only: no credential value is
ever read into the result, hashed or stored."""

from __future__ import annotations

import json
import time

import pytest

from aipager import claude_paths, usage_store
from aipager.claude_bootstrap import SessionLogin, session_login

SECRET = "sk-ant-oat01-" + "Z" * 40


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "work" / "frontend"
    f.mkdir(parents=True)
    return f


def _trust(folder):
    paths = claude_paths.locate({}, cwd=str(folder))
    data = (json.loads(paths.global_config.read_text())
            if paths.global_config.is_file() else {"projects": {}})
    data["projects"][str(folder.resolve())] = {"hasTrustDialogAccepted": True}
    _write(paths.global_config, data)


def _home():
    return str(claude_paths.locate({}).config_home)


def test_no_folder_is_unknown():
    assert session_login({"CLAUDE_CODE_OAUTH_TOKEN": SECRET}, folder=None).kind == "unknown"
    assert session_login({}, folder="").kind == "unknown"


def test_a_token_in_the_environment(folder):
    login = session_login({"CLAUDE_CODE_OAUTH_TOKEN": SECRET}, folder=str(folder))
    assert login == SessionLogin("token", "env", _home())


def test_an_auth_token_in_the_environment(folder):
    login = session_login({"ANTHROPIC_AUTH_TOKEN": SECRET}, folder=str(folder))
    assert login == SessionLogin("token", "env", _home())


def test_nothing_set_is_the_saved_sign_in(folder):
    assert session_login({}, folder=str(folder)) == SessionLogin("saved", "", _home())


def test_an_env_token_with_a_fresh_credentials_file_is_the_saved_sign_in(folder):
    """launch_session unsets that token when the credentials file is fresh,
    so the session runs on the saved sign-in."""
    creds = claude_paths.locate({}).credentials
    _write(creds, {"claudeAiOauth": {"accessToken": "x",
                                     "expiresAt": (time.time() + 3600) * 1000}})
    login = session_login({"CLAUDE_CODE_OAUTH_TOKEN": SECRET}, folder=str(folder))
    assert login.kind == "saved"


def test_a_user_settings_token_is_its_own_source(folder):
    _write(claude_paths.locate({}).user_settings, {"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}})
    login = session_login({}, folder=str(folder))
    assert login == SessionLogin("token", "user", _home())


def test_a_folder_token_counts_only_in_a_trusted_folder(folder):
    _write(folder / ".claude" / "settings.local.json",
           {"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}})
    assert session_login({}, folder=str(folder)).kind == "saved"
    _trust(folder)
    login = session_login({}, folder=str(folder))
    assert login == SessionLogin("token", f"local:{folder}", _home())


def test_two_folders_with_their_own_tokens_are_two_sign_ins(tmp_path, folder):
    other = tmp_path / "work" / "backend"
    other.mkdir()
    for f in (folder, other):
        _write(f / ".claude" / "settings.json", {"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}})
        _trust(f)
    a = session_login({}, folder=str(folder))
    b = session_login({}, folder=str(other))
    assert a.kind == b.kind == "token"
    assert usage_store.login_key(a) != usage_store.login_key(b)


def test_another_config_home_is_another_sign_in(tmp_path, folder):
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "work-claude")}
    login = session_login(env, folder=str(folder))
    assert login == SessionLogin("saved", "", str(tmp_path / "work-claude"))
    assert usage_store.login_key(login) != usage_store.login_key(
        session_login({}, folder=str(folder)))


def test_a_gateway_is_named_by_its_host(folder):
    login = session_login({"ANTHROPIC_BASE_URL": "https://GW.example:8443/v1",
                           "ANTHROPIC_AUTH_TOKEN": SECRET}, folder=str(folder))
    assert login == SessionLogin("gateway", "gw.example", _home())


def test_anthropics_own_address_is_not_a_gateway(folder):
    login = session_login({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"},
                          folder=str(folder))
    assert login.kind == "saved"


def test_a_cloud_switch_is_cloud(folder):
    login = session_login({"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_OAUTH_TOKEN": SECRET},
                          folder=str(folder))
    assert login.kind == "cloud"


def test_an_error_is_unknown(folder, monkeypatch):
    def boom(*a, **kw):
        raise OSError("unreadable")
    monkeypatch.setattr(claude_paths, "locate", boom)
    assert session_login({}, folder=str(folder)) == SessionLogin("unknown")


@pytest.mark.parametrize("env", [
    {"CLAUDE_CODE_OAUTH_TOKEN": SECRET},
    {"ANTHROPIC_AUTH_TOKEN": SECRET},
    {"ANTHROPIC_BASE_URL": "https://gw.example", "ANTHROPIC_AUTH_TOKEN": SECRET},
    {"ANTHROPIC_API_KEY": SECRET},
], ids=["oauth", "auth-token", "gateway", "api-key"])
def test_no_credential_value_reaches_the_result(env, folder):
    login = session_login(env, folder=str(folder))
    for text in (repr(login), login.kind, login.source, login.config_home,
                 usage_store.login_key(login)):
        assert SECRET not in text and SECRET[-12:] not in text


def test_login_for_caches_per_session_and_folder(monkeypatch, folder):
    calls = []

    def fake(env, *, folder):
        calls.append(folder)
        return SessionLogin("saved", "", "/h")

    monkeypatch.setattr("aipager.claude_bootstrap.session_login", fake)

    class S:
        name, cwd = "claude-frontend", str(folder)

    assert usage_store.login_for(S()).kind == "saved"
    assert usage_store.login_for(S()).kind == "saved"
    assert calls == [str(folder)]


def test_login_for_a_session_without_a_folder_is_unknown():
    class S:
        name, cwd = "claude-adopted", ""

    assert usage_store.login_for(S()) == SessionLogin("unknown")
    assert usage_store.login_key(SessionLogin("unknown")) == "unknown"
