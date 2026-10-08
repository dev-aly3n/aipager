"""The automatic problem report offer (roadmap 8.112 step 3, bot/report_offer.py).

Every gate of ``policy.due_offer`` is fed live conditions here, one at a
time, and the 1c caller contract is pinned: settle persisted, the offer
saved before the notice, restored when the notice did not go out,
answers bound to their offer. No clock is patched: ``tick`` and
``maybe_offer`` take ``now`` and ``mono``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from aipager import config, install_source, preferences
from aipager.bot import report_flow, report_offer
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import FloodSkipped
from aipager.bot.transport import MUTED, SKIPPED
from aipager.report import markers, policy, store
from aipager.state import Status, TrackedSession
from tests.report_ui_harness import GROUP, OWNER, STRANGER, make_bot, tap, text_update, toasts

NOW = int(time.time())
MONO = 50_000.0
DAY = 86400


def _src(kind="pipx", origin="index"):
    return install_source.InstallSource(kind=kind, prefix="/p", python="/p/bin/python",
                                        origin=origin)


@pytest.fixture
def ready(mk_bot, monkeypatch):
    """A bot for which every gate holds: an offer is due."""
    monkeypatch.delenv("AIPAGER_REPORT_PROMPTS", raising=False)
    monkeypatch.setattr(install_source, "detect_install_source", lambda: _src())
    markers.first_start(NOW - 3 * DAY)
    markers.set_started_at(NOW - 2 * 3600)
    store.record_site("crash", file="aipager/cli/daemon.py", line=0, fn="_cmd_start",
                      where="daemon", trigger="crash", tier="bug", now=NOW - 600)
    bot, tg = make_bot(mk_bot)
    bot._report_last_busy_mono = MONO - 600
    bot._report_owner_seen_at = NOW - 60
    return bot, tg


def _offer(run_async, bot, now=NOW, mono=MONO):
    return run_async(report_offer.maybe_offer(bot, now=now, mono=mono))


def test_offer_when_every_gate_holds(ready, run_async):
    bot, tg = ready
    assert _offer(run_async, bot) is True
    (notice,) = tg.sent
    assert notice.chat_id == OWNER
    assert notice.text.startswith("aipager stopped unexpectedly once.")
    assert report_offer.NOTICE_TAIL in notice.text
    state = store.policy_state()
    assert state.last_offer_ts == NOW and len(state.pending) == 1
    data = [b.callback_data for row in notice.kw["reply_markup"].inline_keyboard for b in row]
    assert data == [f"_:rp:op:{NOW}", f"_:rp:on:{NOW}", f"_:rp:od:{NOW}"]


def test_notice_is_ornament_skip(ready, run_async):
    bot, tg = ready
    _offer(run_async, bot)
    assert tg.sent[0].kw["rate_limit_args"] == {"kind": "skip", "class": "ornament"}


def _busy_session(bot):
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    bot.registry._sessions[sess.name] = sess


@pytest.mark.parametrize("gate", [
    "busy_session", "owner_inactive", "owner_never_seen", "no_owner_dm", "developer_editable",
    "developer_local", "env_off", "settings_off", "restart_quiet", "no_start",
    "first_install_quiet", "no_install_marker", "auto_off", "gap", "pending"])
def test_gate(ready, run_async, monkeypatch, gate):
    bot, tg = ready
    mono = MONO
    if gate == "busy_session":
        _busy_session(bot)
        run_async(report_offer.tick(bot, now=NOW, mono=MONO - 1))
        bot._report_offer_checked_mono = None
    elif gate == "owner_inactive":
        bot._report_owner_seen_at = NOW - policy.OWNER_ACTIVE_WINDOW - 1
    elif gate == "owner_never_seen":
        bot._report_owner_seen_at = None
    elif gate == "no_owner_dm":
        monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")
    elif gate == "developer_editable":
        monkeypatch.setattr(install_source, "detect_install_source",
                            lambda: _src(kind="editable"))
    elif gate == "developer_local":
        monkeypatch.setattr(install_source, "detect_install_source",
                            lambda: _src(origin="local"))
    elif gate == "env_off":
        monkeypatch.setenv("AIPAGER_REPORT_PROMPTS", "0")
    elif gate == "settings_off":
        preferences.set_problem_reports("off")
    elif gate == "restart_quiet":
        markers.set_started_at(NOW - policy.RESTART_QUIET + 60)
    elif gate == "no_start":
        markers.set_started_at(None)
    elif gate == "first_install_quiet":
        Path(config.REPORT_INSTALL_FILE).unlink()
        markers.first_start(NOW - policy.FIRST_INSTALL_QUIET + 60)
    elif gate == "no_install_marker":
        Path(config.REPORT_INSTALL_FILE).unlink()
    elif gate == "auto_off":
        store.save_policy(policy.State(auto_off=True))
    elif gate == "gap":
        store.save_policy(policy.State(last_offer_ts=NOW - policy.REPORT_OFFER_MIN_GAP + 60))
    elif gate == "pending":
        store.save_policy(policy.State(last_offer_ts=NOW - 100, pending=("x" * 16,)))
    assert _offer(run_async, bot, mono=mono) is False
    assert tg.sent == []


def test_idle_is_needed(ready, run_async):
    bot, tg = ready
    bot._report_last_busy_mono = MONO - policy.IDLE_BEFORE_OFFER + 5
    assert _offer(run_async, bot) is False
    assert _offer(run_async, bot, mono=MONO + 10) is True


def test_group_activity_does_not_count(ready, run_async):
    bot, tg = ready
    bot._report_owner_seen_at = None
    report_flow.on_update(bot, text_update("hi", user_id=OWNER, chat_id=GROUP))
    report_flow.on_update(bot, text_update("hi", user_id=STRANGER, chat_id=OWNER))
    assert bot._report_owner_seen_at is None
    assert _offer(run_async, bot) is False
    report_flow.on_update(bot, text_update("hi", user_id=OWNER, chat_id=OWNER))
    assert bot._report_owner_seen_at is not None
    assert _offer(run_async, bot, now=int(bot._report_owner_seen_at)) is True


@pytest.mark.parametrize("why", ["muted", "retry", "warning", "minimal"])
def test_unclear_dm_does_not_burn_slot(ready, run_async, monkeypatch, why):
    bot, tg = ready
    if why == "muted":
        MUTE.mute(OWNER, 60)
    else:
        limiter = MagicMock()
        limiter.warning_remaining.return_value = 30.0 if why == "warning" else 0.0
        limiter.minimal_mode.return_value = why == "minimal"
        limiter.snapshot.return_value = {"chats": [
            {"chat_id": OWNER, "retry_until_in": 12.0 if why == "retry" else 0.0,
             "waiters": 0}]}
        monkeypatch.setattr("aipager.bot.rich_message._rate_limiter", limiter)
    called = []
    monkeypatch.setattr(policy, "due_offer", lambda *a, **k: called.append(1) or ())
    assert _offer(run_async, bot) is False
    assert called == [] and tg.sent == []
    assert store.policy_state() == policy.State()


def test_offer_saved_before_notice_sent(ready, run_async):
    bot, tg = ready
    seen = []
    real = tg.send_message

    async def _check(chat_id, text, **kw):
        store.reset()   # forget memory: what is on disk counts
        seen.append(store.policy_state())
        return await real(chat_id, text, **kw)

    tg.send_message = _check
    assert _offer(run_async, bot) is True
    assert seen[0].last_offer_ts == NOW and seen[0].pending


@pytest.mark.parametrize("how", ["skipped", "muted", "raised", "none"])
def test_skipped_notice_restores_policy(ready, run_async, how):
    bot, tg = ready
    before = store.policy_state()
    if how == "skipped":
        tg.send_result = SKIPPED
    elif how == "muted":
        tg.send_result = MUTED
    elif how == "raised":
        tg.send_error = FloodSkipped("x")
    else:
        async def _none(*a, **k):
            return None
        tg.send_message = _none
    assert _offer(run_async, bot) is False
    store.reset()
    assert store.policy_state() == before


@pytest.mark.parametrize("error", [
    BadRequest("Chat not found"), Forbidden("bot was blocked by the user"), RetryAfter(30),
], ids=["bad_request", "forbidden", "retry_after"])
def test_refused_notice_restores_policy(ready, run_async, error):
    """Telegram refused it: never shown, so as if never offered."""
    bot, tg = ready
    before = store.policy_state()
    tg.send_error = error
    assert _offer(run_async, bot) is False
    store.reset()
    assert store.policy_state() == before


@pytest.mark.parametrize("error", [
    TimedOut("timed out"), NetworkError("connection reset"), RuntimeError("?"),
], ids=["timed_out", "network_error", "other"])
def test_notice_of_unknown_delivery_keeps_the_offer(ready, run_async, error):
    """A timeout can come after Telegram delivered the notice: the offer
    stays recorded (its buttons still answer, no second notice soon)."""
    bot, tg = ready
    tg.send_error = error
    assert _offer(run_async, bot) is False
    store.reset()
    state = store.policy_state()
    assert state.last_offer_ts == NOW and len(state.pending) == 1
    # Its answer is still bound to it.
    query = _answer(run_async, bot, "on", NOW)
    assert query.edit_message_text.await_args.args[0] == report_flow.OFFER_DECLINED_TEXT


@pytest.mark.parametrize("how", ["skipped", "refused"])
def test_restore_never_overwrites_a_newer_state(ready, run_async, how):
    """Compare-and-restore: a policy change landing while the notice was
    on its way (a /settings re-enable, say) is not undone."""
    bot, tg = ready
    newer = policy.State(declines_in_row=0, auto_off=False, last_offer_ts=NOW - 7 * DAY)

    async def _send(chat_id, text, **kw):
        store.save_policy(newer)
        if how == "skipped":
            return SKIPPED
        raise BadRequest("Chat not found")

    tg.send_message = _send
    assert _offer(run_async, bot) is False
    store.reset()
    assert store.policy_state() == newer


def test_settle_result_saved(ready, run_async, monkeypatch):
    bot, tg = ready
    store.save_policy(policy.State(last_offer_ts=NOW - policy.OFFER_ANSWER_WINDOW - 5,
                                   pending=("a" * 16,)))
    monkeypatch.setattr("aipager.config.CHAT_ID", "-100123")   # no offer either way
    _offer(run_async, bot)
    store.reset()
    state = store.policy_state()
    assert state.pending == () and state.declines_in_row == 1


def test_tick_creates_no_install_marker(mk_bot, run_async):
    bot, _tg = make_bot(mk_bot)
    run_async(report_offer.tick(bot, now=NOW, mono=MONO))
    run_async(report_offer.maybe_offer(bot, now=NOW, mono=MONO))
    assert not Path(config.REPORT_INSTALL_FILE).exists()


def test_first_tick_counts_as_busy_never_a_type_error(ready, run_async):
    bot, tg = ready
    bot._report_last_busy_mono = None
    bot._report_offer_checked_mono = None
    run_async(report_offer.tick(bot, now=NOW, mono=MONO))
    assert bot._report_last_busy_mono == MONO and tg.sent == []
    # Idle from the first tick on, not "idle forever".
    run_async(report_offer.tick(bot, now=NOW + 70, mono=MONO + 70))
    assert tg.sent == []
    run_async(report_offer.tick(bot, now=NOW + 200, mono=MONO + 200))
    assert len(tg.sent) == 1


def test_a_tick_without_a_check_starts_the_idle_clock(mk_bot, run_async):
    """The first tick starts "idle since" even when it runs no offer
    check, so idle time counts from the daemon's first tick."""
    bot, _tg = make_bot(mk_bot)
    bot._report_offer_checked_mono = MONO          # a check just ran
    bot._report_last_busy_mono = None
    run_async(report_offer.tick(bot, now=NOW, mono=MONO + 1))
    assert bot._report_last_busy_mono == MONO + 1
    run_async(report_offer.tick(bot, now=NOW + 2, mono=MONO + 3))
    assert bot._report_last_busy_mono == MONO + 1  # idle since then


