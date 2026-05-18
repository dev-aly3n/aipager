"""Tests for ``aipager.team`` — allow-list loading and rule evaluation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aipager.team import (
    Role,
    Rules,
    Team,
    TeamConfigError,
    User,
    attribution_label,
    load_team,
    remember_unauthorized,
    reset_unauthorized_seen,
)


# ---------- Role / User / Rules --------------------------------------


def test_role_values():
    assert Role.ADMIN.value == "admin"
    assert Role.DEVELOPER.value == "developer"
    assert Role.READ_ONLY.value == "read_only"


def test_user_permissions_admin():
    u = User(id=1, label="alice", role=Role.ADMIN)
    assert u.can_prompt and u.can_approve and u.bypasses_rules


def test_user_permissions_developer():
    u = User(id=2, label="bob", role=Role.DEVELOPER)
    assert u.can_prompt and u.can_approve
    assert u.bypasses_rules is False


def test_user_permissions_read_only():
    u = User(id=3, label="charlie", role=Role.READ_ONLY)
    assert u.can_prompt is False
    assert u.can_approve is False
    assert u.bypasses_rules is False


def test_rules_deny_blocks_developer():
    rules = Rules(deny_tools=("Write", "Edit"))
    bob = User(id=2, label="bob", role=Role.DEVELOPER)
    assert rules.tool_is_denied("Write", bob)
    assert rules.tool_is_denied("Edit", bob)
    assert rules.tool_is_denied("Bash", bob) is False


def test_rules_deny_bypassed_by_admin():
    rules = Rules(deny_tools=("Write",))
    alice = User(id=1, label="alice", role=Role.ADMIN)
    assert rules.tool_is_denied("Write", alice) is False


def test_rules_deny_treats_unknown_user_as_non_admin():
    """Safety default: when the triggering user can't be resolved,
    treat them as restricted (most conservative interpretation)."""
    rules = Rules(deny_tools=("Bash",))
    assert rules.tool_is_denied("Bash", None) is True


def test_rules_empty_denies_nothing():
    assert Rules().tool_is_denied("Write", None) is False


# ---------- Team --------------------------------------


def _example_team() -> Team:
    return Team(
        group_id=-100123,
        users={
            1: User(id=1, label="alice", role=Role.ADMIN),
            2: User(id=2, label="bob", role=Role.DEVELOPER),
            3: User(id=3, label="charlie", role=Role.READ_ONLY),
        },
        rules=Rules(deny_tools=("Write",)),
    )


def test_team_is_authorized():
    t = _example_team()
    assert t.is_authorized(1)
    assert t.is_authorized(2)
    assert t.is_authorized(3)
    assert t.is_authorized(99) is False
    assert t.is_authorized(None) is False


def test_team_get_returns_user():
    t = _example_team()
    assert t.get(1).label == "alice"
    assert t.get(99) is None


def test_team_warns_when_no_admin(caplog):
    Team(
        group_id=-100123,
        users={2: User(id=2, label="bob", role=Role.DEVELOPER)},
    )
    assert any("no admin user" in r.message for r in caplog.records)


# ---------- load_team --------------------------------------


def test_load_team_missing_returns_none(tmp_path: Path):
    assert load_team(tmp_path / "nonexistent.yaml") is None


def test_load_team_mode_not_team_returns_none(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text("mode: personal\n")
    assert load_team(f) is None


def test_load_team_happy_path(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
        mode: team
        group_id: -100123456789
        users:
          - id: 12345
            label: alice
            role: admin
          - id: 67890
            label: bob
            role: developer
          - id: 11111
            label: charlie
            role: read_only
        rules:
          deny_tools:
            - Write
            - Edit
    """))
    team = load_team(f)
    assert team is not None
    assert team.group_id == -100123456789
    assert len(team.users) == 3
    assert team.users[12345].role == Role.ADMIN
    assert team.rules.deny_tools == ("Write", "Edit")


def test_load_team_yaml_parse_error(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text("mode: team\nusers: [\n")  # unclosed list
    with pytest.raises(TeamConfigError, match="parse error"):
        load_team(f)


def test_load_team_rejects_string_group_id(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
        mode: team
        group_id: "-100123"
        users:
          - {id: 1, label: a, role: admin}
    """))
    with pytest.raises(TeamConfigError, match="group_id"):
        load_team(f)


def test_load_team_rejects_empty_users(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
        mode: team
        group_id: -100123
        users: []
    """))
    with pytest.raises(TeamConfigError, match="non-empty"):
        load_team(f)


def test_load_team_rejects_unknown_role(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
        mode: team
        group_id: -100123
        users:
          - {id: 1, label: a, role: superuser}
    """))
    with pytest.raises(TeamConfigError, match="role"):
        load_team(f)


def test_load_team_rejects_duplicate_user_id(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
        mode: team
        group_id: -100123
        users:
          - {id: 1, label: a, role: admin}
          - {id: 1, label: a2, role: developer}
    """))
    with pytest.raises(TeamConfigError, match="duplicate"):
        load_team(f)


def test_load_team_rejects_empty_label(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
        mode: team
        group_id: -100123
        users:
          - {id: 1, label: "  ", role: admin}
    """))
    with pytest.raises(TeamConfigError, match="label"):
        load_team(f)


def test_load_team_rejects_non_string_deny_tools(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
        mode: team
        group_id: -100123
        users:
          - {id: 1, label: a, role: admin}
        rules:
          deny_tools: [Write, 5]
    """))
    with pytest.raises(TeamConfigError, match="deny_tools"):
        load_team(f)


def test_load_team_allows_missing_rules(tmp_path: Path):
    f = tmp_path / "team.yaml"
    f.write_text(textwrap.dedent("""\
        mode: team
        group_id: -100123
        users:
          - {id: 1, label: a, role: admin}
    """))
    team = load_team(f)
    assert team is not None
    assert team.rules.deny_tools == ()


# ---------- unauthorized seen tracker --------------------------------------


def test_unauthorized_first_call_returns_false_then_true():
    reset_unauthorized_seen()
    assert remember_unauthorized(42) is False  # first time
    assert remember_unauthorized(42) is True   # already seen
    assert remember_unauthorized(99) is False  # different user, first time
    assert remember_unauthorized(99) is True


def test_unauthorized_reset_clears_state():
    reset_unauthorized_seen()
    remember_unauthorized(42)
    reset_unauthorized_seen()
    assert remember_unauthorized(42) is False  # back to fresh


# ---------- attribution_label --------------------------------------


def test_attribution_label_known_user():
    u = User(id=1, label="alice", role=Role.ADMIN)
    assert attribution_label(u) == "@alice"


def test_attribution_label_none():
    assert attribution_label(None) == "@unknown"
