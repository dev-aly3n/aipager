"""design.md success criteria 7, 8, 9 -- ``detect_auth()`` against the
fixture-binary contract in entrypoints.md.

Criterion 9 is explicitly called out as likely to fake-pass: the two
failure states ("probe failed" vs "confirmed logged out") must stay
textually and semantically distinct. Every failure-mode test here
asserts ``auth_method == "unknown"`` *and* separately asserts it is
NOT ``"none"`` -- the conflation the contract calls out by name.
"""
from __future__ import annotations

import pytest

from aipager.claude_resolve import detect_auth


@pytest.mark.parametrize(
    "auth_method,logged_in",
    [
        ("none", False),
        ("oauth_token", True),
        ("api_key", True),
        ("third_party", True),
    ],
)
def test_detect_auth_matches_each_documented_shape(
    claude_fixture_factory, auth_method, logged_in,
):
    path = claude_fixture_factory(
        version="2.1.235 (Claude Code)",
        logged_in=logged_in,
        auth_method=auth_method,
    )

    result = detect_auth(path, "2.1.235", {}, timeout=3.0)

    assert result.logged_in is logged_in
    assert result.auth_method == auth_method


def test_below_version_gate_skips_probe_without_spawning(claude_fixture_factory):
    # Deliberately a path that does not exist -- if the version gate
    # were bypassed, detect_auth would have to spawn it and fail with a
    # probe error instead of a version-gated one.
    nonexistent_path = "/definitely/does/not/exist/claude"

    result = detect_auth(nonexistent_path, "2.1.40", {}, timeout=3.0)

    assert result.source == "version-gated"
    assert result.auth_method == "unknown", (
        "a version-gated probe must never report 'none' -- that would "
        "read as a confirmed not-logged-in"
    )
    assert result.auth_method != "none"
    assert result.error is not None


def test_at_version_gate_boundary_probe_runs(claude_fixture_factory):
    """2.1.41 is the documented floor ('v2.1.41+') -- the probe must run,
    not be gated, at exactly the boundary."""
    path = claude_fixture_factory(
        version="2.1.41 (Claude Code)", logged_in=True, auth_method="oauth_token",
    )

    result = detect_auth(path, "2.1.41", {}, timeout=3.0)

    assert result.source != "version-gated"
    assert result.logged_in is True
    assert result.auth_method == "oauth_token"


def test_missing_binary_reports_unknown_never_none(claude_fixture_factory):
    result = detect_auth(
        "/definitely/does/not/exist/claude", "2.1.235", {}, timeout=3.0,
    )

    assert result.auth_method == "unknown"
    assert result.auth_method != "none"
    assert result.error is not None


def test_nonzero_exit_nonjson_stdout_reports_unknown_never_none(
    claude_fixture_factory,
):
    path = claude_fixture_factory(
        version="2.1.235 (Claude Code)", auth_exit=17, auth_nonjson=True,
    )

    result = detect_auth(path, "2.1.235", {}, timeout=3.0)

    assert result.auth_method == "unknown"
    assert result.auth_method != "none"
    assert result.error is not None


def test_hanging_probe_times_out_and_reports_unknown_never_none(
    claude_fixture_factory,
):
    # A real subprocess.run(..., timeout=...) call underneath detect_auth
    # -- this genuinely blocks for the timeout we pass, so we pass a
    # short one here rather than the 5s default. The fixture sleeps
    # comfortably longer than that timeout so this reliably exercises
    # the timeout path rather than racing it.
    path = claude_fixture_factory(
        version="2.1.235 (Claude Code)", auth_sleep_seconds=3.0,
    )

    result = detect_auth(path, "2.1.235", {}, timeout=0.5)

    assert result.auth_method == "unknown"
    assert result.auth_method != "none"
    assert result.error is not None


def test_detect_auth_never_raises_on_any_failure_mode(claude_fixture_factory):
    """Belt-and-suspenders on entrypoints.md's "Never raises" contract --
    each failure mode above already implicitly proves this (an
    unhandled exception would fail the test with an error, not an
    assertion failure), but this test makes the "never raises" promise
    the explicit subject rather than a side effect.
    """
    hang_path = claude_fixture_factory(auth_sleep_seconds=2.0)
    try:
        detect_auth(hang_path, "2.1.235", {}, timeout=0.3)
        detect_auth("/nope/not/here", "2.1.235", {}, timeout=1.0)
        detect_auth(hang_path, "2.1.40", {}, timeout=1.0)  # version-gated
    except Exception as e:  # noqa: BLE001 - this IS the assertion
        pytest.fail(f"detect_auth raised {e!r} instead of returning a result")
