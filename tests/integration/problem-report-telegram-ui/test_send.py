"""SC-9..13: Send posts exactly the previewed bytes, once, off the event
loop; every outcome has its line; a lost card sends nothing; Cancel
drops the report (design.md Success criteria 9-13; spec.md "Send with a
fake network (MockTransport) -> the exact previewed bytes went out and
the card shows the reference, Cancel, double tap single-flight, daily
cap and kill-switch lines").

Every send goes to :class:`FakeNet` through ``report_flow.SEND_TRANSPORT``.

Methods: equivalence partitioning over the sender's outcomes (sent,
offline, rate limited, rejected, disabled, too old, daily cap);
boundary: the daily cap (5 sends, the 6th refused); error guessing: a
double tap while the first send is in flight, a tap after a restart,
the store changing between preview and Send, a network error."""

from __future__ import annotations

import asyncio
import json
import re
import threading

import httpx
import pytest

from aipager.report import send, wording


def _open(bot, d, h):
    return h.open_card(bot, d)


def _block_bytes(bot, cid, mid, h) -> bytes:
    return h.preview_block(bot.tg.text_of(cid, mid)).encode("utf-8")


# ---- SC-9: exactly the previewed bytes -------------------------------------------

def test_send_posts_one_envelope(bot, drive, h, run_async, net):
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert len(net.posts) == 1


