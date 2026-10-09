"""Which language a message uses (section 6), the ``ui_language`` store
behind it, and check 12: a message is never sent in another chat's
language."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aipager import i18n, preferences
from aipager.i18n import locales, resolve

GROUP, DM, ALY, BOB = -1001, 555, 555, 777


def _update(chat_id, user_id, language_code=None):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=user_id, language_code=language_code),
    )


@pytest.fixture
def qps(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps", "fa"}))


def _writes(monkeypatch):
    calls = []
    real = preferences._save_raw
    monkeypatch.setattr(preferences, "_save_raw",
                        lambda store: (calls.append(dict(store)), real(store)))
    return calls


# ---- storage ---------------------------------------------------------------

def test_an_unknown_code_writes_nothing(monkeypatch):
    calls = _writes(monkeypatch)
    for bad in ("de", "qps", "qps-rtl", "xx", "EN", None, 5):
        with pytest.raises(ValueError):
            preferences.set_ui_language(DM, bad)
    assert calls == []
    assert not preferences._PREFERENCES_PATH.exists()
    assert preferences.get_ui_language(DM) == ""


def test_a_choice_is_stored_in_the_chat_entry_and_survives_a_reload(qps):
    preferences.set_ui_language(GROUP, "qps")
    preferences._cache = None
    assert preferences.get_ui_language(GROUP) == "qps"
    assert preferences.get_ui_language(DM) == ""
    preferences.set_ui_language(GROUP, "")
    assert preferences.get_ui_language(GROUP) == ""


def test_a_stored_value_of_the_wrong_shape_reads_as_unset(qps):
    preferences._ensure_loaded()[str(DM)] = {"ui_language": 7}
    assert preferences.get_ui_language(DM) == ""
    assert i18n.for_chat(DM).code == "en"


def test_a_stored_language_that_is_no_longer_enabled_reads_as_english(monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"de"}))
    preferences.set_ui_language(GROUP, "de")
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset())
    assert i18n.for_chat(GROUP).code == "en"


def test_the_language_is_not_a_reply_style():
    assert "ui_language" not in preferences._FIELD_VALIDATORS
    assert "ui_language" not in preferences.Preferences.__dataclass_fields__


def test_move_chat_carries_the_language(qps):
    preferences.set_ui_language(GROUP, "qps")
    assert preferences.move_chat(GROUP, -1002)
    assert preferences.get_ui_language(-1002) == "qps"


# ---- resolution ------------------------------------------------------------

def test_a_private_chat_follows_telegram_until_it_chooses(qps, monkeypatch):
    calls = _writes(monkeypatch)
    assert i18n.for_update(_update(DM, ALY, "fa-IR")).code == "fa"
    # Remembered for background messages to that chat, in memory only.
    assert i18n.for_chat(DM).code == "fa"
    assert calls == []
    preferences.set_ui_language(DM, "en")
    assert i18n.for_update(_update(DM, ALY, "fa")).code == "en"
    # A language we do not have falls back to English and is forgotten.
    preferences.set_ui_language(DM, "")
    assert i18n.for_update(_update(DM, ALY, "ja")).code == "en"
    assert i18n.for_chat(DM).code == "en"


def test_a_group_never_gets_the_dm_language(qps):
    preferences.set_ui_language(DM, "qps")
    i18n.for_update(_update(DM, ALY, "fa"))
    assert i18n.for_chat(GROUP).code == "en"
    assert i18n.for_update(_update(GROUP, ALY, "fa")).code == "en"
    # What only Aly sees in the group is hers.
    assert i18n.for_person(_update(GROUP, ALY, "fa")).code == "qps"


def test_a_dm_never_gets_the_group_language(qps):
    preferences.set_ui_language(GROUP, "qps")
    assert i18n.for_update(_update(GROUP, BOB)).code == "qps"
    assert i18n.for_chat(DM).code == "en"
    assert i18n.for_update(_update(DM, ALY)).code == "en"
    assert i18n.for_person(_update(GROUP, BOB, None)).code == "qps"
    assert i18n.for_person(_update(GROUP, BOB, "fa")).code == "fa"


def test_the_group_seen_language_is_never_recorded(qps):
    i18n.for_update(_update(GROUP, BOB, "fa"))
    assert resolve._seen == {}
    assert i18n.for_chat(BOB).code == "en"


def test_strangers_and_viewers(qps):
    assert i18n.for_stranger(_update(DM, 9, "fa")).code == "fa"
    assert i18n.for_stranger(_update(DM, 9, "ja")).code == "en"
    assert i18n.for_stranger(_update(DM, 9, "qps")).code == "en"
    assert i18n.for_viewer(ALY, "fa").code == "fa"
    preferences.set_ui_language(ALY, "en")
    assert i18n.for_viewer(ALY, "fa").code == "en"
    assert i18n.for_viewer(None, None).code == "en"
    assert i18n.for_viewer(GROUP, "fa").code == "fa"


def test_for_sess_uses_the_session_chat(qps):
    preferences.set_ui_language(GROUP, "qps")
    assert i18n.for_sess(SimpleNamespace(scope_chat_id=GROUP, name="x")).code == "qps"
    assert i18n.for_sess(SimpleNamespace(scope_chat_id=DM, name="x")).code == "en"


def test_junk_updates_give_english():
    assert i18n.for_update(SimpleNamespace()).code == "en"
    assert i18n.for_person(SimpleNamespace()).code == "en"
    assert i18n.for_chat("x").code == "en"
    assert i18n.default_code() == "en"


# ---- check 12 through the real surfaces --------------------------------------

def test_check_12_the_language_surfaces_never_cross_chats(mk_bot, run_async, mk_update,
                                                         monkeypatch):
    """A DM in English and a group set to the pseudo language, driven
    through /language, the language callbacks, /settings and the menu
    registration: every group-bound text is pseudo-marked, and no
    DM-bound text is."""
    from unittest.mock import AsyncMock, MagicMock

    from aipager.policy import load_policy
    from aipager.scope import Member, Scope
    from tests.test_language_setting import edited, tap

    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    bot = mk_bot(scopes=[
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=ALY, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"))),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=ALY, label="aly", role="owner"),)),
    ])
    from pathlib import Path
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    bot._app.bot.set_my_commands = AsyncMock()
    bot._app.bot.set_chat_menu_button = AsyncMock()
    bot._send_keyboard = AsyncMock()
    mark = "⟦"

    # An admin switches the group: the confirmation is the group's.
    text, rows = edited(tap(bot, run_async, "_:set:lang:qps", chat=GROUP, user=ALY))
    assert text.count(mark) >= 2 and rows[-1][0][0].startswith(mark)

    def language_cmd(chat, user):
        update = mk_update("/language", chat_id=chat, user_id=user)
        update.effective_user.language_code = "en"
        run_async(bot._handle_language_cmd(update, MagicMock()))
        call = update.message.reply_text.await_args
        kb = call.kwargs["reply_markup"]
        return call.args[0], [b.text for row in kb.inline_keyboard for b in row]

    group_text, group_buttons = language_cmd(GROUP, BOB)
    dm_text, dm_buttons = language_cmd(DM, ALY)
    assert group_text.startswith(mark) and group_buttons[-1].startswith(mark)
    assert mark not in dm_text and not any(mark in b for b in dm_buttons)

    for chat, user, marked in ((GROUP, BOB, True), (DM, ALY, False)):
        text, rows = edited(tap(bot, run_async, "_:set:lang", chat=chat, user=user))
        assert (mark in text) is marked
        assert (mark in rows[-1][0][0]) is marked
        _text, rows = edited(tap(bot, run_async, "_:set", chat=chat, user=user))
        assert (mark in rows[0][0][0]) is marked, rows[0]

    menus = {c.kwargs["scope"].chat_id: {cmd.command: cmd.description for cmd in c.args[0]}
             for c in bot._app.bot.set_my_commands.await_args_list}
    assert menus[GROUP]["language"].startswith(mark)
    run_async(bot._update_bot_commands_per_scope())
    menus.update({c.kwargs["scope"].chat_id: {cmd.command: cmd.description
                                              for cmd in c.args[0]}
                  for c in bot._app.bot.set_my_commands.await_args_list})
    assert mark not in menus[DM]["language"]


def test_check_12_a_persian_group_and_a_russian_dm_never_cross(mk_bot, run_async, monkeypatch):
    """A group in Persian whose session's last sender keeps Russian in
    their own chat, and a private chat in Russian: every shared message is
    in its own chat's language, never the other's (G72)."""
    import re
    from unittest.mock import AsyncMock, MagicMock

    from aipager.bot import animation, dashboard
    from aipager.state import Status, TrackedSession

    persian, cyrillic = re.compile("[؀-ۿ]"), re.compile("[Ѐ-ӿ]")
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    preferences.set_ui_language(DM, "ru")
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=5))
    sessions = {}
    for chat, name in ((GROUP, "claude-g"), (DM, "claude-d")):
        s = TrackedSession(name=name, label=name[-1], status=Status.BUSY)
        s.scope_chat_id, s.scope_kind = chat, ("group" if chat < 0 else "dm")
        s.turn_sender_id = BOB
        s.tool_history = [("🔑 Bash: ls → Allowed", True), ("Read: /a", False)]
        bot.registry._sessions[name] = s
        sessions[chat] = s

    def surfaces(chat):
        s = sessions[chat]
        out = [animation.build_stream_card(s, "Working"),
               bot._build_busy_text(s.label, "Working", s),
               bot._render_pinned(chat, [s])[0]]
        bot._app.bot.send_message.reset_mock()
        run_async(bot._send_keyboard(level="main", chat_id=chat))
        kb = bot._app.bot.send_message.await_args.kwargs["reply_markup"]
        out += [b.text for row in kb.keyboard for b in row]
        return " ".join(out)

    group, dm = surfaces(GROUP), surfaces(DM)
    assert persian.search(group) and not cyrillic.search(group)
    assert cyrillic.search(dm) and not persian.search(dm)
