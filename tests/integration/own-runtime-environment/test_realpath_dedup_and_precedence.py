"""design.md success criteria 3, 4, 6:

    3. Two candidates with equal realpath() (symlink fixture reproducing
       /bin->/usr/bin) yield exactly one chosen install and zero "also
       found".
    4. Two candidates with different realpaths and versions -> higher
       chosen, lower in others.
    6. Two distinct installs -> exactly one "also found: ... - set
       claude_path to override" line, version-descending.

All three exercise real ``$PATH`` discovery per entrypoints.md's
fixture-binary contract ("two fixture paths where one is an os.symlink
to the other, both on $PATH" for the duplicate case), so each test
restores the real (un-stubbed) ``_candidate_paths`` -- see
conftest.py's module docstring -- rather than guessing at its internal
representation.
"""
from __future__ import annotations

import os

import aipager.claude_resolve as claude_resolve_mod
from aipager.claude_resolve import resolve_claude_binary

from conftest import write_claude_fixture


def _put_on_path(monkeypatch, *dirs):
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))


def test_symlinked_duplicate_yields_one_chosen_and_zero_also_found(
    tmp_path, monkeypatch, real_candidate_paths,
):
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)

    real_dir = tmp_path / "usr_bin"
    real_dir.mkdir()
    real_claude = real_dir / "claude"
    write_claude_fixture(real_claude, version="2.1.200 (Claude Code)")

    symlink_dir = tmp_path / "bin"
    symlink_dir.mkdir()
    (symlink_dir / "claude").symlink_to(real_claude)

    # Directory order matters for "which one is reported" but not for
    # "how many distinct installs exist" -- put the symlink first, like
    # /bin before /usr/bin on the box the bug was found on.
    _put_on_path(monkeypatch, symlink_dir, real_dir)

    resolved = resolve_claude_binary(force=True)

    assert resolved.others == (), (
        f"a symlinked duplicate of the SAME real install was reported as "
        f"an extra distinct install: {resolved.others!r}"
    )
    assert os.path.realpath(resolved.chosen.path) == str(real_claude)


def test_two_distinct_installs_higher_version_chosen_lower_in_others(
    tmp_path, monkeypatch, real_candidate_paths,
):
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)

    dir_a = tmp_path / "install_a"
    dir_a.mkdir()
    low = write_claude_fixture(dir_a / "claude", version="2.1.100 (Claude Code)")

    dir_b = tmp_path / "install_b"
    dir_b.mkdir()
    high = write_claude_fixture(dir_b / "claude", version="2.1.235 (Claude Code)")

    _put_on_path(monkeypatch, dir_a, dir_b)

    resolved = resolve_claude_binary(force=True)

    assert resolved.chosen.path == high
    assert resolved.chosen.version == "2.1.235"
    assert len(resolved.others) == 1
    assert resolved.others[0].path == low
    assert resolved.others[0].version == "2.1.100"


def test_three_distinct_installs_others_is_version_descending(
    tmp_path, monkeypatch, real_candidate_paths,
):
    monkeypatch.setattr(claude_resolve_mod, "_candidate_paths", real_candidate_paths)

    versions = ["2.1.50", "2.1.235", "2.1.140"]
    dirs = []
    for i, v in enumerate(versions):
        d = tmp_path / f"install_{i}"
        d.mkdir()
        write_claude_fixture(d / "claude", version=f"{v} (Claude Code)")
        dirs.append(d)

    _put_on_path(monkeypatch, *dirs)

    resolved = resolve_claude_binary(force=True)

    assert resolved.chosen.version == "2.1.235"
    other_versions = [o.version for o in resolved.others]
    assert other_versions == sorted(other_versions, key=_semver, reverse=True), (
        f"'others' must be version-descending, got {other_versions!r}"
    )
    assert set(other_versions) == {"2.1.50", "2.1.140"}


def _semver(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))
