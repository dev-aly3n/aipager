"""Iteration 2: what an offer notice's failure does to the offer
(design.md Success criterion 15, refined by the coordinator's
iteration-2 rule): the offer is persisted before the notice; it is taken
back (the slot not spent) only when the notice was definitely NOT
delivered (a MUTED or SKIPPED result, BadRequest, Forbidden, RetryAfter),
and kept when it may have arrived (TimedOut, NetworkError). And the
notice is edited after the preview outcome when "Preview report" is
tapped.

Methods: equivalence partitioning over the notice's failure classes
(restored: bad request, forbidden, retry after, muted, skipped; kept:
timed out, network error); error guessing: python-telegram-bot makes
BadRequest and TimedOut both subclasses of NetworkError, so a check that
tests NetworkError first would get one of the two wrong; the DM muted or
put in a 429 deferral between choosing the offer and sending it; a
timed-out notice that did reach the phone and is then answered; a
preview card that cannot be posted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from aipager import config
from aipager.bot.flood import MUTE
from aipager.report import policy, store

OPENED = "Opened the report preview below."

RESTORED = {
    "bad_request": lambda: BadRequest("Chat not found"),
    "forbidden": lambda: Forbidden("Forbidden: bot was blocked by the user"),
    "retry_after": lambda: RetryAfter(1),
}
KEPT = {
    "timed_out": lambda: TimedOut(),
    "network_error": lambda: NetworkError("Connection reset by peer"),
}


def _is_notice(kwargs) -> bool:
    return "_:rp:op:" in str(kwargs.get("reply_markup"))


def _ts_from(markup) -> int:
    for row in markup.inline_keyboard:
        for b in row:
            if b.callback_data.startswith("_:rp:op:"):
                return int(b.callback_data.rsplit(":", 1)[1])
    raise AssertionError("no Preview button")


def _fail_notice_with(w, make_exc):
    """Every attempt to send the notice raises *make_exc()*; other sends
    go through."""
    def _on_send(chat_id, args, kwargs):
        if _is_notice(kwargs):
            raise make_exc()
    w.bot.tg.on_send = _on_send


async def _offer(w, **ticks):
    await w.owner_acts()
    return await w.run_ticks(**ticks)


def _on_disk_offer() -> tuple:
    path = Path(config.REPORTS_FILE)
    doc = json.loads(path.read_text()) if path.exists() else {}
    pol = doc.get("policy") or {}
    return (list(pol.get("pending") or []), pol.get("last_offer_ts"))


# ---- the PTB hierarchy that makes this partition non-trivial ------------------------------

def test_bad_request_is_a_network_error_in_ptb():
    assert issubclass(BadRequest, NetworkError)


def test_timed_out_is_a_network_error_in_ptb():
    assert issubclass(TimedOut, NetworkError)


# ---- definitely not delivered: the offer is taken back ----------------------------------

@pytest.mark.parametrize("cls", sorted(RESTORED))
def test_undelivered_notice_restores_policy(offer_world, run_async, cls):
    w = offer_world()
    _fail_notice_with(w, RESTORED[cls])
    run_async(_offer(w, seconds=120))
    assert store.policy_state() == policy.State()


@pytest.mark.parametrize("cls", sorted(RESTORED))
def test_undelivered_notice_restores_policy_on_disk(offer_world, run_async, cls):
    w = offer_world()
    _fail_notice_with(w, RESTORED[cls])
    run_async(_offer(w, seconds=120))
    assert _on_disk_offer() == ([], None)


@pytest.mark.parametrize("cls", sorted(RESTORED))
def test_undelivered_notice_is_offered_again_later(offer_world, run_async, cls):
    w = offer_world()
    _fail_notice_with(w, RESTORED[cls])
    run_async(_offer(w, seconds=120))
    w.bot.tg.on_send = None
    assert len(run_async(w.run_ticks(offset=300))) == 1


# ---- may have arrived: the offer is kept -----------------------------------------------

@pytest.mark.parametrize("cls", sorted(KEPT))
def test_maybe_delivered_notice_keeps_the_pending_offer(offer_world, run_async, cls):
    w = offer_world()
    _fail_notice_with(w, KEPT[cls])
    run_async(_offer(w, seconds=120))
    assert store.policy_state().pending == (w.fp,)


@pytest.mark.parametrize("cls", sorted(KEPT))
def test_maybe_delivered_notice_keeps_the_offer_stamp(offer_world, run_async, cls):
    w = offer_world()
    _fail_notice_with(w, KEPT[cls])
    run_async(_offer(w, seconds=120))
    assert store.policy_state().last_offer_ts is not None


@pytest.mark.parametrize("cls", sorted(KEPT))
def test_maybe_delivered_notice_keeps_the_offer_on_disk(offer_world, run_async, cls):
    w = offer_world()
    _fail_notice_with(w, KEPT[cls])
    run_async(_offer(w, seconds=120))
    assert _on_disk_offer()[0] == [w.fp]


@pytest.mark.parametrize("cls", sorted(KEPT))
def test_maybe_delivered_notice_is_not_sent_again(offer_world, run_async, cls):
    """The slot was spent: no second notice within the offer gap."""
    w = offer_world()
    _fail_notice_with(w, KEPT[cls])
    run_async(_offer(w, seconds=120))
    w.bot.tg.on_send = None
    assert run_async(w.run_ticks(offset=300)) == []


@pytest.mark.parametrize("cls", sorted(KEPT))
def test_timed_out_notice_that_arrived_can_be_answered(offer_world, h, run_async, cls):
    """The notice reached the phone even though the call failed: its
    Preview report button still opens the card."""
    w = offer_world()
    seen = {}

    def _on_send(chat_id, args, kwargs):
        if _is_notice(kwargs):
            seen["ts"] = _ts_from(kwargs["reply_markup"])
            raise KEPT[cls]()
    w.bot.tg.on_send = _on_send
    run_async(_offer(w, seconds=120))
    w.bot.tg.on_send = None
    run_async(w.drive.tap(f"_:rp:op:{seen['ts']}", message_id=31337))
    assert len(w.bot.tg.cards(h.OWNER)) == 1


# ---- a MUTED result: the DM is muted between the choice and the send -------------

@pytest.fixture
def close_dm_after_choice(monkeypatch, h):
    """Error guessing: a 429 ban elsewhere mutes the owner DM right after
    the offer was chosen and persisted, before its notice goes out (a
    MUTED result). A SKIPPED result is not reachable here: the flood
    limiter sits inside python-telegram-bot's request path, which the
    fake Telegram replaces."""
    real = store.save_policy
    state = {"how": None, "calls": 0}

    def _save_policy(st, *a, **kw):
        out = real(st, *a, **kw)
        if st.pending and state["calls"] == 0:     # the offer, written before its notice
            state["calls"] += 1
            if state["how"] == "muted":
                MUTE.mute(h.OWNER, 600.0)
        return out
    monkeypatch.setattr(store, "save_policy", _save_policy)
    return state


@pytest.mark.parametrize("how", ["muted"])
def test_dm_closed_after_choice_sends_no_notice(offer_world, run_async, close_dm_after_choice,
                                               how):
    w = offer_world()
    close_dm_after_choice["how"] = how
    assert _notices_now(w, run_async) == []


@pytest.mark.parametrize("how", ["muted"])
def test_dm_closed_after_choice_restores_policy(offer_world, run_async, close_dm_after_choice,
                                               how):
    w = offer_world()
    close_dm_after_choice["how"] = how
    _notices_now(w, run_async)
    assert store.policy_state() == policy.State()


def test_dm_closed_after_choice_fixture_runs(offer_world, run_async, close_dm_after_choice):
    """Positive control: the offer really was chosen (the hook ran)."""
    w = offer_world()
    close_dm_after_choice["how"] = "muted"
    _notices_now(w, run_async)
    assert close_dm_after_choice["calls"] >= 1


def test_dm_left_open_after_choice_sends_the_notice(offer_world, run_async,
                                                    close_dm_after_choice):
    """Control: the hook alone (nothing closed) does not stop the notice."""
    w = offer_world()
    assert len(_notices_now(w, run_async)) == 1


def _notices_now(w, run_async):
    return run_async(_offer(w))


# ---- the notice is edited after the preview outcome ----------------------------------------

def _offered(w, run_async):
    run_async(_offer(w))
    (notice,) = w.notices()
    return notice


def _tap_preview(w, h, run_async, notice):
    run_async(w.drive.tap(f"_:rp:op:{_ts_from(notice['markup'])}", chat=notice["chat_id"],
                          message_id=notice["message_id"]))


def test_notice_edit_comes_after_the_card(offer_world, h, run_async):
    w = offer_world()
    notice = _offered(w, run_async)
    _tap_preview(w, h, run_async, notice)
    events = w.bot.tg.events
    card_at = next(i for i, e in enumerate(events) if e["op"] == "send"
                   and "Report a problem" in str(e["text"]))
    edit_at = next(i for i, e in enumerate(events) if e["op"] == "edit"
                   and e["message_id"] == notice["message_id"] and OPENED in str(e["text"]))
    assert card_at < edit_at


def _card_refused(w):
    def _on_send(chat_id, args, kwargs):
        if "Report a problem" in str(kwargs.get("text", args[0] if args else "")):
            raise Forbidden("Forbidden: bot was blocked by the user")
    w.bot.tg.on_send = _on_send


def test_notice_does_not_say_opened_when_the_card_fails(offer_world, h, run_async):
    w = offer_world()
    notice = _offered(w, run_async)
    _card_refused(w)
    _tap_preview(w, h, run_async, notice)
    assert OPENED not in w.bot.tg.text_of(notice["chat_id"], notice["message_id"])


def test_notice_is_edited_when_the_card_fails(offer_world, h, run_async):
    """The owner is told something, not left with a silent button."""
    w = offer_world()
    notice = _offered(w, run_async)
    before = w.bot.tg.text_of(notice["chat_id"], notice["message_id"])
    _card_refused(w)
    _tap_preview(w, h, run_async, notice)
    assert w.bot.tg.text_of(notice["chat_id"], notice["message_id"]) != before


def test_card_refused_fixture_posts_no_card(offer_world, h, run_async):
    """The partition above is real: no card reached the DM."""
    w = offer_world()
    notice = _offered(w, run_async)
    _card_refused(w)
    _tap_preview(w, h, run_async, notice)
    assert w.bot.tg.cards(h.OWNER) == []


def test_notice_failed_card_text_has_no_em_dash(offer_world, h, run_async):
    w = offer_world()
    notice = _offered(w, run_async)
    _card_refused(w)
    _tap_preview(w, h, run_async, notice)
    assert h.EM_DASH not in w.bot.tg.text_of(notice["chat_id"], notice["message_id"])