def test_maybe_offer_before_any_tick_is_not_idle(ready, run_async):
    bot, tg = ready
    bot._report_last_busy_mono = None
    assert _offer(run_async, bot) is False
    assert bot._report_last_busy_mono == MONO


def test_tick_checks_at_most_every_interval(ready, run_async, monkeypatch):
    bot, _tg = ready
    calls = []

    async def _count(b, **k):
        calls.append(k["mono"])

    monkeypatch.setattr(report_offer, "maybe_offer", _count)
    for step in range(0, 130, 2):
        run_async(report_offer.tick(bot, now=NOW + step, mono=MONO + step))
    assert calls == [MONO, MONO + 60, MONO + 120]


def test_tick_never_raises(ready, run_async, monkeypatch):
    bot, _tg = ready

    async def _boom(*a, **k):
        raise RuntimeError("offer broke")

    monkeypatch.setattr(report_offer, "maybe_offer", _boom)
    monkeypatch.setattr(report_offer, "busy_now", lambda b: 1 / 0)
    run_async(report_offer.tick(bot, now=NOW, mono=MONO))


def test_install_source_failure_counts_as_developer(ready, run_async, monkeypatch):
    bot, tg = ready

    def _boom():
        raise OSError("x")

    monkeypatch.setattr(install_source, "detect_install_source", _boom)
    assert _offer(run_async, bot) is False and tg.sent == []


