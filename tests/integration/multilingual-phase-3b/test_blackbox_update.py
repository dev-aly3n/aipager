"""Black-box: /update, its taps, the post-restart line and the renderers
the Mini App shares, in English, Persian and Russian (phase 3b design,
success criteria 6, 7, 11). Every Telegram call is a recorded fake."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences, self_update
from aipager.bot import update_flow
from aipager.state import Status
from tests import i18n_bb3b as L
from tests import i18n_ml2
from tests.i18n_ml2 import BOB, DM, GROUP, OWNER, callback, message
from tests.i18n_surfaces import update_status

EN, FA, RU = i18n.get("en"), i18n.get("fa"), i18n.get("ru")

# Equivalence classes of a check's result (one per offer/line variant).
STATUSES = {
    "both_newer": update_status(),
    "aipager_not_upgradable": update_status(cc_newer=False, cc_latest="2.1.300",
                                            upgradable=False, reason="editable install"),
    "claude_blocked": update_status(ap_newer=False, latest="1.0.1",
                                    blocked="npm prefix not writable"),
    "both_current": update_status(ap_newer=False, latest="1.0.1", cc_newer=False,
                                  cc_latest="2.1.300"),
    "aipager_unchecked": update_status(latest=None, cc_newer=False, cc_latest="2.1.300"),
    "claude_unknown_installed_newer": update_status(ap_newer=False, latest="1.0.1", cc=None,
                                                    installed="1.0.2"),
    "manual_restart": update_status(cc_latest=None, automatic=False,
                                    restart_reason="no service unit"),
}
IDS = list(STATUSES)
VERSIONS = ("1.0.1", "1.0.2", "2.1.300", "2.1.310")


@pytest.fixture
def bot(mk_bot):
    return i18n_ml2.make_bot(mk_bot)


def _texts(offer):
    return [t for t in (*offer.lines, offer.summary, offer.restart, *offer.notes, offer.label)
            if t]


def _callback_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# ---- update_offer / check_payload / render_check ---------------------------------

@pytest.mark.parametrize("case", IDS)
def test_no_tr_is_english(case):
    st = STATUSES[case]
    assert update_flow.update_offer(st) == update_flow.update_offer(st, tr=EN)


@pytest.mark.parametrize("case", IDS)
def test_offer_kind_is_the_same_id_in_every_language(case):
    st = STATUSES[case]
    kinds = {update_flow.update_offer(st, tr=t).kind for t in (EN, FA, RU)}
    assert len(kinds) == 1


@pytest.mark.parametrize("case", IDS)
def test_persian_offer_lines_follow_the_persian_rules(case):
    offer = update_flow.update_offer(STATUSES[case], tr=FA)
    assert L.fa_problems("\n".join(_texts(offer))) == []


@pytest.mark.parametrize("case", IDS)
def test_russian_offer_lines_follow_the_russian_rules(case):
    offer = update_flow.update_offer(STATUSES[case], tr=RU)
    assert L.ru_problems("\n".join(_texts(offer))) == []


@pytest.mark.parametrize("case", IDS)
def test_persian_offer_keeps_versions_latin(case):
    st = STATUSES[case]
    english = " ".join(_texts(update_flow.update_offer(st, tr=EN)))
    persian = L.bare(" ".join(_texts(update_flow.update_offer(st, tr=FA))))
    shown = [v for v in VERSIONS if v in english]
    assert [v for v in shown if v not in persian] == []


@pytest.mark.parametrize("case", IDS)
def test_render_check_callback_data_is_the_same_in_every_language(case):
    st = STATUSES[case]
    data = [_callback_data(update_flow.render_check(st, show_paths=True, tr=t)[1])
            for t in (EN, FA, RU)]
    assert data[0] == data[1] == data[2]


@pytest.mark.parametrize("case", IDS)
def test_render_check_without_tr_is_english(case):
    st = STATUSES[case]
    a = update_flow.render_check(st, show_paths=False)
    b = update_flow.render_check(st, show_paths=False, tr=EN)
    assert (a[0], _callback_data(a[1])) == (b[0], _callback_data(b[1]))


@pytest.mark.parametrize("case", IDS)
def test_render_check_in_persian_follows_the_rules(case):
    text, _kb = update_flow.render_check(STATUSES[case], show_paths=True, tr=FA)
    assert L.fa_problems(text) == []


@pytest.mark.parametrize("case", IDS)
def test_render_check_in_russian_follows_the_rules(case):
    text, _kb = update_flow.render_check(STATUSES[case], show_paths=True, tr=RU)
    assert L.ru_problems(text) == []


@pytest.mark.parametrize("case", IDS)
def test_check_payload_source_is_not_translated(case):
    st = STATUSES[case]
    sources = {update_flow.check_payload(st, show_paths=True, tr=t)["source"]
               for t in (EN, FA, RU)}
    assert len(sources) == 1


def test_check_payload_offer_label_is_translated_but_kind_kept():
    st = STATUSES["both_newer"]
    en = update_flow.check_payload(st, show_paths=True, tr=EN)["offer"]
    ru = update_flow.check_payload(st, show_paths=True, tr=RU)["offer"]
    assert (ru["kind"] == en["kind"], L.is_ru(ru["label"])) == (True, True)


def test_check_prompt_markup_english_label_is_the_constant():
    kb = update_flow.check_prompt_markup()
    assert kb.inline_keyboard[0][0].text == update_flow.CHECK_BUTTON_TEXT


@pytest.mark.parametrize("tr", [FA, RU], ids=["fa", "ru"])
def test_check_prompt_markup_keeps_its_callback_data(tr):
    assert _callback_data(update_flow.check_prompt_markup(tr=tr)) == ["_:up:chk"]


@pytest.mark.parametrize("name", [
    "DENIED_TEXT", "STALE_TEXT", "MENU_OUT_OF_DATE_TEXT", "CHECK_PROMPT_TEXT",
    "CHECK_BUTTON_TEXT", "CHECKING_TEXT", "UP_TO_DATE_TEXT", "CHECK_FAILED_TEXT",
    "CANT_UPDATE_HERE_TEXT", "OFFER_STALE_TEXT"])
def test_module_constants_are_still_english_strings(name):
    value = getattr(update_flow, name)
    assert isinstance(value, str) and not L.is_fa(value) and not L.is_ru(value)


def test_menu_out_of_date_keeps_the_update_command_whole_in_persian():
    assert "/update" in FA.text(update_flow.MENU_OUT_OF_DATE_TEXT)


# ---- /update command -------------------------------------------------------------

def _run_update_cmd(bot, run_async, *, chat, user):
    update = message("/update", chat=chat, user=user)
    run_async(update_flow.handle_update_cmd(bot, update, MagicMock()))
    return update.message.reply_text.await_args


@pytest.mark.parametrize("code,check", [("fa", L.fa_problems), ("ru", L.ru_problems)])
def test_admin_update_prompt_follows_the_dm_language(bot, run_async, code, check):
    preferences.set_ui_language(DM, code)
    call = _run_update_cmd(bot, run_async, chat=DM, user=OWNER)
    assert check(call.args[0]) == []


def test_admin_update_prompt_button_keeps_its_callback_data_in_persian(bot, run_async):
    preferences.set_ui_language(DM, "fa")
    call = _run_update_cmd(bot, run_async, chat=DM, user=OWNER)
    assert _callback_data(call.kwargs["reply_markup"]) == ["_:up:chk"]


def test_update_prompt_with_nothing_stored_is_english(bot, run_async):
    call = _run_update_cmd(bot, run_async, chat=DM, user=OWNER)
    assert call.args[0] == EN.html(update_flow.CHECK_PROMPT_TEXT)


def test_non_admin_denied_reply_is_in_the_groups_language_not_the_persons(bot, run_async):
    """A reply in the group is a shared message: the chat's language."""
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    call = _run_update_cmd(bot, run_async, chat=GROUP, user=BOB)
    assert call.args[0] == FA.text(update_flow.DENIED_TEXT)


