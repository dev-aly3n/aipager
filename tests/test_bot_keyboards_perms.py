"""Tests for new permission-mode keyboards in KeyboardMixin."""

from __future__ import annotations

import pytest
from telegram import InlineKeyboardMarkup


@pytest.fixture
def bot(mk_bot):
    return mk_bot()


# ---- _build_permission_keyboard: 2×2 grid ----------------------------------

def test_permission_keyboard_has_two_rows(bot):
    kb = bot._build_permission_keyboard("claude-dev")
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 2


def test_permission_keyboard_row0_allow_deny(bot):
    kb = bot._build_permission_keyboard("claude-dev")
    row0 = kb.inline_keyboard[0]
    assert len(row0) == 2
    labels = [btn.text for btn in row0]
    assert any("Allow" in l and "always" not in l.lower() for l in labels), labels
    assert any("Deny" in l for l in labels), labels


def test_permission_keyboard_row1_allow_always_stop(bot):
    kb = bot._build_permission_keyboard("claude-dev")
    row1 = kb.inline_keyboard[1]
    assert len(row1) == 2
    labels = [btn.text for btn in row1]
    assert any("Allow always" in l or "allow always" in l.lower() for l in labels), labels
    assert any("Stop" in l for l in labels), labels


def test_permission_keyboard_callback_data(bot):
    kb = bot._build_permission_keyboard("claude-dev")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "claude-dev:allow" in cbs
    assert "claude-dev:deny" in cbs
    assert "claude-dev:allow_always" in cbs
    assert "claude-dev:stop" in cbs


# ---- _build_perms_confirm_keyboard -----------------------------------------

def test_perms_confirm_keyboard_has_one_row(bot):
    kb = bot._build_perms_confirm_keyboard("claude-dev")
    assert len(kb.inline_keyboard) == 1


def test_perms_confirm_keyboard_buttons(bot):
    kb = bot._build_perms_confirm_keyboard("claude-dev")
    row = kb.inline_keyboard[0]
    labels = [btn.text for btn in row]
    cbs = [btn.callback_data for btn in row]
    assert any("Yes" in l or "switch" in l.lower() for l in labels), labels
    assert any("Cancel" in l for l in labels), labels
    assert any("perms_confirm" in c for c in cbs), cbs
    assert any("perms_cancel" in c for c in cbs), cbs


# ---- _build_perms_busy_keyboard --------------------------------------------

def test_perms_busy_keyboard_has_one_row(bot):
    kb = bot._build_perms_busy_keyboard("claude-dev")
    assert len(kb.inline_keyboard) == 1


def test_perms_busy_keyboard_buttons(bot):
    kb = bot._build_perms_busy_keyboard("claude-dev")
    row = kb.inline_keyboard[0]
    labels = [btn.text for btn in row]
    cbs = [btn.callback_data for btn in row]
    assert any("Stop" in l or "switch" in l.lower() for l in labels), labels
    assert any("Not now" in l or "now" in l.lower() for l in labels), labels
    assert any("perms_stop_switch" in c for c in cbs), cbs
    assert any("perms_wait" in c for c in cbs), cbs


# ---- _build_resume_mode_keyboard -------------------------------------------

def test_resume_mode_keyboard_has_two_rows(bot):
    kb = bot._build_resume_mode_keyboard("claude-dev", False)
    assert len(kb.inline_keyboard) == 2


def test_resume_mode_keyboard_default_label_ask(bot):
    """When persisted_skip_perms=False, Ask button should have (default) suffix."""
    kb = bot._build_resume_mode_keyboard("claude-dev", persisted_skip_perms=False)
    row0 = kb.inline_keyboard[0]
    labels = [btn.text for btn in row0]
    ask_label = next(l for l in labels if "Ask" in l)
    auto_label = next(l for l in labels if "Auto" in l)
    assert "(default)" in ask_label
    assert "(default)" not in auto_label


def test_resume_mode_keyboard_default_label_auto(bot):
    """When persisted_skip_perms=True, Auto button should have (default) suffix."""
    kb = bot._build_resume_mode_keyboard("claude-dev", persisted_skip_perms=True)
    row0 = kb.inline_keyboard[0]
    labels = [btn.text for btn in row0]
    ask_label = next(l for l in labels if "Ask" in l)
    auto_label = next(l for l in labels if "Auto" in l)
    assert "(default)" not in ask_label
    assert "(default)" in auto_label


def test_resume_mode_keyboard_callbacks(bot):
    kb = bot._build_resume_mode_keyboard("claude-dev", False)
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any("resume_mode_ask" in c for c in cbs), cbs
    assert any("resume_mode_auto" in c for c in cbs), cbs
    assert any("resume_mode_cancel" in c for c in cbs), cbs


def test_resume_mode_keyboard_cancel_in_row1(bot):
    kb = bot._build_resume_mode_keyboard("claude-dev", False)
    row1 = kb.inline_keyboard[1]
    cbs = [btn.callback_data for btn in row1]
    assert any("resume_mode_cancel" in c for c in cbs), cbs


# ---- _make_cb: 64-byte limit -----------------------------------------------

def test_make_cb_normal_within_limit(bot):
    cb = bot._make_cb("claude-dev", "perms_confirm")
    assert len(cb.encode()) <= 64
    assert cb == "claude-dev:perms_confirm"


def test_make_cb_asserts_on_overflow(bot):
    # _make_cb now raises AssertionError instead of silently truncating,
    # so operators get a clear error rather than a broken dispatch lookup.
    long_name = "claude-" + "x" * 60
    action = "perms_stop_switch"
    with pytest.raises(AssertionError, match="callback_data overflow"):
        bot._make_cb(long_name, action)
