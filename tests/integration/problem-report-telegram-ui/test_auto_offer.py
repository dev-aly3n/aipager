"""SC-14..18 and SC-22: the automatic offer, end to end through the
monitor tick with real conditions (design.md Success criteria 14-18, 22;
spec.md "the automatic offer honours every policy gate end to end with
real conditions (busy session, owner inactive, group target, developer
install, env off, settings Off, within 1 h of restart, within 48 h of
first install), answers bound to their offer (stale callback ignored),
two declines -> off line").

Driven only through ``report_offer.tick(bot, now=, mono=)`` (the monitor's
per-tick entry point), Telegram updates, the store and the markers.
:class:`OfferWorld` opens every gate; each test flips exactly one.

Methods: equivalence partitioning (one class per gate), boundary-value
analysis (49 h vs 47 h since install, 61 vs 30 min since start, idle
just under vs over 2 min), error guessing (a notice Telegram refuses or
skips, a muted DM, a 429, a warning regime, minimal mode, a forged or
stale or garbled answer)."""

from __future__ import annotations

import json
import time

import pytest
from telegram.error import BadRequest

from aipager import config
from aipager.bot import rich_message
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import BudgetRateLimiter
from aipager.report import policy, store
from aipager.state import Status

DAY = 86400
OFF_LINE = "Automatic offers are now off. To turn them back on: /settings, then Problem reports."
DECLINED = "OK. aipager won't ask about this again on this version."


def _ts_of(notice) -> int:
    for row in notice["markup"].inline_keyboard:
        for b in row:
            if b.callback_data.startswith("_:rp:op:"):
                return int(b.callback_data.rsplit(":", 1)[1])
    raise AssertionError("no Preview button")


async def _offer(w, **ticks):
    await w.owner_acts()
    return await w.run_ticks(**ticks)


def _notices(w, run_async, **ticks):
    return run_async(_offer(w, **ticks))


@pytest.fixture
def limiter():
    lim = BudgetRateLimiter(signal_path=config.FLOOD_BACKOFF_FILE)
    rich_message.set_rate_limiter(lim)
    yield lim
    lim.reset()


@pytest.fixture
def due_spy(monkeypatch):
    calls = []
    real = policy.due_offer

    def _spy(*a, **kw):
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(policy, "due_offer", _spy)
    return calls


# ---- the open baseline ---------------------------------------------------------------

def test_every_gate_open_offers_once(offer_world, run_async):
    w = offer_world()
    assert len(_notices(w, run_async)) == 1


def test_notice_goes_to_owner_dm(offer_world, h, run_async):
    w = offer_world()
    assert _notices(w, run_async)[0]["chat_id"] == h.OWNER


def test_notice_wording(offer_world, run_async):
    w = offer_world()
    text = _notices(w, run_async)[0]["text"]
    assert text.startswith("aipager hit an internal error 2 times (")


def test_notice_tail(offer_world, run_async):
    w = offer_world()
    assert ("A report helps get it fixed. It holds only versions and code locations, "
            "never your chats, prompts, names or paths.") in _notices(w, run_async)[0]["text"]


def test_notice_buttons(offer_world, run_async):
    w = offer_world()
    n = _notices(w, run_async)[0]
    labels = [b.text for row in n["markup"].inline_keyboard for b in row]
    assert labels == ["Preview report", "Not now", "Don't ask for this"]


def test_notice_callbacks_carry_the_offer_ts(offer_world, run_async):
    w = offer_world()
    n = _notices(w, run_async)[0]
    ts = _ts_of(n)
    data = [b.callback_data for row in n["markup"].inline_keyboard for b in row]
    assert data == [f"_:rp:op:{ts}", f"_:rp:on:{ts}", f"_:rp:od:{ts}"]


def test_notice_callbacks_fit_64_bytes(offer_world, run_async):
    w = offer_world()
    n = _notices(w, run_async)[0]
    assert all(len(b.callback_data.encode()) <= 64 for row in n["markup"].inline_keyboard
               for b in row)


def test_notice_ts_is_the_persisted_last_offer(offer_world, run_async):
    w = offer_world()
    n = _notices(w, run_async)[0]
    assert _ts_of(n) == store.policy_state().last_offer_ts


