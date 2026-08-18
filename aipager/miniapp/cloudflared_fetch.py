"""Download, verify and cache the ``cloudflared`` binary aipager uses to
run the managed Mini App tunnel.

Stdlib only, on purpose — a base install (no ``aipager[miniapp]``
extra) must still be able to import this module cleanly (see
``tests/test_no_aiohttp_in_base_modules.py``, which does not cover this
file directly today, but the discipline is the same one that test
enforces elsewhere in ``aipager.miniapp``).

**A system ``cloudflared`` on ``PATH`` is never trusted** — unlike
``aipager.dtach.inject._resolve_dtach``'s fallback to ``shutil.which``.
cloudflared opens a public HTTPS path from the internet into this
machine's loopback server; an arbitrary, unverified binary merely
*named* ``cloudflared`` somewhere on ``PATH`` is a categorically bigger
risk than dtach's local PTY injection. This module only ever executes
its own downloaded-and-verified copy.

**Verification is fail-closed and happens before any bytes are made
executable or run.** The download is HTTPS via ``urllib.request``
(stdlib, cert-verified by default — same call shape
``cli/daemon.py::_telegram_preflight`` already uses). The bytes' SHA256
is compared against a pinned, hardcoded hash for the resolved platform
— sourced from Cloudflare's own release page "SHA256 Checksums" block
for the pinned tag, never invented. Every failure mode (unsupported
platform, no network, checksum mismatch, corrupt archive, install
error) collapses to the same outcome: :func:`ensure_cloudflared`
returns ``None``. Never raises. Never ``chmod``\\ s or executes
anything that has not passed verification.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import platform
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Pinned version + checksums. Maintainer-controlled security constants —
# NOT operator tunables, so they live here rather than in config.py (see
# config.py's own comment on the managed-tunnel section for why).
#
# Update BOTH together, in the same commit, whenever the pin moves:
# never point at a floating "latest" URL, which would silently break
# every hash the moment Cloudflare cuts a new release.
# ---------------------------------------------------------------------

_CLOUDFLARED_VERSION = "2026.8.2"

_RELEASE_BASE = "https://github.com/cloudflare/cloudflared/releases/download"

# Filenames as published for this tag. macOS ships a .tgz containing a
# single `cloudflared` file; every other supported platform ships a bare
# executable.
_ASSET_NAMES: dict[str, str] = {
    "linux-amd64": "cloudflared-linux-amd64",
    "linux-arm64": "cloudflared-linux-arm64",
    "darwin-amd64": "cloudflared-darwin-amd64.tgz",
    "darwin-arm64": "cloudflared-darwin-arm64.tgz",
}

# SHA256 of the exact bytes served at each _ASSET_NAMES URL for
# _CLOUDFLARED_VERSION (the .tgz itself for darwin, not the binary
# inside it — verification happens BEFORE extraction).
#
# Sourcing note (read before ever changing these): Cloudflare's GitHub
# release for this tag publishes a "### SHA256 Checksums:" text block in
# the release body. For linux-amd64, linux-arm64 and both .pkg assets,
# that text block agrees exactly with the SHA256 GitHub itself computed
# for the uploaded asset (the release API's `digest` field) — but for
# BOTH darwin assets (`cloudflared-darwin-amd64.tgz`,
# `cloudflared-darwin-arm64.tgz`) the published text block does NOT
# match the actual bytes being served; it appears stale, from a build
# that was re-uploaded after the checksums text was written. Confirmed
# by downloading both .tgz files directly and computing sha256 locally —
# the values below match the REAL downloaded bytes (and GitHub's own
# `digest` field), not the release-body text for those two entries. A
# hash that never matches what's actually downloaded would permanently
# and silently disable the managed tunnel for every macOS install (fails
# closed per this module's contract, but needs a human to notice and
# fix it) — pinning the text-block value here would have shipped exactly
# that bug on day one.
_SHA256: dict[str, str] = {
    "linux-amd64": "fcfb02b575a52ca1af2e3267af4e1517bcdeb30ac48c834c69abaed3c0576ad2",
    "linux-arm64": "7747d94570fb390cf47dcb4f9555c193c6355cda9793f0d878d9049e5d6a7790",
    "darwin-amd64": "f1727723c586500e2092368ae21871b3df7ddfd2cb097f22d81bee4a9c458bb4",
    "darwin-arm64": "9042c2c5d8b2de78e60f313d5fb31b6c5c1cebde787a3caf1f2c9588084ac442",
}

_DOWNLOAD_TIMEOUT_SECONDS = 30.0


def platform_key() -> str | None:
    """``"linux-amd64"`` / ``"linux-arm64"`` / ``"darwin-amd64"`` /
    ``"darwin-arm64"``, or ``None`` for anything else (including
    Windows, 32-bit, or an unrecognised ``machine()``) — the caller's
    cue to disable the feature gracefully rather than guess."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        if machine in ("x86_64", "amd64"):
            return "linux-amd64"
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
        return None
    if system == "Darwin":
        if machine in ("x86_64", "amd64"):
            return "darwin-amd64"
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64"
        return None
    return None


