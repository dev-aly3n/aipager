"""POST /api/report/send, black box (design.md Success criterion 6 and
the guard table; entrypoints.md "HTTP routes", "Send outcome facts",
"Side effects").

Every send goes to :class:`FakeNet` through ``report_flow.SEND_TRANSPORT``.

Methods: equivalence partitioning over the note (none, plain, normalized
already, needing normalization), the draft (fresh, unknown, expired,
evicted, foreign, lost by a restart, being sent, already final) and the
sender's outcomes; boundary-value analysis on the note (500 vs 501 code
points, 2000 vs 2001 for the body bound, emoji counted as one), the
draft id (64 vs 65 characters), the draft lifetime (just under vs over
24 h) and the draft bound (3 kept vs a 4th); error guessing: a double
tap, a lost response then a repeat, a ``report`` key smuggled in the
body, odd characters in the note (quotes, backslashes, RTL, flags, ZWJ),
note text leaking into logs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading

import httpx
import pytest

from aipager.bot import report_flow
from aipager.report import schema, wording

PLAIN_NOTE = "It froze when I sent a photo."


def _send_flow(bot, run_async, h, note=None, *, extra=None, record=True):
    """Build a draft as the owner, send it with *note*; return
    (draft body, send status, send body)."""
    if record:
        h.record_exc()

    async def go(api):
        _, d = await api.draft()
        status, body = await api.send(d["draft"], note, extra=extra)
        return d, status, body
    return h.serve(bot, run_async, go)


# ---- exact bytes --------------------------------------------------------------------------

def test_send_posts_exactly_one_envelope(bot, h, run_async, net):
    _send_flow(bot, run_async, h)
    assert len(net.posts) == 1


def test_sent_attachment_equals_preview_without_note(bot, h, run_async, net):
    d, _, _ = _send_flow(bot, run_async, h)
    assert h.attachment_of(net.posts[0]) == d["preview"].encode("utf-8")


def test_sent_attachment_equals_displayed_report_without_note(bot, h, run_async, net):
    d, _, _ = _send_flow(bot, run_async, h)
    assert h.attachment_of(net.posts[0]) == h.exact(d["report"])


def test_empty_note_sends_no_note_key(bot, h, run_async, net):
    d, _, _ = _send_flow(bot, run_async, h, note="")
    assert h.attachment_of(net.posts[0]) == h.exact(d["report"])


def test_sent_attachment_equals_displayed_report_with_note(bot, h, run_async, net):
    d, _, _ = _send_flow(bot, run_async, h, note=PLAIN_NOTE)
    assert h.attachment_of(net.posts[0]) == h.exact(d["report"], PLAIN_NOTE)


def test_the_note_is_the_last_key_sent(bot, h, run_async, net):
    _send_flow(bot, run_async, h, note=PLAIN_NOTE)
    sent = json.loads(h.attachment_of(net.posts[0]))
    assert list(sent)[-1] == "note"


TRICKY = {
    "quotes": 'He said "it broke" and \'left\'',
    "backslashes": "C:\\Users\\x\\path and \\n literally",
    "rtl": "\u05e9\u05dc\u05d5\u05dd \u0645\u0631\u062d\u0628\u0627 mixed",
    "flag": "flag \U0001F1EE\U0001F1F9 ok",
    "zwj-family": "family \U0001F468\u200d\U0001F469\u200d\U0001F467 here",
    "emoji": "\U0001F993\U0001F525 broken",
    "newline": "line one\nline two",
    "html": "</script><b>x</b> & <img src=x>",
    "lrm": "abc\u200edef",
    "combining": "e\u0301 cafe\u0301",
    "cjk": "\u58ca\u308c\u305f",
}


@pytest.mark.parametrize("raw", list(TRICKY.values()), ids=list(TRICKY))
def test_normalized_tricky_note_is_sent_byte_for_byte(bot, h, run_async, net, raw):
    """The normalized form (the server's authority) is accepted, and the
    bytes sent are the report plus that note, json.dumps, no ASCII
    escaping."""
    note = schema.normalize_note(raw) or ""
    d, status, _ = _send_flow(bot, run_async, h, note=note)
    assert h.attachment_of(net.posts[0]) == h.exact(d["report"], note)


@pytest.mark.parametrize("raw", list(TRICKY.values()), ids=list(TRICKY))
def test_tricky_note_is_either_accepted_or_refused_with_its_normal_form(
        bot, h, run_async, net, raw):
    expected = schema.normalize_note(raw) or ""
    _, status, body = _send_flow(bot, run_async, h, note=raw)
    if expected == raw:
        assert (status, body["outcome"]) == (200, "sent")
    else:
        assert (status, body) == (422, {"error": "note_changed", "note": expected})


def test_a_report_key_in_the_body_is_ignored(bot, h, run_async, net):
    d, _, _ = _send_flow(bot, run_async, h, extra={"report": {"schema": "evil", "x": 1}})
    assert h.attachment_of(net.posts[0]) == d["preview"].encode("utf-8")


def test_a_report_key_in_the_body_does_not_refuse(bot, h, run_async, net):
    _, status, _ = _send_flow(bot, run_async, h, extra={"report": "anything"})
    assert status == 200


def test_unknown_keys_are_ignored(bot, h, run_async, net):
    _, status, _ = _send_flow(bot, run_async, h, extra={"preview": "x", "trigger": "auto"})
    assert status == 200


def test_send_uses_the_draft_not_the_store_now(bot, h, run_async, net):
    """The store changes between draft and send: the draft's bytes go."""
    h.record_exc()

    async def go(api):
        _, d = await api.draft()
        h.record_exc(ValueError)
        await api.send(d["draft"], "")
        return d
    d = h.serve(bot, run_async, go)
    assert h.attachment_of(net.posts[0]) == d["preview"].encode("utf-8")


# ---- note normalization -------------------------------------------------------------------

@pytest.mark.parametrize("note, normal", [
    ("hello ", "hello"),
    (" hello", "hello"),
    ("hello\n", "hello"),
    ("\thello\t", "hello"),
], ids=["trailing-space", "leading-space", "trailing-newline", "tabs"])
def test_note_with_surrounding_space_is_refused_with_normalized_note(
        bot, h, run_async, net, note, normal):
    _, status, body = _send_flow(bot, run_async, h, note=note)
    assert (status, body) == (422, {"error": "note_changed", "note": normal})


@pytest.mark.parametrize("note", ["\u200b", "\u200b\u200d\ufeff", "   ", "\n\n"],
                         ids=["zwsp", "invisible-mix", "spaces", "newlines"])
def test_invisible_only_note_is_refused_with_empty_note(bot, h, run_async, net, note):
    _, status, body = _send_flow(bot, run_async, h, note=note)
    assert (status, body) == (422, {"error": "note_changed", "note": ""})


def test_over_500_is_refused_with_cut_note(bot, h, run_async, net):
    _, status, body = _send_flow(bot, run_async, h, note="a" * 501)
    assert (status, body) == (422, {"error": "note_changed", "note": "a" * 500})


def test_exactly_500_is_accepted(bot, h, run_async, net):
    _, status, body = _send_flow(bot, run_async, h, note="a" * 500)
    assert (status, body["outcome"]) == (200, "sent")


def test_500_emoji_code_points_are_accepted(bot, h, run_async, net):
    """500 code points but 1000 UTF-16 units and 2000 bytes: the limit
    counts code points."""
    _, status, body = _send_flow(bot, run_async, h, note="\U0001F993" * 500)
    assert (status, body["outcome"]) == (200, "sent")


def test_500_emoji_note_bytes_are_sent(bot, h, run_async, net):
    note = "\U0001F993" * 500
    d, _, _ = _send_flow(bot, run_async, h, note=note)
    assert h.attachment_of(net.posts[0]) == h.exact(d["report"], note)


def test_501_emoji_are_refused_with_500(bot, h, run_async, net):
    _, status, body = _send_flow(bot, run_async, h, note="\U0001F993" * 501)
    assert (status, body) == (422, {"error": "note_changed", "note": "\U0001F993" * 500})


@pytest.mark.parametrize("note", ["hello ", "\u200b", "a" * 501],
                         ids=["trailing", "invisible", "too-long"])
def test_note_changed_sends_nothing(bot, h, run_async, net, note):
    _send_flow(bot, run_async, h, note=note)
    assert net.requests == []


def test_note_changed_keeps_the_draft_open(bot, h, run_async, net):
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, "hello ")
        return await api.send(draft, "hello")
    status, body = h.serve(bot, run_async, go)
    assert (status, body["outcome"]) == (200, "sent")


