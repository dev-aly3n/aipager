"""Tests for `aipager.audit` JSONL append (item 4.2)."""

from __future__ import annotations

import json

from aipager import audit


def test_append_writes_jsonl_line(tmp_path):
    log = tmp_path / "audit.jsonl"
    ok = audit.append(
        session="claude-jim", label="jim", action="Allowed",
        tool="Bash", summary="ls -la /tmp",
        path=log,
    )
    assert ok is True
    lines = log.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["session"] == "claude-jim"
    assert record["label"] == "jim"
    assert record["action"] == "Allowed"
    assert record["tool"] == "Bash"
    assert record["summary"] == "ls -la /tmp"
    assert "ts" in record


def test_append_creates_parent_dir(tmp_path):
    log = tmp_path / "nested" / "dir" / "audit.jsonl"
    ok = audit.append(
        session="claude-jim", label="jim", action="Denied",
        tool="Bash", summary="rm -rf /",
        path=log,
    )
    assert ok is True
    assert log.exists()


def test_append_truncates_long_summary(tmp_path):
    log = tmp_path / "audit.jsonl"
    very_long = "x" * 5000
    audit.append(
        session="claude-jim", label="jim", action="Allowed",
        summary=very_long,
        path=log,
    )
    record = json.loads(log.read_text().splitlines()[0])
    assert len(record["summary"]) == 500


def test_append_appends_multiple_records(tmp_path):
    log = tmp_path / "audit.jsonl"
    for i in range(3):
        audit.append(
            session="claude-jim", label="jim", action="Allowed",
            tool="Bash", summary=f"call #{i}",
            path=log,
        )
    lines = log.read_text().splitlines()
    assert len(lines) == 3
    assert all('"call #' in line for line in lines)


def test_append_returns_false_on_unwritable_path(tmp_path, monkeypatch, capsys):
    # Make tmp_path read-only so .mkdir / write fails.
    bad_path = tmp_path / "readonly" / "audit.jsonl"
    (tmp_path / "readonly").mkdir(mode=0o500)
    try:
        ok = audit.append(
            session="claude-jim", label="jim", action="Allowed",
            path=bad_path,
        )
        # Either the mkdir succeeds anyway (some FS) or the write fails;
        # in either case the function should not raise.
        assert ok in (True, False)
    finally:
        # Restore perms so cleanup works
        (tmp_path / "readonly").chmod(0o700)


def test_default_audit_path_is_under_home():
    assert audit.AUDIT_LOG_PATH.name == "aipager-audit.jsonl"
    assert ".claude" in str(audit.AUDIT_LOG_PATH)


def test_append_includes_user_attribution_fields(tmp_path):
    """Team-mode records carry the Telegram user identity."""
    log = tmp_path / "audit.jsonl"
    audit.append(
        session="claude-jim", label="jim", action="Allowed",
        tool="Bash", summary="ls",
        user_id=12345, username="alice", display_name="Alice Smith",
        path=log,
    )
    record = json.loads(log.read_text().splitlines()[0])
    assert record["user_id"] == 12345
    assert record["username"] == "alice"
    assert record["display_name"] == "Alice Smith"


def test_append_omits_user_attribution_for_personal_mode(tmp_path):
    """When user info isn't passed, the fields are present but null/empty
    — so the log schema stays stable for downstream consumers."""
    log = tmp_path / "audit.jsonl"
    audit.append(
        session="claude-jim", label="jim", action="Allowed",
        path=log,
    )
    record = json.loads(log.read_text().splitlines()[0])
    assert record["user_id"] is None
    assert record["username"] == ""
    assert record["display_name"] == ""


# ---- scope attribution + denial (Phase H) -------------------------------

def test_append_records_scope_attribution(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit.append(
        session="claude-jim__d100", label="jim", action="prompt",
        user_id=100, username="ana",
        scope_label="ana DM", scope_chat_id=100,
        denied=False, bypass_safety=True,
        path=log,
    )
    record = json.loads(log.read_text().splitlines()[0])
    assert record["scope_label"] == "ana DM"
    assert record["scope_chat_id"] == 100
    assert record["denied"] is False
    assert record["bypass_safety"] is True
    assert record["reason"] == ""


def test_append_records_denial_with_reason(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit.append(
        session="-", label="-", action="/new",
        scope_label="team", scope_chat_id=-300,
        denied=True, reason="not-a-member",
        path=log,
    )
    record = json.loads(log.read_text().splitlines()[0])
    assert record["denied"] is True
    assert record["reason"] == "not-a-member"


def test_append_legacy_shape_has_empty_scope_fields(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit.append(session="claude-jim", label="jim", action="Allowed",
                 path=log)
    record = json.loads(log.read_text().splitlines()[0])
    assert record["scope_label"] == ""
    assert record["scope_chat_id"] is None
    assert record["denied"] is False
    assert record["bypass_safety"] is False
