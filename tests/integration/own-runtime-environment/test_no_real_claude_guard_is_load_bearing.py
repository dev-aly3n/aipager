"""design.md success criterion 19:

    "Every non-e2e test reaching resolve_claude_binary() without
    explicit mocking gets no candidates, never a real installed binary
    -- verified by temporarily reverting the autouse fixture and
    confirming a NAMED test starts spawning a real subprocess."

The task brief calls this the single most likely criterion to
fake-pass, and demands the test distinguish "no candidates because the
guard suppresses discovery" from "no candidates because discovery is
fundamentally broken". Both halves are proven with the SAME fixture
binary present at the SAME location on $PATH:

- test_guard_suppresses_a_real_discoverable_candidate: with the
  session's default (tests/conftest.py's autouse
  `_no_real_claude_candidates`) fixture in effect, resolution reports
  NO candidates even though one genuinely exists on $PATH.
- test_discovery_genuinely_works_once_the_guard_is_lifted: the SAME
  fixture, the SAME $PATH, with only `_candidate_paths` restored (the
  documented opt-out seam) -- resolution finds it. This is what rules
  out "the guard passes vacuously because discovery is broken anyway".
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import aipager.claude_resolve as claude_resolve_mod
from aipager.claude_resolve import ClaudeNotFoundError, resolve_claude_binary


def _put_on_path(monkeypatch, *dirs):
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))


def test_guard_suppresses_a_real_discoverable_candidate(
    tmp_path, monkeypatch, claude_fixture_factory, isolate_from_real_local_bin_claude,
):
    fixture_path = claude_fixture_factory(version="2.1.235 (Claude Code)")
    _put_on_path(monkeypatch, Path(fixture_path).parent)

    # Deliberately NOT restoring _candidate_paths here -- this is the
    # suite's session default, exactly what every other test in the
    # non-e2e suite gets implicitly.
    try:
        resolve_claude_binary(force=True)
        raised = False
    except ClaudeNotFoundError:
        raised = True

    assert raised, (
        "resolve_claude_binary() found a candidate even though the "
        "session-default guard (tests/conftest.py's "
        "_no_real_claude_candidates) should have made discovery return "
        "zero candidates -- the guard is not load-bearing"
    )


def test_discovery_genuinely_works_once_the_guard_is_lifted(
    tmp_path, monkeypatch, claude_fixture_factory, real_candidate_paths,
    isolate_from_real_local_bin_claude,
):
    fixture_path = claude_fixture_factory(version="2.1.235 (Claude Code)")
    _put_on_path(monkeypatch, Path(fixture_path).parent)

    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)

    resolved = resolve_claude_binary(force=True)

    assert resolved.chosen.path == fixture_path, (
        "with the guard opted out of (the documented, per-test seam), "
        "discovery must actually find the fixture -- if this fails, the "
        "previous test's ClaudeNotFoundError proves nothing about the "
        "guard being load-bearing, only that discovery is broken outright"
    )


def test_memo_does_not_leak_a_successful_resolution_across_tests(
    tmp_path, monkeypatch, claude_fixture_factory, real_candidate_paths,
    isolate_from_real_local_bin_claude,
):
    """tests/conftest.py's guard docstring specifically calls out
    resetting claude_resolve._memo before AND after every test -- without
    that, a test ordered after a real-discovery test (like the one above)
    would see ITS successful resolution memoized and returned to code
    that never mocked anything, defeating the guard for every test after
    the first that opts out. This reruns the exact "guard active,
    fixture present" scenario as the FIRST test above, asserting the memo
    genuinely starts clean.
    """
    fixture_path = claude_fixture_factory(version="2.1.235 (Claude Code)")
    _put_on_path(monkeypatch, Path(fixture_path).parent)

    assert claude_resolve_mod._memo is None
    assert claude_resolve_mod._memo_error is None

    with pytest.raises(ClaudeNotFoundError):
        resolve_claude_binary(force=False)  # memo-respecting path, not force=True
