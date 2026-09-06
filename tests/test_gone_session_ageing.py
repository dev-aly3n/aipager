"""Gone sessions age out of the registry (roadmap 8.12).

``MAX_GONE_HISTORY`` only caps how MANY gone entries the registry keeps;
a session that died weeks ago sat in ``/resume`` and the dashboard until
fifty newer ones pushed it out, and on 2026-09-06 twenty-four throwaway
sessions had to be pruned by hand. Now ``SessionRegistry.expire_gone``
drops a GONE entry whose ``gone_at`` is older than
``GONE_SESSION_MAX_AGE_DAYS`` (default 14, ``0`` disables), at load and
once per monitor tick. An entry with no stamp is never aged — there is
nothing to measure — and the count cap is untouched.

No real Telegram, dtach or claude; save/load goes through
``tmp_state_file``.
"""

from __future__ import annotations

import logging
import time

from aipager import config, state as state_mod
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession

DAY = 86400.0


async def _coroutine_returning(value):
    return value


def _gone(registry: SessionRegistry, name: str, *, age_days: float | None, now: float) -> TrackedSession:
    sess = TrackedSession(name=name, label=name.removeprefix("claude-"), status=Status.GONE)
    sess.gone_at = None if age_days is None else now - age_days * DAY
    registry._sessions[name] = sess
    return sess


def _seed(registry: SessionRegistry, now: float):
    old = _gone(registry, "claude-old", age_days=15, now=now)
    recent = _gone(registry, "claude-recent", age_days=13, now=now)
    live = TrackedSession(name="claude-live", label="live", status=Status.BUSY)
    registry._sessions[live.name] = live
    unstamped = _gone(registry, "claude-unstamped", age_days=None, now=now)
    registry.track_message(101, "claude-old", 7)
    registry.track_message(102, "claude-recent", 7)
    return old, recent, live, unstamped


def test_expire_gone_drops_only_entries_older_than_the_age(caplog):
    registry = SessionRegistry()
    now = time.time()
    _seed(registry, now)
    registry._dirty = False

    with caplog.at_level(logging.INFO, logger="aipager.state"):
        dropped = registry.expire_gone(now)

    assert dropped == ["claude-old"]
    assert set(registry.all_sessions()) == {"claude-recent", "claude-live", "claude-unstamped"}
    assert (7, 101) not in registry._msg_map  # remove() scrubbed the map
    assert registry.get_session_by_msg(102, 7).name == "claude-recent"
    assert registry._dirty is True
    assert any("Dropping gone session claude-old" in r.getMessage() and "15d ago" in r.getMessage()
               for r in caplog.records if r.levelno == logging.INFO)


def test_expire_gone_is_a_no_op_when_nothing_is_old_enough():
    registry = SessionRegistry()
    now = time.time()
    _gone(registry, "claude-recent", age_days=13, now=now)
    registry._dirty = False

    assert registry.expire_gone(now) == []
    assert "claude-recent" in registry.all_sessions()
    assert registry._dirty is False


def test_zero_or_negative_age_disables_ageing(monkeypatch):
    registry = SessionRegistry()
    now = time.time()
    _gone(registry, "claude-ancient", age_days=400, now=now)
    for value in (0.0, -1.0):
        monkeypatch.setattr(state_mod, "GONE_SESSION_MAX_AGE_DAYS", value)
        assert registry.expire_gone(now) == []
    assert "claude-ancient" in registry.all_sessions()


def test_default_age_is_fourteen_days():
    assert config.GONE_SESSION_MAX_AGE_DAYS == 14


def test_load_drops_an_aged_entry_from_the_file(tmp_state_file, caplog):
    registry = SessionRegistry()
    now = time.time()
    _seed(registry, now)
    registry.save()

    loaded = SessionRegistry()
    with caplog.at_level(logging.INFO, logger="aipager.state"):
        loaded.load()

    assert "claude-old" not in loaded.all_sessions()
    assert "claude-recent" in loaded.all_sessions()
    assert "claude-live" in loaded.all_sessions()
    assert (7, 101) not in loaded._msg_map
    assert loaded.get_session_by_msg(102, 7).name == "claude-recent"


def test_last_active_session_pointing_at_a_dropped_entry_is_cleared():
    registry = SessionRegistry()
    now = time.time()
    _seed(registry, now)
    registry.last_active_session = "claude-old"

    registry.expire_gone(now)

    assert registry.last_active_session == ""  # the unset value, not None


def test_monitor_scan_ages_out_gone_entries(monkeypatch, run_async):
    registry = SessionRegistry()
    now = time.time()
    old, recent, live, _unstamped = _seed(registry, now)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(["claude-live"]),
    )

    async def _noop(*a, **kw):
        return None
    monitor = SessionMonitor(registry, _noop)

    run_async(monitor._scan())

    assert "claude-old" not in registry.all_sessions()
    assert "claude-recent" in registry.all_sessions()
    assert "claude-unstamped" in registry.all_sessions()


def test_count_cap_still_applies_independently():
    """The LRU cap keeps working for entries that are recent but many."""
    registry = SessionRegistry()
    now = time.time()
    for i in range(state_mod.MAX_GONE_HISTORY + 3):
        _gone(registry, f"claude-g{i}", age_days=1, now=now - i)
    registry.get_or_create("claude-newcomer")
    gone = [s for s in registry.all_sessions().values() if s.status == Status.GONE]
    assert len(gone) == state_mod.MAX_GONE_HISTORY
    assert registry.expire_gone(now) == []


def test_exact_cutoff_and_future_stamps_survive():
    """Strict comparison: an entry exactly at the cutoff lives one more
    tick; a stamp in the future (clock skew) is never older than anything."""
    registry = SessionRegistry()
    now = time.time()
    at_cutoff = _gone(registry, "claude-edge", age_days=None, now=now)
    at_cutoff.gone_at = now - state_mod.GONE_SESSION_MAX_AGE_DAYS * DAY
    future = _gone(registry, "claude-future", age_days=None, now=now)
    future.gone_at = now + DAY

    assert registry.expire_gone(now) == []
    assert {"claude-edge", "claude-future"} <= set(registry.all_sessions())


def test_hidden_gone_entries_age_out_like_any_other():
    registry = SessionRegistry()
    now = time.time()
    hidden = _gone(registry, "claude-hidden", age_days=20, now=now)
    hidden.hidden_from_status = True

    assert registry.expire_gone(now) == ["claude-hidden"]
