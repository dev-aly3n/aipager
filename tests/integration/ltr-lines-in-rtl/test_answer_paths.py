"""English chat, Persian answer: every send path that puts aipager's own
lines into a right-to-left rich message marks them, and nothing else
changes (design.md Part A success criteria, Part B "Send-path audit" and
"Added success criteria"; entrypoints.md "Side effects").

Driven only through ``TelegramBot.notify`` / ``flush_held_answers`` and
observed at the transport seams (``send_rich_message``,
``edit_message_text_rich``, the rich transport's ``_post`` and the PTB
bot's ``send_message``). ``asyncio`` and ``time`` are never patched.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, preferences
from aipager.bot import held as held_mod
from aipager.bot import held_message
from aipager.bot import notify as notify_mod
from aipager.bot.flood import MUTE
from aipager.state import Status, TrackedSession
from tests.footer_text import unfooter


def _load_kit():
    name = "ltr_lines_in_rtl_kit"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _load_kit()
CHAT = 555
TRIGGER = 7
CARD = 42
LRM, FSI, PDI, RLM = kit.LRM, kit.FSI, kit.PDI, kit.RLM
NEW_MARKS = re.compile("[\u200e\u2068\u2069]")
PARAS = pytest.mark.parametrize("para", [1, None], ids=["desktop-web", "ios-android"])
RESULT_LINE = "\u200e\U0001f4ac **\u2068x1\u2069**\u200e"


# ---- harness: the finish path through send_rich_message --------------------------------

def _answer(mk_bot, run_async, monkeypatch, *, label="x1", summary=kit.FA_ANSWER,
            elapsed=250, kept=False, layout=None, agents=True, code=""):
    """One Stop: the answer goes out. Returns what reached the seams."""
    preferences.set_ui_language(CHAT, code)
    if layout:
        preferences.set_preference(CHAT, "layout", layout)
    rich = AsyncMock(return_value={"message_id": 77})
    edit = AsyncMock(return_value={"message_id": CARD})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", rich)
    monkeypatch.setattr("aipager.bot.notify.edit_message_text_rich", edit)
    bot = mk_bot()
    sess = TrackedSession(name="claude-x1", label=label, status=Status.IDLE)
    sess.scope_chat_id, sess.scope_kind = CHAT, "dm"
    bot.registry._sessions[sess.name] = sess
    sess.busy_started_at = time.monotonic() - elapsed if elapsed else 0.0
    sess.busy_started_wall = 0.0
    sess.last_lines_added, sess.last_lines_removed = 10, 5
    if agents:
        sess.bg_agent_started("a1", "general-purpose", time.monotonic())
    if kept or layout == "merged":
        sess.busy_msg_id = CARD
        sess.tool_history = [("Read: /a", True), ("SendMessage", True)]
    if kept:
        bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock(return_value=MagicMock(message_id=98))
    bot._maybe_update_bot_name = AsyncMock()
    run_async(bot.notify(sess, "idle_prompt", {"summary": summary}))
    sends = [(c.args[1], c.kwargs.get("is_rtl")) for c in rich.await_args_list]
    edits = [(c.args[2], c.kwargs.get("is_rtl")) for c in edit.await_args_list]
    plain = [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    plain += [c.kwargs.get("caption") or "" for c in bot._app.bot.send_document.await_args_list]
    return SimpleNamespace(sends=sends, edits=edits, plain=plain, sess=sess)


def _the_answer(out, needle=None):
    """The one rich message (send or merged edit) carrying the answer."""
    needles = (needle,) if needle else (kit.FA_ANSWER[:6], kit.EN_ANSWER[:6])
    found = [(t, r) for t, r in out.sends + out.edits if any(n in t for n in needles)]
    assert len(found) == 1, (out.sends, out.edits)
    return found[0]


def _block(text: str) -> list[str]:
    """The agents block's lines under the answer, as Markdown."""
    tail = unfooter(text).rsplit("\n\n---\n\n", 1)[1]
    return [ln for ln in tail.split("\n") if ln.strip()]


@pytest.fixture
def finish(mk_bot, run_async, monkeypatch):
    def _go(**kw):
        return _answer(mk_bot, run_async, monkeypatch, **kw)
    return _go


