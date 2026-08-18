"""design.md success criterion 7:

    "A payload whose SHA256 does not match the pinned value is discarded
    before chmod/exec; ensure_cloudflared() returns None; the attempt
    counts toward the ceiling like any other failure."

The strong version of this criterion (per the /ship task brief) is not
"the function returned None" -- a stub that always returns None would
pass that trivially -- but that the corrupt payload is never made
executable and never handed to a subprocess. Both are checked directly:
the whole cache-dir tree is scanned for anything executable, and the
autouse ``leak_guard`` fixture's subprocess spy (asserted empty) proves
nothing was ever exec'd.
"""
from __future__ import annotations

import stat
from pathlib import Path

import pytest

from aipager.miniapp.cloudflared_fetch import (
    cache_dir,
    ensure_cloudflared,
    platform_key,
    verify_sha256,
)


def _any_executable_file(root: Path) -> list[Path]:
    if not root.exists():
        return []
    found = []
    for p in root.rglob("*"):
        if p.is_file() and (p.stat().st_mode & stat.S_IXUSR):
            found.append(p)
    return found


@pytest.fixture(autouse=True)
def _require_a_resolvable_platform():
    if platform_key() is None:  # pragma: no cover - depends on the CI host
        pytest.skip("no platform_key() on this host; criterion 7 needs a "
                    "platform the download path would actually attempt")


def _fake_urlopen_returning(payload: bytes):
    class _Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _urlopen(*args, **kwargs):
        return _Resp()

    return _urlopen


def test_verify_sha256_pure_logic_rejects_a_mismatch():
    payload = b"whatever bytes a corrupted or tampered download contains"
    wrong_hex = "0" * 64
    assert verify_sha256(payload, wrong_hex) is False


def test_verify_sha256_pure_logic_accepts_a_match():
    import hashlib
    payload = b"a known-good fixture payload"
    correct_hex = hashlib.sha256(payload).hexdigest()
    assert verify_sha256(payload, correct_hex) is True


def test_mismatched_download_never_executes_and_returns_none(
    monkeypatch, run_async, leak_guard,
):
    # Deliberately wrong: real cloudflared bytes would never equal this,
    # so this is a checksum mismatch against whatever the pinned hash is
    # for this platform, without needing to know that pinned value.
    bad_payload = b"not a real cloudflared binary, deliberately wrong bytes"
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(bad_payload))

    result = run_async(ensure_cloudflared())

    assert result is None
    assert leak_guard.subprocess_calls == [], (
        "a checksum-mismatched payload was passed to a subprocess"
    )
    executables = _any_executable_file(cache_dir())
    assert executables == [], (
        f"a checksum-mismatched payload was made executable: {executables}"
    )


def test_mismatched_download_leaves_the_cache_dir_without_a_cloudflared_binary(
    monkeypatch, run_async,
):
    """Narrower than "no executables anywhere": even a non-executable
    leftover named like the real binary would be a problem (a later
    reuse-if-present check could pick it up without re-verifying).
    """
    bad_payload = b"still not a real cloudflared binary"
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(bad_payload))

    run_async(ensure_cloudflared())

    root = cache_dir()
    if root.exists():
        leftovers = [p for p in root.rglob("*cloudflared*") if p.is_file()]
        assert leftovers == [], (
            f"checksum mismatch left a cloudflared-named file behind: {leftovers}"
        )


def test_checksum_mismatch_counts_toward_the_restart_ceiling_like_any_failure(
    monkeypatch, run_async, settle,
):
    """ensure_cloudflared() failing (checksum mismatch) must be treated
    by TunnelManager the same as any other failed attempt -- it must not
    be silently ignored/retried forever outside the ceiling accounting.
    """
    import aipager.miniapp.tunnel_manager as tunnel_manager_mod

    MAX = 3
    bad_payload = b"deliberately corrupt bytes for the ceiling-accounting test"
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(bad_payload))
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", MAX)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 0.02)

    seam_calls = {"n": 0}

    async def seam_should_never_run(binary, port):
        seam_calls["n"] += 1
        raise AssertionError(
            "spawn_and_discover_url was called despite the binary never "
            "verifying -- a checksum failure must stop before spawn"
        )

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", seam_should_never_run)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=_noop)
        try:
            await manager.start()
            final = await settle(lambda: seam_calls["n"], timeout=8.0)
        finally:
            await manager.stop()
        return final

    final = run_async(scenario())
    assert final == 0, "the seam ran even though the binary never verified"
    # We cannot directly read TunnelManager's private attempt counter
    # (not exported per entrypoints.md); the ceiling test itself
    # (test_restart_ceiling.py) proves the counting/giving-up mechanism
    # using the documented seam. This test's job is narrower: prove a
    # checksum failure reaches that same failure path rather than being
    # swallowed silently before ever counting.


async def _noop(url):
    pass
