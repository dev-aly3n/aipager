"""design.md success criterion 13:

    "_install_linux() on a differing existing unit without --yes aborts
    without writing when declined; with --yes backs up and overwrites."

design.md section 7 also documents the no-op case ("byte-identical ->
no-op") and the fresh-install case (covered in
test_service_unit_contents.py). This file covers the two "an existing,
DIFFERENT unit is already there" branches.
"""
from __future__ import annotations

import aipager.service as service_mod


def _write_existing_unit(text: str = "[Unit]\nDescription=old hand-written unit\n"):
    service_mod.LINUX_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    service_mod.LINUX_UNIT_PATH.write_text(text)
    return text


def test_declining_the_prompt_leaves_the_existing_unit_byte_for_byte_unchanged(
    monkeypatch,
):
    original_text = _write_existing_unit()
    before_mtime = service_mod.LINUX_UNIT_PATH.stat().st_mtime_ns

    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")

    rc = service_mod._install_linux(yes=False)

    assert rc == 0
    after_text = service_mod.LINUX_UNIT_PATH.read_text()
    assert after_text == original_text, (
        "declining the diff-and-ask prompt must leave the file byte-for-byte "
        "unchanged"
    )
    assert service_mod.LINUX_UNIT_PATH.stat().st_mtime_ns == before_mtime
    # No backup should have been created for a declined overwrite either.
    backups = list(service_mod.LINUX_UNIT_PATH.parent.glob("*.bak.*"))
    assert backups == []


def test_yes_flag_backs_up_and_overwrites_without_prompting(monkeypatch):
    original_text = _write_existing_unit()

    def _explode(*a, **k):
        raise AssertionError(
            "--yes must never prompt — input() was called anyway"
        )
    monkeypatch.setattr("builtins.input", _explode)

    rc = service_mod._install_linux(yes=True)

    assert rc == 0
    new_text = service_mod.LINUX_UNIT_PATH.read_text()
    assert new_text != original_text, "the differing unit was not overwritten"
    assert "LoadCredential=" in new_text

    backups = list(service_mod.LINUX_UNIT_PATH.parent.glob("*.bak.*"))
    assert len(backups) == 1, f"expected exactly one backup file, got {backups!r}"
    assert backups[0].read_text() == original_text


def test_byte_identical_existing_unit_is_a_no_op_and_never_prompts(monkeypatch):
    # First install to get the real rendered content...
    service_mod._install_linux(yes=True)
    rendered_once = service_mod.LINUX_UNIT_PATH.read_text()
    mtime_after_first_install = service_mod.LINUX_UNIT_PATH.stat().st_mtime_ns

    def _explode(*a, **k):
        raise AssertionError(
            "a byte-identical re-install must never prompt"
        )
    monkeypatch.setattr("builtins.input", _explode)

    rc = service_mod._install_linux(yes=False)

    assert rc == 0
    assert service_mod.LINUX_UNIT_PATH.read_text() == rendered_once
    assert service_mod.LINUX_UNIT_PATH.stat().st_mtime_ns == mtime_after_first_install, (
        "a byte-identical unit must not even be rewritten (mtime should be "
        "untouched), let alone backed up"
    )
    backups = list(service_mod.LINUX_UNIT_PATH.parent.glob("*.bak.*"))
    assert backups == []
