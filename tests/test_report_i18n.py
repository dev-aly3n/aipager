"""Multilingual phase 3b: problem reports in Persian and Russian.

The bot keeps keyed copies of the CLI's wording (``aipager report`` stays
English and its table stays i18n-free); the card is measured in its own
language; pop-ups after a tap are the tapper's."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aipager import i18n, preferences
from aipager.bot import report_drafts, report_flow, report_offer
from aipager.cli import report as cli_report
from aipager.i18n import Plural
from aipager.report import schema, send, wording

OWNER = 222333444        # tests/conftest.py pins config.CHAT_ID to this
GROUP = -1001
RLM = "\u200f"


def _cyrillic(text):
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def _persian(text):
    return any("؀" <= ch <= "ۿ" for ch in text)


def test_bot_lines_equal_the_cli_table():
    """Every keyed outcome line, the heading and the explanation read in
    English exactly as ``aipager report`` prints them."""
    en = i18n.get("en")
    for outcome in send.OUTCOMES:
        msg = report_flow.OUTCOME_MSGS[outcome]
        if isinstance(msg, Plural):
            keyed = en.n(msg, send.SENDS_PER_DAY, kind="text")
        elif msg.key == "rep.outcome.too_old":
            keyed = en.text(msg, command="`aipager update`")
        elif msg.key == "rep.outcome.sent":
            keyed = en.text(msg, reference="ap1-x")
        else:
            keyed = en.text(msg)
        assert keyed == wording.OUTCOME_LINES[outcome].format(reference="ap1-x"), outcome
        assert report_flow.outcome_line(outcome, "ap1-x", tr=en) == keyed
    assert report_flow._visible_heading(en) == "🐞 " + wording.HEADING
    assert en.text(report_flow.EXPLANATION) == "\n".join(wording.EXPLANATION)
    assert en.n(report_flow._NOTE_PROMPT, schema.NOTE_MAX, kind="text") == report_flow.NOTE_PROMPT
    assert en.n(report_flow._NOTE_CUT, schema.NOTE_MAX, kind="text") == report_flow.NOTE_CUT


def test_the_status_reserve_is_unchanged_in_english():
    assert report_flow._status_reserve(i18n.get("en")) == report_flow._STATUS_RESERVE
    assert report_flow._status_reserve() == report_flow._STATUS_RESERVE
    assert report_flow._status_reserve(i18n.get("ru")) > report_flow._STATUS_RESERVE


def test_wording_imports_no_i18n():
    """``aipager.report.wording`` is the CLI's table: plain literals, no
    i18n (the privacy sweep and the CLI's ``.format`` both rely on it)."""
    tree = ast.parse(Path(wording.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    assert not any("i18n" in n for n in names), names


def test_the_cli_prints_the_english_table():
    assert cli_report.INTRO is wording.INTRO
    assert cli_report.OUTCOME_LINES is wording.OUTCOME_LINES
    assert wording.OUTCOME_LINES["sent"].startswith("Sent. Reference {reference} ")


def _preview_at_english_limit() -> bytes:
    """The longest preview that still fits inline in English."""
    en = i18n.get("en")
    size = 100
    while report_flow._fits_inline(b"a" * (size + 1), en):
        size += 1
    return b"a" * size


def test_a_long_russian_card_goes_to_a_document(mk_bot, run_async, monkeypatch):
    """Measured in the card's language (ruling 7): a report that just fits
    an English card goes out as report.json for a Russian owner."""
    preview = _preview_at_english_limit()
    assert report_flow._fits_inline(preview, i18n.get("en"))
    assert not report_flow._fits_inline(preview, i18n.get("ru"))
    preferences.set_ui_language(OWNER, "ru")
    bot = mk_bot()
    posted = []

    async def _built(bot, trigger, errors=None):
        return {"v": 1}, preview

    async def _post(bot, kept):
        posted.append(kept.mode)
        return True
    monkeypatch.setattr(report_flow, "build_checked", _built)
    monkeypatch.setattr(report_flow, "_post_card", _post)
    assert run_async(report_flow.open_preview(bot, trigger="manual")) \
        is report_flow.OpenResult.OPENED
    preferences.set_ui_language(OWNER, "")
    run_async(report_flow.open_preview(bot, trigger="manual"))
    assert posted == ["document", "inline"]


def test_the_card_in_persian_and_russian():
    kept = report_flow.KeptReport(report={"v": 1}, preview=b'{"v": 1}', chat_id=OWNER)
    fa = i18n.get("fa")
    text = report_flow.card_text(kept, report_flow.shown(fa, report_flow.NOTE_CUT), tr=fa)
    assert text.startswith(RLM + "🐞 <b>")
    assert "۵۰۰" in text and "500" not in text
    assert '<pre>{"v": 1}</pre>' in text            # the report itself stays English
    ru = i18n.get("ru")
    text = report_flow._capture_text(ru)
    assert "не больше 500 символов" in text
    labels = [b.text for row in report_flow.preview_keyboard(ru).inline_keyboard for b in row]
    assert labels == ["📤 Отправить", "✏️ Добавить заметку", "✖️ Отмена"]


def test_report_button_markup_language():
    ru = i18n.get("ru")
    kb = report_flow.report_button_markup(tr=ru)
    assert kb.inline_keyboard[0][0].text == "🐞 Сообщить о проблеме"
    kb = report_flow.report_button_markup(report_flow.REPORT_THIS_BUTTON)
    assert kb.inline_keyboard[0][0].text == "🐞 Report this"
    assert kb.inline_keyboard[0][0].callback_data == "_:rp:open"


def test_the_offer_notice_in_russian_and_persian():
    entries = [{"trigger": "crash", "count": 3}, {"trigger": "crash"}]
    ru = report_offer.notice_text(entries, i18n.get("ru"))
    assert ru.startswith("aipager неожиданно остановился 3 раза. И ещё 1 ошибка. ")
    fa = report_offer.notice_text([{"trigger": "error", "type": "builtins.KeyError",
                                    "frames": [{"file": "aipager/bot/notify.py"}]}],
                                  i18n.get("fa"))
    assert _persian(fa) and "KeyError" in fa and "ارسال پیام‌ها به Telegram" in fa
    en = report_offer.notice_text(entries)
    assert en.startswith("aipager stopped unexpectedly 3 times. Also 1 other error. A report")


def _internal(file):
    return [{"trigger": "error", "type": "builtins.KeyError", "count": 1,
             "frames": [{"file": file}]}]


def test_a_keyed_area_reads_as_part_of_the_persian_sentence():
    """A translated area is not isolated like a name and carries no
    line-start mark mid-sentence; an unknown file's stem is still
    isolated."""
    fa = i18n.get("fa")
    head = report_offer.notice_text(_internal("aipager/bot/notify.py"), fa).split(". ")[0]
    inside = head[head.index("(") + 1:head.index(")")]
    assert inside.endswith(" در ارسال پیام‌ها به Telegram")
    assert "\u200f" not in inside
    stem = report_offer.notice_text(_internal("aipager/bot/zzz_mod.py"), fa)
    assert "\u2068zzz_mod\u2069" in stem
    # English and Russian bytes do not depend on it.
    assert report_offer.notice_text(_internal("aipager/bot/notify.py")).startswith(
        "aipager hit an internal error once (KeyError in message delivery).")
    assert report_offer.notice_text(_internal("aipager/bot/notify.py"), i18n.get("ru")).startswith(
        "aipager 1 раз столкнулся с внутренней ошибкой (KeyError, область: отправка сообщений в Telegram).")


def test_russian_one_time_offers_show_the_number():
    """Decision 14: a Russian one-time head says "1 раз", not "один раз"."""
    ru = i18n.get("ru")
    assert report_offer.notice_text([{"trigger": "hook_cap"}], ru).startswith(
        "Хук aipager 1 раз упёрся в лимит памяти. ")
    assert report_offer.notice_text([{"trigger": "crash"}], ru).startswith(
        "aipager неожиданно остановился 1 раз. ")


def test_areas_in_the_viewers_language():
    report = {"errors": [{"frames": [{"file": "aipager/dtach/notify_hook.py"}]},
                         {"frames": [{"file": "aipager/bot/zzz.py"}]}, "x"]}
    assert report_drafts.areas(report) == ["the hook", "zzz", "aipager"]
    assert report_drafts.areas(report, tr=i18n.get("ru")) == ["hook", "zzz", "aipager"]


def _tap(user, chat, data):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock(message_id=42, chat_id=chat)
    query.message.chat = MagicMock(id=chat)
    query.from_user = MagicMock(id=user, language_code=None)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = query.message.chat
    return update, query


def test_report_toasts_use_the_tappers_language(mk_bot, run_async, monkeypatch):
    """The owner taps Report a problem in a Persian group while their own
    chat is Russian: the pop-up is Russian."""
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(OWNER, "ru")
    bot = mk_bot()

    async def _open(bot, *, trigger, errors=None):
        return report_flow.OpenResult.OPENED
    monkeypatch.setattr(report_flow, "open_preview", _open)
    update, query = _tap(OWNER, GROUP, "_:rp:open")
    run_async(report_flow.handle_callback(bot, update, query, "_", "rp:open"))
    assert query.answer.await_args.args[0] == "Предпросмотр отчёта в твоём личном чате с ботом."


def test_a_strangers_toast_is_theirs(mk_bot, run_async):
    preferences.set_ui_language(OWNER, "fa")
    preferences.set_ui_language(7, "ru")
    bot = mk_bot()
    update, query = _tap(7, OWNER, "_:rp:send")
    run_async(report_flow.handle_callback(bot, update, query, "_", "rp:send"))
    assert query.answer.await_args.args[0] == \
        "Сообщить о проблеме может только владелец этого aipager."


def test_outcome_lines_in_russian_plurals():
    ru = i18n.get("ru")
    assert report_flow.outcome_line("daily_cap", tr=ru) == (
        f"С одного компьютера можно отправить не больше {send.SENDS_PER_DAY} отчётов в день. "
        "Попробуй завтра.")
    assert report_flow.outcome_line("bogus", tr=ru) == report_flow.outcome_line(
        "offline", tr=ru)
    assert _cyrillic(report_drafts.reply_for(report_drafts.FAILED, None, tr=ru).line)
