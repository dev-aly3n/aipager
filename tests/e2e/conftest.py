"""Fixtures for the real-Claude E2E safety suite.

All tests here are marked ``e2e`` (auto-applied) and skipped unless real
Claude is available + authenticated. Excluded from the default `pytest`
run via the ``addopts = -m 'not e2e'`` in pyproject.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e import harness


def pytest_collection_modifyitems(config, items):
    """Auto-mark everything in tests/e2e as ``e2e`` so the suite is opt-in
    without each test needing the decorator."""
    for item in items:
        if "tests/e2e/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def claude_available() -> bool:
    """Skip the whole e2e suite unless `claude` is on PATH AND a one-shot
    probe succeeds (i.e. authenticated + reachable)."""
    if harness.claude_bin() is None:
        pytest.skip("claude CLI not on PATH")
    if harness.aipager_hook_bin() is None:
        pytest.skip("aipager-hook not installed")
    try:
        p = subprocess.run(
            ["claude", "-p", "reply with exactly: OK", "--max-turns", "1"],
            capture_output=True, text=True, timeout=90,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        pytest.skip(f"claude probe failed: {e}")
    if p.returncode != 0:
        pytest.skip(f"claude probe non-zero (auth?): {p.stderr[:200]!r}")
    return True


@pytest.fixture
def project(tmp_path):
    """A temp Claude project wiring the real aipager-hook (PreToolUse)."""
    return harness.make_project(tmp_path)


@pytest.fixture
def session(request):
    """Unique session name + automatic snapshot cleanup."""
    s = harness.new_session()
    yield s
    harness.clear_snapshot(s)
