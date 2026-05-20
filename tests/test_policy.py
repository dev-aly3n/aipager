"""Tests for aipager.policy — role/safety merge + validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from aipager.policy import (
    PolicyError,
    load_policy,
    validate_policy_files,
    validate_scopes_against_policy,
)
from aipager.scope import Member, Scope


def _load(tmp_path: Path, policy_text: str | None = None, dfiles: dict | None = None):
    pp = tmp_path / "policy.yaml"
    pd = tmp_path / "policy.d"
    if policy_text is not None:
        pp.write_text(policy_text)
    if dfiles:
        pd.mkdir()
        for name, body in dfiles.items():
            (pd / name).write_text(body)
    return load_policy(pp, pd)


def test_defaults_when_no_files(tmp_path: Path):
    p = load_policy(tmp_path / "policy.yaml", tmp_path / "policy.d")
    assert sorted(p.roles) == ["admin", "owner", "read_only", "user"]
    assert p.roles["owner"].bypass_safety is True
    assert p.roles["user"].bypass_safety is False
    assert p.roles["user"].deny_tools == ()
    # built-in safety floor present
    assert "~/.claude/**" in p.safety_deny_paths_no_access
    assert r"\bclaude\b" in p.safety_deny_bash_patterns


def test_role_override_replaces_field(tmp_path: Path):
    p = _load(tmp_path, "roles:\n  user:\n    deny_tools: [Bash]\n")
    assert p.roles["user"].deny_tools == ("Bash",)
    # other fields keep their built-in defaults
    assert p.roles["user"].can_prompt is True


def test_custom_role_definition(tmp_path: Path):
    p = _load(tmp_path,
              "roles:\n  reviewer:\n    allow_tools: [Read, Grep]\n    can_approve: false\n")
    assert "reviewer" in p.roles
    assert p.roles["reviewer"].allow_tools == ("Read", "Grep")
    assert p.roles["reviewer"].can_approve is False


def test_policy_d_lexical_order_wins(tmp_path: Path):
    p = _load(tmp_path, dfiles={
        "10-base.yaml": "roles:\n  user:\n    deny_tools: [Bash]\n",
        "20-over.yaml": "roles:\n  user:\n    deny_tools: [WebFetch]\n",
    })
    assert p.roles["user"].deny_tools == ("WebFetch",)


def test_safety_floor_is_union(tmp_path: Path):
    p = _load(tmp_path,
              "safety:\n  deny_paths_no_write: ['**/*.lock']\n")
    # built-in B1 floor still present
    assert "~/.claude/**" in p.safety_deny_paths_no_access
    # operator addition unioned in
    assert "**/*.lock" in p.safety_deny_paths_no_write


def test_unknown_role_key_rejected(tmp_path: Path):
    with pytest.raises(PolicyError, match="unknown key"):
        _load(tmp_path, "roles:\n  user:\n    nonsense: true\n")


def test_bad_bool_type_rejected(tmp_path: Path):
    with pytest.raises(PolicyError, match="true/false"):
        _load(tmp_path, "roles:\n  user:\n    bypass_safety: yes_please\n")


def test_bad_regex_rejected(tmp_path: Path):
    with pytest.raises(PolicyError, match="invalid bash deny pattern"):
        _load(tmp_path, "safety:\n  deny_bash_patterns: ['(unclosed']\n")


def test_unknown_safety_key_rejected(tmp_path: Path):
    with pytest.raises(PolicyError, match="unknown key"):
        _load(tmp_path, "safety:\n  bogus: []\n")


def test_validate_scopes_against_policy_ok(tmp_path: Path):
    p = load_policy(tmp_path / "p.yaml", tmp_path / "pd")
    scopes = [Scope(chat_id=1, kind="dm", label="d",
                    members=(Member(id=1, label="a", role="admin"),))]
    validate_scopes_against_policy(scopes, p)  # no raise


def test_validate_scopes_against_policy_undefined_role(tmp_path: Path):
    p = load_policy(tmp_path / "p.yaml", tmp_path / "pd")
    scopes = [Scope(chat_id=1, kind="dm", label="d",
                    members=(Member(id=1, label="a", role="wizard"),))]
    with pytest.raises(PolicyError, match="undefined role 'wizard'"):
        validate_scopes_against_policy(scopes, p)


def test_validate_policy_files_clean(tmp_path: Path):
    assert validate_policy_files(
        scopes=None,
        policy_path=tmp_path / "p.yaml",
        policy_d=tmp_path / "pd",
    ) == []


def test_validate_policy_files_reports_undefined_role(tmp_path: Path):
    scopes = [Scope(chat_id=1, kind="dm", label="d",
                    members=(Member(id=1, label="a", role="ghost"),))]
    problems = validate_policy_files(
        scopes=scopes,
        policy_path=tmp_path / "p.yaml",
        policy_d=tmp_path / "pd",
    )
    assert problems and "ghost" in problems[0]