def test_monitor_tick_runs_both_even_if_one_fails(mk_bot, run_async, monkeypatch):
    bot, _tg = make_bot(mk_bot)
    ran = []

    async def _pinned():
        ran.append("pinned")
        raise RuntimeError("bar broke")

    async def _tick(b, **k):
        ran.append("offer")

    bot.pinned_tick = _pinned
    monkeypatch.setattr(report_offer, "tick", _tick)
    run_async(bot.monitor_tick())
    assert ran == ["pinned", "offer"]


# ---- busy_now --------------------------------------------------------------------

def test_busy_now(mk_bot, monkeypatch):
    bot, _tg = make_bot(mk_bot)
    assert report_offer.busy_now(bot) is False
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.IDLE)
    bot.registry._sessions[sess.name] = sess
    assert report_offer.busy_now(bot) is False
    sess.busy_card_owed = True
    assert report_offer.busy_now(bot) is True
    sess.busy_card_owed = False
    sess.status = Status.INTERACTIVE
    assert report_offer.busy_now(bot) is True
    sess.status = Status.IDLE
    bot._keyboard_owed = {OWNER: None}
    assert report_offer.busy_now(bot) is True
    bot._keyboard_owed = {}
    limiter = MagicMock()
    limiter.snapshot.return_value = {"chats": [{"chat_id": OWNER, "waiters": 2}]}
    monkeypatch.setattr("aipager.bot.rich_message._rate_limiter", limiter)
    assert report_offer.busy_now(bot) is True


