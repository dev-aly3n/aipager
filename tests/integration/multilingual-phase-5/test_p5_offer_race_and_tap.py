"""Black-box, fix iteration 2: the offer card is sent once even when the start
sweep and a person's message race, and a language picked on the card applies
in full (phase 5 item 3; design.md D9, D11; entrypoints.md ``language_launch``
"``offer_sweep`` shares the per-chat in-flight guard with ``after_update``",
and ``language_offer.handle_callback``).

Success criteria: "an existing DM owner with a supported non-English app gets
exactly one offer card"; an explicit choice keeps today's three refresh calls
(the ``/`` menu, one reply keyboard, the menu button); "Keep English" on a chat
that already reads English changes nothing on screen.

Partitions (race): which starts first = sweep / update; the first send =
ok / fails; updates during the sweep's send = 1 / many. Partitions (tap):
button = the language / Keep English; tapped = once / twice; a post-reply
hook for the same callback update = absent / present.

Every send yields 50 ms (a real wait inside the mock, the way the outbound
gate and the network do), so the two paths overlap. The sweep's wait goes
through the documented ``_sleep`` seam; asyncio is never patched.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences as prefs
from aipager.bot import language_launch
from aipager.i18n import persist
from tests import i18n_ml2_bb as bb
from tests.i18n_ml2_bb import callback, message, reply_keyboards

OWNER, BOB = 555, 2
PERSIAN = bb.PERSIAN


@pytest.fixture(autouse=True)
def _quick_sweep(monkeypatch):
    async def _yield(_seconds):
        await asyncio.sleep(0)
    monkeypatch.setattr(language_launch, "SWEEP_DELAY", 0.0)
    monkeypatch.setattr(language_launch, "_sleep", _yield)


@pytest.fixture
def upgraded(p5):
    p5.marker(OWNER)


def _upd(user, code):
    return message("hi", chat=user, user=user, language_code=code)


def _is_offer(kw):
    markup = kw.get("reply_markup")
    rows = getattr(markup, "inline_keyboard", None) or ()
    return any(str(b.callback_data).startswith("_:set:lang_offer:") for r in rows for b in r)


def _offer_attempts(p5, bot, chat=OWNER):
    return [s for s in p5.sends(bot, chat) if _is_offer(s)]


def _slow_sends(bot, *, fail_first=False):
    state = {"n": 0}

    async def _send(*_a, **_kw):
        state["n"] += 1
        await asyncio.sleep(0.05)
        if fail_first and state["n"] == 1:
            raise RuntimeError("network down")
        return MagicMock(message_id=4242)
    bot._app.bot.send_message = AsyncMock(side_effect=_send)


async def _until_a_send_began(bot):
    for _ in range(400):
        if bot._app.bot.send_message.await_count:
            return True
        await asyncio.sleep(0.005)
    return False


def _sweep_then_updates(bot, run_async, n_updates=1):
    """The sweep starts; while its offer send is in flight, *n_updates*
    messages from the owner arrive."""
    async def go():
        loop = asyncio.get_running_loop()
        sweep = loop.create_task(language_launch.offer_sweep(bot))
        began = await _until_a_send_began(bot)
        for _ in range(n_updates):
            await language_launch.after_update(bot, _upd(OWNER, "fa"), MagicMock())
        await sweep
        await language_launch.drain(bot)
        return began
    return run_async(go())


def _update_then_sweep(bot, run_async):
    """The owner's message starts its offer; while that send is in flight the
    start sweep runs."""
    async def go():
        await language_launch.after_update(bot, _upd(OWNER, "fa"), MagicMock())
        began = await _until_a_send_began(bot)
        await language_launch.offer_sweep(bot)
        await language_launch.drain(bot)
        return began
    return run_async(go())


# ---- the race: never two offers ------------------------------------------------------------

def test_the_sweeps_send_really_was_in_flight_when_the_message_came(bot, run_async, upgraded):
    """Control: the scenario below does overlap (otherwise it tests nothing)."""
    persist.set_seen(OWNER, "fa")
    _slow_sends(bot)
    assert _sweep_then_updates(bot, run_async) is True


def test_a_message_during_the_sweeps_offer_brings_no_second_offer(bot, run_async, p5,
                                                                   upgraded):
    persist.set_seen(OWNER, "fa")
    _slow_sends(bot)
    _sweep_then_updates(bot, run_async)
    assert len(_offer_attempts(p5, bot)) == 1


def test_many_messages_during_the_sweeps_offer_bring_no_second_offer(bot, run_async, p5,
                                                                     upgraded):
    persist.set_seen(OWNER, "fa")
    _slow_sends(bot)
    _sweep_then_updates(bot, run_async, n_updates=5)
    assert len(_offer_attempts(p5, bot)) == 1


def test_the_sweep_during_a_messages_offer_sends_no_second_one(bot, run_async, p5, upgraded):
    _slow_sends(bot)
    _update_then_sweep(bot, run_async)
    assert len(_offer_attempts(p5, bot)) == 1


def test_the_raced_offer_is_recorded_once_done(bot, run_async, upgraded):
    persist.set_seen(OWNER, "fa")
    _slow_sends(bot)
    _sweep_then_updates(bot, run_async)
    assert persist.offer_done(OWNER) is True


def test_after_the_race_a_restart_and_a_message_bring_nothing_more(bot, run_async, launch, p5,
                                                                   upgraded):
    persist.set_seen(OWNER, "fa")
    _slow_sends(bot)
    _sweep_then_updates(bot, run_async)
    p5.restart()
    launch(bot, _upd(OWNER, "fa"))
    assert len(_offer_attempts(p5, bot)) == 1


def test_a_failed_sweep_offer_does_not_block_a_later_one(bot, run_async, launch, upgraded):
    """The sweep's send fails: the chat is released and the next message may
    still offer (a failed send is not 'done')."""
    persist.set_seen(OWNER, "fa")
    _slow_sends(bot, fail_first=True)
    run_async(language_launch.offer_sweep(bot))
    launch(bot, _upd(OWNER, "fa"))
    assert persist.offer_done(OWNER) is True


def test_a_failed_sweep_offer_is_retried_by_exactly_one_later_send(bot, run_async, launch, p5,
                                                                    upgraded):
    persist.set_seen(OWNER, "fa")
    _slow_sends(bot, fail_first=True)
    run_async(language_launch.offer_sweep(bot))
    launch(bot, _upd(OWNER, "fa"))
    launch(bot, _upd(OWNER, "fa"))
    assert len(_offer_attempts(p5, bot)) == 2      # the failed one + one success


def test_the_race_for_one_chat_does_not_hold_back_another(bot, run_async, p5):
    """Two existing owners: the guard is per chat, so the sweep still offers
    to the second while the first one's message is handled."""
    p5.marker(OWNER, BOB)
    persist.set_seen(OWNER, "fa")
    persist.set_seen(BOB, "ru")
    _slow_sends(bot)
    _sweep_then_updates(bot, run_async)
    assert (len(_offer_attempts(p5, bot, OWNER)), len(_offer_attempts(p5, bot, BOB))) == (1, 1)


