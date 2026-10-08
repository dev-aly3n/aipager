"""SC-18 (the /settings half) and SC-19: "Problem reports: Ask me / Off"
in the owner's DM only; Off silences offers; after two declines, a line
says automatic offers are off with a button that turns them back on
(design.md Success criteria 18, 19; spec.md item 4; entrypoints.md
"Telegram commands", "Inline buttons").

Methods: equivalence partitioning over where /settings is asked (owner
DM, group, a non-owner's own DM) and the stored state (ask, off,
auto_off, env off); error guessing: a non-owner forging the section's
buttons, the root re-rendered by Back."""

from __future__ import annotations

import pytest

from aipager import preferences
from aipager.bot import settings_menu
from aipager.report import policy, store


def _rows(markup):
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return []
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def _settings(bot, drive, h, run_async, *, user=None, chat=None):
    user = h.OWNER if user is None else user
    chat = h.OWNER if chat is None else chat
    run_async(drive(bot).command("/settings", user=user, chat=chat))
    sent = [e for e in bot.tg.sent(chat) if "Settings" in (e["text"] or "")]
    return sent[-1] if sent else None


def _report_row(card):
    return [t for t, d in _rows(card["markup"]) if d == "_:rp:set"] if card else []


@pytest.fixture
def admin_dm_bot(mk_bot, h):
    """The owner's DM, the team group, and the admin's own DM scope."""
    bot = mk_bot(scopes=[h.owner_dm_scope(), h.owner_dm_scope(h.ADMIN, role="admin"),
                         h.group_scope()])
    bot.policy = h._policy()
    bot.tg = h.FakeTelegram(bot)
    return bot


# ---- SC-19: the row ------------------------------------------------------------------

def test_owner_dm_settings_has_problem_reports_ask_me(bot, drive, h, run_async):
    assert _report_row(_settings(bot, drive, h, run_async)) == ["🐞 Problem reports: Ask me"]


def test_owner_dm_settings_shows_off(bot, drive, h, run_async):
    preferences.set_problem_reports("off")
    assert _report_row(_settings(bot, drive, h, run_async)) == ["🐞 Problem reports: Off"]


def test_owner_dm_settings_shows_offers_off(bot, drive, h, run_async):
    store.save_policy(policy.State(auto_off=True, declines_in_row=2))
    assert _report_row(_settings(bot, drive, h, run_async)) == ["🐞 Problem reports: offers off"]