def test_after_note_changed_the_normalized_note_is_what_goes(bot, h, run_async, net):
    h.record_exc()

    async def go(api):
        _, d = await api.draft()
        _, refused = await api.send(d["draft"], "  tidy me  ")
        await api.send(d["draft"], refused["note"])
        return d
    d = h.serve(bot, run_async, go)
    assert h.attachment_of(net.posts[0]) == h.exact(d["report"], "tidy me")


# ---- body bounds (400) ----------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    [], "x", 5, None,
    {"note": "hi"},
    {"draft": 123},
    {"draft": None},
    {"draft": ["a"]},
    {"draft": ""},
    {"draft": "d" * 65},
    {"draft": "d", "note": 5},
    {"draft": "d", "note": ["x"]},
    {"draft": "d", "note": "a" * 2001},
], ids=["list", "string", "number", "null", "no-draft", "draft-int", "draft-null",
        "draft-list", "draft-empty", "draft-65", "note-int", "note-list", "note-2001"])
def test_bad_body_is_400(bot, h, run_async, net, body):
    status, resp = h.serve(bot, run_async,
                           lambda api: api.post("/api/report/send", json_body=body))
    assert (status, resp) == (400, {"error": "bad_request"})


def test_body_that_is_not_json_is_400(bot, h, run_async, net):
    status, _ = h.serve(bot, run_async,
                        lambda api: api.post("/api/report/send", raw=b"{nope"))
    assert status == 400


