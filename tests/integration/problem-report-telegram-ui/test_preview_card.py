"""SC-5 and SC-6: the preview card shows the exact report bytes, inline
when they fit, else as a report.json document, never cut (design.md
Success criteria 5, 6; spec.md "preview content equals render_preview
bytes"; entrypoints.md "Visible texts").

Methods: equivalence partitioning (a small report inline, an oversized
one as a document); boundary: the kept-card cap (5 kept, the 6th evicts
the oldest); error guessing: Telegram refusing the HTML of the inline
form, report JSON holding characters HTML must escape."""

from __future__ import annotations

import json

import pytest
from telegram.error import BadRequest

from aipager.report import builder, wording

MANY_FUNCS = 10


def _card(bot, h, run_async, drive):
    d = drive(bot)
    cid, mid = run_async(h.open_card(bot, d))
    return cid, mid, bot.tg.text_of(cid, mid)


def _many_bugs(h, n=MANY_FUNCS):
    for i in range(n):
        h.record_bug(fn=f"save_{i}", file="aipager/state.py")


# ---- SC-5: inline, exact -------------------------------------------------------

def test_card_heading(bot, drive, h, run_async):
    _, _, text = _card(bot, h, run_async, drive)
    assert text.startswith("🐞 <b>Report a problem</b>")


@pytest.mark.parametrize("line", wording.EXPLANATION)
def test_card_explanation_lines(bot, drive, h, run_async, line):
    _, _, text = _card(bot, h, run_async, drive)
    assert line.replace("'", "&#x27;") in text or line in text


def test_card_buttons_are_send_note_cancel(bot, drive, h, run_async):
    cid, mid, _ = _card(bot, h, run_async, drive)
    assert [d for _, d in bot.tg.buttons_of(cid, mid)] == ["_:rp:send", "_:rp:note",
                                                          "_:rp:cancel"]


def test_card_goes_to_owner_dm(bot, drive, h, run_async):
    cid, _, _ = _card(bot, h, run_async, drive)
    assert cid == h.OWNER


def test_inline_block_is_json(bot, drive, h, run_async):
    _, _, text = _card(bot, h, run_async, drive)
    assert json.loads(h.preview_block(text))["schema"] == "aipager-report/1"


def test_inline_block_equals_render_preview(bot, drive, h, run_async):
    """The block, html-unescaped, is exactly render_preview of the report
    it shows (render_preview is deterministic, so any re-wrapping, cutting
    or escaping drift breaks the equality)."""
    h.record_bug()
    _, _, text = _card(bot, h, run_async, drive)
    block = h.preview_block(text)
    assert block == builder.render_preview(json.loads(block))


def test_inline_block_with_html_specials_is_exact(bot, drive, h, run_async):
    """Error guessing: the report holds `<`, `>` and `&`-like bytes (e.g.
    "<10m" uptime buckets); escaping must round-trip exactly."""
    h.record_bug()
    _, _, text = _card(bot, h, run_async, drive)
    block = h.preview_block(text)
    assert block.encode("utf-8") == builder.render_preview(json.loads(block)).encode("utf-8")


def test_manual_card_trigger_is_manual(bot, drive, h, run_async):
    _, _, text = _card(bot, h, run_async, drive)
    assert json.loads(h.preview_block(text))["trigger"] == "manual"


def test_manual_card_carries_stored_errors(bot, drive, h, run_async):
    fp = h.record_bug()
    _, _, text = _card(bot, h, run_async, drive)
    assert [e["fingerprint"] for e in json.loads(h.preview_block(text))["errors"]] == [fp]


def test_card_holds_no_chat_id(bot, drive, h, run_async):
    """The report's own promise: no chat ids (here: the owner DM id)."""
    _, _, text = _card(bot, h, run_async, drive)
    assert str(h.OWNER) not in h.preview_block(text)


def test_card_is_sent_at_instant_priority(bot, drive, h, run_async):
    """User-visible state after a tap is INSTANT class (spec: memory
    instant user-visible state)."""
    _card(bot, h, run_async, drive)
    assert (bot.tg.cards()[-1]["kwargs"].get("rate_limit_args") or {}).get("class") == "instant"


# ---- SC-6: oversized goes as a document, never cut ----------------------------

def _big(bot, h, run_async, drive):
    _many_bugs(h)
    cid, mid, text = _card(bot, h, run_async, drive)
    return cid, mid, text, bot.tg.documents


