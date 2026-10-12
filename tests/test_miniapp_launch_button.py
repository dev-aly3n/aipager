"""The permanent Mini App launch surface: the chat menu button. The
reply keyboard had an App button too until 2026-10-12, when it was
removed (a Mini App opened from a keyboard button gets no init data);
the tests below pin that no reply keyboard carries one.

The menu button hands out the Mini App's public URL, which is usable over the raw
internet once obtained (see ``AuthMixin._is_personal_mode_operator``) —
so *which chats get it* is the security property under test here, not a
detail. The same class of leak was already caught once on ``/app``.

Nothing here touches the real Telegram API: the bot's ``_app.bot`` is
the mocked double from ``mk_bot()``.
"""

from unittest.mock import AsyncMock

import pytest

from aipager.bot import group_intake
from aipager.config import APP_BUTTON


def _scope(chat_id, label):
    from aipager.scope import Member, Scope
    return Scope(chat_id=chat_id, kind="dm" if chat_id > 0 else "group",
                 label=label,
                 members=(Member(id=abs(chat_id), label="u", role="user"),))


def _menu_calls(bot):
    return bot._app.bot.set_chat_menu_button.await_args_list


@pytest.fixture(autouse=True)
def _reachable_url(monkeypatch):
    """publish_miniapp_button now probes the URL before advertising it, so a
    dead URL publishes no button at all. These tests are about *which chats
    get a button and which object is sent*, not about reachability — the
    URLs they pass are deliberately fake and would never resolve. Treat
    them as reachable so each test still exercises what its name says.

    Autouse so a test added later cannot silently start asserting on a
    no-button-because-unreachable result and think it proved something about
    scope filtering. The reachability behaviour itself is pinned separately
    in tests/test_miniapp_hardening.py.
    """
    monkeypatch.setattr("aipager.miniapp.tunnel.probe_public_url",
                        AsyncMock(return_value=True))


@pytest.fixture
def bot(mk_bot):
    b = mk_bot()
    b._app.bot.set_chat_menu_button = AsyncMock()
    return b


# ===== who gets the button ================================================

def test_the_default_menu_button_is_never_set(bot, run_async, monkeypatch):
    """THE rule. `set_chat_menu_button` with no chat_id sets the bot's
    *default* menu button, which would show the Mini App — and so
    disclose the tunnel hostname — to anyone who merely opens a chat
    with the bot, member or not."""
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    run_async(bot.publish_miniapp_button("https://tunnel.example/"))

    assert _menu_calls(bot), "no menu button was published at all"
    for call in _menu_calls(bot):
        assert "chat_id" in call.kwargs, (
            "set_chat_menu_button called without chat_id — that sets the "
            "bot-wide default and leaks the URL to every chat"
        )
        assert call.kwargs["chat_id"] is not None


def test_each_dm_scope_gets_the_button(bot, mk_bot, run_async):
    b = mk_bot(scopes=[_scope(100, "ana"), _scope(200, "ben")])
    b._app.bot.set_chat_menu_button = AsyncMock()

    run_async(b.publish_miniapp_button("https://tunnel.example/"))

    assert {c.kwargs["chat_id"] for c in _menu_calls(b)} == {100, 200}
    for call in _menu_calls(b):
        button = call.kwargs["menu_button"]
        assert button.type == "web_app"
        assert button.web_app.url == "https://tunnel.example/"
        assert button.text == APP_BUTTON


def test_group_scopes_are_skipped(bot, mk_bot, run_async):
    """Every Mini App launch surface is private-chat-only in the Bot
    API; a group chat has no menu button to set."""
    b = mk_bot(scopes=[_scope(100, "ana"), _scope(-100200300, "team")])
    b._app.bot.set_chat_menu_button = AsyncMock()

    run_async(b.publish_miniapp_button("https://tunnel.example/"))

    assert {c.kwargs["chat_id"] for c in _menu_calls(b)} == {100}


def test_a_group_chat_id_in_personal_mode_is_skipped(bot, run_async, monkeypatch):
    """Personal mode's single CHAT_ID may be a group — same reason."""
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "-1001234567")

    run_async(bot.publish_miniapp_button("https://tunnel.example/"))

    assert _menu_calls(bot) == []


@pytest.mark.parametrize("chat_id", ["", "not-an-int", None])
def test_an_unusable_chat_id_publishes_nothing(bot, run_async, monkeypatch, chat_id):
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", chat_id)

    run_async(bot.publish_miniapp_button("https://tunnel.example/"))

    assert _menu_calls(bot) == []


# ===== taking it down again ===============================================

