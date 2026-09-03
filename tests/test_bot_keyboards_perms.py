"""Tests for new permission-mode keyboards in KeyboardMixin."""

from __future__ import annotations

import pytest
from telegram import InlineKeyboardMarkup

from aipager.state import Status, TrackedSession


@pytest.fixture
def bot(mk_bot):
    return mk_bot()


# Fixed, not the TrackedSession default of 0: `_build_permission_keyboard`
# and friends derive chat_id via `resolve_chat_id_int(sess) or 0`, which
# falls back to `config.CHAT_ID` for an unstamped (0) session — and that
# module global is baked at import time from whatever
# `~/.config/aipager/aipager.yaml` happens to exist on THIS machine, not
# from a hermetic test default. Pinning `scope_chat_id` decouples these
# tests from that ambient state.
_TEST_CHAT_ID = 100


def _sess(bot, name="claude-dev", label="dev", status=Status.IDLE):
    sess = TrackedSession(name=name, label=label, status=status,
                          scope_chat_id=_TEST_CHAT_ID)
    bot.registry._sessions[sess.name] = sess
    return sess


def _destination(bot, cb, *, chat_id=_TEST_CHAT_ID):
    """Resolve one `_:sx:<idx>:<verb>` callback_data string to
    `(session_name, verb)` — asserts the DESTINATION a button reaches,
    not the encoding, since no `{name}:<verb>` form can fit Telegram's
    64-byte cap (design.md)."""
    from aipager.bot import session_parity

    sentinel, kind, idx, verb = cb.split(":", 3)
    assert (sentinel, kind) == ("_", "sx"), f"unexpected callback form: {cb!r}"
    sess = session_parity._resolve_pref_index(bot, chat_id, idx)
    return (sess.name if sess is not None else None), verb


# ---- _build_permission_keyboard: 2×2 grid ----------------------------------

def test_permission_keyboard_has_two_rows(bot):
    kb = bot._build_permission_keyboard(_sess(bot))
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 2


def test_permission_keyboard_row0_allow_deny(bot):
    kb = bot._build_permission_keyboard(_sess(bot))
    row0 = kb.inline_keyboard[0]
    assert len(row0) == 2
    labels = [btn.text for btn in row0]
    assert any("Allow" in lbl and "always" not in lbl.lower() for lbl in labels), labels
    assert any("Deny" in lbl for lbl in labels), labels


def test_permission_keyboard_row1_allow_always_stop(bot):
    # Allow always exists only with a standing rule to widen (2.1.259 guard).
    kb = bot._build_permission_keyboard(_sess(bot), always_available=True)
    row1 = kb.inline_keyboard[1]
    assert len(row1) == 2
    labels = [btn.text for btn in row1]
    assert any("Allow always" in lbl or "allow always" in lbl.lower() for lbl in labels), labels
    assert any("Stop" in lbl for lbl in labels), labels


def test_permission_keyboard_callback_data(bot):
    sess = _sess(bot)
    kb = bot._build_permission_keyboard(sess, always_available=True)
    dests = [_destination(bot, btn.callback_data)
             for row in kb.inline_keyboard for btn in row]
    assert (sess.name, "allow") in dests
    assert (sess.name, "deny") in dests
    assert (sess.name, "allow_always") in dests
    assert (sess.name, "stop") in dests


# ---- _build_perms_confirm_keyboard -----------------------------------------

def test_perms_confirm_keyboard_has_one_row(bot):
    kb = bot._build_perms_confirm_keyboard(_sess(bot))
    assert len(kb.inline_keyboard) == 1


def test_perms_confirm_keyboard_buttons(bot):
    sess = _sess(bot)
    kb = bot._build_perms_confirm_keyboard(sess)
    row = kb.inline_keyboard[0]
    labels = [btn.text for btn in row]
    dests = [_destination(bot, btn.callback_data) for btn in row]
    assert any("Yes" in lbl or "switch" in lbl.lower() for lbl in labels), labels
    assert any("Cancel" in lbl for lbl in labels), labels
    assert (sess.name, "perms_confirm") in dests
    assert (sess.name, "perms_cancel") in dests


# ---- _build_perms_busy_keyboard --------------------------------------------

def test_perms_busy_keyboard_has_one_row(bot):
    kb = bot._build_perms_busy_keyboard(_sess(bot))
    assert len(kb.inline_keyboard) == 1


def test_perms_busy_keyboard_buttons(bot):
    sess = _sess(bot)
    kb = bot._build_perms_busy_keyboard(sess)
    row = kb.inline_keyboard[0]
    labels = [btn.text for btn in row]
    dests = [_destination(bot, btn.callback_data) for btn in row]
    assert any("Stop" in lbl or "switch" in lbl.lower() for lbl in labels), labels
    assert any("Not now" in lbl or "now" in lbl.lower() for lbl in labels), labels
    assert (sess.name, "perms_stop_switch") in dests
    assert (sess.name, "perms_wait") in dests


