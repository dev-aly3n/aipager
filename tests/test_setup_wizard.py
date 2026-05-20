"""Tests for aipager.wizard helpers."""

from __future__ import annotations

import pytest

from aipager.wizard import _constants, settings_patch, team_setup, telegram_api


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
    assert telegram_api._normalize_token(raw) == expected


def test_normalize_token_empty():
    assert telegram_api._normalize_token("") == ""
    assert telegram_api._normalize_token("   ") == ""


def test_normalize_token_passthrough_for_non_canonical():
    # If the user pastes something that doesn't match the canonical regex,
    # we trim and return as-is rather than rejecting (the API call below
    # will fail with HTTP 401 / 404 and surface the right message).
    assert telegram_api._normalize_token("notatoken") == "notatoken"


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
    msg = telegram_api._explain_http_error(code, err)
    assert expected_substr in msg


# ----- _CHAT_NOT_FOUND_RE -----

@pytest.mark.parametrize("text", [
    "Bad Request: chat not found",
    "BAD REQUEST: Chat Not Found",
    "Chat_not_found",
    "chat-not-found",
])
def test_chat_not_found_regex_matches(text):
    assert _constants._CHAT_NOT_FOUND_RE.search(text) is not None


@pytest.mark.parametrize("text", [
    "user not found",
    "permission denied",
    "",
])
def test_chat_not_found_regex_no_match(text):
    assert _constants._CHAT_NOT_FOUND_RE.search(text) is None


# ----- _fetch_chat_id group-chat detection -----

def test_fetch_chat_id_returns_advisory_for_group_only(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json", lambda url: (
        {"ok": True, "result": [
            {"message": {"chat": {"id": -100, "type": "group", "title": "g"}}},
            {"message": {"chat": {"id": -101, "type": "supergroup", "title": "g2"}}},
        ]}, 200, ""
    ))
    cid, who, advisory = team_setup._fetch_chat_id("tok")
    assert cid is None
    assert who is None
    assert advisory is not None
    assert "group" in advisory or "supergroup" in advisory


def test_fetch_chat_id_picks_private(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json", lambda url: (
        {"ok": True, "result": [
            {"message": {"chat": {"id": -100, "type": "group"}}},
            {"message": {"chat": {"id": 42, "type": "private", "username": "alice"}}},
        ]}, 200, ""
    ))
    cid, who, advisory = team_setup._fetch_chat_id("tok")
    assert cid == 42
    assert who == "alice"
    assert advisory is None


def test_fetch_chat_id_empty(monkeypatch):
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": []}, 200, ""))
    cid, who, advisory = team_setup._fetch_chat_id("tok")
    assert cid is None
    assert advisory is None


# ----- _validate_settings_schema -----

def test_validate_schema_accepts_dict():
    settings_patch._validate_settings_schema(
        {"hooks": {"SessionStart": [{"hooks": []}]}}
    )


def test_validate_schema_accepts_missing_hooks():
    settings_patch._validate_settings_schema({})


def test_validate_schema_rejects_str_hooks():
    with pytest.raises(ValueError, match="dict"):
        settings_patch._validate_settings_schema({"hooks": "oops"})


def test_validate_schema_rejects_str_event_value():
    with pytest.raises(ValueError, match="list"):
        settings_patch._validate_settings_schema(
            {"hooks": {"SessionStart": "nope"}}
        )


# ----- _step_settings JSONC detection -----

def test_step_settings_jsonc_comment_hint(monkeypatch, tmp_path, capsys):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("// a comment\n{}\n")
    monkeypatch.setattr(settings_patch, "CLAUDE_SETTINGS", settings_file)

    with pytest.raises(ValueError) as exc:
        settings_patch._step_settings()
    assert "comments" in str(exc.value).lower() or "//" in str(exc.value)


