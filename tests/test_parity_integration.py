"""The wiring the three streams could not test themselves.

Each stream built its module in an isolated worktree without the
shared-file registration lines, so nothing exercised the lines that make
those modules reachable: the `/status` ⋮ rows, the `/settings` entry
point, the `/new` branch, the command registrations, and the App button
placement. That wiring is exactly where a parity exercise silently ends
up with a module nobody can reach.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from aipager.bot import handlers as handlers_mod
from aipager.state import Status


def _cb(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def _texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


# ---- /status carries a ⋮ row per rendered session -----------------------

def test_status_offers_a_menu_row_per_session_in_render_order(
        mk_bot, mk_update, run_async, monkeypatch):
    bot = mk_bot()
    for label in ("alpha", "beta"):
        s = bot.registry.get_or_create(f"claude-{label}")
        s.label = label
        bot.registry.transition(f"claude-{label}", Status.IDLE)
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        lambda n: _true())
    update = mk_update("/status")

    run_async(bot._handle_status(update, MagicMock()))

    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    data = _cb(kb)
    assert "claude-alpha:menu" in data
    assert "claude-beta:menu" in data
    assert data.index("claude-alpha:menu") < data.index("claude-beta:menu"), (
        "menu rows must follow the same order as the status blocks")
    assert all(t.startswith("⋮ ") for t in _texts(kb) if "⋮" in t)


async def _true():
    return True


def test_clear_gone_stays_last_when_present(
        mk_bot, mk_update, run_async, monkeypatch):
    """Anyone used to where that button sits must still find it there."""
    bot = mk_bot()
    s = bot.registry.get_or_create("claude-dead")
    s.label = "dead"
    bot.registry.transition("claude-dead", Status.GONE)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", lambda n: _false())
    update = mk_update("/status")

    run_async(bot._handle_status(update, MagicMock()))

    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert _cb(kb)[-1] == "_:clear_gone"


async def _false():
    return False


# ---- the App button row --------------------------------------------------

def test_app_row_is_empty_in_a_group(mk_bot, mk_update):
    """Telegram rejects an ENTIRE keyboard carrying a web_app button in a
    group — one misplaced button costs every other button in the message."""
    bot = mk_bot()
    bot._miniapp_url = "https://tunnel.example/"
    update = mk_update("/settings")
    update.effective_chat.type = "group"
    assert bot._app_button_row(update) == []


def test_app_row_is_empty_without_a_url(mk_bot, mk_update):
    bot = mk_bot()
    bot._miniapp_url = ""
    update = mk_update("/settings")
    update.effective_chat.type = "private"
    assert bot._app_button_row(update) == []


def test_app_row_present_in_a_private_chat_with_a_url(mk_bot, mk_update):
    bot = mk_bot()
    bot._miniapp_url = "https://tunnel.example/"
    update = mk_update("/settings")
    update.effective_chat.type = "private"
    row = bot._app_button_row(update)
    assert len(row) == 1 and len(row[0]) == 1
    assert row[0][0].web_app.url == "https://tunnel.example/"


# ---- the modules are actually reachable ----------------------------------

def test_handlers_imports_both_flow_modules():
    """A module nobody dispatches to is dead code, and the streams could
    not verify this themselves."""
    assert hasattr(handlers_mod, "new_flow")
    assert hasattr(handlers_mod, "session_parity")


def test_settings_root_reaches_per_session_preferences():
    from aipager.bot.settings_menu import render_settings_root
    _text, kb = render_settings_root(1)
    assert "_:spref" in _cb(kb), (
        "the per-session renderer is unreachable from /settings")