# ---- the notice --------------------------------------------------------------------

def _entry(**kw):
    base = {"fingerprint": "f" * 16, "trigger": "log_exception", "type": "builtins.TypeError",
            "count": 3, "frames": [{"file": "aipager/bot/animation.py", "line": 1, "fn": "x"}]}
    base.update(kw)
    return base


def test_notice_text():
    assert report_offer.notice_text([_entry()]).startswith(
        "aipager hit an internal error 3 times (TypeError in the busy card).")
    assert "(KeyError in hook_receiver2)" in report_offer.notice_text([_entry(
        type="KeyError", frames=[{"file": "aipager/x/hook_receiver2.py"}])])
    assert report_offer.notice_text([_entry(trigger="hook_cap", count=1)]).startswith(
        "The aipager hook hit its memory cap once.")
    two = report_offer.notice_text([_entry(), _entry(), _entry()])
    assert "Also 2 other errors." in two
    assert "Also 1 other error." in report_offer.notice_text([_entry(), _entry()])
    assert report_offer.notice_text([_entry(frames=[])]).count("(TypeError in aipager)") == 1


# ---- answers -------------------------------------------------------------------------

def _answer(run_async, bot, verb, ts, **kw):
    update, query = tap(f"_:rp:{verb}:{ts}", **kw)

    async def _go():
        await bot._handle_callback(update, MagicMock())
        await asyncio.sleep(0)

    run_async(_go())
    return query


def test_preview_carries_offered_errors_only(ready, run_async):
    bot, tg = ready
    store.record_site("log_error", file="aipager/bot/notify.py", line=5, fn="x",
                      where="daemon", trigger="log_error", tier="anomaly", now=NOW - 100)
    _offer(run_async, bot)
    offered = store.policy_state().pending
    query = _answer(run_async, bot, "op", NOW)
    assert query.edit_message_text.await_args.args[0] == report_flow.OFFER_PREVIEW_TEXT
    (kept,) = bot._report_cards.values()
    assert kept.report["trigger"] == "auto"
    assert tuple(e["fingerprint"] for e in kept.report["errors"]) == offered
    state = store.policy_state()
    assert state.pending == () and state.declines_in_row == 0


