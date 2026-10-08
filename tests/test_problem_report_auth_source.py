"""The Claude auth source a problem report names (roadmap 8.112 step 3):
found once at daemon start and kept on the bot, never probed again."""

from __future__ import annotations

import pytest

from aipager import claude_bootstrap
from aipager.claude_bootstrap import ProvenanceInfo
from aipager.claude_resolve import AuthStatus
from tests.test_auth_notice import _bootstrap_with_auth, _run_daemon_with


@pytest.mark.parametrize("source", ["env", "file", "probe-failed"])
def test_bootstrap_keeps_the_auth_source(monkeypatch, tmp_path, source):
    info = _bootstrap_with_auth(monkeypatch, tmp_path,
                                AuthStatus(source != "probe-failed", "oauth_token", source))
    assert info.auth_source == source


def test_provenance_defaults_to_unknown():
    info = ProvenanceInfo(lines=["x"], auth_ok=True,
                          pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {}))
    assert info.auth_source == "unknown"


def test_the_daemon_hands_it_to_the_bot(monkeypatch):
    monkeypatch.setattr(claude_bootstrap, "recover_auth_or_notice", lambda p: None)
    bot = _run_daemon_with(monkeypatch, ProvenanceInfo(
        lines=["x"], auth_ok=True, auth_source="keychain",
        pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {})))
    assert bot.claude_auth_source == "keychain"


def test_no_binary_means_unknown(monkeypatch):
    bot = _run_daemon_with(monkeypatch, None)
    assert bot.claude_auth_source == "unknown"
