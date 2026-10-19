"""What the startup auth notice reports and which fix it gives.

The kinds found are the variables a credential is in, a cloud provider
switch only when Claude Code reads it as on (1/true/yes/on); the fix is the
cloud provider's credentials when a switch is on, the key for the address
when ANTHROPIC_BASE_URL is a gateway or another provider, else a Claude
login. Claude's settings env counts, as Claude Code lays it over the
environment."""

from __future__ import annotations

import json

import pytest

from aipager import claude_bootstrap, claude_paths, claude_resolve
from aipager.claude_bootstrap import AuthNotice, PendingAuthCheck


def _settings(tmp_path, env):
    """Claude Code's user settings file: conftest points its config home
    at this test's tmp home, so with nothing written there is none."""
    path = claude_paths.DEFAULT_CONFIG_HOME / "settings.json"
    assert path.is_relative_to(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"env": env}))


# ---- the kinds ---------------------------------------------------------------

def test_every_credential_is_named_by_its_variable_in_a_fixed_order():
    env = {"CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_API_KEY": "k",
           "AWS_BEARER_TOKEN_BEDROCK": "b", "ANTHROPIC_AUTH_TOKEN": "a",
           "CLAUDE_CODE_OAUTH_TOKEN": "t", "CLAUDE_CODE_USE_BEDROCK": "true"}
    assert claude_bootstrap._kinds_in(env) == [
        "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
        "AWS_BEARER_TOKEN_BEDROCK", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"]


@pytest.mark.parametrize("value, found", [
    ("t", True), ("0", True), ("  t  ", True), ("", False), ("   ", False),
], ids=["token", "zero-is-still-a-token", "padded", "empty", "blank"])
def test_a_token_counts_unless_blank(value, found):
    assert (claude_bootstrap._kinds_in({"ANTHROPIC_API_KEY": value})
            == (["ANTHROPIC_API_KEY"] if found else []))


def test_a_value_that_is_not_text_is_ignored():
    assert claude_bootstrap._kinds_in({"ANTHROPIC_API_KEY": 5,
                                       "CLAUDE_CODE_USE_VERTEX": True}) == []


@pytest.mark.parametrize("switch", claude_bootstrap._CLOUD_SWITCHES)
@pytest.mark.parametrize("value, on", [
    ("1", True), ("true", True), ("YES", True), (" on ", True),
    ("0", False), ("false", False), ("no", False), ("enabled", False), ("", False),
])
def test_a_cloud_switch_counts_only_when_claude_code_reads_it_as_on(switch, value, on):
    assert claude_bootstrap._kinds_in({switch: value}) == ([switch] if on else [])


def test_doctor_still_sees_a_credential_in_daemon_env(tmp_path):
    from aipager import doctor
    path = tmp_path / "daemon.env"
    path.write_text("CLAUDE_CODE_USE_FOUNDRY=1\n")
    assert doctor._daemon_env_state(path) == "credential"


# ---- the fix -----------------------------------------------------------------

@pytest.mark.parametrize("env, fix", [
    ({}, "login"),
    ({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}, "login"),
    ({"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"}, "gateway"),
    ({"CLAUDE_CODE_USE_BEDROCK": "1"}, "cloud"),
    ({"CLAUDE_CODE_USE_MANTLE": "yes"}, "cloud"),
    ({"CLAUDE_CODE_USE_VERTEX": "0", "ANTHROPIC_BASE_URL": "http://localhost:4000"},
     "gateway"),
    ({"CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_BASE_URL": "http://localhost:4000"},
     "cloud"),
], ids=["nothing", "anthropic", "gateway", "bedrock", "mantle", "switch-off-gateway",
        "switch-wins"])
def test_the_fix_follows_the_setup(env, fix):
    assert claude_bootstrap._fix_for(env) == (fix, ())
    assert claude_bootstrap._fix_for(env, rejected=True) == (fix, ())


def test_the_fix_reads_claude_settings_too(tmp_path):
    _settings(tmp_path, {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"})
    assert claude_bootstrap._fix_for({}) == ("gateway", ())
    _settings(tmp_path, {"CLAUDE_CODE_USE_VERTEX": "1"})
    assert claude_bootstrap._fix_for({}) == ("cloud", ())


def test_claude_settings_override_the_environment_as_claude_code_does(tmp_path):
    _settings(tmp_path, {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"})
    assert claude_bootstrap._fix_for(
        {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"}) == ("login", ())


@pytest.mark.parametrize("env, variables", [
    ({"CLAUDE_CODE_OAUTH_TOKEN": "t"}, ("CLAUDE_CODE_OAUTH_TOKEN",)),
    ({"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "a"},
     ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")),
    ({"ANTHROPIC_API_KEY": "k", "AWS_BEARER_TOKEN_BEDROCK": "b"}, ("ANTHROPIC_API_KEY",)),
], ids=["setup-token", "key-and-token", "key-and-a-bedrock-token"])
def test_a_refused_credential_in_the_environment_is_named_not_a_login(env, variables):
    """Claude Code uses a token or key in its environment before a saved
    login: logging in again would change nothing."""
    assert claude_bootstrap._fix_for(env, rejected=True) == ("variable", variables)
    # Not refused (nothing was found to work): a login is still the fix.
    assert claude_bootstrap._fix_for(env) == ("login", ())


def test_a_bedrock_token_without_its_switch_is_not_what_was_refused():
    """Claude Code reads AWS_BEARER_TOKEN_BEDROCK only with the Bedrock switch
    on (then the fix is "cloud"): a leftover one next to a refused saved
    login must not be named as the culprit."""
    assert claude_bootstrap._fix_for({"AWS_BEARER_TOKEN_BEDROCK": "b"},
                                     rejected=True) == ("login", ())
    assert claude_bootstrap._fix_for({"AWS_BEARER_TOKEN_BEDROCK": "b",
                                      "CLAUDE_CODE_USE_BEDROCK": "1"},
                                     rejected=True) == ("cloud", ())


def test_a_refused_key_in_claude_settings_is_named_too(tmp_path):
    _settings(tmp_path, {"ANTHROPIC_API_KEY": "k"})
    assert claude_bootstrap._fix_for({}, rejected=True) == ("variable", ("ANTHROPIC_API_KEY",))


def test_a_refused_key_for_a_gateway_still_points_at_the_address():
    assert claude_bootstrap._fix_for(
        {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://openrouter.ai/api"},
        rejected=True) == ("gateway", ())


# ---- what the sweep hands the daemon ----------------------------------------

@pytest.mark.parametrize("state, rejected", [("rejected", True), ("absent", False)])
def test_the_notice_carries_the_sweep_the_state_and_the_fix(monkeypatch, state, rejected):
    """The fix comes from the session's environment."""
    monkeypatch.setattr(claude_resolve, "validate_credential",
                        lambda *a, **k: claude_resolve.CredentialCheck(state))
    monkeypatch.setattr(claude_bootstrap, "_recover_auth",
                        lambda *a, **k: (False, ["ANTHROPIC_API_KEY"]))
    pending = PendingAuthCheck("/x/claude", "2.1.303",
                               {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"})
    assert claude_bootstrap.recover_auth_or_notice(pending) == AuthNotice(
        found=("ANTHROPIC_API_KEY",), rejected=rejected, fix="gateway")


@pytest.mark.parametrize("state", ["valid", "unknown"])
def test_no_notice_when_the_credential_works_or_it_cannot_tell(monkeypatch, state):
    monkeypatch.setattr(claude_resolve, "validate_credential",
                        lambda *a, **k: claude_resolve.CredentialCheck(state))
    assert claude_bootstrap.recover_auth_or_notice(
        PendingAuthCheck("/x/claude", "2.1.303", {})) is None


def test_a_refused_token_in_the_session_env_is_what_the_notice_names(monkeypatch):
    monkeypatch.setattr(claude_resolve, "validate_credential",
                        lambda *a, **k: claude_resolve.CredentialCheck("rejected"))
    monkeypatch.setattr(claude_bootstrap, "_recover_auth",
                        lambda *a, **k: (False, ["CLAUDE_CODE_OAUTH_TOKEN"]))
    pending = PendingAuthCheck("/x/claude", "2.1.303", {"CLAUDE_CODE_OAUTH_TOKEN": "t"})
    assert claude_bootstrap.recover_auth_or_notice(pending) == AuthNotice(
        found=("CLAUDE_CODE_OAUTH_TOKEN",), rejected=True, fix="variable",
        variables=("CLAUDE_CODE_OAUTH_TOKEN",))
