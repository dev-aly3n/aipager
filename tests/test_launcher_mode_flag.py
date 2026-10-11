"""launcher._with_mode_flag: `aipager launch` starts a session in Auto mode
unless its own arguments choose a mode."""

from __future__ import annotations

import pytest

from aipager.dtach.launcher import _with_mode_flag


@pytest.mark.parametrize("args", [
    [], ["--model", "opus"], ["--allow-dangerously-skip-permissions"],
], ids=["none", "other-args", "allow-flag-is-not-a-mode"])
def test_auto_is_added_in_front(args):
    assert _with_mode_flag(args) == ["--permission-mode", "auto", *args]


@pytest.mark.parametrize("args", [
    ["--permission-mode", "default"], ["--permission-mode=plan"],
    ["--dangerously-skip-permissions"], ["--model", "opus", "--permission-mode", "auto"],
], ids=["separate", "equals", "bypass", "later"])
def test_a_chosen_mode_is_kept_alone(args):
    out = _with_mode_flag(args)
    assert out == args
    assert out is not args  # a copy: the caller's list is never changed