# ---- _build_resume_mode_keyboard -------------------------------------------

def test_resume_mode_keyboard_has_two_rows(bot):
    kb = bot._build_resume_mode_keyboard(_sess(bot), False)
    assert len(kb.inline_keyboard) == 2


def test_resume_mode_keyboard_default_label_ask(bot):
    """When persisted_skip_perms=False, Ask button should have (default) suffix."""
    kb = bot._build_resume_mode_keyboard(_sess(bot), persisted_skip_perms=False)
    row0 = kb.inline_keyboard[0]
    labels = [btn.text for btn in row0]
    ask_label = next(lbl for lbl in labels if "Ask" in lbl)
    auto_label = next(lbl for lbl in labels if "Auto" in lbl)
    assert "(default)" in ask_label
    assert "(default)" not in auto_label


def test_resume_mode_keyboard_default_label_auto(bot):
    """When persisted_skip_perms=True, Auto button should have (default) suffix."""
    kb = bot._build_resume_mode_keyboard(_sess(bot), persisted_skip_perms=True)
    row0 = kb.inline_keyboard[0]
    labels = [btn.text for btn in row0]
    ask_label = next(lbl for lbl in labels if "Ask" in lbl)
    auto_label = next(lbl for lbl in labels if "Auto" in lbl)
    assert "(default)" not in ask_label
    assert "(default)" in auto_label


def test_resume_mode_keyboard_callbacks_for_a_known_session(bot):
    """A session's resume-mode picker gets the short indexed form,
    because `{name}:resume_mode_cancel` reaches 83 bytes for a
    maximum-length name — well past Telegram's 64-byte cap."""
    sess = _sess(bot, status=Status.GONE)

    kb = bot._build_resume_mode_keyboard(sess, False, chat_id=555)
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    dests = [_destination(bot, c, chat_id=555) for c in cbs]

    assert {v for _, v in dests} == {
        "resume-ask", "resume-auto", "resume-cancel"}, dests
    assert {n for n, _ in dests} == {sess.name}, dests
    for c in cbs:
        assert len(c.encode()) <= 64, c


def test_resume_mode_keyboard_cancel_in_row1(bot):
    sess = _sess(bot)
    kb = bot._build_resume_mode_keyboard(sess, False)  # chat_id defaults to 0
    row1 = kb.inline_keyboard[1]
    dests = [_destination(bot, btn.callback_data, chat_id=0) for btn in row1]
    assert (sess.name, "resume-cancel") in dests


# ---- _make_cb: deleted by this ship -----------------------------------

def test_make_cb_no_longer_exists(bot):
    """`_make_cb` and its overflow assertion are gone — after the switch
    to `session_cb`'s indexed form, nothing can overflow, so the guard
    that could never fire was dead code (design.md). A missing-attribute
    error is the whole point here, not an `AssertionError` from calling
    it — entrypoints.md is explicit that Testers must not rely on
    `AssertionError` from this name."""
    assert not hasattr(bot, "_make_cb")


# ---- Allow always only with a standing rule (2.1.259 guard) ----------------

def _labels(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def test_permission_keyboard_offers_allow_always_only_with_a_rule(bot):
    """Since Claude Code 2.1.259 the dialog's second row without a rule is
    "Yes, and switch to auto mode" — the button that would select it must
    not exist. Allow / Deny / Stop are always there."""
    sess = _sess(bot)
    assert any("Allow always" in lbl for lbl in _labels(
        bot._build_permission_keyboard(sess, always_available=True)))
    for flag in (False, None):
        labels = _labels(bot._build_permission_keyboard(sess, always_available=flag))
        assert not any("Allow always" in lbl for lbl in labels), (flag, labels)
        assert any(lbl.endswith("Allow") for lbl in labels), labels
        assert any("Deny" in lbl for lbl in labels), labels
        assert any("Stop" in lbl for lbl in labels), labels


def test_permission_keyboard_reads_the_flag_from_pending_permission(bot):
    sess = _sess(bot)
    sess.pending_permission = {"tool_summary": "Bash: x",
                               "tool_info": {"name": "Bash", "always_available": True}}
    assert any("Allow always" in lbl for lbl in _labels(bot._build_permission_keyboard(sess)))
    sess.pending_permission = {"tool_summary": "Bash: x",
                               "tool_info": {"name": "Bash", "always_available": False}}
    assert not any("Allow always" in lbl for lbl in _labels(bot._build_permission_keyboard(sess)))
    sess.pending_permission = {"tool_summary": "Bash: x", "tool_info": {"name": "Bash"}}
    assert not any("Allow always" in lbl for lbl in _labels(bot._build_permission_keyboard(sess)))
    sess.pending_permission = None
    assert not any("Allow always" in lbl for lbl in _labels(bot._build_permission_keyboard(sess)))
