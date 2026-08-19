"""Tests for aipager.claude_resolve — binary resolution + auth detection.

Fixture claude binaries follow entrypoints.md's fixture-binary contract:
an executable shell script that answers ``--version`` (and optionally
``auth status``) the same shape the real CLI does. Every test here
either supplies its own candidates via ``_candidate_paths`` /
``_verify_candidate`` or relies on the autouse
``_no_real_claude_candidates`` fixture (tests/conftest.py) — none ever
reaches a real installed `claude`.
"""

from __future__ import annotations

import os

import pytest

from aipager import claude_resolve
# Bound at import time, before the autouse `_no_real_claude_candidates`
# fixture (tests/conftest.py) patches `claude_resolve._candidate_paths`
# to a no-candidates stub for every OTHER test in the suite. The tests
# exercising the real implementation below call this name directly,
# bypassing the patched module attribute entirely.
from aipager.claude_resolve import _candidate_paths as _real_candidate_paths


def _write_fixture_claude(
    path, version: str = "2.1.235", *,
    auth_body: str | None = None, auth_exit: int = 0,
    version_exit: int = 0, version_output: str | None = None,
    sleep_seconds: float = 0,
):
    """Write an executable shell-script stand-in for `claude` at *path*.

    Answers ``--version`` and ``auth status`` per the fixture-binary
    contract in entrypoints.md.
    """
    if version_output is None:
        version_output = f"{version} (Claude Code)"
    auth_body = auth_body if auth_body is not None else (
        '{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}'
    )
    script = f"""#!/bin/sh
if [ "$1" = "--version" ]; then
  sleep {sleep_seconds}
  echo '{version_output}'
  exit {version_exit}
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  sleep {sleep_seconds}
  echo '{auth_body}'
  exit {auth_exit}
fi
exit 0
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def _reset_memo():
    # Belt-and-braces on top of conftest's own reset — every test in
    # this module deliberately drives resolution, so a clean memo at
    # both ends avoids any risk of leaking into a sibling test file.
    claude_resolve._memo = None
    claude_resolve._memo_error = None
    yield
    claude_resolve._memo = None
    claude_resolve._memo_error = None


# ---- resolve_claude_binary: not-found / basic success --------------------

def test_resolve_raises_when_nothing_verifies():
    with pytest.raises(claude_resolve.ClaudeNotFoundError):
        claude_resolve.resolve_claude_binary()


def test_try_resolve_returns_none_when_nothing_verifies():
    assert claude_resolve.try_resolve_claude_binary() is None


def test_resolve_succeeds_with_one_fixture(tmp_path, monkeypatch):
    fx = _write_fixture_claude(tmp_path / "claude")
    monkeypatch.setattr(claude_resolve, "_candidate_paths",
                        lambda: [(str(fx), 4)])
    resolved = claude_resolve.resolve_claude_binary()
    assert resolved.chosen.path == str(fx)
    assert resolved.chosen.version == "2.1.235"
    assert resolved.others == ()


def test_not_found_error_lists_every_candidate_and_why(tmp_path, monkeypatch):
    missing = tmp_path / "nope" / "claude"
    broken = _write_fixture_claude(tmp_path / "broken", version_exit=1)
    monkeypatch.setattr(
        claude_resolve, "_candidate_paths",
        lambda: [(str(missing), 3), (str(broken), 4)],
    )
    with pytest.raises(claude_resolve.ClaudeNotFoundError) as exc:
        claude_resolve.resolve_claude_binary()
    msg = str(exc.value)
    assert str(missing) in msg
    assert str(broken) in msg
    assert "exited 1" in msg


# ---- criterion 3: realpath-dedup on equal realpaths -----------------------

def test_symlink_duplicate_yields_one_chosen_zero_others(tmp_path, monkeypatch):
    """Reproduces /bin -> /usr/bin: two candidate PATHS, one real file."""
    real = _write_fixture_claude(tmp_path / "usr_bin_claude")
    link = tmp_path / "bin_claude"
    os.symlink(real, link)
    monkeypatch.setattr(
        claude_resolve, "_candidate_paths",
        lambda: [(str(link), 4), (str(real), 4)],
    )
    resolved = claude_resolve.resolve_claude_binary()
    assert resolved.others == ()
    # Highest precedence per realpath is "first discovered" at equal
    # tier — the symlink path, listed first, wins the literal `path`.
    assert resolved.chosen.path == str(link)
    assert resolved.chosen.realpath == os.path.realpath(str(real))


# ---- criterion 4: distinct realpaths + versions ---------------------------

def test_distinct_installs_higher_version_chosen_lower_in_others(tmp_path, monkeypatch):
    new = _write_fixture_claude(tmp_path / "new" / "claude", version="2.1.235")
    old = _write_fixture_claude(tmp_path / "old" / "claude", version="2.1.143")
    monkeypatch.setattr(
        claude_resolve, "_candidate_paths",
        lambda: [(str(old), 4), (str(new), 4)],
    )
    resolved = claude_resolve.resolve_claude_binary()
    assert resolved.chosen.path == str(new)
    assert resolved.chosen.version == "2.1.235"
    assert len(resolved.others) == 1
    assert resolved.others[0].path == str(old)
    assert resolved.others[0].version == "2.1.143"


def test_tier1_override_wins_outright_even_over_higher_version(tmp_path, monkeypatch):
    """claude_path (tier 1) beats a higher-version tier-4 PATH find —
    tiers 1-2 are absolute, never compared by version."""
    configured = _write_fixture_claude(tmp_path / "configured" / "claude", version="2.0.0")
    newer = _write_fixture_claude(tmp_path / "newer" / "claude", version="9.9.9")
    monkeypatch.setattr(
        claude_resolve, "_candidate_paths",
        lambda: [(str(configured), 1), (str(newer), 4)],
    )
    resolved = claude_resolve.resolve_claude_binary()
    assert resolved.chosen.path == str(configured)
    assert resolved.others[0].path == str(newer)


def test_stale_tier1_override_falls_through_with_warning(tmp_path, monkeypatch, caplog):
    working = _write_fixture_claude(tmp_path / "working" / "claude")
    stale = str(tmp_path / "does-not-exist" / "claude")
    monkeypatch.setattr(
        claude_resolve, "_candidate_paths",
        lambda: [(stale, 1), (str(working), 4)],
    )
    import logging
    with caplog.at_level(logging.WARNING):
        resolved = claude_resolve.resolve_claude_binary()
    assert resolved.chosen.path == str(working)
    assert any("claude_path" in r.message for r in caplog.records)


# ---- _candidate_paths (the REAL function, not a caller's stub) -----------
#
# Every test above bypasses _candidate_paths() entirely by monkeypatching
# it to a lambda. These exercise the real implementation end-to-end,
# specifically to pin the tier-1 (claude_path config) source: it must
# read `aipager.scope.CONFIG_PATH` fresh at call time, not the module's
# `path: Path = CONFIG_PATH` default bound once at scope.py's import —
# an earlier version of this call sat on that default and, under a test
# that repoints `_scope.CONFIG_PATH` (every test in this suite does, via
# conftest's autouse `_isolate_wizard_config`), silently read and wrote
# the OPERATOR'S REAL ~/.config/aipager/aipager.yaml instead of the
# test's tmp file.

def test_candidate_paths_tier1_reads_the_current_config_path_not_a_stale_default(
        tmp_path, monkeypatch):
    from aipager import scope as _scope

    cfg = tmp_path / "aipager.yaml"
    cfg.write_text("schema_version: 3\nbot_token: TOK\nscopes: []\n"
                   "claude_path: /configured/claude\n")
    monkeypatch.setattr(_scope, "CONFIG_PATH", cfg)

    candidates = _real_candidate_paths()
    assert ("/configured/claude", 1) in candidates


def test_candidate_paths_tier1_absent_when_config_has_no_claude_path(
        tmp_path, monkeypatch):
    from aipager import scope as _scope

    cfg = tmp_path / "aipager.yaml"
    cfg.write_text("schema_version: 3\nbot_token: TOK\nscopes: []\n")
    monkeypatch.setattr(_scope, "CONFIG_PATH", cfg)

    candidates = _real_candidate_paths()
    assert not any(tier == 1 for _path, tier in candidates)


def test_candidate_paths_never_touches_a_real_home_config(tmp_path, monkeypatch):
    """Belt-and-braces: even with NO CONFIG_PATH redirect at all in this
    specific test, _candidate_paths() must not blow up or reach outside
    tmp — the autouse `_isolate_wizard_config` fixture (conftest.py)
    already redirects `aipager.scope.CONFIG_PATH` for every test, so
    this just confirms _candidate_paths() actually honours that."""
    from aipager import scope as _scope
    # Whatever conftest already redirected CONFIG_PATH to — assert it's
    # NOT the real path, then call the real function.
    assert _scope.CONFIG_PATH != _scope.CONFIG_PATH.__class__(
        "~/.config/aipager/aipager.yaml"
    ).expanduser()
    _real_candidate_paths()  # must not raise


# ---- criteria 5 & 6: provenance formatting --------------------------------

def _install(path="/x/claude", version="2.1.235"):
    return claude_resolve.ClaudeInstall(path=path, realpath=path, version=version)


def test_format_provenance_single_install():
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    auth = claude_resolve.AuthStatus(logged_in=True, auth_method="oauth_token", source="env")
    lines = claude_resolve.format_provenance(resolved, auth)
    assert lines == ["claude: /x/claude (2.1.235) · auth: oauth_token (env)"]


def test_format_provenance_two_installs_one_also_found_line():
    resolved = claude_resolve.ResolvedClaude(
        chosen=_install("/x/claude", "2.1.235"),
        others=(_install("/y/claude", "2.1.143"),),
    )
    auth = claude_resolve.AuthStatus(logged_in=False, auth_method="none", source="unknown")
    lines = claude_resolve.format_provenance(resolved, auth)
    assert lines == [
        "claude: /x/claude (2.1.235) · auth: none (not logged in)",
        "also found: /y/claude (2.1.143) — set claude_path to override",
    ]


def test_format_provenance_probe_failed():
    resolved = claude_resolve.ResolvedClaude(chosen=_install())
    auth = claude_resolve.AuthStatus(False, "unknown", "probe-failed", error="timed out")
    lines = claude_resolve.format_provenance(resolved, auth)
    assert lines[0] == "claude: /x/claude (2.1.235) · auth: unknown (status probe failed: timed out)"


def test_format_provenance_version_gated():
    resolved = claude_resolve.ResolvedClaude(chosen=_install(version="2.1.0"))
    auth = claude_resolve.AuthStatus(
        False, "unknown", "version-gated",
        error="binary predates 2.1.41 — upgrade to check",
    )
    lines = claude_resolve.format_provenance(resolved, auth)
    assert lines[0] == (
        "claude: /x/claude (2.1.0) · "
        "auth: unknown (binary predates 2.1.41 — upgrade to check)"
    )


# ---- criterion 7: detect_auth matches every documented shape -------------

@pytest.mark.parametrize("logged_in,method,exit_code", [
    (False, "none", 1),
    (True, "oauth_token", 0),
    (True, "api_key", 0),
    (True, "third_party", 0),
])
def test_detect_auth_matches_documented_shapes(tmp_path, logged_in, method, exit_code):
    body = (
        f'{{"loggedIn": {"true" if logged_in else "false"}, '
        f'"authMethod": "{method}", "apiProvider": "firstParty"}}'
    )
    fx = _write_fixture_claude(
        tmp_path / "claude", auth_body=body, auth_exit=exit_code,
    )
    auth = claude_resolve.detect_auth(str(fx), "2.1.235", env={})
    assert auth.logged_in is logged_in
    assert auth.auth_method == method
    assert auth.error is None


# ---- criterion 8: version-gated below 2.1.41 ------------------------------

def test_detect_auth_skips_probe_below_min_version(tmp_path):
    # No fixture even needed — the version gate fires before any exec.
    auth = claude_resolve.detect_auth("/nonexistent/claude", "2.1.40", env={})
    assert auth.source == "version-gated"
    assert auth.auth_method == "unknown"
    assert auth.logged_in is False


def test_detect_auth_probes_at_exactly_min_version(tmp_path):
    fx = _write_fixture_claude(
        tmp_path / "claude",
        auth_body='{"loggedIn": true, "authMethod": "oauth_token", "apiProvider": "firstParty"}',
    )
    auth = claude_resolve.detect_auth(str(fx), "2.1.41", env={})
    assert auth.source != "version-gated"
    assert auth.logged_in is True


# ---- criterion 9: probe failure never reads as "none" ---------------------

def test_detect_auth_nonzero_non_json_is_unknown_not_none(tmp_path):
    fx = tmp_path / "claude"
    fx.write_text("#!/bin/sh\necho 'not json'\nexit 3\n")
    fx.chmod(0o755)
    auth = claude_resolve.detect_auth(str(fx), "2.1.235", env={})
    assert auth.auth_method == "unknown"
    assert auth.source == "probe-failed"
    assert auth.error is not None


def test_detect_auth_missing_binary_is_unknown_not_none(tmp_path):
    missing = tmp_path / "does-not-exist"
    auth = claude_resolve.detect_auth(str(missing), "2.1.235", env={})
    assert auth.auth_method == "unknown"
    assert auth.source == "probe-failed"


def test_detect_auth_hang_times_out_and_is_unknown_not_none(tmp_path):
    fx = _write_fixture_claude(
        tmp_path / "claude",
        auth_body='{"loggedIn": true, "authMethod": "oauth_token"}',
        sleep_seconds=10,
    )
    auth = claude_resolve.detect_auth(str(fx), "2.1.235", env={}, timeout=0.3)
    assert auth.auth_method == "unknown"
    assert auth.source == "probe-failed"
    assert "timed out" in auth.error


def test_detect_auth_never_raises_on_any_failure_shape(tmp_path):
    """Belt-and-braces: no matter what the fixture does, detect_auth
    returns a value — it must never propagate an exception."""
    fx = tmp_path / "claude"
    fx.write_text("#!/bin/sh\nexit 1\n")  # no stdout at all
    fx.chmod(0o755)
    auth = claude_resolve.detect_auth(str(fx), "2.1.235", env={})
    assert auth.auth_method == "unknown"
    assert auth.logged_in is False


# ---- detect_auth: uses the GIVEN env, not the caller's os.environ --------

def test_detect_auth_uses_given_env_not_process_environ(tmp_path, monkeypatch):
    """Critical: the probe must run in the caller-supplied env, so a
    daemon under LoadCredential= (token absent from its own os.environ)
    still reports correctly when given build_session_env()'s result."""
    fx = tmp_path / "claude"
    fx.write_text("""#!/bin/sh
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then
    echo '{"loggedIn": true, "authMethod": "oauth_token", "apiProvider": "firstParty"}'
    exit 0
  fi
  echo '{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}'
  exit 1
fi
exit 0
""")
    fx.chmod(0o755)
    # The daemon's own os.environ has nothing — simulate LoadCredential=.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    given_env = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-from-credentials-dir", "PATH": os.environ.get("PATH", "")}
    auth = claude_resolve.detect_auth(str(fx), "2.1.235", env=given_env)
    assert auth.logged_in is True
    assert auth.auth_method == "oauth_token"