# ---- finish path ----------------------------------------------------------------------------

def test_a_persian_answer_in_an_english_chat_goes_out_right_to_left(finish):
    _text, is_rtl = _the_answer(finish())
    assert is_rtl is True


def test_the_finished_line_starts_with_an_lrm(finish):
    text, _ = _the_answer(finish())
    assert text.split("\n", 1)[0].startswith(LRM)


def test_the_finished_line_ends_with_an_lrm(finish):
    text, _ = _the_answer(finish())
    assert text.split("\n", 1)[0].endswith(LRM)


@PARAS
def test_the_finished_line_reads_in_order(finish, para):
    text, _ = _the_answer(finish())
    line = kit.lines(text.split("\n", 1)[0])[0]
    assert kit.order_problems(line, para) == [], kit.visual_text(line, para)


def test_the_finished_line_shows_the_glyph_then_name_then_finished(finish):
    text, _ = _the_answer(finish())
    line = kit.lines(text.split("\n", 1)[0])[0]
    assert kit.visual_text(line, 1) == "\U0001f4acx1·Finished(4:10,+10-5)"


def test_the_finished_line_with_a_persian_name_keeps_the_glyph_first(finish):
    text, _ = _the_answer(finish(label=kit.FA_NAME))
    line = kit.lines(text.split("\n", 1)[0])[0]
    assert kit.visual_text(line, 1).startswith("\U0001f4ac" + kit.FA_NAME[::-1] + "·Finished")


@PARAS
def test_the_finished_line_with_a_persian_name_reads_in_order(finish, para):
    text, _ = _the_answer(finish(label=kit.FA_NAME))
    line = kit.lines(text.split("\n", 1)[0])[0]
    assert kit.order_problems(line, para) == [], kit.visual_text(line, para)


def test_claudes_answer_is_byte_identical(finish):
    text, _ = _the_answer(finish())
    assert text.split("\n\n")[1] == kit.FA_ANSWER


def test_the_divider_carries_no_mark(finish):
    text, _ = _the_answer(finish())
    assert "\n\n---\n\n" in text and kit.marked_divider_or_blank(text) == []


def test_every_agents_block_line_starts_with_an_lrm(finish):
    text, _ = _the_answer(finish())
    block = _block(text)
    assert block and all(ln.removeprefix("- ").startswith(LRM) for ln in block), block


def test_every_agents_block_line_ends_with_an_lrm(finish):
    text, _ = _the_answer(finish())
    block = _block(text)
    assert block and all(ln.endswith(LRM) for ln in block), block


@PARAS
def test_every_agents_block_line_reads_in_order(finish, para):
    text, _ = _the_answer(finish())
    tail = text.rsplit("\n\n---\n\n", 1)[1]
    bad = [(ln, kit.order_problems(ln, para)) for ln in kit.lines(tail)
           if kit.order_problems(ln, para)]
    assert bad == []


def test_no_two_marks_touch_outside_code(finish):
    text, _ = _the_answer(finish(label=kit.FA_NAME))
    assert not kit.PAIR.search(kit.outside_code(text))


def test_no_rlm_in_the_message(finish):
    text, _ = _the_answer(finish())
    assert RLM not in text


def test_nothing_invisible_inside_code_or_tags(finish):
    text, _ = _the_answer(finish(kept=True))
    assert kit.invisible_in_code_or_tags(text) == []


# ---- the answer under a kept card ----------------------------------------------------------

def test_the_result_line_under_a_kept_card_is_marked_and_isolated(finish):
    text, _ = _the_answer(finish(kept=True, elapsed=9))
    assert text.split("\n", 1)[0] == RESULT_LINE


@PARAS
def test_the_result_line_under_a_kept_card_with_a_persian_name_reads_in_order(finish, para):
    text, _ = _the_answer(finish(kept=True, elapsed=9, label=kit.FA_NAME))
    line = kit.lines(text.split("\n", 1)[0])[0]
    assert kit.order_problems(line, para) == [], kit.visual_text(line, para)


