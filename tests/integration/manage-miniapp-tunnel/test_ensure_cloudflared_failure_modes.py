"""design.md success criterion 9:

    "ensure_cloudflared() returns None without raising when
    platform_key() is None or the download raises
    URLError/OSError/TimeoutError."
"""
from __future__ import annotations

import socket
import urllib.error

import pytest

import aipager.miniapp.cloudflared_fetch as cf_mod
from aipager.miniapp.cloudflared_fetch import ensure_cloudflared


def test_unsupported_platform_returns_none_without_raising(
    monkeypatch, run_async, leak_guard,
):
    monkeypatch.setattr(cf_mod, "platform_key", lambda: None)

    result = run_async(ensure_cloudflared())

    assert result is None
    assert leak_guard.urlopen_calls == [], (
        "an unsupported platform still attempted a network download"
    )


@pytest.mark.parametrize(
    "exc_factory",
    [
        pytest.param(lambda: urllib.error.URLError("no route to host"), id="URLError"),
        pytest.param(lambda: OSError("network unreachable"), id="OSError"),
        pytest.param(lambda: socket.timeout("timed out"), id="TimeoutError-socket"),
        pytest.param(lambda: TimeoutError("timed out"), id="TimeoutError-builtin"),
    ],
)
def test_download_exception_returns_none_without_raising(
    monkeypatch, run_async, exc_factory,
):
    def _raise(*args, **kwargs):
        raise exc_factory()

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    result = run_async(ensure_cloudflared())

    assert result is None


def test_offline_does_not_leave_a_binary_in_the_cache(monkeypatch, run_async):
    from aipager.miniapp.cloudflared_fetch import cache_dir

    def _raise(*args, **kwargs):
        raise urllib.error.URLError("simulated offline")

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    run_async(ensure_cloudflared())

    root = cache_dir()
    if root.exists():
        leftovers = [p for p in root.rglob("*") if p.is_file()]
        assert leftovers == [], f"an offline download left files behind: {leftovers}"