def test_notice_names_a_typed_error(offer_world, h, run_async):
    w = offer_world(bug=False)
    for back in (3000, 1200):
        try:
            raise TypeError("x")
        except TypeError as exc:
            store.record_exception(exc, where="daemon", trigger="log_exception",
                                   tier="bug", now=w.now - back)
    assert "(TypeError in " in _notices(w, run_async)[0]["text"]


def test_crash_notice_wording(offer_world, run_async):
    w = offer_world(bug=False)
    store.record_site("crash", file="aipager/cli/daemon.py", line=0, fn="_cmd_start",
                      where="daemon", trigger="crash", tier="bug", now=w.now - 600)
    assert _notices(w, run_async)[0]["text"].startswith("aipager stopped unexpectedly ")


def test_hook_cap_notice_wording(offer_world, run_async):
    """A relayed record needs a day's span (the policy); seen 2 days ago
    and 10 minutes ago."""
    w = offer_world(bug=False)
    for back in (2 * DAY, 600):
        store.record_site("hook_cap", file="aipager/dtach/notify_hook.py", line=0, fn="main",
                          where="hook", trigger="hook_cap", tier="bug", now=w.now - back)
    assert "The aipager hook hit its memory cap" in _notices(w, run_async)[0]["text"]


def test_notice_mentions_other_errors(offer_world, h, run_async):
    w = offer_world()
    h.record_bug(fn="load", now=w.now - 600)
    assert " Also 1 other errors." in _notices(w, run_async)[0]["text"] or \
        " Also 1 other error." in _notices(w, run_async)[0]["text"]


def test_one_offer_across_many_ticks(offer_world, run_async):
    """Rare by rule: an hour of ticks after the offer adds no second one."""
    w = offer_world()
    run_async(_offer(w))
    more = run_async(w.run_ticks(seconds=3600, step=60, offset=600))
    assert len(more) == 1


# ---- SC-16: lowest priority ----------------------------------------------------------

def test_notice_is_ornament_skip(offer_world, run_async):
    w = offer_world()
    n = _notices(w, run_async)[0]
    assert n["kwargs"].get("rate_limit_args") == {"kind": "skip", "class": "ornament"}


# ---- SC-14: each gate alone stops the offer ---------------------------------------------

@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE])
def test_gate_busy_session(offer_world, h, run_async, status):
    w = offer_world()
    sess = h.add_session(w.bot.registry, "jim")

    def _flip(i):
        sess.status = status
    assert _notices(w, run_async, busy_during=_flip) == []


def test_gate_idle_under_two_minutes(offer_world, h, run_async):
    """Boundary: busy until 60 s before the last tick."""
    w = offer_world()
    sess = h.add_session(w.bot.registry, "jim")

    def _flip(i):
        sess.status = Status.BUSY if i < 15 else Status.IDLE   # ticks every 20 s, 19 ticks
    assert _notices(w, run_async, busy_during=_flip) == []


def test_idle_over_two_minutes_after_busy_offers(offer_world, h, run_async):
    """Positive control for the idle gate: busy, then three idle minutes."""
    w = offer_world()
    sess = h.add_session(w.bot.registry, "jim")

    def _flip(i):
        sess.status = Status.BUSY if i < 9 else Status.IDLE
    assert len(_notices(w, run_async, busy_during=_flip, seconds=600)) == 1


def test_gate_owner_inactive_over_30_min(offer_world, run_async):
    """The owner's stamp is the real clock; the first tick is 31 min later."""
    w = offer_world()
    lag = int(time.time()) - w.now
    assert _notices(w, run_async, offset=lag + 31 * 60) == []


def test_gate_owner_never_seen(offer_world, run_async):
    w = offer_world()
    assert run_async(w.run_ticks()) == []


def test_gate_owner_activity_only_in_group(offer_world, h, run_async):
    w = offer_world("scope")

    async def go():
        await w.owner_acts(chat=h.GROUP)
        return await w.run_ticks()
    assert run_async(go()) == []