def test_draft_of_64_chars_is_in_bounds(bot, h, run_async, net):
    """Boundary: 64 is allowed, so an unknown 64-character id is 410, not
    400."""
    status, _ = h.serve(bot, run_async, lambda api: api.send("d" * 64, ""))
    assert status == 410


def test_draft_of_one_char_is_in_bounds(bot, h, run_async, net):
    status, _ = h.serve(bot, run_async, lambda api: api.send("d", ""))
    assert status == 410


def test_note_of_2000_code_points_is_in_bounds(bot, h, run_async, net):
    """Boundary: 2000 code points pass the body bound and reach the note
    check (over 500, so note_changed)."""
    _, status, body = _send_flow(bot, run_async, h, note="\U0001F993" * 2000)
    assert (status, body.get("error")) == (422, "note_changed")


def test_bad_body_sends_nothing(bot, h, run_async, net):
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        return await api.post("/api/report/send", json_body={"draft": draft, "note": 7})
    h.serve(bot, run_async, go)
    assert net.requests == []


# ---- gate on the send route ------------------------------------------------------------------

def test_bad_init_data_is_401_nothing_sent(bot, h, run_async, net):
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        return await api.post("/api/report/send", json_body={"draft": draft, "note": ""},
                              hdrs=h.bad_headers())
    status, _ = h.serve(bot, run_async, go)
    assert (status, len(net.requests)) == (401, 0)


def test_bad_init_data_with_a_bad_body_is_401(bot, h, run_async, net):
    status, _ = h.serve(bot, run_async, lambda api: api.post(
        "/api/report/send", json_body=[], hdrs=h.bad_headers()))
    assert status == 401


def test_non_owner_cannot_send_owners_draft(make_bot, h, run_async, net):
    h.record_exc()

    async def go(api):
        draft = await api.draft_id(scope=h.OWNER)
        return await api.send(draft, "", user=h.ADMIN, scope=h.GROUP)
    status, body = h.serve(make_bot("scope"), run_async, go)
    assert (status, body, len(net.requests)) == (403, {"error": "forbidden"}, 0)


def test_stranger_cannot_send_owners_draft(make_bot, h, run_async, net):
    async def go(api):
        draft = await api.draft_id(scope=h.OWNER)
        return await api.send(draft, "", user=h.STRANGER)
    status, _ = h.serve(make_bot("scope"), run_async, go)
    assert (status, len(net.requests)) == (403, 0)