def test_sent_attachment_equals_previewed_bytes(bot, drive, h, run_async, net):
    h.record_exc()
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        shown = _block_bytes(bot, cid, mid, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return shown
    shown = run_async(go())
    assert h.attachment_of(net.posts[0]) == shown


def test_sent_bytes_are_previewed_bytes_after_store_change(bot, drive, h, run_async, net):
    """A report built again at tap time would carry the new error."""
    h.record_exc(KeyError)
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        shown = _block_bytes(bot, cid, mid, h)
        h.record_exc(ValueError)
        h.record_exc(TypeError)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return shown
    shown = run_async(go())
    assert h.attachment_of(net.posts[0]) == shown


def test_oversized_sent_bytes_equal_the_document(bot, drive, h, run_async, net):
    h.record_many_exc(10)
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        h.record_exc(UnicodeError)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert h.attachment_of(net.posts[0]) == bot.tg.documents[0]["bytes"]


def test_oversized_report_is_really_a_document(bot, drive, h, run_async, net):
    """The partition above is real: ten typed errors do not fit a card."""
    h.record_many_exc(10)
    run_async(_open(bot, drive(bot), h))
    assert len(bot.tg.documents) == 1


@pytest.mark.parametrize("how", ["log_error_line", "crash", "hook_cap"])
def test_send_report_whose_error_has_no_type_posts(bot, drive, h, run_async, net, how):
    """Error guessing: the store records ERROR log lines, unclean exits
    and hook cap hits by call site, with no exception type. Those are the
    records the automatic offer is about, so their report must send."""
    from aipager.report import store
    kind, trigger = {"log_error_line": ("log_error", "log_error"),
                     "crash": ("crash", "crash"),
                     "hook_cap": ("hook_cap", "hook_cap")}[how]
    store.record_site(kind, file="aipager/state.py", line=10, fn="save",
                      where="daemon", trigger=trigger, tier="bug")
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert len(net.posts) == 1


def test_send_report_whose_error_has_no_type_is_not_try_later(bot, drive, h, run_async, net):
    """A send that cannot work must not tell the owner to try again."""
    h.record_bug()
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return bot.tg.text_of(cid, mid)
    assert wording.TRY_LATER not in run_async(go())


def test_card_shows_the_sent_line(bot, drive, h, run_async, net):
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return bot.tg.text_of(cid, mid)
    text = run_async(go())
    assert re.search(r"Sent\. Reference \S+ \(quote it if you open a GitHub issue\)\.", text)


_REFERENCE = re.compile(r"Sent\. Reference (\S+) \(quote it if you open a GitHub issue\)\.")


def _sent_reference(bot, d, h, run_async, net):
    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return bot.tg.text_of(cid, mid)
    found = _REFERENCE.search(run_async(go()))
    assert found, "no Sent line on the card"
    (post,) = net.posts
    header, items = h.parse_envelope(post.content)
    event = next(json.loads(p) for i, p in items if i.get("type") == "event")
    return found.group(1), header, event


@pytest.mark.parametrize("stored", ["typed", "call_site"])
def test_sent_reference_has_the_ap1_form(bot, drive, h, run_async, net, stored):
    """The reference rule (coordinator decision, iteration 2): a report
    that carries a keyed error is quoted by that error's checked ap1-
    fingerprint, the same value Sentry can search as the report_fp tag;
    both a typed (exception) and a call-site (log line) error count."""
    if stored == "typed":
        h.record_exc()
    else:
        h.record_bug()
    ref, _header, event = _sent_reference(bot, drive(bot), h, run_async, net)
    assert ref.startswith("ap1-")
    assert event["tags"]["report_fp"] == ref


def test_sent_reference_without_errors_is_the_event_id(bot, drive, h, run_async, net):
    """A report with no errors has no fingerprint to quote: the reference
    is the full 32-hex Sentry event id."""
    ref, header, _event = _sent_reference(bot, drive(bot), h, run_async, net)
    assert re.fullmatch(r"[0-9a-f]{32}", ref)
    assert header["event_id"] == ref


def test_sent_card_has_no_send_button(bot, drive, h, run_async, net):
    """Sent is final: the keyboard goes."""
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return bot.tg.buttons_of(cid, mid)
    assert all(data != "_:rp:send" for _, data in run_async(go()))


# ---- SC-10: single flight, off the loop -----------------------------------------

def _gated(h):
    gate = threading.Event()
    fake = h.FakeNet(gate=gate)
    return fake, gate


async def _wait(pred, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end and not pred():
        await asyncio.sleep(0.01)
    return pred()


def _double_tap(bot, d, h, fake, gate):
    async def go():
        cid, mid = await _open(bot, d, h)
        first = asyncio.ensure_future(d.tap("_:rp:send", chat=cid, message_id=mid))
        await _wait(lambda: len(fake.posts) >= 1)
        try:
            await d.tap("_:rp:send", chat=cid, message_id=mid, wait_outcome=False)
        finally:
            gate.set()
        await first
        return cid, mid
    return go()


def test_double_tap_in_flight_sends_once(bot, drive, h, run_async, use_net):
    fake, gate = _gated(h)
    use_net(fake)
    run_async(_double_tap(bot, drive(bot), h, fake, gate))
    assert len(fake.posts) == 1


def test_double_tap_in_flight_says_already_sending(bot, drive, h, run_async, use_net):
    fake, gate = _gated(h)
    use_net(fake)
    run_async(_double_tap(bot, drive(bot), h, fake, gate))
    assert "Already sending." in bot.tg.toast_texts()


def test_simultaneous_taps_send_once(bot, drive, h, run_async, net):
    """Two taps delivered in the same loop turn."""
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await asyncio.gather(d.tap("_:rp:send", chat=cid, message_id=mid),
                             d.tap("_:rp:send", chat=cid, message_id=mid))
    run_async(go())
    assert len(net.posts) == 1


def test_tap_after_sent_sends_nothing_more(bot, drive, h, run_async, net):
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert len(net.posts) == 1


def test_send_runs_in_worker_thread(bot, drive, h, run_async, net):
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return threading.current_thread()
    loop_thread = run_async(go())
    assert net.threads and all(t is not loop_thread for t in net.threads)


def test_loop_stays_responsive_during_send(bot, drive, h, run_async, use_net):
    """While Sentry holds the send open, the next update (here /help in
    the owner DM, delivered after the Send tap's handler returned, as
    Telegram delivers updates one at a time) is answered."""
    fake, gate = _gated(h)
    use_net(fake)
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        try:
            await asyncio.wait_for(d.tap("_:rp:send", chat=cid, message_id=mid,
                                         wait_outcome=False), 2.0)
            await _wait(lambda: len(fake.posts) >= 1)
            before = len(bot.tg.sent(h.OWNER))
            await asyncio.wait_for(d.command("/help"), 2.0)
            answered = len(bot.tg.sent(h.OWNER)) - before
            still_pending = not gate.is_set() and h.SENDING in bot.tg.text_of(cid, mid)
        finally:
            gate.set()
        await h.wait_for_outcome(bot, cid, mid)
        return answered, still_pending
    assert run_async(go()) == (1, True)


# ---- SC-11: outcome lines ------------------------------------------------------------

def _send_with(bot, d, h, run_async):
    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return cid, mid
    return run_async(go())


@pytest.mark.parametrize("doc_changes, outcome", [
    ({"enabled": False}, "disabled"),
    ({"dsn": ""}, "disabled"),
    ({"min_version": "99.0"}, "too_old"),
], ids=["kill-switch", "empty-key", "too-old"])
def test_refused_send_shows_its_line(bot, drive, h, run_async, use_net, doc_changes, outcome):
    use_net(h.FakeNet(doc=h.key_doc(**doc_changes)))
    cid, mid = _send_with(bot, drive(bot), h, run_async)
    assert wording.OUTCOME_LINES[outcome] in bot.tg.text_of(cid, mid).replace("&#x27;", "'")


@pytest.mark.parametrize("doc_changes", [{"enabled": False}, {"min_version": "99.0"}],
                         ids=["kill-switch", "too-old"])
def test_refused_send_posts_nothing(bot, drive, h, run_async, use_net, doc_changes):
    fake = use_net(h.FakeNet(doc=h.key_doc(**doc_changes)))
    _send_with(bot, drive(bot), h, run_async)
    assert fake.posts == []


@pytest.mark.parametrize("fake_kw", [
    {"sentry_status": 503}, {"sentry_status": 429}, {"sentry_status": 400},
    {"sentry_error": httpx.ConnectError("down")}, {"sentry_error": httpx.ReadTimeout("slow")},
], ids=["503", "429", "400", "connect-error", "timeout"])
def test_try_later_line(bot, drive, h, run_async, use_net, fake_kw):
    use_net(h.FakeNet(**fake_kw))
    cid, mid = _send_with(bot, drive(bot), h, run_async)
    assert wording.TRY_LATER in bot.tg.text_of(cid, mid)


@pytest.mark.parametrize("fake_kw", [{"sentry_status": 503},
                                     {"sentry_error": httpx.ConnectError("down")}],
                         ids=["503", "connect-error"])
def test_offline_keeps_the_send_button(bot, drive, h, run_async, use_net, fake_kw):
    use_net(h.FakeNet(**fake_kw))
    cid, mid = _send_with(bot, drive(bot), h, run_async)
    assert ("📤 Send", "_:rp:send") in bot.tg.buttons_of(cid, mid)


def test_retry_after_offline_sends_the_same_bytes(bot, drive, h, run_async, use_net):
    down = use_net(h.FakeNet(sentry_status=503))
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        up = use_net(h.FakeNet())
        await d.tap("_:rp:send", chat=cid, message_id=mid)
        return up
    up = run_async(go())
    assert h.attachment_of(up.posts[0]) == h.attachment_of(down.posts[0])


@pytest.mark.parametrize("doc_changes", [{"enabled": False}, {"min_version": "99.0"}],
                         ids=["kill-switch", "too-old"])
def test_final_outcome_removes_send_button(bot, drive, h, run_async, use_net, doc_changes):
    use_net(h.FakeNet(doc=h.key_doc(**doc_changes)))
    cid, mid = _send_with(bot, drive(bot), h, run_async)
    assert all(data != "_:rp:send" for _, data in bot.tg.buttons_of(cid, mid))


def test_daily_cap_line(bot, drive, h, run_async, use_net):
    used = h.FakeNet()
    for _ in range(send.SENDS_PER_DAY):
        assert send.send(_a_report(), transport=used.transport).outcome == send.SENT
    use_net(h.FakeNet())
    cid, mid = _send_with(bot, drive(bot), h, run_async)
    assert wording.OUTCOME_LINES["daily_cap"] in bot.tg.text_of(cid, mid)


def test_daily_cap_touches_no_network(bot, drive, h, run_async, use_net):
    used = h.FakeNet()
    for _ in range(send.SENDS_PER_DAY):
        send.send(_a_report(), transport=used.transport)
    fresh = use_net(h.FakeNet())
    _send_with(bot, drive(bot), h, run_async)
    assert fresh.posts == []


def test_fifth_send_of_the_day_still_goes(bot, drive, h, run_async, use_net):
    """Boundary: 4 used, the card's send is the 5th and goes out."""
    used = h.FakeNet()
    for _ in range(send.SENDS_PER_DAY - 1):
        send.send(_a_report(), transport=used.transport)
    fresh = use_net(h.FakeNet())
    _send_with(bot, drive(bot), h, run_async)
    assert len(fresh.posts) == 1


def _a_report():
    from aipager.report import builder
    return builder.build_report("manual")


# ---- SC-12: a lost card sends nothing ----------------------------------------------

def test_send_without_kept_report_sends_nothing(make_bot, drive, h, run_async, net):
    """A restart: a fresh daemon receives the tap on the old card."""
    old = make_bot("personal")
    cid, mid = run_async(h.open_card(old, drive(old)))
    new = make_bot("personal")
    run_async(drive(new).tap("_:rp:send", chat=cid, message_id=mid))
    assert net.posts == []


def test_send_without_kept_report_says_preview_again(make_bot, drive, h, run_async, net):
    old = make_bot("personal")
    cid, mid = run_async(h.open_card(old, drive(old)))
    new = make_bot("personal")
    run_async(drive(new).tap("_:rp:send", chat=cid, message_id=mid))
    assert ("This preview is no longer open, so nothing was sent. "
            "Open Report a problem again to see a fresh one.") in new.tg.text_of(cid, mid)


def test_send_without_kept_report_removes_buttons(make_bot, drive, h, run_async, net):
    old = make_bot("personal")
    cid, mid = run_async(h.open_card(old, drive(old)))
    new = make_bot("personal")
    run_async(drive(new).tap("_:rp:send", chat=cid, message_id=mid))
    assert new.tg.buttons_of(cid, mid) == []


def test_send_on_unknown_message_builds_nothing(bot, drive, h, run_async, net):
    """Error guessing: a forged send on a message that was never a card."""
    run_async(drive(bot).tap("_:rp:send", message_id=4711))
    assert (net.posts, bot.tg.cards()) == ([], [])


# ---- SC-13: Cancel ---------------------------------------------------------------------

def _cancel(bot, d, h, run_async, then_send=False):
    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:cancel", chat=cid, message_id=mid)
        if then_send:
            await d.tap("_:rp:send", chat=cid, message_id=mid)
        return cid, mid
    return run_async(go())


def test_cancel_says_nothing_was_sent(bot, drive, h, run_async, net):
    cid, mid = _cancel(bot, drive(bot), h, run_async)
    assert "nothing was sent" in bot.tg.text_of(cid, mid).lower()


def test_cancel_posts_nothing(bot, drive, h, run_async, net):
    _cancel(bot, drive(bot), h, run_async)
    assert net.requests == []


def test_cancel_removes_the_buttons(bot, drive, h, run_async, net):
    cid, mid = _cancel(bot, drive(bot), h, run_async)
    assert all(data != "_:rp:send" for _, data in bot.tg.buttons_of(cid, mid))


def test_send_after_cancel_sends_nothing(bot, drive, h, run_async, net):
    """The kept report is dropped: a stale Send button cannot revive it."""
    _cancel(bot, drive(bot), h, run_async, then_send=True)
    assert net.posts == []


def test_cancel_from_capture_drops_report(bot, drive, h, run_async, net):
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:note", chat=cid, message_id=mid)
        await d.tap("_:rp:cancel", chat=cid, message_id=mid)
        await d.text("a note after cancel")
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert net.posts == []


def test_cancel_keeps_the_report_json_out_of_any_send(bot, drive, h, run_async, net):
    """A cancelled card and a later fresh card: only the fresh one's
    bytes ever go out."""
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:cancel", chat=cid, message_id=mid)
        h.record_exc()
        cid2, mid2 = await _open(bot, d, h)
        shown = _block_bytes(bot, cid2, mid2, h)
        await d.tap("_:rp:send", chat=cid2, message_id=mid2)
        return shown
    shown = run_async(go())
    assert [h.attachment_of(p) for p in net.posts] == [shown]


def test_sent_report_is_valid_json(bot, drive, h, run_async, net):
    d = drive(bot)

    async def go():
        cid, mid = await _open(bot, d, h)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert json.loads(h.attachment_of(net.posts[0]))["schema"] == "aipager-report/1"