# ---- criterion 19's named test: safe-by-default with no explicit mock ----

def test_no_explicit_mock_yields_zero_candidates():
    """Without any resolver mock in the test body, resolution must find
    zero candidates by default — proof the autouse
    `_no_real_claude_candidates` fixture (tests/conftest.py) is wired
    in for this module too. See implementation.md for the
    fixture-reverted mutation check that proves this guard is
    load-bearing project-wide.
    """
    with pytest.raises(claude_resolve.ClaudeNotFoundError) as exc:
        claude_resolve.resolve_claude_binary()
    assert str(exc.value).strip().endswith("Tried:")


# ---- force= bypasses the memo ---------------------------------------------

def test_force_true_bypasses_memo(tmp_path, monkeypatch):
    fx = _write_fixture_claude(tmp_path / "claude", version="1.0.0")
    monkeypatch.setattr(claude_resolve, "_candidate_paths", lambda: [(str(fx), 4)])
    first = claude_resolve.resolve_claude_binary()
    assert first.chosen.version == "1.0.0"

    fx2 = _write_fixture_claude(tmp_path / "claude2", version="2.0.0")
    monkeypatch.setattr(claude_resolve, "_candidate_paths", lambda: [(str(fx2), 4)])
    # Without force, the memo from the first call wins.
    memoized = claude_resolve.resolve_claude_binary()
    assert memoized.chosen.version == "1.0.0"
    # With force, the new candidate set is picked up.
    fresh = claude_resolve.resolve_claude_binary(force=True)
    assert fresh.chosen.version == "2.0.0"


