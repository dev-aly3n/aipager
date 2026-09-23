"""A ban outlives the process (rows H and I; rule R5; 8.28).

The defect, in the code's own words. ``flood.py``'s docstring said "In
memory only, on the monotonic clock: a daemon restart forgets the mute
(its startup message is one attempt — accepted)", and
``BudgetRateLimiter.initialize`` said "Nothing is restored: a restart
starts every chat at ×1 (R8)" — and unlinked the backoff file to make it
so. Telegram's penalty regime is measured in HOURS; ours was measured in
process lifetimes.

On 2026-09-15 that cost 9.5 hours: a daemon restarted during a ban came
back knowing nothing, and its own startup notice — plus
``_recover_busy_message``'s edit of every orphaned busy card — were the
first requests into it.

Two changes make it work, and both are tested here: the mute deadline is
WALL-clock (a monotonic one is seconds-since-boot and means nothing after
a restart), and ``bot/flood_state.py`` carries the deadline, each chat's
earned rate and its ban history in a file under ``$HOME`` — not on the
tmpfs where the two runtime signal files live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipager import config
from aipager.bot import flood_state
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = 256113222
BAN = 34212.0        # the real 2026-09-15 retry_after


@pytest.fixture(autouse=True)
def _fresh_state():
    """No test inherits another's dirty flag or write throttle."""
    flood_state.clear()
    yield
    flood_state.clear()


def _state_path() -> Path:
    return Path(config.FLOOD_STATE_FILE)


def _install(limiter):
    import aipager.bot.rich_message as rm
    rm.set_rate_limiter(limiter)
    return limiter


# ── D-3(b): the hard safety requirement ──────────────────────────────────────

def test_the_flood_state_path_under_pytest_is_inside_tmp_path(tmp_path):
    """MANDATORY (D-3b). ``config.FLOOD_STATE_FILE`` points into the
    operator's REAL ``~/.claude/``, and ``_guard_real_home`` deliberately
    excludes that directory from its snapshot — so a missing redirect in
    ``tests/conftest.py::_isolate_home_paths`` would let the suite write
    into the live install and NO existing guard would catch it.

    Asserted on the path the code actually resolves, not on the constant,
    because ``flood_state`` reads it late through ``config``: if anyone
    ever re-imports it by value, this fails.

    Mutation: remove the ``aipager.config.FLOOD_STATE_FILE`` entry from
    ``_isolate_home_paths`` and this names the real home path.
    """
    resolved = flood_state._path()
    assert tmp_path in resolved.parents, resolved
    assert Path.home() not in resolved.parents, (
        f"the suite is writing flood state into the real home: {resolved}")

    flood_state.mark_dirty()
    flood_state.save_if_dirty(force=True)
    assert resolved.exists()
    assert tmp_path in resolved.parents


# ── the file itself ──────────────────────────────────────────────────────────

def test_a_missing_file_reads_as_empty_and_loads_nothing(limiter):
    """A first run. Mutation: raise on a missing file and the daemon
    cannot start at all on a fresh install."""
    _install(limiter)
    assert flood_state.read() == {}
    assert flood_state.load() is False


@pytest.mark.parametrize("body", [
    "not json at all",
    "[]",
    '{"version": 999, "chats": []}',
    '{"chats": "not a list"}',
    "",
])
def test_a_corrupt_or_wrong_version_file_starts_fresh_and_never_raises(
    limiter, body,
):
    """Criterion 16, and the ``SessionRegistry.load`` contract: a bad
    state file must leave the daemon RUNNING. Starting fresh is always
    safe here — everything in this file is re-learned within hours.

    Mutation: drop the version check, or let json errors escape, and one
    bad write bricks every subsequent start.
    """
    _install(limiter)
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(body)
    assert flood_state.read() == {}
    assert flood_state.load() is False


