"""Integration tests: SC18 — no user-facing "unsafe", "dangerous", "danger"
strings in handlers, callbacks, session_ops, README, docs, wizard files.

Except for the literal string '--dangerously-skip-permissions' where it names
Anthropic's CLI flag.

Black-box approach: we read the FILE CONTENT as text and grep for the
forbidden patterns. This tests the observable output of the build/code, not
internals.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Project root (two levels up from this test file's location: tests/integration/perms_mode_ux/)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# Pattern that is ALLOWED: the Anthropic CLI flag verbatim
_ALLOWED_FLAG = "--dangerously-skip-permissions"

# Pattern to detect for the JSON API setting (also Anthropic's name, not ours)
_ALLOWED_SETTING = "skipDangerousModePermissionPrompt"

# Forbidden words as whole patterns (case-insensitive)
_FORBIDDEN = re.compile(r'\b(unsafe|dangerous|danger)\b', re.IGNORECASE)

# File paths relative to project root to check
_FILES_TO_CHECK = [
    "aipager/bot/handlers.py",
    "aipager/bot/callbacks.py",
    "aipager/bot/session_ops.py",
    "README.md",
    "docs/groups.md",
]

# Wizard files — check whole directory
_WIZARD_DIR = _PROJECT_ROOT / "aipager/wizard"


def _extract_forbidden_lines(text: str, source_label: str) -> list[str]:
    """Return list of (line_no, line) tuples where forbidden words appear,
    excluding lines that only contain the allowed Anthropic CLI flag."""
    violations = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _FORBIDDEN.search(line):
            # Remove the allowed flag from the line and check again
            stripped = line.replace(_ALLOWED_FLAG, "").replace(_ALLOWED_SETTING, "")
            if _FORBIDDEN.search(stripped):
                violations.append(f"  {source_label}:{lineno}: {line.strip()}")
    return violations


# --------------------------------------------------------------------------- #
# SC18 — Check each listed file                                                #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("rel_path", _FILES_TO_CHECK)
def test_sc18_no_forbidden_strings_in_file(rel_path):
    """SC18: File must not contain 'unsafe', 'dangerous', or 'danger'
    except as part of the literal --dangerously-skip-permissions flag."""
    file_path = _PROJECT_ROOT / rel_path
    if not file_path.exists():
        pytest.skip(f"File does not exist: {rel_path}")

    text = file_path.read_text(encoding="utf-8", errors="replace")
    violations = _extract_forbidden_lines(text, rel_path)

    assert not violations, (
        f"SC18 FAIL — forbidden user-facing strings found in {rel_path}:\n"
        + "\n".join(violations)
    )


def test_sc18_no_forbidden_strings_in_wizard_files():
    """SC18: Wizard files must not contain 'unsafe', 'dangerous', or 'danger'
    except as part of the literal --dangerously-skip-permissions flag."""
    if not _WIZARD_DIR.exists():
        pytest.skip("Wizard directory does not exist")

    all_violations = []
    for py_file in sorted(_WIZARD_DIR.glob("**/*.py")):
        rel = py_file.relative_to(_PROJECT_ROOT)
        text = py_file.read_text(encoding="utf-8", errors="replace")
        violations = _extract_forbidden_lines(text, str(rel))
        all_violations.extend(violations)

    assert not all_violations, (
        "SC18 FAIL — forbidden strings in wizard files:\n"
        + "\n".join(all_violations)
    )


# --------------------------------------------------------------------------- #
# Positive sanity: allowed flag IS present (proves grep actually works)       #
# --------------------------------------------------------------------------- #

def test_sc18_sanity_allowed_flag_exists_in_handlers():
    """Sanity: --dangerously-skip-permissions appears in handlers.py
    (if it doesn't, either the feature is incomplete OR the test file list
    is wrong). This ensures our grep is not trivially passing on missing files."""
    handlers = _PROJECT_ROOT / "aipager/bot/handlers.py"
    if not handlers.exists():
        pytest.skip("handlers.py not found")

    text = handlers.read_text(encoding="utf-8", errors="replace")
    assert _ALLOWED_FLAG in text, (
        "Sanity: handlers.py must mention --dangerously-skip-permissions "
        "(the Anthropic CLI flag that's allowed). If this test fails, "
        "either the handler doesn't help-text the flag OR the file path is wrong."
    )
