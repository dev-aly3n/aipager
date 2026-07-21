"""Integration tests: SC16, SC17 — Allow always keyboard and keystroke.

SC16: Permission keyboard has exactly 2 rows; row 0 = [Allow, Deny], row 1 = [Allow always, Stop].
SC17: Tapping "Allow always" sends exactly Down, Down, Enter with 0.1s delays.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from aipager.state import SessionRegistry, Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_query(callback_data, *, user_id=12345, message_id=42):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.message.chat = MagicMock()
    query.message.chat.id = -100
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = -100
    return update, query


def _make_bot(*, registry=None):
    from aipager.bot import TelegramBot
    if registry is None:
        registry = SessionRegistry()
    bot = TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    return bot


# --------------------------------------------------------------------------- #
# SC16 — Permission keyboard: 2 rows, correct buttons                         #
# --------------------------------------------------------------------------- #

def test_sc16_permission_keyboard_has_exactly_two_rows():
    """SC16: _build_permission_keyboard must return InlineKeyboardMarkup with
    exactly 2 rows."""
    bot = _make_bot()
    kb = bot._build_permission_keyboard("claude-ben")
    assert len(kb.inline_keyboard) == 2, (
        f"Permission keyboard must have 2 rows; got {len(kb.inline_keyboard)}"
    )


def test_sc16_permission_keyboard_row0_is_allow_and_deny():
    """SC16: Row 0 must have exactly 2 buttons: Allow (not always) and Deny."""
    bot = _make_bot()
    kb = bot._build_permission_keyboard("claude-ben")
    row0 = kb.inline_keyboard[0]

    assert len(row0) == 2, f"Row 0 must have 2 buttons; got {len(row0)}"

    labels = [btn.text for btn in row0]
    cbs = [btn.callback_data for btn in row0]

    # Must have Allow (but NOT Allow always)
    assert any("Allow" in l and "always" not in l.lower() for l in labels), (
        f"Row 0 must have Allow (not always); labels: {labels}"
    )
    # Must have Deny
    assert any("Deny" in l for l in labels), (
        f"Row 0 must have Deny; labels: {labels}"
    )
    # Callback data
    assert "claude-ben:allow" in cbs, f"Row 0 must have :allow callback; cbs: {cbs}"
    assert "claude-ben:deny" in cbs, f"Row 0 must have :deny callback; cbs: {cbs}"


def test_sc16_permission_keyboard_row1_is_allow_always_and_stop():
    """SC16: Row 1 must have exactly 2 buttons: Allow always and Stop."""
    bot = _make_bot()
    kb = bot._build_permission_keyboard("claude-ben")
    row1 = kb.inline_keyboard[1]

    assert len(row1) == 2, f"Row 1 must have 2 buttons; got {len(row1)}"

    labels = [btn.text for btn in row1]
    cbs = [btn.callback_data for btn in row1]

    # Must have Allow always
    assert any("Allow always" in l or "always" in l.lower() for l in labels), (
        f"Row 1 must have Allow always; labels: {labels}"
    )
    # Must have Stop
    assert any("Stop" in l for l in labels), (
        f"Row 1 must have Stop; labels: {labels}"
    )
    # Callback data
    assert "claude-ben:allow_always" in cbs, (
        f"Row 1 must have :allow_always callback; cbs: {cbs}"
    )
    assert "claude-ben:stop" in cbs, (
        f"Row 1 must have :stop callback; cbs: {cbs}"
    )


def test_sc16_permission_keyboard_row1_col0_is_allow_always():
    """SC16: First button of row 1 must be Allow always (🟢), not Stop."""
    bot = _make_bot()
    kb = bot._build_permission_keyboard("claude-ben")
    row1 = kb.inline_keyboard[1]

    first_label = row1[0].text
    assert "always" in first_label.lower() or "🟢" in first_label, (
        f"Row 1 col 0 must be Allow always; got '{first_label}'"
    )


def test_sc16_permission_keyboard_allow_always_icon():
    """SC16: Allow always button must have 🟢 icon (visually distinct from ✅ Allow)."""
    bot = _make_bot()
    kb = bot._build_permission_keyboard("claude-ben")
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    allow_always_btn = next(
        (b for b in all_buttons if "allow_always" in (b.callback_data or "")), None
    )
    assert allow_always_btn is not None, "Must have allow_always button"
    assert "🟢" in allow_always_btn.text, (
        f"Allow always must have 🟢 icon; got '{allow_always_btn.text}'"
    )


# --------------------------------------------------------------------------- #
# SC17 — Allow always sends Down + Down + Enter with 0.1s delays              #
# --------------------------------------------------------------------------- #

def test_sc17_allow_always_sends_exactly_down_down_enter():
    """SC17: Tapping Allow always must inject keys in order: Down, Down, Enter."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-ben"] = sess

    key_calls = []

    async def mock_send_keys(session_name, key):
        key_calls.append(key)
        return True

    async def mock_is_alive(name):
        return True

    update, query = _make_query("claude-ben:allow_always")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive):
        _run(bot._handle_callback(update, MagicMock()))

    assert key_calls == ["Down", "Down", "Enter"], (
        f"allow_always must send ['Down', 'Down', 'Enter']; got {key_calls}"
    )


