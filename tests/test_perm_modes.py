"""aipager/perm_modes.py: the three permission modes, their launch flags,
what Claude Code's reported mode means, Shift+Tab press counts, and the
migrations of stored values."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from aipager import perm_modes as pm


def test_the_three_ids_in_order():
    assert pm.MODES == ("ask", "auto", "full")


@pytest.mark.parametrize("mode, flag", [
    ("ask", "--permission-mode default"),
    ("auto", "--permission-mode auto"),
    ("full", "--dangerously-skip-permissions"),
], ids=["ask", "auto", "full"])
def test_launch_flag(mode, flag):
    assert pm.launch_flag(mode) == flag
    assert pm.launch_args(mode) == tuple(flag.split())


@pytest.mark.parametrize("bad", [True, False, "", "bypass", "Auto", None, 1],
                         ids=["bool-true", "bool-false", "empty", "unknown", "case", "none",
                              "int"])
def test_launch_flag_refuses_anything_else(bad):
    with pytest.raises(ValueError):
        pm.launch_flag(bad)
    assert pm.is_mode(bad) is False


def test_the_allow_bypass_flag_is_never_in_this_module_or_inject():
    """The flag that would put bypass into the Shift+Tab cycle (and so make
    every press count wrong) appears in neither file."""
    root = Path(pm.__file__).resolve().parent
    for rel in ("perm_modes.py", "dtach/inject.py"):
        assert "--allow-dangerously-skip-permissions" not in (root / rel).read_text(), rel


@pytest.mark.parametrize("raw, mapped", [
    ("default", "ask"), ("manual", "ask"), ("auto", "auto"), ("bypassPermissions", "full"),
    ("acceptEdits", "other"), ("plan", "other"), ("dontAsk", "other"),
    ("", None), ("Default", None), (None, None), (True, None),
], ids=["default", "manual", "auto", "bypass", "accept-edits", "plan", "dont-ask", "empty",
        "case", "none", "bool"])
def test_from_claude(raw, mapped):
    assert pm.from_claude(raw) == mapped


@pytest.mark.parametrize("start, target, want", [
    ("default", "auto", 3), ("auto", "ask", 1), ("plan", "auto", 1), ("acceptEdits", "auto", 2),
    ("plan", "ask", 2), ("acceptEdits", "ask", 3), ("default", "ask", 0), ("auto", "auto", 0),
    ("manual", "auto", 3),
], ids=["ask-to-auto", "auto-to-ask", "plan-to-auto", "accept-edits-to-auto", "plan-to-ask",
        "accept-edits-to-ask", "ask-to-ask", "auto-to-auto", "manual-to-auto"])
def test_presses_follow_claude_codes_cycle(start, target, want):
    assert pm.presses(start, target) == want


@pytest.mark.parametrize("start, target, want", [
    ("default", "ask", 0), ("plan", "ask", 1), ("acceptEdits", "ask", 2),
    ("default", "auto", None), ("auto", "ask", None),
], ids=["ask", "plan-to-ask", "accept-edits-to-ask", "auto-unreachable", "from-auto"])
def test_presses_without_auto_in_the_cycle(start, target, want):
    assert pm.presses(start, target, auto_in_cycle=False) == want


@pytest.mark.parametrize("start, target", [
    ("bypassPermissions", "ask"), ("dontAsk", "auto"), ("weird", "ask"), ("default", "full"),
    ("", "ask"),
], ids=["from-bypass", "from-dont-ask", "unknown", "to-full", "empty"])
def test_presses_none_when_shift_tab_cannot_get_there(start, target):
    assert pm.presses(start, target) is None


@pytest.mark.parametrize("record, want", [
    ({"perm_mode": "auto"}, "auto"), ({"perm_mode": "full", "skip_perms": False}, "full"),
    ({"skip_perms": True}, "full"), ({"skip_perms": False}, "ask"), ({}, "ask"),
    ({"perm_mode": "x", "skip_perms": True}, "full"), ({"skip_perms": 1}, "ask"),
    ("not a dict", "ask"),
], ids=["new", "new-wins", "legacy-true", "legacy-false", "empty", "invalid-new",
        "truthy-legacy", "not-a-dict"])
def test_mode_from_saved(record, want):
    assert pm.mode_from_saved(record) == want


@pytest.mark.parametrize("raw, want", [
    ({"new_session_perm_mode": "ask"}, "ask"),
    ({"new_session_perm_mode": "full", "new_session_mode": "ask"}, "full"),
    ({"new_session_mode": "auto"}, "full"),
    ({"new_session_mode": "ask"}, "ask"),
    ({"new_session_mode": ""}, "auto"),
    ({}, "auto"),
    ({"new_session_perm_mode": "bogus", "new_session_mode": "ask"}, "ask"),
    ({"new_session_mode": "weird"}, "auto"),
    (None, "auto"),
], ids=["new", "new-wins", "legacy-auto-was-bypass", "legacy-ask", "legacy-empty", "missing",
        "invalid-new", "invalid-legacy", "not-a-dict"])
def test_mode_from_new_session_prefs(raw, want):
    assert pm.mode_from_new_session_prefs(raw) == want


@pytest.mark.parametrize("mode, may_full, want", [
    ("full", False, "auto"), ("full", True, "full"), ("ask", False, "ask"),
    ("auto", False, "auto"),
], ids=["full-refused", "full-allowed", "ask", "auto"])
def test_narrowest_allowed(mode, may_full, want):
    assert pm.narrowest_allowed(mode, may_full) == want


def test_the_module_loads_no_i18n():
    """It is in the hook's import closure (through aipager.state)."""
    before = set(sys.modules)
    for name in [m for m in sys.modules if m == "aipager.perm_modes"]:
        del sys.modules[name]
    importlib.import_module("aipager.perm_modes")
    loaded = set(sys.modules) - before
    assert not any(m.startswith("aipager.i18n") for m in loaded)
