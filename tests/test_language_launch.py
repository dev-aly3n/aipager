"""The language launch (multilingual phase 5, design 7.1 and 7.4).

After each update in a private chat this install serves (group 1, after
the reply): the offer card once ever to a chat that existed before the
marker, the first-contact line once ever (its own message) to a chat new
since it, both at the lowest priority and never in a group; a language
moved by Telegram (no choice) refreshes the ``/`` menu and menu button
with no keyboard message. At start, the offer to existing chats whose
Telegram language an earlier run saw.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Update
from telegram.ext import TypeHandler

from aipager import i18n, preferences
from aipager.bot import language_launch, language_menu, report_offer, transport
from aipager.i18n import persist
from aipager.policy import load_policy
from aipager.scope import Member, Scope

DM, GROUP, BOB = 555, -1001, 777
OFFER_KEY = "_:set:lang_offer:"


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=DM, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"))),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=DM, label="aly", role="owner"),)),
    ])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    b._app.bot.set_my_commands = AsyncMock()
    b._app.bot.set_chat_menu_button = AsyncMock()
    b._send_keyboard = AsyncMock()
    b._on_language_changed = AsyncMock()
    return b


@pytest.fixture
def sends(monkeypatch):
    calls = []

    async def _send(_tg, chat_id, text, **kwargs):
        calls.append((chat_id, text, kwargs))
        return MagicMock(message_id=len(calls))

    monkeypatch.setattr(transport, "send_text", _send)
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)
    return calls


@pytest.fixture
def existing():
    return persist.ensure_launch_marker(people=[DM, BOB], prior_run=True)


@pytest.fixture
def fresh():
    return persist.ensure_launch_marker(people=(), prior_run=False)


def _update(chat, user, language_code):
    update = MagicMock()
    update.effective_chat = MagicMock(id=chat)
    update.effective_user = MagicMock(id=user, language_code=language_code)
    update.callback_query = None
    return update


def after(bot, run_async, chat=DM, user=DM, language_code="fa"):
    async def _go():
        await language_launch.after_update(bot, _update(chat, user, language_code), None)
        await language_launch.drain(bot)
    run_async(_go())


def _offers(sends):
    return [c for c in sends if c[2].get("reply_markup") is not None and any(
        b.callback_data.startswith(OFFER_KEY)
        for row in c[2]["reply_markup"].inline_keyboard for b in row)]


def _lowest_priority(kwargs):
    args = kwargs["rate_limit_args"]
    return (args["class"], args["kind"], kwargs["disable_notification"]) == (
        "ornament", "skip", True)


# ---- the offer (7.4) -----------------------------------------------------------------

def test_an_existing_owner_with_a_persian_app_gets_one_offer(bot, run_async, sends, existing):
    after(bot, run_async)
    [(chat, text, kwargs)] = sends
    assert chat == DM and "فارسی" in text
    assert _lowest_priority(kwargs)
    assert _offers(sends)
    assert persist.offer_done(DM)


def test_the_offer_never_comes_again_even_after_a_restart(bot, run_async, sends, existing):
    after(bot, run_async)
    after(bot, run_async)
    persist.reset()
    after(bot, run_async, language_code="ru")
    assert len(sends) == 1


def test_the_offer_never_comes_again_after_an_answer(bot, run_async, sends, existing):
    after(bot, run_async)
    preferences.set_ui_language(DM, "en")         # "Keep English"
    persist.reset()
    after(bot, run_async)
    assert len(sends) == 1


def test_no_offer_for_an_english_app_or_a_language_we_lack(bot, run_async, sends, existing):
    after(bot, run_async, language_code="en")
    after(bot, run_async, language_code="ja")
    assert sends == [] and not persist.offer_done(DM)


def test_no_offer_in_a_group(bot, run_async, sends, existing):
    after(bot, run_async, chat=GROUP, user=DM)
    assert sends == []


def test_no_offer_in_a_private_chat_this_install_does_not_serve(bot, run_async, sends,
                                                                existing):
    after(bot, run_async, chat=BOB, user=BOB)
    assert sends == [] and persist.seen(BOB) is None


def test_no_offer_when_the_sender_is_not_the_chats_person(bot, run_async, sends, existing):
    persist.set_seen(DM, "fa")              # the chat's person would get one
    after(bot, run_async, chat=DM, user=BOB, language_code="ru")
    assert sends == [] and persist.seen(BOB) is None


def test_no_offer_while_muted_and_one_once_clear(bot, run_async, sends, existing,
                                                monkeypatch):
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: False)
    after(bot, run_async)
    assert sends == [] and not persist.offer_done(DM)
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)
    after(bot, run_async)
    assert len(sends) == 1


def test_no_offer_to_a_chat_that_chose(bot, run_async, sends, existing):
    preferences.set_ui_language(DM, "auto")
    after(bot, run_async)
    assert sends == []


def test_no_offer_for_a_new_chat(bot, run_async, sends, fresh):
    after(bot, run_async)
    assert _offers(sends) == []


# ---- the first-contact line (7.1) ---------------------------------------------------

def test_a_new_chat_gets_the_line_once_as_its_own_message(bot, run_async, sends, fresh):
    after(bot, run_async, language_code="ru")
    [(chat, text, kwargs)] = sends
    assert chat == DM
    assert text == language_menu.first_contact_text(i18n.get("ru"))
    assert "reply_markup" not in kwargs
    assert _lowest_priority(kwargs)
    assert persist.first_contact_done(DM)
    after(bot, run_async, language_code="ru")
    persist.reset()
    after(bot, run_async, language_code="ru")
    assert len(sends) == 1


def test_never_the_line_in_english(bot, run_async, sends, fresh):
    after(bot, run_async, language_code="en")
    assert sends == [] and not persist.first_contact_done(DM)


def test_never_the_line_for_an_existing_chat(bot, run_async, sends, existing):
    after(bot, run_async, language_code="ru")
    assert all(text != language_menu.first_contact_text(i18n.get("ru"))
               for _c, text, _k in sends)
    assert not persist.first_contact_done(DM)
    # It gets the offer instead.
    assert len(_offers(sends)) == 1 and persist.offer_done(DM)


def test_never_the_line_in_a_group(bot, run_async, sends, fresh):
    after(bot, run_async, chat=GROUP, user=BOB, language_code="ru")
    assert sends == []


def test_never_the_line_after_a_choice(bot, run_async, sends, fresh):
    preferences.set_ui_language(DM, "ru")
    after(bot, run_async, language_code="ru")
    assert sends == []


def test_no_line_while_muted_and_one_once_clear(bot, run_async, sends, fresh, monkeypatch):
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: False)
    after(bot, run_async, language_code="ru")
    assert sends == [] and not persist.first_contact_done(DM)
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)
    after(bot, run_async, language_code="ru")
    assert len(sends) == 1 and persist.first_contact_done(DM)


@pytest.mark.parametrize("result", [None, transport.MUTED, transport.SKIPPED])
def test_a_line_that_did_not_go_out_is_not_recorded(bot, run_async, fresh, monkeypatch,
                                                    result):
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)

    async def _send(*_a, **_k):
        return result

    monkeypatch.setattr(transport, "send_text", _send)
    after(bot, run_async, language_code="ru")
    assert not persist.first_contact_done(DM)


def test_a_failed_send_never_raises(bot, run_async, fresh, monkeypatch):
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)

    async def _boom(*_a, **_k):
        raise RuntimeError("network")

    monkeypatch.setattr(transport, "send_text", _boom)
    after(bot, run_async, language_code="ru")
    assert not persist.first_contact_done(DM)


def test_the_start_screen_no_longer_carries_the_line(mk_bot, run_async, mk_update, fresh):
    tg = mk_bot()
    tg._send_keyboard = AsyncMock()
    update = mk_update("/start", chat_id=DM, user_id=DM)
    update.effective_user.language_code = "ru"
    run_async(tg._send_home(update))
    text = tg._app.bot.send_message.await_args.args[1]
    assert text.startswith("👋")


# ---- a language moved by Telegram -------------------------------------------------

def test_a_detected_change_refreshes_the_menu_without_a_keyboard(bot, run_async, sends,
                                                                 fresh):
    bot._registered_scope_lang[DM] = "en"
    after(bot, run_async, language_code="ru")
    bot._on_language_changed.assert_awaited_once_with(DM, keyboard=False)


def test_no_refresh_when_the_language_did_not_move(bot, run_async, sends, existing):
    bot._registered_scope_lang[DM] = "en"
    after(bot, run_async, language_code="ru")
    bot._on_language_changed.assert_not_awaited()


def test_the_refresh_sends_no_keyboard(mk_bot, run_async):
    tg = mk_bot(scopes=[Scope(chat_id=DM, kind="dm", label="dm",
                              members=(Member(id=DM, label="a", role="owner"),))])
    tg._send_keyboard = AsyncMock()
    tg._update_bot_commands_per_scope = AsyncMock()
    tg._set_menu_button_for = AsyncMock()
    run_async(tg._on_language_changed(DM, keyboard=False))
    tg._send_keyboard.assert_not_awaited()
    tg._update_bot_commands_per_scope.assert_awaited_once()
    tg._set_menu_button_for.assert_awaited_once_with(DM)
    run_async(tg._on_language_changed(DM))
    tg._send_keyboard.assert_awaited_once()


# ---- start, the sweep and stop ------------------------------------------------------

def test_the_handler_is_registered_after_the_replies(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    tg = mk_bot()
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda h, *a, **k: registered.append(
        (h, k.get("group", a[0] if a else 0))))
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater.start_polling = AsyncMock()
    tg._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    tg._update_bot_commands = AsyncMock()

    async def _go():
        await tg.start()
        # start()'s background tasks: settled here, never left pending.
        tasks = [tg._language_offer_task, tg._descriptions_task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    run_async(_go())
    ours = [(h, g) for h, g in registered if isinstance(h, TypeHandler)
            and getattr(h.callback, "func", None) is language_launch.after_update]
    assert len(ours) == 1
    handler, group = ours[0]
    assert handler.type is Update and handler.callback.args == (tg,) and group == 1
    assert group > max(g for _h, g in registered if g != group)


def test_the_sweep_offers_to_an_existing_chat_seen_before(bot, run_async, sends, existing,
                                                          monkeypatch):
    slept = []

    async def _no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(language_launch, "_sleep", _no_sleep)
    persist.set_seen(DM, "fa")
    run_async(language_launch.offer_sweep(bot))
    assert len(_offers(sends)) == 1 and persist.offer_done(DM)
    assert slept == [language_launch.SWEEP_DELAY]
    run_async(language_launch.offer_sweep(bot))
    assert len(sends) == 1


@pytest.mark.parametrize("seen, stored", [("en", ""), (None, ""), ("fa", "en"), ("fa", "auto")])
def test_the_sweep_skips_what_does_not_fit(bot, run_async, sends, existing, monkeypatch,
                                           seen, stored):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(language_launch, "_sleep", _no_sleep)
    persist.set_seen(DM, seen)
    if stored:
        preferences.set_ui_language(DM, stored)
    run_async(language_launch.offer_sweep(bot))
    assert sends == []


def test_one_step_per_chat_at_a_time_and_stop_cancels_it(bot, run_async, fresh, monkeypatch):
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)
    gate = []

    async def _blocked(*_a, **_k):
        gate.append(1)
        await asyncio.sleep(3600)

    monkeypatch.setattr(transport, "send_text", _blocked)

    async def _go():
        upd = _update(DM, DM, "ru")
        await language_launch.after_update(bot, upd, None)
        await language_launch.after_update(bot, upd, None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        tasks = list(bot._language_launch_tasks)
        assert len(tasks) == 1
        language_launch.cancel_tasks(bot)
        await asyncio.gather(*tasks, return_exceptions=True)
        return tasks

    tasks = run_async(_go())
    assert gate == [1] and all(t.cancelled() for t in tasks)
    assert bot._language_launch_in_flight == set()
    assert not persist.first_contact_done(DM)


def test_stop_cancels_the_launch_tasks(mk_bot, run_async, monkeypatch):
    tg = mk_bot()
    cancelled = []
    monkeypatch.setattr(language_launch, "cancel_tasks", lambda b: cancelled.append(b))
    tg._app.updater.stop = AsyncMock()
    tg._app.stop = AsyncMock()
    tg._app.shutdown = AsyncMock()

    async def _go():
        tg._language_offer_task = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        await tg.stop()
        await asyncio.sleep(0)
        return tg._language_offer_task

    task = run_async(_go())
    assert cancelled == [tg] and task.cancelled()


# ---- the sweep and an update never both offer (review rev-iter1-001) -------------------

@pytest.fixture
def slow_sends(monkeypatch):
    """A send that yields for a while, like one waiting in the ORNAMENT
    queue, so the other path can run meanwhile."""
    calls = []

    async def _send(_tg, chat_id, text, **kwargs):
        calls.append((chat_id, text, kwargs))
        await asyncio.sleep(0.05)
        return MagicMock(message_id=len(calls))

    monkeypatch.setattr(transport, "send_text", _send)
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)
    return calls


@pytest.fixture
def no_sweep_delay(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(language_launch, "_sleep", _no_sleep)


def test_an_update_during_the_sweeps_offer_sends_no_second_one(
        bot, run_async, slow_sends, existing, no_sweep_delay):
    persist.set_seen(DM, "fa")

    async def _go():
        sweep = asyncio.get_running_loop().create_task(language_launch.offer_sweep(bot))
        while not slow_sends:                      # the sweep's send is under way
            await asyncio.sleep(0.001)
        await language_launch.after_update(bot, _update(DM, DM, "fa"), None)
        await language_launch.drain(bot)
        await sweep

    run_async(_go())
    assert len(_offers(slow_sends)) == 1 and persist.offer_done(DM)
    assert bot._language_launch_in_flight == set()


def test_the_sweep_during_an_updates_offer_sends_no_second_one(
        bot, run_async, slow_sends, existing, no_sweep_delay):
    persist.set_seen(DM, "fa")

    async def _go():
        await language_launch.after_update(bot, _update(DM, DM, "fa"), None)
        while not slow_sends:                      # the update's send is under way
            await asyncio.sleep(0.001)
        await language_launch.offer_sweep(bot)
        await language_launch.drain(bot)

    run_async(_go())
    assert len(_offers(slow_sends)) == 1 and persist.offer_done(DM)


def test_the_sweep_releases_the_chat_when_its_offer_fails(
        bot, run_async, existing, no_sweep_delay, monkeypatch):
    monkeypatch.setattr(report_offer, "dm_clear", lambda chat_id: True)

    async def _boom(*_a, **_k):
        raise RuntimeError("network")

    monkeypatch.setattr(transport, "send_text", _boom)
    persist.set_seen(DM, "fa")
    run_async(language_launch.offer_sweep(bot))
    assert _in_flight_now(bot) == set() and not persist.offer_done(DM)


def _in_flight_now(bot):
    return set(getattr(bot, "_language_launch_in_flight", set()))


# ---- a damaged state file (review rev-iter1-004) ---------------------------------------

def _damage():
    from aipager import config
    path = config.LANGUAGE_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    persist.reset()


def test_no_offer_and_no_new_marker_while_the_state_file_is_damaged(
        bot, run_async, sends, no_sweep_delay):
    _damage()
    assert language_launch.ensure_marker(bot) is None
    assert persist.launch_marker() is None
    after(bot, run_async, language_code="fa")
    persist.set_seen(DM, "fa")
    run_async(language_launch.offer_sweep(bot))
    assert sends == [] and not persist.offer_done(DM)
    # Still so after a restart: the damage is recorded, not forgotten.
    persist.reset()
    assert language_launch.ensure_marker(bot) is None
    after(bot, run_async, language_code="fa")
    assert sends == []


def _warnings(caplog):
    return [r for r in caplog.records if r.levelname == "WARNING"
            and r.name == persist.__name__]


def test_a_still_damaged_state_is_warned_at_every_start(bot, caplog):
    _damage()
    with caplog.at_level("WARNING", logger=persist.__name__):
        assert language_launch.ensure_marker(bot) is None
        # The start that finds it: one warning (where it was moved), not two.
        (found,) = _warnings(caplog)
        assert ".bad-" in found.getMessage()
        for _start in range(2):
            caplog.clear()
            persist.reset()                           # a restart
            assert language_launch.ensure_marker(bot) is None
            (again,) = _warnings(caplog)
            message = again.getMessage()
            assert "still marked damaged" in message and ".bad-" in message
            assert "JSONDecodeError" in message


def test_a_good_state_file_logs_no_damage_warning(bot, caplog):
    with caplog.at_level("WARNING", logger=persist.__name__):
        assert language_launch.ensure_marker(bot) is not None
        persist.reset()
        assert language_launch.ensure_marker(bot) is not None
    assert _warnings(caplog) == []


# ---- a grace after start (tester-iter1-002) --------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_right_after_start_a_step_waits_out_the_grace_before_it_sends(
        bot, run_async, sends, fresh, monkeypatch):
    clock = _Clock()
    events = []

    async def _sleep(seconds):
        events.append(("sleep", seconds))
        clock.now += seconds

    monkeypatch.setattr(language_launch, "_clock", clock)
    monkeypatch.setattr(language_launch, "_sleep", _sleep)
    real_send = transport.send_text

    async def _send(*a, **k):
        events.append(("send",))
        return await real_send(*a, **k)

    monkeypatch.setattr(transport, "send_text", _send)
    language_launch.note_started(bot)
    clock.now += 3.0                                   # a message 3 s after start
    after(bot, run_async, language_code="ru")
    assert events == [("sleep", language_launch.START_GRACE - 3.0), ("send",)]
    assert persist.first_contact_done(DM)


def test_after_the_grace_a_step_sends_at_once(bot, run_async, sends, fresh, monkeypatch):
    clock = _Clock()
    slept = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(language_launch, "_clock", clock)
    monkeypatch.setattr(language_launch, "_sleep", _sleep)
    language_launch.note_started(bot)
    clock.now += language_launch.START_GRACE
    after(bot, run_async, language_code="ru")
    assert slept == [] and len(sends) == 1


def test_the_grace_is_short_and_start_sets_it(mk_bot, run_async, monkeypatch):
    assert 0 < language_launch.START_GRACE < language_launch.SWEEP_DELAY
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    tg = mk_bot()
    stub = MagicMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater.start_polling = AsyncMock()
    tg._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    tg._update_bot_commands = AsyncMock()
    clock = _Clock()
    monkeypatch.setattr(language_launch, "_clock", clock)

    async def _go():
        await tg.start()
        tasks = [tg._language_offer_task, tg._descriptions_task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    run_async(_go())
    assert tg._language_launch_ready_at == clock.now + language_launch.START_GRACE
