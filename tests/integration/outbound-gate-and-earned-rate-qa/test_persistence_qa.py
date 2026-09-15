"""R5 — a memory that outlasts the ban: rows H and I, criteria 14-17.

The defect this replaces is one line of the old code: "a restart starts
every chat at ×1". A daemon restarted during a 9.5-hour ban used to come
back believing the chat was healthy and spend the rest of the ban proving
otherwise.

Everything here goes through the documented surface: ``flood.MUTE``,
``flood_state``, ``BudgetRateLimiter.serialise/restore`` and the file at
``config.FLOOD_STATE_FILE`` — which the autouse ``_isolate_home_paths``
fixture redirects into ``tmp_path`` (criterion 17 asserts exactly that).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from telegram.error import RetryAfter

import aipager.bot.rich_message as rm
from aipager import config, status
from aipager.bot import flood_state
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = -1001234567
BAN = 34212.0        # the real 9.5-hour ban of 2026-09-15


async def _ok():
    return "sent"


def _state_path() -> Path:
    return Path(config.FLOOD_STATE_FILE)


# ===== criterion 17 — the test suite must never touch the real file =====

def test_the_flood_state_path_under_pytest_is_inside_tmp_path(tmp_path):
    """D-3(b), a hard safety requirement: ``_guard_real_home`` deliberately
    excludes ``~/.claude/`` from its snapshot, so a missing redirect would
    write into the operator's real home and NOTHING would catch it."""
    assert _state_path().is_relative_to(tmp_path)


def test_a_forced_save_writes_only_inside_tmp_path(tmp_path):
    """The same guard, behaviourally: after a real write the only new file
    is under ``tmp_path``."""
    MUTE.mute(CHAT, BAN)
    flood_state.mark_dirty()
    flood_state.save_if_dirty(force=True)
    assert _state_path().exists() and _state_path().is_relative_to(tmp_path)


# ===== row H — a ban, and then nothing at all ============================