def test_stale_tap_does_nothing(ready, run_async):
    bot, tg = ready
    _offer(run_async, bot)
    before = store.policy_state()
    for ts in (NOW - 1, "abc", ""):
        query = _answer(run_async, bot, "on", ts)
        assert toasts(query) == [report_flow.OFFER_STALE_TEXT]
        query.edit_message_reply_markup.assert_awaited()
    assert store.policy_state() == before


@pytest.mark.parametrize("ts", ["\u00b2", "\u0661\u0662", "\uff11"],
                         ids=["superscript", "arabic_indic", "fullwidth"])
def test_unicode_digit_ts_is_stale_not_a_crash(ready, run_async, ts):
    """``str.isdigit`` says yes to these, ``int()`` refuses or misreads
    them: only ASCII digits are an offer's ts."""
    bot, tg = ready
    _offer(run_async, bot)
    before = store.policy_state()
    query = _answer(run_async, bot, "on", ts)
    assert toasts(query) == [report_flow.OFFER_STALE_TEXT]
    assert store.policy_state() == before


def test_preview_notice_says_opened_only_after_the_card_went_out(ready, run_async):
    bot, tg = ready
    _offer(run_async, bot)
    order = []
    real_send = tg.send_message

    async def _send(chat_id, text, **kw):
        order.append("card")
        return await real_send(chat_id, text, **kw)

    tg.send_message = _send
    update, query = tap(f"_:rp:op:{NOW}")

    async def _edit(*a, **kw):
        order.append("notice")
    query.edit_message_text.side_effect = _edit
    run_async(bot._handle_callback(update, MagicMock()))
    assert order == ["card", "notice"]
    assert query.edit_message_text.await_args.args[0] == report_flow.OFFER_PREVIEW_TEXT


def test_preview_that_did_not_open_says_so_on_the_notice(ready, run_async):
    bot, tg = ready
    _offer(run_async, bot)
    tg.send_result = MUTED          # the card does not go out
    query = _answer(run_async, bot, "op", NOW)
    text = query.edit_message_text.await_args.args[0]
    assert text == report_flow.OFFER_NOT_OPENED_TEXT
    assert report_flow.OFFER_PREVIEW_TEXT not in text
    assert bot._report_cards == {}


def test_foreign_tap_does_nothing(ready, run_async):
    bot, tg = ready
    _offer(run_async, bot)
    before = store.policy_state()
    query = _answer(run_async, bot, "od", NOW, user_id=STRANGER, chat_id=STRANGER)
    assert toasts(query) == [report_flow.NOT_OWNER_TEXT]
    query = _answer(run_async, bot, "od", NOW, chat_id=GROUP)
    assert toasts(query) == [report_flow.NOT_OWNER_TEXT]
    assert store.policy_state() == before and bot._report_cards == {}


def test_two_declines_say_off(ready, run_async):
    bot, tg = ready
    _offer(run_async, bot)
    query = _answer(run_async, bot, "on", NOW)
    text = query.edit_message_text.await_args.args[0]
    assert text == report_flow.OFFER_DECLINED_TEXT
    first = store.policy_state()
    assert first.declines_in_row == 1 and first.auto_off is False
    # A second offer (as maybe_offer would have recorded it past the gap),
    # and a second decline in a row.
    fp = store.record_site("hook_cap", file="aipager/dtach/notify_hook.py", line=1,
                           fn="main", where="daemon", trigger="hook_cap", tier="bug")
    store.save_policy(policy.note_offer(first, (fp,), NOW, store._version()))
    query = _answer(run_async, bot, "od", NOW)
    text = query.edit_message_text.await_args.args[0]
    assert text == report_flow.OFFER_DECLINED_TEXT + " " + report_flow.OFFER_OFF_TEXT
    assert store.policy_state().auto_off is True
    assert report_flow.settings_row_state(bot, OWNER, OWNER) == "offers off"