def test_scope_owner_dm_settings_has_row(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    assert _report_row(_settings(bot, drive, h, run_async)) != []


def test_group_settings_has_no_row(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    assert _report_row(_settings(bot, drive, h, run_async, chat=h.GROUP)) == []


def test_admin_own_dm_settings_has_no_row(admin_dm_bot, drive, h, run_async):
    bot = admin_dm_bot
    assert _report_row(_settings(bot, drive, h, run_async, user=h.ADMIN, chat=h.ADMIN)) == []


def test_render_settings_root_without_state_has_no_row(h):
    """The renderer adds the row only when given (entrypoints.md)."""
    _text, markup = settings_menu.render_settings_root(h.OWNER)
    assert all(d != "_:rp:set" for _, d in _rows(markup))


def test_render_settings_root_with_state_has_row(h):
    _text, markup = settings_menu.render_settings_root(h.OWNER, problem_reports="ask")
    assert any(d == "_:rp:set" for _, d in _rows(markup))


def test_back_rerender_keeps_the_row(bot, drive, h, run_async):
    d = drive(bot)
    card = _settings(bot, drive, h, run_async)
    run_async(d.tap("_:set:layout", message_id=card["message_id"]))
    run_async(d.tap("_:set:back", message_id=card["message_id"]))
    assert any(dd == "_:rp:set" for _, dd in bot.tg.buttons_of(h.OWNER, card["message_id"]))


# ---- the section -----------------------------------------------------------------------

def _section(bot, drive, h, run_async, *, user=None):
    d = drive(bot)
    card = _settings(bot, drive, h, run_async)
    mid = card["message_id"]
    run_async(d.tap("_:rp:set", user=user or h.OWNER, message_id=mid))
    return d, mid


def test_section_offers_ask_and_off(bot, drive, h, run_async):
    _, mid = _section(bot, drive, h, run_async)
    data = [dd for _, dd in bot.tg.buttons_of(h.OWNER, mid)]
    assert {"_:rp:set:ask", "_:rp:set:off", "_:set:back"} <= set(data)


def test_section_marks_the_current_choice(bot, drive, h, run_async):
    _, mid = _section(bot, drive, h, run_async)
    labels = {dd: t for t, dd in bot.tg.buttons_of(h.OWNER, mid)}
    assert "✅" in labels["_:rp:set:ask"] and "✅" not in labels["_:rp:set:off"]


def test_tap_off_stores_off(bot, drive, h, run_async):
    d, mid = _section(bot, drive, h, run_async)
    run_async(d.tap("_:rp:set:off", message_id=mid))
    assert preferences.get_problem_reports() == "off"


def test_tap_ask_after_off_stores_ask(bot, drive, h, run_async):
    preferences.set_problem_reports("off")
    d, mid = _section(bot, drive, h, run_async)
    run_async(d.tap("_:rp:set:ask", message_id=mid))
    assert preferences.get_problem_reports() == "ask"


def test_stranger_tap_off_changes_nothing(bot, drive, h, run_async):
    d, mid = _section(bot, drive, h, run_async)
    run_async(d.tap("_:rp:set:off", user=h.STRANGER, message_id=mid))
    assert preferences.get_problem_reports() == "ask"


def test_group_admin_tap_off_changes_nothing(make_bot, drive, h, run_async):
    bot = make_bot("scope")
    run_async(drive(bot).tap("_:rp:set:off", user=h.ADMIN, chat=h.GROUP))
    assert preferences.get_problem_reports() == "ask"


def test_off_does_not_touch_other_chats_preferences(bot, drive, h, run_async):
    before = preferences.get_preferences(h.GROUP)
    d, mid = _section(bot, drive, h, run_async)
    run_async(d.tap("_:rp:set:off", message_id=mid))
    assert preferences.get_preferences(h.GROUP) == before


def test_problem_reports_not_in_per_chat_schema():
    assert all("problem" not in str(s).lower() for s in settings_menu.settings_schema())


def test_default_is_ask():
    assert preferences.get_problem_reports() == "ask"


@pytest.mark.parametrize("bad", ["maybe", "", "ASK", None, 1])
def test_setter_rejects_anything_else(bad):
    with pytest.raises(ValueError):
        preferences.set_problem_reports(bad)


def test_off_keeps_the_manual_button(bot, drive, h, run_async):
    """Off silences only the automatic offer: /help still offers a report."""
    preferences.set_problem_reports("off")
    run_async(drive(bot).command("/help"))
    assert any(d == "_:rp:open" for e in bot.tg.sent(h.OWNER) for _, d in _rows(e["markup"]))


def test_off_keeps_the_manual_preview(bot, drive, h, run_async):
    preferences.set_problem_reports("off")
    run_async(drive(bot).tap("_:rp:open"))
    assert len(bot.tg.cards(h.OWNER)) == 1


# ---- SC-18: automatic offers off, and back on ---------------------------------------

def _auto_off_section(bot, drive, h, run_async):
    store.save_policy(policy.State(auto_off=True, declines_in_row=2))
    return _section(bot, drive, h, run_async)


def test_section_says_automatic_offers_off(bot, drive, h, run_async):
    _, mid = _auto_off_section(bot, drive, h, run_async)
    assert ("Automatic offers: off (you said no to two offers in a row)."
            in bot.tg.text_of(h.OWNER, mid))


def test_section_has_turn_back_on_button(bot, drive, h, run_async):
    _, mid = _auto_off_section(bot, drive, h, run_async)
    assert ("Turn offers back on", "_:rp:set:on") in bot.tg.buttons_of(h.OWNER, mid)


def test_reenable_restores_auto_off_false(bot, drive, h, run_async):
    d, mid = _auto_off_section(bot, drive, h, run_async)
    run_async(d.tap("_:rp:set:on", message_id=mid))
    assert store.policy_state().auto_off is False


def test_reenable_is_persisted(bot, drive, h, run_async):
    import json
    d, mid = _auto_off_section(bot, drive, h, run_async)
    run_async(d.tap("_:rp:set:on", message_id=mid))
    assert json.loads(h.reports_file().read_text())["policy"]["auto_off"] is False


def test_reenable_drops_the_off_line(bot, drive, h, run_async):
    d, mid = _auto_off_section(bot, drive, h, run_async)
    run_async(d.tap("_:rp:set:on", message_id=mid))
    assert "Automatic offers: off" not in bot.tg.text_of(h.OWNER, mid)


def test_stranger_reenable_changes_nothing(bot, drive, h, run_async):
    d, mid = _auto_off_section(bot, drive, h, run_async)
    run_async(d.tap("_:rp:set:on", user=h.STRANGER, message_id=mid))
    assert store.policy_state().auto_off is True


def test_section_without_auto_off_has_no_turn_on(bot, drive, h, run_async):
    _, mid = _section(bot, drive, h, run_async)
    assert all(dd != "_:rp:set:on" for _, dd in bot.tg.buttons_of(h.OWNER, mid))


def test_section_says_env_off(bot, drive, h, run_async, monkeypatch):
    monkeypatch.setenv("AIPAGER_REPORT_PROMPTS", "0")
    _, mid = _section(bot, drive, h, run_async)
    assert ("Offers are also off on this machine through AIPAGER_REPORT_PROMPTS=0."
            in bot.tg.text_of(h.OWNER, mid))


def test_two_declines_then_reenable_end_to_end(offer_world, h, run_async):
    """The whole loop: an offer declined twice turns offers off, and the
    /settings button turns them back on."""
    w = offer_world()
    store.save_policy(policy.State(declines_in_row=1))

    async def go():
        await w.owner_acts()
        notices = await w.run_ticks()
        n = notices[0]
        ts = [b.callback_data for row in n["markup"].inline_keyboard for b in row][2]
        await w.drive.tap(ts, chat=n["chat_id"], message_id=n["message_id"])
        await w.drive.command("/settings")
        card = [e for e in w.bot.tg.sent(h.OWNER) if "Settings" in (e["text"] or "")][-1]
        await w.drive.tap("_:rp:set", message_id=card["message_id"])
        await w.drive.tap("_:rp:set:on", message_id=card["message_id"])
    run_async(go())
    assert store.policy_state().auto_off is False
