"""Black-box, iteration 2: the turn path's end states and stored words.

- (c) A busy card that ends because the dialog was answered in the
  terminal ("Denied in the terminal" / "Stopped in the terminal") reads in
  the chat's language (review rev-iter1-003). Driven through
  ``notify(sess, "dialog_ended_in_terminal")``.
- (d) The pinned bar and /status show the stored no-details prompt summary
  ("Permission needed", "Needs input") in the chat's language
  (rev-iter1-006); a real tool summary is never translated.
- (e) Russian "still running/working" agrees with every count (the
  predicate is number-neutral, rev-iter1-001); Persian names who did it
  as the subject of the verb, never with با ("with", rev-iter1-004; the
  passive توسط became active voice in the 2026-10-11 Persian rewrite).

English is today's text in every case.
"""

from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, policy_snapshot, preferences
from aipager.bot import agents_line
from aipager.state import Status
from tests.i18n_ml2_bb import (
    CYRILLIC,
    DM_A,
    FSI,
    PDI,
    PERSIAN,
    RLM,
    add_session,
    catalog,
    make_bot,
    message,
    strip_marks,
)
from tests import terminal_end_tx
from tests.test_separate_permission_prompt import BOB as W_BOB
from tests.test_separate_permission_prompt import GROUP as W_GROUP
from tests.test_separate_permission_prompt import _World

# ---- (c) the card settles at the terminal ----------------------------------------------


def _terminal_end(mk_bot, run_async, tmp_path, code, *, question):
    bot = make_bot(mk_bot)
    preferences.set_ui_language(DM_A, code)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    bot._stop_animation = MagicMock()
    bot._start_animation = MagicMock()
    bot._drain_next_queued = AsyncMock()
    s = add_session(bot, chat=DM_A, label="dlg", status=Status.INTERACTIVE)
    s.busy_msg_id = 60
    s.busy_started_at = time.monotonic() - 9
    s.interactive_entered_at = time.monotonic() - 5
    s.pending_permission = ({"ask_question": True, "question": "Which one?"} if question
                            else {"tool_summary": "Bash: ls"})
    s.transcript_path = str(tmp_path / "t.jsonl")
    # What a terminal "No" leaves (live, roadmap 8.152).
    terminal_end_tx.terminal_no(s.transcript_path)
    policy_snapshot.mark_turn_open(s.name)
    run_async(bot.notify(s, "dialog_ended_in_terminal",
                         {"entered_at": s.interactive_entered_at}))
    texts = [c.args[1] for c in bot._edit_busy_raw.await_args_list]
    assert len(texts) == 1, texts
    return texts[0]


ENDS = [(False, "card.denied_terminal"), (True, "card.stopped_terminal")]


@pytest.mark.parametrize("question, key", ENDS, ids=["denied", "stopped"])
def test_a_russian_card_ended_in_the_terminal_reads_russian(mk_bot, run_async, tmp_path,
                                                             question, key):
    text = _terminal_end(mk_bot, run_async, tmp_path, "ru", question=question)
    assert text == catalog("ru")[key]["v"].replace("{session}", "dlg")


@pytest.mark.parametrize("question, key", ENDS, ids=["denied", "stopped"])
def test_a_persian_card_ended_in_the_terminal_reads_persian(mk_bot, run_async, tmp_path,
                                                             question, key):
    text = _terminal_end(mk_bot, run_async, tmp_path, "fa", question=question)
    assert strip_marks(text) == catalog("fa")[key]["v"].replace("{session}", "dlg")


@pytest.mark.parametrize("question", [False, True], ids=["denied", "stopped"])
def test_a_persian_terminal_end_starts_right_to_left(mk_bot, run_async, tmp_path, question):
    text = _terminal_end(mk_bot, run_async, tmp_path, "fa", question=question)
    assert text.startswith(RLM)


def test_a_persian_terminal_end_isolates_the_session_name(mk_bot, run_async, tmp_path):
    text = _terminal_end(mk_bot, run_async, tmp_path, "fa", question=False)
    assert FSI + "dlg" + PDI in text


def test_a_russian_terminal_end_has_no_marks(mk_bot, run_async, tmp_path):
    text = _terminal_end(mk_bot, run_async, tmp_path, "ru", question=True)
    assert not re.search("[\u200f\u2068\u2069]", text)


