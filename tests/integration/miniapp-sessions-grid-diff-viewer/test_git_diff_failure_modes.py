"""Black-box adversarial tests for `GET /api/sessions/{label}/diff`'s
git failure modes, driven end-to-end over real HTTP against a real
bound loopback server -- never by importing `aipager.miniapp.diff`
directly (that module, and its `collect_diff`/`_run_git_bounded`, are
on entrypoints.md's "NOT exported" list).

Every git repo here is a real, throwaway repo built fresh inside
`tmp_path` -- never the real project repo, never
`/home/aly/aipager` / this worktree.

Same disclosed methodology deviation as the other files in this
directory: `MiniAppServer` is imported only to obtain a real bound
socket that serves the documented route contract; every assertion is
against the wire-level HTTP response.

Gaps this file closes relative to the developer's
tests/test_miniapp_diff.py (which already covers all of these at the
`collect_diff()` function-call level, and
tests/test_miniapp_server.py, which covers the route being wired to
`collect_diff` via monkeypatch): the SAME failure modes, but proven at
the HTTP boundary -- through real auth, real routing, real JSON
serialization -- with response size actually measured in bytes rather
than assumed, and using file sizes/counts large enough to trigger
truncation without depending on (or asserting) any specific internal
byte-cap constant.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import aiohttp
import pytest

from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
_CLIENT_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sign(fields: dict, bot_token: str) -> str:
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(fields.items()) if k != "hash"
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id: int, *, bot_token: str = BOT_TOKEN) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


@pytest.fixture(autouse=True)
def _bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


@asynccontextmanager
async def _running(server):
    await server.start()
    try:
        yield
    finally:
        await server.stop()


async def _get(base_url, path, headers=None):
    async with aiohttp.ClientSession(timeout=_CLIENT_TIMEOUT) as session:
        async with session.get(f"{base_url}{path}", headers=headers) as resp:
            body = await resp.read()
            return resp.status, body


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(path):
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)


def _commit_all(path, message="commit"):
    _git("add", "-A", cwd=path)
    _git("commit", "-q", "-m", message, cwd=path)


def _server_for_cwd(mk_bot, registry, cwd):
    scope = Scope(chat_id=-100, kind="group", label="team",
                   members=(Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "bot"
    sess = registry.get_or_create("claude-dev")
    sess.label = "dev"
    sess.scope_chat_id = -100
    sess.status = Status.IDLE
    sess.cwd = str(cwd)
    port = _free_port()
    return MiniAppServer(bot, registry, port=port), f"http://127.0.0.1:{port}"


def _fetch_diff(server, base_url, run_async):
    good = _init_data(555)

    async def _run():
        async with _running(server):
            return await _get(
                base_url, "/api/sessions/dev/diff",
                {"X-Telegram-Init-Data": good},
            )
    return run_async(_run())


# --------------------------------------------------------------------- #
# not a repo / missing cwd / no commits yet -- through real HTTP        #
# --------------------------------------------------------------------- #

def test_non_git_dir_returns_not_a_git_repo(mk_bot, run_async, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    server, base_url = _server_for_cwd(mk_bot, SessionRegistry(), plain)

    status, body = _fetch_diff(server, base_url, run_async)
    assert status == 200
    assert json.loads(body) == {"available": False, "reason": "not_a_git_repo"}


def test_missing_cwd_returns_cwd_missing(mk_bot, run_async, tmp_path):
    missing = tmp_path / "does-not-exist-at-all"
    server, base_url = _server_for_cwd(mk_bot, SessionRegistry(), missing)

    status, body = _fetch_diff(server, base_url, run_async)
    assert status == 200
    assert json.loads(body) == {"available": False, "reason": "cwd_missing"}


def test_no_commits_yet_returns_that_reason(mk_bot, run_async, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    server, base_url = _server_for_cwd(mk_bot, SessionRegistry(), repo)

    status, body = _fetch_diff(server, base_url, run_async)
    assert status == 200
    assert json.loads(body) == {"available": False, "reason": "no_commits_yet"}


def test_none_of_the_failure_reasons_ever_500_or_hang(mk_bot, run_async, tmp_path):
    """Sweep all three no-repo-ish scenarios and confirm none takes
    anywhere near GIT_TIMEOUT_SECONDS-scale wall time (git should fail
    fast, before even spawning a diff)."""
    scenarios = {
        "not_a_git_repo": tmp_path / "plain2",
        "cwd_missing": tmp_path / "gone2",
        "no_commits_yet": tmp_path / "repo2",
    }
    scenarios["not_a_git_repo"].mkdir()
    scenarios["no_commits_yet"].mkdir()
    _init_repo(scenarios["no_commits_yet"])

    for reason, cwd in scenarios.items():
        server, base_url = _server_for_cwd(mk_bot, SessionRegistry(), cwd)
        start = time.monotonic()
        status, body = _fetch_diff(server, base_url, run_async)
        elapsed = time.monotonic() - start
        assert status == 200, f"{reason}: got {status}"
        assert json.loads(body)["reason"] == reason
        assert elapsed < 3.0, f"{reason} took {elapsed:.2f}s -- looks like a hang"


# --------------------------------------------------------------------- #
# binary file: never raw bytes, always binary:true/patch:null           #
# --------------------------------------------------------------------- #

def test_binary_file_never_carries_raw_bytes_over_http(mk_bot, run_async, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo)
    (repo / "photo.bin").write_bytes(bytes(range(256)) * 4)

    server, base_url = _server_for_cwd(mk_bot, SessionRegistry(), repo)
    status, body = _fetch_diff(server, base_url, run_async)
    assert status == 200

    # The raw HTTP body itself must be valid UTF-8 JSON -- if raw binary
    # leaked in, this decode would fail outright.
    payload = json.loads(body.decode("utf-8"))
    assert payload["available"] is True
    entry = next(f for f in payload["files"] if f["path"] == "photo.bin")
    assert entry["binary"] is True
    assert entry["patch"] is None
    # No stray NUL / control-byte escape sequences anywhere in the wire body.
    assert b"\x00" not in body


# --------------------------------------------------------------------- #
# huge diff: bounded response, truncation flags set, no hang            #
# --------------------------------------------------------------------- #

def test_huge_single_file_diff_is_bounded_and_flagged_truncated(mk_bot, run_async, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "big.txt").write_text("baseline\n")
    _commit_all(repo)
    # ~3MB of content, every line unique so git can't collapse the diff --
    # deliberately larger than any reasonable "few MB" response bound,
    # without hardcoding the implementation's specific byte-cap constant.
    lines = [f"line {i} {'x' * 40}" for i in range(60_000)]
    (repo / "big.txt").write_text("\n".join(lines) + "\n")

    server, base_url = _server_for_cwd(mk_bot, SessionRegistry(), repo)
    start = time.monotonic()
    status, body = _fetch_diff(server, base_url, run_async)
    elapsed = time.monotonic() - start

    assert status == 200
    assert elapsed < 10.0, f"huge diff took {elapsed:.2f}s -- looks like a hang"
    body_len = len(body)
    assert body_len < 3_000_000, (
        f"diff response body was {body_len} bytes -- not bounded well "
        f"under a few MB as design.md's success criteria requires"
    )
    payload = json.loads(body)
    assert payload["available"] is True
    entry = next(f for f in payload["files"] if f["path"] == "big.txt")
    # Either the per-file patch was truncated, or the file itself was
    # dropped from a truncated file list -- either way the raw multi-MB
    # patch must never appear whole.
    assert entry["truncated"] is True or payload["files_truncated"] is True


def test_many_changed_files_produces_bounded_response_and_files_truncated(
    mk_bot, run_async, tmp_path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "base.txt").write_text("baseline\n")
    _commit_all(repo)
    for i in range(400):
        (repo / f"new_{i}.txt").write_text(f"content {i}\n")

    server, base_url = _server_for_cwd(mk_bot, SessionRegistry(), repo)
    start = time.monotonic()
    status, body = _fetch_diff(server, base_url, run_async)
    elapsed = time.monotonic() - start

    assert status == 200
    assert elapsed < 10.0, f"400-file diff took {elapsed:.2f}s -- looks like a hang"
    payload = json.loads(body)
    assert payload["available"] is True
    assert payload["files_truncated"] is True
    assert len(payload["files"]) < 400, (
        "all 400 changed files were returned -- MAX_FILES-style cap "
        "does not appear to be enforced"
    )
    assert len(body) < 3_000_000


# --------------------------------------------------------------------- #
# git missing from PATH -- at the HTTP boundary                         #
# --------------------------------------------------------------------- #

def test_git_not_installed_returns_that_reason_over_http(mk_bot, run_async, tmp_path, monkeypatch):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    server, base_url = _server_for_cwd(mk_bot, SessionRegistry(), repo)
    status, body = _fetch_diff(server, base_url, run_async)
    assert status == 200
    assert json.loads(body) == {"available": False, "reason": "git_not_installed"}


# --------------------------------------------------------------------- #
# concurrent diff requests don't corrupt or cross-wire responses        #
# --------------------------------------------------------------------- #

def test_concurrent_diff_requests_for_different_sessions_stay_isolated(
    mk_bot, run_async, tmp_path,
):
    """Two sessions, two different real repos, fired concurrently --
    each response must reflect its OWN repo's changes, never the other's."""
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                   members=(Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "bot"

    repo1 = tmp_path / "repo1"
    repo1.mkdir()
    _init_repo(repo1)
    (repo1 / "f.txt").write_text("a\n")
    _commit_all(repo1)
    (repo1 / "only_in_repo1.txt").write_text("unique1\n")

    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    _init_repo(repo2)
    (repo2 / "f.txt").write_text("b\n")
    _commit_all(repo2)
    (repo2 / "only_in_repo2.txt").write_text("unique2\n")

    s1 = registry.get_or_create("claude-1")
    s1.label = "sess1"
    s1.scope_chat_id = -100
    s1.cwd = str(repo1)

    s2 = registry.get_or_create("claude-2")
    s2.label = "sess2"
    s2.scope_chat_id = -100
    s2.cwd = str(repo2)

    port = _free_port()
    server = MiniAppServer(bot, registry, port=port)
    base_url = f"http://127.0.0.1:{port}"
    good = _init_data(555)

    async def _run():
        import asyncio
        async with _running(server):
            r1, r2 = await asyncio.gather(
                _get(base_url, "/api/sessions/sess1/diff", {"X-Telegram-Init-Data": good}),
                _get(base_url, "/api/sessions/sess2/diff", {"X-Telegram-Init-Data": good}),
            )
            return r1, r2

    (status1, body1), (status2, body2) = run_async(_run())
    assert status1 == status2 == 200
    payload1 = json.loads(body1)
    payload2 = json.loads(body2)
    paths1 = {f["path"] for f in payload1["files"]}
    paths2 = {f["path"] for f in payload2["files"]}
    assert "only_in_repo1.txt" in paths1
    assert "only_in_repo1.txt" not in paths2
    assert "only_in_repo2.txt" in paths2
    assert "only_in_repo2.txt" not in paths1
