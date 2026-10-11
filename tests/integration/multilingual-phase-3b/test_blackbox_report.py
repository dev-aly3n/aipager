"""Black-box: the problem report's card, offer, outcome lines and Mini App
answers in English, Persian and Russian, and the CLI's ``aipager report``
staying English (phase 3b design, success criteria 6, 7, 8, 10, 11).
The network is the recorded fake of ``tests/report_ui_harness.py``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import i18n, preferences
from aipager.bot import report_drafts, report_flow, report_offer
from aipager.miniapp.server import MiniAppServer
from aipager.policy import load_policy
from aipager.report import wording
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry
from tests import i18n_bb3b as L
from tests.report_ui_harness import (
    GROUP,
    OWNER,
    FakeNet,
    FakeTg,
    drain_sends,
    key_doc,
    pin_version,
    tap,
    toasts,
)
from tests.test_miniapp_preferences_api import BOT_TOKEN, _sign

EN, FA, RU = i18n.get("en"), i18n.get("fa"), i18n.get("ru")
ADMIN = 555
OUTCOMES = sorted(wording.OUTCOME_LINES)
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    pin_version(monkeypatch)
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _entry(count=3, file="aipager/bot/notify.py"):
    return {"fingerprint": "ap1-0123456789ab", "tier": "error", "where": "daemon",
            "trigger": "exception", "logger": "aipager.bot.notify", "type": "builtins.KeyError",
            "cause_types": [], "errno": None, "tg_class": None, "event": None, "tool": None,
            "count": count, "first_day": "2026-10-01", "last_day": "2026-10-07",
            "versions_seen": ["0.7.20"], "external": [],
            "frames": [{"file": file, "line": 10, "fn": "deliver"}]}


def _data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# ---- outcome lines ------------------------------------------------------------------

@pytest.mark.parametrize("outcome", OUTCOMES)
def test_english_outcome_line_equals_the_cli_table(outcome):
    assert report_flow.outcome_line(outcome, "ap1-0123456789ab") == \
        wording.OUTCOME_LINES[outcome].format(reference="ap1-0123456789ab")


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_persian_outcome_line_follows_the_rules(outcome):
    assert L.fa_problems(report_flow.outcome_line(outcome, "ap1-0123456789ab", tr=FA)) == []


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_russian_outcome_line_follows_the_rules(outcome):
    assert L.ru_problems(report_flow.outcome_line(outcome, "ap1-0123456789ab", tr=RU)) == []


@pytest.mark.parametrize("tr", [FA, RU], ids=["fa", "ru"])
def test_the_reference_is_shown_whole(tr):
    line = report_flow.outcome_line("sent", "ap1-0123456789ab", tr=tr)
    assert "ap1-0123456789ab" in line


def test_the_daily_cap_counts_in_persian_digits():
    from aipager.report import send
    line = report_flow.outcome_line("daily_cap", tr=FA)
    assert L.fa_digits(send.SENDS_PER_DAY) in line


def test_the_too_old_line_keeps_its_command_unmarked_in_persian():
    line = report_flow.outcome_line("too_old", tr=FA)
    assert "`aipager update`" in line


def test_reply_for_keeps_outcome_and_retry_in_every_language():
    shapes = {(r.outcome, r.reference, r.retry) for r in
              (report_drafts.reply_for("rate_limited", None, tr=t) for t in (EN, FA, RU))}
    assert len(shapes) == 1


def test_reply_for_without_tr_is_english():
    assert report_drafts.reply_for("sent", "ap1-x").line == \
        wording.OUTCOME_LINES["sent"].format(reference="ap1-x")


# ---- the card and its buttons ----------------------------------------------------------

@pytest.mark.parametrize("tr", [FA, RU], ids=["fa", "ru"])
def test_preview_keyboard_callback_data_is_unchanged(tr):
    assert _data(report_flow.preview_keyboard(tr=tr)) == _data(report_flow.preview_keyboard())


@pytest.mark.parametrize("label", ["REPORT_BUTTON", "REPORT_THIS_BUTTON"])
def test_report_button_english_label_is_the_constant(label):
    kb = report_flow.report_button_markup(getattr(report_flow, label))
    assert kb.inline_keyboard[0][0].text == getattr(report_flow, label)


@pytest.mark.parametrize("label", ["REPORT_BUTTON", "REPORT_THIS_BUTTON"])
@pytest.mark.parametrize("tr,check", [(FA, L.fa_problems), (RU, L.ru_problems)],
                         ids=["fa", "ru"])
def test_report_button_label_is_translated(label, tr, check):
    kb = report_flow.report_button_markup(getattr(report_flow, label), tr=tr)
    assert check(kb.inline_keyboard[0][0].text) == []


@pytest.mark.parametrize("tr", [FA, RU], ids=["fa", "ru"])
def test_report_button_callback_data_is_unchanged(tr):
    assert _data(report_flow.report_button_markup(tr=tr)) == \
        _data(report_flow.report_button_markup())


# ---- the automatic offer ------------------------------------------------------------------

def test_area_of_a_known_file_is_translated():
    assert (L.is_fa(report_offer.area(_entry(), tr=FA)),
            L.is_ru(report_offer.area(_entry(), tr=RU))) == (True, True)


def test_area_of_an_unknown_file_is_the_same_name_in_every_language():
    entry = _entry(file="aipager/zz_not_a_module.py")
    shown = {L.bare(report_offer.area(entry, tr=t)) for t in (EN, FA, RU)}
    assert len(shown) == 1


def test_area_without_tr_is_english():
    assert report_offer.area(_entry()) == "message delivery"


def test_notice_without_tr_is_english():
    assert report_offer.notice_text([_entry(3)]).startswith(
        "aipager hit an internal error 3 times (KeyError in message delivery).")


@pytest.mark.parametrize("n", [1, 2, 3])
def test_persian_notice_follows_the_rules(n):
    assert L.fa_problems(report_offer.notice_text([_entry(n)], tr=FA)) == []


def test_persian_notice_counts_in_persian_digits():
    assert L.fa_digits(3) in report_offer.notice_text([_entry(3)], tr=FA)


def test_persian_notice_keeps_the_exception_type_latin():
    assert "KeyError" in report_offer.notice_text([_entry(3)], tr=FA)


def test_russian_notice_plural_forms():
    def render(n):
        return report_offer.notice_text([_entry(n)], tr=RU).split(".")[0]
    # 1 has its own exact form (English "once"); see the next test.
    assert L.ru_plural_problems(render, counts=L.RU_COUNTS[1:]) == []


def test_russian_notice_shows_the_number_for_one():
    """Operator decision 14: Russian counted forms always show the number
    ("1 раз", not "один раз")."""
    first = report_offer.notice_text([_entry(1)], tr=RU).split(".")[0]
    assert " 1 " in first


def test_russian_notice_other_errors_plural_forms():
    def render(n):
        entries = [_entry(2)] + [dict(_entry(1), fingerprint=f"ap1-{i:012x}",
                                      type=f"builtins.E{i}")
                                 for i in range(n)]
        text = report_offer.notice_text(entries, tr=RU)
        return [s for s in text.split(". ") if str(n) in s][-1]
    assert L.ru_plural_problems(render) == []


@pytest.mark.parametrize("tr", [FA, RU], ids=["fa", "ru"])
def test_notice_keyboard_callback_data_is_unchanged(tr):
    assert _data(report_offer.notice_keyboard(1234, tr=tr)) == \
        _data(report_offer.notice_keyboard(1234))


@pytest.mark.parametrize("tr,check", [(FA, L.fa_problems), (RU, L.ru_problems)],
                         ids=["fa", "ru"])
def test_notice_keyboard_labels_are_translated(tr, check):
    labels = [b.text for row in report_offer.notice_keyboard(1234, tr=tr).inline_keyboard
              for b in row]
    assert [lb for lb in labels if check(lb)] == []


# ---- the bot: card in the owner's DM language, pop-ups in the tapper's ------------------

def _scopes():
    group = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="aly", role="owner"),
        Member(id=ADMIN, label="ada", role="admin")))
    dm = Scope(chat_id=OWNER, kind="dm", label="aly DM",
               members=(Member(id=OWNER, label="aly", role="owner"),))
    return [dm, group]


def _bot(mk_bot):
    registry = SessionRegistry()
    bot = mk_bot(registry, scopes=_scopes())
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    tg = FakeTg()
    tg.username = "aipager_test_bot"
    bot._app.bot = tg
    return bot, tg, registry


def _press(bot, run_async, data, **kw):
    update, query = tap(data, **kw)

    async def _go():
        await bot._handle_callback(update, MagicMock())
        await drain_sends(bot)
    run_async(_go())
    return query


def test_persian_owner_card_follows_the_rules(mk_bot, run_async):
    preferences.set_ui_language(OWNER, "fa")
    bot, tg, _r = _bot(mk_bot)
    _press(bot, run_async, "_:rp:open")
    assert L.fa_problems(tg.sent[0].text) == []


def test_persian_owner_card_buttons_keep_their_callback_data(mk_bot, run_async):
    preferences.set_ui_language(OWNER, "fa")
    bot, tg, _r = _bot(mk_bot)
    _press(bot, run_async, "_:rp:open")
    assert _data(tg.sent[0].kw["reply_markup"]) == _data(report_flow.preview_keyboard())


def test_the_report_inside_a_persian_card_has_no_invisible_marks(mk_bot, run_async):
    """The report itself is the fixed English JSON, shown in <pre>."""
    preferences.set_ui_language(OWNER, "fa")
    bot, tg, _r = _bot(mk_bot)
    _press(bot, run_async, "_:rp:open")
    card = tg.sent[0].text if tg.sent else ""
    blocks = L.code_spans(card)
    assert [b for b in blocks if any(m in b for m in L.MARKS)] == []


def test_a_report_tap_in_a_russian_group_pops_up_in_the_persian_owners_language(
        mk_bot, run_async):
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(OWNER, "fa")
    bot, _tg, _r = _bot(mk_bot)
    query = _press(bot, run_async, "_:rp:open", user_id=OWNER, chat_id=GROUP)
    shown = toasts(query)
    assert shown and [t for t in shown if not L.is_fa(t)] == []


def test_a_non_owners_report_tap_pops_up_in_their_own_language(mk_bot, run_async):
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(ADMIN, "ru")
    bot, _tg, _r = _bot(mk_bot)
    query = _press(bot, run_async, "_:rp:open", user_id=ADMIN, chat_id=GROUP)
    shown = toasts(query)
    assert shown and [t for t in shown if not L.is_ru(t)] == []


@pytest.mark.parametrize("data", ["_:rp:send", "_:rp:note", "_:rp:back", "_:rp:cancel"])
def test_a_tap_on_a_lost_card_edits_it_in_the_owners_language(mk_bot, run_async, data):
    preferences.set_ui_language(OWNER, "ru")
    bot, _tg, _r = _bot(mk_bot)
    query = _press(bot, run_async, data, message_id=999)
    edits = [c.args[0] if c.args else c.kwargs.get("text")
             for c in query.edit_message_text.await_args_list]
    assert edits and [L.ru_problems(e) for e in edits] == [[] for _ in edits]


@pytest.mark.parametrize("data", ["_:rp:op:123", "_:rp:on:123", "_:rp:od:123"])
def test_a_stale_offer_tap_pops_up_in_the_tappers_language(mk_bot, run_async, data):
    preferences.set_ui_language(OWNER, "fa")
    bot, _tg, _r = _bot(mk_bot)
    shown = toasts(_press(bot, run_async, data))
    assert shown and [L.fa_problems(t) for t in shown] == [[] for _ in shown]


def test_an_english_owners_card_is_unchanged(mk_bot, run_async):
    bot, tg, _r = _bot(mk_bot)
    _press(bot, run_async, "_:rp:open")
    assert tg.sent[0].text.startswith(EN.html(report_flow.HEADING_HTML))


# ---- the Mini App: areas and line in the viewer's language --------------------------------

def _init(user_id, language_code=None):
    import time
    from urllib.parse import urlencode
    user = {"id": user_id, "first_name": "Test"}
    if language_code is not None:
        user["language_code"] = language_code
    fields = {"auth_date": str(int(time.time())), "user": json.dumps(user)}
    fields["hash"] = _sign(fields, BOT_TOKEN)
    return {"X-Telegram-Init-Data": urlencode(fields)}


def _server(mk_bot):
    bot, tg, registry = _bot(mk_bot)
    return MiniAppServer(bot, registry, port=8765)


def _posts(run_async, srv, calls):
    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            out = []
            for path, headers, body in calls:
                resp = await client.post(path, headers=headers, json=body)
                out.append((resp.status, await resp.json()))
            return out
        finally:
            await client.close()
    return run_async(_run())


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    return fake


@pytest.fixture
def with_an_error(monkeypatch):
    real_build = report_flow._build
    monkeypatch.setattr(report_flow, "_build",
                        lambda trigger, errors, ctx: real_build(trigger, [_entry()], ctx))


def _draft(run_async, srv, headers):
    ((status, body),) = _posts(run_async, srv, [("/api/report/draft", headers, {})])
    assert status == 200
    return body


def test_draft_areas_follow_the_viewers_stored_persian(mk_bot, run_async, with_an_error):
    preferences.set_ui_language(OWNER, "fa")
    body = _draft(run_async, _server(mk_bot), _init(OWNER, "en"))
    assert body["areas"] and [a for a in body["areas"] if not L.is_fa(a)] == []


def test_draft_areas_with_nothing_stored_and_a_russian_app_are_english(
        mk_bot, run_async, with_an_error):
    body = _draft(run_async, _server(mk_bot), _init(OWNER, "ru"))
    assert body["areas"] == ["message delivery"]


def test_draft_preview_is_the_same_english_json_for_a_persian_viewer(
        mk_bot, run_async, with_an_error):
    preferences.set_ui_language(OWNER, "fa")
    body = _draft(run_async, _server(mk_bot), _init(OWNER))
    assert json.loads(body["preview"]) == body["report"]


def test_send_line_follows_the_viewers_stored_russian(mk_bot, run_async, net):
    preferences.set_ui_language(OWNER, "ru")
    srv = _server(mk_bot)
    d = _draft(run_async, srv, _init(OWNER))
    ((status, body),) = _posts(run_async, srv, [
        ("/api/report/send", _init(OWNER), {"draft": d["draft"]})])
    assert (status, body["outcome"], L.ru_problems(body["line"])) == (200, "sent", [])


def test_a_repeated_send_line_is_in_the_viewers_language_too(mk_bot, run_async, net):
    preferences.set_ui_language(OWNER, "fa")
    srv = _server(mk_bot)
    d = _draft(run_async, srv, _init(OWNER))
    first, second = _posts(run_async, srv, [
        ("/api/report/send", _init(OWNER), {"draft": d["draft"]}),
        ("/api/report/send", _init(OWNER), {"draft": d["draft"]})])
    assert (second[1]["repeat"], L.fa_problems(second[1]["line"]), len(net.posts)) == \
        (True, [], 1)


def test_the_sent_reference_is_whole_in_the_persian_line(mk_bot, run_async, net):
    preferences.set_ui_language(OWNER, "fa")
    srv = _server(mk_bot)
    d = _draft(run_async, srv, _init(OWNER))
    ((_s, body),) = _posts(run_async, srv, [
        ("/api/report/send", _init(OWNER), {"draft": d["draft"]})])
    assert body["reference"] in body["line"]


def test_a_try_later_answer_keeps_retry_for_a_persian_viewer(mk_bot, run_async, monkeypatch):
    fake = FakeNet(sentry_status=429)
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    preferences.set_ui_language(OWNER, "fa")
    srv = _server(mk_bot)
    d = _draft(run_async, srv, _init(OWNER))
    ((status, body),) = _posts(run_async, srv, [
        ("/api/report/send", _init(OWNER), {"draft": d["draft"]})])
    assert (status, body["outcome"], body["retry"], L.is_fa(body["line"])) == \
        (200, "rate_limited", True, True)


def test_a_disabled_answer_in_russian(mk_bot, run_async, monkeypatch):
    fake = FakeNet(doc=key_doc(enabled=False))
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    preferences.set_ui_language(OWNER, "ru")
    srv = _server(mk_bot)
    d = _draft(run_async, srv, _init(OWNER))
    ((_s, body),) = _posts(run_async, srv, [
        ("/api/report/send", _init(OWNER), {"draft": d["draft"]})])
    assert (body["outcome"], body["retry"], L.ru_problems(body["line"])) == \
        ("disabled", False, [])


def test_an_english_viewers_send_line_equals_the_cli_table(mk_bot, run_async, net):
    srv = _server(mk_bot)
    d = _draft(run_async, srv, _init(OWNER))
    ((_s, body),) = _posts(run_async, srv, [
        ("/api/report/send", _init(OWNER), {"draft": d["draft"]})])
    assert body["line"] == wording.OUTCOME_LINES["sent"].format(reference=body["reference"])


# ---- the CLI and the wording table stay English -----------------------------------------

def _cli_env(tmp_path):
    home = tmp_path / "cli-home"
    (home / ".config" / "aipager").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_TG_")}
    env.update(HOME=str(home), PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1",
               XDG_CONFIG_HOME=str(home / ".config"), XDG_STATE_HOME=str(home / ".local/state"),
               XDG_DATA_HOME=str(home / ".local/share"))
    return home, env


def test_the_wording_table_loads_without_i18n(tmp_path):
    _home, env = _cli_env(tmp_path)
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, aipager.report.wording; print('aipager.i18n' in sys.modules)"],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "False"


def test_cli_report_stays_english_with_a_persian_chat_stored(tmp_path):
    """`aipager report` with no terminal prints the report and sends
    nothing; a Persian choice stored for the operator's chat changes none
    of its words."""
    preferences.set_ui_language(OWNER, "fa")
    stored = preferences._PREFERENCES_PATH.read_bytes()
    home, env = _cli_env(tmp_path)
    (home / ".config" / "aipager" / "preferences.json").write_bytes(stored)
    out = subprocess.run([sys.executable, "-m", "aipager", "report"], env=env,
                         cwd=str(tmp_path), stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=120)
    assert (out.stdout.startswith(wording.INTRO), L.PERSIAN.search(out.stdout),
            L.CYRILLIC.search(out.stdout)) == (True, None, None)


# ---- iteration 2: the reworded offer notice and its boundaries --------------------------

KNOWN_FILES = ["aipager/bot/notify.py", "aipager/bot/handlers.py", "aipager/dtach/inject.py",
               "aipager/state.py", "aipager/miniapp/server.py"]
AREA_FILES = KNOWN_FILES + ["aipager/zz_not_a_module.py"]

#: Russian prepositions that put the noun after them in a case other than the
#: nominative; the area name is a nominative phrase, so none may precede it.
RU_CASE_PREPOSITIONS = {"в", "во", "на", "о", "об", "обо", "при", "по", "из", "от", "для",
                        "с", "со", "к", "ко", "у", "без", "до", "про", "над", "под", "за",
                        "перед", "через", "из-за", "среди"}


def _ru_word_before_area(file):
    entry = _entry(2, file=file)
    shown = report_offer.area(entry, tr=RU)
    text = report_offer.notice_text([entry], tr=RU)
    head = text[:text.index(shown)]
    words = head.replace("(", " ").replace(",", " ").replace(":", " ").split()
    return words[-1].lower() if words else ""


@pytest.mark.parametrize("file", AREA_FILES)
def test_russian_notice_shows_the_area_exactly_as_named(file):
    """The area appears in its own (nominative) form, not inflected."""
    entry = _entry(2, file=file)
    assert report_offer.area(entry, tr=RU) in report_offer.notice_text([entry], tr=RU)


@pytest.mark.parametrize("file", AREA_FILES)
def test_russian_notice_puts_no_case_governing_preposition_before_the_area(file):
    assert _ru_word_before_area(file) not in RU_CASE_PREPOSITIONS


@pytest.mark.parametrize("n", L.RU_COUNTS)
def test_russian_notice_follows_the_russian_rules(n):
    assert L.ru_problems(report_offer.notice_text([_entry(n)], tr=RU)) == []


def test_russian_notice_for_one_does_not_spell_the_number():
    first = report_offer.notice_text([_entry(1)], tr=RU).split(".")[0]
    assert "один" not in first.lower()


def test_russian_notice_with_a_latin_area_follows_the_russian_rules():
    entry = _entry(2, file="aipager/miniapp/server.py")
    assert L.ru_problems(report_offer.notice_text([entry], tr=RU)) == []


@pytest.mark.parametrize("file", AREA_FILES)
def test_persian_notice_nests_no_line_start_mark_inside_an_isolate(file):
    text = report_offer.notice_text([_entry(2, file=file)], tr=FA)
    assert L.FSI + L.RLM not in text


@pytest.mark.parametrize("file", AREA_FILES)
def test_persian_notice_shows_the_area(file):
    entry = _entry(2, file=file)
    assert L.bare(report_offer.area(entry, tr=FA)) in \
        L.bare(report_offer.notice_text([entry], tr=FA))


@pytest.mark.parametrize("file", AREA_FILES)
def test_persian_notice_follows_the_rules_for_every_area(file):
    assert L.fa_problems(report_offer.notice_text([_entry(2, file=file)], tr=FA)) == []


def test_english_notice_for_one_says_once():
    assert report_offer.notice_text([_entry(1)]).startswith(
        "aipager hit an internal error once (KeyError in message delivery).")


def test_english_notice_for_two_counts():
    assert report_offer.notice_text([_entry(2)]).startswith(
        "aipager hit an internal error 2 times (KeyError in message delivery).")


@pytest.mark.parametrize("file", AREA_FILES)
def test_english_notice_names_the_english_area(file):
    entry = _entry(2, file=file)
    assert f"(KeyError in {report_offer.area(entry)})." in report_offer.notice_text([entry])


def test_draft_areas_follow_the_viewers_stored_russian(mk_bot, run_async, with_an_error):
    preferences.set_ui_language(OWNER, "ru")
    body = _draft(run_async, _server(mk_bot), _init(OWNER, "fa"))
    assert body["areas"] and [a for a in body["areas"] if not L.is_ru(a)] == []


def test_draft_areas_after_a_cleared_choice_are_english(mk_bot, run_async, with_an_error):
    preferences.set_ui_language(OWNER, "fa")
    preferences.set_ui_language(OWNER, "")
    body = _draft(run_async, _server(mk_bot), _init(OWNER, "fa"))
    assert body["areas"] == ["message delivery"]