def test_owner_activity_in_scope_dm_offers(offer_world, h, run_async):
    """Positive control for the group case: the same bot, the DM."""
    w = offer_world("scope")
    assert len(_notices(w, run_async)) == 1


def test_gate_someone_else_active_in_owner_dm(offer_world, h, run_async):
    """Only the owner's own activity counts."""
    w = offer_world()

    async def go():
        await w.owner_acts(user=h.STRANGER)
        return await w.run_ticks()
    assert run_async(go()) == []


@pytest.mark.parametrize("mode", ["scope_no_dm", "scope_two_owners"])
def test_gate_no_owner_dm(offer_world, h, run_async, mode):
    w = offer_world(mode)

    async def go():
        await w.owner_acts(chat=h.GROUP)
        await w.owner_acts()
        return await w.run_ticks()
    run_async(go())
    assert [e for e in w.bot.tg.sent() if "_:rp:op:" in str(e["markup"])] == []


@pytest.mark.parametrize("origin", ["editable", "local"])
def test_gate_developer_install(offer_world, released_install, run_async, origin):
    w = offer_world()
    released_install(origin)
    assert _notices(w, run_async) == []


def test_gate_env_off(offer_world, run_async, monkeypatch):
    monkeypatch.setenv("AIPAGER_REPORT_PROMPTS", "0")
    w = offer_world()
    assert _notices(w, run_async) == []


def test_env_other_than_zero_does_not_silence(offer_world, run_async, monkeypatch):
    monkeypatch.setenv("AIPAGER_REPORT_PROMPTS", "1")
    w = offer_world()
    assert len(_notices(w, run_async)) == 1


def test_gate_settings_off(offer_world, run_async):
    from aipager import preferences
    preferences.set_problem_reports("off")
    w = offer_world()
    assert _notices(w, run_async) == []


def test_gate_within_one_hour_of_start(offer_world, run_async):
    w = offer_world(started_ago=30 * 60)
    assert _notices(w, run_async) == []


def test_start_61_minutes_ago_offers(offer_world, run_async):
    w = offer_world(started_ago=61 * 60)
    assert len(_notices(w, run_async)) == 1


def test_gate_start_unknown(offer_world, run_async):
    w = offer_world(started_ago=None)
    assert _notices(w, run_async) == []


def test_gate_within_48_hours_of_install(offer_world, run_async):
    w = offer_world(install_age=47 * 3600)
    assert _notices(w, run_async) == []


def test_install_49_hours_ago_offers(offer_world, run_async):
    w = offer_world(install_age=49 * 3600)
    assert len(_notices(w, run_async)) == 1


def test_gate_no_install_marker(offer_world, run_async):
    w = offer_world(install_age=None)
    assert _notices(w, run_async) == []


def test_gate_muted_dm(offer_world, h, run_async):
    w = offer_world()
    MUTE.mute(h.OWNER, 600.0)
    assert _notices(w, run_async) == []


def _retrying(limiter, h):
    limiter.note_retry_after(h.OWNER, 60)   # a small 429 (a larger one is a ban)


def test_retry_fixture_bars_the_chat(h, limiter):
    _retrying(limiter, h)
    rows = [c for c in limiter.snapshot().get("chats", []) if c.get("chat_id") == h.OWNER]
    assert rows and rows[0]["retry_until_in"] > 0


def test_gate_retry_after_429(offer_world, h, run_async, limiter):
    w = offer_world()
    _retrying(limiter, h)
    assert _notices(w, run_async, seconds=40) == []


def _warn(limiter, h):
    limiter.restore([{"chat_id": h.OWNER, "warned_until": time.time() + 3600}])


def test_warning_fixture_is_a_warning_regime(h, limiter):
    _warn(limiter, h)
    assert limiter.warning_remaining(h.OWNER) > 0


def test_gate_warning_regime(offer_world, h, run_async, limiter):
    w = offer_world()
    _warn(limiter, h)
    assert _notices(w, run_async) == []


def _minimal(limiter, h):
    limiter.restore([{"chat_id": h.OWNER, "rate": config.FLOOD_MIN_RATE,
                      "rate_earned_at": time.time(), "backoff": 1.0,
                      "last_429_at": None, "ban_stamps": [], "hourly_minimal": True}])


