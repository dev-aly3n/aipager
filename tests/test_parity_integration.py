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

    from aipager.bot import session_parity as sp

    kb = update.message.reply_text.await_args.kwargs["reply_markup"]
    data = _cb(kb)
    menu_cbs = [d for d in data if d.endswith(":menu")]
    assert len(menu_cbs) == 2, f"expected one ⋮ row per session, got {data}"

    # Assert on where each button LEADS, not on its literal encoding —
    # the callbacks carry an index now (they must, to fit 64 bytes), so a
    # string match would pin the encoding rather than the behaviour.
    resolved = []
    for cb in menu_cbs:
        idx = cb.split(":")[2]
        sess = sp._resolve_pref_index(bot, update.effective_chat.id, idx)
        assert sess is not None, f"{cb} resolves to nothing"
        resolved.append(sess.name)
    assert resolved == ["claude-alpha", "claude-beta"], (
        f"menu rows must follow the status block order, got {resolved}")
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


# ---- regressions found in review ----------------------------------------

def test_a_stale_menu_button_never_opens_another_session(
        mk_bot, mk_update, run_async):
    """Open session A's ⋮ menu, then B's, then tap A's still-visible
    Preferences button. It must reach A.

    The index table used to be overwritten per render, so A's button
    (encoded `_:spref:0`) resolved to whichever session was rendered
    last — a silent write to a session the user was not looking at.
    """
    from aipager.bot import session_parity as sp

    bot = mk_bot()
    for label in ("alpha", "beta"):
        s = bot.registry.get_or_create(f"claude-{label}")
        s.label = label

    a = bot.registry.get("claude-alpha")
    b = bot.registry.get("claude-beta")
    _t, kb_a = sp._render_session_menu(bot, 555, a)
    a_pref_cb = [x.callback_data for row in kb_a.inline_keyboard for x in row
                 if x.callback_data.startswith("_:spref:")][0]
    sp._render_session_menu(bot, 555, b)          # B renders second

    idx = a_pref_cb.rsplit(":", 1)[1]
    resolved = sp._resolve_pref_index(bot, 555, idx)

    assert resolved is not None, "A's own button stopped resolving"
    assert resolved.name == "claude-alpha", (
        f"A's Preferences button resolved to {resolved.name} — a stale "
        "index wrote to the wrong session")


def test_the_voice_restart_button_is_not_swallowed(mk_bot, run_async):
    """`__voice__:restart` collides with our own "restart" verb.

    Claiming it broke the "Restart daemon now" button shown after
    installing the voice extra — it answered "Session not found". The
    existing test for that button set up a mock but never asserted on
    it, so nothing caught the regression.
    """
    from unittest.mock import AsyncMock, MagicMock

    from aipager.bot import session_parity as sp

    bot = mk_bot()
    query = MagicMock()
    query.from_user = MagicMock(id=1)
    bot._safe_answer = AsyncMock()

    claimed = run_async(
        sp.handle_callback(bot, MagicMock(), query, "__voice__", "restart"))

    assert claimed is False, "session_parity swallowed a __voice__ callback"
    bot._safe_answer.assert_not_awaited()


# ---- callback_data must fit Telegram's 64-byte cap ----------------------

def test_every_session_callback_fits_the_64_byte_cap(mk_bot, run_async):
    """Telegram rejects the ENTIRE keyboard when any callback_data exceeds
    64 bytes — so one long-named session would break every button in the
    message, not just its own.

    `{name}:restart-confirm` reached 68 bytes for a realistic label plus
    its scope suffix. Internal names are themselves capped at 64, so no
    `{name}:<verb>` form can ever be safe; the buttons carry an index
    instead.
    """
    from aipager.bot import session_parity as sp

    bot = mk_bot()
    # A label at the practical maximum, plus the scope suffix a DM adds.
    name = "claude-" + ("w" * 40) + "__d256113222"
    sess = bot.registry.get_or_create(name)
    sess.label = "w" * 40

    for verb in ("menu", "restart", "restart-confirm", "restart-cancel",
                 "rename", "rename-cancel", "delete", "delete-confirm",
                 "delete-cancel", "diff", "menu-close"):
        cb = sp.session_cb(bot, 256113222, sess, verb)
        assert len(cb.encode()) <= 64, (
            f"{verb} produced {len(cb.encode())} bytes: {cb!r}")


def test_the_short_callback_form_resolves_back_to_the_session(
        mk_bot, run_async):
    """An index is only safe if it round-trips to the same session."""
    from aipager.bot import session_parity as sp

    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-roundtrip")
    sess.label = "roundtrip"
    cb = sp.session_cb(bot, 777, sess, "restart-confirm")

    _sentinel, rest = cb.split(":", 1)          # "_", "sx:<idx>:<verb>"
    parts = rest.split(":", 2)
    resolved = sp._resolve_pref_index(bot, 777, parts[1])

    assert resolved is not None and resolved.name == "claude-roundtrip"
    assert parts[2] == "restart-confirm", "the verb must survive intact"


def _welcome_text(bot):
    """`/start` sends the welcome and THEN the persistent keyboard, so
    `await_args` holds the keyboard's "⌨️" — not the text under test."""
    for call in bot._app.bot.send_message.await_args_list:
        if len(call.args) > 1 and "aipager" in str(call.args[1]):
            return call.args[1]
    raise AssertionError("/start sent no welcome message")


# ---- /start's help text and the registered command menu must agree ------

def test_start_help_mentions_the_session_management_commands(
        mk_bot, mk_update, run_async):
    """`/start` is the first thing a new user sees, and its command list
    was written before `/restart`, `/rename`, `/delete` and `/diff`
    existed. A command reachable only from the Mini App (or only from
    Telegram's `/` menu, which is a separate surface) is exactly the
    parity gap this feature exists to close.
    """
    bot = mk_bot()
    update = mk_update("/start", chat_id=555)

    run_async(bot._handle_start_cmd(update, MagicMock()))

    text = _welcome_text(bot)
    for cmd in ("/status", "/stop", "/restart", "/rename", "/diff",
                "/kill", "/delete", "/settings", "/perms", "/new"):
        assert cmd in text, f"{cmd} is missing from the /start help"


def test_start_help_never_advertises_an_unregistered_command(mk_bot, mk_update,
                                                             run_async):
    """The inverse drift: a command named in the welcome text that no
    handler is registered for. Telegram renders it as a tappable link,
    so it looks live and answers with nothing at all.
    """
    import re

    bot = mk_bot()
    update = mk_update("/start", chat_id=555)
    run_async(bot._handle_start_cmd(update, MagicMock()))
    text = _welcome_text(bot)

    registered = {c.command for c in type(bot)._command_list(set())}
    # Strip the HTML first: the welcome is parse_mode="HTML", and a
    # closing </b> looks exactly like a "/b" command to the scanner.
    plain = re.sub(r"<[^>]+>", "", text)
    mentioned = set(re.findall(r"(?<![\w/])/([a-z]+)", plain))
    unknown = mentioned - registered
    assert not unknown, (
        f"/start advertises command(s) with no registration: {sorted(unknown)}")
