"""Black-box: the one-time offer card and first-contact line (phase 5 item 3;
design.md section 5, D2, D8, D9, D11; entrypoints.md ``language_launch``,
side effects "one offer card" and "one first-contact message").

Success criteria: an existing DM owner with a supported non-English app gets
exactly one offer card, as ORNAMENT plus skip, never again after a restart or
any answer; "Keep English" stores "en". A new private chat gets exactly one
first-contact message, never in English, never in a group, never after a
restart. Both silent, low priority, never in groups.

Partitions: person = existing / new / fresh install; chat = served private /
group / unserved private; Telegram language = fa / ru / en / ja / none;
stored = nothing / code / auto; delivery = sent / muted / failed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n
from aipager import preferences as prefs
from aipager.bot import language_launch, language_menu
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
from aipager.i18n import persist
from tests.i18n_ml2_bb import callback, message

OWNER, BOB, CAROL, GROUP = 555, 2, 3, -1001
STRANGER = 777005


def _upd(user, code, chat=None):
    return message("hi", chat=user if chat is None else chat, user=user, language_code=code)


def _first_contact(code):
    return str(language_menu.first_contact_text(i18n.get(code)))


def _offer_sends(p5, bot, chat):
    return [s for s in p5.sends(bot, chat)
            if any(d.startswith("_:set:lang_offer:")
                   for _t, d in p5.inline_buttons(s.get("reply_markup")))]


def _first_contact_sends(p5, bot, chat, code):
    return [s for s in p5.sends(bot, chat) if str(s.get("text")) == _first_contact(code)]


@pytest.fixture
def upgraded(p5):
    """An upgrade: the owner existed before the launch; bob and carol are new."""
    p5.marker(OWNER)


# ---- the offer: who gets it -----------------------------------------------------------

def test_an_existing_persian_owner_gets_one_offer(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    assert len(_offer_sends(p5, bot, OWNER)) == 1


def test_the_offer_buttons_are_the_language_and_keep_english(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    (card,) = _offer_sends(p5, bot, OWNER)
    assert [d for _t, d in p5.inline_buttons(card["reply_markup"])] == [
        "_:set:lang_offer:fa", "_:set:lang_offer:keep"]


def test_the_offer_names_the_language_by_its_own_name(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    (card,) = _offer_sends(p5, bot, OWNER)
    assert "فارسی" in str(card["text"])


def test_the_offer_is_silent(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    (card,) = _offer_sends(p5, bot, OWNER)
    assert card.get("disable_notification") is True


def test_the_offer_is_ornament_and_skip(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    (card,) = _offer_sends(p5, bot, OWNER)
    assert card.get("rate_limit_args") == rate_limit_args(kind="skip",
                                                          priority=PRIORITY_ORNAMENT)


def test_the_offer_is_recorded(bot, launch, upgraded):
    launch(bot, _upd(OWNER, "ru"))
    assert persist.offer_done(OWNER) is True


@pytest.mark.parametrize("code", ["en", "en-US", "ja", None, ""])
def test_no_offer_for_an_english_or_unsupported_app(bot, launch, p5, upgraded, code):
    launch(bot, _upd(OWNER, code))
    assert _offer_sends(p5, bot, OWNER) == []


@pytest.mark.parametrize("stored", ["en", "fa", "auto"])
def test_no_offer_after_a_choice(bot, launch, p5, upgraded, stored):
    prefs.set_ui_language(OWNER, stored)
    launch(bot, _upd(OWNER, "fa"))
    assert _offer_sends(p5, bot, OWNER) == []


def test_no_offer_for_a_new_person(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "fa"))
    assert _offer_sends(p5, bot, BOB) == []


def test_no_offer_in_a_group(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa", chat=GROUP))
    assert p5.sends(bot) == []


def test_a_group_message_records_no_offer(bot, launch, upgraded):
    launch(bot, _upd(OWNER, "fa", chat=GROUP))
    assert persist.offer_done(GROUP) is False


def test_no_offer_to_a_chat_this_install_does_not_serve(bot, launch, p5, upgraded):
    launch(bot, _upd(STRANGER, "fa"))
    assert p5.sends(bot) == []


# ---- the offer: once ---------------------------------------------------------------------

def test_the_offer_comes_once(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    launch(bot, _upd(OWNER, "fa"))
    assert len(_offer_sends(p5, bot, OWNER)) == 1


def test_the_offer_never_comes_again_after_a_restart(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    p5.restart()
    launch(bot, _upd(OWNER, "fa"))
    assert len(_offer_sends(p5, bot, OWNER)) == 1


def test_an_app_switch_does_not_bring_a_second_offer(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    launch(bot, _upd(OWNER, "ru"))
    assert len(_offer_sends(p5, bot, OWNER)) == 1


def test_the_offer_never_comes_again_after_keep_english(bot, launch, p5, upgraded, run_async):
    launch(bot, _upd(OWNER, "fa"))
    update, _q = callback("_:set:lang_offer:keep", chat=OWNER, user=OWNER, language_code="fa")
    run_async(bot._handle_callback(update, MagicMock()))
    p5.restart()
    launch(bot, _upd(OWNER, "fa"))
    assert len(_offer_sends(p5, bot, OWNER)) == 1


def test_keep_english_stores_english(bot, launch, upgraded, run_async):
    launch(bot, _upd(OWNER, "fa"))
    update, _q = callback("_:set:lang_offer:keep", chat=OWNER, user=OWNER, language_code="fa")
    run_async(bot._handle_callback(update, MagicMock()))
    assert prefs.get_ui_language(OWNER) == "en"


def test_the_language_button_stores_the_language(bot, launch, upgraded, run_async):
    launch(bot, _upd(OWNER, "fa"))
    update, _q = callback("_:set:lang_offer:fa", chat=OWNER, user=OWNER, language_code="fa")
    run_async(bot._handle_callback(update, MagicMock()))
    assert prefs.get_ui_language(OWNER) == "fa"


def test_two_existing_owners_each_get_their_own_offer(bot, launch, p5):
    p5.marker(OWNER, BOB)
    launch(bot, _upd(OWNER, "fa"))
    launch(bot, _upd(BOB, "ru"))
    assert (len(_offer_sends(p5, bot, OWNER)), len(_offer_sends(p5, bot, BOB))) == (1, 1)


# ---- the offer: delivery failures (error guessing) ---------------------------------------

def test_no_offer_into_a_muted_chat(bot, launch, p5, upgraded):
    MUTE.mute(OWNER, 5000)
    launch(bot, _upd(OWNER, "fa"))
    assert _offer_sends(p5, bot, OWNER) == []


def test_a_muted_chat_gets_the_offer_once_the_mute_ends(bot, launch, p5, upgraded):
    MUTE.mute(OWNER, 5000)
    launch(bot, _upd(OWNER, "fa"))
    MUTE.clear()
    launch(bot, _upd(OWNER, "fa"))
    assert len(_offer_sends(p5, bot, OWNER)) == 1


def test_a_failed_offer_send_is_not_recorded(bot, launch, upgraded):
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("network down"))
    launch(bot, _upd(OWNER, "fa"))
    assert persist.offer_done(OWNER) is False


# ---- the offer at start ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_sweep_wait(monkeypatch):
    """The start sweep waits ``SWEEP_DELAY`` (public, not in entrypoints.md)
    before it acts; tests do not wait it out."""
    monkeypatch.setattr(language_launch, "SWEEP_DELAY", 0.0)


def test_the_start_sweep_offers_from_the_persisted_language(bot, run_async, p5, upgraded):
    persist.set_seen(OWNER, "fa")
    run_async(language_launch.offer_sweep(bot))
    assert len(_offer_sends(p5, bot, OWNER)) == 1


def test_a_second_start_sweep_sends_nothing_more(bot, run_async, p5, upgraded):
    persist.set_seen(OWNER, "fa")
    run_async(language_launch.offer_sweep(bot))
    p5.restart()
    run_async(language_launch.offer_sweep(bot))
    assert len(_offer_sends(p5, bot, OWNER)) == 1


def test_the_start_sweep_sends_nothing_with_nothing_seen(bot, run_async, p5, upgraded):
    run_async(language_launch.offer_sweep(bot))
    assert p5.sends(bot) == []


def test_the_start_sweep_skips_a_new_person(bot, run_async, p5, upgraded):
    persist.set_seen(BOB, "fa")
    run_async(language_launch.offer_sweep(bot))
    assert _offer_sends(p5, bot, BOB) == []


def test_the_start_sweep_skips_an_english_owner(bot, run_async, p5, upgraded):
    persist.set_seen(OWNER, "en")
    run_async(language_launch.offer_sweep(bot))
    assert p5.sends(bot) == []


# ---- the first-contact line: who gets it ----------------------------------------------------

@pytest.mark.parametrize("code", ["fa", "ru", "zh-Hans", "ar"])
def test_a_new_private_chat_gets_one_first_contact_line(bot, launch, p5, upgraded, code):
    launch(bot, _upd(BOB, code))
    assert len(_first_contact_sends(p5, bot, BOB, code)) == 1


def test_the_first_contact_line_is_silent(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "fa"))
    (msg,) = _first_contact_sends(p5, bot, BOB, "fa")
    assert msg.get("disable_notification") is True


def test_the_first_contact_line_is_ornament_and_skip(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "fa"))
    (msg,) = _first_contact_sends(p5, bot, BOB, "fa")
    assert msg.get("rate_limit_args") == rate_limit_args(kind="skip",
                                                         priority=PRIORITY_ORNAMENT)


def test_the_first_contact_line_names_the_language(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "ru"))
    (msg,) = _first_contact_sends(p5, bot, BOB, "ru")
    assert "/language" in str(msg["text"])


def test_the_first_contact_line_is_recorded(bot, launch, upgraded):
    launch(bot, _upd(BOB, "fa"))
    assert persist.first_contact_done(BOB) is True


@pytest.mark.parametrize("code", ["en", "en-GB", "ja", None])
def test_no_first_contact_line_in_english(bot, launch, p5, upgraded, code):
    launch(bot, _upd(BOB, code))
    assert p5.sends(bot, BOB) == []


def test_no_first_contact_line_in_a_group(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "fa", chat=GROUP))
    assert p5.sends(bot) == []


def test_no_first_contact_line_for_an_existing_person(bot, launch, p5, upgraded):
    launch(bot, _upd(OWNER, "fa"))
    assert _first_contact_sends(p5, bot, OWNER, "fa") == []


@pytest.mark.parametrize("stored", ["ru", "en", "auto"])
def test_no_first_contact_line_after_a_choice(bot, launch, p5, upgraded, stored):
    prefs.set_ui_language(BOB, stored)
    launch(bot, _upd(BOB, "fa"))
    assert p5.sends(bot, BOB) == []


def test_no_first_contact_line_to_an_unserved_chat(bot, launch, p5, upgraded):
    launch(bot, _upd(STRANGER, "fa"))
    assert p5.sends(bot) == []


# ---- the first-contact line: once ---------------------------------------------------------------

def test_the_first_contact_line_comes_once(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "fa"))
    launch(bot, _upd(BOB, "fa"))
    assert len(p5.sends(bot, BOB)) == 1


def test_the_first_contact_line_never_comes_after_a_restart(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "fa"))
    p5.restart()
    launch(bot, _upd(BOB, "fa"))
    assert len(p5.sends(bot, BOB)) == 1


def test_an_app_switch_brings_no_second_first_contact_line(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "fa"))
    launch(bot, _upd(BOB, "ru"))
    assert _first_contact_sends(p5, bot, BOB, "ru") == []


def test_two_new_people_each_get_their_own_line(bot, launch, p5, upgraded):
    launch(bot, _upd(BOB, "fa"))
    launch(bot, _upd(CAROL, "fa"))
    assert (len(p5.sends(bot, BOB)), len(p5.sends(bot, CAROL))) == (1, 1)


# ---- the first-contact line: delivery failures ----------------------------------------------------

def test_no_first_contact_line_into_a_muted_chat(bot, launch, p5, upgraded):
    MUTE.mute(BOB, 5000)
    launch(bot, _upd(BOB, "fa"))
    assert p5.sends(bot, BOB) == []


def test_a_muted_first_contact_line_is_not_recorded(bot, launch, upgraded):
    MUTE.mute(BOB, 5000)
    launch(bot, _upd(BOB, "fa"))
    assert persist.first_contact_done(BOB) is False


def test_a_failed_first_contact_send_is_not_recorded(bot, launch, upgraded):
    bot._app.bot.send_message = AsyncMock(side_effect=RuntimeError("network down"))
    launch(bot, _upd(BOB, "fa"))
    assert persist.first_contact_done(BOB) is False


def test_a_none_first_contact_send_is_not_recorded(bot, launch, upgraded):
    bot._app.bot.send_message = AsyncMock(return_value=None)
    launch(bot, _upd(BOB, "fa"))
    assert persist.first_contact_done(BOB) is False


# ---- a fresh install (D2) ---------------------------------------------------------------------

def test_a_fresh_install_owner_gets_the_first_contact_line(bot, launch, p5):
    persist.ensure_launch_marker([OWNER], prior_run=False)
    launch(bot, _upd(OWNER, "fa"))
    assert len(_first_contact_sends(p5, bot, OWNER, "fa")) == 1


def test_a_fresh_install_owner_gets_no_offer(bot, launch, p5):
    persist.ensure_launch_marker([OWNER], prior_run=False)
    launch(bot, _upd(OWNER, "fa"))
    assert _offer_sends(p5, bot, OWNER) == []


# ---- no marker at all: everyone is existing -----------------------------------------------------

def test_without_a_marker_a_dm_gets_no_first_contact_line(bot, launch, p5):
    launch(bot, _upd(BOB, "fa"))
    assert _first_contact_sends(p5, bot, BOB, "fa") == []


# ---- /start no longer carries the line (D8) ----------------------------------------------------

def test_start_no_longer_carries_the_first_contact_line(bot, run_async, upgraded):
    update = _upd(BOB, "fa")
    update.message.text = "/start"
    run_async(bot._handle_start_cmd(update, MagicMock()))
    texts = [str(c.args[0] if c.args else c.kwargs.get("text", ""))
             for c in update.message.reply_text.await_args_list]
    texts += [str(c.kwargs.get("text", "")) for c in bot._app.bot.send_message.await_args_list]
    assert all(_first_contact(code) not in t for t in texts for code in ("fa", "en"))


# ---- a detected change refreshes the menu, never sends a keyboard (D9) ---------------------------

@pytest.fixture
def registered(mk_bot, run_async, upgraded):
    """A bot whose `/` menus were registered as at start (English)."""
    from tests import i18n_ml2_bb as bb
    b = bb.make_bot(mk_bot)
    run_async(b._update_bot_commands())
    b._app.bot.set_my_commands.reset_mock()
    b._app.bot.set_chat_menu_button.reset_mock()
    return b


def _menu_chats(bot):
    return [c.kwargs["scope"].chat_id for c in bot._app.bot.set_my_commands.await_args_list
            if getattr(c.kwargs.get("scope"), "chat_id", None) is not None]


def test_a_detected_language_change_refreshes_the_chats_menu(registered, launch):
    launch(registered, _upd(BOB, "fa"))
    assert _menu_chats(registered) == [BOB]


def test_the_refreshed_menu_is_in_the_detected_language(registered, launch):
    launch(registered, _upd(BOB, "fa"))
    (call,) = registered._app.bot.set_my_commands.await_args_list
    commands = call.args[0] if call.args else call.kwargs["commands"]
    assert any(c.description.startswith("\u200f") for c in commands)


def test_a_detected_language_change_refreshes_the_menu_button(registered, launch):
    launch(registered, _upd(BOB, "fa"))
    chats = [c.kwargs.get("chat_id")
             for c in registered._app.bot.set_chat_menu_button.await_args_list]
    assert chats == [BOB]


def test_a_detected_language_change_sends_no_reply_keyboard(registered, launch):
    from tests.i18n_ml2_bb import reply_keyboards
    launch(registered, _upd(BOB, "fa"))
    assert reply_keyboards(registered, BOB) == []


def test_the_same_language_again_does_not_refresh_the_menu(registered, launch):
    launch(registered, _upd(BOB, "fa"))
    launch(registered, _upd(BOB, "fa"))
    assert _menu_chats(registered) == [BOB]


def test_an_existing_english_chat_menu_is_not_refreshed(registered, launch):
    launch(registered, _upd(OWNER, "fa"))
    assert _menu_chats(registered) == []


# ---- the marker at start ------------------------------------------------------------------------

def test_ensure_marker_counts_the_scopes_people_after_a_prior_run(bot):
    prefs.set_ui_language(9001, "en")
    persist.note_prior_run()
    language_launch.ensure_marker(bot)
    assert {OWNER, BOB, CAROL, 9001} <= set(persist.launch_marker()["people"])


def test_ensure_marker_counts_the_configured_chat(bot):
    prefs.set_ui_language(9001, "en")
    persist.note_prior_run()
    language_launch.ensure_marker(bot)
    assert 222333444 in persist.launch_marker()["people"]


def test_ensure_marker_never_counts_a_group(bot):
    prefs.set_ui_language(GROUP, "fa")
    persist.note_prior_run()
    language_launch.ensure_marker(bot)
    assert all(p > 0 for p in persist.launch_marker()["people"])


def test_ensure_marker_on_a_fresh_machine_is_fresh(bot):
    assert persist.note_prior_run() is False
    language_launch.ensure_marker(bot)
    assert persist.launch_marker()["people"] == []


def test_ensure_marker_does_not_rewrite(bot, p5):
    p5.marker(OWNER)
    language_launch.ensure_marker(bot)
    assert persist.launch_marker()["people"] == [OWNER]