def test_send_from_group_scope_is_refused(make_bot, h, run_async, net):
    async def go(api):
        draft = await api.draft_id(scope=h.OWNER)
        return await api.send(draft, "", scope=h.GROUP)
    status, body = h.serve(make_bot("scope"), run_async, go)
    assert (status, body, len(net.requests)) == (403, {"error": "private_only"}, 0)


@pytest.mark.parametrize("mode", ["scope_no_dm", "scope_two_owners"])
def test_send_without_owner_is_409(make_bot, h, run_async, net, mode):
    status, body = h.serve(make_bot(mode), run_async,
                           lambda api: api.send("whatever", "", scope=h.GROUP))
    assert (status, body) == (409, {"error": "no_owner"})


def test_rate_limited_send_is_429_nothing_sent(bot, h, run_async, net):
    async def go(api):
        draft = await api.draft_id()
        api.exhaust_budget()
        return await api.send(draft, "")
    status, body = h.serve(bot, run_async, go)
    assert (status, body, len(net.requests)) == (429, {"error": "too_many_requests"}, 0)


def test_rate_limited_send_keeps_the_draft(bot, h, run_async, net):
    async def go(api):
        draft = await api.draft_id()
        api.exhaust_budget()
        await api.send(draft, "")
        api.keep_budget = False
        return await api.send(draft, "")
    status, body = h.serve(bot, run_async, go)
    assert (status, body["outcome"]) == (200, "sent")


def test_order_private_only_before_429_on_send(make_bot, h, run_async, net):
    async def go(api):
        draft = await api.draft_id(scope=h.OWNER)
        api.exhaust_budget()
        return await api.send(draft, "", scope=h.GROUP)
    _, body = h.serve(make_bot("scope"), run_async, go)
    assert body == {"error": "private_only"}


# ---- stale drafts (410) -------------------------------------------------------------------

def test_unknown_draft_is_410_draft_gone(bot, h, run_async, net):
    status, body = h.serve(bot, run_async, lambda api: api.send("no-such-draft", ""))
    assert (status, body) == (410, {"error": "draft_gone"})


def test_unknown_draft_sends_nothing(bot, h, run_async, net):
    h.serve(bot, run_async, lambda api: api.send("no-such-draft", ""))
    assert net.requests == []


def test_unknown_draft_with_a_bad_note_is_410_not_422(bot, h, run_async, net):
    """Order: the lookup comes before the note check."""
    status, _ = h.serve(bot, run_async, lambda api: api.send("no-such-draft", "x "))
    assert status == 410


def _expired_send(bot, h, run_async, clock, age):
    async def go(api):
        draft = await api.draft_id()
        clock[0] += age
        return await api.send(draft, "")
    return h.serve(bot, run_async, go)


def test_expired_draft_is_410_nothing_sent(bot, h, run_async, net, clock):
    status, body = _expired_send(bot, h, run_async, clock, 86400 + 1)
    assert (status, body, len(net.requests)) == (410, {"error": "draft_gone"}, 0)


def test_draft_well_past_24h_is_410(bot, h, run_async, net, clock):
    status, _ = _expired_send(bot, h, run_async, clock, 7 * 86400)
    assert status == 410


def test_draft_just_under_24h_still_sends(bot, h, run_async, net, clock):
    status, body = _expired_send(bot, h, run_async, clock, 86400 - 5)
    assert (status, body["outcome"]) == (200, "sent")


def test_evicted_draft_is_410(bot, h, run_async, net, clock):
    async def go(api):
        ids = []
        for _ in range(4):
            ids.append(await api.draft_id())
            clock[0] += 1
        return await api.send(ids[0], "")
    status, body = h.serve(bot, run_async, go)
    assert (status, body, len(net.requests)) == (410, {"error": "draft_gone"}, 0)


def test_three_drafts_are_all_kept(bot, h, run_async, net, clock):
    """Boundary: MAX_DRAFTS is 3, so the oldest of three still sends."""
    async def go(api):
        ids = []
        for _ in range(3):
            ids.append(await api.draft_id())
            clock[0] += 1
        return await api.send(ids[0], "")
    status, body = h.serve(bot, run_async, go)
    assert (status, body["outcome"]) == (200, "sent")


