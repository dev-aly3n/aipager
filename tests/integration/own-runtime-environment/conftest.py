"""Package-wide fixtures for the "aipager owns its runtime environment"
black-box integration tests.

Why this package needs its own layer on top of ``tests/conftest.py``'s
guards, not just a reuse of them:

1. ``tests/conftest.py``'s ``_no_real_claude_candidates`` autouse fixture
   deliberately makes ``claude_resolve._candidate_paths()`` return ``[]``
   for every test in the suite by default -- exactly what design.md's
   own criterion 19 asks us to prove is load-bearing. A handful of tests
   here need REAL candidate discovery (against fixture binaries, never a
   real installed claude) to exercise precedence/realpath-dedup, so this
   file captures a reference to the ORIGINAL ``_candidate_paths`` at
   **module import time** -- i.e. at test-collection time, strictly
   before any per-test fixture (autouse or not) has run and before
   anything has monkeypatched it. A test that wants real discovery
   restores this saved reference itself, exactly as
   ``tests/conftest.py``'s own docstring says to.

2. ``aipager.dtach.inject._credentials_file_is_fresh`` /
   ``_stash_expired_credentials_file`` compute ``Path.home() /
   ".claude" / ".credentials.json"`` FRESH on every call (not a cached
   module constant), so they are NOT covered by
   ``tests/conftest.py``'s ``_isolate_home_paths`` dict (which only
   patches already-bound module attributes). ``_stash_expired_
   credentials_file`` *renames* that file when it looks expired. Any
   test that reaches ``launch_session()`` without neutralizing these two
   functions risks reading -- and, on an unlucky machine state, RENAMING
   -- the operator's real ``~/.claude/.credentials.json``. Neutralized
   here, autouse, for every test in this package.

3. ``aipager.service._install_linux()`` shells out to REAL
   ``systemctl --user daemon-reload`` / ``enable --now aipager.service``
   / ``loginctl show-user`` via ``aipager.service._run``. The live
   daemon on this machine runs as a systemd user unit named exactly
   ``aipager.service`` (a hand-written unit the orchestrator installed).
   A test that calls ``_install_linux()`` without neutralizing ``_run``
   would enable/restart the REAL unit mid-pipeline -- exactly what the
   task brief says never to do. Neutralized here, autouse, for every
   test in this package; a test asserting something about the systemctl
   calls themselves inspects ``fake_service_run.calls`` instead of
   letting the real command through.

4. ``aipager.service._install_linux()`` also calls
   ``_post_install_probe()``, which sleeps ~2s then calls
   ``aipager.doctor.check_daemon()`` -- which probes
   ``aipager.config.SOCKET_PATH``. That constant is computed ONCE at
   import time and is NOT in ``_isolate_home_paths``'s redirect dict, so
   its default value is whatever this real machine resolves (frequently
   ``/tmp/aipager.sock`` -- the REAL live daemon's control socket).
   Redirected here, autouse, for every test in this package, so no test
   can ever probe the live daemon's socket even indirectly.
"""
from __future__ import annotations

import json
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import aipager.claude_resolve as claude_resolve_mod

# Captured at collection time -- see module docstring point 1. Tests that
# want real candidate discovery pass this back to
# ``monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", ...)``
# themselves, inside the test body, so the restore always outlives the
# session-default stub applied by ``tests/conftest.py``'s autouse fixture.
REAL_CANDIDATE_PATHS = claude_resolve_mod._candidate_paths


@pytest.fixture
def real_candidate_paths():
    """The un-stubbed ``claude_resolve._candidate_paths``, for tests that
    need genuine discovery against fixture binaries."""
    return REAL_CANDIDATE_PATHS


@pytest.fixture(autouse=True)
def _never_touch_real_claude_credentials_file(monkeypatch):
    """See module docstring point 2. Every ``launch_session()`` call in
    this package goes through these no-ops instead of touching
    ``~/.claude/.credentials.json`` on the real machine."""
    monkeypatch.setattr(
        "aipager.dtach.inject._credentials_file_is_fresh", lambda: False)
    monkeypatch.setattr(
        "aipager.dtach.inject._stash_expired_credentials_file", lambda: None)


