"""Fixtures for the opt-in E2E suite (``pytest tests/e2e -m e2e``).

Everything here is marked ``e2e`` (auto-applied) and excluded from the
default run by ``addopts = -m 'not e2e'`` in pyproject. Each part skips
cleanly when what it needs is missing:

- Real-Claude hook tests (``test_e2e_bash_denies``, ``_benign``,
  ``_origin``, ``_path_denies``, ``_roles``, ``_sticky``,
  ``_slash_command`` (8.74), ``_joined_turn`` (8.77), ``_home_folder``
  (8.79, 8.61), ``_policy_safety`` (8.96), ``_bash_search`` (8.98)): one real ``claude -p`` turn
  each in a temp project wired to the real installed ``aipager-hook``,
  with the policy snapshot written through the production pipeline
  (see ``harness``). Claude loads project settings only
  (``--setting-sources project,local``), so the operator's own hooks never
  rewrite the snapshot; the hook is the first ``aipager-hook`` on ``PATH``
  (else this venv's, or ``AIPAGER_E2E_HOOK``). Need ``claude`` installed and authenticated
  (``claude_available``); the fake-home ones also need Claude to
  authenticate with ``HOME`` elsewhere (``fake_home_claude``: export
  ``CLAUDE_CODE_OAUTH_TOKEN``). They post nothing to Telegram and never
  reach the running daemon. Runs read the session's tool list from
  Claude's ``init`` event (``--output-format stream-json``); a test that
  needs the Grep or Glob tool skips, naming the version, when the session
  does not offer it (``require_tools``, ``ClaudeRun.skip_unless_offered``).
- ``test_e2e_hook_direct``: the same scenarios with no Claude, piping
  ``PreToolUse`` payloads into the real hook. Needs only ``aipager-hook``.
- ``test_e2e_whoami``: ``/whoami`` from a real policy.yaml file, no Claude.
- ``test_e2e_daemon_halt``: a real dtach session halted on a safety
  block. Skips while a daemon runs.
- ``test_e2e_live_daemon`` and ``test_e2e_live_cli``: the RUNNING daemon,
  its Mini App and the installed CLI. Need ``AIPAGER_E2E_LIVE=1``; the
  daemon tests post cards and answers to the operator's DM (each module
  docstring lists what). The restart test also needs
  ``AIPAGER_E2E_RESTART=1``.

Run the whole suite memory-capped, with ``AIPAGER_E2E_LIVE=1``, under
``aipager.daemon_secrets.build_session_env()`` (so a nested ``claude`` is
logged in)::

    AIPAGER_E2E_LIVE=1 systemd-run --user --scope -q -p MemoryMax=2G \\
      -p MemorySwapMax=0 .venv/bin/python -c 'import sys, subprocess; \\
      from aipager.daemon_secrets import build_session_env; \\
      sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", \\
      "-p", "no:cacheprovider", "tests/e2e", "-m", "e2e", "-rA"], \\
      env=build_session_env()))'
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aipager import policy_snapshot
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
    if harness.claude_bin() is None or shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH")
    if harness.aipager_hook_bin() is None:
        pytest.skip("aipager-hook not installed")
    try:
        p = subprocess.run(
            ["claude", "-p", "reply with exactly: OK", "--max-turns", "1",
             "--setting-sources", "project,local"],
            capture_output=True, text=True, timeout=90,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        pytest.skip(f"claude probe failed: {e}")
    if p.returncode != 0:
        pytest.skip(f"claude probe non-zero (auth?): {p.stderr[:200]!r}")
    return True


@pytest.fixture
def _snapshots_where_the_hook_reads(_isolate_session_tmp_files, monkeypatch):
    """Undo tests/conftest.py's per-test redirect of the policy snapshot
    for e2e tests: the real ``aipager-hook`` (a subprocess) reads
    ``/tmp/claude-policy-<session>.json``, so the harness must write it
    there (see ``harness.PRODUCTION_SNAPSHOT_PATH``). Each e2e session
    name is unique and its file is removed by the ``session`` fixture."""
    path = harness.PRODUCTION_SNAPSHOT_PATH("claude-e2e-probe")
    assert path.parent == Path("/tmp") and path.name == "claude-policy-claude-e2e-probe.json", (
        f"harness captured a redirected snapshot_path: {path}")
    monkeypatch.setattr(policy_snapshot, "snapshot_path", harness.PRODUCTION_SNAPSHOT_PATH)


@pytest.fixture(scope="session")
def fake_home_claude(claude_available, tmp_path_factory) -> bool:
    """Skip unless real Claude also works with ``HOME`` pointed at a fake
    home (the fake-home tests need it: they never let a broken guard near
    the operator's real files)."""
    probe_home = tmp_path_factory.mktemp("probe-home")
    why = harness.probe_claude(probe_home, probe_home)
    if why:
        pytest.skip(why)
    return True


@pytest.fixture(scope="session")
def claude_tools(claude_available, tmp_path_factory):
    """The tools a Claude session offers here and the Claude Code
    version, read once from a probe's ``init`` event (``(None, "")`` when
    the probe printed none: tests then run, they never skip on a guess)."""
    return harness.probe_tools(tmp_path_factory.mktemp("tools-probe"))


@pytest.fixture
def require_tools(claude_tools):
    """``require_tools("Grep")`` skips the test before any Claude run when
    the session would not offer that tool (roadmap 8.98: Claude Code
    2.1.289 here has no Grep or Glob tool). Tests also call
    ``ClaudeRun.skip_unless_offered`` on their own run's tool list."""
    tools, version = claude_tools

    def _require(*names: str) -> None:
        harness.skip_unless_offered(tools, *names, version=version)
    return _require


@pytest.fixture
def hook_installed() -> str:
    hook = harness.aipager_hook_bin()
    if hook is None:
        pytest.skip("aipager-hook not installed")
    return hook


@pytest.fixture
def project(tmp_path):
    """A temp Claude project wiring the real aipager-hook (PreToolUse)."""
    return harness.make_project(tmp_path)


@pytest.fixture
def fake_home(tmp_path):
    """A fake home folder with canary copies of protected files
    (:func:`harness.make_fake_home`)."""
    return harness.make_fake_home(tmp_path)


@pytest.fixture
def session(_snapshots_where_the_hook_reads):
    """Unique session name whose snapshot the real hook reads, removed
    afterwards."""
    s = harness.new_session()
    yield s
    harness.clear_snapshot(s)