def test_minimal_fixture_is_minimal_mode(h, limiter):
    _minimal(limiter, h)
    assert limiter.minimal_mode(h.OWNER)


def test_gate_minimal_mode(offer_world, h, run_async, limiter):
    w = offer_world()
    _minimal(limiter, h)
    assert _notices(w, run_async) == []


def test_healthy_limiter_offers(offer_world, run_async, limiter):
    """Positive control for the limiter gates."""
    w = offer_world()
    assert len(_notices(w, run_async)) == 1


def test_gate_auto_off(offer_world, run_async):
    w = offer_world()
    store.save_policy(policy.State(auto_off=True, declines_in_row=2))
    assert _notices(w, run_async) == []


def test_gate_not_a_bug(offer_world, h, run_async):
    w = offer_world(bug=False)
    h.record_bug(now=w.now - 600, tier="anomaly")
    assert _notices(w, run_async) == []


def test_gate_bug_seen_once(offer_world, h, run_async):
    w = offer_world(bug=False)
    h.record_bug(now=w.now - 600, occasions=1)
    assert _notices(w, run_async) == []


# ---- SC-15: persisted before sending, restored when not shown -------------------------

def test_offer_saved_before_notice_sent(offer_world, h, run_async):
    w = offer_world()
    seen = {}

    def _on_send(chat_id, args, kwargs):
        if "_:rp:op:" in str(kwargs.get("reply_markup")):
            seen["policy"] = json.loads(h.reports_file().read_text()).get("policy")
    w.bot.tg.on_send = _on_send
    _notices(w, run_async)
    assert (seen.get("policy") or {}).get("pending") == [w.fp]


@pytest.mark.parametrize("how", ["raised", "none"])
def test_failed_notice_restores_policy(offer_world, run_async, how):
    w = offer_world()
    if how == "raised":
        w.bot.tg.fail_send = BadRequest("Chat not found")
    else:
        w.bot.tg.send_returns_none = True
    run_async(_offer(w, seconds=120))
    assert store.policy_state() == policy.State()


@pytest.mark.parametrize("how", ["raised", "none"])
def test_failed_notice_restores_policy_on_disk(offer_world, h, run_async, how):
    w = offer_world()
    if how == "raised":
        w.bot.tg.fail_send = BadRequest("Chat not found")
    else:
        w.bot.tg.send_returns_none = True
    run_async(_offer(w, seconds=120))
    on_disk = (json.loads(h.reports_file().read_text()).get("policy")
               if h.reports_file().exists() else None) or {}
    assert (on_disk.get("pending") or [], on_disk.get("last_offer_ts")) == ([], None)


def test_failed_notice_is_offered_again_later(offer_world, run_async):
    """The slot was not burned: once Telegram is back, the offer comes."""
    w = offer_world()
    w.bot.tg.fail_send = BadRequest("Chat not found")
    run_async(_offer(w, seconds=120))
    w.bot.tg.fail_send = None
    assert len(run_async(w.run_ticks(offset=300))) == 1


def test_muted_dm_never_reaches_due_offer(offer_world, h, run_async, due_spy):
    w = offer_world()
    MUTE.mute(h.OWNER, 600.0)
    _notices(w, run_async)
    assert due_spy == []


def test_open_dm_reaches_due_offer(offer_world, run_async, due_spy):
    """Positive control for the spy above."""
    w = offer_world()
    _notices(w, run_async)
    assert due_spy != []


def test_muted_dm_leaves_policy_untouched(offer_world, h, run_async):
    w = offer_world()
    MUTE.mute(h.OWNER, 600.0)
    _notices(w, run_async)
    assert store.policy_state() == policy.State()


def test_settle_result_saved(offer_world, h, run_async):
    """An offer unanswered for a day is a decline, and that is persisted
    by the check even though no new offer is made (3-day gap)."""
    w = offer_world()
    store.save_policy(policy.State(last_offer_ts=w.now - 2 * DAY, pending=("ap1-00000000dead",),
                                   offered={"ap1-00000000dead": {"version": "0.4.4",
                                                                 "ts": w.now - 2 * DAY}}))
    _notices(w, run_async)
    on_disk = json.loads(h.reports_file().read_text())["policy"]
    assert (on_disk["pending"], on_disk["declines_in_row"]) == ([], 1)