def test_step_settings_skips_backup_if_unchanged(monkeypatch, tmp_path, capsys):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_patch, "CLAUDE_SETTINGS", settings_file)
    monkeypatch.setattr(settings_patch.shutil, "which",
                        lambda name: f"/usr/bin/{name}")

    # First call: writes initial.
    settings_patch._step_settings()
    assert settings_file.exists()
    before_files = sorted(tmp_path.iterdir())

    # Second call: should be a no-op since content is identical.
    settings_patch._step_settings()
    after_files = sorted(tmp_path.iterdir())
    assert before_files == after_files, "no backup should have been created"
    out = capsys.readouterr().out
    assert "already up to date" in out


# ----- _step_deps blocking behaviour -----

def test_step_deps_returns_true_when_all_present(monkeypatch, capsys):
    monkeypatch.setattr(settings_patch.shutil, "which",
                        lambda name: f"/usr/bin/{name}")
    # dtach_bin import path
    import sys as _sys

    class _FakeDtachBin:
        @staticmethod
        def path():
            return "/usr/bin/dtach"
    monkeypatch.setitem(_sys.modules, "dtach_bin", _FakeDtachBin)
    assert settings_patch._step_deps() is True


def test_step_deps_returns_false_when_hook_missing(monkeypatch):
    monkeypatch.setattr(settings_patch.shutil, "which",
                        lambda name: None if "hook" in name or "status" in name else "/usr/bin/x")
    import sys as _sys

    class _FakeDtachBin:
        @staticmethod
        def path():
            return "/usr/bin/dtach"
    monkeypatch.setitem(_sys.modules, "dtach_bin", _FakeDtachBin)
    assert settings_patch._step_deps() is False


# ----- _resolve_user --------------------------------------------------


def test_resolve_user_at_handle_returns_id_and_username(monkeypatch):

    def fake_http(url, timeout=10.0):
        assert "getChat" in url and "chat_id=%40arian_hamdi" in url \
            or "chat_id=@arian_hamdi" in url
        return ({
            "ok": True,
            "result": {
                "id": 256113222,
                "first_name": "Arian",
                "username": "Arian_Hamdi",
                "type": "private",
            },
        }, 200, "")

    monkeypatch.setattr(team_setup, "_http_json", fake_http)
    out = team_setup._resolve_user("token", "@arian_hamdi")
    assert out == (256113222, "arian_hamdi")  # lowercased


def test_resolve_user_bare_handle_gets_at_prefixed(monkeypatch):
    seen = []

    def fake_http(url, timeout=10.0):
        seen.append(url)
        return ({
            "ok": True,
            "result": {"id": 1, "username": "alice", "type": "private"},
        }, 200, "")

    monkeypatch.setattr(team_setup, "_http_json", fake_http)
    out = team_setup._resolve_user("t", "alice")  # no @
    assert out == (1, "alice")
    assert "@alice" in seen[0]


def test_resolve_user_numeric_input_kept_as_is(monkeypatch):
    seen = []

    def fake_http(url, timeout=10.0):
        seen.append(url)
        return ({
            "ok": True,
            "result": {"id": 12345, "first_name": "Bob", "type": "private"},
        }, 200, "")

    monkeypatch.setattr(team_setup, "_http_json", fake_http)
    out = team_setup._resolve_user("t", "12345")
    # falls back to first_name (lowercased) when no username
    assert out == (12345, "bob")
    assert "chat_id=12345" in seen[0]


def test_resolve_user_unknown_handle_returns_none(monkeypatch):
    monkeypatch.setattr(team_setup, "_http_json",
                        lambda u, timeout=10.0: ({"ok": False,
                                                   "description": "chat not found"}, 400, ""))
    assert team_setup._resolve_user("t", "@unknown") is None


def test_resolve_user_rejects_non_private_chat(monkeypatch):
    monkeypatch.setattr(team_setup, "_http_json",
                        lambda u, timeout=10.0: ({
                            "ok": True,
                            "result": {"id": -100, "type": "channel",
                                       "title": "AipagerDev"},
                        }, 200, ""))
    assert team_setup._resolve_user("t", "@aipagerdev") is None


def test_resolve_user_empty_inputs_return_none():
    assert team_setup._resolve_user("", "@alice") is None
    assert team_setup._resolve_user("token", "") is None
    assert team_setup._resolve_user("token", "   ") is None