def test_the_document_carries_chats_as_an_array_not_an_object(
    limiter, flood_clock,
):
    """An int chat id must not round-trip through a JSON object key: it
    would come back as the string "-1001234567" and land on a different
    budget than the one it left.

    Mutation: serialise `chats` as a dict keyed by chat id and the
    restored budget is a new, empty one for a chat that looks identical.
    """
    _install(limiter)
    limiter.note_retry_after(CHAT, 5)
    flood_state.save_if_dirty(force=True)

    doc = json.loads(_state_path().read_text())
    assert doc["version"] == flood_state.SCHEMA_VERSION
    assert isinstance(doc["chats"], list)
    assert doc["chats"][0]["chat_id"] == CHAT
    assert isinstance(doc["chats"][0]["chat_id"], int)


def test_a_write_failure_is_never_visible_to_a_caller(limiter, monkeypatch):
    """A full disk must not be able to stop a send. Mutation: let the
    OSError escape `save_if_dirty` and a write failure propagates out of
    the session monitor's 2 s tick."""
    _install(limiter)
    monkeypatch.setattr(
        "aipager.config.FLOOD_STATE_FILE",
        "/proc/definitely-not-writable/aipager-flood-state.json")
    flood_state.mark_dirty()
    assert flood_state.save_if_dirty(force=True) is False


def test_writes_are_debounced_but_a_forced_write_always_happens(
    limiter, flood_clock,
):
    """The dirty flag plus the monitor's existing 2 s tick IS the
    debounce; this is the floor under it, so a burst of 429s costs one
    write rather than twenty. `lifecycle.stop()` forces, because there is
    no next tick."""
    _install(limiter)
    flood_state.mark_dirty()
    assert flood_state.save_if_dirty() is True
    flood_state.mark_dirty()
    assert flood_state.save_if_dirty() is False, "the debounce did not hold"
    assert flood_state.save_if_dirty(force=True) is True


def test_a_clean_state_writes_nothing(limiter):
    """Mutation: write unconditionally and an idle daemon rewrites this
    file every 2 s for ever."""
    _install(limiter)
    assert flood_state.save_if_dirty() is False
    assert not _state_path().exists()


# ── row H → row I: the ban survives the restart ──────────────────────────────

def test_a_ban_and_its_reduced_rate_survive_a_restart(flood_clock, run_async):
    """Row I, end to end, and the row that MUST fail on 0.7.12.

    A 34212 s ban (the real one) is armed, the state is flushed as
    `lifecycle.stop()` flushes it, and a COMPLETELY FRESH limiter with an
    empty `MUTE` — which is what a restarted daemon has — loads it back.

    Mutation: drop `flood_state.load()` from `lifecycle.start`, or restore
    the deadline on the monotonic clock, and the second daemon starts
    sending into a ban with 9 hours left to run.
    """
    first = _install(BudgetRateLimiter(
        clock=flood_clock, sleep=flood_clock.sleep,
        signal_path=config.FLOOD_BACKOFF_FILE))
    MUTE.mute(CHAT, BAN, source="sendMessage")
    first.note_ban(CHAT, BAN)
    assert first.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)
    assert flood_state.save_if_dirty(force=True) is True

    # ── the restart ──
    MUTE.clear()
    first.reset()
    assert not MUTE.is_muted(CHAT), "precondition: the new process knows nothing"
    flood_clock.advance(120.0)          # the daemon was down for two minutes

    second = _install(BudgetRateLimiter(
        clock=flood_clock, sleep=flood_clock.sleep,
        signal_path=config.FLOOD_BACKOFF_FILE))
    assert flood_state.load() is True

    assert MUTE.is_muted(CHAT), "the ban was forgotten across the restart"
    assert MUTE.remaining(CHAT) == pytest.approx(BAN - 120.0, abs=2.0)
    assert second.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)
    assert second.snapshot()["chats"][0]["bans_today"] == 1


def test_the_restored_mute_refuses_the_first_send_of_the_new_daemon(
    flood_clock, run_async,
):
    """Row I's observable. `lifecycle.start` issues real calls at startup
    — the startup notice, and `_recover_busy_message`'s `edit_message_text`
    for every orphaned busy card. Under a restored mute the gate refuses
    them; before 8.28 they were the first requests into the ban.
    """
    first = _install(BudgetRateLimiter(clock=flood_clock,
                                       sleep=flood_clock.sleep))
    MUTE.mute(CHAT, BAN, source="sendMessage")
    first.note_ban(CHAT, BAN)
    flood_state.save_if_dirty(force=True)

    MUTE.clear()
    second = _install(BudgetRateLimiter(clock=flood_clock,
                                        sleep=flood_clock.sleep))
    flood_state.load()

    ran: list[str] = []

    async def _startup_notice():
        ran.append("sent")

    with pytest.raises(FloodMuted):
        run_async(second.process_request(
            callback=_startup_notice, args=(), kwargs={},
            endpoint="editMessageText", data={"chat_id": CHAT},
            rate_limit_args=None))
    assert ran == [], "the new daemon sent into the restored ban"