# ---- criterion 2: every call site agrees given the same candidates -------

def test_all_call_sites_resolve_the_same_binary_from_the_same_candidates(
        tmp_path, monkeypatch):
    """preflight.require_claude(), dtach.launcher._claude_version_diag()
    (via resolution success/failure) and claude_bootstrap.
    bootstrap_claude_settings()'s provenance line must all agree,
    because they all funnel through this one resolver — the exact
    six-independent-lookups bug this module exists to fix."""
    from aipager import claude_bootstrap, preflight
    from aipager.dtach import launcher as dtach_launcher

    fx = _write_fixture_claude(tmp_path / "claude", version="2.1.235")
    monkeypatch.setattr(claude_resolve, "_candidate_paths", lambda: [(str(fx), 4)])
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(claude_bootstrap, "_CLAUDE_JSON", tmp_path / ".claude.json")

    # 1) preflight.require_claude()
    resolved_path = preflight.require_claude()
    assert resolved_path == str(fx)

    # 2) _claude_version_diag(): "" means resolution succeeded with no diagnostic.
    assert dtach_launcher._claude_version_diag() == ""

    # 3) bootstrap_claude_settings()'s provenance names the same path.
    info = claude_bootstrap.bootstrap_claude_settings("/workspace")
    assert info is not None
    assert info.lines[0].startswith(f"claude: {fx} (2.1.235)")