@pytest.mark.parametrize("question, want", [
    (False, "\U0001f6ab <b>dlg</b> · Denied in the terminal"),
    (True, "⚠️ <b>dlg</b> · Stopped in the terminal")],
    ids=["denied", "stopped"])
def test_an_english_terminal_end_is_todays_text(mk_bot, run_async, tmp_path, question, want):
    assert _terminal_end(mk_bot, run_async, tmp_path, "", question=question) == want


# ---- (d) the stored no-details summary on the pinned bar and /status ------------------


@pytest.fixture
def no_details(mk_bot, monkeypatch, run_async):
    """A group session waiting on a permission prompt that came with no
    tool details (through the real notify path)."""
    from aipager.bot import dashboard
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)

    def _make(code):
        world = _World(mk_bot, monkeypatch)
        preferences.set_ui_language(W_GROUP, code)
        s = world.session()
        run_async(world.bot.notify(s, "permission_prompt",
                                   {"tool_info": None, "hook_reply": None}))
        return world.bot, s
    return _make


def _pinned_first(bot, chat, s):
    return bot._render_pinned(chat, [s])[0].split("\n", 1)[0]


def _status_text(bot, run_async, chat, user):
    update = message("/status", chat=chat, user=user)
    run_async(bot._handle_status(update, MagicMock()))
    call = update.message.reply_text.await_args
    assert call is not None
    return call.args[0] if call.args else call.kwargs["text"]


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_pinned_bar_translates_permission_needed(no_details, code):
    bot, s = no_details(code)
    first = strip_marks(_pinned_first(bot, W_GROUP, s))
    assert first.endswith(catalog(code)["perm.needed"]["v"])


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_pinned_bar_has_no_english_permission_needed(no_details, code):
    bot, s = no_details(code)
    assert "Permission needed" not in _pinned_first(bot, W_GROUP, s)


def test_the_english_pinned_bar_keeps_permission_needed(no_details):
    bot, s = no_details("")
    assert _pinned_first(bot, W_GROUP, s) == "⏳ api needs you - Permission needed"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_status_translates_permission_needed(no_details, run_async, code):
    bot, _s = no_details(code)
    text = strip_marks(_status_text(bot, run_async, W_GROUP, W_BOB))
    assert text.rstrip().endswith(catalog(code)["perm.needed"]["v"])


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_status_has_no_english_permission_needed(no_details, run_async, code):
    bot, _s = no_details(code)
    assert "Permission needed" not in _status_text(bot, run_async, W_GROUP, W_BOB)


def test_english_status_keeps_permission_needed(no_details, run_async):
    bot, _s = no_details("")
    text = _status_text(bot, run_async, W_GROUP, W_BOB)
    assert text.endswith("<b>api</b> · ⏳ needs you · Permission needed")


def _stored(mk_bot, monkeypatch, code, summary):
    from aipager.bot import dashboard
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    bot = make_bot(mk_bot)
    preferences.set_ui_language(DM_A, code)
    s = add_session(bot, chat=DM_A, label="x1", status=Status.INTERACTIVE)
    s.pending_permission = {"tool_summary": summary}
    return _pinned_first(bot, DM_A, s)


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_stored_needs_input_is_translated_on_the_pinned_bar(mk_bot, monkeypatch, code):
    first = strip_marks(_stored(mk_bot, monkeypatch, code, "Needs input"))
    assert first.endswith(catalog(code)["pin.needs_input"]["v"])


def test_an_english_stored_needs_input_is_todays_text(mk_bot, monkeypatch):
    assert _stored(mk_bot, monkeypatch, "", "Needs input") == (
        "⏳ x1 needs you - Needs input")


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_tool_summary_holding_the_words_is_never_translated(mk_bot, monkeypatch, code):
    summary = "Bash: echo Permission needed"
    assert summary in strip_marks(_stored(mk_bot, monkeypatch, code, summary))


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_differently_cased_sentinel_is_shown_as_stored(mk_bot, monkeypatch, code):
    """Only aipager's exact stored words are mapped; anything else is a
    summary (shown as stored, design.md decision 8)."""
    assert "permission needed" in strip_marks(
        _stored(mk_bot, monkeypatch, code, "permission needed"))


def test_the_persian_pinned_summary_keeps_its_line_right_to_left(no_details):
    bot, s = no_details("fa")
    assert _pinned_first(bot, W_GROUP, s).startswith(RLM)


