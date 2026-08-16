"""Tests for aipager.miniapp.tunnel.detect_public_url — must never raise,
whatever `tailscale` does or doesn't do."""

import subprocess
from unittest.mock import MagicMock

from aipager.miniapp import tunnel


def test_binary_absent_returns_none(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: None)
    assert tunnel.detect_public_url() is None


def test_valid_response_returns_https_url(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/tailscale")
    result = MagicMock(
        returncode=0,
        stdout='{"Self": {"DNSName": "my-node.tailxyz.ts.net."}}',
    )
    monkeypatch.setattr(tunnel.subprocess, "run", lambda *a, **k: result)
    assert tunnel.detect_public_url() == "https://my-node.tailxyz.ts.net/"


def test_malformed_json_returns_none(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/tailscale")
    result = MagicMock(returncode=0, stdout="not json{{{")
    monkeypatch.setattr(tunnel.subprocess, "run", lambda *a, **k: result)
    assert tunnel.detect_public_url() is None


def test_missing_dns_name_returns_none(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/tailscale")
    result = MagicMock(returncode=0, stdout='{"Self": {}}')
    monkeypatch.setattr(tunnel.subprocess, "run", lambda *a, **k: result)
    assert tunnel.detect_public_url() is None


def test_missing_self_key_returns_none(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/tailscale")
    result = MagicMock(returncode=0, stdout="{}")
    monkeypatch.setattr(tunnel.subprocess, "run", lambda *a, **k: result)
    assert tunnel.detect_public_url() is None


def test_nonzero_exit_returns_none(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/tailscale")
    result = MagicMock(returncode=1, stdout="")
    monkeypatch.setattr(tunnel.subprocess, "run", lambda *a, **k: result)
    assert tunnel.detect_public_url() is None


def test_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/tailscale")

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=3)

    monkeypatch.setattr(tunnel.subprocess, "run", _raise)
    assert tunnel.detect_public_url() is None


def test_oserror_returns_none(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/bin/tailscale")

    def _raise(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr(tunnel.subprocess, "run", _raise)
    assert tunnel.detect_public_url() is None