def test_no_url_restores_the_commands_menu(bot, run_async, monkeypatch):
    """A button left pointing at a port nothing is listening on survives
    every restart and opens a broken page — worse than no button."""
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    run_async(bot.publish_miniapp_button(""))

    assert len(_menu_calls(bot)) == 1
    assert _menu_calls(bot)[0].kwargs["menu_button"].type == "commands"


def test_publishing_records_the_url_for_the_keyboard(bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    run_async(bot.publish_miniapp_button("https://tunnel.example/"))
    assert bot._miniapp_url == "https://tunnel.example/"

    run_async(bot.publish_miniapp_button(""))
    assert bot._miniapp_url == ""


# ===== failures must not take startup down ================================

def test_one_failing_chat_does_not_cost_the_others_their_button(mk_bot, run_async):
    """This runs during startup. A chat that has blocked the bot must
    not stop the remaining scopes — or the daemon."""
    b = mk_bot(scopes=[_scope(100, "ana"), _scope(200, "ben"), _scope(300, "cy")])
    calls = []

    async def flaky(*, chat_id, menu_button):
        calls.append(chat_id)
        if chat_id == 200:
            raise RuntimeError("Forbidden: bot was blocked by the user")

    b._app.bot.set_chat_menu_button = AsyncMock(side_effect=flaky)

    run_async(b.publish_miniapp_button("https://tunnel.example/"))

    assert calls == [100, 200, 300], "a failure stopped the remaining chats"


# ===== no Mini App button on the reply keyboard ===========================

def _web_app_buttons(kb):
    return [btn for row in kb.keyboard for btn in row
            if getattr(btn, "web_app", None) is not None]


@pytest.mark.parametrize("chat_id", [555, -1001234567], ids=["dm", "group"])
@pytest.mark.parametrize("level", ["main", "templates", "commands", "models"])
def test_no_reply_keyboard_carries_a_web_app_button(mk_bot, run_async, chat_id, level):
    """2026-10-12: Telegram gives a Mini App opened from a reply-keyboard
    `web_app` button no init data ("It is empty if the Mini App was
    launched from a keyboard button"), so it showed "Open this page from
    the Telegram app to sign in." and nothing else. No reply keyboard may
    carry one, even with the Mini App on; the menu button, /app and the
    inline Open buttons are the ways in."""
    b = mk_bot()
    b._miniapp_url = "https://tunnel.example/"

    run_async(b._send_keyboard(level=level, chat_id=chat_id))

    b._app.bot.send_message.assert_awaited_once()
    kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
    assert kb.keyboard, "no keyboard rows at all"
    assert _web_app_buttons(kb) == [], f"the {level} keyboard grew a web_app button"
    texts = {btn.text for row in kb.keyboard for btn in row}
    assert APP_BUTTON not in texts
    assert group_intake.mark_label(APP_BUTTON, chat_id) not in texts


def test_the_main_keyboards_last_row_is_templates_and_commands(mk_bot, run_async):
    b = mk_bot()
    b._miniapp_url = "https://tunnel.example/"

    run_async(b._send_keyboard(level="main", chat_id=555))

    kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
    assert [btn.text for btn in kb.keyboard[-1]] == ["Templates", "Commands"]


# ===== the shared URL resolver ============================================

def test_a_configured_url_wins_and_never_probes(run_async, monkeypatch):
    from aipager.miniapp import tunnel

    def boom():
        raise AssertionError("tailscale was probed despite a configured URL")

    monkeypatch.setattr(tunnel, "detect_public_url", boom)
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "https://pinned.example/")

    assert run_async(tunnel.resolve_public_url()) == "https://pinned.example/"


def test_the_blocking_probe_runs_off_the_event_loop(run_async, monkeypatch):
    """`detect_public_url` shells out to `tailscale status --json`
    synchronously. On the daemon's single shared loop a hung binary
    would stall every scope's messages, hooks and animations at once —
    a real stage-1 bug, not a hypothetical."""
    import threading

    from aipager.miniapp import tunnel

    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    seen = {}

    def probe():
        seen["thread"] = threading.current_thread().name
        return "https://tailnet.example/"

    monkeypatch.setattr(tunnel, "detect_public_url", probe)

    async def _run():
        seen["loop_thread"] = threading.current_thread().name
        return await tunnel.resolve_public_url()

    assert run_async(_run()) == "https://tailnet.example/"
    assert seen["thread"] != seen["loop_thread"], (
        "the blocking probe ran on the event loop's own thread"
    )