# ---- SC-22: the check never creates install.json ----------------------------------------

def test_tick_creates_no_install_marker(offer_world, h, run_async):
    w = offer_world(install_age=None)
    _notices(w, run_async)
    assert not h.install_file().exists()


# ---- SC-17: answers bound to their offer ------------------------------------------------

def _answer(w, h, run_async, verb, *, ts=None, user=None, chat=None):
    n = w.notices()[0]
    ts = _ts_of(n) if ts is None else ts
    run_async(w.drive.tap(f"_:rp:{verb}:{ts}", user=user or h.OWNER,
                          chat=chat or n["chat_id"], message_id=n["message_id"]))
    return n


def test_preview_answer_opens_an_auto_card(offer_world, h, run_async):
    w = offer_world()
    _notices(w, run_async)
    _answer(w, h, run_async, "op")
    card = w.bot.tg.cards(h.OWNER)[-1]
    assert json.loads(h.preview_block(card["text"]))["trigger"] == "auto"


def test_preview_carries_offered_errors_only(offer_world, h, run_async):
    w = offer_world()
    h.record_bug(fn="once_only", now=w.now - 600, occasions=1)   # stored, not eligible
    _notices(w, run_async)
    _answer(w, h, run_async, "op")
    card = w.bot.tg.cards(h.OWNER)[-1]
    assert [e["fingerprint"] for e in json.loads(h.preview_block(card["text"]))["errors"]] \
        == [w.fp]


def test_preview_answer_edits_the_notice(offer_world, h, run_async):
    w = offer_world()
    _notices(w, run_async)
    n = _answer(w, h, run_async, "op")
    assert "Opened the report preview below." in w.bot.tg.text_of(n["chat_id"], n["message_id"])


def test_preview_answer_clears_pending(offer_world, h, run_async):
    w = offer_world()
    _notices(w, run_async)
    _answer(w, h, run_async, "op")
    assert store.policy_state().pending == ()


def test_preview_answer_is_not_a_decline(offer_world, h, run_async):
    w = offer_world()
    _notices(w, run_async)
    _answer(w, h, run_async, "op")
    assert store.policy_state().declines_in_row == 0


def test_auto_preview_send_posts(offer_world, h, run_async, net):
    """The offer's own report must be sendable (its record is an ERROR
    log line: a call site with no exception type)."""
    w = offer_world()
    _notices(w, run_async)
    _answer(w, h, run_async, "op")
    card = w.bot.tg.cards(h.OWNER)[-1]
    run_async(w.drive.tap("_:rp:send", chat=card["chat_id"], message_id=card["message_id"]))
    assert len(net.posts) == 1


def test_auto_preview_send_of_typed_error_posts_offered_bytes(offer_world, h, run_async, net):
    w = offer_world(bug=False)
    for back in (3000, 1200):
        try:
            raise TypeError("x")
        except TypeError as exc:
            store.record_exception(exc, where="daemon", trigger="log_exception",
                                   tier="bug", now=w.now - back)
    _notices(w, run_async)
    _answer(w, h, run_async, "op")
    card = w.bot.tg.cards(h.OWNER)[-1]
    run_async(w.drive.tap("_:rp:send", chat=card["chat_id"], message_id=card["message_id"]))
    assert h.attachment_of(net.posts[0]) == h.preview_block(card["text"]).encode("utf-8")


@pytest.mark.parametrize("verb", ["on", "od"])
def test_decline_edits_the_notice(offer_world, h, run_async, verb):
    w = offer_world()
    _notices(w, run_async)
    n = _answer(w, h, run_async, verb)
    assert DECLINED in w.bot.tg.text_of(n["chat_id"], n["message_id"]).replace("&#x27;", "'")


@pytest.mark.parametrize("verb", ["on", "od"])
def test_decline_counts_one(offer_world, h, run_async, verb):
    w = offer_world()
    _notices(w, run_async)
    _answer(w, h, run_async, verb)
    assert store.policy_state().declines_in_row == 1