def test_a_mute_that_lapsed_while_the_daemon_was_down_is_dropped(
    flood_clock, run_async,
):
    """The other direction, and just as important: a ban that expired
    overnight must NOT be resurrected. Mutation: restore every entry
    regardless of its deadline and a restart re-mutes a healthy chat for
    however long the file happens to say."""
    first = _install(BudgetRateLimiter(clock=flood_clock,
                                       sleep=flood_clock.sleep))
    MUTE.mute(CHAT, 600.0, source="sendMessage")
    first.note_ban(CHAT, 600.0)
    flood_state.save_if_dirty(force=True)

    MUTE.clear()
    flood_clock.advance(601.0)          # it lapsed while we were down

    _install(BudgetRateLimiter(clock=flood_clock, sleep=flood_clock.sleep))
    flood_state.load()
    assert not MUTE.is_muted(CHAT)


def test_the_earned_rate_keeps_recovering_across_the_restart(flood_clock):
    """The rate is rebased onto the new process's clock, not reset. A
    daemon that was down for two hours of a six-hour recovery comes back
    two hours in — the memory is of WALL time, which is the only kind
    Telegram has.

    Mutation: restore `rate_earned_at` as stored (a monotonic value from
    another boot) and the anchor lands in the far past or the far future,
    so the chat either jumps to the ceiling or never climbs again.

    AMENDED by 8.30 R5 / row J: the 429 starts a six-hour warning regime
    that now SURVIVES the restart too, so the windows the chat keeps
    earning across it are the slow ones (``FLOOD_RATE_RECOVERY_HOURS`` /
    10 each), not 60 s — 0.7.13 forgot the 429 at the restart. Two slow
    windows, so the climb stays under the warned ceiling.
    """
    slow = config.FLOOD_RATE_RECOVERY_HOURS * 3600.0 / 10
    first = _install(BudgetRateLimiter(clock=flood_clock,
                                       sleep=flood_clock.sleep))
    first.note_retry_after(CHAT, 5)
    reduced = first.earned_rate(CHAT)
    flood_state.save_if_dirty(force=True)

    first.reset()
    flood_clock.advance(slow * 2)

    second = _install(BudgetRateLimiter(clock=flood_clock,
                                        sleep=flood_clock.sleep))
    flood_state.load()
    assert second.earned_rate(CHAT) == pytest.approx(
        reduced + 2 * config.FLOOD_RATE_INCREASE)


# ── D-7: the clamp ───────────────────────────────────────────────────────────

def test_a_deadline_further_out_than_the_cap_is_clamped_not_trusted(
    flood_clock, caplog,
):
    """Criterion 16 / D-7. Making the deadline wall-clock is what lets it
    survive a restart; it also makes it sensitive to an NTP jump, a VM
    resumed from a snapshot or a container with a bad RTC. The longest ban
    ever observed is 34212 s (9.5 h), so anything past
    `FLOOD_MUTE_MAX_SECONDS` (24 h) is a clock bug, not a ban.

    Mutation: trust the stored deadline and one skewed clock mutes the bot
    for as long as the skew — with no way for the user to clear it.
    """
    caplog.set_level("WARNING", logger="aipager.bot.flood")
    absurd = flood_clock.wall + config.FLOOD_MUTE_MAX_SECONDS * 400
    assert MUTE.restore([{"chat_id": CHAT, "until": absurd,
                          "retry_after": 1e9}]) == 1

    assert MUTE.remaining(CHAT) == pytest.approx(
        config.FLOOD_MUTE_MAX_SECONDS, abs=2.0)
    assert any("clamped" in r.getMessage() for r in caplog.records)


