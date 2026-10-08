"""Iteration 2: an offer answer's ``<ts>`` must be the offer's
``last_offer_ts`` written in ASCII digits (entrypoints.md: "<ts> is the
offer's last_offer_ts (int unix seconds). A tap whose <ts> is not the
current pending offer ... changes nothing"; coordinator's iteration-2
rule "offer stamps must be ASCII digits"; design.md Success criterion
17).

Error guessing: Python's ``int()`` accepts much more than ASCII digits.
It accepts other scripts' decimal digits ("١٧٩١..." or full-width
"１７９１..."), surrounding whitespace, a leading "+" and "_" digit
separators. Each of those spells the right number with the wrong
bytes. A forged or garbled callback must not count as the owner's answer.

Methods: equivalence partitioning over the stamp's spelling (ASCII,
other-script digits, padded, signed, separated); boundary: the exact
ASCII stamp (accepted) next to each of its look-alikes."""

from __future__ import annotations

import pytest

from aipager.report import store

_ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_FULLWIDTH = str.maketrans("0123456789", "０１２３４５６７８９")
_DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")


def _spell(ts: int, how: str) -> str:
    s = str(ts)
    return {
        "arabic_indic": s.translate(_ARABIC_INDIC),
        "fullwidth": s.translate(_FULLWIDTH),
        "devanagari": s.translate(_DEVANAGARI),
        "mixed_last_digit": s[:-1] + s[-1].translate(_ARABIC_INDIC),
        "leading_space": " " + s,
        "trailing_newline": s + "\n",
        "plus_sign": "+" + s,
        "underscores": s[:4] + "_" + s[4:],
    }[how]


LOOK_ALIKES = ["arabic_indic", "fullwidth", "devanagari", "mixed_last_digit",
               "leading_space", "trailing_newline", "plus_sign", "underscores"]


def _ts_of(notice) -> int:
    for row in notice["markup"].inline_keyboard:
        for b in row:
            if b.callback_data.startswith("_:rp:op:"):
                return int(b.callback_data.rsplit(":", 1)[1])
    raise AssertionError("no Preview button")


def _offered(w, run_async):
    async def go():
        await w.owner_acts()
        return await w.run_ticks()
    (notice,) = run_async(go())
    return notice, store.policy_state()


def _tap(w, run_async, notice, verb, ts_text):
    run_async(w.drive.tap(f"_:rp:{verb}:{ts_text}", chat=notice["chat_id"],
                          message_id=notice["message_id"]))


@pytest.mark.parametrize("how", LOOK_ALIKES)
def test_look_alike_is_the_same_number(how, offer_world, run_async):
    """The partition is real: Python's int() reads each look-alike as the
    offer's own stamp."""
    w = offer_world()
    notice, _ = _offered(w, run_async)
    assert int(_spell(_ts_of(notice), how)) == _ts_of(notice)


@pytest.mark.parametrize("how", LOOK_ALIKES)
def test_look_alike_stamp_fits_callback_data(how, offer_world, run_async):
    w = offer_world()
    notice, _ = _offered(w, run_async)
    assert len(f"_:rp:op:{_spell(_ts_of(notice), how)}".encode()) <= 64


@pytest.mark.parametrize("how", LOOK_ALIKES)
def test_look_alike_decline_changes_no_policy(how, offer_world, run_async):
    w = offer_world()
    notice, before = _offered(w, run_async)
    _tap(w, run_async, notice, "od", _spell(_ts_of(notice), how))
    assert store.policy_state() == before


@pytest.mark.parametrize("how", LOOK_ALIKES)
def test_look_alike_preview_opens_no_card(how, offer_world, h, run_async):
    w = offer_world()
    notice, _ = _offered(w, run_async)
    _tap(w, run_async, notice, "op", _spell(_ts_of(notice), how))
    assert w.bot.tg.cards(h.OWNER) == []


@pytest.mark.parametrize("how", LOOK_ALIKES)
def test_look_alike_leaves_the_offer_answerable(how, offer_world, h, run_async):
    """After the forged tap, the real stamp still works: the offer was
    not consumed."""
    w = offer_world()
    notice, _ = _offered(w, run_async)
    _tap(w, run_async, notice, "on", _spell(_ts_of(notice), how))
    _tap(w, run_async, notice, "op", str(_ts_of(notice)))
    assert len(w.bot.tg.cards(h.OWNER)) == 1


def test_ascii_stamp_opens_the_card(offer_world, h, run_async):
    """Positive control for every look-alike above."""
    w = offer_world()
    notice, _ = _offered(w, run_async)
    _tap(w, run_async, notice, "op", str(_ts_of(notice)))
    assert len(w.bot.tg.cards(h.OWNER)) == 1


def test_ascii_stamp_decline_counts(offer_world, run_async):
    w = offer_world()
    notice, _ = _offered(w, run_async)
    _tap(w, run_async, notice, "od", str(_ts_of(notice)))
    assert store.policy_state().declines_in_row == 1
