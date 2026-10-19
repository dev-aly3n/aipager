"""What aipager says about a credential fits a gateway or another provider.

With ANTHROPIC_BASE_URL pointing elsewhere, "run `claude auth login`" is the
wrong fix for a refused credential: the key for that address is. A
daemon.env holding a gateway's ANTHROPIC_AUTH_TOKEN or a cloud provider
switch already holds a credential, so `doctor --fix` must not offer to add
a second one. And the credential probe (`claude -p`) uses an
ANTHROPIC_API_KEY Claude Code was told not to use, so doctor checks that
answer itself.
"""

from __future__ import annotations

import json

import pytest

from aipager import claude_bootstrap, claude_resolve, daemon_secrets, doctor

KEY = "sk-ant-api03-" + "B" * 30 + "tail-of-the-key-0002"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "CREDENTIALS_DIRECTORY",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
                "CLAUDE_CODE_USE_MANTLE"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("url,custom", [
    ("", False),
    ("https://api.anthropic.com", False),
    ("https://api.anthropic.com/", False),
    ("https://eu.api.anthropic.com/v1", False),
    ("https://claude.ai/api", False),
    ("https://openrouter.ai/api", True),
    ("http://localhost:11434", True),
    ("https://api.z.ai/api/anthropic", True),
    ("https://anthropic.com.example.net", True),
    ("https://evilanthropic.com", True),
    ("http://[::1", True),
    ("api.anthropic.com", False),
    ("openrouter.ai/api", True),
], ids=lambda v: str(v))
def test_uses_custom_endpoint(url, custom):
    assert claude_resolve.uses_custom_endpoint({"ANTHROPIC_BASE_URL": url}) is custom


def _install():
    return claude_resolve.ClaudeInstall(path="/x/claude", realpath="/x/claude",
                                        version="2.1.302")


def _doctor(monkeypatch, *, env, auth, check="valid"):
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda **kw: resolved)
    monkeypatch.setattr(daemon_secrets, "build_session_env", lambda *a, **kw: dict(env))
    monkeypatch.setattr(claude_resolve, "detect_auth", lambda *a, **k: auth)
    monkeypatch.setattr(claude_resolve, "validate_credential",
                        lambda *a, **k: claude_resolve.CredentialCheck(check, "x"))
    return doctor.check_claude_auth()


def test_doctor_points_a_gateway_at_its_key_not_at_a_claude_login(monkeypatch):
    r = _doctor(monkeypatch, env={"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"},
                auth=claude_resolve.AuthStatus(False, "none", "unknown"))
    assert r.status == doctor.WARN
    assert "ANTHROPIC_BASE_URL" in r.fix and "claude auth login" not in r.fix


def test_doctor_reads_the_address_from_claude_settings_too(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://localhost:4000"}}))
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)

    r = _doctor(monkeypatch, env={"ANTHROPIC_AUTH_TOKEN": "gw"},
                auth=claude_resolve.AuthStatus(True, "oauth_token", "env"), check="rejected")
    assert r.status == doctor.WARN
    assert "ANTHROPIC_BASE_URL" in r.fix


def test_doctor_keeps_the_claude_login_fix_on_anthropic(monkeypatch):
    r = _doctor(monkeypatch, env={"ANTHROPIC_BASE_URL": "https://api.anthropic.com"},
                auth=claude_resolve.AuthStatus(False, "none", "unknown"))
    assert "claude auth login" in r.fix


def test_doctor_warns_when_a_refused_key_leaves_sessions_with_no_login(monkeypatch, tmp_path):
    """`claude -p` uses the key anyway, so the probe says it works while an
    interactive session answers "Not logged in"."""
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps(
        {"customApiKeyResponses": {"approved": [], "rejected": [KEY[-20:]]}}))
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", claude_json)

    r = _doctor(monkeypatch, env={"ANTHROPIC_API_KEY": KEY},
                auth=claude_resolve.AuthStatus(True, "api_key", "env"))
    assert r.status == doctor.WARN
    assert "Not logged in" in " ".join(r.detail)
    assert "daemon.env" in r.fix
    assert KEY[-20:] not in " ".join(r.detail) + r.fix


def test_doctor_is_quiet_about_a_refused_key_next_to_a_login(monkeypatch, tmp_path):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({
        "oauthAccount": {"accountUuid": "u"},
        "customApiKeyResponses": {"approved": [], "rejected": [KEY[-20:]]}}))
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", claude_json)

    r = _doctor(monkeypatch, env={"ANTHROPIC_API_KEY": KEY},
                auth=claude_resolve.AuthStatus(True, "api_key", "env"))
    assert r.status == doctor.OK


def test_an_auth_token_is_not_called_a_setup_token():
    assert claude_bootstrap._kinds_in({"ANTHROPIC_AUTH_TOKEN": "gw"}) == ["auth token"]
    assert claude_bootstrap._kinds_in({"CLAUDE_CODE_OAUTH_TOKEN": "t"}) == ["setup token"]


@pytest.mark.parametrize("line,state", [
    ("ANTHROPIC_AUTH_TOKEN=gw-token", "credential"),
    ("CLAUDE_CODE_USE_VERTEX=1", "credential"),
    ("CLAUDE_CODE_USE_FOUNDRY=true", "credential"),
    ("CLAUDE_CODE_USE_BEDROCK=1", "credential"),
    ("CLAUDE_CODE_USE_VERTEX=0", "none"),
    ("CLAUDE_CODE_USE_MANTLE=yes", "credential"),
    ("ANTHROPIC_BASE_URL=https://openrouter.ai/api", "none"),
])
def test_doctor_fix_sees_a_gateway_or_cloud_credential(tmp_path, line, state):
    path = tmp_path / "daemon.env"
    path.write_text(line + "\n")
    assert doctor._daemon_env_state(path) == state
