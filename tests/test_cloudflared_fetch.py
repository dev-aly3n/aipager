"""Tests for aipager.miniapp.cloudflared_fetch — download, verify, cache.

No test here ever hits the real network, downloads a real binary, or
executes anything: `urllib.request.urlopen` is monkeypatched in every
test that reaches it, and the cache dir is always redirected via
`AIPAGER_CLOUDFLARED_CACHE_DIR` to `tmp_path`.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import urllib.error

import pytest

from aipager.miniapp import cloudflared_fetch as cf


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
    """Every test in this file gets its own cache dir — never the real
    ~/.local/share/aipager/cloudflared/."""
    monkeypatch.setenv("AIPAGER_CLOUDFLARED_CACHE_DIR", str(tmp_path / "cf-cache"))


def _force_platform(monkeypatch, system: str, machine: str) -> None:
    monkeypatch.setattr(cf.platform, "system", lambda: system)
    monkeypatch.setattr(cf.platform, "machine", lambda: machine)


# ===== platform_key =========================================================

@pytest.mark.parametrize("system,machine,expected", [
    ("Linux", "x86_64", "linux-amd64"),
    ("Linux", "amd64", "linux-amd64"),
    ("Linux", "aarch64", "linux-arm64"),
    ("Linux", "arm64", "linux-arm64"),
    ("Darwin", "x86_64", "darwin-amd64"),
    ("Darwin", "arm64", "darwin-arm64"),
    ("Linux", "i686", None),
    ("Linux", "armv7l", None),
    ("Darwin", "i386", None),
    ("Windows", "AMD64", None),
    ("FreeBSD", "amd64", None),
])
def test_platform_key_mapping(monkeypatch, system, machine, expected):
    _force_platform(monkeypatch, system, machine)
    assert cf.platform_key() == expected


# ===== verify_sha256 (pure) =================================================

def test_verify_sha256_correct_hash():
    data = b"hello cloudflared"
    digest = hashlib.sha256(data).hexdigest()
    assert cf.verify_sha256(data, digest) is True


def test_verify_sha256_wrong_hash():
    data = b"hello cloudflared"
    wrong = hashlib.sha256(b"something else").hexdigest()
    assert cf.verify_sha256(data, wrong) is False


def test_verify_sha256_empty_data():
    empty_digest = hashlib.sha256(b"").hexdigest()
    assert cf.verify_sha256(b"", empty_digest) is True
    assert cf.verify_sha256(b"", "not-a-real-hash") is False


def test_verify_sha256_case_insensitive():
    data = b"case check"
    digest = hashlib.sha256(data).hexdigest()
    assert cf.verify_sha256(data, digest.upper()) is True


# ===== cache_dir =============================================================

def test_cache_dir_honours_env_override(tmp_path, monkeypatch):
    target = tmp_path / "somewhere-else"
    monkeypatch.setenv("AIPAGER_CLOUDFLARED_CACHE_DIR", str(target))
    result = cf.cache_dir()
    assert result == target
    assert result.is_dir()


def test_cache_dir_is_mode_0700(tmp_path, monkeypatch):
    target = tmp_path / "perm-check"
    monkeypatch.setenv("AIPAGER_CLOUDFLARED_CACHE_DIR", str(target))
    result = cf.cache_dir()
    assert (result.stat().st_mode & 0o777) == 0o700


def test_cache_dir_default_includes_pinned_version(tmp_path, monkeypatch):
    monkeypatch.delenv("AIPAGER_CLOUDFLARED_CACHE_DIR", raising=False)
    monkeypatch.setattr(cf.Path, "home", lambda: tmp_path)
    result = cf.cache_dir()
    assert result == (
        tmp_path / ".local" / "share" / "aipager" / "cloudflared" / cf._CLOUDFLARED_VERSION
    )


# ===== _extract_tgz (pure-ish, no I/O beyond an in-memory buffer) ==========

def _make_tgz(binary_content: bytes, member_name: str = "cloudflared") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=member_name)
        info.size = len(binary_content)
        tar.addfile(info, io.BytesIO(binary_content))
    return buf.getvalue()


def test_extract_tgz_returns_the_binary_bytes():
    payload = b"#!/bin/sh\necho fake cloudflared\n"
    tgz = _make_tgz(payload)
    assert cf._extract_tgz(tgz) == payload


def test_extract_tgz_missing_member_returns_none():
    tgz = _make_tgz(b"binary", member_name="not-cloudflared")
    assert cf._extract_tgz(tgz) is None


def test_extract_tgz_corrupt_archive_returns_none():
    assert cf._extract_tgz(b"this is not a gzip file at all") is None


# ===== ensure_cloudflared — the full flow, network faked ====================

def _fake_urlopen_returning(data: bytes):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return data

    def _open(url, timeout=None):
        return _Resp()

    return _open


def test_ensure_cloudflared_unsupported_platform_never_touches_network(
    monkeypatch, run_async,
):
    _force_platform(monkeypatch, "Windows", "AMD64")

    def _boom(*a, **k):
        raise AssertionError("urlopen must not be called for an unsupported platform")

    monkeypatch.setattr(cf.urllib.request, "urlopen", _boom)
    assert run_async(cf.ensure_cloudflared()) is None


def test_ensure_cloudflared_offline_returns_none(monkeypatch, run_async):
    _force_platform(monkeypatch, "Linux", "x86_64")

    def _raise(url, timeout=None):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(cf.urllib.request, "urlopen", _raise)
    assert run_async(cf.ensure_cloudflared()) is None


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("dns failure"),
    OSError("connection reset"),
    TimeoutError("timed out"),
])
def test_ensure_cloudflared_download_exceptions_return_none_without_raising(
    monkeypatch, run_async, exc,
):
    _force_platform(monkeypatch, "Linux", "x86_64")

    def _raise(url, timeout=None):
        raise exc

    monkeypatch.setattr(cf.urllib.request, "urlopen", _raise)
    assert run_async(cf.ensure_cloudflared()) is None


def test_ensure_cloudflared_checksum_mismatch_discards_and_never_installs(
    monkeypatch, run_async,
):
    _force_platform(monkeypatch, "Linux", "x86_64")
    payload = b"not the real cloudflared binary"
    # Deliberately do NOT patch _SHA256 — the pinned production hash for
    # linux-amd64 will not match this payload, exercising the real
    # mismatch path end-to-end.
    monkeypatch.setattr(cf.urllib.request, "urlopen", _fake_urlopen_returning(payload))

    result = run_async(cf.ensure_cloudflared())

    assert result is None
    dest = cf.cache_dir() / "cloudflared"
    assert not dest.exists(), "a checksum mismatch must never install a file"


def test_ensure_cloudflared_checksum_mismatch_logs_a_warning(
    monkeypatch, run_async, caplog,
):
    _force_platform(monkeypatch, "Linux", "x86_64")
    monkeypatch.setattr(
        cf.urllib.request, "urlopen", _fake_urlopen_returning(b"wrong bytes"),
    )
    with caplog.at_level("WARNING"):
        run_async(cf.ensure_cloudflared())
    messages = [r.getMessage() for r in caplog.records]
    assert any("checksum" in m and "mismatch" in m for m in messages)


def test_ensure_cloudflared_happy_path_linux_installs_verified_binary(
    monkeypatch, run_async,
):
    _force_platform(monkeypatch, "Linux", "x86_64")
    payload = b"#!/bin/sh\necho fake cloudflared binary\n"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(cf._SHA256, "linux-amd64", digest)
    monkeypatch.setattr(cf.urllib.request, "urlopen", _fake_urlopen_returning(payload))

    result = run_async(cf.ensure_cloudflared())

    assert result is not None
    dest_path = cf.cache_dir() / "cloudflared"
    assert result == str(dest_path)
    assert dest_path.read_bytes() == payload
    assert (dest_path.stat().st_mode & 0o777) == 0o755


def test_ensure_cloudflared_happy_path_darwin_extracts_tgz(monkeypatch, run_async):
    _force_platform(monkeypatch, "Darwin", "arm64")
    binary_payload = b"#!/bin/sh\necho fake darwin cloudflared\n"
    tgz_bytes = _make_tgz(binary_payload)
    digest = hashlib.sha256(tgz_bytes).hexdigest()
    monkeypatch.setitem(cf._SHA256, "darwin-arm64", digest)
    monkeypatch.setattr(cf.urllib.request, "urlopen", _fake_urlopen_returning(tgz_bytes))

    result = run_async(cf.ensure_cloudflared())

    assert result is not None
    dest_path = cf.cache_dir() / "cloudflared"
    # The installed file is the EXTRACTED binary, not the raw .tgz.
    assert dest_path.read_bytes() == binary_payload


def test_ensure_cloudflared_cache_hit_never_touches_network(
    monkeypatch, run_async, tmp_path,
):
    _force_platform(monkeypatch, "Linux", "x86_64")
    dest = cf.cache_dir() / "cloudflared"
    dest.write_bytes(b"already cached")
    dest.chmod(0o755)

    def _boom(*a, **k):
        raise AssertionError("a cache hit must never call urlopen")

    monkeypatch.setattr(cf.urllib.request, "urlopen", _boom)

    result = run_async(cf.ensure_cloudflared())
    assert result == str(dest)


def test_ensure_cloudflared_non_executable_cached_file_is_re_fetched(
    monkeypatch, run_async,
):
    """A cached file that lost its execute bit (e.g. a stray `chmod` on
    the cache dir) must not be handed back as-is — it would fail to
    exec. Re-verify-and-reinstall rather than trusting the path alone."""
    _force_platform(monkeypatch, "Linux", "x86_64")
    dest = cf.cache_dir() / "cloudflared"
    dest.write_bytes(b"stale, not executable")
    dest.chmod(0o644)

    payload = b"#!/bin/sh\necho fresh cloudflared\n"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(cf._SHA256, "linux-amd64", digest)
    monkeypatch.setattr(cf.urllib.request, "urlopen", _fake_urlopen_returning(payload))

    result = run_async(cf.ensure_cloudflared())
    assert result == str(dest)
    assert dest.read_bytes() == payload
    assert (dest.stat().st_mode & 0o777) == 0o755