def test_a_fourth_draft_keeps_the_newest(bot, h, run_async, net, clock):
    async def go(api):
        ids = []
        for _ in range(4):
            ids.append(await api.draft_id())
            clock[0] += 1
        return await api.send(ids[-1], "")
    status, _ = h.serve(bot, run_async, go)
    assert status == 200


def test_foreign_draft_is_410_nothing_sent(bot, h, run_async, net, monkeypatch):
    """The owner changed between draft and send (personal mode, CHAT_ID
    moved): the new owner cannot send the old owner's draft."""
    async def go(api):
        draft = await api.draft_id(user=h.OWNER)
        monkeypatch.setattr("aipager.config.CHAT_ID", h.OTHER_OWNER)
        return await api.send(draft, "", user=h.OTHER_OWNER)
    status, body = h.serve(bot, run_async, go)
    assert (status, body, len(net.requests)) == (410, {"error": "draft_gone"}, 0)


def test_foreign_draft_in_scope_mode_is_410(make_bot, h, run_async, net):
    bot = make_bot("scope")

    async def go(api):
        draft = await api.draft_id(scope=h.OWNER)
        bot.scopes = [h.owner_dm_scope(h.OTHER_OWNER), h.group_scope()]
        return await api.send(draft, "", user=h.OTHER_OWNER, scope=h.OTHER_OWNER)
    status, _ = h.serve(bot, run_async, go)
    assert (status, len(net.requests)) == (410, 0)


def test_draft_lost_by_a_restart_is_410(make_bot, h, run_async, net):
    old, new = make_bot("personal"), make_bot("personal")
    draft = h.serve(old, run_async, lambda api: api.draft_id())
    status, body = h.serve(new, run_async, lambda api: api.send(draft, ""))
    assert (status, body, len(net.requests)) == (410, {"error": "draft_gone"}, 0)


# ---- single flight -------------------------------------------------------------------------

def _gated(use_net, h):
    gate = threading.Event()
    return use_net(h.FakeNet(gate=gate)), gate


def test_double_send_posts_once(bot, h, run_async, use_net):
    fake, gate = _gated(use_net, h)
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        first = asyncio.ensure_future(api.send(draft, ""))
        await h.wait_until(fake.post_started.is_set, what="the first POST")
        second = await api.send(draft, "")
        gate.set()
        return await first, second
    h.serve(bot, run_async, go)
    assert len(fake.posts) == 1


def test_second_send_while_sending_is_409_sending(bot, h, run_async, use_net):
    fake, gate = _gated(use_net, h)

    async def go(api):
        draft = await api.draft_id()
        first = asyncio.ensure_future(api.send(draft, ""))
        await h.wait_until(fake.post_started.is_set, what="the first POST")
        second = await api.send(draft, "")
        gate.set()
        await first
        return second
    assert h.serve(bot, run_async, go) == (409, {"error": "sending"})


def test_first_send_still_succeeds_after_a_409(bot, h, run_async, use_net):
    fake, gate = _gated(use_net, h)

    async def go(api):
        draft = await api.draft_id()
        first = asyncio.ensure_future(api.send(draft, ""))
        await h.wait_until(fake.post_started.is_set, what="the first POST")
        await api.send(draft, "")
        gate.set()
        return await first
    status, body = h.serve(bot, run_async, go)
    assert (status, body["outcome"]) == (200, "sent")


def test_sending_draft_with_a_bad_note_is_409_not_422(bot, h, run_async, use_net):
    """Order: the state check comes before the note check."""
    fake, gate = _gated(use_net, h)

    async def go(api):
        draft = await api.draft_id()
        first = asyncio.ensure_future(api.send(draft, ""))
        await h.wait_until(fake.post_started.is_set, what="the first POST")
        second = await api.send(draft, "bad ")
        gate.set()
        await first
        return second[0]
    assert h.serve(bot, run_async, go) == 409


def test_simultaneous_sends_post_once(bot, h, run_async, use_net):
    fake, gate = _gated(use_net, h)

    async def go(api):
        draft = await api.draft_id()
        tasks = [asyncio.ensure_future(api.send(draft, "")) for _ in range(2)]
        await h.wait_until(lambda: any(t.done() for t in tasks) or len(fake.posts) > 1,
                           what="one of two sends to answer")
        gate.set()
        return [await t for t in tasks]
    h.serve(bot, run_async, go)
    assert len(fake.posts) == 1


