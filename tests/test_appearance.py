"""/settings → 🎨 Appearance: button colours per chat (aipager/bot/appearance.py,
aipager/preferences.py's appearance store).

Telegram allows three button styles (primary blue, success green, danger
red) or none; every choice starts on Normal, which sends no style at all.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences
from aipager.bot import appearance, settings_menu
from aipager.policy import load_policy
from aipager.scope import Member, Scope

DM = 555
GROUP = -1001


def _buttons(kb):
    return [b for row in kb.to_dict()["inline_keyboard"] for b in row]


# ---- the store ---------------------------------------------------------------------

def test_every_choice_starts_on_normal():
    assert preferences.get_appearance(DM) == preferences.Appearance(
        stop="normal", prompt="normal", app="normal", settings="normal")


@pytest.mark.parametrize("field,value", [
    ("stop", "blue"), ("stop", "green"), ("stop", "red"), ("prompt", "coloured"),
    ("app", "red"), ("settings", "highlight"),
])
def test_a_choice_is_stored_and_read_back(field, value):
    preferences.set_appearance(DM, field, value)
    assert getattr(preferences.get_appearance(DM), field) == value
    preferences._cache = None                       # read again from disk
    assert getattr(preferences.get_appearance(DM), field) == value


def test_normal_removes_the_stored_choice():
    preferences.set_appearance(DM, "stop", "red")
    preferences.set_appearance(DM, "stop", "normal")
    assert "appearance_stop" not in preferences._ensure_loaded().get(str(DM), {})


@pytest.mark.parametrize("field,value", [
    ("stop", "purple"), ("stop", "#ff0000"), ("prompt", "blue"), ("settings", "red"),
    ("layout", "card"), ("nope", "normal"),
], ids=["purple", "hex", "prompt-blue", "settings-red", "a-preference", "unknown"])
def test_a_value_telegram_cannot_draw_is_refused_before_writing(field, value):
    with pytest.raises(ValueError):
        preferences.set_appearance(DM, field, value)
    assert preferences.get_appearance(DM) == preferences.Appearance()


def test_a_bad_stored_value_reads_as_normal():
    store = preferences._ensure_loaded()
    store[str(DM)] = {"appearance_stop": "purple", "appearance_app": 7}
    assert preferences.get_appearance(DM) == preferences.Appearance()


# ---- what the keyboards use -------------------------------------------------------

@pytest.mark.parametrize("colour,style", [("normal", None), ("blue", "primary"),
                                          ("green", "success"), ("red", "danger")])
def test_colours_map_to_telegrams_styles(colour, style):
    preferences.set_appearance(DM, "stop", colour)
    preferences.set_appearance(DM, "app", colour)
    assert appearance.stop_style(DM) == style
    assert appearance.app_style(DM) == style


def test_coloured_prompt_buttons():
    assert appearance.prompt_styles(DM) == (None, None)
    preferences.set_appearance(DM, "prompt", "coloured")
    assert appearance.prompt_styles(DM) == ("success", "danger")


def test_the_settings_highlight():
    assert appearance.choice_style(DM) is None
    preferences.set_appearance(DM, "settings", "highlight")
    assert appearance.choice_style(DM) == "primary"


def test_a_chat_that_cannot_be_told_gets_the_plain_look():
    assert appearance.stop_style(None) is None
    assert appearance.stop_style("x") is None


# ---- the /settings pages -----------------------------------------------------------

def test_the_root_menu_has_the_appearance_row():
    _text, kb = settings_menu.render_settings_root(DM)
    assert "_:set:ap" in [b["callback_data"] for b in _buttons(kb)]


def test_the_page_lists_every_choice_with_its_value():
    preferences.set_appearance(DM, "stop", "red")
    text, kb = appearance.render_page(DM)
    assert "Appearance" in text
    rows = _buttons(kb)
    assert [b["callback_data"] for b in rows] == [
        "_:set:ap:stop", "_:set:ap:prompt", "_:set:ap:app", "_:set:ap:settings",
        "_:set:back"]
    assert rows[0]["text"] == "🛑 Stop button: Red ✅"
    assert rows[1]["text"] == "🔐 Allow and Deny buttons: Normal"


def test_a_choice_page_marks_the_current_option_and_goes_back():
    preferences.set_appearance(DM, "stop", "green")
    text, kb = appearance.render_field(DM, "stop")
    assert "busy card" in text
    rows = _buttons(kb)
    assert [b["text"] for b in rows[:4]] == ["Normal", "Blue", "Green ✅", "Red"]
    assert rows[-1]["callback_data"] == "_:set:ap"
    assert all("style" not in b for b in rows)


def test_highlight_paints_the_current_choice_blue_everywhere_in_settings():
    preferences.set_appearance(DM, "settings", "highlight")
    _text, kb = appearance.render_field(DM, "settings")
    marked = [b for b in _buttons(kb) if "style" in b]
    assert [(b["text"], b["style"]) for b in marked] == [("Current choice in blue ✅",
                                                          "primary")]
    _text, kb = settings_menu.render_settings_section(DM, "length")
    marked = [b for b in _buttons(kb) if "style" in b]
    assert len(marked) == 1 and marked[0]["text"].endswith("✅")


def test_an_unknown_choice_page_is_none():
    assert appearance.render_field(DM, "colour") is None


def test_the_schema_carries_the_dots():
    schema = {g["field"]: g for g in appearance.appearance_schema()}
    assert list(schema) == ["stop", "prompt", "app", "settings"]
    stop = {o["value"]: o["swatch"] for o in schema["stop"]["options"]}
    assert stop == {"normal": [], "blue": ["blue"], "green": ["green"], "red": ["red"]}
    prompt = {o["value"]: o["swatch"] for o in schema["prompt"]["options"]}
    assert prompt == {"normal": [], "coloured": ["green", "red"]}
    assert schema["settings"]["options"][1]["swatch"] == ["blue"]


# ---- the callbacks ---------------------------------------------------------------------

@pytest.fixture
def mk_query():
    def _mk(callback_data, *, user_id=12345, chat_id=DM):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = 42
        query.message.text = ""
        query.message.chat = MagicMock()
        query.message.chat.id = chat_id
        query.from_user = MagicMock()
        query.from_user.id = user_id
        query.from_user.username = "alice"
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        return update, query
    return _mk


def _group_bot(mk_bot):
    bot = mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="dev", members=(
        Member(id=1, label="owner", role="owner"), Member(id=2, label="bob", role="user")))])
    bot.policy = load_policy()
    return bot


def test_the_page_opens_in_place(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set:ap")
    run_async(bot._handle_callback(update, MagicMock()))
    assert "Appearance" in query.edit_message_text.await_args.args[0]


def test_a_dm_tap_sets_the_choice(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set:ap:stop:red")
    run_async(bot._handle_callback(update, MagicMock()))
    assert preferences.get_appearance(DM).stop == "red"
    kb = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert "Red ✅" in [b["text"] for b in _buttons(kb)]


def test_a_member_cannot_change_a_groups_look(mk_bot, mk_query, run_async):
    bot = _group_bot(mk_bot)
    update, query = mk_query("_:set:ap:stop:red", chat_id=GROUP, user_id=2)
    run_async(bot._handle_callback(update, MagicMock()))
    assert preferences.get_appearance(GROUP).stop == "normal"
    query.edit_message_text.assert_not_awaited()
    toasts = [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]
    assert any("admin" in t.lower() for t in toasts)


def test_an_admin_changes_a_groups_look_and_is_named(mk_bot, mk_query, run_async):
    bot = _group_bot(mk_bot)
    update, query = mk_query("_:set:ap:prompt:coloured", chat_id=GROUP, user_id=1)
    run_async(bot._handle_callback(update, MagicMock()))
    assert preferences.get_appearance(GROUP).prompt == "coloured"
    assert "Changed by" in query.edit_message_text.await_args.args[0]


@pytest.mark.parametrize("data", ["_:set:ap:stop:purple", "_:set:ap:colour",
                                  "_:set:ap:stop:red:extra"],
                         ids=["bad-value", "bad-field", "too-long"])
def test_a_bad_tap_changes_nothing(mk_bot, mk_query, run_async, data):
    bot = mk_bot()
    update, query = mk_query(data)
    run_async(bot._handle_callback(update, MagicMock()))
    assert preferences.get_appearance(DM) == preferences.Appearance()
    query.edit_message_text.assert_not_awaited()


# ---- the App buttons ---------------------------------------------------------------------

def test_the_pinned_bars_app_button_takes_the_chats_colour(mk_bot):
    bot = mk_bot()
    bot._miniapp_url = "https://words-here.trycloudflare.com"
    bot._miniapp_button_chats = lambda: [DM]
    _text, kb = bot._render_pinned(DM, [])
    [app] = [b for b in _buttons(kb) if "web_app" in b]
    assert "style" not in app
    preferences.set_appearance(DM, "app", "green")
    _text, kb = bot._render_pinned(DM, [])
    [app] = [b for b in _buttons(kb) if "web_app" in b]
    assert app["style"] == "success"
    json.dumps(kb.to_dict())                               # still a valid payload


def test_the_stop_under_a_question_takes_the_chats_colour(mk_bot):
    from aipager.bot.transport import resolve_chat_id_int
    from aipager.state import TrackedSession
    bot = mk_bot()
    sess = TrackedSession(name="claude-x1", label="x1", scope_chat_id=DM)
    bot.registry._sessions[sess.name] = sess
    preferences.set_appearance(resolve_chat_id_int(sess) or 0, "stop", "green")
    kb = bot._build_inline_ask_keyboard(sess, [{"label": "A"}, {"label": "B"}])
    stop = _buttons(kb)[-1]
    assert stop["style"] == "success"
    assert all("style" not in b for b in _buttons(kb)[:-1])


def test_the_app_button_under_app_takes_the_chats_colour(mk_bot):
    bot = mk_bot()
    bot._miniapp_url = "https://words-here.trycloudflare.com"
    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = DM
    [[app]] = bot._app_button_row(update)
    assert app.to_dict().get("style") is None
    preferences.set_appearance(DM, "app", "blue")
    [[app]] = bot._app_button_row(update)
    assert app.to_dict()["style"] == "primary"


def test_a_tap_from_a_chat_that_cannot_be_told_needs_an_admin(mk_bot, mk_query, run_async):
    # An inline message's tap carries no chat: treated as a group, so only
    # an admin may change the look (the chat-wide store's entry 0).
    bot = _group_bot(mk_bot)
    update, query = mk_query("_:set:ap:stop:red", user_id=2)
    update.effective_chat = None
    update.message = None
    query.message = None
    assert run_async(appearance.handle_callback(bot, update, query, "_", "set:ap:stop:red"))
    assert preferences.get_appearance(0) == preferences.Appearance()
    query.edit_message_text.assert_not_awaited()
    toasts = [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]
    assert any("admin" in t.lower() for t in toasts)


# ---- writes that change nothing --------------------------------------------------------

def test_re_picking_the_choice_in_force_writes_nothing(monkeypatch):
    preferences.set_appearance(DM, "stop", "red")
    saves = []
    monkeypatch.setattr(preferences, "_save_raw", lambda store: saves.append(store))
    preferences.set_appearance(DM, "stop", "red")
    preferences.set_appearance(DM + 1, "stop", "normal")
    assert saves == []
    assert str(DM + 1) not in preferences._ensure_loaded()


def test_back_to_normal_leaves_no_empty_entry():
    preferences.set_appearance(DM, "app", "blue")
    preferences.set_appearance(DM, "app", "normal")
    assert str(DM) not in preferences._ensure_loaded()
    # So a supergroup move into that id is not refused.
    preferences.set_appearance(DM, "app", "blue")
    preferences.set_appearance(DM, "app", "normal")
    preferences.set_preference(GROUP, "layout", "merged")
    assert preferences.move_chat(GROUP, DM)


def test_a_bad_stored_value_is_replaced_by_normal():
    preferences._ensure_loaded()[str(DM)] = {"appearance_stop": "purple", "layout": "merged"}
    preferences.set_appearance(DM, "stop", "normal")
    assert "appearance_stop" not in preferences._ensure_loaded()[str(DM)]


def test_appearance_fields_never_share_a_name_with_a_preference():
    # The Mini App's chat line finds a field's title by name in both lists.
    assert not {g["field"] for g in settings_menu.settings_schema()} & set(
        preferences.APPEARANCE_FIELDS)


# ---- Highlight on every /settings page that marks a choice -----------------------------

def _styled(kb):
    return [(b["text"], b["style"]) for b in _buttons(kb) if "style" in b]


def _one_blue_tick(kb):
    styled = _styled(kb)
    assert len(styled) == 1, styled
    assert styled[0][0].endswith("✅") and styled[0][1] == "primary"


def test_the_language_page_highlights_its_choice(mk_bot, mk_query, run_async):
    bot = mk_bot()
    update, query = mk_query("_:set:lang")
    run_async(bot._handle_callback(update, MagicMock()))
    assert _styled(query.edit_message_text.await_args.kwargs["reply_markup"]) == []
    preferences.set_appearance(DM, "settings", "highlight")
    update, query = mk_query("_:set:lang")
    run_async(bot._handle_callback(update, MagicMock()))
    _one_blue_tick(query.edit_message_text.await_args.kwargs["reply_markup"])


@pytest.mark.parametrize("view", ["", "model", "cwd"], ids=["mode", "model", "folder"])
def test_the_new_sessions_pages_highlight_their_choice(mk_bot, view):
    from aipager.bot import new_flow
    bot = mk_bot()
    _text, kb = new_flow.render_new_session_defaults(bot, DM, view=view)
    assert _styled(kb) == []
    preferences.set_appearance(DM, "settings", "highlight")
    _text, kb = new_flow.render_new_session_defaults(bot, DM, view=view)
    _one_blue_tick(kb)


def test_a_sessions_own_preference_page_highlights_its_choice():
    from aipager import state
    from aipager.bot import session_parity
    from aipager.state import TrackedSession
    sess = TrackedSession(name="claude-x1", label="x1", scope_chat_id=DM)
    _text, kb = session_parity.render_session_preferences_field(
        sess, DM, "length", cb_prefix="_:spref:x1")
    assert _styled(kb) == []
    preferences.set_appearance(DM, "settings", "highlight")
    _text, kb = session_parity.render_session_preferences_field(
        sess, DM, "length", cb_prefix="_:spref:x1")
    _one_blue_tick(kb)                                    # "Use chat default ✅"
    attr = state.PREFERENCE_OVERRIDE_FIELDS["answer_length"]
    setattr(sess, attr, "short")                          # the session's own choice
    _text, kb = session_parity.render_session_preferences_field(
        sess, DM, "length", cb_prefix="_:spref:x1")
    _one_blue_tick(kb)
    assert _styled(kb)[0][0].startswith("Short")


def test_the_problem_reports_page_highlights_its_choice():
    from aipager.bot import report_flow
    _text, kb = report_flow.render_settings_section(None, DM)
    assert _styled(kb) == []
    preferences.set_appearance(DM, "settings", "highlight")
    for choice in ("ask", "off"):
        preferences.set_problem_reports(choice)
        _text, kb = report_flow.render_settings_section(None, DM)
        _one_blue_tick(kb)
        assert [b["callback_data"] for b in _buttons(kb) if "style" in b] == [
            f"_:rp:set:{choice}"]


def test_the_language_page_after_a_change_keeps_the_highlight(mk_bot, mk_query, run_async):
    bot = mk_bot()
    preferences.set_appearance(DM, "settings", "highlight")
    update, query = mk_query("_:set:lang:en")
    run_async(bot._handle_callback(update, MagicMock()))
    assert preferences.get_ui_language(DM) == "en"
    _one_blue_tick(query.edit_message_text.await_args.kwargs["reply_markup"])


def test_the_language_command_highlights_its_choice(mk_bot, mk_update, run_async):
    def _language_kb():
        update = mk_update("/language", chat_id=DM, user_id=12345)
        update.effective_chat.type = "private"
        run_async(bot._handle_language_cmd(update, MagicMock()))
        return update.message.reply_text.await_args.kwargs["reply_markup"]

    bot = mk_bot()
    assert _styled(_language_kb()) == []
    preferences.set_appearance(DM, "settings", "highlight")
    _one_blue_tick(_language_kb())


def test_a_retired_model_still_chosen_is_highlighted(mk_bot):
    from aipager.bot import new_flow
    from aipager.config import RETIRED_MODEL_LABELS
    retired = next(iter(RETIRED_MODEL_LABELS))
    preferences._ensure_loaded()[str(DM)] = {preferences._NEW_SESSION_KEYS["model"]: retired}
    preferences.set_appearance(DM, "settings", "highlight")
    _text, kb = new_flow.render_new_session_defaults(mk_bot(), DM, view="model")
    _one_blue_tick(kb)
    assert retired in _styled(kb)[0][0]
