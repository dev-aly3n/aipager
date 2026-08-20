"""design.md success criteria 5, 6:

    5. Single-install provenance matches
       "claude: <path> (<version>) . auth: <method> (<source>)" exactly.
    6. Two distinct installs -> exactly one "also found: ... - set
       claude_path to override" line, version-descending.

Plus the two auth-shape edges design.md's "provenance line" section
spells out literally: not-logged-in and probe-failed wording.
"""
from __future__ import annotations

import os
from pathlib import Path

import aipager.claude_resolve as claude_resolve_mod
from aipager.claude_resolve import (
    detect_auth,
    format_provenance,
    resolve_claude_binary,
)


def _put_on_path(monkeypatch, *dirs):
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))


def test_single_install_provenance_line_matches_exact_format(
    tmp_path, monkeypatch, claude_fixture_factory, real_candidate_paths,
    isolate_from_real_local_bin_claude,
):
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)
    # Tier 4 ($PATH) must be empty here -- otherwise this box's own real
    # /usr/bin/claude, /bin/claude (verified present on the test
    # machine) would also verify and appear as "also found" entries,
    # which is not what this test is about and would mean running
    # --version against a real installed claude.
    _put_on_path(monkeypatch, tmp_path / "empty_path_dir")
    monkeypatch.setenv("AIPAGER_CLAUDE_BIN", claude_fixture_factory(
        version="2.1.235 (Claude Code)", logged_in=True, auth_method="oauth_token",
    ))

    resolved = resolve_claude_binary(force=True)
    auth = detect_auth(
        resolved.chosen.path, resolved.chosen.version, {}, timeout=3.0,
    )
    lines = format_provenance(resolved, auth)

    assert lines[0] == (
        f"claude: {resolved.chosen.path} (2.1.235) · "
        f"auth: oauth_token ({auth.source})"
    )
    assert len(lines) == 1, (
        f"single-install case must not emit an 'also found' line, got {lines!r}"
    )


def test_two_installs_exactly_one_also_found_line_version_descending(
    tmp_path, monkeypatch, real_candidate_paths, claude_fixture_factory,
    isolate_from_real_local_bin_claude,
):
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)

    low = claude_fixture_factory(name="lower", version="2.1.100 (Claude Code)")
    high = claude_fixture_factory(name="higher", version="2.1.235 (Claude Code)")
    _put_on_path(monkeypatch, Path(low).parent, Path(high).parent)

    resolved = resolve_claude_binary(force=True)
    auth = detect_auth(
        resolved.chosen.path, resolved.chosen.version, {}, timeout=3.0,
    )
    lines = format_provenance(resolved, auth)

    also_found = [line for line in lines if line.startswith("also found:")]
    assert len(also_found) == 1, f"expected exactly one 'also found' line: {lines!r}"
    assert also_found[0] == (
        f"also found: {low} (2.1.100) — set claude_path to override"
    )


def test_not_logged_in_wording(claude_fixture_factory):
    path = claude_fixture_factory(logged_in=False, auth_method="none")
    from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
    install = ClaudeInstall(path=path, realpath=path, version="2.1.235")
    resolved = ResolvedClaude(chosen=install, others=())

    auth = detect_auth(path, "2.1.235", {}, timeout=3.0)
    lines = format_provenance(resolved, auth)

    assert "auth: none (not logged in)" in lines[0]


def test_probe_failed_wording(claude_fixture_factory):
    from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
    install = ClaudeInstall(
        path="/does/not/exist/claude", realpath="/does/not/exist/claude",
        version="2.1.235",
    )
    resolved = ResolvedClaude(chosen=install, others=())

    auth = detect_auth("/does/not/exist/claude", "2.1.235", {}, timeout=3.0)
    lines = format_provenance(resolved, auth)

    assert "auth: unknown (status probe failed:" in lines[0], lines[0]
