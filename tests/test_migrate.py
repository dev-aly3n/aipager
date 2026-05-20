"""Tests for aipager.migrate — v1 (config.env/team.yaml) → v2 generation.

Phase A migration is additive: it writes aipager.yaml + seeds
policy.yaml, COPIES the v1 files to *.bak.<ts>, and leaves the
originals in place (the runtime still uses them in Phase A).
"""

from __future__ import annotations

import textwrap

import pytest

from aipager import config, migrate
from aipager import policy as policy_mod
from aipager import scope as scope_mod
from aipager import team as team_mod
from aipager.scope import load_scopes


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Redirect all migration paths into tmp_path."""
    aipager_yaml = tmp_path / "aipager.yaml"
    policy_yaml = tmp_path / "policy.yaml"
    config_env = tmp_path / "config.env"
    team_yaml = tmp_path / "team.yaml"
    monkeypatch.setattr(scope_mod, "CONFIG_PATH", aipager_yaml)
    monkeypatch.setattr(policy_mod, "POLICY_PATH", policy_yaml)
    monkeypatch.setattr(config, "_XDG_CONFIG", config_env)
    monkeypatch.setattr(team_mod, "TEAM_CONFIG_PATH", team_yaml)
    monkeypatch.setattr(config, "BOT_TOKEN", "TOK")
    monkeypatch.setattr(config, "CHAT_ID", "")
    return {
        "aipager": aipager_yaml, "policy": policy_yaml,
        "config_env": config_env, "team": team_yaml,
    }


def test_migrate_personal(paths, monkeypatch):
    monkeypatch.setattr(config, "CHAT_ID", "256113222")
    paths["config_env"].write_text("CLAUDE_TG_BOT_TOKEN=TOK\nCLAUDE_TG_CHAT_ID=256113222\n")

    assert migrate.migrate_to_v2() is True
    scopes, token = load_scopes(paths["aipager"])
    assert token == "TOK"
    assert len(scopes) == 1
    s = scopes[0]
    assert s.kind == "dm" and s.chat_id == 256113222
    assert len(s.members) == 1
    assert s.members[0].role == "admin"   # admin, NOT owner (silent migration)
    # policy.yaml seeded
    assert paths["policy"].exists()
    # config.env backed up AND retained
    assert paths["config_env"].exists()
    baks = list(paths["config_env"].parent.glob("config.env.bak.*"))
    assert len(baks) == 1


def test_migrate_team(paths):
    paths["team"].write_text(textwrap.dedent("""\
        mode: team
        group_id: -4152307515
        users:
          - {id: 1, label: aly3n, role: admin}
          - {id: 2, label: arian, role: developer}
        rules:
          deny_tools: [Bash]
    """))
    assert migrate.migrate_to_v2() is True
    scopes, _ = load_scopes(paths["aipager"])
    assert len(scopes) == 1
    g = scopes[0]
    assert g.kind == "group" and g.chat_id == -4152307515
    assert g.deny_tools == ("Bash",)
    roles = {m.label: m.role for m in g.members}
    assert roles == {"aly3n": "admin", "arian": "user"}  # developer→user
    # team.yaml backed up + retained
    assert paths["team"].exists()
    assert list(paths["team"].parent.glob("team.yaml.bak.*"))


def test_migrate_idempotent(paths, monkeypatch):
    monkeypatch.setattr(config, "CHAT_ID", "5")
    assert migrate.migrate_to_v2() is True
    assert paths["aipager"].exists()
    # second call is a no-op
    assert migrate.migrate_to_v2() is False


def test_migrate_preserves_existing_policy(paths, monkeypatch):
    monkeypatch.setattr(config, "CHAT_ID", "5")
    paths["policy"].write_text("# my hand-tuned policy\nroles:\n  user:\n    deny_tools: [Bash]\n")
    before = paths["policy"].read_text()
    migrate.migrate_to_v2()
    assert paths["policy"].read_text() == before  # byte-identical, not clobbered


def test_migrate_no_token_is_noop(paths, monkeypatch):
    monkeypatch.setattr(config, "BOT_TOKEN", "")
    monkeypatch.setattr(config, "CHAT_ID", "5")
    assert migrate.migrate_to_v2() is False
    assert not paths["aipager"].exists()
