"""The launch rule (multilingual phase 5, design section 6).

"Automatic" is its own stored value (``ui_language: "auto"``, private
chats only), so a chat that picked it follows Telegram while a chat that
never chose keeps its old behaviour. A private chat new since the launch
marker follows its person's Telegram language; one that existed before
stays English until its person chooses.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import language_menu
from aipager.i18n import for_chat, locales, persist
from aipager.policy import load_policy
from aipager.scope import Member, Scope

GROUP, DM = -1001, 555
ALY, BOB = 555, 777


@pytest.fixture
def bot(mk_bot):
    b = mk_bot(scopes=[
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=ALY, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"))),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=ALY, label="aly", role="owner"),)),
    ])
    b.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    b._app.bot.set_my_commands = AsyncMock()
    b._app.bot.set_chat_menu_button = AsyncMock()
    b._send_keyboard = AsyncMock()
    return b


def tap(bot, run_async, data, *, chat, user, language_code=None):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = 42
    query.message.chat = MagicMock(id=chat)
    query.message.chat_id = chat
    query.from_user = MagicMock(id=user, language_code=language_code)
    query.from_user.username = "aly" if user == ALY else "bob"
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock(id=chat)
    update.message = None
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def _marked(query):
    call = query.edit_message_text.await_args
    kb = call.kwargs.get("reply_markup")
    return [b.callback_data.rsplit(":", 1)[1]
            for row in kb.inline_keyboard for b in row if b.text.endswith("✅")]


# ---- "Automatic" is stored ----------------------------------------------------

def test_automatic_is_stored_for_a_private_chat():
    assert preferences.set_ui_language(DM, "auto") == "auto"
    preferences._cache = None
    assert preferences.get_ui_language(DM) == "auto"
    stored = json.loads(Path(preferences._PREFERENCES_PATH).read_text())
    assert stored[str(DM)] == {"ui_language": "auto"}


@pytest.mark.parametrize("chat", [GROUP, 0, True])
def test_automatic_is_refused_for_a_group_before_any_write(chat):
    with pytest.raises(ValueError):
        preferences.set_ui_language(chat, "auto")
    assert not Path(preferences._PREFERENCES_PATH).exists()


def test_stored_automatic_is_never_read_as_a_language():
    preferences.set_ui_language(DM, "auto")
    assert for_chat(DM).code == "en"


def test_the_automatic_tap_stores_automatic(bot, run_async):
    preferences.set_ui_language(DM, "fa")
    q = tap(bot, run_async, "_:set:lang:auto", chat=DM, user=ALY)
    assert preferences.get_ui_language(DM) == "auto"
    assert _marked(q) == ["auto"]


def test_the_automatic_tap_in_a_group_is_refused(bot, run_async):
    q = tap(bot, run_async, "_:set:lang:auto", chat=GROUP, user=ALY)
    assert preferences.get_ui_language(GROUP) == ""
    assert q.answer.await_args.args[0] == "Invalid value"


def test_a_stored_code_is_marked():
    preferences.set_ui_language(DM, "fa")
    assert language_menu.current_choice(DM, private=True) == "fa"


def test_stored_automatic_is_marked_automatic():
    preferences.set_ui_language(DM, "auto")
    assert language_menu.current_choice(DM, private=True) == "auto"
    assert language_menu.current_choice(DM, private=True, telegram_code="fa") == "auto"


# ---- the one rule (section 6) ---------------------------------------------------

NEW = 4242          # a person new since the marker
OLD = ALY           # a person listed in the marker


def _upd(chat, user, language_code=None):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat),
                           effective_user=SimpleNamespace(id=user, language_code=language_code))


@pytest.fixture
def marker():
    return persist.ensure_launch_marker(people=[OLD, BOB], prior_run=True)


def test_a_new_private_chat_follows_a_persian_app(marker):
    assert i18n.for_update(_upd(NEW, NEW, "fa")).code == "fa"
    assert i18n.for_chat(NEW).code == "fa"


def test_an_existing_private_chat_stays_english(marker):
    assert i18n.for_update(_upd(OLD, OLD, "fa")).code == "en"
    assert i18n.for_chat(OLD).code == "en"


def test_a_new_persons_pop_up_and_mini_app_follow_their_app(marker):
    assert i18n.for_person(_upd(GROUP, NEW, "ru")).code == "ru"
    assert i18n.for_viewer(NEW, "ru").code == "ru"


def test_an_existing_persons_pop_up_and_mini_app_stay_as_before(marker):
    assert i18n.for_person(_upd(GROUP, BOB, "ru")).code == "en"
    assert i18n.for_viewer(BOB, "ru").code == "en"
    preferences.set_ui_language(GROUP, "de")
    assert i18n.for_person(_upd(GROUP, BOB, "ru")).code == "de"


def test_an_explicit_choice_wins(marker):
    preferences.set_ui_language(NEW, "de")
    assert i18n.for_update(_upd(NEW, NEW, "fa")).code == "de"
    assert i18n.for_viewer(NEW, "fa").code == "de"
    assert i18n.for_person(_upd(GROUP, NEW, "fa")).code == "de"


def test_stored_automatic_follows_an_app_change_even_for_an_existing_person(marker):
    preferences.set_ui_language(OLD, "auto")
    assert i18n.for_update(_upd(OLD, OLD, "fa")).code == "fa"
    assert i18n.for_update(_upd(OLD, OLD, "ru")).code == "ru"
    assert i18n.for_viewer(OLD, "de").code == "de"
    assert i18n.for_chat(OLD).code == "ru"


def test_a_background_message_uses_the_persisted_language_after_a_restart(marker):
    i18n.for_update(_upd(NEW, NEW, "fa"))
    persist.reset()                     # a restart: memory gone, file kept
    assert i18n.for_chat(NEW).code == "fa"


def test_a_missing_language_code_keeps_it(marker):
    i18n.for_update(_upd(NEW, NEW, "fa"))
    assert i18n.resolve.note_language(_upd(NEW, NEW, None)) is False
    assert i18n.resolve.note_language(_upd(NEW, NEW, "")) is False
    assert i18n.for_chat(NEW).code == "fa"


def test_an_unsupported_language_code_clears_it(marker):
    i18n.for_update(_upd(NEW, NEW, "fa"))
    assert i18n.resolve.note_language(_upd(NEW, NEW, "ja")) is True
    assert persist.seen(NEW) is None
    assert i18n.for_chat(NEW).code == "en"
    assert i18n.resolve.note_language(_upd(NEW, NEW, "zh-hant")) is False


def test_a_group_update_never_records_a_language(marker):
    assert i18n.resolve.note_language(_upd(GROUP, NEW, "fa")) is False
    assert persist.seen(NEW) is None


def test_strangers_get_their_telegram_language():
    user = SimpleNamespace(id=999, language_code="ar")
    assert i18n.for_stranger(user).code == "ar"
    assert i18n.for_stranger(_upd(999, 999, "ar")).code == "ar"
    assert i18n.for_stranger(SimpleNamespace(id=999, language_code="ja")).code == "en"
    preferences.set_ui_language(999, "tr")
    assert i18n.for_stranger(user).code == "tr"


def test_without_a_marker_everyone_is_existing():
    assert i18n.for_update(_upd(NEW, NEW, "fa")).code == "en"
    assert i18n.for_viewer(NEW, "fa").code == "en"


def test_the_kill_switch(marker, monkeypatch):
    monkeypatch.setattr(locales, "AUTO_DETECT", False)
    assert locales.detect("fa") is None and locales.detect("en-US") == "en"
    assert i18n.for_update(_upd(NEW, NEW, "fa")).code == "en"
    assert i18n.for_viewer(NEW, "fa").code == "en"
    assert i18n.for_person(_upd(GROUP, NEW, "fa")).code == "en"
    assert i18n.for_stranger(_upd(NEW, NEW, "fa")).code == "fa"
    # Everyone but strangers: a stored Automatic too.
    preferences.set_ui_language(OLD, "auto")
    assert i18n.for_update(_upd(OLD, OLD, "fa")).code == "en"
    assert i18n.for_viewer(OLD, "fa").code == "en"


def test_automatic_code():
    assert i18n.automatic_code(NEW, "fa-IR") == "fa"
    assert i18n.automatic_code(NEW, "ja") == "en"
    assert i18n.automatic_code(NEW) == "en"
    persist.set_seen(NEW, "ru")
    assert i18n.automatic_code(NEW) == "ru"


# ---- what /language marks in a private chat -------------------------------------

def test_a_new_person_with_nothing_stored_is_on_automatic(marker):
    persist.set_seen(NEW, "fa")
    assert language_menu.current_choice(NEW, private=True) == "auto"


def test_an_existing_english_app_owner_sees_todays_mark(marker):
    persist.set_seen(OLD, "en")
    assert language_menu.current_choice(OLD, private=True) == "auto"
    persist.set_seen(OLD, None)
    assert language_menu.current_choice(OLD, private=True) == "auto"


def test_an_existing_persian_app_owner_sees_english_marked(marker):
    persist.set_seen(OLD, "fa")
    assert language_menu.current_choice(OLD, private=True) == "en"
    assert language_menu.current_choice(OLD, private=True, telegram_code="en") == "auto"
    persist.set_seen(OLD, None)
    assert language_menu.current_choice(OLD, private=True, telegram_code="fa") == "en"


def test_a_group_marks_the_language_in_force(marker):
    assert language_menu.current_choice(GROUP, private=False) == "en"
    preferences.set_ui_language(GROUP, "fa")
    assert language_menu.current_choice(GROUP, private=False) == "fa"


# ---- the marker at start -----------------------------------------------------------

def test_the_marker_lists_every_known_person(mk_bot, monkeypatch):
    from aipager.bot import language_launch
    monkeypatch.setattr("aipager.config.CHAT_ID", "31")
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "32")
    preferences.set_ui_language(33, "en")
    preferences.set_ui_language(-1009, "en")
    bot = mk_bot(scopes=[
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=ALY, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"))),
        Scope(chat_id=34, kind="dm", label="dm",
              members=(Member(id=34, label="x", role="owner"),)),
    ])
    marker = language_launch.ensure_marker(bot)
    assert marker["people"] == [31, 32, 33, 34, ALY, BOB]
    assert marker["fresh"] is False


def test_the_marker_on_a_fresh_install_lists_nobody(mk_bot):
    from aipager.bot import language_launch
    persist.note_prior_run()
    marker = language_launch.ensure_marker(mk_bot())
    assert marker["fresh"] is True and marker["people"] == []


def test_the_marker_is_not_rewritten_at_the_next_start(mk_bot):
    from aipager.bot import language_launch
    first = language_launch.ensure_marker(mk_bot())
    persist.reset()
    preferences.set_ui_language(77, "en")
    assert language_launch.ensure_marker(mk_bot()) == first


def test_start_writes_the_marker_before_any_handler_or_polling(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    bot = mk_bot()
    order = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda *a, **k: order.append(
        ("handler", persist.launch_marker() is not None)))
    stub.initialize = AsyncMock(side_effect=lambda: order.append(
        ("initialize", persist.launch_marker() is not None)))
    stub.start = AsyncMock()
    stub.updater.start_polling = AsyncMock()
    bot._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    bot._update_bot_commands = AsyncMock()

    async def _go():
        await bot.start()
        # start()'s background tasks: settled here, never left pending.
        tasks = [bot._language_offer_task, bot._descriptions_task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    run_async(_go())
    assert order and all(written for _what, written in order)
    assert 555 in persist.launch_marker()["people"]


def test_the_daemon_notes_a_prior_run_before_writing_its_own_markers(monkeypatch):
    from aipager.cli import daemon
    from aipager.report import runtime
    seen = []
    monkeypatch.setattr(persist, "note_prior_run", lambda: seen.append("prior") or True)
    monkeypatch.setattr(runtime, "daemon_starting", lambda **k: seen.append("starting"))
    monkeypatch.setattr(runtime, "daemon_stopped", lambda **k: seen.append("stopped"))
    monkeypatch.setattr(daemon, "_running_as_service", lambda: False)

    async def _run(_name):
        return None

    monkeypatch.setattr(daemon, "_run_daemon", _run)
    daemon._run_daemon_reporting_crashes("bot")
    assert seen == ["prior", "starting", "stopped"]
