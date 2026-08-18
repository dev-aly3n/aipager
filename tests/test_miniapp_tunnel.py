"""Tests for aipager.miniapp.tunnel.detect_public_url — must never raise,
whatever `tailscale` does or doesn't do. Also covers the managed-tunnel
URL slot and resolve_public_url()'s full precedence chain."""

import subprocess
from unittest.mock import MagicMock

import pytest

from aipager.miniapp import tunnel


@pytest.fixture(autouse=True)
def _reset_managed_tunnel_url():
    """The managed-tunnel slot is a bare module-level global (by design
    — see tunnel.py's docstring on why it is deliberately in-memory
    only). Reset it around every test in this file so one test setting
    it can never leak into the next, whatever order pytest runs them
    in."""
    tunnel.set_managed_tunnel_url("")
    yield
    tunnel.set_managed_tunnel_url("")


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


# ===== set_managed_tunnel_url / get_managed_tunnel_url ====================

def test_managed_tunnel_url_roundtrip():
    assert tunnel.get_managed_tunnel_url() == ""
    tunnel.set_managed_tunnel_url("https://foo.trycloudflare.com")
    assert tunnel.get_managed_tunnel_url() == "https://foo.trycloudflare.com"


def test_managed_tunnel_url_clear():
    tunnel.set_managed_tunnel_url("https://foo.trycloudflare.com")
    tunnel.set_managed_tunnel_url("")
    assert tunnel.get_managed_tunnel_url() == ""


# ===== resolve_public_url precedence =======================================
# explicit MINIAPP_PUBLIC_URL > managed tunnel slot > Tailscale > ""

def test_resolve_prefers_explicit_override_over_everything(monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "https://override.example/")
    tunnel.set_managed_tunnel_url("https://managed.trycloudflare.com")
    monkeypatch.setattr(tunnel, "detect_public_url", lambda: "https://tailscale.example/")

    assert run_async(tunnel.resolve_public_url()) == "https://override.example/"


def test_resolve_uses_managed_tunnel_when_no_override(monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    tunnel.set_managed_tunnel_url("https://managed.trycloudflare.com")
    # If the managed slot were skipped, this would win instead — proves
    # the managed branch actually short-circuits before Tailscale runs.
    monkeypatch.setattr(tunnel, "detect_public_url", lambda: "https://tailscale.example/")

    assert run_async(tunnel.resolve_public_url()) == "https://managed.trycloudflare.com"


def test_resolve_falls_back_to_tailscale_when_no_override_and_no_managed_url(
    monkeypatch, run_async,
):
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    # managed slot left at "" by the autouse fixture
    monkeypatch.setattr(tunnel, "detect_public_url", lambda: "https://tailscale.example/")

    assert run_async(tunnel.resolve_public_url()) == "https://tailscale.example/"


def test_resolve_returns_empty_when_nothing_resolves(monkeypatch, run_async):
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr(tunnel, "detect_public_url", lambda: None)

    assert run_async(tunnel.resolve_public_url()) == ""


def test_resolve_rejects_a_non_https_managed_url(monkeypatch, run_async):
    """Defensive: resolve_public_url()'s final https-only check must
    still apply to the managed branch, even though TunnelManager only
    ever writes https:// or "" into the slot in practice."""
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    tunnel.set_managed_tunnel_url("http://insecure.trycloudflare.com")

    assert run_async(tunnel.resolve_public_url()) == ""