# ---- taps: pop-ups in the tapper's language --------------------------------------

def _tap(bot, run_async, data, *, chat, user):
    update, query = callback(data, chat=chat, user=user)
    query.message.edit_text = AsyncMock()
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def _alerts(query):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in query.answer.await_args_list]


def test_member_with_no_dm_choice_gets_the_groups_language_alert(bot, run_async):
    """No DM choice and no Telegram language: the tapped chat's language."""
    preferences.set_ui_language(GROUP, "fa")
    update, query = callback("_:up:chk", chat=GROUP, user=BOB)
    query.from_user.language_code = None
    run_async(bot._handle_callback(update, MagicMock()))
    assert _alerts(query) == [FA.text(update_flow.DENIED_TEXT)]


def test_member_whose_dm_is_english_gets_an_english_alert_in_a_persian_group(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "en")
    query = _tap(bot, run_async, "_:up:chk", chat=GROUP, user=BOB)
    assert _alerts(query) == [str(update_flow.DENIED_TEXT)]


def test_voice_install_tap_gate_refusal_is_the_update_denial_in_the_tappers_language(
        bot, run_async):
    """The tap gate's update refusal (any card needing the update right)."""
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    query = _tap(bot, run_async, "__voice__:install", chat=GROUP, user=BOB)
    assert _alerts(query) == [RU.text(update_flow.DENIED_TEXT)]


