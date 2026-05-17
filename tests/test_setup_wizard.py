"""Tests for aipager.setup_wizard helpers."""

from __future__ import annotations

import pytest

from aipager import setup_wizard


# ----- _normalize_token -----

@pytest.mark.parametrize("raw,expected", [
    ("1234567:ABCdef-_ghijklmnopqrstuvwxyz0123", "1234567:ABCdef-_ghijklmnopqrstuvwxyz0123"),
    ("  1234567:ABCdef-_ghijklmnopqrstuvwxyz0123  ", "1234567:ABCdef-_ghijklmnopqrstuvwxyz0123"),
    ("'1234567:ABCdef-_ghijklmnopqrstuvwxyz0123'", "1234567:ABCdef-_ghijklmnopqrstuvwxyz0123"),
    ('"1234567:ABCdef-_ghijklmnopqrstuvwxyz0123"', "1234567:ABCdef-_ghijklmnopqrstuvwxyz0123"),
    ("Use this token: 1234567:ABCdef-_ghijklmnopqrstuvwxyz0123",
     "1234567:ABCdef-_ghijklmnopqrstuvwxyz0123"),
    ("1234567:ABCdef-_ghijklmnopqrstuvwxyz0123\n", "1234567:ABCdef-_ghijklmnopqrstuvwxyz0123"),
    ("1234567:ABCdef-_ghijklmnopqrstuvwxyz0123:", "1234567:ABCdef-_ghijklmnopqrstuvwxyz0123"),
])
def test_normalize_token_extracts_canonical(raw, expected):
    assert setup_wizard._normalize_token(raw) == expected


def test_normalize_token_empty():
    assert setup_wizard._normalize_token("") == ""
    assert setup_wizard._normalize_token("   ") == ""


def test_normalize_token_passthrough_for_non_canonical():
    # If the user pastes something that doesn't match the canonical regex,
    # we trim and return as-is rather than rejecting (the API call below
    # will fail with HTTP 401 / 404 and surface the right message).
    assert setup_wizard._normalize_token("notatoken") == "notatoken"


# ----- _explain_http_error -----

@pytest.mark.parametrize("code,err,expected_substr", [
    (401, "Unauthorized", "rejected the token"),
    (404, "Not Found", "URL is malformed"),
    (429, "Too Many", "rate-limiting"),
    (500, "Server", "transient"),
    (503, "Bad Gateway", "transient"),
    (None, "network: timed out", "can't reach api.telegram.org"),
])
def test_explain_http_error(code, err, expected_substr):
    msg = setup_wizard._explain_http_error(code, err)
    assert expected_substr in msg


# ----- _CHAT_NOT_FOUND_RE -----

@pytest.mark.parametrize("text", [
    "Bad Request: chat not found",
    "BAD REQUEST: Chat Not Found",
    "Chat_not_found",
    "chat-not-found",
])
def test_chat_not_found_regex_matches(text):
    assert setup_wizard._CHAT_NOT_FOUND_RE.search(text) is not None


@pytest.mark.parametrize("text", [
    "user not found",
    "permission denied",
    "",
])
def test_chat_not_found_regex_no_match(text):
    assert setup_wizard._CHAT_NOT_FOUND_RE.search(text) is None


# ----- _fetch_chat_id group-chat detection -----

def test_fetch_chat_id_returns_advisory_for_group_only(monkeypatch):
    monkeypatch.setattr(setup_wizard, "_http_json", lambda url: (
        {"ok": True, "result": [
            {"message": {"chat": {"id": -100, "type": "group", "title": "g"}}},
            {"message": {"chat": {"id": -101, "type": "supergroup", "title": "g2"}}},
        ]}, 200, ""
    ))
    cid, who, advisory = setup_wizard._fetch_chat_id("tok")
    assert cid is None
    assert who is None
    assert advisory is not None
    assert "group" in advisory or "supergroup" in advisory