def test_resolve_user_network_error_returns_none(monkeypatch):
    monkeypatch.setattr(team_setup, "_http_json",
                        lambda u, timeout=10.0: (None, None, "network: unreachable"))
    assert team_setup._resolve_user("t", "@alice") is None


# ----- _finalize_user --------------------------------------------------


def test_finalize_user_accepts_default_on_empty_input(monkeypatch):
    """Empty label input falls back to suggested_label."""
    monkeypatch.setattr(team_setup, "_ask", lambda q: "")  # admin hits enter
    out = team_setup._finalize_user(42, "alice", existing_labels=set())
    assert out == {"id": 42, "label": "alice"}


def test_finalize_user_uses_typed_label(monkeypatch):
    monkeypatch.setattr(team_setup, "_ask", lambda q: "custom")
    out = team_setup._finalize_user(42, "alice", existing_labels=set())
    assert out == {"id": 42, "label": "custom"}


def test_resolve_user_falls_back_to_getUpdates_when_getChat_fails(monkeypatch):
    """Telegram's bot API blocks @handle → user_id lookup via getChat for
    most users. _resolve_user should fall back to scanning getUpdates for
    a message whose from.username matches."""

    def fake_http(url, timeout=10.0):
        if "getChat" in url:
            # Telegram says "chat not found" for the @handle
            return ({"ok": False, "description": "chat not found"}, 400, "")
        # getUpdates returns a recent message from the user
        return ({
            "ok": True,
            "result": [{
                "message": {
                    "from": {
                        "id": 256113222,
                        "username": "Arian_Hamdi",
                        "first_name": "Arian",
                    },
                    "chat": {"id": -100, "type": "supergroup"},
                },
            }],
        }, 200, "")

    monkeypatch.setattr(team_setup, "_http_json", fake_http)
    out = team_setup._resolve_user("token", "@arian_hamdi")
    assert out == (256113222, "arian_hamdi")


def test_resolve_user_case_insensitive_username_match(monkeypatch):
    """Telegram canonical-lowercases @handles, but admins paste mixed case.
    The fallback scan should match regardless of input case."""

    def fake_http(url, timeout=10.0):
        if "getChat" in url:
            return ({"ok": False, "description": "chat not found"}, 400, "")
        return ({
            "ok": True,
            "result": [{
                "message": {
                    "from": {"id": 99, "username": "alice"},
                    "chat": {"id": -1, "type": "group"},
                },
            }],
        }, 200, "")

    monkeypatch.setattr(team_setup, "_http_json", fake_http)
    assert team_setup._resolve_user("t", "@ALICE")[0] == 99
    assert team_setup._resolve_user("t", "@Alice")[0] == 99
    assert team_setup._resolve_user("t", "alice")[0] == 99


def test_resolve_user_handle_with_no_updates_returns_none(monkeypatch):
    """If the user has never messaged any chat the bot is in, neither
    getChat nor getUpdates can resolve them."""

    def fake_http(url, timeout=10.0):
        if "getChat" in url:
            return ({"ok": False, "description": "chat not found"}, 400, "")
        return ({"ok": True, "result": []}, 200, "")  # no recent updates

    monkeypatch.setattr(team_setup, "_http_json", fake_http)
    assert team_setup._resolve_user("t", "@phantom") is None


def test_resolve_user_numeric_skips_updates_scan(monkeypatch):
    """Numeric input must NOT trigger the getUpdates fallback."""
    seen_urls = []

    def fake_http(url, timeout=10.0):
        seen_urls.append(url)
        if "getChat" in url:
            return ({"ok": False, "description": "chat not found"}, 400, "")
        return ({"ok": True, "result": []}, 200, "")

    monkeypatch.setattr(team_setup, "_http_json", fake_http)
    out = team_setup._resolve_user("t", "12345")
    assert out is None
    # Only one call (the getChat); no fallback scan for numeric input.
    assert len(seen_urls) == 1 and "getChat" in seen_urls[0]