# ---- (e) Russian agreement and Persian "by" -----------------------------------------------

PLURAL_VERB = re.compile(r"\b(работают|работает)\b")


def _block(agents=0, shells=0):
    view = agents_line.BlockView(
        agents=tuple(agents_line.AgentRow(f"a{i}", False) for i in range(agents)),
        shells_running=shells)
    return agents_line.render_block(view, i18n.get("ru"))


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21, 22, 25])
def test_russian_running_agents_line_has_no_counted_verb(n):
    assert not PLURAL_VERB.search(_block(agents=n))


@pytest.mark.parametrize("n", [1, 21])
def test_russian_running_shells_line_has_no_counted_verb(n):
    assert not PLURAL_VERB.search(_block(shells=n))


@pytest.mark.parametrize("n", [1, 21])
def test_russian_running_line_for_one_agent_still_reads_russian(n):
    assert CYRILLIC.search(_block(agents=n).split("\n\n")[0])


WAITING = i18n.Msg("card.waiting.working", "\U0001f504 **{session}** · {agents} still working")


@pytest.mark.parametrize("agents", ["1 агент", "21 агент", "2 агента", "5 агентов",
                                    "1 команда"])
def test_the_russian_waiting_line_agrees_with_any_count(agents):
    out = i18n.get("ru").md(WAITING, session="x1", agents=agents)
    assert not PLURAL_VERB.search(out)


def test_the_russian_waiting_line_is_russian():
    out = i18n.get("ru").md(WAITING, session="x1", agents="1 агент")
    assert CYRILLIC.search(out.replace("агент", ""))


BY_KEYS = ["ans.stopped_by", "ans.stopped_by_dropped", "card.stopped_by", "card.denied_by"]


def _values(code, key):
    v = catalog(code)[key]["v"]
    return list(v.values()) if isinstance(v, dict) else [v]


@pytest.mark.parametrize("key", BY_KEYS)
def test_persian_by_makes_the_person_the_subject(key):
    assert all(re.search(r"\{person\} .*کرد", v) for v in _values("fa", key))


@pytest.mark.parametrize("key", BY_KEYS)
def test_persian_by_never_uses_ba(key):
    assert not any(re.search(r"(^|\s)با\s", v) for v in _values("fa", key))


def _stop_by(mk_bot, monkeypatch, run_async, code):
    world = _World(mk_bot, monkeypatch)
    preferences.set_ui_language(W_GROUP, code)
    s = world.session()
    s.status = Status.BUSY
    s.busy_msg_id = 60
    s.busy_started_at = time.monotonic() - 9
    q = world.tap(run_async, W_GROUP, W_BOB, "stop")
    card = [c.args[1] for c in world.bot._edit_busy_raw.await_args_list]
    toast = [c.args[0] for c in q.answer.await_args_list if c.args]
    assert card and toast
    return card[-1], toast[-1]


def test_a_persian_stop_tap_settles_the_card_with_the_person_as_subject(
        mk_bot, monkeypatch, run_async):
    card, _t = _stop_by(mk_bot, monkeypatch, run_async, "fa")
    assert FSI + "@bob" + PDI + " متوقف کرد" in card


def test_a_persian_stop_tap_toast_names_the_person_as_subject(mk_bot, monkeypatch, run_async):
    _c, toast = _stop_by(mk_bot, monkeypatch, run_async, "fa")
    assert re.search("@bob" + r"\W* .*متوقف کرد", toast) and PERSIAN.search(toast)


def test_a_persian_stop_card_keeps_the_mention_whole(mk_bot, monkeypatch, run_async):
    card, _t = _stop_by(mk_bot, monkeypatch, run_async, "fa")
    assert "@bob" in card and "@" + RLM not in card and "@" + FSI not in card


def test_an_english_stop_tap_card_is_todays_text(mk_bot, monkeypatch, run_async):
    card, _t = _stop_by(mk_bot, monkeypatch, run_async, "")
    assert card == "⚠️ <b>api</b> · Stopped by @bob"


def test_a_russian_stop_tap_card_reads_russian(mk_bot, monkeypatch, run_async):
    card, _t = _stop_by(mk_bot, monkeypatch, run_async, "ru")
    assert card == catalog("ru")["card.stopped_by"]["v"].format(session="api", person="@bob")
