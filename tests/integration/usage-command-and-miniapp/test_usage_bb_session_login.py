"""claude_bootstrap.session_login, black box (design.md "Chosen approach"
(1), Success criterion 3; decisions.md 5; entrypoints.md "Exported
functions").

Partitions over the launch environment: nothing (a saved sign-in), a
login token in the base env, a gateway (custom base URL), a cloud switch,
an untrusted folder's settings carrying a token. Error guessing: the
credential value must never reach any field or the repr; odd folders and
odd values never raise."""

from __future__ import annotations

import dataclasses
import json

import pytest

from aipager.claude_bootstrap import SessionLogin, session_login

SECRET = "sk-ant-oat01-SECRET-do-not-show-0123456789"


@pytest.fixture(autouse=True)
def _folder(tmp_path, monkeypatch):
    """A plain launch folder (a session always has one; an empty one is
    the "unknown" login, design.md (1))."""
    d = tmp_path / "launch"
    d.mkdir()
    monkeypatch.setattr(_STATE, "folder", str(d))


class _STATE:
    folder = ""


def _login(env, folder=None):
    return session_login(env, folder=_STATE.folder if folder is None else folder)


@pytest.mark.parametrize("folder", ["", None], ids=["empty", "none"])
def test_no_folder_is_the_unknown_login(folder):
    assert session_login({"CLAUDE_CODE_OAUTH_TOKEN": SECRET}, folder=folder).kind == "unknown"


def test_no_credential_in_the_env_is_a_saved_sign_in():
    assert _login({}).kind == "saved"


@pytest.mark.parametrize("var", ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"])
def test_a_login_token_in_the_env_is_kind_token(var):
    assert _login({var: SECRET}).kind == "token"


def test_a_base_env_token_comes_from_the_env_layer():
    assert _login({"CLAUDE_CODE_OAUTH_TOKEN": SECRET}).source == "env"


def test_a_custom_base_url_is_a_gateway():
    env = {"ANTHROPIC_BASE_URL": "https://gw.example.com/v1", "ANTHROPIC_AUTH_TOKEN": SECRET}
    assert _login(env).kind == "gateway"


def test_a_gateway_source_is_its_host():
    env = {"ANTHROPIC_BASE_URL": "https://gw.example.com:8443/v1",
           "ANTHROPIC_AUTH_TOKEN": SECRET}
    assert "gw.example.com" in _login(env).source


@pytest.mark.parametrize("var", ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"])
def test_a_cloud_switch_is_kind_cloud(var):
    assert _login({var: "1"}).kind == "cloud"


def test_a_token_in_an_untrusted_folder_settings_does_not_count(tmp_path):
    folder = tmp_path / "proj"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".claude" / "settings.local.json").write_text(
        json.dumps({"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}}))
    assert _login({}, str(folder)).kind == "saved"


def test_the_result_is_a_frozen_session_login():
    out = _login({"CLAUDE_CODE_OAUTH_TOKEN": SECRET})
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.kind = "x"  # type: ignore[misc]


def test_the_kind_is_one_of_the_documented_kinds():
    kinds = {_login(e).kind for e in (
        {}, {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}, {"CLAUDE_CODE_USE_BEDROCK": "1"},
        {"ANTHROPIC_BASE_URL": "https://gw.example.com", "ANTHROPIC_AUTH_TOKEN": SECRET})}
    assert kinds <= {"token", "saved", "gateway", "cloud", "unknown"}


@pytest.mark.parametrize("env", [
    {"CLAUDE_CODE_OAUTH_TOKEN": SECRET},
    {"ANTHROPIC_AUTH_TOKEN": SECRET},
    {"ANTHROPIC_API_KEY": SECRET},
    {"ANTHROPIC_BASE_URL": "https://gw.example.com", "ANTHROPIC_AUTH_TOKEN": SECRET},
    {"ANTHROPIC_BASE_URL": f"https://user:{SECRET}@gw.example.com"},
], ids=["oauth", "auth-token", "api-key", "gateway", "creds-in-url"])
def test_no_credential_value_in_the_repr(env):
    assert SECRET not in repr(_login(env))


@pytest.mark.parametrize("env", [
    {"CLAUDE_CODE_OAUTH_TOKEN": SECRET},
    {"ANTHROPIC_BASE_URL": f"https://user:{SECRET}@gw.example.com"},
], ids=["oauth", "creds-in-url"])
def test_no_credential_value_in_any_field(env):
    out = _login(env)
    assert [f for f in dataclasses.fields(out) if SECRET in str(getattr(out, f.name))] == []


def test_a_token_from_folder_settings_is_never_in_the_result(tmp_path):
    folder = tmp_path / "proj"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}}))
    assert SECRET not in repr(_login({}, str(folder)))


@pytest.mark.parametrize("folder", ["/nonexistent/folder/xyz", "", "\x00bad", "relative/dir"],
                         ids=["missing", "empty", "nul", "relative"])
def test_odd_folders_never_raise(folder):
    assert isinstance(_login({}, folder), SessionLogin)


def test_a_corrupt_folder_settings_file_never_raises(tmp_path):
    folder = tmp_path / "proj"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".claude" / "settings.local.json").write_text("{not json")
    assert isinstance(_login({}, str(folder)), SessionLogin)


@pytest.mark.parametrize("url", ["not a url", "https://", "://x", "http://[::1"],
                         ids=["words", "no-host", "no-scheme", "bad-ipv6"])
def test_an_odd_base_url_never_raises(url):
    assert isinstance(_login({"ANTHROPIC_BASE_URL": url}), SessionLogin)
