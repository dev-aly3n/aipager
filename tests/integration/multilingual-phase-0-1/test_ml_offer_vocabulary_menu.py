"""The upgrade offer card is off; "Vocabulary level"; /language in the menu
and as a reserved name; the Auto/Ask labels (entrypoints.md).
"""

from __future__ import annotations

import pytest

from aipager import preferences as prefs

DM = 555
GROUP = -1001
OWNER = 555
BOB = 2


# ---- the offer card ----------------------------------------------------------------

def test_the_offer_is_switched_off():
    from aipager.bot import language_offer
    assert language_offer.OFFER_ENABLED is False


@pytest.mark.parametrize("code", ["fa", "de", "en", None, ""])
@pytest.mark.parametrize("chat", [DM, GROUP])
def test_maybe_offer_returns_false(tg_bot, run_async, code, chat):
    from aipager.bot.language_offer import maybe_offer
    assert run_async(maybe_offer(tg_bot, chat, code)) is False


@pytest.mark.parametrize("code", ["fa", "de", "ru"])
def test_maybe_offer_sends_nothing(tg_bot, run_async, code):
    from aipager.bot.language_offer import maybe_offer
    run_async(maybe_offer(tg_bot, DM, code))
    assert tg_bot._app.bot.send_message.await_count == 0


def test_maybe_offer_writes_nothing(tg_bot, run_async, ml):
    from aipager.bot.language_offer import maybe_offer
    before = ml.prefs_bytes()
    run_async(maybe_offer(tg_bot, DM, "fa"))
    assert ml.prefs_bytes() == before


def test_the_offer_text_names_the_language_by_its_own_name():
    from aipager import i18n
    from aipager.bot.language_offer import render_offer
    text, _kb = render_offer(i18n.get("en"), "fa")
    assert text == (
        "🌐 aipager now speaks فارسی. This changes only aipager's own messages "
        "and buttons here and in the Mini App, not your Claude sessions: Claude "
        "still answers in the language you write to it.")


def test_the_offer_buttons_are_the_language_and_keep_english(ml):
    from aipager import i18n
    from aipager.bot.language_offer import render_offer
    _text, kb = render_offer(i18n.get("en"), "de")
    assert ml.buttons(kb) == [("Deutsch", "_:set:lang_offer:de"),
                              ("Keep English", "_:set:lang_offer:keep")]


def test_the_offer_callback_never_carries_the_endonym(ml):
    from aipager import i18n
    from aipager.bot.language_offer import render_offer
    _text, kb = render_offer(i18n.get("en"), "zh-Hans")
    assert all("简体中文" not in d for _t, d in ml.buttons(kb))