# ---- a language picked on the card applies in full ---------------------------------------------

@pytest.fixture
def offered(mk_bot, run_async, launch, p5, upgraded):
    """An upgraded owner with a Persian app has the offer card; the `/` menus
    were registered at start (English); every call record is then cleared."""
    b = bb.make_bot(mk_bot)
    run_async(b._update_bot_commands())
    launch(b, _upd(OWNER, "fa"))
    assert len(_offer_attempts(p5, b)) == 1
    tg = b._app.bot
    for m in (tg.send_message, tg.set_my_commands, tg.set_chat_menu_button):
        m.reset_mock()
    return b


def _tap(bot, run_async, token):
    update, _q = callback(f"_:set:lang_offer:{token}", chat=OWNER, user=OWNER,
                          language_code="fa")
    run_async(bot._handle_callback(update, MagicMock()))
    return update


def _menu_chats(bot):
    return [c.kwargs["scope"].chat_id for c in bot._app.bot.set_my_commands.await_args_list
            if getattr(c.kwargs.get("scope"), "chat_id", None) is not None]


def _button_chats(bot):
    return [c.kwargs.get("chat_id") for c in bot._app.bot.set_chat_menu_button.await_args_list]


def test_a_language_tap_sends_one_reply_keyboard(offered, run_async):
    _tap(offered, run_async, "fa")
    assert len(reply_keyboards(offered, OWNER)) == 1


def test_the_new_reply_keyboard_is_in_the_picked_language(offered, run_async):
    _tap(offered, run_async, "fa")
    (keyboard,) = reply_keyboards(offered, OWNER)
    assert PERSIAN.search(" ".join(keyboard))


def test_a_language_tap_refreshes_the_chats_menu(offered, run_async):
    _tap(offered, run_async, "fa")
    assert _menu_chats(offered) == [OWNER]


def test_the_refreshed_menu_is_in_the_picked_language(offered, run_async):
    _tap(offered, run_async, "fa")
    (call,) = [c for c in offered._app.bot.set_my_commands.await_args_list
               if getattr(c.kwargs.get("scope"), "chat_id", None) == OWNER]
    commands = call.args[0] if call.args else call.kwargs["commands"]
    assert any(PERSIAN.search(c.description) for c in commands)


def test_a_language_tap_refreshes_the_menu_button(offered, run_async):
    _tap(offered, run_async, "fa")
    assert _button_chats(offered) == [OWNER]


def test_the_post_reply_hook_after_a_tap_does_not_refresh_twice(offered, run_async):
    update = _tap(offered, run_async, "fa")

    async def hook():
        await language_launch.after_update(offered, update, MagicMock())
        await language_launch.drain(offered)
    run_async(hook())
    assert (_menu_chats(offered), len(reply_keyboards(offered, OWNER))) == ([OWNER], 1)


def test_a_second_tap_of_the_same_language_sends_no_second_keyboard(offered, run_async):
    _tap(offered, run_async, "fa")
    _tap(offered, run_async, "fa")
    assert len(reply_keyboards(offered, OWNER)) == 1


def test_keep_english_on_an_english_chat_sends_no_keyboard(offered, run_async):
    _tap(offered, run_async, "keep")
    assert reply_keyboards(offered, OWNER) == []


def test_keep_english_on_an_english_chat_leaves_the_menu(offered, run_async):
    _tap(offered, run_async, "keep")
    assert _menu_chats(offered) == []


def test_keep_english_on_an_english_chat_leaves_the_menu_button(offered, run_async):
    _tap(offered, run_async, "keep")
    assert _button_chats(offered) == []


def test_keep_english_still_stores_english(offered, run_async):
    _tap(offered, run_async, "keep")
    assert prefs.get_ui_language(OWNER) == "en"
