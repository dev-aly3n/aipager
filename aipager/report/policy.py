"""When aipager may OFFER to send a problem report (roadmap 8.112).

The manual "Report a problem" button and ``aipager report`` are never
gated: this decides only the automatic offer, and that offer is rare by
construction. The operator, 2026-10-07: "I don't want it to appear for the
user too often, otherwise it will be another issue itself", "at most once
in 3-4 days", and never "in the first 2 days after first install (not the
updates)". Design: /home/aly/researches/15-issue-reporting/design.md
section 4, "Frequency budget"; D4 for developer installs; section 7 for
relayed errors.

Pure: :func:`due_offer` decides from the stored records, the policy
:class:`State` and the live :class:`Conditions` passed in. No Telegram,
no clock, no I/O here; the caller persists what :func:`note_offer`,
:func:`note_answer` and :func:`settle` return.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

#: At most one automatic offer in this many seconds, all pending bugs combined.
REPORT_OFFER_MIN_GAP = 3 * 86400
#: No automatic offer this long after the FIRST daemon start on the machine
#: (the install marker; an update or a reinstall never restarts it).
FIRST_INSTALL_QUIET = 48 * 3600
#: No automatic offer this long after a daemon (re)start.
RESTART_QUIET = 3600
#: Everything (sessions, pending sends) idle this long before an offer.
IDLE_BEFORE_OFFER = 120
#: The owner acted in their private chat within this long.
OWNER_ACTIVE_WINDOW = 30 * 60
#: This many declines in a row (an ignored offer counts) turn offers off.
DECLINES_TO_OFF = 2
#: Separate occasions (``store.OCCASION_GAP`` apart) before a bug is offered.
OCCASIONS_TO_OFFER = 2
#: An offer left unanswered this long was ignored: a decline.
OFFER_ANSWER_WINDOW = 86400
#: Bugs that count at once: the daemon died, or a hook hit its memory cap.
AT_ONCE_TRIGGERS = frozenset({"crash", "hook_cap"})
#: Records another process told the daemon about: any local process can
#: write that socket, so they must also span a full day, first to last.
RELAYED_WHERE = frozenset({"hook", "statusline", "cli"})
RELAYED_MIN_SPAN = 86400
#: Bugs one offer carries at most (a report's ``errors`` cap).
MAX_PER_OFFER = 10
#: Fingerprints remembered as offered (oldest forgotten first).
OFFERED_KEPT = 200
ANSWERS = ("send", "not_now", "dont_ask")


@dataclass(frozen=True)
class Conditions:
    """The live situation the caller reads (all times in unix seconds)."""
    now: int
    version: str | None
    first_start: int | None           # the install marker
    daemon_started: float | None      # this daemon's start
    idle_for: float                   # seconds with no session busy and no send pending
    owner_active_ago: float | None    # seconds since the owner last acted in their DM
    owner_dm: bool                    # the offer would go to the owner's private chat
    prompts_on: bool                  # /settings "Problem reports: Ask me"
    env_off: bool                     # AIPAGER_REPORT_PROMPTS=0
    developer_install: bool           # origin local/vcs, or editable (D4)


@dataclass(frozen=True)
class State:
    """What the policy remembers between offers (kept in the local store)."""
    last_offer_ts: int | None = None
    pending: tuple[str, ...] = ()     # the offer still awaiting an answer
    declines_in_row: int = 0
    auto_off: bool = False
    #: fingerprint -> {"version": the aipager version it was offered on,
    #: "ts": when}
    offered: dict = field(default_factory=dict)


def _declined(state: State) -> State:
    count = state.declines_in_row + 1
    return replace(state, pending=(), declines_in_row=count,
                   auto_off=state.auto_off or count >= DECLINES_TO_OFF)


def settle(state: State, now: int) -> State:
    """*state* with an offer unanswered for :data:`OFFER_ANSWER_WINDOW`
    counted as a decline, and a last offer dated in the future (a clock
    that was wrong, or went back) dated now: it must neither lock offers
    out until then nor skip the gap (the caller persists the result)."""
    if state.last_offer_ts is not None and state.last_offer_ts > now:
        state = replace(state, last_offer_ts=now)
    if (state.pending and state.last_offer_ts is not None
            and now - state.last_offer_ts >= OFFER_ANSWER_WINDOW):
        return _declined(state)
    return state


def eligible(record: dict, state: State, version: str | None) -> bool:
    """Whether one stored record may be offered (the per-bug rules)."""
    entry = record["entry"]
    if entry["tier"] != "bug":
        return False
    needed = 1 if entry["trigger"] in AT_ONCE_TRIGGERS else OCCASIONS_TO_OFFER
    if record["occasions"] < needed:
        return False
    if entry["where"] in RELAYED_WHERE and record["last_ts"] - record["first_ts"] < RELAYED_MIN_SPAN:
        return False
    offered = state.offered.get(entry["fingerprint"])
    if offered is not None:
        if offered["version"] == version:
            return False  # once per version, whatever the answer was
        # After an upgrade, only if it happened again ON this version after
        # the offer: the store keeps the version of its latest occurrence
        # last, so a recurrence on an older version does not count.
        seen = entry["versions_seen"]
        if not seen or seen[-1] != version or record["last_ts"] <= offered["ts"]:
            return False
    return True


def due_offer(records: list[dict], state: State, conditions: Conditions) -> tuple[str, ...]:
    """The fingerprints to offer now, combined into one offer; empty when
    no offer may be made. Every gate below must hold."""
    cond = conditions
    if not cond.prompts_on or cond.env_off or cond.developer_install or not cond.owner_dm:
        return ()
    state = settle(state, cond.now)
    if state.auto_off or state.pending:
        return ()
    if cond.first_start is None or cond.now - cond.first_start < FIRST_INSTALL_QUIET:
        return ()
    if cond.daemon_started is None or cond.now - cond.daemon_started < RESTART_QUIET:
        return ()
    if state.last_offer_ts is not None and cond.now - state.last_offer_ts < REPORT_OFFER_MIN_GAP:
        return ()
    if cond.idle_for < IDLE_BEFORE_OFFER:
        return ()
    if cond.owner_active_ago is None or cond.owner_active_ago > OWNER_ACTIVE_WINDOW:
        return ()
    chosen = [r for r in records if eligible(r, state, cond.version)]
    chosen.sort(key=lambda r: -r["last_ts"])
    return tuple(r["entry"]["fingerprint"] for r in chosen[:MAX_PER_OFFER])


def note_offer(state: State, fingerprints, now, version: str | None) -> State:
    """An offer of *fingerprints* was shown at *now* (unix seconds; a float
    is stored as its whole seconds, as the store requires)."""
    now = int(now)
    offered = dict(state.offered)
    for key in fingerprints:
        offered[key] = {"version": version, "ts": now}
    if len(offered) > OFFERED_KEPT:
        keep = sorted(offered, key=lambda k: offered[k]["ts"])[-OFFERED_KEPT:]
        offered = {k: offered[k] for k in keep}
    return replace(state, last_offer_ts=now, pending=tuple(fingerprints), offered=offered)


def reenable(state: State) -> State:
    """Automatic offers turned back on (the /settings switch): the declines
    are forgotten, the 3-day gap from the last offer still holds."""
    return replace(state, auto_off=False, declines_in_row=0, pending=())


def note_answer(state: State, answer: str) -> State:
    """The user answered the pending offer: ``send`` resets the decline
    count; ``not_now`` and ``dont_ask`` are declines (both mean never
    again for those bugs on this version)."""
    if answer not in ANSWERS or not state.pending:
        return state
    if answer == "send":
        return replace(state, pending=(), declines_in_row=0)
    return _declined(state)
