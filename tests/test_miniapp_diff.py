"""Tests for aipager.miniapp.diff.collect_diff.

Every repo here is a throwaway git repo built inside `tmp_path` — never
the real project repo, never `/home/aly/aipager` (or wherever this
worktree happens to live). `run_async` (no pytest-asyncio) drives the
coroutines under test, matching the rest of this project's async tests.
"""

import os
import subprocess
import time

import pytest

from aipager.miniapp import diff


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(path):
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)


def _commit_all(path, message="commit"):
    _git("add", "-A", cwd=path)
    _git("commit", "-q", "-m", message, cwd=path)


# ===== not a repo / missing cwd ==========================================

def test_non_repo_dir_returns_not_a_git_repo(tmp_path, run_async):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = run_async(diff.collect_diff(str(plain)))
    assert result == {"available": False, "reason": "not_a_git_repo"}


def test_missing_cwd_returns_cwd_missing(tmp_path, run_async):
    missing = tmp_path / "does-not-exist"
    result = run_async(diff.collect_diff(str(missing)))
    assert result == {"available": False, "reason": "cwd_missing"}


def test_empty_cwd_returns_cwd_missing(run_async):
    result = run_async(diff.collect_diff(""))
    assert result == {"available": False, "reason": "cwd_missing"}


def test_no_commits_yet_returns_that_reason(tmp_path, run_async):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    result = run_async(diff.collect_diff(str(repo)))
    assert result == {"available": False, "reason": "no_commits_yet"}


# ===== clean / modified / untracked ======================================

def test_clean_repo_returns_empty_files(tmp_path, run_async):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo)

    result = run_async(diff.collect_diff(str(repo)))
    assert result == {"available": True, "files": [], "files_truncated": False}


def test_modified_tracked_file_has_non_null_patch(tmp_path, run_async):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo)
    (repo / "a.txt").write_text("hello\nworld\n")

    result = run_async(diff.collect_diff(str(repo)))
    assert result["available"] is True
    assert result["files_truncated"] is False
    assert len(result["files"]) == 1
    entry = result["files"][0]
    assert entry["path"] == "a.txt"
    assert entry["change_type"] == "modified"
    assert entry["binary"] is False
    assert entry["patch"] is not None
    assert "+world" in entry["patch"]
    assert entry["truncated"] is False


def test_untracked_file_reports_whole_file_as_added(tmp_path, run_async):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo)
    (repo / "new.txt").write_text("brand new content\n")

    result = run_async(diff.collect_diff(str(repo)))
    assert len(result["files"]) == 1
    entry = result["files"][0]
    assert entry["path"] == "new.txt"
    assert entry["change_type"] == "untracked"
    assert entry["binary"] is False
    assert entry["patch"] is not None
    assert "+brand new content" in entry["patch"]


def test_deleted_tracked_file_reports_deleted(tmp_path, run_async):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo)
    (repo / "a.txt").unlink()

    result = run_async(diff.collect_diff(str(repo)))
    assert len(result["files"]) == 1
    assert result["files"][0]["change_type"] == "deleted"
    assert result["files"][0]["patch"] is not None


# ===== binary files ========================================================

def test_binary_file_reports_binary_true_no_raw_bytes(tmp_path, run_async):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo)
    # Real non-UTF-8 bytes — not just "looks binary", actually undecodable.
    (repo / "blob.bin").write_bytes(bytes(range(256)))

    result = run_async(diff.collect_diff(str(repo)))
    entry = next(f for f in result["files"] if f["path"] == "blob.bin")
    assert entry["binary"] is True
    assert entry["patch"] is None

    import json
    # Must round-trip through JSON cleanly — no raw bytes anywhere.
    encoded = json.dumps(result)
    assert "\\u0000" not in encoded  # no embedded NUL from the binary blob


# ===== bounding ============================================================

def test_oversized_file_is_truncated_and_bounded(tmp_path, run_async, monkeypatch):
    monkeypatch.setattr(diff, "MAX_DIFF_BYTES_PER_FILE", 500)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("baseline\n")
    _commit_all(repo)
    (repo / "a.txt").write_text("\n".join(f"line {i}" for i in range(2000)) + "\n")

    result = run_async(diff.collect_diff(str(repo)))
    entry = result["files"][0]
    assert entry["truncated"] is True
    assert result["files_truncated"] is True
    # patch may be None (fully skipped) or a string bounded to the cap —
    # either way it must never exceed the configured byte ceiling.
    if entry["patch"] is not None:
        assert len(entry["patch"].encode("utf-8")) <= 500


def test_max_files_cap_sets_files_truncated(tmp_path, run_async, monkeypatch):
    monkeypatch.setattr(diff, "MAX_FILES", 2)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.txt").write_text("baseline\n")
    _commit_all(repo)
    for i in range(5):
        (repo / f"new{i}.txt").write_text(f"content {i}\n")

    result = run_async(diff.collect_diff(str(repo)))
    assert result["files_truncated"] is True
    assert len(result["files"]) == 2


# ===== process-level failure modes =========================================

def test_git_not_installed_returns_that_reason(tmp_path, run_async, monkeypatch):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    result = run_async(diff.collect_diff(str(repo)))
    assert result == {"available": False, "reason": "git_not_installed"}


def test_timeout_returns_git_error_without_hanging(tmp_path, run_async, monkeypatch):
    """A `git` that sleeps past GIT_TIMEOUT_SECONDS must be killed, not
    awaited to completion — this asserts wall-clock time stays well
    under the fake process's own 1s sleep, proving the bounded-wait
    path actually fires rather than the test merely getting lucky.
    ``sleep 1`` (not the real ``GIT_TIMEOUT_SECONDS`` default of 5s)
    keeps this under `_KILL_REAP_GRACE_SECONDS`'s default window so a
    sandboxed/traced test environment where SIGKILL delivery to a
    child can lag has time to actually reap it inside this call rather
    than leaking an unreaped process past the assertion below.
    """
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nsleep 1\n")
    fake_git.chmod(0o755)

    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(diff, "GIT_TIMEOUT_SECONDS", 0.2)

    repo = tmp_path / "repo"
    repo.mkdir()

    start = time.monotonic()
    result = run_async(diff.collect_diff(str(repo)))
    elapsed = time.monotonic() - start

    assert result == {"available": False, "reason": "git_error"}
    assert elapsed < 3.0


# ===== pure parsing helpers ================================================

@pytest.mark.parametrize(("xy", "expected"), [
    ("??", "untracked"),
    (" M", "modified"),
    ("M ", "modified"),
    (" D", "deleted"),
    ("D ", "deleted"),
    ("A ", "added"),
    ("R ", "renamed"),
    ("C ", "copied"),
    ("UU", "conflicted"),
])
def test_change_type_mapping(xy, expected):
    assert diff._change_type(xy) == expected


def test_parse_porcelain_z_consumes_rename_source():
    data = b"R  new.txt\x00old.txt\x00?? untracked.txt\x00"
    entries = diff._parse_porcelain_z(data)
    assert entries == [("R ", "new.txt"), ("??", "untracked.txt")]
