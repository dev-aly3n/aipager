"""The problem report preview card (roadmap 8.112 step 3, bot/report_flow.py).

Unit level: driven through ``bot._handle_callback`` and
``bot._handle_message`` with a recording fake of the Telegram calls and a
fake network (``report_flow.SEND_TRANSPORT``); nothing reaches Telegram,
Sentry or GitHub.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from telegram.error import BadRequest

from aipager import config, preferences
from aipager.bot import report_flow
from aipager.bot.flood import MUTE
from aipager.bot.transport import MUTED
from aipager.report import builder, policy, send, store, wording
from tests.report_ui_harness import (
    GROUP,
    OWNER,
    STRANGER,
    FakeNet,
    attachment,
    drain_sends,
    inline_block,
    key_doc,
    make_bot,
    tap,
    text_update,
    toasts,
)


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    return fake


def _no_network(request):
    raise AssertionError(f"no request may be made here: {request.url}")


@pytest.fixture
def no_net(monkeypatch):
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", httpx.MockTransport(_no_network))


def _open(run_async, bot, trigger="manual", errors=None):
    return run_async(report_flow.open_preview(bot, trigger=trigger, errors=errors))


def _kept(bot) -> report_flow.KeptReport:
    cards = list(bot._report_cards.values())
    assert len(cards) == 1
    return cards[0]


def _press(run_async, bot, data, **kw):
    """One tap, and the Send it may start, to the end."""
    update, query = tap(data, **kw)

    async def _go():
        await bot._handle_callback(update, MagicMock())
        await drain_sends(bot)
    run_async(_go())
    return query


def _press_card(run_async, bot, verb, kept, **kw):
    return _press(run_async, bot, f"_:rp:{verb}", message_id=kept.msg_id, **kw)


# ---- opening the preview ------------------------------------------------------

def test_inline_block_equals_render_preview(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    assert _open(run_async, bot) is report_flow.OpenResult.OPENED
    kept = _kept(bot)
    card = tg.sent[0]
    assert card.chat_id == OWNER and card.message_id == kept.msg_id
    assert card.kw["parse_mode"] == "HTML"
    assert inline_block(card.text) == builder.render_preview(kept.report)
    assert kept.preview == builder.render_preview(kept.report).encode("utf-8")
    assert "Report a problem" in card.text and "Nothing has been sent yet." in card.text
    data = [b.callback_data for row in card.kw["reply_markup"].inline_keyboard for b in row]
    assert data == ["_:rp:send", "_:rp:note", "_:rp:cancel"]
    assert card.kw["rate_limit_args"] == {"class": "instant"}
    assert tg.docs == []


def test_oversized_goes_as_exact_document(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr(report_flow, "TELEGRAM_MAX_TEXT_LEN", 300)
    bot, tg = make_bot(mk_bot)
    assert _open(run_async, bot) is report_flow.OpenResult.OPENED
    kept = _kept(bot)
    assert kept.mode == "document"
    assert inline_block(tg.sent[0].text) is None
    assert report_flow.DOCUMENT_LINE in tg.sent[0].text
    (doc,) = tg.docs
    assert doc.filename == "report.json" and doc.chat_id == OWNER
    assert doc.data == builder.render_preview(kept.report).encode("utf-8") == kept.preview
    assert doc.kw["reply_to_message_id"] == kept.msg_id


def test_a_refused_inline_card_goes_as_a_document(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    real = tg.send_message
    calls = []

    async def _first_refused(chat_id, text, **kw):
        calls.append(text)
        if len(calls) == 1:
            raise BadRequest("Can't parse entities")
        return await real(chat_id, text, **kw)

    tg.send_message = _first_refused
    assert _open(run_async, bot) is report_flow.OpenResult.OPENED
    kept = _kept(bot)
    assert kept.mode == "document" and tg.docs[0].data == kept.preview


def test_a_missing_document_never_offers_send(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr(report_flow, "TELEGRAM_MAX_TEXT_LEN", 300)
    bot, tg = make_bot(mk_bot)

    async def _refused(*a, **k):
        raise BadRequest("nope")

    tg.send_document = _refused
    assert _open(run_async, bot) is report_flow.OpenResult.NOT_SENT
    assert bot._report_cards == {}
    edit = tg.last_edit_of(tg.sent[0].message_id)
    assert edit["reply_markup"] is None and "could not be sent" in edit["text"]


def test_no_owner_opens_nothing(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    bot, tg = make_bot(mk_bot)
    built = []
    monkeypatch.setattr(builder, "build_report", lambda *a, **k: built.append(1))
    assert _open(run_async, bot) is report_flow.OpenResult.NO_OWNER
    assert tg.sent == [] and built == []


def test_a_muted_dm_opens_nothing(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    MUTE.mute(OWNER, 60)
    assert _open(run_async, bot) is report_flow.OpenResult.NOT_SENT
    assert tg.sent == [] and bot._report_cards == {}


def test_kept_cards_bounded(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    for _ in range(report_flow.MAX_KEPT_CARDS + 2):
        _open(run_async, bot)
    assert len(bot._report_cards) == report_flow.MAX_KEPT_CARDS
    kept_ids = {mid for _chat, mid in bot._report_cards}
    assert kept_ids == {m.message_id for m in tg.sent[-report_flow.MAX_KEPT_CARDS:]}


def test_kept_card_expires(mk_bot, run_async, no_net):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    kept.created -= report_flow.KEPT_CARD_TTL + 1
    query = _press_card(run_async, bot, "send", kept)
    assert bot._report_cards == {}
    assert report_flow.LOST_TEXT in query.edit_message_text.await_args.args[0]


def test_manual_report_carries_the_stores_errors(mk_bot, run_async):
    store.record_site("crash", file="aipager/cli/daemon.py", line=0, fn="_cmd_start",
                      where="daemon", trigger="crash", tier="bug")
    bot, _tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    assert kept.report["trigger"] == "manual"
    assert [e["fingerprint"] for e in kept.report["errors"]] == [
        e["fingerprint"] for e in store.errors()]


# ---- who may open it ----------------------------------------------------------

def _help(run_async, bot, *, user_id, chat_id):
    update = text_update("/help", user_id=user_id, chat_id=chat_id)
    run_async(bot._handle_help_cmd(update, MagicMock()))
    return update.message.reply_text.await_args.kwargs.get("reply_markup")


def test_help_button_for_the_owner_only(mk_bot, run_async):
    bot, _tg = make_bot(mk_bot)
    markup = _help(run_async, bot, user_id=OWNER, chat_id=OWNER)
    assert [b.callback_data for row in markup.inline_keyboard for b in row] == ["_:rp:open"]
    assert _help(run_async, bot, user_id=STRANGER, chat_id=OWNER) is None


def test_no_owner_no_button(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    bot, _tg = make_bot(mk_bot)
    assert _help(run_async, bot, user_id=OWNER, chat_id=OWNER) is None


@pytest.mark.parametrize("verb", ["open", "send", "note", "back", "cancel", "set",
                                  "set:off", "set:on", "op:1800000000", "od:1800000000"])
def test_non_owner_tap_refused_nothing_built(mk_bot, run_async, monkeypatch, verb, no_net):
    bot, tg = make_bot(mk_bot)
    monkeypatch.setattr(builder, "build_report",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("built")))
    query = _press(run_async, bot, f"_:rp:{verb}", user_id=STRANGER, chat_id=STRANGER)
    assert toasts(query) == [report_flow.NOT_OWNER_TEXT]
    assert tg.sent == [] and tg.edits == [] and tg.docs == []
    query.edit_message_text.assert_not_awaited()
    assert preferences.get_problem_reports() == "ask"


def test_group_tap_opens_in_dm(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    query = _press(run_async, bot, "_:rp:open", chat_id=GROUP)
    assert [m.chat_id for m in tg.sent] == [OWNER]
    assert toasts(query) == [report_flow.IN_DM_TEXT]


def test_card_verbs_only_in_the_owner_dm(mk_bot, run_async, no_net):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    query = _press(run_async, bot, "_:rp:send", chat_id=GROUP, message_id=kept.msg_id)
    assert toasts(query) == [report_flow.NOT_OWNER_TEXT]
    assert kept.state == "open"


def test_no_owner_tap_says_so(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    bot, tg = make_bot(mk_bot)
    query = _press(run_async, bot, "_:rp:open")
    assert toasts(query) == [report_flow.NO_OWNER_TEXT] and tg.sent == []


# ---- Send -----------------------------------------------------------------------

def test_sent_bytes_are_previewed_bytes_after_store_change(mk_bot, run_async, net):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    shown = inline_block(tg.sent[0].text).encode("utf-8")
    # The store changes after the preview: a new error, new counters.
    store.record_site("crash", file="aipager/cli/daemon.py", line=0, fn="_cmd_start",
                      where="daemon", trigger="crash", tier="bug")
    store.record_counter("watchdog_restart", 3)
    _press_card(run_async, bot, "send", kept)
    (post,) = net.posts
    assert attachment(post.content) == shown
    final = tg.last_edit_of(kept.msg_id)
    assert "Sent. Reference " in final["text"] and final["reply_markup"] is None
    assert kept.state == "done" and kept.report is None


def test_double_tap_sends_once(mk_bot, run_async, net):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)

    async def _both():
        u1, q1 = tap("_:rp:send", message_id=kept.msg_id)
        u2, q2 = tap("_:rp:send", message_id=kept.msg_id)
        await asyncio.gather(bot._handle_callback(u1, MagicMock()),
                             bot._handle_callback(u2, MagicMock()))
        await drain_sends(bot)
        return q1, q2

    q1, q2 = run_async(_both())
    assert len(net.posts) == 1
    assert report_flow.ALREADY_SENDING in toasts(q1) + toasts(q2)
    # A late tap after it went out does not overwrite the outcome.
    query = _press_card(run_async, bot, "send", kept)
    assert toasts(query) == [report_flow.ALREADY_SENT]
    assert len(net.posts) == 1


def test_send_runs_in_worker_thread(mk_bot, run_async, monkeypatch):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    report = kept.report
    seen = []

    def _fake_send(rep, transport=None, now=None):
        seen.append((threading.current_thread() is threading.main_thread(), rep, transport))
        return send.SendResult(send.SENT, "ap1-test")

    marker = httpx.MockTransport(_no_network)
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", marker)
    monkeypatch.setattr(send, "send", _fake_send)
    _press_card(run_async, bot, "send", kept)
    assert seen == [(False, report, marker)]
    assert seen[0][1] is report        # the very object previewed


def test_other_updates_are_handled_while_a_send_is_in_flight(mk_bot, run_async, monkeypatch):
    """PTB handles one update at a time: the tap's handler must return
    while Sentry is still answering, so the next update (another chat's
    tap, a message, Stop) is not queued behind the send."""
    gate = threading.Event()
    fake = FakeNet()

    def _gated(request):
        if request.method == "POST":
            gate.wait(10)
        return fake(request)

    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", httpx.MockTransport(_gated))
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)

    async def _go():
        try:
            u1, _q1 = tap("_:rp:send", message_id=kept.msg_id)
            # The handler returns with the send still waiting on the network.
            await asyncio.wait_for(bot._handle_callback(u1, MagicMock()), 2.0)
            assert kept.state == "sending" and not kept.send_task.done()
            # Another update goes through meanwhile: the settings page...
            u2, q2 = tap("_:rp:set")
            await asyncio.wait_for(bot._handle_callback(u2, MagicMock()), 2.0)
            q2.edit_message_text.assert_awaited()
            # ...and a second Send tap is told it is already going.
            u3, q3 = tap("_:rp:send", message_id=kept.msg_id)
            await asyncio.wait_for(bot._handle_callback(u3, MagicMock()), 2.0)
            assert not kept.send_task.done()
        finally:
            gate.set()
        await drain_sends(bot)
        return q3

    q3 = run_async(_go())
    assert toasts(q3) == [report_flow.ALREADY_SENDING]
    assert len(fake.posts) == 1
    assert "Sent. Reference " in tg.last_edit_of(kept.msg_id)["text"]


def test_a_send_that_raises_is_final_not_try_later(mk_bot, run_async, monkeypatch):
    """send.send should never raise; if it does, the same report would
    fail the same way: a final line, no Send button to loop on."""
    def _broken(rep, transport=None, now=None):
        raise AttributeError("'NoneType' object has no attribute 'rpartition'")

    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", httpx.MockTransport(_no_network))
    monkeypatch.setattr(send, "send", _broken)
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    _press_card(run_async, bot, "send", kept)
    final = tg.last_edit_of(kept.msg_id)
    assert report_flow.SEND_FAILED_TEXT in final["text"]
    assert wording.TRY_LATER not in final["text"]
    assert final["reply_markup"] is None
    assert kept.state == "done" and kept.report is None
    query = _press_card(run_async, bot, "send", kept)
    assert toasts(query) == [report_flow.ALREADY_SENT]


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


@pytest.mark.parametrize("case,outcome,keeps_buttons", [
    ("daily_cap", "daily_cap", False),
    ("kill_switch", "disabled", False),
    ("too_old", "too_old", False),
    ("offline", "offline", True),
    ("rate_limited", "rate_limited", True),
])
def test_outcome_lines(mk_bot, run_async, monkeypatch, case, outcome, keeps_buttons):
    fake = FakeNet()
    if case == "daily_cap":
        path = Path(config.REPORT_SENDS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"day": _today(), "count": send.SENDS_PER_DAY}))
    elif case == "kill_switch":
        fake.doc = key_doc(enabled=False)
    elif case == "too_old":
        fake.doc = key_doc(min_version="999.0.0")
    elif case == "offline":
        fake.sentry_status = 503
    else:
        fake.sentry_status = 429
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    _press_card(run_async, bot, "send", kept)
    edit = tg.last_edit_of(kept.msg_id)
    assert wording.OUTCOME_LINES[outcome] in edit["text"]
    if keeps_buttons:
        assert edit["reply_markup"] is not None and kept.state == "open"
        assert kept.report is not None
    else:
        assert edit["reply_markup"] is None and kept.state == "done"


def test_send_without_kept_report_sends_nothing(mk_bot, run_async, no_net):
    bot, tg = make_bot(mk_bot)
    query = _press(run_async, bot, "_:rp:send", message_id=77)
    text = query.edit_message_text.await_args.args[0]
    assert report_flow.LOST_TEXT in text
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert not Path(config.REPORT_SENDS_FILE).exists()


def test_cancel_drops_the_kept_report(mk_bot, run_async, no_net):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    query = _press_card(run_async, bot, "cancel", kept)
    assert bot._report_cards == {}
    assert report_flow.CANCELLED_TEXT in query.edit_message_text.await_args.args[0]
    assert not Path(config.REPORT_SENDS_FILE).exists()


# ---- Add a note -------------------------------------------------------------------

def _message(run_async, bot, text, **kw):
    update = text_update(text, **kw)

    async def _go():
        # The group -1 pre-handler runs first, then the message handler.
        from aipager.bot import new_flow
        await new_flow.close_if_moved_on(bot, update)
        await bot._handle_message(update, MagicMock())
        await asyncio.sleep(0)

    run_async(_go())
    return update


def _note_open(run_async, bot, tg):
    _open(run_async, bot)
    kept = _kept(bot)
    _press_card(run_async, bot, "note", kept)
    assert kept.state == "note"
    assert bot._report_note_pending["msg_id"] == kept.msg_id
    assert report_flow.NOTE_PROMPT[3:] in tg.last_edit_of(kept.msg_id)["text"]
    return kept


def test_note_becomes_the_reports_note(mk_bot, run_async, net):
    bot, tg = make_bot(mk_bot)
    kept = _note_open(run_async, bot, tg)
    update = _message(run_async, bot, "  it froze after /stop  ")
    assert bot._report_note_pending is None and kept.state == "open"
    assert kept.report["note"] == "it froze after /stop"
    edit = tg.last_edit_of(kept.msg_id)
    assert report_flow.NOTE_ADDED in edit["text"]
    assert inline_block(edit["text"]) == builder.render_preview(kept.report)
    update.message.reply_text.assert_not_awaited()
    _press_card(run_async, bot, "send", kept)
    assert json.loads(attachment(net.posts[0].content))["note"] == "it froze after /stop"


def test_note_normalized_and_cut(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    kept = _note_open(run_async, bot, tg)
    _message(run_async, bot, "x" * 700)
    assert kept.report["note"] == "x" * 500
    assert report_flow.NOTE_CUT in tg.last_edit_of(kept.msg_id)["text"]


def test_empty_note_adds_nothing(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    kept = _note_open(run_async, bot, tg)
    _message(run_async, bot, "​​")
    assert "note" not in kept.report
    assert report_flow.NOTE_EMPTY in tg.last_edit_of(kept.msg_id)["text"]


def test_note_in_document_mode_replaces_the_file(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr(report_flow, "TELEGRAM_MAX_TEXT_LEN", 300)
    bot, tg = make_bot(mk_bot)
    kept = _note_open(run_async, bot, tg)
    old_doc = kept.doc_msg_id
    _message(run_async, bot, "hello")
    assert len(tg.docs) == 2 and tg.docs[-1].data == kept.preview
    assert json.loads(kept.preview)["note"] == "hello"
    assert tg.deleted == [(OWNER, old_doc)]


def _photo_update():
    update = text_update("", message_id=6000)
    update.message.text = None
    update.message.photo = [MagicMock()]
    return update


@pytest.mark.parametrize("action", [
    "command", "other_button", "photo", "reply_to_other", "keyboard_word",
    "forward", "miniapp", "another_card"])
def test_other_action_closes(mk_bot, run_async, action):
    bot, tg = make_bot(mk_bot)
    kept = _note_open(run_async, bot, tg)
    from aipager.bot import new_flow

    async def _pre(update):
        await new_flow.close_if_moved_on(bot, update)
        await asyncio.sleep(0)

    if action == "command":
        run_async(_pre(text_update("/status")))
    elif action == "other_button":
        run_async(_pre(tap("_:set:back", message_id=kept.msg_id + 50)[0]))
    elif action == "photo":
        run_async(_pre(_photo_update()))
    elif action == "reply_to_other":
        run_async(_pre(text_update("hello", reply_to=kept.msg_id + 9)))
    elif action == "keyboard_word":
        run_async(_pre(text_update("status")))
    elif action == "forward":
        update = text_update("hello")
        update.message.forward_origin = MagicMock()
        run_async(_pre(update))
    elif action == "miniapp":
        async def _mini():
            report_flow.close_note(bot, OWNER)
            await asyncio.sleep(0)
        run_async(_mini())
    else:
        run_async(_pre(tap("_:rp:open", message_id=kept.msg_id + 50)[0]))
    assert bot._report_note_pending is None
    assert kept.state == "open"
    edit = tg.last_edit_of(kept.msg_id)
    assert report_flow.NOTE_CLOSED in edit["text"]
    assert [b.callback_data for row in edit["reply_markup"].inline_keyboard for b in row][0] \
        == "_:rp:send"
    # The next text is no note.
    assert not run_async(report_flow.maybe_handle_text(
        bot, text_update("late"), MagicMock(), "late"))
    assert "note" not in kept.report


def test_the_cards_own_buttons_and_the_note_leave_it_open(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    kept = _note_open(run_async, bot, tg)
    report_flow.on_update(bot, tap("_:rp:back", message_id=kept.msg_id)[0])
    report_flow.on_update(bot, text_update("my note"))
    report_flow.on_update(bot, text_update("my note", reply_to=kept.msg_id))
    # Someone else's action is theirs.
    report_flow.on_update(bot, text_update("/stop", user_id=STRANGER, chat_id=STRANGER))
    assert bot._report_note_pending is not None


def test_capture_not_armed_when_muted(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    MUTE.mute(OWNER, 60)
    _press_card(run_async, bot, "note", kept)
    assert bot._report_note_pending is None and kept.state == "open"


def test_capture_not_armed_when_the_edit_was_refused(mk_bot, run_async, monkeypatch):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)

    async def _muted(*a, **k):
        return MUTED

    monkeypatch.setattr(report_flow, "edit_text_at", _muted)
    _press_card(run_async, bot, "note", kept)
    assert bot._report_note_pending is None and kept.state == "open"


def test_back_closes_the_capture(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    kept = _note_open(run_async, bot, tg)
    _press_card(run_async, bot, "back", kept)
    assert bot._report_note_pending is None and kept.state == "open"
    assert inline_block(tg.last_edit_of(kept.msg_id)["text"]) == builder.render_preview(
        kept.report)


def test_an_expired_capture_takes_no_text(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    kept = _note_open(run_async, bot, tg)
    bot._report_note_pending["last_active"] -= report_flow.NOTE_TTL + 1
    assert not run_async(report_flow.maybe_handle_text(
        bot, text_update("late"), MagicMock(), "late"))
    assert "note" not in kept.report


def test_note_capture_closes_the_name_card(mk_bot, run_async):
    bot, tg = make_bot(mk_bot)
    _open(run_async, bot)
    kept = _kept(bot)
    from aipager.bot import new_flow
    new_flow._pending_store(bot)[(OWNER, OWNER)] = {"step": "name", "msg_id": 9,
                                                    "last_active": time.monotonic()}
    _press_card(run_async, bot, "note", kept)
    assert (OWNER, OWNER) not in new_flow._pending_store(bot)


# ---- /settings --------------------------------------------------------------------

def test_row_only_owner_dm(mk_bot):
    bot, _tg = make_bot(mk_bot)
    assert report_flow.settings_row_state(bot, OWNER, OWNER) == "Ask me"
    assert report_flow.settings_row_state(bot, GROUP, OWNER) is None
    assert report_flow.settings_row_state(bot, OWNER, STRANGER) is None
    assert report_flow.settings_row_state(bot, STRANGER, STRANGER) is None
    preferences.set_problem_reports("off")
    assert report_flow.settings_row_state(bot, OWNER, OWNER) == "Off"
    preferences.set_problem_reports("ask")
    store.save_policy(policy.State(auto_off=True))
    assert report_flow.settings_row_state(bot, OWNER, OWNER) == "offers off"


def test_settings_command_shows_the_row_in_the_owner_dm_only(mk_bot, run_async):
    bot, _tg = make_bot(mk_bot)

    def _rows(user_id, chat_id):
        update = text_update("/settings", user_id=user_id, chat_id=chat_id)
        run_async(bot._handle_settings_cmd(update, MagicMock()))
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    assert "_:rp:set" in _rows(OWNER, OWNER)
    assert "_:rp:set" not in _rows(STRANGER, OWNER)
    assert "_:rp:set" not in _rows(OWNER, GROUP)


def test_settings_back_rerenders_the_row(mk_bot, run_async):
    bot, _tg = make_bot(mk_bot)
    query = _press(run_async, bot, "_:set:back")
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert "_:rp:set" in [b.callback_data for row in markup.inline_keyboard for b in row]


def test_settings_switch(mk_bot, run_async):
    bot, _tg = make_bot(mk_bot)
    query = _press(run_async, bot, "_:rp:set")
    text = query.edit_message_text.await_args.args[0]
    assert "Problem reports" in text and report_flow.SETTINGS_AUTO_OFF not in text
    _press(run_async, bot, "_:rp:set:off")
    assert preferences.get_problem_reports() == "off"
    _press(run_async, bot, "_:rp:set:ask")
    assert preferences.get_problem_reports() == "ask"
    query = _press(run_async, bot, "_:rp:set:bogus")
    assert toasts(query) == [report_flow.INVALID_TEXT]


def test_reenable(mk_bot, run_async):
    bot, _tg = make_bot(mk_bot)
    store.save_policy(policy.State(last_offer_ts=int(time.time()) - 10, declines_in_row=2,
                                   auto_off=True))
    query = _press(run_async, bot, "_:rp:set")
    text = query.edit_message_text.await_args.args[0]
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    assert report_flow.SETTINGS_AUTO_OFF in text
    assert "_:rp:set:on" in [b.callback_data for row in markup.inline_keyboard for b in row]
    _press(run_async, bot, "_:rp:set:on")
    state = store.policy_state()
    assert state.auto_off is False and state.declines_in_row == 0
    store.reset()
    assert store.policy_state().auto_off is False      # persisted


def test_env_off_line(mk_bot, run_async, monkeypatch):
    monkeypatch.setenv("AIPAGER_REPORT_PROMPTS", "0")
    bot, _tg = make_bot(mk_bot)
    query = _press(run_async, bot, "_:rp:set")
    assert report_flow.SETTINGS_ENV_OFF in query.edit_message_text.await_args.args[0]


# ---- the memory-cap notice ----------------------------------------------------------

def _memcap(run_async, bot):
    from aipager.state import Status, TrackedSession
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    bot.registry._sessions[sess.name] = sess
    calls = []

    async def _send(*a, **k):
        calls.append((a, k))
        return MagicMock(message_id=1)

    bot._app.bot.send_message = _send
    run_async(bot.notify(sess, "hook_memory_cap_hit", {"hook": "aipager-hook", "tool": "Bash"}))
    return calls


def test_memory_cap_notice_carries_report_this(mk_bot, run_async):
    bot, _tg = make_bot(mk_bot)
    calls = _memcap(run_async, bot)
    (args, kwargs), = [c for c in calls if "memory cap hit" in c[0][1]]
    data = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert data == ["_:rp:open"]


def test_memory_cap_notice_without_owner_has_no_button(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    bot, _tg = make_bot(mk_bot)
    calls = _memcap(run_async, bot)
    (args, kwargs), = [c for c in calls if "memory cap hit" in c[0][1]]
    assert "reply_markup" not in kwargs