def test_denied_tap_never_edits_the_shared_message(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(BOB, "ru")
    query = _tap(bot, run_async, "_:up:chk", chat=GROUP, user=BOB)
    assert (query.edit_message_text.await_count, query.message.edit_text.await_count) == (0, 0)


def _checking_bot(bot):
    async def _check():
        bot.updates._checked = (time.monotonic(), STATUSES["both_newer"])
        return STATUSES["both_newer"]
    bot.updates.check = _check
    spawned = []
    bot.updates._spawn = spawned.append
    return spawned


def test_checking_toast_is_the_tappers_language(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(OWNER, "ru")
    spawned = _checking_bot(bot)
    query = _tap(bot, run_async, "_:up:chk", chat=GROUP, user=OWNER)
    for coro in spawned:
        run_async(coro)
    assert all(L.is_ru(a) for a in _alerts(query) if a) and any(_alerts(query))


def test_check_result_edit_is_the_groups_language(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(OWNER, "ru")
    spawned = _checking_bot(bot)
    query = _tap(bot, run_async, "_:up:chk", chat=GROUP, user=OWNER)
    for coro in spawned:
        run_async(coro)
    assert L.fa_problems(query.message.edit_text.await_args.args[0]) == []


def _stale_go(bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(OWNER, "ru")
    started = []

    async def _start(kind, **kw):
        started.append(kind)
        return update_flow.StartResult(True, None, None)
    bot.updates.start = _start
    query = _tap(bot, run_async, "_:up:go:both", chat=GROUP, user=OWNER)
    edits = [(c.args[0] if c.args else c.kwargs.get("text"), c.kwargs.get("reply_markup"))
             for c in (*query.edit_message_text.await_args_list,
                       *query.message.edit_text.await_args_list)]
    return started, edits


def test_stale_offer_tap_starts_nothing(bot, run_async):
    started, _edits = _stale_go(bot, run_async)
    assert started == []


def test_stale_offer_tap_edits_the_shared_message_in_the_chats_language(bot, run_async):
    _started, edits = _stale_go(bot, run_async)
    assert edits and [L.fa_problems(t) for t, _kb in edits] == [[] for _ in edits]


def test_stale_offer_edit_keeps_its_check_callback_data(bot, run_async):
    _started, edits = _stale_go(bot, run_async)
    assert [_callback_data(kb) for _t, kb in edits if kb] == [["_:up:chk"]]


def test_cancel_edit_is_the_chats_language(bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(OWNER, "fa")
    query = _tap(bot, run_async, "_:up:x", chat=GROUP, user=OWNER)
    edits = [c.args[0] if c.args else c.kwargs.get("text")
             for c in (*query.edit_message_text.await_args_list,
                       *query.message.edit_text.await_args_list)]
    assert edits and all(L.ru_problems(e) == [] for e in edits)


# ---- the post-restart line (deliver_update_marker) --------------------------------

def _deliver(bot, run_async, monkeypatch, *, chat, n_back, n_missing=0, running="1.0.2"):
    sessions = []
    for i in range(n_back + n_missing):
        name = f"claude-r{i}"
        s = bot.registry.get_or_create(name)
        s.label = f"r{i}"
        s.scope_chat_id, s.scope_kind = chat, ("dm" if chat > 0 else "group")
        s.status = Status.IDLE if i < n_back else Status.UNKNOWN
        sessions.append({"name": name, "label": f"r{i}"})
    marker = {"from": "1.0.1", "to": "1.0.2", "chat_id": chat, "user_id": OWNER,
              "scheduled_at": time.time(), "sessions": sessions}
    monkeypatch.setattr(self_update, "MARKER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(self_update, "read_and_clear_marker", lambda: dict(marker))
    monkeypatch.setattr(self_update, "running_version", lambda: running)
    tg = bot._app.bot
    tg.send_message.reset_mock()
    run_async(update_flow.deliver_update_marker(bot, bot.registry))
    return [(c.args[0] if c.args else c.kwargs.get("chat_id"),
             c.args[1] if len(c.args) > 1 else c.kwargs.get("text"))
            for c in tg.send_message.await_args_list]


def test_marker_english_is_unchanged(bot, run_async, monkeypatch):
    sent = _deliver(bot, run_async, monkeypatch, chat=DM, n_back=2)
    assert sent == [(DM, "✅ aipager updated 1.0.1 → 1.0.2, 2 sessions re-adopted")]


def test_marker_english_singular_and_not_back(bot, run_async, monkeypatch):
    sent = _deliver(bot, run_async, monkeypatch, chat=DM, n_back=1, n_missing=1)
    assert sent == [(DM, "✅ aipager updated 1.0.1 → 1.0.2, 1 session re-adopted\n"
                         "⚠️ Not back: r1")]


def test_marker_line_is_in_the_marker_chats_language_not_the_starters_dm(
        bot, run_async, monkeypatch):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(OWNER, "ru")
    sent = _deliver(bot, run_async, monkeypatch, chat=GROUP, n_back=3)
    assert [(c, L.fa_problems(t)) for c, t in sent] == [(GROUP, [])]


def test_marker_in_persian_counts_with_persian_digits(bot, run_async, monkeypatch):
    preferences.set_ui_language(DM, "fa")
    (_chat, text), = _deliver(bot, run_async, monkeypatch, chat=DM, n_back=3)
    assert L.fa_digits(3) in text


def test_marker_in_persian_keeps_versions_latin(bot, run_async, monkeypatch):
    preferences.set_ui_language(DM, "fa")
    (_chat, text), = _deliver(bot, run_async, monkeypatch, chat=DM, n_back=3)
    assert ("1.0.1" in L.bare(text), "1.0.2" in L.bare(text)) == (True, True)


def test_marker_in_persian_keeps_session_names_unmarked_inside(bot, run_async, monkeypatch):
    preferences.set_ui_language(DM, "fa")
    (_chat, text), = _deliver(bot, run_async, monkeypatch, chat=DM, n_back=0, n_missing=1)
    assert "r0" in text


def test_marker_russian_re_adopted_plural_forms(bot, run_async, monkeypatch):
    preferences.set_ui_language(DM, "ru")

    def render(n):
        bot.registry._sessions.clear()
        (_chat, text), = _deliver(bot, run_async, monkeypatch, chat=DM, n_back=n)
        return text
    assert L.ru_plural_problems(render) == []


def test_marker_wrong_version_in_persian(bot, run_async, monkeypatch):
    preferences.set_ui_language(DM, "fa")
    (_chat, text), = _deliver(bot, run_async, monkeypatch, chat=DM, n_back=1, running="1.0.1")
    assert (L.fa_problems(text), "1.0.2" in L.bare(text)) == ([], True)


def test_marker_wrong_version_in_russian(bot, run_async, monkeypatch):
    preferences.set_ui_language(DM, "ru")
    (_chat, text), = _deliver(bot, run_async, monkeypatch, chat=DM, n_back=1, running="1.0.1")
    assert L.ru_problems(text) == []