def test_simultaneous_sends_answer_200_and_409(bot, h, run_async, use_net):
    fake, gate = _gated(use_net, h)

    async def go(api):
        draft = await api.draft_id()
        tasks = [asyncio.ensure_future(api.send(draft, "")) for _ in range(2)]
        await h.wait_until(lambda: any(t.done() for t in tasks) or len(fake.posts) > 1,
                           what="one of two sends to answer")
        gate.set()
        return [await t for t in tasks]
    statuses = sorted(s for s, _ in h.serve(bot, run_async, go))
    assert statuses == [200, 409]


def test_a_sending_draft_survives_three_newer_drafts(bot, h, run_async, use_net, clock):
    """Eviction never drops a draft being sent: its send still answers."""
    fake, gate = _gated(use_net, h)

    async def go(api):
        draft = await api.draft_id()
        first = asyncio.ensure_future(api.send(draft, ""))
        await h.wait_until(fake.post_started.is_set, what="the first POST")
        for _ in range(3):
            clock[0] += 1
            await api.draft_id()
        gate.set()
        return await first
    status, body = h.serve(bot, run_async, go)
    assert (status, body["outcome"]) == (200, "sent")


# ---- outcomes -------------------------------------------------------------------------------

def test_sent_outcome_is_sent(bot, h, run_async, net):
    _, _, body = _send_flow(bot, run_async, h)
    assert body["outcome"] == "sent"


def test_sent_reference_has_the_ap1_form(bot, h, run_async, net):
    _, _, body = _send_flow(bot, run_async, h)
    assert re.fullmatch(r"ap1-[0-9a-f]{12}", body["reference"])


def test_sent_reference_is_the_first_errors_fingerprint(bot, h, run_async, net):
    d, _, body = _send_flow(bot, run_async, h)
    assert body["reference"] == d["report"]["errors"][0]["fingerprint"]


def test_sent_reference_without_errors_is_an_event_id(bot, h, run_async, net):
    _, _, body = _send_flow(bot, run_async, h, record=False)
    assert re.fullmatch(r"[0-9a-f]{32}", body["reference"])


def test_sent_line_is_the_shared_wording(bot, h, run_async, net):
    _, _, body = _send_flow(bot, run_async, h)
    assert body["line"] == report_flow.outcome_line("sent", body["reference"])


def test_sent_is_final(bot, h, run_async, net):
    _, _, body = _send_flow(bot, run_async, h)
    assert (body["retry"], body["repeat"]) == (False, False)


def test_sent_counts_in_the_daily_file(bot, h, run_async, net):
    _send_flow(bot, run_async, h)
    assert h.sends_file().exists()


TRY_LATER_NETS = {
    "503": {"sentry_status": 503},
    "429": {"sentry_status": 429},
    "400": {"sentry_status": 400},
    "connect-error": {"sentry_error": httpx.ConnectError("down")},
    "timeout": {"sentry_error": httpx.ReadTimeout("slow")},
}


@pytest.mark.parametrize("kw", list(TRY_LATER_NETS.values()), ids=list(TRY_LATER_NETS))
def test_try_later_outcome_is_retryable(bot, h, run_async, use_net, kw):
    use_net(h.FakeNet(**kw))
    _, status, body = _send_flow(bot, run_async, h)
    assert (status, body["outcome"] in wording.RETRYABLE, body["retry"]) == (200, True, True)


@pytest.mark.parametrize("kw", list(TRY_LATER_NETS.values()), ids=list(TRY_LATER_NETS))
def test_try_later_line(bot, h, run_async, use_net, kw):
    use_net(h.FakeNet(**kw))
    _, _, body = _send_flow(bot, run_async, h)
    assert body["line"] == wording.TRY_LATER


@pytest.mark.parametrize("kw", list(TRY_LATER_NETS.values()), ids=list(TRY_LATER_NETS))
def test_try_later_has_no_reference(bot, h, run_async, use_net, kw):
    use_net(h.FakeNet(**kw))
    _, _, body = _send_flow(bot, run_async, h)
    assert body["reference"] is None