def test_fetch_chat_id_picks_private(monkeypatch):
    monkeypatch.setattr(setup_wizard, "_http_json", lambda url: (
        {"ok": True, "result": [
            {"message": {"chat": {"id": -100, "type": "group"}}},
            {"message": {"chat": {"id": 42, "type": "private", "username": "alice"}}},
        ]}, 200, ""
    ))
    cid, who, advisory = setup_wizard._fetch_chat_id("tok")
    assert cid == 42
    assert who == "alice"
    assert advisory is None


def test_fetch_chat_id_empty(monkeypatch):
    monkeypatch.setattr(setup_wizard, "_http_json",
                        lambda url: ({"ok": True, "result": []}, 200, ""))
    cid, who, advisory = setup_wizard._fetch_chat_id("tok")
    assert cid is None
    assert advisory is None


# ----- _validate_settings_schema -----

def test_validate_schema_accepts_dict():
    setup_wizard._validate_settings_schema(
        {"hooks": {"SessionStart": [{"hooks": []}]}}
    )


def test_validate_schema_accepts_missing_hooks():
    setup_wizard._validate_settings_schema({})


def test_validate_schema_rejects_str_hooks():
    with pytest.raises(ValueError, match="dict"):
        setup_wizard._validate_settings_schema({"hooks": "oops"})


def test_validate_schema_rejects_str_event_value():
    with pytest.raises(ValueError, match="list"):
        setup_wizard._validate_settings_schema(
            {"hooks": {"SessionStart": "nope"}}
        )


# ----- _step_settings JSONC detection -----

def test_step_settings_jsonc_comment_hint(monkeypatch, tmp_path, capsys):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("// a comment\n{}\n")
    monkeypatch.setattr(setup_wizard, "CLAUDE_SETTINGS", settings_file)

    with pytest.raises(ValueError) as exc:
        setup_wizard._step_settings()
    assert "comments" in str(exc.value).lower() or "//" in str(exc.value)


def test_step_settings_skips_backup_if_unchanged(monkeypatch, tmp_path, capsys):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(setup_wizard, "CLAUDE_SETTINGS", settings_file)
    monkeypatch.setattr(setup_wizard.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    # First call: writes initial.
    setup_wizard._step_settings()
    assert settings_file.exists()
    before_files = sorted(tmp_path.iterdir())

    # Second call: should be a no-op since content is identical.
    setup_wizard._step_settings()
    after_files = sorted(tmp_path.iterdir())
    assert before_files == after_files, "no backup should have been created"
    out = capsys.readouterr().out
    assert "already up to date" in out


# ----- _step_deps blocking behaviour -----

def test_step_deps_returns_true_when_all_present(monkeypatch, capsys):
    monkeypatch.setattr(setup_wizard.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    # dtach_bin import path
    import sys as _sys

    class _FakeDtachBin:
        @staticmethod
        def path():
            return "/usr/bin/dtach"
    monkeypatch.setitem(_sys.modules, "dtach_bin", _FakeDtachBin)
    assert setup_wizard._step_deps() is True


def test_step_deps_returns_false_when_hook_missing(monkeypatch):
    monkeypatch.setattr(setup_wizard.shutil, "which",
                        lambda name: None if "hook" in name or "status" in name else "/usr/bin/x")
    import sys as _sys

    class _FakeDtachBin:
        @staticmethod
        def path():
            return "/usr/bin/dtach"
    monkeypatch.setitem(_sys.modules, "dtach_bin", _FakeDtachBin)
    assert setup_wizard._step_deps() is False


# ----- _step_write_env permissions tolerance -----

def test_step_write_env_chmod_failure_warns(monkeypatch, tmp_path, capsys):
    target = tmp_path / "config.env"
    monkeypatch.setattr(setup_wizard, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(setup_wizard, "CONFIG_ENV", target)

    def _boom(*a, **k):
        raise OSError("Operation not permitted")
    monkeypatch.setattr(setup_wizard.os, "chmod", _boom)

    setup_wizard._step_write_env("toktok", 99)
    assert target.exists()
    contents = target.read_text()
    assert "toktok" in contents
    assert "99" in contents
    err = capsys.readouterr().err
    assert "chmod" in err.lower() or "non-POSIX" in err
