"""The permanent Mini App launch surfaces: the chat menu button and the
reply-keyboard button.

Both hand out the Mini App's public URL, which is usable over the raw
internet once obtained (see ``AuthMixin._is_personal_mode_operator``) —
so *which chats get it* is the security property under test here, not a
detail. The same class of leak was already caught once on ``/app``.

Nothing here touches the real Telegram API: the bot's ``_app.bot`` is
the mocked double from ``mk_bot()``.
"""

from unittest.mock import AsyncMock

import pytest

from aipager.config import APP_BUTTON


def _scope(chat_id, label):
    from aipager.scope import Member, Scope
    return Scope(chat_id=chat_id, kind="dm" if chat_id > 0 else "group",
                 label=label,
                 members=(Member(id=abs(chat_id), label="u", role="user"),))


def _menu_calls(bot):
    return bot._app.bot.set_chat_menu_button.await_args_list


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


# ===== the reply-keyboard button ==========================================

def test_the_keyboard_offers_the_app_button_in_a_dm(mk_bot, run_async):
    b = mk_bot()
    b._miniapp_url = "https://tunnel.example/"

    run_async(b._send_keyboard(level="main", chat_id=555))

    kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
    app_buttons = [btn for row in kb.keyboard for btn in row if btn.text == APP_BUTTON]
    assert len(app_buttons) == 1
    assert app_buttons[0].web_app.url == "https://tunnel.example/"


def test_the_keyboard_omits_the_app_button_in_a_group(mk_bot, run_async):
    """Telegram rejects a keyboard containing a `web_app` button in a
    group — the WHOLE keyboard, not just that button. Including it would
    cost the operator every other button in the chat."""
    b = mk_bot()
    b._miniapp_url = "https://tunnel.example/"

    run_async(b._send_keyboard(level="main", chat_id=-1001234567))

    kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
    texts = {btn.text for row in kb.keyboard for btn in row}
    assert APP_BUTTON not in texts
    assert "status" in texts, "the rest of the keyboard must survive"


def test_the_keyboard_omits_the_app_button_with_no_miniapp(mk_bot, run_async):
    b = mk_bot()
    b._miniapp_url = ""

    run_async(b._send_keyboard(level="main", chat_id=555))

    kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
    texts = {btn.text for row in kb.keyboard for btn in row}
    assert APP_BUTTON not in texts


def test_no_web_app_button_ever_reaches_a_non_main_keyboard(mk_bot, run_async):
    """Only the main level gained a button; the sub-menus are unchanged."""
    b = mk_bot()
    b._miniapp_url = "https://tunnel.example/"

    for level in ("templates", "commands", "models"):
        run_async(b._send_keyboard(level=level, chat_id=555))
        kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
        for row in kb.keyboard:
            for btn in row:
                assert btn.web_app is None, f"{level} keyboard grew a web_app button"


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


# ===== the keyboard must know the URL before the FIRST keyboard goes out ==

def test_the_first_keyboard_of_the_run_already_offers_the_app_button(
    mk_bot, run_async, monkeypatch,
):
    """rev-iter1-001. `TelegramBot.start()` ends by calling
    `_update_bot_commands()`, which in personal mode sends the persistent
    keyboard immediately — long before the Mini App server has started and
    `publish_miniapp_button` has run.

    So the URL has to be known *before* start(), or the very first
    keyboard after every restart is missing its App button and nothing
    re-sends it until some unrelated event (a session created, a
    Templates→Back tap) happens to refresh it. That is the whole feature
    silently not working on the surface the operator looks at most.
    """
    b = mk_bot()
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    b.prime_miniapp_url("https://tunnel.example/")
    run_async(b._update_bot_commands())      # what start() ends with

    kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
    texts = {btn.text for row in kb.keyboard for btn in row}
    assert APP_BUTTON in texts, (
        "the first keyboard of the run has no App button — the URL was not "
        "known before start() sent it"
    )


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
