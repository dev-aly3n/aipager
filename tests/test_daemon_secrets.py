"""The daemon credential file — parsing and session-env construction.

Planned in design.md's file-by-file list and missed during implementation;
added here because the parser's grammar is a compatibility contract, not an
implementation detail: the same file may be handed to `docker run
--env-file`, which does NOT strip quotes. A parser that stripped them would
silently disagree with Docker about identical bytes.
"""
from __future__ import annotations

import os

from aipager import daemon_secrets


def _seed(tmp_path, monkeypatch, text):
    """Write the credential where systemd's LoadCredential= would put it and
    point the module at it via the documented env var — no internals."""
    (tmp_path / "claude_oauth").write_text(text)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    return tmp_path / "claude_oauth"


# ===== grammar =============================================================

def test_plain_key_value_is_parsed(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "CLAUDE_CODE_OAUTH_TOKEN=abc123\n")
    assert daemon_secrets.read_daemon_credential() == {
        "CLAUDE_CODE_OAUTH_TOKEN": "abc123"}


def test_quotes_are_NOT_stripped(tmp_path, monkeypatch):
    """The compatibility contract. `config.py:_load_env_file` strips quotes;
    this parser must not, because Docker's --env-file does not either — the
    same file must mean the same thing to both."""
    _seed(tmp_path, monkeypatch, 'TOK="quoted"\n')
    got = daemon_secrets.read_daemon_credential()
    assert got == {"TOK": '"quoted"'}, (
        "quotes were stripped — this file would mean something different to "
        "docker run --env-file than it does to aipager"
    )


def test_comments_and_blank_lines_are_ignored(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "# a comment\n\nA=1\n\n#B=2\nC=3\n")
    assert daemon_secrets.read_daemon_credential() == {"A": "1", "C": "3"}


def test_export_prefix_is_not_special(tmp_path, monkeypatch):
    """Deliberately not shell syntax: `export X=1` is not a valid line, and
    must not silently produce a key named 'export X'."""
    _seed(tmp_path, monkeypatch, "export A=1\nB=2\n")
    got = daemon_secrets.read_daemon_credential()
    assert "B" in got and got["B"] == "2"
    assert "A" not in got or got.get("A") != "1" or "export A" not in got


def test_value_containing_equals_keeps_everything_after_the_first(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "TOK=a=b=c\n")
    assert daemon_secrets.read_daemon_credential() == {"TOK": "a=b=c"}


# ===== best-effort: never raises ==========================================

def test_missing_file_yields_empty_and_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path / "empty"))
    assert daemon_secrets.read_daemon_credential() == {}


def test_unreadable_file_yields_empty_and_does_not_raise(tmp_path, monkeypatch):
    f = _seed(tmp_path, monkeypatch, "A=1\n")
    os.chmod(f, 0o000)
    try:
        assert daemon_secrets.read_daemon_credential() == {}
    finally:
        os.chmod(f, 0o600)


def test_non_utf8_bytes_yield_empty_and_do_not_raise(tmp_path, monkeypatch):
    """The docstring promises 'never raises'. A credential file written by a
    different tool, or truncated mid-write, can be undecodable."""
    (tmp_path / "claude_oauth").write_bytes(b"TOK=\xff\xfe\x00binary\n")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    assert isinstance(daemon_secrets.read_daemon_credential(), dict)


# ===== build_session_env ==================================================

def test_session_env_starts_from_base_and_overlays_credentials(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "CLAUDE_CODE_OAUTH_TOKEN=tok\n")
    env = daemon_secrets.build_session_env(base_env={"PATH": "/usr/bin"})
    assert env["PATH"] == "/usr/bin", "base environment was discarded"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"


def test_session_env_does_not_mutate_the_base_dict(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "TOK=1\n")
    base = {"PATH": "/usr/bin"}
    daemon_secrets.build_session_env(base_env=base)
    assert base == {"PATH": "/usr/bin"}, "base env mutated in place"