def test_the_result_line_under_a_kept_card_shows_the_glyph_first(finish):
    text, _ = _the_answer(finish(kept=True, elapsed=9, label=kit.FA_NAME))
    line = kit.lines(text.split("\n", 1)[0])[0]
    assert kit.visual_text(line, 1) == "\U0001f4ac" + kit.FA_NAME[::-1]


# ---- the merged layout ---------------------------------------------------------------------

def test_the_merged_answer_goes_out_right_to_left(finish):
    _text, is_rtl = _the_answer(finish(layout="merged"))
    assert is_rtl is True


def test_the_merged_result_line_is_marked_and_isolated(finish):
    text, _ = _the_answer(finish(layout="merged"))
    assert RESULT_LINE + "\n\n" + kit.FA_ANSWER in text


@PARAS
def test_every_aipager_line_of_the_merged_message_reads_in_order(finish, para):
    text, _ = _the_answer(finish(layout="merged"))
    own = [ln for ln in kit.lines(text) if kit.FA_ANSWER not in ln]
    bad = [(ln, kit.order_problems(ln, para)) for ln in own if kit.order_problems(ln, para)]
    assert own and bad == []


def test_the_merged_card_rows_keep_marks_outside_code(finish):
    text, _ = _the_answer(finish(layout="merged"))
    assert kit.invisible_in_code_or_tags(text) == []


@PARAS
def test_the_merged_result_line_with_a_persian_name_reads_in_order(finish, para):
    text, _ = _the_answer(finish(layout="merged", label=kit.FA_NAME))
    line = [ln for ln in kit.lines(text) if ln.lstrip(LRM).startswith("\U0001f4ac")][0]
    assert kit.order_problems(line, para) == [], kit.visual_text(line, para)


# ---- what must not change ------------------------------------------------------------------

def test_an_english_answer_in_an_english_chat_carries_no_mark(finish):
    text, is_rtl = _the_answer(finish(summary=kit.EN_ANSWER))
    assert is_rtl is False and not NEW_MARKS.search(text), text


def test_an_english_answer_under_a_kept_card_carries_no_mark(finish):
    text, _ = _the_answer(finish(summary=kit.EN_ANSWER, kept=True, elapsed=9))
    assert not NEW_MARKS.search(text), text


def test_a_long_persian_answers_html_header_carries_no_new_mark(finish):
    out = finish(summary=(kit.FA_ANSWER + " ") * 2500)
    headers = [t for t in out.plain if "x1" in t]
    assert headers and not any(NEW_MARKS.search(t) for t in headers), headers


def test_a_long_persian_answers_html_header_with_a_persian_name_carries_no_new_mark(finish):
    out = finish(summary=(kit.FA_ANSWER + " ") * 2500, label=kit.FA_NAME)
    headers = [t for t in out.plain if kit.FA_NAME in t]
    assert headers and not any(NEW_MARKS.search(t) for t in headers), headers


def test_a_persian_answer_in_a_persian_chat_has_no_lrm(finish):
    text, is_rtl = _the_answer(finish(code="fa"))
    assert is_rtl is True and LRM not in text


def test_an_arabic_answer_in_an_arabic_chat_has_no_lrm(finish):
    text, _ = _the_answer(finish(code="ar", summary=kit.AR_ANSWER), kit.AR_ANSWER[:6])
    assert LRM not in text


@pytest.mark.parametrize("code", ["de", "fr", "ru"])
def test_another_ltr_language_marks_its_block_too(finish, code):
    text, _ = _the_answer(finish(code=code))
    block = _block(text)
    assert block and all(ln.removeprefix("- ").startswith(LRM) and ln.endswith(LRM)
                         for ln in block), block


# ---- the background job's interim answer (operator screenshot 1) ---------------------------

class _Wire:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_rich = False


