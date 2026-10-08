"""The Mini App's problem report drafts (roadmap 8.112 follow-up):
aipager.bot.report_drafts, white box. The store (bound to its owner,
24 h, at most 3, never evicting one being sent), the note check, and the
send (claimed before any await, shielded, final outcomes recorded).
Every send goes through a fake network (report_flow.SEND_TRANSPORT)."""

from __future__ import annotations

import asyncio
import threading

import pytest

from aipager.bot import report_drafts, report_flow
from aipager.report import builder, wording
from tests.report_ui_harness import OWNER, FakeNet, attachment, pin_version

OTHER = 4242


@pytest.fixture(autouse=True)
def _pinned_version(monkeypatch):
    pin_version(monkeypatch)


@pytest.fixture
def clock(monkeypatch):
    """report_flow's one clock seam, moved by hand."""
    now = [1_000_000.0]
    monkeypatch.setattr(report_flow, "_mono", lambda: now[0])
    return now


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    return fake


class GatedNet(FakeNet):
    """A fake network whose envelope POST waits for :attr:`gate`."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.gate = threading.Event()
        self.posting = threading.Event()

    def __call__(self, request):
        if request.method == "POST":
            self.posting.set()
            self.gate.wait(10)
        return super().__call__(request)


def _bot(mk_bot):
    return mk_bot()


def _open(run_async, bot, user_id=OWNER):
    draft = run_async(report_drafts.open_draft(bot, user_id))
    assert draft is not None
    return draft


# ---- the store -------------------------------------------------------------------

def test_open_draft_keeps_the_builders_manual_report(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    assert draft.report["trigger"] == "manual"
    assert draft.preview == builder.render_preview(draft.report).encode()
    assert bot._report_drafts == {draft.id: draft}
    assert draft.created == clock[0] and draft.state == "open"
    assert len(draft.id) >= 20


def test_mini_app_drafts_never_touch_the_telegram_cards(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    bot._report_cards = {}
    for _ in range(4):
        _open(run_async, bot)
    assert bot._report_cards == {}


def test_open_draft_keeps_nothing_when_the_build_fails(mk_bot, run_async, monkeypatch):
    bot = _bot(mk_bot)

    def boom(*_a):
        raise OSError("disk")
    monkeypatch.setattr(report_flow, "_build", boom)
    assert run_async(report_drafts.open_draft(bot, OWNER)) is None
    assert getattr(bot, "_report_drafts", {}) == {}


def test_lookup_finds_the_owners_draft(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    assert report_drafts.lookup(bot, OWNER, draft.id) is draft


def test_lookup_refuses_another_users_draft(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    assert report_drafts.lookup(bot, OTHER, draft.id) is None
    assert report_drafts.lookup(bot, str(OWNER), draft.id) is None


def test_lookup_refuses_an_unknown_id(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    _open(run_async, bot)
    assert report_drafts.lookup(bot, OWNER, "nope") is None
    assert report_drafts.lookup(bot, OWNER, None) is None


def test_draft_expires_after_24h(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    clock[0] += report_drafts.DRAFT_TTL
    assert report_drafts.lookup(bot, OWNER, draft.id) is draft
    clock[0] += 1
    assert report_drafts.lookup(bot, OWNER, draft.id) is None
    assert bot._report_drafts == {}


def test_a_fourth_draft_evicts_the_oldest(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    drafts = []
    for _ in range(report_drafts.MAX_DRAFTS + 1):
        drafts.append(_open(run_async, bot))
        clock[0] += 1
    assert report_drafts.MAX_DRAFTS == 3
    assert list(bot._report_drafts) == [d.id for d in drafts[1:]]
    assert report_drafts.lookup(bot, OWNER, drafts[0].id) is None


def test_eviction_never_drops_a_sending_draft(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    first = _open(run_async, bot)
    first.state = "sending"
    clock[0] += 1
    later = [_open(run_async, bot) for _ in range(report_drafts.MAX_DRAFTS)]
    assert first.id in bot._report_drafts
    assert later[0].id not in bot._report_drafts
    assert len(bot._report_drafts) == report_drafts.MAX_DRAFTS


def test_expiry_never_drops_a_sending_draft(mk_bot, run_async, clock):
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    draft.state = "sending"
    clock[0] += report_drafts.DRAFT_TTL + 10
    assert report_drafts.lookup(bot, OWNER, draft.id) is draft


def test_areas_use_the_offer_notices_words(mk_bot, run_async):
    report = {"errors": [
        {"frames": [{"file": "aipager/bot/animation.py", "line": 1, "fn": "f"}]},
        {"frames": [{"file": "aipager/bot/flood.py", "line": 1, "fn": "f"}]},
        {"frames": []},
    ]}
    assert report_drafts.areas(report) == ["the busy card", "flood", "aipager"]
    assert report_drafts.areas({"errors": []}) == []


# ---- the note ----------------------------------------------------------------------

def test_a_normalized_note_is_added_last(mk_bot, run_async, clock):
    draft = _open(run_async, _bot(mk_bot))
    status, note, candidate = report_drafts.check_note(draft, "It froze.")
    assert (status, note) == ("ok", "It froze.")
    assert list(candidate)[-1] == "note" and candidate["note"] == "It froze."
    assert "note" not in draft.report


def test_an_empty_note_adds_no_key(mk_bot, run_async, clock):
    draft = _open(run_async, _bot(mk_bot))
    status, note, candidate = report_drafts.check_note(draft, "")
    assert (status, note) == ("ok", "")
    assert candidate == draft.report and "note" not in candidate


@pytest.mark.parametrize("typed, normalized", [
    ("It froze. ", "It froze."),
    (" ", ""),
    ("​", ""),
    ("a\tb", "a b"),
    ("x" * 501, "x" * 500),
])
def test_a_note_not_in_normalized_form_is_handed_back(mk_bot, run_async, clock, typed,
                                                       normalized):
    draft = _open(run_async, _bot(mk_bot))
    assert report_drafts.check_note(draft, typed) == ("changed", normalized, None)


def test_a_candidate_that_fails_its_checks_is_refused(mk_bot, run_async, clock, monkeypatch):
    from aipager.report import send
    draft = _open(run_async, _bot(mk_bot))
    monkeypatch.setattr(send, "checked_preview", lambda _r: None)
    assert report_drafts.check_note(draft, "fine") == ("refused", "fine", None)


# ---- sending -------------------------------------------------------------------------

def test_a_sent_draft_posts_exactly_the_candidate(mk_bot, run_async, clock, net):
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    _status, _note, candidate = report_drafts.check_note(draft, "It froze.")
    reply = run_async(report_drafts.send_draft(bot, draft, candidate))
    assert reply.outcome == "sent" and reply.retry is False
    assert reply.line == wording.OUTCOME_LINES["sent"].format(reference=reply.reference)
    (post,) = net.posts
    assert attachment(post.content) == builder.render_preview(candidate).encode()


def test_final_outcome_drops_the_report(mk_bot, run_async, clock, net):
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    reply = run_async(report_drafts.send_draft(bot, draft, draft.report))
    assert draft.state == "done" and draft.report is None
    assert draft.result == ("sent", reply.reference)


@pytest.mark.parametrize("status, outcome", [(429, "rate_limited"), (400, "rejected"),
                                             (503, "offline")])
def test_try_later_reopens_the_draft(mk_bot, run_async, clock, monkeypatch, status, outcome):
    fake = FakeNet(sentry_status=status)
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    report = draft.report
    reply = run_async(report_drafts.send_draft(bot, draft, report))
    assert (reply.outcome, reply.retry, reply.line) == (outcome, True, wording.TRY_LATER)
    assert draft.state == "open" and draft.report is report and draft.result is None


def test_a_send_that_breaks_inside_aipager_is_final(mk_bot, run_async, clock, monkeypatch):
    from aipager.report import send

    def boom(*_a, **_k):
        raise RuntimeError("bug")
    monkeypatch.setattr(send, "send", boom)
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)
    reply = run_async(report_drafts.send_draft(bot, draft, draft.report))
    assert (reply.outcome, reply.reference, reply.retry) == ("failed", None, False)
    assert reply.line == report_flow.SEND_FAILED_TEXT
    assert draft.state == "done" and draft.result == ("failed", None)


def test_claim_happens_before_any_await(mk_bot, run_async, clock, monkeypatch):
    """The first step of a send marks the draft, before the send task
    has even started: a second request in between sees it sending."""
    net = GatedNet()
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", net.transport)
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)

    async def go():
        task = asyncio.ensure_future(report_drafts.send_draft(bot, draft, draft.report))
        await asyncio.sleep(0)
        seen = draft.state
        net.gate.set()
        await asyncio.wait_for(task, 10)
        return seen
    assert run_async(go()) == "sending"


def test_cancelled_request_still_records_the_outcome(mk_bot, run_async, clock, monkeypatch):
    net = GatedNet()
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", net.transport)
    bot = _bot(mk_bot)
    draft = _open(run_async, bot)

    async def go():
        request = asyncio.ensure_future(report_drafts.send_draft(bot, draft, draft.report))
        for _ in range(1000):
            if net.posting.is_set():
                break
            await asyncio.sleep(0.01)
        request.cancel()
        await asyncio.sleep(0)
        net.gate.set()
        try:
            await asyncio.wait_for(draft.send_task, 10)
        except asyncio.CancelledError:
            pass     # the unshielded mutant: the send itself was cancelled
        assert request.cancelled()
    run_async(go())
    assert draft.state == "done" and draft.result[0] == "sent"


def test_reply_for_maps_every_outcome_to_the_shared_wording():
    from aipager.report import send
    for outcome in send.OUTCOMES:
        reply = report_drafts.reply_for(outcome, "ap1-0123456789ab")
        assert reply.line == report_flow.outcome_line(outcome, "ap1-0123456789ab")
        assert reply.retry is (outcome in wording.RETRYABLE)
    failed = report_drafts.reply_for("failed", None)
    assert (failed.line, failed.retry) == (report_flow.SEND_FAILED_TEXT, False)