def test_oversized_report_goes_as_one_document(bot, drive, h, run_async):
    _, _, _, docs = _big(bot, h, run_async, drive)
    assert len(docs) == 1


def test_oversized_report_document_is_really_oversized(bot, drive, h, run_async):
    """The partition is real: the document is larger than a Telegram
    message could carry."""
    _, _, _, docs = _big(bot, h, run_async, drive)
    assert len(docs[0]["bytes"]) > 4096


def test_oversized_document_bytes_equal_render_preview(bot, drive, h, run_async):
    _, _, _, docs = _big(bot, h, run_async, drive)
    data = docs[0]["bytes"]
    assert data == builder.render_preview(json.loads(data)).encode("utf-8")


def test_oversized_document_is_named_report_json(bot, drive, h, run_async):
    _, _, _, docs = _big(bot, h, run_async, drive)
    assert docs[0]["filename"] == "report.json"


def test_oversized_document_carries_every_error(bot, drive, h, run_async):
    """Never cut: all the stored errors are in it."""
    _, _, _, docs = _big(bot, h, run_async, drive)
    assert len(json.loads(docs[0]["bytes"])["errors"]) == MANY_FUNCS


def test_oversized_document_goes_to_owner_dm(bot, drive, h, run_async):
    _, _, _, docs = _big(bot, h, run_async, drive)
    assert docs[0]["chat_id"] == h.OWNER


def test_oversized_document_replies_to_card(bot, drive, h, run_async):
    _, mid, _, docs = _big(bot, h, run_async, drive)
    kw = docs[0]["kwargs"]
    target = kw.get("reply_to_message_id")
    if target is None and kw.get("reply_parameters") is not None:
        target = kw["reply_parameters"].message_id
    assert target == mid


def test_oversized_card_says_so(bot, drive, h, run_async):
    _, _, text, _ = _big(bot, h, run_async, drive)
    assert "The exact report is in the report.json file that replies to this card." in text


def test_oversized_card_has_no_cut_json(bot, drive, h, run_async):
    _, _, text, _ = _big(bot, h, run_async, drive)
    assert h.preview_block(text) is None


def test_oversized_card_fits_a_telegram_message(bot, drive, h, run_async):
    _, _, text, _ = _big(bot, h, run_async, drive)
    assert len(text.encode("utf-16-le")) // 2 <= 4096


def test_small_report_sends_no_document(bot, drive, h, run_async):
    _card(bot, h, run_async, drive)
    assert bot.tg.documents == []


# ---- error guessing: Telegram refuses the inline HTML --------------------------

def test_parse_refusal_falls_back_to_exact_document(bot, drive, h, run_async):
    state = {"first": True}

    def _fail():
        if state["first"]:
            state["first"] = False
            return BadRequest("Can't parse entities: unsupported start tag")
        return None
    bot.tg.fail_send = _fail
    d = drive(bot)
    run_async(d.tap("_:rp:open"))
    docs = bot.tg.documents
    assert len(docs) == 1 and docs[0]["bytes"] == builder.render_preview(
        json.loads(docs[0]["bytes"])).encode("utf-8")


# ---- the kept cards are bounded --------------------------------------------------

def _open_n(bot, h, run_async, drive, n):
    d = drive(bot)

    async def go():
        return [await h.open_card(bot, d) for _ in range(n)]
    return d, run_async(go())


def test_sixth_card_evicts_the_oldest(bot, drive, h, run_async, net):
    d, cards = _open_n(bot, h, run_async, drive, 6)
    cid, mid = cards[0]
    run_async(d.tap("_:rp:send", chat=cid, message_id=mid))
    assert net.posts == []


def test_second_oldest_card_survives_six_opens(bot, drive, h, run_async, net):
    """Boundary: 5 kept means the 2nd of 6 is still sendable."""
    d, cards = _open_n(bot, h, run_async, drive, 6)
    cid, mid = cards[1]
    run_async(d.tap("_:rp:send", chat=cid, message_id=mid))
    assert len(net.posts) == 1


def test_evicted_card_says_preview_again(bot, drive, h, run_async, net):
    d, cards = _open_n(bot, h, run_async, drive, 6)
    cid, mid = cards[0]
    run_async(d.tap("_:rp:send", chat=cid, message_id=mid))
    assert "no longer open" in bot.tg.text_of(cid, mid)