@pytest.fixture
def wire(monkeypatch):
    w = _Wire()

    async def _post(method, payload, **kw):
        w.calls.append((method, dict(payload, _kw=kw)))
        if w.fail_rich and method == "sendRichMessage":
            raise RuntimeError("bad markdown")
        return {"ok": True, "result": {"message_id": 9000 + len(w.calls)}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return w


def _wire_bot(mk_bot, w, label="x1"):
    bot = mk_bot()

    def _rec(name):
        async def _call(*args, **kwargs):
            await asyncio.sleep(0)
            w.calls.append((name, dict(kwargs, _args=args)))
            return MagicMock(message_id=8000 + len(w.calls))
        return _call

    for name in ("send_message", "delete_message", "edit_message_text", "send_document",
                 "send_chat_action"):
        setattr(bot._app.bot, name, _rec(name))
    bot._maybe_update_bot_name = AsyncMock()
    preferences.set_ui_language(CHAT, "")
    s = bot.registry.get_or_create("claude-x1")
    s.label = label
    s.status = Status.IDLE
    s.scope_chat_id, s.scope_kind = CHAT, "dm"
    s.busy_msg_id, s.busy_card_trigger, s.trigger_msg_id = CARD, TRIGGER, TRIGGER
    s.busy_started_at = time.monotonic() - 5
    s.tool_history = [("Agent: run the suite", True)]
    now = time.monotonic()
    s.add_subagent("a1", {"type": "general-purpose", "started_at": now,
                          "description": "run the suite"})
    assert s.bg_agent_started("a1", "general-purpose", now)
    return bot, s


async def _drain():
    for _ in range(5):
        pending = [t for t in getattr(notify_mod, "_BACKGROUND_TASKS", set()) if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _interim(run_async, bot, sess, text=kit.FA_ANSWER):
    async def _go():
        await bot.notify(sess, "idle_prompt", {"summary": text, "raw_md": text})
        await _drain()
    run_async(asyncio.wait_for(_go(), timeout=5))


def _interim_sends(w):
    return [p for m, p in w.calls if m == "sendRichMessage"
            and kit.FA_ANSWER[:6] in p["rich_message"]["markdown"]]


@pytest.fixture
def interim(mk_bot, run_async, wire):
    def _go(label="x1"):
        bot, sess = _wire_bot(mk_bot, wire, label)
        _interim(run_async, bot, sess)
        assert sess.job_background_open() is True
        (payload,) = _interim_sends(wire)
        return payload["rich_message"], bot, sess
    return _go


def test_the_interim_answer_goes_out_right_to_left(interim):
    rich, _bot, _sess = interim()
    assert rich["is_rtl"] is True


def test_the_interim_result_line_is_marked_and_isolated(interim):
    rich, _bot, _sess = interim()
    assert rich["markdown"].split("\n", 1)[0] == RESULT_LINE


def test_the_interim_result_line_shows_the_glyph_first_on_desktop(interim):
    rich, _bot, _sess = interim()
    line = kit.lines(rich["markdown"].split("\n", 1)[0])[0]
    assert kit.visual_text(line, 1) == "\U0001f4acx1"


@PARAS
def test_the_interim_result_line_with_a_persian_name_reads_in_order(interim, para):
    rich, _bot, _sess = interim(kit.FA_NAME)
    line = kit.lines(rich["markdown"].split("\n", 1)[0])[0]
    assert kit.order_problems(line, para) == [], kit.visual_text(line, para)


@PARAS
def test_the_interim_block_lines_read_in_order(interim, para):
    rich, _bot, _sess = interim()
    tail = rich["markdown"].rsplit("\n\n---\n\n", 1)[1]
    lines = kit.lines(tail)
    assert any("Results from the agents will follow here." in ln for ln in lines)
    assert [ln for ln in lines if kit.order_problems(ln, para)] == []


def test_the_interim_keeps_claudes_answer_byte_identical(interim):
    rich, _bot, _sess = interim()
    assert rich["markdown"].split("\n\n")[1] == kit.FA_ANSWER


def test_the_interim_plain_fallback_carries_no_new_mark(mk_bot, run_async, wire):
    wire.fail_rich = True
    bot, sess = _wire_bot(mk_bot, wire)
    _interim(run_async, bot, sess)
    plain = [p["_args"][1] for m, p in wire.calls if m == "send_message"
             and kit.FA_ANSWER[:6] in str(p["_args"][1])]
    assert plain and not any(NEW_MARKS.search(t) for t in plain), plain


def test_the_interim_plain_fallback_opens_with_the_plain_result_line(mk_bot, run_async, wire):
    wire.fail_rich = True
    bot, sess = _wire_bot(mk_bot, wire)
    _interim(run_async, bot, sess)
    plain = [p["_args"][1] for m, p in wire.calls if m == "send_message"
             and kit.FA_ANSWER[:6] in str(p["_args"][1])]
    assert plain and plain[0].startswith("\U0001f4ac x1\n\n"), plain


# ---- the agents block edit after the answer ------------------------------------------------

def _block_edit(interim, run_async, wire):
    rich, bot, sess = interim()
    sent = rich["markdown"]
    (rec,) = sess.agents_lines
    sess.active_subagents.pop("a1", None)
    sess.bg_agent_stopped("a1", time.monotonic())
    before = len(wire.calls)
    run_async(asyncio.wait_for(
        bot.notify(sess, "agents_line_done", {"now": time.monotonic() + 1000}), timeout=5))
    edits = [p for m, p in wire.calls[before:] if m == "editMessageText"
             and p.get("message_id") == rec["msg_id"]]
    assert len(edits) == 1, wire.calls[before:]
    return sent, edits[0]["rich_message"]


def test_a_block_edit_keeps_the_marked_result_line_and_answer_byte_for_byte(
        interim, run_async, wire):
    sent, edited = _block_edit(interim, run_async, wire)
    head = sent.rsplit("\n\n---\n\n", 1)[0]
    assert edited["markdown"].startswith(head + "\n\n---\n\n")


def test_a_block_edit_keeps_the_answers_direction(interim, run_async, wire):
    _sent, edited = _block_edit(interim, run_async, wire)
    assert edited["is_rtl"] is True


def test_a_block_edit_redraws_the_block_marked(interim, run_async, wire):
    _sent, edited = _block_edit(interim, run_async, wire)
    block = _block(edited["markdown"])
    assert block and all(ln.removeprefix("- ").startswith(LRM) and ln.endswith(LRM)
                         for ln in block), block


@PARAS
def test_a_block_edit_reads_in_order(interim, run_async, wire, para):
    _sent, edited = _block_edit(interim, run_async, wire)
    tail = edited["markdown"].rsplit("\n\n---\n\n", 1)[1]
    assert [ln for ln in kit.lines(tail) if kit.order_problems(ln, para)] == []


# ---- held answers ---------------------------------------------------------------------------

def _held_bot(mk_bot, monkeypatch, *, rich_text, is_rtl=None, rich_fails=False):
    preferences.set_ui_language(CHAT, "")
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._confirm_held = MagicMock()
    sent: list[tuple[str, dict]] = []

    async def _rich(chat, text, **kw):
        if rich_fails:
            raise RuntimeError("bad markdown")
        sent.append((text, kw))
        return {"message_id": 1}
    monkeypatch.setattr(notify_mod, "send_rich_message", AsyncMock(side_effect=_rich))
    fields = dict(session="claude-x1", label="x1", chat_id=CHAT, key="k", rich_text=rich_text,
                  plain_text="\U0001f4ac x1\n\n" + kit.FA_ANSWER, reply_to=None,
                  attempts=held_mod.HELD_ANSWER_MAX_ATTEMPTS - 1 if rich_fails else 0,
                  held_seconds=lambda: 300.0)
    if is_rtl is not None:
        fields["is_rtl"] = is_rtl
    entry = SimpleNamespace(**fields)
    monkeypatch.setattr(held_mod.HELD, "pending", lambda chat: [entry])
    monkeypatch.setattr(held_mod.HELD, "drop", lambda chat, key: None)
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.IDLE)
    sess.scope_chat_id = CHAT
    bot.registry._sessions[sess.name] = sess
    return bot, sess, sent


HELD_RICH = RESULT_LINE + "\n\n" + kit.FA_ANSWER


@pytest.mark.parametrize("stored", [True, None], ids=["stored-is-rtl", "detected"])
def test_a_held_answers_late_marker_is_marked_in_the_rich_send(
        mk_bot, run_async, monkeypatch, stored):
    bot, sess, sent = _held_bot(mk_bot, monkeypatch, rich_text=HELD_RICH, is_rtl=stored)
    run_async(bot.flush_held_answers(sess))
    marker = sent[0][0].split("\n", 1)[0]
    assert marker == held_message.late_marker_text(i18n.get("en").directed(True), 300)


def test_a_held_answers_late_marker_starts_and_ends_with_an_lrm(mk_bot, run_async, monkeypatch):
    bot, sess, sent = _held_bot(mk_bot, monkeypatch, rich_text=HELD_RICH, is_rtl=True)
    run_async(bot.flush_held_answers(sess))
    marker = sent[0][0].split("\n", 1)[0]
    assert marker.startswith(LRM) and marker.endswith(LRM), marker


@PARAS
def test_a_held_answers_late_marker_reads_in_order(mk_bot, run_async, monkeypatch, para):
    bot, sess, sent = _held_bot(mk_bot, monkeypatch, rich_text=HELD_RICH, is_rtl=True)
    run_async(bot.flush_held_answers(sess))
    line = kit.lines(sent[0][0].split("\n", 1)[0])[0]
    assert kit.order_problems(line, para) == [], kit.visual_text(line, para)


def test_a_held_answer_goes_out_right_to_left(mk_bot, run_async, monkeypatch):
    bot, sess, sent = _held_bot(mk_bot, monkeypatch, rich_text=HELD_RICH, is_rtl=True)
    run_async(bot.flush_held_answers(sess))
    assert sent[0][1].get("is_rtl") is True


def test_a_held_answers_body_keeps_its_marked_result_line(mk_bot, run_async, monkeypatch):
    bot, sess, sent = _held_bot(mk_bot, monkeypatch, rich_text=HELD_RICH, is_rtl=True)
    run_async(bot.flush_held_answers(sess))
    assert sent[0][0].endswith("\n\n" + HELD_RICH)


def test_a_held_answers_plain_fallback_marker_carries_no_mark(mk_bot, run_async, monkeypatch):
    bot, sess, _sent = _held_bot(mk_bot, monkeypatch, rich_text=HELD_RICH, is_rtl=True,
                                 rich_fails=True)
    run_async(bot.flush_held_answers(sess))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text.split("\n", 1)[0] == held_message.late_marker_text(i18n.get("en"), 300)


def test_a_held_answers_plain_fallback_lead_carries_no_mark(mk_bot, run_async, monkeypatch):
    bot, sess, _sent = _held_bot(mk_bot, monkeypatch, rich_text=HELD_RICH, is_rtl=True,
                                 rich_fails=True)
    run_async(bot.flush_held_answers(sess))
    text = bot._app.bot.send_message.await_args.args[1]
    assert not NEW_MARKS.search(text), text


def test_an_english_held_answer_keeps_the_unmarked_marker(mk_bot, run_async, monkeypatch):
    bot, sess, sent = _held_bot(mk_bot, monkeypatch, rich_text="\U0001f4ac **x1**\n\ndone",
                                is_rtl=False)
    run_async(bot.flush_held_answers(sess))
    assert sent[0][0].split("\n", 1)[0] == held_mod.late_marker(300)


def _held_by_a_mute(mk_bot, run_async, wire):
    """A Stop while Telegram has muted the chat: the answer is held, with
    no HTTP, for a later flush."""
    bot, sess = _wire_bot(mk_bot, wire)
    sess.active_subagents.clear()
    sess.bg_agent_stopped("a1", time.monotonic())
    sess.busy_started_at = time.monotonic() - 250
    MUTE.mute(CHAT, 600.0)
    _interim(run_async, bot, sess)
    (entry,) = held_mod.HELD.pending(CHAT)
    return entry


def test_an_answer_held_by_a_mute_keeps_the_marked_finished_line(mk_bot, run_async, wire):
    entry = _held_by_a_mute(mk_bot, run_async, wire)
    first = entry.rich_text.split("\n", 1)[0]
    assert first.startswith(LRM) and first.endswith(LRM), first


def test_an_answer_held_by_a_mute_keeps_an_unmarked_plain_copy(mk_bot, run_async, wire):
    entry = _held_by_a_mute(mk_bot, run_async, wire)
    assert not NEW_MARKS.search(entry.plain_text), entry.plain_text
