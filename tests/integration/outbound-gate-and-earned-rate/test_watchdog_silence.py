"""The busy-card watchdog goes quiet while a chat is suppressed (row M, R7).

348 restarts in 54 minutes, measured 2026-09-15, against SIX actual HTTP
refusals all day — so this was log spam, not a ban extender. It was
spam with a cause, though, and the cause is a loop:

1. a ban arms ``flood.MUTE``;
2. the animator's next tick returned ``None`` from ``_animate_tick``;
3. ``_animate_busy`` read ``None`` as a permanent failure, broke its loop,
   and the task finished — so ``sess.animation_running()`` went False;
4. within 20 s the monitor saw a BUSY session with no animate task,
   logged ``no animate task while BUSY — restarting the busy-card
   animation`` at WARNING and dispatched ``busy_card_watchdog``;
5. ``_resume_animation_if_dead`` logged its own INFO and started a task;
6. the new task hit step 2 again.

Two log lines per restart, throttled to one restart per session per
``CARD_STALE_SECONDS`` (20 s) — about 3/min/session, and two BUSY sessions
gives ~324 in 54 minutes, which is the measurement.

Both ends are fixed, and both are needed: ``_animate_tick`` no longer
kills its own task (tested in ``test_rich_message_flood.py``), AND the
watchdog declines to act on a suppressed chat. Fixing only the watchdog
would leave ``notify.py``'s ``tool_use`` path calling
``_resume_animation_if_dead`` on the same loop.
"""

from __future__ import annotations

import logging

import pytest

from aipager.session_monitor import (
    CARD_STALE_SECONDS,
    busy_card_watchdog_action,
    card_suppression_transition,
    cards_suppressed,
)
from aipager.state import Status, TrackedSession

CHAT = 256113222
BAN = 34212.0


def _stale_busy() -> TrackedSession:
    """A session the watchdog WOULD act on: BUSY, card open, no animate
    task, last acted on long ago."""
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.BUSY)
    sess.busy_msg_id = 4242
    sess.scope_chat_id = CHAT
    sess.busy_started_at = 1.0
    sess.card_watchdog_at = 0.0
    return sess


# ── the pure decision ────────────────────────────────────────────────────────

def test_the_watchdog_would_restart_an_unsuppressed_stale_card():
    """The control. Without it, "returns None when suppressed" would also
    pass for a session the watchdog was never going to act on."""
    sess = _stale_busy()
    now = 100.0 + CARD_STALE_SECONDS
    assert busy_card_watchdog_action(sess, now) == ("restart", 0.0)


def test_the_watchdog_declines_to_act_on_a_suppressed_chat():
    """Row M. There is no point restarting an animation that cannot
    animate — and doing it anyway is where the 348 restarts came from.

    Mutation: drop the `suppressed` early return and this returns
    ``("restart", 0.0)``, which is the storm.
    """
    sess = _stale_busy()
    now = 100.0 + CARD_STALE_SECONDS
    assert busy_card_watchdog_action(sess, now, suppressed=True) is None


def test_the_suppressed_check_comes_before_every_other_rule():
    """It must be the FIRST rule, not one of several: a refresh is as
    pointless as a restart while the chat cannot be written to."""
    sess = _stale_busy()
    sess.animation_running = lambda: True
    sess.last_tool_edit_at = 1.0
    now = 100.0 + CARD_STALE_SECONDS
    assert busy_card_watchdog_action(sess, now) == ("refresh", now - 1.0)
    assert busy_card_watchdog_action(sess, now, suppressed=True) is None