def test_arming_a_mute_is_clamped_too(flood_clock, caplog):
    """Both ends, not just the restore: a `retry_after` that absurd could
    equally arrive from Telegram (or from a bug parsing one)."""
    caplog.set_level("WARNING", logger="aipager.bot.flood")
    MUTE.mute(CHAT, config.FLOOD_MUTE_MAX_SECONDS * 10)
    assert MUTE.remaining(CHAT) == pytest.approx(
        config.FLOOD_MUTE_MAX_SECONDS, abs=2.0)


def test_the_mute_deadline_is_wall_clock_not_monotonic(flood_clock):
    """D-7, directly. `time.monotonic()` is seconds since BOOT and
    restarts at a different origin, so a persisted monotonic deadline is
    meaningless — which is why every decision now compares against
    `time.time()`.

    Driven by moving ONLY the wall clock: if any decision still read the
    monotonic one, the mute would not lift here.
    """
    MUTE.mute(CHAT, 100.0)
    assert MUTE.is_muted(CHAT)
    flood_clock.wall += 101.0          # wall only; monotonic stands still
    assert not MUTE.is_muted(CHAT), "a decision is still on the monotonic clock"


def test_serialise_round_trips_through_the_file(flood_clock):
    """What `MUTE.serialise` writes is what `MUTE.restore` reads."""
    MUTE.mute(CHAT, 5000.0)
    entries = MUTE.serialise()
    assert entries[0]["chat_id"] == CHAT
    MUTE.clear()
    assert MUTE.restore(entries) == 1
    assert MUTE.remaining(CHAT) == pytest.approx(5000.0, abs=2.0)


@pytest.mark.parametrize("entries", [
    None, "nonsense", [None], [{"chat_id": 1}], [{"until": "soon"}],
    [{"chat_id": 1, "until": None}],
])
def test_restore_never_raises_on_junk(entries):
    """Whatever the file holds, the daemon starts."""
    assert MUTE.restore(entries) == 0


def test_the_limiter_restore_never_raises_on_junk(limiter):
    """Whatever the file holds, the daemon starts.

    The last line changed in iteration 2 (T2): a numeric field that cannot
    be read is now DEFAULTED and the rest of the entry kept, rather than
    the whole entry being discarded. Discarding it is the unsafe
    direction — the entry is also where `ban_stamps` live, so one junk
    rate used to forget a chat's whole ban history. What matters is that
    the chat is left at a SAFE rate, which is asserted rather than the
    count.
    """
    assert limiter.restore(None) == 0
    assert limiter.restore(["not a dict"]) == 0
    assert limiter.restore([{"chat_id": None}]) == 0
    limiter.restore([{"chat_id": 1, "rate": "fast"}])
    assert limiter.earned_rate(1) == pytest.approx(config.FLOOD_START_RATE)


def test_a_restored_rate_outside_the_legal_range_is_clamped(limiter):
    """A hand-edited or corrupted file must not be able to set a rate
    Telegram never permits. Mutation: trust the stored value and a file
    saying 50 calls/s opens the daemon at 50 calls/s."""
    limiter.restore([{"chat_id": CHAT, "rate": 999.0}])
    assert limiter.earned_rate(CHAT) == pytest.approx(
        config.TELEGRAM_PRIVATE_MAX_RATE)
    limiter.restore([{"chat_id": CHAT, "rate": -5.0}])
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_the_volatile_columns_are_written_but_ignored_on_load(
    limiter, flood_clock, run_async,
):
    """`sustained_used` / `sustained_limit` / `minimal` exist for the
    `aipager status` reader in another process. They describe a rolling
    window that has long since rolled by the time anyone restarts, so
    loading them back would report a stale burst as current."""
    _install(limiter)

    async def _cb():
        return "sent"

    run_async(limiter.process_request(
        callback=_cb, args=(), kwargs={}, endpoint="sendMessage",
        data={"chat_id": CHAT}, rate_limit_args=None))
    flood_state.save_if_dirty(force=True)
    doc = json.loads(_state_path().read_text())
    assert doc["chats"][0]["sustained_used"] == 1

    limiter.reset()
    flood_state.load()
    assert limiter.sustained_used(CHAT) == 0, "a stale window was restored"
