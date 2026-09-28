"""No test may reach a live session's files under /tmp (incident 2026-09-28).

The card-lifecycle harness ran the ``/kill`` path for a session named like
the operator's own, and the real ``/tmp/claude-policy-<session>.json`` was
deleted: that session's next self-woken turn was held to the restrictive
floor and its Bash blocked. ``tests/conftest.py`` now redirects every
per-session /tmp path for every test; these rows fail if it stops doing so.

These rows are the ones that run when a redirect has gone missing, so they
must stay harmless then too: the session name cannot belong to a live
session (a ``.`` is outside the launcher's ``[A-Za-z0-9_-]`` names), and
every path is checked to sit inside ``tmp_path`` BEFORE anything is
written or removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aipager import config, policy_snapshot
from aipager.bot import handlers
from aipager.dtach import enforce
from aipager.dtach.launcher import _NAME_RE

NEVER_LIVE = "claude-isolation.guard"


def _under(path, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


def _require_under(root: Path, *paths) -> None:
    """Fail before any write or removal if a path escaped ``root``."""
    escaped = [str(p) for p in paths if not _under(p, root)]
    if escaped:
        pytest.fail(f"not redirected into tmp_path: {escaped}")


def test_the_guard_name_can_never_be_a_live_session():
    assert not _NAME_RE.fullmatch(NEVER_LIVE.removeprefix("claude-"))


def test_policy_paths_resolve_inside_this_tests_tmp_path(tmp_path):
    assert _under(policy_snapshot.snapshot_path(NEVER_LIVE), tmp_path)
    assert _under(policy_snapshot.reply_context_path(NEVER_LIVE), tmp_path)
    assert _under(policy_snapshot.notes_dir(NEVER_LIVE), tmp_path)


def test_import_time_bindings_are_redirected_too(tmp_path):
    """Modules that bound a path at import time read their own name, not
    the module attribute: each binding is redirected as well."""
    assert _under(enforce.reply_context_path(NEVER_LIVE), tmp_path)
    assert _under(config.FILE_DOWNLOAD_DIR, tmp_path)
    assert _under(handlers.FILE_DOWNLOAD_DIR, tmp_path)


def test_the_kill_path_removes_only_the_redirected_files(tmp_path):
    """``clear_session_files`` (run by /kill and by a session going GONE)
    removes the redirected files and nothing else."""
    snap = policy_snapshot.snapshot_path(NEVER_LIVE)
    reply = policy_snapshot.reply_context_path(NEVER_LIVE)
    notes = policy_snapshot.notes_dir(NEVER_LIVE)
    _require_under(tmp_path, snap, reply, notes)
    for p in (snap, reply):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    notes.mkdir(parents=True, exist_ok=True)

    policy_snapshot.clear_session_files(NEVER_LIVE)

    assert not snap.exists()
    assert not reply.exists()
    assert not notes.exists()


def test_the_safety_hook_reads_the_redirected_snapshot(tmp_path):
    """``enforce`` reads the snapshot through ``read_snapshot``, which
    resolves ``snapshot_path`` at call time: it sees the redirected file."""
    snap = policy_snapshot.snapshot_path(NEVER_LIVE)
    _require_under(tmp_path, snap)
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text('{"bypass_safety": true, "marker": "redirected"}')
    assert policy_snapshot.read_snapshot(NEVER_LIVE)["marker"] == "redirected"