@pytest.mark.parametrize("value", ["", None, "http://insecure.example/", "ftp://x/"])
def test_anything_that_is_not_https_is_no_url(run_async, monkeypatch, value):
    """Telegram rejects a non-HTTPS Web App outright, so passing one on
    would produce a button that cannot work."""
    from aipager.miniapp import tunnel

    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "")
    monkeypatch.setattr(tunnel, "detect_public_url", lambda: value)

    assert run_async(tunnel.resolve_public_url()) == ""


# ===== the first keyboard of the run ======================================

def test_the_first_keyboard_of_the_run_goes_out_without_a_web_app_button(
    mk_bot, run_async, monkeypatch,
):
    """`TelegramBot.start()` ends by calling `_update_bot_commands()`,
    which in personal mode sends the persistent keyboard. With the Mini
    App URL already primed that first keyboard goes out at once, and it
    is the one that replaces a keyboard an older aipager left on the
    phone with a `web_app` App button (2026-10-12)."""
    b = mk_bot()
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    # keyboards.py binds its own CHAT_ID from config at import time;
    # patching only lifecycle's copy would read the real machine's chat.
    monkeypatch.setattr("aipager.bot.keyboards.CHAT_ID", "555")

    b.prime_miniapp_url("https://tunnel.example/")
    run_async(b._update_bot_commands())      # what start() ends with

    b._app.bot.send_message.assert_awaited()
    kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
    assert _web_app_buttons(kb) == []
    assert APP_BUTTON not in {btn.text for row in kb.keyboard for btn in row}


def test_priming_makes_no_telegram_call(mk_bot, run_async):
    """Priming only records the URL. The menu button still waits until
    the Mini App server is confirmed listening."""
    b = mk_bot()
    b._app.bot.set_chat_menu_button = AsyncMock()

    b.prime_miniapp_url("https://tunnel.example/")

    b._app.bot.set_chat_menu_button.assert_not_awaited()
    assert b._miniapp_url == "https://tunnel.example/"


def test_a_dm_scope_with_a_negative_chat_id_is_still_skipped():
    """rev-iter1-003. `scope.py` never ties `chat_id`'s sign to `kind` —
    a hand-written `aipager.yaml` can declare `kind: dm` with a negative
    id. The `> 0` test is therefore doing independent work, and the
    helper every other test uses derives `kind` FROM the sign, so none of
    them would notice if it were dropped.
    """
    from aipager.bot import TelegramBot
    from aipager.scope import Member, Scope
    from aipager.state import SessionRegistry

    bot = TelegramBot(SessionRegistry())
    bot.scopes = [
        Scope(chat_id=-4242, kind="dm", label="mislabelled",
              members=(Member(id=1, label="u", role="user"),)),
        Scope(chat_id=77, kind="dm", label="real",
              members=(Member(id=77, label="u", role="user"),)),
    ]

    assert bot._miniapp_button_chats() == [77]


def test_a_button_that_cannot_be_built_leaves_neither_surface_offering_one(
    bot, run_async, monkeypatch,
):
    """rev-iter2-003. The guard around the pre-call work exists so the
    "never raises" promise covers the whole method, not just the one line
    inside the loop's try. It must also fail closed: if the URL could not
    be turned into a button, the keyboard has to stop offering it too, or
    the two surfaces disagree about whether there is a Mini App.
    """
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    monkeypatch.setattr(
        type(bot), "_miniapp_button_chats",
        lambda self: (_ for _ in ()).throw(RuntimeError("boom")),
        raising=True,
    )

    run_async(bot.publish_miniapp_button("https://tunnel.example/"))

    bot._app.bot.set_chat_menu_button.assert_not_awaited()
    assert bot._miniapp_url == "", (
        "the keyboard would still offer a button the menu button does not have"
    )


def test_publishing_without_a_running_app_is_a_silent_no_op(
    mk_bot, run_async, monkeypatch, caplog,
):
    """The same `if not self._app: return` guard every sibling that
    touches `_app.bot` uses.

    Asserting on the log, not on the return: without the guard the
    per-chat `except Exception` swallows the AttributeError on `None`
    anyway, so the method still returns cleanly and still records the
    URL. The only observable difference is a warning per chat — which
    makes the log the thing worth asserting on. A version of this test
    that checked the return value passed with the guard deleted.
    """
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    b = mk_bot()
    b._app = None

    with caplog.at_level("WARNING"):
        run_async(b.publish_miniapp_button("https://tunnel.example/"))

    assert b._miniapp_url == "https://tunnel.example/"
    assert not [r for r in caplog.records if "menu button" in r.getMessage()], (
        "tried to call Telegram with no Application — logged a warning per chat"
    )
