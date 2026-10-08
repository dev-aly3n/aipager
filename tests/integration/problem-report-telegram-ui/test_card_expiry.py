"""Iteration 2: a kept preview card expires 24 h after it was opened
(design.md "Preview card": "each expires after 24 h"; "Risks": "kept
reports expire in 24 h"). An expired card's Send sends nothing and says
to preview again, the same as a card lost to a restart (Success
criterion 12).

The card clock is read through ``report_flow._mono``, the one seam the
developer named for this (the coordinator's iteration-2 note). It is not
listed in entrypoints.md, so the test only shifts it relative to its own
reading and assumes nothing else about it.

Methods: boundary-value analysis (one minute before and one minute after
24 h); equivalence partitioning over the verbs on an expired card (Send,
Add a note); error guessing: an old card opened beside a fresh one."""

from __future__ import annotations

import pytest

from aipager.bot import report_flow

TTL = 24 * 3600
LOST = ("This preview is no longer open, so nothing was sent. "
        "Open Report a problem again to see a fresh one.")


@pytest.fixture
def card_clock(monkeypatch):
    """Shift the card clock by ``card_clock["shift"]`` seconds."""
    real = report_flow._mono
    base = real()
    state = {"shift": 0.0}
    monkeypatch.setattr(report_flow, "_mono", lambda: base + state["shift"]
                        + (real() - base))
    return state


def _aged_send(bot, d, h, run_async, card_clock, age):
    async def go():
        cid, mid = await h.open_card(bot, d)
        card_clock["shift"] = age
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return cid, mid
    return run_async(go())


@pytest.mark.parametrize("age", [TTL + 60, 3 * TTL])
def test_expired_card_sends_nothing(bot, drive, h, run_async, net, card_clock, age):
    _aged_send(bot, drive(bot), h, run_async, card_clock, age)
    assert net.posts == []


@pytest.mark.parametrize("age", [TTL + 60, 3 * TTL])
def test_expired_card_says_preview_again(bot, drive, h, run_async, net, card_clock, age):
    cid, mid = _aged_send(bot, drive(bot), h, run_async, card_clock, age)
    assert LOST in bot.tg.text_of(cid, mid)


def test_expired_card_loses_its_buttons(bot, drive, h, run_async, net, card_clock):
    cid, mid = _aged_send(bot, drive(bot), h, run_async, card_clock, TTL + 60)
    assert bot.tg.buttons_of(cid, mid) == []


@pytest.mark.parametrize("age", [0, TTL - 60])
def test_card_younger_than_a_day_still_sends(bot, drive, h, run_async, net, card_clock, age):
    """Boundary just inside, and the control for the tests above."""
    _aged_send(bot, drive(bot), h, run_async, card_clock, age)
    assert len(net.posts) == 1


def test_expired_card_note_tap_does_not_capture(bot, drive, h, run_async, card_clock):
    """Add a note on an expired card does not arm a capture: the owner's
    next text is not taken as a note for a card that can no longer send."""
    d = drive(bot)

    async def go():
        cid, mid = await h.open_card(bot, d)
        card_clock["shift"] = TTL + 60
        await d.tap("_:rp:note", chat=cid, message_id=mid)
        return cid, mid
    cid, mid = run_async(go())
    assert "Type your note" not in bot.tg.text_of(cid, mid)


def test_expired_card_beside_a_fresh_one(bot, drive, h, run_async, net, card_clock):
    """Error guessing: only the old card expires; a card opened after the
    clock moved still sends."""
    d = drive(bot)

    async def go():
        await h.open_card(bot, d)
        card_clock["shift"] = TTL + 60
        cid, mid = await h.open_card(bot, d)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert len(net.posts) == 1