def cache_dir() -> Path:
    """Where the verified binary is/will be cached.

    Honours ``AIPAGER_CLOUDFLARED_CACHE_DIR`` (read fresh on every call,
    never cached at import time) so tests can redirect it with a plain
    env-var patch and never touch
    ``~/.local/share/aipager/cloudflared/``. Created ``0o700`` —
    ``mkdir(mode=)`` alone is subject to umask, so the mode is set again
    explicitly after creation, mirroring
    ``wizard/settings_patch.py::_write_config_env``'s discipline.
    """
    override = os.environ.get("AIPAGER_CLOUDFLARED_CACHE_DIR")
    if override:
        directory = Path(override)
    else:
        directory = (
            Path.home() / ".local" / "share" / "aipager"
            / "cloudflared" / _CLOUDFLARED_VERSION
        )
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def verify_sha256(data: bytes, expected_hex: str) -> bool:
    """Pure, no I/O. ``True`` iff ``sha256(data).hexdigest()`` equals
    ``expected_hex`` (case-insensitively)."""
    return hashlib.sha256(data).hexdigest() == expected_hex.lower()


def _download_sync(url: str) -> bytes | None:
    """Download ``url`` synchronously (cert-verified by default — no
    custom SSL context, same as the stdlib default
    ``cli/daemon.py::_telegram_preflight`` relies on). Always called
    inside an executor, never on the event loop thread.

    Never raises: a network failure, a bad status, a timeout — all
    collapse to ``None``, the same "no binary available" outcome as
    every other fetch failure. ``urllib.error.HTTPError`` is a
    ``URLError`` subclass, so it is covered by the same branch.
    """
    try:
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        log.warning("cloudflared: download failed for %s: %s", url, exc)
        return None


def _extract_tgz(data: bytes) -> bytes | None:
    """Pull the ``cloudflared`` binary out of a downloaded macOS
    ``.tgz``. Never raises — a corrupt or unexpected archive returns
    ``None``, the same as any other verification failure."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member = tar.getmember("cloudflared")
            extracted = tar.extractfile(member)
            if extracted is None:
                return None
            return extracted.read()
    except (tarfile.TarError, KeyError, OSError):
        return None


def _atomic_install(dest: Path, data: bytes) -> bool:
    """Write ``data`` to a temp file beside ``dest``, ``chmod(0o755)``
    it, then ``Path.replace()`` it into place — so a reader never
    observes a partially-written or not-yet-verified file at ``dest``.
    Only ever called after :func:`verify_sha256` has passed. Never
    raises; a failure at any step returns ``False`` and best-effort
    cleans up the temp file."""
    tmp = dest.with_name(f"{dest.name}.download-{os.getpid()}")
    try:
        tmp.write_bytes(data)
        tmp.chmod(0o755)
        tmp.replace(dest)
        return True
    except OSError as exc:
        log.warning("cloudflared: could not install binary to %s: %s", dest, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


async def ensure_cloudflared() -> str | None:
    """Absolute path to a verified, executable ``cloudflared`` binary —
    downloading and caching it on first call, reusing the cache on
    every call after. Returns ``None`` (never raises) when the platform
    is unsupported, there is no network, the download fails, or
    verification fails.
    """
    try:
        key = platform_key()
        if key is None:
            log.warning(
                "cloudflared: unsupported platform (%s/%s) — managed Mini "
                "App tunnel disabled",
                platform.system(), platform.machine(),
            )
            return None

        dest = cache_dir() / "cloudflared"
        if dest.is_file() and os.access(dest, os.X_OK):
            return str(dest)

        filename = _ASSET_NAMES[key]
        url = f"{_RELEASE_BASE}/{_CLOUDFLARED_VERSION}/{filename}"
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, _download_sync, url)
        if data is None:
            return None

        expected = _SHA256[key]
        if not verify_sha256(data, expected):
            log.warning(
                "cloudflared: checksum mismatch for %s (expected %s) — "
                "discarding download, never executing it",
                filename, expected,
            )
            return None

        if filename.endswith(".tgz"):
            binary = _extract_tgz(data)
            if binary is None:
                log.warning(
                    "cloudflared: could not extract binary from %s", filename,
                )
                return None
        else:
            binary = data

        if not _atomic_install(dest, binary):
            return None

        log.info(
            "cloudflared: downloaded and verified %s (%s) -> %s",
            _CLOUDFLARED_VERSION, key, dest,
        )
        return str(dest)
    except Exception:
        # The docstring promises this never raises — mirrors
        # bot/lifecycle.py::publish_miniapp_button's own reasoning: the
        # guarantee has to cover every step, not just the ones already
        # wrapped in a narrower try/except above.
        log.warning("cloudflared: fetch/verify failed unexpectedly", exc_info=True)
        return None


__all__ = ["ensure_cloudflared", "platform_key", "cache_dir", "verify_sha256"]