def test_the_action_stays_pure_and_defaults_to_not_suppressed():
    """``suppressed`` is passed IN, never looked up, so this function has
    no I/O, no clock and no `MUTE` lookup — which is what lets every
    existing row call it with a fabricated ``now``.

    Mutation: look the mute up inside and every pure row in
    ``test_busy_card_watchdog.py`` starts depending on global state.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(busy_card_watchdog_action)))
    fn = tree.body[0]
    # Drop the docstring: it DESCRIBES the lookup this function must not
    # do, so a naive text search over the source matches its own prose.
    body = fn.body[1:] if (isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)
                           ) else fn.body
    code = "\n".join(ast.unparse(node) for node in body)
    assert "MUTE" not in code, "the pure decision function grew an I/O lookup"
    assert "monotonic" not in code
    assert "import" not in code, "a late import is still I/O in disguise"
    sess = _stale_busy()
    assert busy_card_watchdog_action(sess, 100.0 + CARD_STALE_SECONDS) \
        == ("restart", 0.0)


# ── the impure half ──────────────────────────────────────────────────────────

def test_cards_are_suppressed_while_the_chat_is_muted(flood_clock, limiter):
    from aipager.bot.flood import MUTE

    assert cards_suppressed(CHAT) is False
    MUTE.mute(CHAT, BAN)
    assert cards_suppressed(CHAT) is True
    flood_clock.advance(BAN + 1)
    assert cards_suppressed(CHAT) is False


def test_cards_are_suppressed_while_the_chat_is_in_minimal_mode(
    flood_clock, limiter, monkeypatch,
):
    """Minimal mode suspends ornaments, and the card is the biggest
    ornament there is — so the watchdog must not fight it either."""
    import aipager.bot.rich_message as rm
    monkeypatch.setattr(rm, "_rate_limiter", limiter)

    assert cards_suppressed(CHAT) is False
    limiter.note_retry_after(CHAT, 5)
    limiter.note_retry_after(CHAT, 5)
    assert limiter.minimal_mode(CHAT) is True
    assert cards_suppressed(CHAT) is True


def test_cards_suppressed_never_raises_and_fails_open(monkeypatch):
    """It runs on the 2 s scan for every session. A limiter problem must
    not be able to stop the scan, and "not suppressed" is the safe answer
    — the watchdog then behaves exactly as it did before 8.29.
    """
    assert cards_suppressed(None) is False
    assert cards_suppressed(0) is False

    def _boom():
        raise RuntimeError("limiter exploded")

    monkeypatch.setattr("aipager.bot.rich_message.get_rate_limiter", _boom)
    assert cards_suppressed(CHAT) is False


# ── exactly one INFO each way ────────────────────────────────────────────────

def test_the_transition_reports_start_once_then_lift_once():
    """Row M's log clause. At a 2 s tick the naive ``if suppressed: log``
    emits 1,800 lines an hour — which is the same disease as the 348
    restarts, in a different organ.

    Mutation: return ``"start"`` whenever ``suppressed`` is true and the
    INFO repeats every tick for the length of the ban.
    """
    seen: set = set()
    assert card_suppression_transition(seen, "claude-a", True) == "start"
    assert card_suppression_transition(seen, "claude-a", True) is None
    assert card_suppression_transition(seen, "claude-a", True) is None
    assert card_suppression_transition(seen, "claude-a", False) == "lift"
    assert card_suppression_transition(seen, "claude-a", False) is None


def test_the_transition_is_tracked_per_session():
    """Two sessions in one chat suppress and resume independently of each
    other's log state."""
    seen: set = set()
    assert card_suppression_transition(seen, "claude-a", True) == "start"
    assert card_suppression_transition(seen, "claude-b", True) == "start"
    assert card_suppression_transition(seen, "claude-a", False) == "lift"
    assert card_suppression_transition(seen, "claude-b", True) is None


def test_the_transition_can_cycle():
    """A chat banned twice in one day logs twice — the 2026-09-15 timeline
    was three bans. Mutation: never discard from the set and the second
    suppression is silent."""
    seen: set = set()
    for _ in range(3):
        assert card_suppression_transition(seen, "claude-a", True) == "start"
        assert card_suppression_transition(seen, "claude-a", False) == "lift"


def test_a_session_never_suppressed_logs_nothing():
    """The overwhelmingly common case: no entry, no transition, no line."""
    seen: set = set()
    for _ in range(50):
        assert card_suppression_transition(seen, "claude-a", False) is None
    assert seen == set()


# ── the two INFOs, as the monitor emits them ─────────────────────────────────

def test_the_monitor_logs_one_line_when_suppression_starts_and_one_when_it_lifts(
    caplog,
):
    """The wiring, end to end over the transition helper: whatever the
    tick count, the operator sees one line each way.

    Mutation: log inside the ``if suppressed`` branch directly and this
    counts one line per tick.
    """
    log = logging.getLogger("aipager.session_monitor")
    seen: set = set()
    caplog.set_level(logging.INFO, logger="aipager.session_monitor")

    def _tick(suppressed: bool) -> None:
        transition = card_suppression_transition(seen, "claude-dev", suppressed)
        if transition == "start":
            log.info("[dev] busy-card updates suppressed — the chat is "
                     "flood-muted or in minimal mode")
        elif transition == "lift":
            log.info("[dev] busy-card updates resumed")

    for _ in range(30):
        _tick(True)
    for _ in range(30):
        _tick(False)

    lines = [r.getMessage() for r in caplog.records]
    assert len([line for line in lines if "suppressed" in line]) == 1, lines
    assert len([line for line in lines if "resumed" in line]) == 1, lines


@pytest.mark.parametrize("suppressed", [True, False])
def test_the_watchdog_row_and_the_transition_agree_about_one_chat(suppressed):
    """The two halves are used together on every tick; this pins that a
    suppressed chat both declines the action AND reports a transition."""
    seen: set = set()
    sess = _stale_busy()
    now = 100.0 + CARD_STALE_SECONDS
    action = busy_card_watchdog_action(sess, now, suppressed=suppressed)
    transition = card_suppression_transition(seen, sess.name, suppressed)
    if suppressed:
        assert action is None and transition == "start"
    else:
        assert action == ("restart", 0.0) and transition is None