@pytest.mark.parametrize("code", ["ja", "pl", "qps", "xx"])
def test_a_forged_offer_tap_cannot_select_a_disabled_language(tg_bot, tap, code):
    """Error guessing: the card is never sent, but its callback is routed.
    A tap must not store a language users cannot select."""
    tap(tg_bot, f"_:set:lang_offer:{code}", chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(DM) != code


def test_keep_english_on_the_offer_stores_english(tg_bot, tap):
    """design.md 5: the offer callback 'sets the DM language (keep sets en)'."""
    tap(tg_bot, "_:set:lang_offer:keep", chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(DM) == "en"


# entrypoints.md Amendment: "keep stores en. A disabled or pseudo code gets
# the Invalid value toast and stores nothing."

@pytest.mark.parametrize("code", ["ja", "pl", "qps", "qps-rtl"])
def test_a_disabled_or_pseudo_offer_code_gets_invalid_value(tg_bot, tap, ml, code):
    q = tap(tg_bot, f"_:set:lang_offer:{code}", chat_id=DM, user_id=OWNER)
    assert "Invalid value" in [t for t, _alert in ml.toasts(q)]


@pytest.mark.parametrize("code", ["ja", "pl", "qps", "qps-rtl", "xx", ""])
def test_a_refused_offer_code_writes_nothing(tg_bot, tap, ml, code):
    before = ml.prefs_bytes()
    tap(tg_bot, f"_:set:lang_offer:{code}", chat_id=DM, user_id=OWNER)
    assert ml.prefs_bytes() == before


def test_a_refused_offer_code_keeps_the_previous_choice(tg_bot, tap):
    prefs.set_ui_language(DM, "en")
    tap(tg_bot, "_:set:lang_offer:ja", chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(DM) == "en"


def test_keep_english_writes_nothing_when_english_is_already_stored(tg_bot, tap, ml):
    prefs.set_ui_language(DM, "en")
    before = ml.prefs_bytes()
    tap(tg_bot, "_:set:lang_offer:keep", chat_id=DM, user_id=OWNER)
    assert ml.prefs_bytes() == before


def test_the_offer_keep_button_is_the_tapped_keep_callback(tg_bot, tap, ml):
    """The card's own Keep English button, tapped as sent, stores English."""
    from aipager import i18n
    from aipager.bot.language_offer import render_offer
    _text, kb = render_offer(i18n.get("en"), "de")
    (data,) = [d for t, d in ml.buttons(kb) if t == "Keep English"]
    tap(tg_bot, data, chat_id=DM, user_id=OWNER)
    assert prefs.get_ui_language(DM) == "en"



# ---- Vocabulary level ---------------------------------------------------------------

def test_settings_shows_vocabulary_level(tg_bot, command, ml):
    _text, kb = command(tg_bot, "_handle_settings_cmd", "/settings",
                        chat_id=DM, user_id=OWNER)
    rows = [(t, d) for t, d in ml.buttons(kb) if t.startswith("🎓 Vocabulary level: ")]
    assert len(rows) == 1


def test_the_vocabulary_row_keeps_its_callback(tg_bot, command, ml):
    _text, kb = command(tg_bot, "_handle_settings_cmd", "/settings",
                        chat_id=DM, user_id=OWNER)
    (data,) = [d for t, d in ml.buttons(kb) if t.startswith("🎓 Vocabulary level")]
    assert data.startswith("_:set:level")


def test_settings_never_says_language_level(tg_bot, command, ml):
    text, kb = command(tg_bot, "_handle_settings_cmd", "/settings",
                       chat_id=DM, user_id=OWNER)
    shown = text + "\n".join(t for t, _d in ml.buttons(kb))
    assert "Language level" not in shown


def test_the_level_section_says_vocabulary_level(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:level", chat_id=DM, user_id=OWNER)
    assert "Vocabulary level" in ml.edited(q)[0]


def test_the_level_section_never_says_language_level(tg_bot, tap, ml):
    q = tap(tg_bot, "_:set:level", chat_id=DM, user_id=OWNER)
    assert "Language level" not in ml.edited(q)[0]


def test_the_stored_key_is_unchanged(tg_bot, tap):
    tap(tg_bot, "_:set:level:advanced", chat_id=DM, user_id=OWNER)
    assert prefs.get_preferences(DM).language_level == "advanced"


def _mini(mini_server, run_async, method, path, headers, **kw):
    from aiohttp.test_utils import TestClient, TestServer

    async def _go():
        client = TestClient(TestServer(mini_server._build_app()))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, **kw)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return run_async(_go())


def test_the_mini_app_titles_the_field_vocabulary_level(mini_server, run_async, ml):
    _s, body = _mini(mini_server, run_async, "GET", "/api/preferences", ml.hdr(BOB))
    (group,) = [g for g in body["schema"] if g["field"] == "language_level"]
    # The emoji is the page's to strip (design.md 5); the words are fixed.
    assert group["title"].endswith("Vocabulary level")


def test_the_mini_app_mirror_says_vocabulary_level(tg_bot, mini_server, run_async, ml):
    status, _b = _mini(mini_server, run_async, "PUT", "/api/preferences/language_level",
                       ml.hdr(OWNER), json={"value": "advanced"})
    assert status == 200
    texts = [c.kwargs.get("text", c.args[1] if len(c.args) > 1 else "")
             for c in tg_bot._app.bot.send_message.await_args_list]
    assert any("🎓 Vocabulary level → " in t for t in texts), texts


# ---- the / menu and reserved names ----------------------------------------------------

def test_language_is_in_the_menu_after_settings_before_help(monkeypatch):
    from aipager.bot.lifecycle import LifecycleMixin
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    names = [c.command for c in LifecycleMixin._command_list(set())]
    assert names.index("settings") + 1 == names.index("language") == names.index("help") - 1


def test_the_menu_describes_language(monkeypatch):
    from aipager.bot.lifecycle import LifecycleMixin
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    (desc,) = [c.description for c in LifecycleMixin._command_list(set())
               if c.command == "language"]
    assert desc == "Language of aipager's messages"


def test_language_is_in_the_menu_with_sessions_present(monkeypatch):
    from aipager.bot.lifecycle import LifecycleMixin
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    names = [c.command for c in LifecycleMixin._command_list({"dev", "x1"})]
    assert names.count("language") == 1


@pytest.mark.parametrize("name", ["language", "LANGUAGE", "Language"])
def test_language_is_a_reserved_session_name(name):
    from aipager.miniapp.launch import validate_session_name
    assert validate_session_name(name)[0] == ""


def test_the_refusal_names_the_command():
    from aipager.miniapp.launch import validate_session_name
    assert "/language" in validate_session_name("language")[1]


@pytest.mark.parametrize("name", ["languages", "language2", "lang", "my_language"])
def test_neighbouring_names_are_still_allowed(name):
    from aipager.miniapp.launch import validate_session_name
    assert validate_session_name(name) == (name, "")


def test_language_is_one_of_the_groups_own_commands():
    from aipager.bot import group_intake
    assert "language" in group_intake.OWN_COMMANDS


def test_language_is_a_registered_command(mk_bot, run_async):
    """The handler answers /language (it is wired, not only in the menu).
    Same capture as tests/integration/status-home-menu-keyboard."""
    from unittest.mock import AsyncMock, MagicMock

    from telegram.ext import CommandHandler
    b = mk_bot()
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda h, *a, **k: registered.append(h))
    stub.bot = MagicMock()
    stub.bot.set_my_commands = AsyncMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater = MagicMock()
    stub.updater.start_polling = AsyncMock()
    b._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    b._update_bot_commands = AsyncMock()
    try:
        run_async(b.start())
    except Exception:
        pass
    (handler,) = [h for h in registered
                  if isinstance(h, CommandHandler) and "language" in h.commands]
    assert handler.callback == b._handle_language_cmd


# ---- mode labels keep their English ------------------------------------------------

def test_mode_labels_are_unchanged_english():
    from aipager.bot.mode_labels import MODE_ASK, MODE_AUTO
    assert (MODE_AUTO, MODE_ASK) == ("🤖 Auto", "💬 Ask")
