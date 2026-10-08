"""Problem reports, step 1c (roadmap 8.112): when an automatic offer may
be made. The operator asked for it to be rare ("I don't want it to appear
for the user too often", "at most once in 3-4 days", never "in the first 2
days after first install (not the updates)") and for tests "so we make
sure a future update doesn't break it". So:

- one contract test per rule (each gate, flipped alone, stops the offer);
- pin tests on every constant, whose failure names the design;
- a 90-day simulation through the real store, for every answer pattern.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import random

import pytest

from aipager import config
from aipager.report import policy, store
from aipager.report import schema as sc

DESIGN = "/home/aly/researches/15-issue-reporting/design.md section 4 (Frequency budget)"
DAY = 86400
T0 = 20729 * DAY                        # a fixed clock at a UTC midnight: day 0
VERSION = "0.8.0"


def _record(fp="ap1-0000000000a1", *, tier="bug", trigger="log_exception", where="daemon",
            occasions=2, first_day="2026-10-01", last_day="2026-10-01", versions=(VERSION,),
            last_ts=T0, span=3600) -> dict:
    return {"entry": {"fingerprint": fp, "tier": tier, "trigger": trigger, "where": where,
                      "first_day": first_day, "last_day": last_day,
                      "versions_seen": list(versions)},
            "occasions": occasions, "first_ts": last_ts - span, "last_ts": last_ts,
            "occasion_ts": last_ts}


def _open(now=T0 + 10 * DAY, **changes) -> policy.Conditions:
    """Every gate open: an offer is due if a bug is."""
    base = policy.Conditions(
        now=now, version=VERSION, first_start=now - 10 * DAY, daemon_started=now - 2 * 3600,
        idle_for=600, owner_active_ago=60, owner_dm=True, prompts_on=True, env_off=False,
        developer_install=False)
    return dataclasses.replace(base, **changes)


FRESH = policy.State()


# ---- pins: loosening a rule fails here, naming the design ---------------------------

def test_the_rules_are_pinned():
    pins = [
        (policy.REPORT_OFFER_MIN_GAP >= 3 * DAY, "at most one offer per 3 days"),
        (policy.FIRST_INSTALL_QUIET >= 48 * 3600, "no offer within 48 h of the first install"),
        (policy.RESTART_QUIET >= 3600, "no offer within 1 h of a daemon restart"),
        (policy.DECLINES_TO_OFF <= 2, "two declines in a row turn offers off"),
        (policy.OCCASIONS_TO_OFFER >= 2, "a bug must happen on 2 separate occasions"),
        (store.OCCASION_GAP >= 600, "occasions at least 10 minutes apart"),
        (policy.IDLE_BEFORE_OFFER >= 120, "only after 2 minutes of everything idle"),
        (policy.OWNER_ACTIVE_WINDOW <= 30 * 60, "the owner active within 30 minutes"),
        (policy.OFFER_ANSWER_WINDOW <= DAY, "an ignored offer is a decline after a day"),
        (policy.AT_ONCE_TRIGGERS <= {"crash", "hook_cap"}, "only a crash or a cap hit at once"),
        (policy.RELAYED_WHERE >= {"hook", "statusline", "cli"}, "relayed records need a day"),
        (policy.RELAYED_MIN_SPAN >= DAY, "relayed records must span a full day"),
        (policy.OFFERED_KEPT >= 100, "offered bugs are remembered (else re-offered)"),
    ]
    broken = [name for ok, name in pins if not ok]
    assert broken == [], f"loosened: {broken}. See {DESIGN}; the operator asked for these."


# ---- the open baseline offers, and each gate alone stops it --------------------------

def test_with_every_gate_open_a_bug_is_offered():
    assert policy.due_offer([_record()], FRESH, _open()) == ("ap1-0000000000a1",)


@pytest.mark.parametrize(("name", "conditions"), [
    ("settings off", {"prompts_on": False}),
    ("AIPAGER_REPORT_PROMPTS=0", {"env_off": True}),
    ("a developer install", {"developer_install": True}),
    ("a group or another chat", {"owner_dm": False}),
    ("busy", {"idle_for": policy.IDLE_BEFORE_OFFER - 1}),
    ("never idle", {"idle_for": 0}),
    ("the owner away", {"owner_active_ago": policy.OWNER_ACTIVE_WINDOW + 1}),
    ("the owner never seen", {"owner_active_ago": None}),
    ("just installed", {"first_start": T0 + 10 * DAY - policy.FIRST_INSTALL_QUIET + 1}),
    ("no install marker", {"first_start": None}),
    ("just restarted", {"daemon_started": T0 + 10 * DAY - policy.RESTART_QUIET + 1}),
    ("start unknown", {"daemon_started": None})])
def test_each_gate_alone_stops_the_offer(name, conditions):
    assert policy.due_offer([_record()], FRESH, _open(**conditions)) == (), name


def test_the_operator_s_numbers_hold_as_numbers():
    """The headline rules checked with literal times, so a loosened
    constant cannot agree with itself."""
    now = T0 + 10 * DAY
    assert policy.due_offer([_record()], FRESH, _open(first_start=now - 47 * 3600)) == ()
    assert policy.due_offer([_record()], FRESH, _open(daemon_started=now - 59 * 60)) == ()
    assert policy.due_offer([_record()], FRESH, _open(idle_for=119)) == ()
    assert policy.due_offer([_record()], FRESH, _open(owner_active_ago=31 * 60)) == ()
    state = policy.note_answer(_offered([_record()]), "send")
    two_days_later = _open(now=state.last_offer_ts + 2 * DAY + 23 * 3600)
    assert policy.due_offer([_record(fp="ap1-0000000000b2")], state, two_days_later) == ()


def test_the_gates_open_exactly_at_their_limits():
    now = T0 + 10 * DAY
    edge = _open(first_start=now - policy.FIRST_INSTALL_QUIET,
                 daemon_started=now - policy.RESTART_QUIET,
                 idle_for=policy.IDLE_BEFORE_OFFER,
                 owner_active_ago=policy.OWNER_ACTIVE_WINDOW)
    assert policy.due_offer([_record()], FRESH, edge) != ()


def test_a_new_version_alone_does_not_hold_back_an_offer():
    """The quiet runs from the install marker, which an update never
    rewrites (tests/test_problem_report_capture.py,
    test_the_first_install_time_is_written_once_and_kept)."""
    upgraded = _open(version="0.9.0")
    rec = _record(versions=("0.8.0", "0.9.0"))
    assert policy.due_offer([rec], FRESH, upgraded) == (rec["entry"]["fingerprint"],)


# ---- which records may be offered ----------------------------------------------------

@pytest.mark.parametrize("tier", ["anomaly", "env", "<invalid>"])
def test_only_bugs_are_offered(tier):
    assert policy.due_offer([_record(tier=tier)], FRESH, _open()) == ()


def test_a_bug_needs_two_occasions():
    assert policy.due_offer([_record(occasions=1)], FRESH, _open()) == ()
    assert policy.due_offer([_record(occasions=2)], FRESH, _open()) != ()


@pytest.mark.parametrize("trigger", ["crash", "hook_cap"])
def test_a_crash_or_a_cap_hit_counts_at_once(trigger):
    rec = _record(trigger=trigger, occasions=1)
    assert policy.due_offer([rec], FRESH, _open()) != ()


@pytest.mark.parametrize("where", ["hook", "statusline", "cli"])
def test_a_relayed_bug_needs_a_full_day(where):
    """Two forged datagrams either side of midnight are two UTC days but
    not a day: the record must span 24 h, first to last."""
    across_midnight = _record(where=where, occasions=5, first_day="2026-09-30",
                              last_day="2026-10-01", span=600)
    short = _record(where=where, occasions=5, span=policy.RELAYED_MIN_SPAN - 1)
    a_day = _record(where=where, occasions=2, span=policy.RELAYED_MIN_SPAN)
    assert policy.due_offer([across_midnight], FRESH, _open()) == ()
    assert policy.due_offer([short], FRESH, _open()) == ()
    assert policy.due_offer([a_day], FRESH, _open()) != ()
    assert policy.RELAYED_MIN_SPAN >= DAY, f"see {DESIGN} and section 7"


def test_a_relayed_cap_hit_or_cli_crash_still_needs_a_full_day():
    for where, trigger in (("hook", "hook_cap"), ("cli", "crash")):
        rec = _record(where=where, trigger=trigger, occasions=1, span=2)
        assert policy.due_offer([rec], FRESH, _open()) == ()
        assert policy.due_offer([_record(where=where, trigger=trigger, occasions=1,
                                         span=DAY)], FRESH, _open()) != ()


def test_pending_bugs_are_combined_into_one_offer_newest_first():
    recs = [_record(fp=f"ap1-00000000000{i}", last_ts=T0 + i) for i in range(1, 4)]
    assert policy.due_offer(recs, FRESH, _open()) == (
        "ap1-000000000003", "ap1-000000000002", "ap1-000000000001")


def test_one_offer_carries_at_most_a_report_s_worth():
    recs = [_record(fp=f"ap1-{i:012x}", last_ts=T0 + i) for i in range(25)]
    assert len(policy.due_offer(recs, FRESH, _open())) == policy.MAX_PER_OFFER


# ---- answers, the gap, versions -----------------------------------------------------

def _offered(recs, state=FRESH, now=T0 + 10 * DAY):
    fps = policy.due_offer(recs, state, _open(now=now))
    assert fps
    return policy.note_offer(state, fps, now, VERSION)


def test_no_second_offer_within_the_gap():
    state = policy.note_answer(_offered([_record()]), "send")
    other = _record(fp="ap1-0000000000b2")
    assert policy.due_offer([other], state, _open(now=T0 + 10 * DAY + policy.REPORT_OFFER_MIN_GAP - 1)) == ()
    assert policy.due_offer([other], state, _open(now=T0 + 10 * DAY + policy.REPORT_OFFER_MIN_GAP)) != ()


def test_an_offer_awaiting_its_answer_blocks_another(monkeypatch):
    """Offers never stack. (With the shipped constants the gap already
    covers it, since an unanswered offer settles as a decline after a day;
    a longer answer window must not let a second offer through.)"""
    monkeypatch.setattr(policy, "OFFER_ANSWER_WINDOW", policy.REPORT_OFFER_MIN_GAP + DAY)
    state = _offered([_record()])
    later = state.last_offer_ts + policy.REPORT_OFFER_MIN_GAP
    assert state.pending
    assert policy.due_offer([_record(fp="ap1-0000000000b2")], state, _open(now=later)) == ()


@pytest.mark.parametrize("answer", ["send", "not_now", "dont_ask"])
def test_a_bug_is_offered_once_per_version_whatever_the_answer(answer):
    rec = _record()
    state = policy.note_answer(_offered([rec]), answer)
    much_later = T0 + 40 * DAY
    assert policy.due_offer([rec], dataclasses.replace(
        state, declines_in_row=0, auto_off=False), _open(now=much_later)) == ()


def test_after_an_upgrade_a_bug_comes_back_only_if_it_recurred():
    rec = _record()
    state = policy.note_answer(_offered([rec]), "send")
    later = T0 + 20 * DAY
    old = _record(versions=("0.8.0",), last_ts=T0)
    again = _record(versions=("0.8.0", "0.9.0"), last_ts=later - 3600)
    assert policy.due_offer([old], state, _open(now=later, version="0.9.0")) == ()
    assert policy.due_offer([again], state, _open(now=later, version="0.9.0")) != ()


def test_seen_on_the_new_version_but_not_since_the_offer_is_not_offered():
    """Upgraded, saw the bug, went back, was offered it, upgraded again: it
    has not happened since the offer, so it is not offered again."""
    offer_ts = T0 + 15 * DAY
    state = policy.State(offered={"ap1-0000000000a1": {"version": "0.8.0", "ts": offer_ts}})
    stale = _record(versions=("0.8.0", "0.9.0"), last_ts=offer_ts - 100)
    fresh = _record(versions=("0.8.0", "0.9.0"), last_ts=offer_ts + 100)
    later = _open(now=T0 + 20 * DAY, version="0.9.0")
    assert policy.due_offer([stale], state, later) == ()
    assert policy.due_offer([fresh], state, later) == ("ap1-0000000000a1",)


def test_a_recurrence_on_an_older_version_does_not_bring_it_back():
    """Seen on 0.9.0, downgraded, offered on 0.8.0, recurred on 0.8.0,
    upgraded again: nothing happened ON 0.9.0 since the offer."""
    offer_ts = T0 + 15 * DAY
    state = policy.State(offered={"ap1-0000000000a1": {"version": "0.8.0", "ts": offer_ts}})
    rec = _record(versions=("0.9.0", "0.8.0"), last_ts=offer_ts + 100)
    assert policy.due_offer([rec], state, _open(now=T0 + 20 * DAY, version="0.9.0")) == ()


def test_a_last_offer_dated_in_the_future_never_locks_offers_out():
    """A clock that was wrong when the offer was made: it is dated now
    (the gap starts again), not trusted until 2096."""
    now = T0 + 10 * DAY
    future = policy.State(last_offer_ts=now + 3650 * DAY)
    settled = policy.settle(future, now)
    assert settled.last_offer_ts == now
    assert policy.due_offer([_record()], settled, _open(now=now)) == ()
    assert policy.due_offer([_record()], settled, _open(now=now + 3 * DAY)) != ()


def test_reenabling_forgets_the_declines_but_keeps_the_gap():
    state = policy.note_answer(_offered([_record()]), "not_now")
    state = policy.note_answer(_offered([_record(fp="ap1-0000000000b2")], state,
                                        now=T0 + 20 * DAY), "not_now")
    assert state.auto_off
    back = policy.reenable(state)
    assert not back.auto_off and back.declines_in_row == 0 and back.pending == ()
    other = _record(fp="ap1-0000000000c3")
    assert policy.due_offer([other], back, _open(now=T0 + 21 * DAY)) == ()   # the gap
    assert policy.due_offer([other], back, _open(now=T0 + 23 * DAY)) != ()


def test_a_float_clock_is_stored_as_whole_seconds():
    state = policy.note_offer(FRESH, ("ap1-0000000000a1",), T0 + 0.75, VERSION)
    assert state.last_offer_ts == T0 and state.offered["ap1-0000000000a1"]["ts"] == T0
    store.save_policy(state)
    store.reset()
    assert store.policy_state() == state and not store.policy_state().auto_off


def test_two_declines_in_a_row_turn_offers_off():
    state = policy.note_answer(_offered([_record()]), "not_now")
    assert state.declines_in_row == 1 and not state.auto_off
    state = policy.note_answer(_offered([_record(fp="ap1-0000000000b2")], state,
                                        now=T0 + 20 * DAY), "dont_ask")
    assert state.auto_off
    assert policy.due_offer([_record(fp="ap1-0000000000c3")], state,
                            _open(now=T0 + 90 * DAY)) == ()


def test_a_send_resets_the_declines():
    state = policy.note_answer(_offered([_record()]), "not_now")
    state = policy.note_answer(_offered([_record(fp="ap1-0000000000b2")], state,
                                        now=T0 + 20 * DAY), "send")
    assert state.declines_in_row == 0 and not state.auto_off


def test_an_ignored_offer_is_a_decline_after_a_day():
    state = _offered([_record()])
    sent_at = state.last_offer_ts
    assert policy.settle(state, sent_at + policy.OFFER_ANSWER_WINDOW - 1) == state
    settled = policy.settle(state, sent_at + policy.OFFER_ANSWER_WINDOW)
    assert settled.pending == () and settled.declines_in_row == 1


def test_an_answer_with_nothing_pending_or_unknown_changes_nothing():
    assert policy.note_answer(FRESH, "not_now") == FRESH
    state = _offered([_record()])
    assert policy.note_answer(state, "maybe") == state


def test_the_offered_memory_is_bounded():
    state = FRESH
    for i in range(policy.OFFERED_KEPT + 30):
        state = policy.note_offer(state, (f"ap1-{i:012x}",), T0 + i, VERSION)
    assert len(state.offered) == policy.OFFERED_KEPT
    assert f"ap1-{policy.OFFERED_KEPT + 29:012x}" in state.offered


# ---- the policy state in the store --------------------------------------------------

def test_the_policy_state_is_written_at_once_and_survives_a_hard_kill():
    """save_policy writes the file itself: a kill before the next debounced
    save (no clean stop) must not forget an offer."""
    # T0 is in the past: the store dates a future time now (tested below).
    state = policy.note_answer(_offered([_record()], now=T0), "not_now")
    store.save_policy(state)
    store.reset()                       # the process died; nothing else saved
    assert store.policy_state() == state


@pytest.mark.parametrize("content", [b"{not json", b"", b"[1]", b'"x"',
                                     b'{"version": 2, "errors": []}'])
def test_a_store_that_cannot_be_read_whole_keeps_offers_off(content):
    """Its policy is unknown: the defaults would undo two declines or the
    gap (and the next save would write them back)."""
    config.REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REPORTS_FILE.write_bytes(content)
    assert store.policy_state().auto_off is True


def test_an_oversized_store_keeps_offers_off():
    config.REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REPORTS_FILE.write_text(json.dumps({"version": 1, "pad": "x" * store.MAX_FILE_BYTES}))
    assert store.policy_state().auto_off is True


def test_no_store_at_all_starts_fresh():
    assert not config.REPORTS_FILE.exists()
    assert store.policy_state() == policy.State()


def test_a_store_from_before_the_policy_reads_as_the_defaults():
    config.REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REPORTS_FILE.write_text(json.dumps({"version": 1, "errors": [], "counters": {},
                                               "digest": {}, "exits": {"unclean": [],
                                                                       "last": "clean"}}))
    assert store.policy_state() == policy.State()


@pytest.mark.parametrize("policy_doc", [
    None, [], "x",
    {"last_offer_ts": None, "pending": [], "declines_in_row": 0, "auto_off": False},
    {"last_offer_ts": None, "pending": [], "declines_in_row": 0, "auto_off": False,
     "offered": {}, "note": "x"},
    {"last_offer_ts": None, "pending": ["ap1-0000000000a1"], "declines_in_row": 0,
     "auto_off": False, "offered": {}},
    {"last_offer_ts": T0, "pending": ["cnryuser"], "declines_in_row": 0, "auto_off": False,
     "offered": {}},
    {"last_offer_ts": T0, "pending": [f"ap1-{i:012x}" for i in range(11)], "declines_in_row": 0,
     "auto_off": False, "offered": {}},
    {"last_offer_ts": "x", "pending": [], "declines_in_row": 0, "auto_off": False, "offered": {}},
    {"last_offer_ts": 5, "pending": [], "declines_in_row": 0, "auto_off": False, "offered": {}},
    {"last_offer_ts": None, "pending": ["cnryuser"], "declines_in_row": 0, "auto_off": False,
     "offered": {}},
    {"last_offer_ts": None, "pending": [], "declines_in_row": -1, "auto_off": False,
     "offered": {}},
    {"last_offer_ts": None, "pending": [], "declines_in_row": True, "auto_off": False,
     "offered": {}},
    {"last_offer_ts": None, "pending": [], "declines_in_row": 0, "auto_off": 0, "offered": {}},
    {"last_offer_ts": None, "pending": [], "declines_in_row": 0, "auto_off": False,
     "offered": {"ap1-0000000000a1": {"version": "cnry user", "ts": T0}}},
    {"last_offer_ts": None, "pending": [], "declines_in_row": 0, "auto_off": False,
     "offered": {"cnryuser": {"version": VERSION, "ts": T0}}},
    {"last_offer_ts": None, "pending": [], "declines_in_row": 0, "auto_off": False,
     "offered": {"ap1-0000000000a1": {"version": "<invalid>", "ts": T0}}},
    {"last_offer_ts": None, "pending": [], "declines_in_row": 0, "auto_off": False,
     "offered": {"ap1-0000000000a1": {"version": VERSION, "ts": T0, "note": "x"}}}])
def test_a_policy_that_is_not_ours_turns_offers_off(policy_doc):
    config.REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REPORTS_FILE.write_text(json.dumps({"version": 1, "policy": policy_doc}))
    assert store.policy_state().auto_off is True


# ---- the 90-day simulation ------------------------------------------------------------

STEP = 600                              # the offer check runs every 10 minutes
SIM_DAYS = 90


@functools.lru_cache(maxsize=1)
def _forged_datagram() -> dict:
    """A relayed error a forger keeps sending (real code, as 1b2 requires)."""
    from aipager.report import relay
    message = {"type": "report_error", "where": "hook", "event": "PreToolUse", "tool": "Bash",
               "denied": False, "env": None,
               "facts": {"type": "builtins.ValueError", "cause_types": [], "errno": None,
                         "frames": [{"file": "aipager/dtach/enforce.py", "line": 3,
                                     "fn": "decide"}], "external": []}}
    checked = relay.read_error_datagram(message)
    assert checked is not None
    return checked


def simulate(answers: str, *, bugs=True, crashes=True, forger=False, anomalies=False,
             owner_dm=True, idle=True, upgrade_on: int | None = None,
             forger_days: int = SIM_DAYS, days: int = SIM_DAYS,
             new_bug_every_hours: int | None = None) -> list[int]:
    """Days in which an offer was made, over *days* days of a daemon that
    has been installed for 10 days, with events every hour, a crash a day,
    and the answer pattern *answers* (send / not_now / dont_ask / ignore /
    mixed)."""
    rng = random.Random(7)
    store.reset()
    state = policy.State()
    offers: list[int] = []
    version = VERSION
    start = T0
    for step in range(days * DAY // STEP):
        now = start + step * STEP
        day = (now - start) // DAY
        if upgrade_on is not None and day >= upgrade_on:
            version = "0.9.0"
        import aipager
        aipager.__version__ = version
        if bugs and step % 6 == 0:          # a bug every hour
            store.record_site("log_error", file="aipager/state.py", line=10, fn="save",
                              where="daemon", trigger="log_error", tier="bug", now=now)
        if crashes and step % (DAY // STEP) == 0:   # a crash every day
            store.record_site("crash", file="aipager/cli/daemon.py", line=0, fn="_cmd_start",
                              where="daemon", trigger="crash", tier="bug", now=now)
        if forger and day < forger_days:    # a forged relayed error every 10 minutes
            store.record_relayed(dict(_forged_datagram()), now=now)
        if new_bug_every_hours and step % (new_bug_every_hours * 6) in (0, 2):
            # A new bug every few hours, seen twice 20 minutes apart.
            fn = f"new_bug_{step // (new_bug_every_hours * 6)}"
            store.record_site("log_error", file="aipager/state.py", line=30, fn=fn,
                              where="daemon", trigger="log_error", tier="bug", now=now)
        if anomalies and step % 3 == 0:
            store.record_site("log_error", file="aipager/state.py", line=20, fn="load",
                              where="daemon", trigger="log_error", tier="anomaly", now=now)
            store.record_counter("tg_5xx", now=now)
            store.record_counter("env_network", now=now)
        state = policy.settle(state, now)
        cond = _open(now=now, version=version, first_start=start - 10 * DAY,
                     daemon_started=start - 2 * 3600, owner_dm=owner_dm,
                     idle_for=600 if idle else 0)
        fps = policy.due_offer(store.records(), state, cond)
        if fps:
            offers.append(day)
            state = policy.note_offer(state, fps, now, version)
            answer = answers if answers != "mixed" else rng.choice(
                ["send", "not_now", "ignore", "send"])
            if answer != "ignore":
                state = policy.note_answer(state, answer)
    return offers


@pytest.fixture(autouse=True)
def _restore_version(monkeypatch):
    import aipager
    monkeypatch.setattr(aipager, "__version__", aipager.__version__)


@pytest.mark.parametrize("answers", ["send", "not_now", "dont_ask", "ignore", "mixed"])
def test_ninety_days_never_bring_more_than_one_offer_per_three_days(answers):
    offers = simulate(answers, forger=True, upgrade_on=45)
    assert len(offers) <= SIM_DAYS // 3
    assert all(b - a >= 3 for a, b in zip(offers, offers[1:])), offers


@pytest.mark.parametrize("answers", ["not_now", "dont_ask", "ignore"])
def test_after_two_declines_there_are_no_more_offers(answers):
    offers = simulate(answers, forger=True, upgrade_on=45)
    assert len(offers) <= 2


def test_sending_every_time_offers_each_bug_once_per_version():
    offers = simulate("send", upgrade_on=45)
    # The day-0 crash counts at once; the hourly bug needs a second
    # occasion and then waits out the 3-day gap; after the upgrade to
    # 0.9.0 both recur and come back once, together.
    assert offers == [0, 3, 45]


def test_anomalies_and_environment_errors_never_bring_an_offer():
    assert simulate("send", bugs=False, crashes=False, anomalies=True) == []


def test_a_group_never_gets_an_offer():
    assert simulate("send", owner_dm=False) == []


def test_a_busy_daemon_never_gets_an_offer():
    assert simulate("send", idle=False) == []


def test_relayed_datagrams_on_one_day_never_bring_an_offer():
    """A forger sending every 10 minutes for one day is never offered;
    its records must span two UTC days first."""
    assert simulate("send", bugs=False, crashes=False, forger=True, forger_days=1,
                    days=5) == []


def test_relayed_datagrams_over_days_are_still_bounded():
    offers = simulate("not_now", bugs=False, crashes=False, forger=True)
    assert len(offers) <= 2


def test_the_offer_rules_never_touch_the_manual_report():
    """The manual path builds a report from the store directly: the policy
    is not on it (no gate, no state change)."""
    from aipager.report import builder
    store.record_site("log_error", file="aipager/state.py", line=10, fn="save",
                      where="daemon", trigger="log_error", tier="bug", now=T0)
    store.save_policy(policy.State(auto_off=True, declines_in_row=5))
    report = builder.build_report("manual", errors=store.errors())
    assert report["errors"] and report["trigger"] == "manual"
    assert sc.INVALID not in json.dumps(report)


def test_due_offer_settles_an_ignored_offer_itself():
    """A caller that forgets to call settle() still gets the decline
    counted: the ignored offer no longer blocks, and it counts once."""
    state = _offered([_record()])
    later = state.last_offer_ts + policy.REPORT_OFFER_MIN_GAP
    assert policy.due_offer([_record(fp="ap1-0000000000b2")], state, _open(now=later)) == (
        "ap1-0000000000b2",)


def test_a_reset_forgets_the_policy_in_memory():
    store.save_policy(policy.State(auto_off=True))
    config.REPORTS_FILE.unlink()
    store.reset()
    assert store.policy_state() == policy.State()


def test_with_new_bugs_all_the_time_the_gap_is_what_limits_offers():
    """The 3-day gap is the binding limit here, and offers keep coming
    when the user keeps sending: between 28 and 30 in 90 days."""
    offers = simulate("send", bugs=False, crashes=False, new_bug_every_hours=4)
    assert 28 <= len(offers) <= 30, offers
    assert all(b - a >= 3 for a, b in zip(offers, offers[1:])), offers


def test_the_policy_state_handed_out_is_a_copy():
    store.save_policy(policy.note_offer(FRESH, ("ap1-0000000000a1",), T0, VERSION))
    handed = store.policy_state()
    handed.offered["ap1-0000000000a1"]["version"] = "9.9.9"
    handed.offered["ap1-0000000000b2"] = {"version": VERSION, "ts": T0}
    assert store.policy_state().offered == {"ap1-0000000000a1": {"version": VERSION, "ts": T0}}


def test_the_store_dates_a_future_offer_now_whatever_the_caller_does():
    """No caller can forget the clock fix: a last offer (or an offered
    bug) dated in the future is dated now when the store reads it."""
    import time
    far = int(time.time()) + 400 * DAY
    store.save_policy(policy.State(last_offer_ts=far,
                                   offered={"ap1-0000000000a1": {"version": VERSION, "ts": far}}))
    store.reset()
    loaded = store.policy_state()
    assert loaded.last_offer_ts <= time.time() + 1
    assert loaded.offered["ap1-0000000000a1"]["ts"] <= time.time() + 1


def test_saving_an_unchanged_policy_writes_nothing(monkeypatch):
    state = policy.note_offer(FRESH, ("ap1-0000000000a1",), T0, VERSION)
    store.save_policy(state)
    writes = []
    monkeypatch.setattr(store, "save_if_dirty", lambda **k: writes.append(k))
    store.save_policy(store.policy_state())
    assert writes == []
    store.save_policy(policy.note_answer(state, "send"))
    assert writes == [{"force": True}]