def test_try_later_keeps_the_draft_and_a_retry_sends(bot, h, run_async, use_net):
    use_net(h.FakeNet(sentry_status=503))
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, PLAIN_NOTE)
        up = use_net(h.FakeNet())
        status, body = await api.send(draft, PLAIN_NOTE)
        return status, body, up
    status, body, up = h.serve(bot, run_async, go)
    assert (status, body["outcome"], len(up.posts)) == (200, "sent", 1)


def test_retry_after_try_later_sends_the_same_bytes(bot, h, run_async, use_net):
    down = use_net(h.FakeNet(sentry_status=503))
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, PLAIN_NOTE)
        up = use_net(h.FakeNet())
        await api.send(draft, PLAIN_NOTE)
        return up
    up = h.serve(bot, run_async, go)
    assert h.attachment_of(up.posts[0]) == h.attachment_of(down.posts[0])


def test_retry_after_try_later_is_not_a_repeat(bot, h, run_async, use_net):
    use_net(h.FakeNet(sentry_status=503))

    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, "")
        use_net(h.FakeNet())
        return await api.send(draft, "")
    _, body = h.serve(bot, run_async, go)
    assert body["repeat"] is False


FINAL_DOCS = {
    "disabled": {"enabled": False},
    "too_old": {"min_version": "99.0"},
}


@pytest.mark.parametrize("outcome", list(FINAL_DOCS))
def test_refused_by_key_file_outcome(bot, h, run_async, use_net, outcome):
    use_net(h.FakeNet(doc=h.key_doc(**FINAL_DOCS[outcome])))
    _, status, body = _send_flow(bot, run_async, h)
    assert (status, body["outcome"]) == (200, outcome)


@pytest.mark.parametrize("outcome", list(FINAL_DOCS))
def test_refused_by_key_file_line(bot, h, run_async, use_net, outcome):
    use_net(h.FakeNet(doc=h.key_doc(**FINAL_DOCS[outcome])))
    _, _, body = _send_flow(bot, run_async, h)
    assert body["line"] == wording.OUTCOME_LINES[outcome]


@pytest.mark.parametrize("outcome", list(FINAL_DOCS))
def test_refused_by_key_file_is_final(bot, h, run_async, use_net, outcome):
    use_net(h.FakeNet(doc=h.key_doc(**FINAL_DOCS[outcome])))
    _, _, body = _send_flow(bot, run_async, h)
    assert (body["retry"], body["reference"]) == (False, None)


@pytest.mark.parametrize("outcome", list(FINAL_DOCS))
def test_refused_by_key_file_posts_nothing(bot, h, run_async, use_net, outcome):
    fake = use_net(h.FakeNet(doc=h.key_doc(**FINAL_DOCS[outcome])))
    _send_flow(bot, run_async, h)
    assert fake.posts == []


@pytest.mark.parametrize("outcome", list(FINAL_DOCS))
def test_final_outcome_repeat_says_the_same_and_fetches_nothing(
        bot, h, run_async, use_net, outcome):
    fake = use_net(h.FakeNet(doc=h.key_doc(**FINAL_DOCS[outcome])))

    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, "")
        seen = len(fake.requests)
        _, body = await api.send(draft, "")
        return body, len(fake.requests) - seen
    body, more = h.serve(bot, run_async, go)
    assert (body["outcome"], body["repeat"], more) == (outcome, True, 0)


def _daily(bot, h, run_async, sends):
    """*sends* Mini App sends in a row, each on its own draft; returns the
    last answer."""
    async def go(api):
        last = None
        for _ in range(sends):
            draft = await api.draft_id()
            last = await api.send(draft, "")
        return last
    return h.serve(bot, run_async, go)


def test_fifth_send_of_the_day_goes(bot, h, run_async, net):
    _, body = _daily(bot, h, run_async, 5)
    assert body["outcome"] == "sent"


def test_sixth_send_of_the_day_is_daily_cap(bot, h, run_async, net):
    _, body = _daily(bot, h, run_async, 6)
    assert body["outcome"] == "daily_cap"


def test_daily_cap_line(bot, h, run_async, net):
    _, body = _daily(bot, h, run_async, 6)
    assert body["line"] == wording.OUTCOME_LINES["daily_cap"]


def test_daily_cap_is_final(bot, h, run_async, net):
    _, body = _daily(bot, h, run_async, 6)
    assert body["retry"] is False


