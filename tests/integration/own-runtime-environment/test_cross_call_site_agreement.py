"""design.md success criterion 2:

    "cli/session.py, preflight.require_claude(), bootstrap_claude_
    settings() and _claude_version_diag() all resolve identically given
    the same PATH/claude_path/AIPAGER_CLAUDE_BIN."

Also doubles as a regression test for the exact "default-argument
staleness" bug class the task brief calls out: ``scope.load_claude_path``
/ ``dump_claude_path`` default to the module's CONFIG_PATH bound at
*definition* time, so a caller inside claude_resolve.py that forgets to
pass ``path=`` would silently read the operator's real
``~/.config/aipager/aipager.yaml`` instead of the test-isolated one.
This is tested here (not skipped) by making tier 1 (``claude_path``
config) and tier 2 (``AIPAGER_CLAUDE_BIN``) point at two DIFFERENT
fixtures and asserting the config one wins -- if claude_resolve read
the stale real path instead, tier 1 would appear unset and tier 2
would incorrectly win, which fails this assertion.
"""
from __future__ import annotations

import os

import aipager.claude_resolve as claude_resolve_mod
import aipager.scope as scope_mod
from aipager.claude_bootstrap import bootstrap_claude_settings
from aipager.dtach.launcher import _claude_version_diag
from aipager.preflight import require_claude


def _put_on_path(monkeypatch, *dirs):
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))


def test_require_claude_diag_and_bootstrap_agree_via_AIPAGER_CLAUDE_BIN(
    tmp_path, monkeypatch, claude_fixture_factory, real_candidate_paths,
    isolate_from_real_local_bin_claude,
):
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)
    _put_on_path(monkeypatch, tmp_path / "empty_path_dir")

    fixture_path = claude_fixture_factory(
        version="2.1.235 (Claude Code)", logged_in=True, auth_method="oauth_token",
    )
    monkeypatch.setenv("AIPAGER_CLAUDE_BIN", fixture_path)

    resolved_path = require_claude()
    assert resolved_path == fixture_path

    diag = _claude_version_diag()
    assert diag == "", (
        f"_claude_version_diag() reported a resolution problem even though "
        f"require_claude() resolved fine: {diag!r}"
    )

    provenance = bootstrap_claude_settings(workdir=str(tmp_path))
    assert provenance is not None
    assert fixture_path in provenance.lines[0], (
        f"bootstrap_claude_settings() resolved a different binary than "
        f"require_claude(): {provenance.lines[0]!r} vs {fixture_path!r}"
    )


def test_claude_path_config_tier_wins_over_AIPAGER_CLAUDE_BIN(
    tmp_path, monkeypatch, claude_fixture_factory, real_candidate_paths,
    isolate_from_real_local_bin_claude,
):
    """Regression test for the config-read staleness bug class -- see
    module docstring. If claude_resolve.py's tier-1 lookup forgot to
    pass path= (reading the real ~/.config/aipager/aipager.yaml instead
    of this test's isolated one), tier 1 would look empty and tier 2
    would win instead, failing this assertion.
    """
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)
    _put_on_path(monkeypatch, tmp_path / "empty_path_dir")

    tier2_path = claude_fixture_factory(
        name="tier2_env_var", version="1.1.1 (Claude Code)",
    )
    monkeypatch.setenv("AIPAGER_CLAUDE_BIN", tier2_path)

    tier1_path = claude_fixture_factory(
        name="tier1_config", version="9.9.9 (Claude Code)",
    )
    # aipager.yaml must exist before dump_claude_path() will write to it
    # (a fresh document, not derived from the operator's real one --
    # scope.CONFIG_PATH is redirected to tmp_path by the root suite's
    # autouse _isolate_wizard_config fixture, and every call below
    # passes path= explicitly regardless, so this never risks touching
    # the real file even if that redirect were ever removed).
    cfg_path = scope_mod.CONFIG_PATH
    scope_mod.dump_scopes(
        [scope_mod.Scope(chat_id=1, kind="dm", label="owner")],
        "TESTTOKEN", path=cfg_path,
    )
    scope_mod.dump_claude_path(tier1_path, path=cfg_path)

    resolved = claude_resolve_mod.resolve_claude_binary(force=True)

    assert resolved.chosen.path == tier1_path, (
        f"tier 1 (claude_path config) must win over tier 2 "
        f"(AIPAGER_CLAUDE_BIN) once it verifies, got {resolved.chosen.path!r} "
        f"-- if this is tier2_path instead, claude_resolve is reading the "
        f"config from the wrong (possibly real, stale-default) path"
    )