def test_a_ban_drives_the_earned_rate_to_the_floor(limiter, run_async):
    """Row H / criterion 14, first half. The 429 path halves; a BAN goes
    straight to ``FLOOD_MIN_RATE`` — a bot with history has proved the
    guess wrong, not slightly high."""
    ran: list[int] = []

    async def _boom():
        ran.append(1)
        raise RetryAfter(BAN)

    with pytest.raises(RetryAfter):
        run_async(limiter.process_request(
            callback=_boom, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": CHAT}, rate_limit_args=None))
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_a_mute_armed_elsewhere_is_learned_by_the_limiter(
    limiter, gated_bot, run_async,
):
    """The rich path's ban never reaches ``_run`` (it is a 200 body), so
    the limiter has to notice it lazily, on the next call it sees. If it
    did not, a rich-path ban would leave the chat at full rate the moment
    the mute lifted."""
    MUTE.mute(CHAT, BAN)
    with pytest.raises(FloodMuted):
        run_async(gated_bot.send_message(chat_id=CHAT, text="hi"))
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_a_ban_is_counted_for_the_operator_the_first_time_it_bites(
    limiter, gated_bot, run_async,
):
    """R9's ``bans_today``: the ban history is what makes 'a reduced start
    tomorrow' possible, so it has to be recorded, not inferred."""
    MUTE.mute(CHAT, BAN)
    with pytest.raises(FloodMuted):
        run_async(gated_bot.send_message(chat_id=CHAT, text="hi"))
    rows = {c["chat_id"]: c for c in limiter.snapshot()["chats"]}
    assert rows[CHAT]["bans_today"] == 1


def test_a_ban_nobody_tried_to_send_through_is_still_remembered(
    limiter, qa_clock, run_async, gated_bot,
):
    """Error guessing on the LAZY sync: ``MUTE.is_muted`` is a destructive
    read, so if the ban is only noticed when a send is attempted, a quiet
    ban (armed by the rich path, never retried) would lift with the chat
    back at its old rate — 'a ban today means a reduced start tomorrow'
    would not hold."""
    MUTE.mute(CHAT, BAN)
    qa_clock.advance(BAN + 1)
    run_async(gated_bot.send_message(chat_id=CHAT, text="hi"))
    assert limiter.earned_rate(CHAT) < config.FLOOD_START_RATE


def test_zero_further_requests_are_made_until_the_ban_lifts(
    gated_bot, qa_clock, run_async,
):
    """Row H / criterion 14, the number the ship is judged on. On
    2026-09-15 this was 3, then 5, then 9."""
    MUTE.mute(CHAT, BAN)

    async def _drive():
        for _ in range(9):
            try:
                await gated_bot.send_message(chat_id=CHAT, text="hi")
            except FloodMuted:
                pass
            qa_clock.advance(60)

    run_async(_drive())
    assert gated_bot.calls == []


def test_sends_resume_by_themselves_once_the_ban_lifts(
    gated_bot, qa_clock, run_async,
):
    """Row H's other half: the mute must EXPIRE, not need a restart."""
    MUTE.mute(CHAT, BAN)
    qa_clock.advance(BAN + 1)
    run_async(gated_bot.send_message(chat_id=CHAT, text="hi"))
    assert gated_bot.endpoints() == ["sendMessage"]


def test_the_refusals_are_counted_for_the_operator(
    limiter, gated_bot, run_async,
):
    """R9: a refusal that is invisible is a refusal nobody can explain."""
    MUTE.mute(CHAT, BAN)

    async def _drive():
        for _ in range(3):
            try:
                await gated_bot.send_message(chat_id=CHAT, text="hi")
            except FloodMuted:
                pass

    run_async(_drive())
    rows = {c["chat_id"]: c for c in limiter.snapshot()["chats"]}
    assert rows[CHAT]["muted_refusals"] == 3


# ===== row I — the restart ===============================================

def _restart(monkeypatch, qa_clock, downtime: float = 0.0):
    """Persist, forget everything a process restart forgets, then load.

    This is the honest shape of row I: the module-level registry and the
    limiter are both rebuilt, exactly as a new process would rebuild them,
    and only the file carries anything across.
    """
    flood_state.mark_dirty()
    assert flood_state.save_if_dirty(force=True) is True
    MUTE.clear()
    qa_clock.advance(downtime)
    fresh = BudgetRateLimiter(clock=qa_clock, sleep=qa_clock.sleep,
                              signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(fresh)
    monkeypatch.setattr(rm, "_rate_limiter", fresh, raising=False)
    assert flood_state.load() is True
    return fresh


def test_a_mute_survives_a_restart(limiter, qa_clock, monkeypatch):
    """Row I / criterion 15. ``lifecycle.start()``'s ``MUTE.clear()`` is
    exactly what R5 forbids: on 0.7.12 the restart was the fastest way to
    put nine more requests into an active ban."""
    MUTE.mute(CHAT, BAN)
    limiter.note_ban(CHAT, BAN)
    _restart(monkeypatch, qa_clock)
    assert MUTE.is_muted(CHAT)


def test_the_reduced_rate_survives_a_restart(limiter, qa_clock, monkeypatch):
    """Row I's second half, and P-4's CONDITION: keeping a 60 s decay is
    only defensible if the hours-scale memory genuinely outlasts a
    restart."""
    MUTE.mute(CHAT, BAN)
    limiter.note_ban(CHAT, BAN)
    fresh = _restart(monkeypatch, qa_clock)
    assert fresh.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_the_restored_deadline_is_on_the_wall_clock(
    limiter, qa_clock, monkeypatch,
):
    """D-7. A monotonic deadline is meaningless after a reboot: half an
    hour of downtime must consume half an hour of the ban, no more."""
    MUTE.mute(CHAT, 3600.0)
    _restart(monkeypatch, qa_clock, downtime=1800.0)
    assert MUTE.remaining(CHAT) == pytest.approx(1800.0, abs=2.0)


def test_a_ban_that_lapsed_during_the_downtime_is_not_restored(
    limiter, qa_clock, monkeypatch,
):
    """Criterion 16: an entry already in the past is dropped on restore —
    a restart must not resurrect yesterday's ban."""
    MUTE.mute(CHAT, 600.0)
    _restart(monkeypatch, qa_clock, downtime=601.0)
    assert not MUTE.is_muted(CHAT)


def test_the_startup_edit_is_refused_by_the_restored_mute(
    limiter, qa_clock, monkeypatch, run_async, make_gated_bot,
):
    """Row I's observable, named in D-6: ``_recover_busy_message`` issues
    an ``edit_message_text`` at startup, immediately after the place the
    old code cleared the mute. Under a restored mute it must be refused —
    this is the "zero Bot API calls during startup" assertion."""
    MUTE.mute(CHAT, BAN)
    limiter.note_ban(CHAT, BAN)
    fresh = _restart(monkeypatch, qa_clock)
    bot = make_gated_bot(fresh)
    with pytest.raises(FloodMuted):
        run_async(bot.edit_message_text("text", CHAT, 42))
    assert bot.calls == []


def test_the_runtime_signal_file_matches_the_restored_state(
    limiter, qa_clock, monkeypatch,
):
    """Row I's third assertion: ``initialize()`` no longer unlinks the
    signal, it refreshes it — otherwise ``aipager status`` would report a
    healthy bot through a 9.5-hour ban."""
    MUTE.mute(CHAT, BAN)
    limiter.note_ban(CHAT, BAN)
    _restart(monkeypatch, qa_clock)
    assert any(int(row["chat_id"]) == CHAT
               for row in status.read_flood_mutes())


# ===== criterion 16 — the clamp, and every malformed file ================

def test_a_deadline_exactly_at_the_clamp_is_kept(qa_clock):
    """Boundary value, just-inside ``FLOOD_MUTE_MAX_SECONDS``."""
    MUTE.mute(CHAT, config.FLOOD_MUTE_MAX_SECONDS)
    assert MUTE.remaining(CHAT) == pytest.approx(
        config.FLOOD_MUTE_MAX_SECONDS)


def test_a_deadline_past_the_clamp_is_cut_down(qa_clock):
    """Boundary value, just-outside: a wall-clock jump must not be able to
    mute the bot for ever (the stated risk of D-7)."""
    MUTE.mute(CHAT, config.FLOOD_MUTE_MAX_SECONDS + 3600)
    assert MUTE.remaining(CHAT) == pytest.approx(
        config.FLOOD_MUTE_MAX_SECONDS)


def test_a_restored_deadline_past_the_clamp_is_cut_down(qa_clock):
    """The same clamp on the OTHER entry point — a file written before a
    clock correction is exactly how an absurd deadline arrives."""
    MUTE.restore([{
        "chat_id": CHAT,
        "until": qa_clock.wall + 10 * config.FLOOD_MUTE_MAX_SECONDS,
        "retry_after": 10 * config.FLOOD_MUTE_MAX_SECONDS,
    }])
    assert MUTE.remaining(CHAT) <= config.FLOOD_MUTE_MAX_SECONDS


def test_a_restored_deadline_in_the_past_is_dropped(qa_clock):
    MUTE.restore([{"chat_id": CHAT, "until": qa_clock.wall - 1,
                   "retry_after": 600}])
    assert not MUTE.is_muted(CHAT)


def test_a_wall_clock_that_jumps_backwards_keeps_the_mute_armed(qa_clock):
    """Error guessing: NTP steps the clock backwards mid-ban. Whatever
    else happens, the ban must not be FORGOTTEN — the failure that costs
    9.5 hours is sending into it, not waiting too long."""
    MUTE.mute(CHAT, 600.0)
    qa_clock.rewind_wall(2 * config.FLOOD_MUTE_MAX_SECONDS)
    assert MUTE.is_muted(CHAT)


def test_a_restart_after_a_backwards_clock_jump_re_clamps_the_deadline(
    limiter, qa_clock, monkeypatch,
):
    """The mitigation the design actually promises for wall-clock skew:
    the clamp is applied on arm AND on restore, so a deadline that a
    backwards NTP step inflated past ``FLOOD_MUTE_MAX_SECONDS`` is cut
    back the next time the daemon starts.

    (Residual, and deliberately not asserted here because the design does
    not promise it: while the daemon keeps RUNNING, a backwards jump does
    stretch the live deadline.)
    """
    MUTE.mute(CHAT, 600.0)
    qa_clock.rewind_wall(2 * config.FLOOD_MUTE_MAX_SECONDS)
    _restart(monkeypatch, qa_clock)
    assert MUTE.remaining(CHAT) <= config.FLOOD_MUTE_MAX_SECONDS


@pytest.mark.parametrize("body", [
    "",
    "{",
    "null",
    "[]",
    '{"version": 99, "chats": [{"chat_id": -100, "rate": 0.05}]}',
    '{"version": 1}',
    '{"version": 1, "chats": "not-a-list"}',
    '{"version": 1, "chats": [42, null, {"no_chat_id": 1}]}',
])
def test_a_malformed_state_file_starts_fresh_and_raises_nothing(
    tmp_path, body,
):
    """Criterion 16 / the ``SessionRegistry.load`` contract. A daemon that
    cannot start because its flood file is half-written is worse than one
    that forgets a ban."""
    path = tmp_path / "broken.json"
    path.write_text(body)
    assert isinstance(flood_state.read(path=str(path)), dict)
    flood_state.load(path=str(path))          # must not raise
    assert not MUTE.is_muted(-100)


def test_a_missing_state_file_starts_fresh(tmp_path):
    missing = tmp_path / "nope" / "flood.json"
    assert flood_state.read(path=str(missing)) == {}
    assert flood_state.load(path=str(missing)) is False


def test_a_truncated_state_file_starts_fresh(tmp_path):
    """Error guessing: tmp+rename makes a torn file unlikely, not
    impossible (a full disk truncates the tmp write)."""
    full = json.dumps({
        "version": 1, "written_at": 1789000000.0,
        "chats": [{"chat_id": CHAT, "rate": 0.05,
                   "muted_until": 1789099999.0, "mute_retry_after": BAN}],
    })
    path = tmp_path / "torn.json"
    path.write_text(full[: len(full) // 2])
    flood_state.load(path=str(path))
    assert not MUTE.is_muted(CHAT)


def test_a_chat_id_stored_as_a_string_still_restores_the_mute(
    tmp_path, qa_clock,
):
    """Error guessing: the schema says ``chats`` is an array precisely so
    an int chat id never round-trips through a JSON string key — but a
    file written by an older or hand-edited writer can still carry one,
    and both ``_key`` functions are documented to int-parse."""
    path = tmp_path / "stringy.json"
    path.write_text(json.dumps({
        "version": 1, "written_at": qa_clock.wall,
        "chats": [{"chat_id": str(CHAT), "rate": 0.05,
                   "rate_earned_at": qa_clock.wall,
                   "muted_until": qa_clock.wall + 600,
                   "mute_retry_after": 600}],
    }))
    flood_state.load(path=str(path))
    assert MUTE.is_muted(CHAT)


def test_an_unknown_key_in_an_entry_is_ignored(tmp_path, qa_clock):
    """Forward compatibility: a newer daemon's extra field must not stop
    an older one from honouring the ban."""
    path = tmp_path / "future.json"
    path.write_text(json.dumps({
        "version": 1, "written_at": qa_clock.wall,
        "chats": [{"chat_id": CHAT, "rate": 0.05,
                   "rate_earned_at": qa_clock.wall,
                   "muted_until": qa_clock.wall + 600,
                   "mute_retry_after": 600,
                   "something_from_the_future": {"a": 1}}],
    }))
    flood_state.load(path=str(path))
    assert MUTE.is_muted(CHAT)


# ===== the write path ====================================================

def test_nothing_is_written_when_nothing_is_dirty():
    """The debounce contract: an idle daemon must not rewrite the file
    every two seconds."""
    flood_state.clear()
    assert flood_state.save_if_dirty() is False


def test_a_second_save_inside_the_minimum_interval_is_throttled():
    """``FLOOD_STATE_MIN_INTERVAL`` asserted by NAME."""
    assert config.FLOOD_STATE_MIN_INTERVAL > 0
    MUTE.mute(CHAT, BAN)
    flood_state.mark_dirty()
    assert flood_state.save_if_dirty(force=True) is True
    flood_state.mark_dirty()
    assert flood_state.save_if_dirty() is False


def test_force_beats_the_throttle(qa_clock):
    """``stop()`` forces a write; losing the ban because the daemon was
    shut down 4 seconds after the last save would be the whole defect
    back again."""
    MUTE.mute(CHAT, BAN)
    flood_state.mark_dirty()
    flood_state.save_if_dirty(force=True)
    flood_state.mark_dirty()
    assert flood_state.save_if_dirty(force=True) is True


def test_an_unwritable_path_never_reaches_a_send_path(tmp_path):
    """A full disk (or a file where the directory should be) must never
    surface to a caller — the shape ``flood._write_signal`` already has."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    MUTE.mute(CHAT, BAN)
    flood_state.mark_dirty()
    flood_state.save_if_dirty(path=str(blocker / "nested" / "state.json"),
                              force=True)   # must not raise


def test_the_written_document_carries_the_documented_schema(qa_clock):
    """The file is read by another PROCESS (``aipager status``), so its
    shape is a contract, not an implementation detail."""
    MUTE.mute(CHAT, BAN)
    flood_state.mark_dirty()
    flood_state.save_if_dirty(force=True)
    doc = json.loads(_state_path().read_text())
    assert doc["version"] == 1 and isinstance(doc["chats"], list)


def test_chat_ids_survive_the_round_trip_as_integers(qa_clock):
    """The reason ``chats`` is an array: a JSON object key would turn
    ``-1001234567`` into ``"-1001234567"`` and split every chat in two."""
    MUTE.mute(CHAT, BAN)
    flood_state.mark_dirty()
    flood_state.save_if_dirty(force=True)
    doc = json.loads(_state_path().read_text())
    assert [c["chat_id"] for c in doc["chats"]] == [CHAT]


def test_the_volatile_counters_are_ignored_on_load(
    limiter, qa_clock, monkeypatch, tmp_path,
):
    """``sustained_used`` / ``sustained_limit`` / ``minimal`` are written
    for the status reader and documented as ignored on load: restoring a
    used window from a file written hours ago would throttle a healthy
    chat for no reason."""
    path = tmp_path / "volatile.json"
    path.write_text(json.dumps({
        "version": 1, "written_at": qa_clock.wall,
        "chats": [{"chat_id": CHAT, "rate": config.FLOOD_START_RATE,
                   "rate_earned_at": qa_clock.wall, "sustained_used": 29,
                   "sustained_limit": 30, "minimal": True}],
    }))
    fresh = BudgetRateLimiter(clock=qa_clock, sleep=qa_clock.sleep,
                              signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(fresh)
    flood_state.load(path=str(path))
    assert fresh.sustained_used(CHAT) == 0
    fresh.reset()
