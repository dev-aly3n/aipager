"""Every surface that offers or confirms a language says, in one line,
that it changes only aipager's own text and never the Claude sessions or
Claude's answers (the multilingual design, 7.0; operator, 2026-10-09:
people would otherwise expect their sessions to switch language too)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.test_language_setting import (
    ALY,
    BOB,
    CONFIRM,
    DM,
    GROUP,
    LIST_GROUP,
    LIST_PRIVATE,
    edited,
    tap,
)
from tests.test_language_setting import bot as _bot_fixture  # noqa: F401

bot = _bot_fixture

#: What the private-chat line must say, piece by piece.
PRIVATE_SAYS = ("aipager uses for its own messages and buttons", "Mini App",
                "Claude sessions don't change", "Claude answers in the language you write to it")
GROUP_SAYS = ("aipager uses for this group's cards and buttons",
              "Claude's sessions don't change", "Claude answers in the language you write to it")
CONFIRM_SAYS = ("aipager now speaks", "Your sessions and Claude's answers are unchanged")


def _says(text, pieces):
    missing = [p for p in pieces if p not in text]
    assert not missing, (missing, text)


def _language_cmd(bot, run_async, mk_update, chat, user):
    update = mk_update("/language", chat_id=chat, user_id=user)
    update.effective_chat.type = "private" if chat > 0 else "group"
    run_async(bot._handle_language_cmd(update, MagicMock()))
    call = update.message.reply_text.await_args
    return call.args[0], call.kwargs["reply_markup"]


def test_the_language_command_says_it_in_a_private_chat(bot, run_async, mk_update):
    text, _kb = _language_cmd(bot, run_async, mk_update, DM, ALY)
    assert text == LIST_PRIVATE
    _says(text, PRIVATE_SAYS)


def test_the_language_command_says_it_in_a_group(bot, run_async, mk_update):
    text, kb = _language_cmd(bot, run_async, mk_update, GROUP, BOB)
    assert text == LIST_GROUP
    _says(text, GROUP_SAYS)
    assert all(b.text != "Automatic (your Telegram language)"
               for row in kb.inline_keyboard for b in row)


@pytest.mark.parametrize("chat, user, want, pieces", [
    (DM, ALY, LIST_PRIVATE, PRIVATE_SAYS), (GROUP, BOB, LIST_GROUP, GROUP_SAYS)])
def test_the_settings_row_opens_the_list_that_says_it(bot, run_async, chat, user, want,
                                                      pieces):
    root = edited(tap(bot, run_async, "_:set", chat=chat, user=user))
    assert root[1][0] == [("🌐 Language: English", "_:set:lang")]
    text, _rows = edited(tap(bot, run_async, "_:set:lang", chat=chat, user=user))
    assert text == want
    _says(text, pieces)


@pytest.mark.parametrize("chat, list_text", [(DM, LIST_PRIVATE), (GROUP, LIST_GROUP)])
def test_the_confirmation_says_it(bot, run_async, chat, list_text):
    text, _rows = edited(tap(bot, run_async, "_:set:lang:en", chat=chat, user=ALY))
    assert text.startswith(CONFIRM + "\n\n" + list_text)
    _says(text, CONFIRM_SAYS)


def test_the_lines_fit_a_phone():
    """The 7.0 lines are one short paragraph (CI budget: 240 characters
    with the longest language name)."""
    from aipager.bot import language_menu
    for msg in (language_menu.LIST_PRIVATE, language_menu.LIST_GROUP,
                language_menu.CHANGED):
        assert "\n" not in msg
        assert len(msg.replace("{language}", "Português (Brasil)")) <= 240


# ---- the Mini App's App language -------------------------------------------

from tests.test_app_language_api import (  # noqa: E402
    MEMBER_ID,
    _call,
    _configured_bot_token,  # noqa: F401 - autouse: the token the initData is signed with
    _hdr,
    _run,
)
from tests.test_app_language_api import server  # noqa: E402, F401


def test_the_mini_app_row_says_it(server):  # noqa: F811
    _status, data = _run(_call(server, "GET", "/api/preferences", _hdr(MEMBER_ID)))
    assert data["app_language"]["hint"] == (
        "Only aipager's own text. Claude's answers don't change.")


def test_the_mini_app_confirmation_says_it(server):  # noqa: F811
    _status, data = _run(_call(server, "PUT", "/api/app-language", _hdr(MEMBER_ID),
                               {"value": "en"}))
    assert data["confirmation"] == CONFIRM
    _says(data["confirmation"], CONFIRM_SAYS)


def test_the_offer_card_says_it():
    from aipager import i18n
    from aipager.bot import language_offer
    text, _kb = language_offer.render_offer(i18n.get("en"), "en")
    _says(text, ("aipager now speaks English",
                 "changes only aipager's own messages and buttons",
                 "not your Claude sessions",
                 "Claude still answers in the language you write to it"))


# ---- in Persian and Russian (multilingual phase 2) -------------------------------

@pytest.mark.parametrize("code", ["fa", "ru"])
def test_every_seven_zero_line_says_it_in_persian_and_russian(code, monkeypatch):
    """The list's line, the confirmation and the first-contact line, each in
    the language: the whole catalog sentence, the 7.0 half included (G83)."""
    from aipager import i18n
    from aipager.bot import language_menu
    from aipager.i18n import core
    tr = i18n.get(code)
    catalog = core._catalog(code)
    mark = "\u200f" if tr.rtl else ""
    for private, key in ((True, "lang.list.private"), (False, "lang.list.group")):
        assert language_menu.list_text(tr, private=private) == mark + catalog[key] + mark
    confirmation = language_menu.confirmation(tr, code)
    first, rest = catalog["lang.changed"].split("{language}", 1)
    assert confirmation.startswith(mark + first) and confirmation.endswith(rest + mark)
    assert len(rest.strip()) > 20          # the 7.0 sentence is there
    line = language_menu.first_contact_text(tr)
    before, after = catalog["lang.first_contact"].split("{language}", 1)
    assert line.startswith(mark + before) and line.endswith(after + mark)
    assert "/language" in line
