"""design.md success criterion 16:

    "check_service_unit_path() FAILs when the unit's Environment=PATH=
    does not contain the resolved binary's directory."

Both the negative (mismatch -> FAIL) and positive (match -> not FAIL)
cases are tested, and the positive case doubles as a canary against a
plausible "test passes for the wrong reason": if
``check_service_unit_path()`` read the unit file from the wrong (e.g.
real, un-redirected) path, the positive case would see a PATH that
does NOT contain our fixture's tmp-path directory and wrongly report
FAIL, failing this test outright. Only a correct implementation reading
the test-isolated unit file passes both halves.
"""
from __future__ import annotations

import os
from pathlib import Path

import aipager.claude_resolve as claude_resolve_mod
import aipager.service as service_mod
from aipager.doctor import FAIL, check_service_unit_path


def _put_on_path(monkeypatch, *dirs):
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))


def _resolve_fixture(monkeypatch, claude_fixture_factory, real_candidate_paths,
                      isolate_from_real_local_bin_claude, tmp_path):
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)
    _put_on_path(monkeypatch, tmp_path / "empty_path_dir")
    fixture_path = claude_fixture_factory(
        version="2.1.235 (Claude Code)",
    )
    monkeypatch.setenv("AIPAGER_CLAUDE_BIN", fixture_path)
    return claude_resolve_mod.resolve_claude_binary(force=True)


def _write_unit_with_path(path_value: str) -> None:
    service_mod.LINUX_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    service_mod.LINUX_UNIT_PATH.write_text(
        "[Unit]\n"
        "Description=AIPager Telegram Bot Daemon\n"
        "StartLimitIntervalSec=0\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"Environment=PATH={path_value}\n"
        "LoadCredential=claude_oauth:%h/.config/aipager/daemon.env\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )


def test_fails_when_unit_path_does_not_contain_resolved_binary_dir(
    tmp_path, monkeypatch, claude_fixture_factory, real_candidate_paths,
    isolate_from_real_local_bin_claude,
):
    resolved = _resolve_fixture(
        monkeypatch, claude_fixture_factory, real_candidate_paths,
        isolate_from_real_local_bin_claude, tmp_path,
    )
    binary_dir = str(Path(resolved.chosen.path).parent)
    unrelated_dir = str(tmp_path / "totally_unrelated_dir")
    assert binary_dir != unrelated_dir

    _write_unit_with_path(f"/usr/bin:/bin:{unrelated_dir}")

    result = check_service_unit_path()

    assert result.status == FAIL, (
        f"expected FAIL when unit PATH lacks {binary_dir!r}, got "
        f"status={result.status!r} detail={result.detail!r}"
    )


def test_passes_when_unit_path_contains_resolved_binary_dir(
    tmp_path, monkeypatch, claude_fixture_factory, real_candidate_paths,
    isolate_from_real_local_bin_claude,
):
    resolved = _resolve_fixture(
        monkeypatch, claude_fixture_factory, real_candidate_paths,
        isolate_from_real_local_bin_claude, tmp_path,
    )
    binary_dir = str(Path(resolved.chosen.path).parent)

    _write_unit_with_path(f"/usr/bin:/bin:{binary_dir}")

    result = check_service_unit_path()

    assert result.status != FAIL, (
        f"a unit PATH that DOES contain {binary_dir!r} was still reported "
        f"as FAIL — either check_service_unit_path() is reading the wrong "
        f"unit file, or the resolved binary's directory, or both: "
        f"status={result.status!r} detail={result.detail!r}"
    )


def test_no_installed_unit_does_not_raise(tmp_path):
    """entrypoints.md: check_service_unit_path() is "read-only". It is
    not documented what status a missing unit produces (that is a
    different situation from "unit installed, PATH mismatched" which
    the two tests above cover), so this only asserts the read-only,
    never-raises contract holds when there is nothing installed to
    read -- not a specific status value entrypoints.md does not commit
    to.
    """
    assert not service_mod.LINUX_UNIT_PATH.exists()

    result = check_service_unit_path()  # must not raise

    assert result.status in ("ok", "warn", "fail")
