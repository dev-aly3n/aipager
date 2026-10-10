"""Multilingual phase 3b: /update in Persian and Russian.

The check (chat and Mini App), the job's sections (in the job chat's
language, whoever tapped), the heartbeat's units, the held-answer warning
chosen from self_update's English blockers, and the post-restart line in
the language of the chat that asked for the update."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from aipager import i18n, preferences, self_update
from aipager.bot import update_flow
from aipager.i18n import plurals
from tests import i18n_surfaces
from tests.i18n_surfaces import update_status

GROUP, MEMBER, OWNER = -1001, 2, 555
RLM, FSI, PDI = "\u200f", "\u2068", "\u2069"
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def _cyrillic(text: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def _persian(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in text)


def test_the_check_in_persian():
    fa = i18n.get("fa")
    offer = update_flow.update_offer(update_status(), fa)
    assert offer.kind == "both"
    assert offer.lines[0] == f"{RLM}aipager {FSI}1.0.1{PDI} ← {FSI}1.0.2{PDI}"
    assert "→" not in "".join(offer.lines)
    assert _persian(offer.label) and _persian(offer.restart)
    text, kb = update_flow.render_check(update_status(), show_paths=True, tr=fa)
    assert text.startswith(RLM + "⬆️ <b>")
    assert all(_persian(b.text) for row in kb.inline_keyboard for b in row)
    payload = update_flow.check_payload(update_status(ap_newer=False, latest="1.0.1",
                                                      cc_newer=False, cc_latest="2.1.300"),
                                        show_paths=False, tr=fa)
    assert payload["offer"] is None and _persian(payload["summary"])
    # The install's own description is English data.
    assert payload["source"] == "pipx"


def test_the_check_in_russian():
    ru = i18n.get("ru")
    status = update_status(cc_newer=False, cc_latest="2.1.300", upgradable=False,
                           reason="editable install")
    offer = update_flow.update_offer(status, ru)
    assert offer.kind is None
    assert offer.lines[0] == "aipager 1.0.1 → 1.0.2 (отсюда не обновить: editable install)"
    assert _cyrillic(offer.summary)
    payload = update_flow.check_payload(update_status(), show_paths=True, tr=ru)
    assert payload["offer"] == {"kind": "both", "label": "Обновить оба"}
    assert payload["lines"][0] == "aipager 1.0.1 → 1.0.2"
    assert payload["restart"] == "Перезапуск: автоматически, когда не идёт ни один ход."


def test_english_without_a_tr():
    offer = update_flow.update_offer(update_status())
    assert offer.lines == ["aipager 1.0.1 → 1.0.2", "Claude Code 2.1.300 → 2.1.310"]
    assert offer.label == "Update both"
    assert offer.restart == "Restart: automatic, once no turn is running."


def test_versions_keep_latin_digits():
    """Versions and exit codes are text that holds digits: never Persian
    digits (decision 1.2)."""
    fa = i18n.get("fa")
    text, _kb = update_flow.render_check(update_status(), show_paths=True, tr=fa)
    for version in ("1.0.1", "1.0.2", "2.1.300", "2.1.310"):
        assert version in text
    assert not any(d in text for d in PERSIAN_DIGITS)


def test_elapsed_label_in_persian():
    """The heartbeat keeps its own shape, with translated units and Persian
    digits (ruling 6)."""
    fa = i18n.get("fa")
    assert update_flow._elapsed_label(150, fa) == f" ({RLM}۲ دقیقه و ۳۰ ثانیه)"
    assert update_flow._elapsed_label(60, fa) == f" ({RLM}۱ دقیقه)"
    assert update_flow._elapsed_label(30, fa) == f" ({RLM}۳۰ ثانیه)"
    assert update_flow._elapsed_label(0, fa) == ""


def test_elapsed_label_in_russian_and_english():
    assert update_flow._elapsed_label(150, i18n.get("ru")) == " (2 мин 30 с)"
    assert update_flow._elapsed_label(150) == " (2 min 30s)"
    assert update_flow._elapsed_label(60) == " (1 min)"
    assert update_flow._elapsed_label(30) == " (30s)"


def _job(chat=GROUP, user=MEMBER, kind="claude", phase="starting"):
    return update_flow._Job(id=7, kind=kind, chat_id=chat, user_id=user, origin="chat",
                            started_at=0.0, phase=phase)


def test_held_answer_warning_in_persian(mk_bot):
    """The warning reads self_update's English blockers (never their
    translation) and picks the held-answers sentence in a Persian chat."""
    preferences.set_ui_language(GROUP, "fa")
    job = _job(phase="gate_timeout")
    job.blockers = ["x1: running a turn"]
    plain = update_flow.UpdateManager._gate_live(job)
    job.blockers.append("1 held answer not yet delivered")
    held = update_flow.UpdateManager._gate_live(job)
    assert _persian(plain) and _persian(held)
    assert "نگه‌داشته" in held and "نگه‌داشته" not in plain


def test_a_persian_offer_starts_the_right_job(mk_bot, run_async):
    """The offer is matched by its kind, never by its (translated) label."""
    preferences.set_ui_language(GROUP, "fa")
    bot = mk_bot()
    mgr = bot.updates
    mgr._checked = (time.monotonic(), update_status())
    started = []

    async def _start(kind, **k):
        started.append(kind)
        return update_flow.StartResult(True, None, None)
    mgr.start = _start
    res = run_async(mgr.start_offer("both", chat_id=GROUP, user_id=MEMBER, origin="chat"))
    assert res.ok and started == ["both"]


def test_sections_follow_the_job_chat(mk_bot, run_async):
    """A job's sections are in the job chat's language, not in the language
    of the person who started it."""
    preferences.set_ui_language(GROUP, "fa")
    preferences.set_ui_language(MEMBER, "ru")
    bot = mk_bot()
    mgr = bot.updates
    mgr._report = AsyncMock()
    labels = []

    async def _beat(job, label, fn, *args):
        labels.append(str(label))
        return self_update.ClaudeUpdateResult("2.1.300", "2.1.310", 0, False, "")
    mgr._await_with_heartbeat = _beat
    job = _job()
    run_async(mgr._claude_step(job))
    assert _persian(job.sections[-1]) and not _cyrillic(job.sections[-1])
    assert _persian(labels[0])
    assert _persian(update_flow._job_text(job))


def _marker_bot(mk_bot, monkeypatch, marker, running="1.0.2"):
    bot = mk_bot()
    monkeypatch.setattr(self_update, "MARKER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(self_update, "read_and_clear_marker", lambda: dict(marker))
    monkeypatch.setattr(self_update, "running_version", lambda: running)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=6))
    return bot


def test_marker_outcome_in_the_chats_language(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(GROUP, "ru")
    preferences.set_ui_language(OWNER, "fa")
    marker = {"from": "1.0.1", "to": "1.0.2", "chat_id": GROUP, "user_id": OWNER,
              "sessions": [{"name": "claude-x9", "label": "x9"}],
              "scheduled_at": time.time()}
    bot = _marker_bot(mk_bot, monkeypatch, marker)
    run_async(update_flow.deliver_update_marker(bot, bot.registry))
    text = bot._app.bot.send_message.await_args.kwargs.get("text") or \
        bot._app.bot.send_message.await_args.args[1]
    assert text == ("✅ aipager обновлён 1.0.1 → 1.0.2, снова подхвачено 0 сессий\n"
                    "⚠️ Не вернулись: x9")


def test_marker_in_english_is_unchanged(mk_bot, run_async, monkeypatch):
    marker = {"from": "1.0.1", "to": "1.0.2", "chat_id": GROUP, "sessions": [],
              "scheduled_at": time.time(), "interrupted": "claude_updating"}
    bot = _marker_bot(mk_bot, monkeypatch, marker, running="1.0.1")
    run_async(update_flow.deliver_update_marker(bot, bot.registry))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text == ("⚠️ An update was interrupted when aipager shut down; the install may "
                    "be partial. Now running 1.0.1. If anything misbehaves, run "
                    "`claude update` again.")


def test_russian_plural_of_re_adopted_sessions():
    ru = i18n.get("ru")
    for n in (1, 2, 5, 11, 12, 21, 22, 25):
        text = ru.n(update_flow._MARKER_DONE, n, kind="text", frm="1", to="2")
        form = {"one": "подхвачена", "few": "подхвачены", "many": "подхвачено"}[
            plurals.category("ru", n)]
        assert f"снова {form} {n} " in text, (n, text)


def test_registry_surfaces_are_migrated():
    for name in ("update.prompt", "update.check", "update.job", "update.steps",
                 "update.marker", "update.taps"):
        assert name in i18n_surfaces.MIGRATED