@pytest.fixture(autouse=True)
def _redirect_socket_path(tmp_path, monkeypatch):
    """See module docstring point 4."""
    monkeypatch.setattr(
        "aipager.config.SOCKET_PATH", str(tmp_path / "aipager-test.sock"))


@dataclass
class FakeServiceRun:
    """Records every ``aipager.service._run`` call and answers every
    caller (``_systemd_user_available``, ``daemon-reload``,
    ``enable --now``, ``_check_linger``) with a harmless success --
    without this, ``_install_linux()`` would shell out to the real
    ``systemctl``/``loginctl`` binaries on this machine. See module
    docstring point 3.
    """

    calls: list = field(default_factory=list)

    def __call__(self, cmd, *, capture=True, check=False):
        self.calls.append(list(cmd))
        if cmd[:3] == ["systemctl", "--user", "is-system-running"]:
            return 0, "running\n", ""
        if cmd[:2] == ["systemctl", "--user"]:
            return 0, "", ""
        if cmd[:1] == ["loginctl"]:
            return 0, "Linger=yes\n", ""
        return 0, "", ""


@pytest.fixture(autouse=True)
def _no_login_shell_token_discovery_by_default(monkeypatch):
    """``ensure_daemon_env()`` falls back to
    ``service._discover_token_via_login_shell()`` when no legacy token
    is found. ``tests/conftest.py``'s own autouse guard makes that raise
    loudly by default (see its docstring) rather than spawn a real login
    shell -- correct, but it means every test in this package that
    reaches ``_install_linux()``/``ensure_daemon_env()`` for a reason
    OTHER than exercising token discovery itself needs a default stub,
    or it fails on an assertion unrelated to what the test is actually
    about. Tests for the discovery path itself override this locally.
    """
    monkeypatch.setattr(
        "aipager.service._discover_token_via_login_shell", lambda: None)


@pytest.fixture(autouse=True)
def _stub_resolve_aipager_bin(monkeypatch):
    """``_install_linux()`` renders ExecStart from
    ``service._resolve_aipager_bin()``, which is ``shutil.which("aipager")``
    and raises ``FileNotFoundError`` when aipager is not on ``$PATH``.

    That made every install test in this package silently depend on the
    machine running the suite having aipager *installed* -- they passed
    on the developer box and would fail on CI, in a fresh checkout, or
    (as actually happened) the moment someone uninstalled aipager to
    test the from-scratch install flow. No test here asserts on the
    resolved value, only on the unit's structure, so a fixed stand-in
    removes the host dependency without weakening anything. A test that
    cares about resolution overrides this locally.
    """
    monkeypatch.setattr(
        "aipager.service._resolve_aipager_bin", lambda: "/usr/bin/aipager")


@pytest.fixture(autouse=True)
def fake_service_run(monkeypatch):
    """See module docstring point 3. Returns the recorder so a test that
    cares about exactly which systemctl invocations happened can assert
    on ``fake_service_run.calls`` -- never a real subprocess."""
    fake = FakeServiceRun()
    monkeypatch.setattr("aipager.service._run", fake)
    return fake