def test_sc17_allow_always_sends_three_keys_not_two_not_four():
    """SC17: Boundary — exactly 3 send_keys calls (not 2=deny, not 4=extra)."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-ben"] = sess

    key_calls = []

    async def mock_send_keys(session_name, key):
        key_calls.append(key)
        return True

    async def mock_is_alive(name):
        return True

    update, query = _make_query("claude-ben:allow_always")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive):
        _run(bot._handle_callback(update, MagicMock()))

    assert len(key_calls) == 3, (
        f"allow_always must send exactly 3 keys; got {len(key_calls)}: {key_calls}"
    )


def test_sc17_allow_always_uses_sleep_between_keystrokes():
    """SC17: allow_always must use asyncio.sleep (0.1s) between keystrokes."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-ben"] = sess

    sleep_calls = []
    key_calls = []

    async def mock_send_keys(session_name, key):
        key_calls.append(("key", key))
        return True

    async def mock_sleep(seconds):
        sleep_calls.append(seconds)
        key_calls.append(("sleep", seconds))

    async def mock_is_alive(name):
        return True

    update, query = _make_query("claude-ben:allow_always")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive), \
         patch("asyncio.sleep", side_effect=mock_sleep):
        _run(bot._handle_callback(update, MagicMock()))

    assert len(sleep_calls) >= 1, (
        f"allow_always must use asyncio.sleep; got sleep_calls={sleep_calls}"
    )
    assert all(abs(s - 0.1) < 0.01 for s in sleep_calls), (
        f"Sleep intervals must be 0.1s; got {sleep_calls}"
    )


# --------------------------------------------------------------------------- #
# Consistency: Allow and Deny use different keystroke counts                  #
# --------------------------------------------------------------------------- #

def test_allow_sends_only_enter():
    """Allow (not always) must send only Enter — no Down navigation."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-ben"] = sess

    key_calls = []

    async def mock_send_keys(session_name, key):
        key_calls.append(key)
        return True

    async def mock_is_alive(name):
        return True

    update, query = _make_query("claude-ben:allow")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive):
        _run(bot._handle_callback(update, MagicMock()))

    # Allow should NOT contain Down (no navigation needed)
    assert "Down" not in key_calls, (
        f"Allow must not send Down; got {key_calls}"
    )
    assert "Enter" in key_calls, f"Allow must send Enter; got {key_calls}"


def test_allow_always_has_more_down_presses_than_deny():
    """Allow always (Down+Down+Enter) must send more Down presses than Deny (Down+Enter)."""
    bot = _make_bot()

    async def mock_is_alive(name):
        return True

    # Deny key calls
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-ben"] = sess
    deny_keys = []

    async def mock_send_deny(session_name, key):
        deny_keys.append(key)
        return True

    update, query = _make_query("claude-ben:deny")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_deny), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive):
        _run(bot._handle_callback(update, MagicMock()))

    # Allow always key calls
    sess2 = TrackedSession(name="claude-ben2", label="ben2", status=Status.INTERACTIVE)
    bot.registry._sessions["claude-ben2"] = sess2
    aa_keys = []

    async def mock_send_aa(session_name, key):
        aa_keys.append(key)
        return True

    update2, query2 = _make_query("claude-ben2:allow_always")
    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_aa), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive):
        _run(bot._handle_callback(update2, MagicMock()))

    deny_downs = deny_keys.count("Down")
    aa_downs = aa_keys.count("Down")
    assert aa_downs > deny_downs, (
        f"Allow always must send more Down presses than Deny; "
        f"allow_always={aa_downs}, deny={deny_downs}"
    )