@pytest.mark.parametrize("verb", ["on", "od"])
def test_first_decline_does_not_say_off(offer_world, h, run_async, verb):
    w = offer_world()
    _notices(w, run_async)
    n = _answer(w, h, run_async, verb)
    assert OFF_LINE not in w.bot.tg.text_of(n["chat_id"], n["message_id"])


def test_decline_opens_no_card(offer_world, h, run_async):
    w = offer_world()
    _notices(w, run_async)
    _answer(w, h, run_async, "on")
    assert w.bot.tg.cards() == []


def _stale_world(offer_world, run_async):
    w = offer_world()
    _notices(w, run_async)
    return w, store.policy_state()


@pytest.mark.parametrize("ts_shift", [-1, 1])
def test_stale_tap_changes_no_policy(offer_world, h, run_async, ts_shift):
    w, before = _stale_world(offer_world, run_async)
    _answer(w, h, run_async, "od", ts=_ts_of(w.notices()[0]) + ts_shift)
    assert store.policy_state() == before


def test_stale_tap_says_offer_no_longer_open(offer_world, h, run_async):
    w, _ = _stale_world(offer_world, run_async)
    _answer(w, h, run_async, "op", ts=_ts_of(w.notices()[0]) - 1)
    assert "This offer is no longer open." in w.bot.tg.toast_texts()


def test_stale_preview_opens_no_card(offer_world, h, run_async):
    w, _ = _stale_world(offer_world, run_async)
    _answer(w, h, run_async, "op", ts=_ts_of(w.notices()[0]) - 1)
    assert w.bot.tg.cards() == []


@pytest.mark.parametrize("ts", ["abc", "", "-1", "1e9", "True"])
def test_garbled_ts_changes_no_policy(offer_world, h, run_async, ts):
    w, before = _stale_world(offer_world, run_async)
    _answer(w, h, run_async, "od", ts=ts)
    assert store.policy_state() == before


def test_answer_after_answer_is_stale(offer_world, h, run_async):
    """The second tap of the same button finds no pending offer."""
    w = offer_world()
    _notices(w, run_async)
    _answer(w, h, run_async, "op")
    _answer(w, h, run_async, "op")
    assert len(w.bot.tg.cards()) == 1


def test_foreign_tap_changes_no_policy(offer_world, h, run_async):
    w, before = _stale_world(offer_world, run_async)
    _answer(w, h, run_async, "od", user=h.STRANGER)
    assert store.policy_state() == before


def test_foreign_preview_tap_opens_no_card(offer_world, h, run_async):
    w, _ = _stale_world(offer_world, run_async)
    _answer(w, h, run_async, "op", user=h.STRANGER)
    assert w.bot.tg.cards() == []


def test_owner_tap_outside_dm_changes_no_policy(offer_world, h, run_async):
    w = offer_world("scope")
    _notices(w, run_async)
    before = store.policy_state()
    _answer(w, h, run_async, "od", chat=h.GROUP)
    assert store.policy_state() == before


# ---- SC-18: two declines turn offers off, with one line ---------------------------------

def _second_decline(offer_world, h, run_async, verb="od"):
    w = offer_world()
    store.save_policy(policy.State(declines_in_row=1))
    _notices(w, run_async)
    n = _answer(w, h, run_async, verb)
    return w, n


@pytest.mark.parametrize("verb", ["on", "od"])
def test_two_declines_say_off(offer_world, h, run_async, verb):
    w, n = _second_decline(offer_world, h, run_async, verb)
    assert OFF_LINE in w.bot.tg.text_of(n["chat_id"], n["message_id"])


def test_two_declines_set_auto_off(offer_world, h, run_async):
    _second_decline(offer_world, h, run_async)
    assert store.policy_state().auto_off is True


def test_two_declines_say_off_once(offer_world, h, run_async):
    w, n = _second_decline(offer_world, h, run_async)
    texts = [e["text"] or "" for e in w.bot.tg.events]
    assert sum(t.count(OFF_LINE) for t in texts) == 1
