"""design.md success criterion 17:

    "SOCKET_PATH resolves under XDG_RUNTIME_DIR when set; falls back to
    /tmp/aipager.sock when unset."

``aipager.config.SOCKET_PATH`` itself is a module-level constant
computed once at import time, so it can't be re-derived per test-case
by toggling env vars after the fact; the pure function behind it
(``_default_socket_path``, documented indirectly via entrypoints.md's
"Env vars" section: "AIPAGER_SOCKET_PATH ... else $XDG_RUNTIME_DIR,
else /tmp/aipager.sock") is what's exercised directly here, matching
the exact three-way precedence entrypoints.md documents.
"""
from __future__ import annotations

from aipager.config import _default_socket_path


def test_resolves_under_xdg_runtime_dir_when_set(monkeypatch):
    monkeypatch.delenv("AIPAGER_SOCKET_PATH", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")

    assert _default_socket_path() == "/run/user/4242/aipager.sock"


def test_falls_back_to_tmp_when_neither_is_set(monkeypatch):
    monkeypatch.delenv("AIPAGER_SOCKET_PATH", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    assert _default_socket_path() == "/tmp/aipager.sock"


def test_explicit_override_wins_outright(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/4242")
    monkeypatch.setenv("AIPAGER_SOCKET_PATH", "/custom/path/mine.sock")

    assert _default_socket_path() == "/custom/path/mine.sock"