def write_claude_fixture(
    path: Path,
    *,
    version: str = "2.1.235 (Claude Code)",
    version_exit: int = 0,
    logged_in: bool = True,
    auth_method: str = "oauth_token",
    auth_exit: int | None = None,
    auth_provider: str = "firstParty",
    auth_sleep_seconds: float | None = None,
    auth_nonjson: bool = False,
    invoked_marker: Path | None = None,
) -> str:
    """Write an executable stand-in for ``claude`` per entrypoints.md's
    "Fixture-binary contract": answers ``--version`` and ``auth
    status`` deterministically, never touches the network or a real
    claude installation.

    ``invoked_marker``: when the script is invoked with any argv other
    than exactly ``--version`` or ``auth status`` (i.e. the shape a real
    session launch uses), it touches this path -- lets a test prove the
    resolved binary really was the one that would have been exec'd,
    without ever letting a real shell/session actually run it.
    """
    if auth_exit is None:
        auth_exit = 0 if logged_in else 1
    auth_payload = json.dumps({
        "loggedIn": logged_in,
        "authMethod": auth_method,
        "apiProvider": auth_provider,
    })

    body_lines = [
        # An absolute interpreter path (not `#!/usr/bin/env python3`):
        # several tests here deliberately overwrite `$PATH` down to just
        # the fixture directories under test, to keep tier-4 ($PATH)
        # discovery fully deterministic regardless of what's actually
        # installed on the machine running the suite (see
        # `test_realpath_dedup_and_precedence.py`'s discovery, made the
        # hard way: a `/usr/bin/env python3` shebang under a `$PATH`
        # that no longer contains `/usr/bin` makes the kernel itself
        # fail the exec with "python3: not found", which
        # `claude_resolve._verify_candidate` faithfully (and correctly)
        # reports as "--version exited 127" -- indistinguishable from a
        # broken candidate. An absolute shebang sidesteps the kernel's
        # own PATH lookup entirely.
        f"#!{sys.executable}",
        "import sys",
        "from pathlib import Path",
        "args = sys.argv[1:]",
        'if args == ["--version"]:',
        f"    sys.stdout.write({version!r} + chr(10))",
        f"    sys.exit({version_exit})",
        'if args[:2] == ["auth", "status"]:',
    ]
    if auth_sleep_seconds is not None:
        body_lines.append("    import time")
        body_lines.append(f"    time.sleep({auth_sleep_seconds!r})")
    if auth_nonjson:
        body_lines.append("    sys.stdout.write('not-json-at-all')")
    else:
        body_lines.append(f"    sys.stdout.write({auth_payload!r})")
    body_lines.append(f"    sys.exit({auth_exit})")
    if invoked_marker is not None:
        body_lines.append(f"Path({str(invoked_marker)!r}).write_text('invoked')")
    body_lines.append("sys.exit(0)")
    body_lines.append("")

    script = "\n".join(body_lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def claude_fixture_factory(tmp_path):
    """Returns a callable ``(name=..., **kwargs) -> str`` writing a
    fresh fixture claude binary under ``tmp_path`` and returning its
    absolute path."""
    counter = {"n": 0}

    def _make(name: str | None = None, **kwargs) -> str:
        counter["n"] += 1
        if name is None:
            name = f"claude-fixture-{counter['n']}"
        return write_claude_fixture(tmp_path / "bin" / name / "claude", **kwargs)

    return _make


@pytest.fixture
def isolate_from_real_local_bin_claude(tmp_path, monkeypatch):
    """Redirect ``Path.home()`` so tier 3 (``~/.local/bin/claude``) never
    resolves to this box's REAL, working claude install.

    Tier 3 is a fixed, home-relative path that ``_candidate_paths()``
    cannot be steered away from by env var or config override, so any
    test that restores real discovery (``real_candidate_paths``) and
    does not use this fixture will silently pick up whatever the
    machine actually running the suite happens to have installed at
    ``~/.local/bin/claude`` -- verified empirically while writing this
    package: without this redirect, precedence/dedup tests using
    ``$PATH``-only fixtures were shadowed outright by this tester's own
    real install, and a real ``--version`` subprocess ran against it.
    """
    fake_home = tmp_path / "fake_home_for_local_bin_tier"
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: fake_home))
    return fake_home


@pytest.fixture(autouse=True)
def _clean_env_precedence(monkeypatch):
    """Every precedence-chain env var starts unset for every test in
    this package, so a test only sees the tiers it explicitly sets --
    never whatever happens to be exported in the process actually
    running the suite."""
    for var in ("AIPAGER_CLAUDE_BIN", "AIPAGER_SOCKET_PATH",
                "CREDENTIALS_DIRECTORY", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
