"""Version parsing and the three release-version lookups (roadmap 8.36).

Any lookup failure must come back as ``None`` ("unknown"), never an
exception or an error dump. The real ``_http_get`` is captured at import
(before conftest's offline recorder replaces it) so the timeout / HTTP
error / oversize cases exercise the genuine function against a fake
``urlopen`` — never the network.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from aipager import self_update
from aipager.self_update import _http_get as REAL_HTTP_GET
from aipager.self_update import (
    claude_channel_info,
    is_newer,
    latest_aipager_version,
    latest_claude_version,
    parse_version,
)


def _fake_get(monkeypatch, body, seen=None):
    def _get(url, *, timeout, max_bytes):
        if seen is not None:
            seen.append((url, timeout, max_bytes))
        return body
    monkeypatch.setattr(self_update, "_http_get", _get)


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ----- parsing ----------------------------------------------------------------

def test_parse_version_plain_releases():
    assert parse_version("0.7.13") == (0, 7, 13)
    assert parse_version("2.1.281") == (2, 1, 281)
    assert parse_version("1.2") == (1, 2)


def test_is_newer_ignores_unparseable_and_prerelease():
    assert is_newer("0.7.14", "0.7.13") is True
    assert is_newer("0.8", "0.7.13") is True
    assert is_newer("0.7.13", "0.7.13") is False
    assert is_newer("0.7.12", "0.7.13") is False
    # Pre-releases and garbage never read as "update available".
    for latest in ("0.8.0rc1", "0.8.0.dev1", "0.8.0-beta", "latest", "", None,
                   "0.8.0+local", "0.8.0.post1"):
        assert is_newer(latest, "0.7.13") is False, latest
    assert is_newer("0.8.0", "0.0.0+unknown") is False
    assert is_newer("0.8.0", None) is False


# ----- aipager (PyPI) -------------------------------------------------------------

def test_latest_aipager_reads_info_version(monkeypatch):
    seen: list = []
    _fake_get(monkeypatch, json.dumps({"info": {"version": "0.7.14"}}).encode(), seen)
    assert latest_aipager_version() == "0.7.14"
    url, timeout, max_bytes = seen[0]
    assert url == "https://pypi.org/pypi/aipager/json"
    assert timeout == self_update.HTTP_TIMEOUT_SECONDS
    assert max_bytes == self_update.PYPI_MAX_BYTES


def _urlopen_raising(exc):
    def _open(req, timeout=None):
        raise exc
    return _open


@pytest.mark.parametrize("case", ["timeout", "http_error", "garbage", "oversize"])
def test_latest_version_unknown_on_fetch_failure(case, monkeypatch):
    if case == "garbage":
        _fake_get(monkeypatch, b"<html>not json</html>")
    else:
        monkeypatch.setattr(self_update, "_http_get", REAL_HTTP_GET)
        if case == "timeout":
            opener = _urlopen_raising(TimeoutError("timed out"))
        elif case == "http_error":
            opener = _urlopen_raising(urllib.error.HTTPError(
                self_update.PYPI_URL, 503, "unavailable", {}, None))
        else:
            body = json.dumps({"info": {"version": "9.9.9"}}).encode()

            def opener(req, timeout=None):
                return _Resp(body + b" " * 64)
            monkeypatch.setattr(self_update, "PYPI_MAX_BYTES", len(body))
        monkeypatch.setattr("urllib.request.urlopen", opener)
    assert latest_aipager_version() is None


def test_http_get_returns_body_within_cap(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: _Resp(b"2.1.281"))
    assert REAL_HTTP_GET("https://example.invalid/x", timeout=1, max_bytes=16) == b"2.1.281"


def test_latest_aipager_rejects_malformed_version(monkeypatch):
    _fake_get(monkeypatch, json.dumps({"info": {"version": "<b>1</b>"}}).encode())
    assert latest_aipager_version() is None
    _fake_get(monkeypatch, json.dumps({"info": {}}).encode())
    assert latest_aipager_version() is None


# ----- Claude Code ----------------------------------------------------------------

def _claude_files(settings=None, claude_json=None):
    from aipager import claude_bootstrap
    claude_bootstrap._SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if settings is not None:
        claude_bootstrap._SETTINGS.write_text(json.dumps(settings))
    if claude_json is not None:
        claude_bootstrap._CLAUDE_JSON.parent.mkdir(parents=True, exist_ok=True)
        claude_bootstrap._CLAUDE_JSON.write_text(json.dumps(claude_json))


def test_claude_latest_uses_configured_channel(monkeypatch):
    _claude_files({"autoUpdatesChannel": "stable"}, {"installMethod": "native"})
    method, channel = claude_channel_info()
    assert (method, channel) == ("native", "stable")
    seen: list = []
    _fake_get(monkeypatch, b"2.1.273\n", seen)
    assert latest_claude_version(method, channel) == "2.1.273"
    assert seen[0][0] == "https://downloads.claude.ai/claude-code-releases/stable"
    assert seen[0][2] == self_update.SMALL_MAX_BYTES


def test_claude_channel_defaults_to_latest():
    _claude_files({"autoUpdatesChannel": "bogus"}, {"installMethod": "native"})
    assert claude_channel_info() == ("native", "latest")
    _claude_files({}, {})
    assert claude_channel_info()[1] == "latest"


def test_claude_latest_npm_maps_rc_to_next(monkeypatch):
    _claude_files({"autoUpdatesChannel": "rc"}, {"installMethod": "npm-global"})
    method, channel = claude_channel_info()
    assert (method, channel) == ("npm", "rc")
    seen: list = []
    _fake_get(monkeypatch, json.dumps(
        {"stable": "2.1.273", "next": "2.1.290", "latest": "2.1.281"}).encode(), seen)
    assert latest_claude_version(method, channel) == "2.1.290"
    assert seen[0][0] == self_update.NPM_DIST_TAGS_URL


def test_claude_latest_unknown_method_is_unknown(monkeypatch):
    seen: list = []
    _fake_get(monkeypatch, b"2.1.281", seen)
    assert latest_claude_version("unknown", "latest") is None
    assert seen == []


def test_claude_method_from_realpath_when_install_method_missing(tmp_path, monkeypatch):
    _claude_files({}, {})
    monkeypatch.setenv("HOME", str(tmp_path))
    native = str(tmp_path / ".local" / "share" / "claude" / "versions" / "2.1.281")
    assert claude_channel_info(native)[0] == "native"
    assert claude_channel_info("/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js")[0] == "npm"
    assert claude_channel_info("/opt/elsewhere/claude")[0] == "unknown"


def test_claude_native_body_must_be_a_bare_version(monkeypatch):
    _fake_get(monkeypatch, b"<html>maintenance</html>")
    assert latest_claude_version("native", "latest") is None
    _fake_get(monkeypatch, None)
    assert latest_claude_version("native", "latest") is None


def test_current_claude_reads_version_fresh(monkeypatch):
    from aipager import claude_resolve

    install = claude_resolve.ClaudeInstall(path="/abs/claude", realpath="/abs/claude",
                                           version="2.1.270")
    monkeypatch.setattr(claude_resolve, "try_resolve_claude_binary",
                        lambda: claude_resolve.ResolvedClaude(chosen=install))
    seen: list = []

    def _run(argv, *, timeout, env=None, capture=True):
        seen.append(argv)
        return self_update.CommandResult(0, "2.1.281 (Claude Code)", False, None)
    monkeypatch.setattr(self_update, "_run_command", _run)
    assert self_update.current_claude() == ("/abs/claude", "2.1.281")
    assert seen == [["/abs/claude", "--version"]]