def test_daily_cap_posts_nothing_more(bot, h, run_async, net):
    _daily(bot, h, run_async, 6)
    assert len(net.posts) == 5


def test_every_answer_carries_the_five_keys(bot, h, run_async, net):
    _, _, body = _send_flow(bot, run_async, h)
    assert set(body) == {"outcome", "reference", "line", "retry", "repeat"}


def test_internal_failure_maps_to_failed_or_a_known_outcome(bot, h, run_async, use_net):
    """Error guessing: the transport blows up with a non-network error.
    Whatever the outcome, its line follows the contract (failed ->
    SEND_FAILED_TEXT, else the shared wording)."""
    use_net(h.FakeNet(sentry_error=RuntimeError("boom inside")))
    _, status, body = _send_flow(bot, run_async, h)
    want = (report_flow.SEND_FAILED_TEXT if body["outcome"] == "failed"
            else report_flow.outcome_line(body["outcome"], body["reference"]))
    assert (status, body["line"]) == (200, want)


# ---- repeat after a final outcome --------------------------------------------------------

def test_repeat_after_sent_returns_same_reference_no_second_post(bot, h, run_async, net):
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        _, first = await api.send(draft, "")
        _, again = await api.send(draft, "")
        return first, again
    first, again = h.serve(bot, run_async, go)
    assert (again["reference"], again["repeat"], len(net.posts)) == (
        first["reference"], True, 1)


def test_repeat_after_sent_says_sent(bot, h, run_async, net):
    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, "")
        return await api.send(draft, "")
    status, body = h.serve(bot, run_async, go)
    assert (status, body["outcome"]) == (200, "sent")


def test_repeat_after_sent_ignores_a_different_note(bot, h, run_async, net):
    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, "")
        return await api.send(draft, "a different note")
    _, body = h.serve(bot, run_async, go)
    assert (body["repeat"], len(net.posts)) == (True, 1)


def test_repeat_after_sent_does_not_count_again(bot, h, run_async, net):
    """A repeat makes no request at all (not even the key file)."""
    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, "")
        seen = len(net.requests)
        await api.send(draft, "")
        return len(net.requests) - seen
    assert h.serve(bot, run_async, go) == 0


# ---- refused sends change nothing -----------------------------------------------------------

def test_refused_sends_write_no_file(bot, h, run_async, net):
    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, "trailing ")
        await api.send("unknown", "")
        await api.post("/api/report/send", json_body={"draft": 5})
    h.serve(bot, run_async, go)
    assert not h.sends_file().exists()


# ---- logs ----------------------------------------------------------------------------------

SECRET = "ZEBRA-secret-note-7731"


def _logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_logs_carry_no_note_text(bot, h, run_async, net, caplog):
    caplog.set_level(logging.DEBUG)
    _send_flow(bot, run_async, h, note=SECRET)
    assert SECRET not in _logged(caplog)


def test_logs_carry_no_note_text_when_refused(bot, h, run_async, net, caplog):
    caplog.set_level(logging.DEBUG)
    _send_flow(bot, run_async, h, note=SECRET + "  ")
    assert SECRET not in _logged(caplog)


def test_logs_carry_no_note_text_on_try_later(bot, h, run_async, use_net, caplog):
    use_net(h.FakeNet(sentry_status=503))
    caplog.set_level(logging.DEBUG)
    _send_flow(bot, run_async, h, note=SECRET)
    assert SECRET not in _logged(caplog)


def test_logs_carry_no_report_fields(bot, h, run_async, net, caplog):
    caplog.set_level(logging.DEBUG)
    h.record_bug(fn="zebrafunctionname")
    _send_flow(bot, run_async, h, note="", record=False)
    assert "zebrafunctionname" not in _logged(caplog)


def test_logs_name_the_outcome(bot, h, run_async, net, caplog):
    caplog.set_level(logging.DEBUG)
    _send_flow(bot, run_async, h)
    assert "problem report send: sent" in _logged(caplog)


def test_logs_name_the_refusal(bot, h, run_async, net, caplog):
    caplog.set_level(logging.DEBUG)
    h.serve(bot, run_async, lambda api: api.send("nope", ""))
    assert "miniapp: report send refused (410) - draft gone" in _logged(caplog)
