"""A repeat send after a final outcome, black box (entrypoints.md "Send
outcome facts": any other outcome is final, the draft's report is let go,
and a later send of the same draft id returns the same outcome and
reference with ``repeat: true`` and sends nothing).

Iteration 2 changed what the server keeps for a final draft (only the
outcome and the reference), so these pin that a repeat still answers from
that record alone.

Methods: equivalence partitioning over the repeat's body (same note, no
note key, a note that would be refused, a report key smuggled in) and the
network after the first send (healthy, down, refusing); boundary-value
analysis on the record's lifetime (just under 24 h vs past it) and the
draft bound (2 newer drafts kept vs 3); error guessing: many repeats in a
row, a repeat from another user."""

from __future__ import annotations

import httpx
import pytest

from aipager.bot import report_drafts, report_flow


def _sent_then(bot, h, run_async, *, between=None, repeat_body=None, repeats=1):
    """Draft, send (sent), run ``await between(api)``, then repeat.
    Returns (first answer, [(status, body) of each repeat])."""
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        _, first = await api.send(draft, "")
        if between is not None:
            await between(api)
        out = []
        for _ in range(repeats):
            if repeat_body is None:
                out.append(await api.send(draft, ""))
            else:
                out.append(await api.post("/api/report/send",
                                          json_body={"draft": draft, **repeat_body}))
        return first, out
    return h.serve(bot, run_async, go)


def test_repeat_returns_the_same_reference(bot, h, run_async, net):
    first, [(_, again)] = _sent_then(bot, h, run_async)
    assert again["reference"] == first["reference"]


def test_repeat_line_is_the_shared_wording_for_that_reference(bot, h, run_async, net):
    first, [(_, again)] = _sent_then(bot, h, run_async)
    assert again["line"] == report_flow.outcome_line("sent", first["reference"])


def test_repeat_is_not_retryable(bot, h, run_async, net):
    _, [(_, again)] = _sent_then(bot, h, run_async)
    assert again["retry"] is False


def test_repeat_carries_the_five_keys(bot, h, run_async, net):
    _, [(_, again)] = _sent_then(bot, h, run_async)
    assert set(again) == {"outcome", "reference", "line", "retry", "repeat"}


def test_many_repeats_all_answer_the_same_reference(bot, h, run_async, net):
    first, rest = _sent_then(bot, h, run_async, repeats=4)
    assert {b["reference"] for _, b in rest} == {first["reference"]}


def test_many_repeats_post_once(bot, h, run_async, net):
    _sent_then(bot, h, run_async, repeats=4)
    assert len(net.posts) == 1


@pytest.mark.parametrize("kw", [{"sentry_status": 503},
                                {"sentry_error": httpx.ConnectError("down")}],
                         ids=["503", "connect-error"])
def test_repeat_with_the_network_down_still_answers_sent(bot, h, run_async, use_net, net, kw):
    async def down(api):
        use_net(h.FakeNet(**kw))
    first, [(_, again)] = _sent_then(bot, h, run_async, between=down)
    assert (again["outcome"], again["reference"]) == ("sent", first["reference"])


def test_repeat_with_the_network_down_makes_no_request(bot, h, run_async, use_net, net):
    later = []

    async def down(api):
        later.append(use_net(h.FakeNet(sentry_status=503)))
    _sent_then(bot, h, run_async, between=down)
    assert later[0].requests == []


@pytest.mark.parametrize("body", [
    {},
    {"note": "a note that was never shown"},
    {"note": "trailing space "},
    {"note": "​"},
    {"report": {"v": 1, "smuggled": True}},
], ids=["no-note-key", "other-note", "unnormalized-note", "invisible-note", "report-key"])
def test_repeat_answers_the_record_whatever_the_body(bot, h, run_async, net, body):
    first, [(status, again)] = _sent_then(bot, h, run_async, repeat_body=body)
    assert (status, again["reference"], again["repeat"]) == (200, first["reference"], True)


def test_repeat_just_before_24h_still_answers(bot, h, run_async, net, clock):
    async def age(api):
        clock[0] += report_drafts.DRAFT_TTL - 1
    first, [(status, again)] = _sent_then(bot, h, run_async, between=age)
    assert (status, again["reference"]) == (200, first["reference"])


def test_repeat_past_24h_is_draft_gone_and_sends_nothing(bot, h, run_async, net, clock):
    async def age(api):
        clock[0] += report_drafts.DRAFT_TTL + 1
    _, [(status, body)] = _sent_then(bot, h, run_async, between=age)
    assert (status, body, len(net.posts)) == (410, {"error": "draft_gone"}, 1)


def test_repeat_with_newer_drafts_within_the_bound(bot, h, run_async, net, clock):
    async def newer(api):
        for _ in range(report_drafts.MAX_DRAFTS - 1):
            clock[0] += 1
            await api.draft_id()
    first, [(status, again)] = _sent_then(bot, h, run_async, between=newer)
    assert (status, again["reference"]) == (200, first["reference"])


def test_repeat_after_eviction_sends_nothing(bot, h, run_async, net, clock):
    async def newer(api):
        for _ in range(report_drafts.MAX_DRAFTS):
            clock[0] += 1
            await api.draft_id()
    _, [(status, _)] = _sent_then(bot, h, run_async, between=newer)
    assert (status, len(net.posts)) == (410, 1)


def test_repeat_from_a_stranger_is_refused(bot, h, run_async, net):
    h.record_exc()

    async def go(api):
        draft = await api.draft_id()
        await api.send(draft, "")
        return await api.send(draft, "", user=h.STRANGER)
    status, _ = h.serve(bot, run_async, go)
    assert status == 403


def test_repeat_after_daily_cap_says_daily_cap_and_sends_nothing(bot, h, run_async, net):
    async def go(api):
        for _ in range(5):
            await api.send(await api.draft_id(), "")
        capped = await api.draft_id()
        await api.send(capped, "")
        seen = len(net.requests)
        _, again = await api.send(capped, "")
        return again, len(net.requests) - seen
    again, more = h.serve(bot, run_async, go)
    assert (again["outcome"], again["repeat"], again["reference"], more) == (
        "daily_cap", True, None, 0)


def test_failed_send_repeat_says_failed_and_sends_nothing(bot, h, run_async, use_net):
    """A non-network error inside the send: whatever outcome it maps to, if
    it is final, a repeat answers it again with no request."""
    fake = use_net(h.FakeNet(sentry_error=RuntimeError("boom inside")))

    async def go(api):
        draft = await api.draft_id()
        _, first = await api.send(draft, "")
        seen = len(fake.requests)
        _, again = await api.send(draft, "")
        return first, again, len(fake.requests) - seen
    first, again, more = h.serve(bot, run_async, go)
    if first["retry"]:
        pytest.skip("this error maps to a retryable outcome: not a final record")
    assert (again["outcome"], again["repeat"], more) == (first["outcome"], True, 0)
