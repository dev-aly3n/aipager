"""Problem reports, step 1b (roadmap 8.112): the local store, the log
handler that feeds it, the crash and install markers, and their wiring
into ``aipager start`` and the session monitor.

The privacy promise holds on disk as well as on the wire: no canary from
a log message, its arguments, ``extra``, an exception or its locals may
reach reports.json, and a hostile reports.json is cleaned on read.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import errno
import json
import logging
import os
import random
import socket
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from telegram import error as tg

from aipager import config
from aipager.report import builder, capture, markers, runtime, store
from aipager.report import fingerprint as fp
from aipager.report import schema as sc
from tests.test_problem_report_privacy import (
    CANARIES, CHAT, CWD, LABEL, PROMPT, TOKEN, USERNAME, _make_exception, assert_no_leak,
)

#: The store keeps days of history against the real clock, so the tests'
#: clock is the real one, at the middle of its hour (no hour boundary).
NOW = int(time.time()) // 3600 * 3600 + 1800


def _raise_in(module: str, make, filename=None):
    """*make()* raised from a function whose frame is in *module* (an
    aipager module's frame counts only with an aipager file name)."""
    namespace = {"__name__": module, "make": make,
                 "token": TOKEN, "prompt": PROMPT}
    file = filename or str(fp.PACKAGE_DIR / "state.py")
    exec(compile("def boom():\n    secret = token + prompt\n    raise make()\n", file, "exec"),
         namespace)
    try:
        namespace["boom"]()
    except BaseException as exc:  # noqa: BLE001 - the exception is the input
        return exc
    raise AssertionError("did not raise")


def _ours(make=lambda: ValueError("x")):
    return _raise_in("aipager.state", make)


def _file_text() -> str:
    path = config.REPORTS_FILE
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _file_without_timestamps() -> str:
    """The store file less its typed unix timestamps (local only, never in
    a report), so the generic "no 7+ digit number" check applies to the
    rest."""
    text = _file_text()
    if not text:
        return ""
    document = json.loads(text)
    for record in document.get("errors", []):
        for key in ("first_ts", "last_ts", "occasion_ts"):
            assert type(record.pop(key)) is int
    document.get("exits", {}).pop("unclean", None)
    return json.dumps(document)


# ---- the store: recording ----------------------------------------------------

def test_an_error_in_aipager_is_recorded_by_fingerprint_as_a_bug():
    exc = _ours()
    key = store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW)
    assert key == fp.fingerprint(exc)
    [entry] = store.errors()
    assert entry["tier"] == "bug" and entry["type"] == "builtins.ValueError"
    assert entry["frames"][0]["file"] == "aipager/state.py"
    assert entry["count"] == 1 and entry["first_day"] == entry["last_day"] == store._day(NOW)


def test_an_error_with_no_aipager_frame_is_an_anomaly():
    exc = _raise_in("somelib.core", lambda: ValueError("x"), filename="/srv/somelib/core.py")
    store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW)
    assert store.errors()[0]["tier"] == "anomaly"


def test_repeats_count_and_occasions_are_at_least_ten_minutes_apart():
    exc = _ours()
    for offset in (0, 60, 599, 600, 650, 1300):
        store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW + offset)
    [record] = store.records()
    assert record["entry"]["count"] == 6
    assert record["occasions"] == 3            # at 0, 600, 1300
    assert (record["first_ts"], record["last_ts"]) == (NOW, NOW + 1300)


def test_a_bug_stays_a_bug():
    exc = _ours()
    store.record_exception(exc, where="daemon", trigger="log_exception", tier="anomaly", now=NOW)
    store.record_exception(exc, where="daemon", trigger="log_exception", tier="bug", now=NOW)
    store.record_exception(exc, where="daemon", trigger="log_exception", tier="anomaly", now=NOW)
    assert store.errors()[0]["tier"] == "bug"


@pytest.mark.parametrize(("make", "counter"), [
    (lambda: OSError(errno.ENOSPC, "full"), "env_disk_full"),
    (lambda: OSError(errno.EDQUOT, "quota"), "env_disk_full"),
    (lambda: OSError(errno.EROFS, "ro"), "env_read_only"),
    (lambda: PermissionError(errno.EACCES, "no"), "env_permission"),
    (lambda: MemoryError(), "env_no_memory"),
    (lambda: ConnectionResetError(errno.ECONNRESET, "reset"), "env_network"),
    (lambda: socket.gaierror(-2, "name"), "env_network"),
    (lambda: httpx.ReadError("x"), "env_network"),
    (lambda: tg.NetworkError("x"), "tg_network"),
    (lambda: tg.TimedOut(), "tg_network"),
    (lambda: tg.RetryAfter(5), "tg_retry_after"),
    (lambda: tg.Conflict("x"), "tg_conflict"),
    (lambda: tg.Forbidden("x"), "tg_forbidden"),
    (lambda: tg.InvalidToken(), "tg_invalid_token"),
    (lambda: TimeoutError(), "env_timeout"),
    (lambda: asyncio.TimeoutError(), "env_timeout"),
    (lambda: asyncio.CancelledError(), "env_cancelled"),
    (lambda: concurrent.futures.TimeoutError(), "env_timeout")])
def test_an_environment_error_is_only_counted(make, counter):
    store.record_exception(_ours(make), where="daemon", trigger="log_exception", now=NOW)
    assert store.errors() == []
    assert store.counters_24h(now=NOW) == {counter: 1}


def test_an_environment_error_is_never_fingerprinted(monkeypatch):
    built = []
    real = builder.error_entry
    monkeypatch.setattr(builder, "error_entry", lambda *a, **k: built.append(1) or real(*a, **k))
    store.record_exception(_ours(lambda: tg.TimedOut()), where="daemon",
                           trigger="log_exception", now=NOW)
    assert built == []
    store.record_exception(_ours(), where="daemon", trigger="log_exception", now=NOW)
    assert built == [1]


def test_eperm_is_no_environment_error():
    """EPERM is aipager acting on something not its own (a pid, a file
    mode), not the user's setup."""
    store.record_exception(_ours(lambda: PermissionError(errno.EPERM, "x")), where="daemon",
                           trigger="log_exception", now=NOW)
    assert store.errors()[0]["tier"] == "bug" and store.counters_24h(now=NOW) == {}


@pytest.mark.parametrize("make", [lambda: tg.NetworkError("Bad Gateway"),
                                  lambda: OSError(errno.ENOSPC, "full"),
                                  lambda: TimeoutError()])
def test_an_error_that_escaped_a_task_is_a_missing_guard_whatever_its_type(make):
    store.record_exception(_ours(make), where="daemon", trigger="task", now=NOW)
    assert store.errors()[0]["tier"] == "bug" and store.counters_24h(now=NOW) == {}
    outside = _raise_in("somelib.core", make, filename="/srv/somelib/core.py")
    store.record_exception(outside, where="daemon", trigger="task", now=NOW)
    assert store.errors()[0]["tier"] == "anomaly"


@pytest.mark.parametrize("make", [lambda: tg.NetworkError("Bad Gateway"),
                                  lambda: OSError(errno.ENOSPC, "full")])
def test_an_error_that_ended_the_daemon_is_a_bug_whatever_its_type(make):
    outside = _raise_in("somelib.core", make, filename="/srv/somelib/core.py")
    store.record_exception(outside, where="daemon", trigger="crash", now=NOW)
    assert store.errors()[0]["tier"] == "bug" and store.counters_24h(now=NOW) == {}


def test_a_bad_request_is_never_weather():
    """A 400 is usually aipager's own malformed request, though PTB makes
    it a NetworkError subclass."""
    store.record_exception(_ours(lambda: tg.BadRequest("can't parse entities")),
                           where="daemon", trigger="log_exception", now=NOW)
    assert store.errors()[0]["type"] == "telegram.error.BadRequest"
    assert store.counters_24h(now=NOW) == {}


def test_at_most_fifty_fingerprints_and_the_least_recent_goes():
    for i in range(store.MAX_FINGERPRINTS + 1):
        store.record_site("log_error", file="aipager/state.py", line=i, fn=f"f{i}",
                          where="daemon", trigger="log_error", tier="anomaly", now=NOW + i)
    fns = [r["entry"]["frames"][0]["fn"] for r in store.records()]
    assert len(fns) == store.MAX_FINGERPRINTS and "f0" not in fns and fns[-1] == "f50"
    store.record_site("log_error", file="aipager/state.py", line=1, fn="f1", where="daemon",
                      trigger="log_error", tier="anomaly", now=NOW + 99)
    store.record_site("log_error", file="aipager/state.py", line=1, fn="g", where="daemon",
                      trigger="log_error", tier="anomaly", now=NOW + 100)
    fns = [r["entry"]["frames"][0]["fn"] for r in store.records()]
    assert "f1" in fns and "f2" not in fns    # touched again, so not the least recent


def test_the_versions_seen_are_the_last_five_distinct(monkeypatch):
    import aipager
    exc = _ours()
    for version in ("0.8.0", "0.8.1", "0.8.0", "0.8.2", "0.8.3", "0.8.4", "0.8.5"):
        monkeypatch.setattr(aipager, "__version__", version)
        store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW)
    # 0.8.0 seen again moves to the end, so 0.8.1 is the oldest and goes.
    assert store.errors()[0]["versions_seen"] == ["0.8.0", "0.8.2", "0.8.3", "0.8.4", "0.8.5"]
    store.reset()
    for _ in range(3):
        store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW)
    assert store.errors()[0]["versions_seen"] == ["0.8.5"]


def test_a_call_site_event_is_keyed_by_kind_and_site_only():
    key = store.record_site("log_error", file="aipager/state.py", line=12, fn="save",
                            where="daemon", trigger="log_error", tier="anomaly", now=NOW)
    assert key == fp.site_fingerprint("log_error", "aipager/state.py", "save")
    entry = store.errors()[0]
    assert entry["type"] is None and entry["frames"] == [
        {"file": "aipager/state.py", "line": 12, "fn": "save"}]
    for bad in ({"file": f"{CWD}/x.py"}, {"fn": USERNAME + " x"}, {"line": -1},
                {"file": "aipager/cnryuser.py"}):
        args = {"file": "aipager/state.py", "line": 1, "fn": "f", **bad}
        assert store.record_site("log_error", **args, where="daemon", trigger="log_error",
                                 tier="anomaly", now=NOW) is None
    assert store.record_site(LABEL, file="aipager/state.py", line=1, fn="f", where="daemon",
                             trigger="log_error", tier="anomaly", now=NOW) is None


def test_counters_are_hourly_fixed_keys_over_the_last_day():
    store.record_counter("tg_5xx", now=NOW - 25 * 3600)
    store.record_counter("tg_5xx", 2, now=NOW - 23 * 3600)
    store.record_counter("tg_5xx", now=NOW)
    for key, n in (("cnry_key", 1), ("tg_5xx", 0), ("tg_5xx", -3), ("tg_5xx", True)):
        store.record_counter(key, n, now=NOW)
    assert store.counters_24h(now=NOW) == {"tg_5xx": 3}


def test_the_digest_keeps_only_shipped_sites_and_known_levels():
    for site, level in ((f"{CWD}/x.py:1", "ERROR"), ("aipager/state.py:1", "INFO"),
                        ("aipager/cnryuser.py:1", "ERROR"), ("aipager/state.py:x", "ERROR")):
        store.record_digest(site, level, now=NOW)
    assert store.digest_24h(now=NOW) == []


def test_the_digest_counts_sites_and_levels_with_a_cap_per_hour():
    for _ in range(3):
        store.record_digest("aipager/state.py:12", "ERROR", now=NOW)
    for i in range(store.DIGEST_SITES_PER_HOUR + 5):
        store.record_digest(f"aipager/state.py:{100 + i}", "WARNING", now=NOW)
    store.record_digest("aipager/state.py:12", "ERROR", now=NOW)   # an existing site still counts
    for site, level in ((f"{CWD}/x.py:1", "ERROR"), ("aipager/state.py:1", "INFO"),
                        ("aipager/cnryuser.py:1", "ERROR")):
        store.record_digest(site, level, now=NOW)
    rows = store.digest_24h(now=NOW)
    assert rows[0] == {"site": "aipager/state.py:12", "level": "ERROR", "n": 4}
    assert len(rows) == store.DIGEST_SITES_PER_HOUR
    assert store.digest_24h(now=NOW + 25 * 3600) == []


def test_exits_are_noted_and_unclean_ones_counted_for_a_week():
    store.note_exit("crash", now=NOW - 8 * 86400)
    store.note_exit("crash", now=NOW - 86400)
    store.note_exit("crash", now=NOW)
    store.note_exit("clean", now=NOW)
    store.note_exit("cnry", now=NOW)
    assert store.exit_facts(now=NOW) == (2, "clean")


def test_the_recorders_never_raise(monkeypatch):
    assert store.record_exception(None, where="daemon", trigger="x", now=NOW) is None
    store.record_counter(None)
    store.record_digest(None, None)
    store.note_exit(None)
    monkeypatch.setattr(builder, "error_entry", lambda *a, **k: 1 / 0)
    assert store.record_exception(_ours(), where="daemon", trigger="log_exception") is None
    assert store.errors() == []


def test_an_entry_the_schema_refuses_is_not_stored():
    exc = _ours()
    assert store.record_exception(exc, where=f"{CWD}", trigger="log_exception", now=NOW) is None
    assert store.errors() == []


# ---- the store: the file -----------------------------------------------------

def _fill():
    store.record_exception(_ours(), where="daemon", trigger="log_exception", now=NOW)
    store.record_counter("tg_5xx", 2, now=NOW)
    store.record_digest("aipager/state.py:12", "ERROR", now=NOW)
    store.note_exit("crash", now=NOW)


def test_the_file_round_trips(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    _fill()
    before = (store.records(), store.counters_24h(now=NOW), store.digest_24h(now=NOW),
              store.exit_facts(now=NOW))
    assert store.save_if_dirty(force=True)
    store.reset()
    after = (store.records(), store.counters_24h(now=NOW), store.digest_24h(now=NOW),
             store.exit_facts(now=NOW))
    assert after == before
    assert not config.REPORTS_FILE.with_name("reports.json.tmp").exists()


def test_old_hours_and_exits_are_trimmed_when_the_file_is_read(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    store.record_counter("tg_5xx", now=NOW - 9 * 86400)
    store.record_counter("tg_5xx", now=NOW - 7 * 86400)
    store.record_digest("aipager/state.py:1", "ERROR", now=NOW - 3 * 86400)
    store.record_digest("aipager/state.py:2", "ERROR", now=NOW - 86400)
    store.note_exit("crash", now=NOW - 8 * 86400)
    store.save_if_dirty(force=True)
    document = json.loads(_file_text())
    assert len(document["counters"]) == 1 and len(document["digest"]) == 1
    assert document["exits"]["unclean"] == []


@pytest.mark.parametrize("content", [
    b"not json", b"[]", b'{"version": 1, "errors": NaN}',
    b"[" * 50_000, b"\xff\xfe\x00garbage", b'{"version": 1, "errors": {"a": 1}}',
    b'{"version": 1, "counters": [1], "digest": "x", "exits": 5}'])
def test_a_bad_file_reads_as_empty_and_never_raises(content):
    config.REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REPORTS_FILE.write_bytes(content)
    assert store.records() == [] and store.counters_24h(now=NOW) == {}
    assert store.exit_facts(now=NOW) == (0, "unknown")


def test_a_file_of_another_version_is_ignored(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    _fill()
    store.save_if_dirty(force=True)
    document = json.loads(_file_text())
    document["version"] = 2
    config.REPORTS_FILE.write_text(json.dumps(document))
    store.reset()
    assert store.records() == [] and store.exit_facts(now=NOW) == (0, "unknown")


def test_non_finite_numbers_are_never_counts():
    config.REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    hour = store._hour(NOW)
    config.REPORTS_FILE.write_text(
        '{"version": 1, "counters": {"%s": {"tg_5xx": NaN, "tg_network": Infinity}},'
        ' "exits": {"unclean": [NaN, -Infinity], "last": "crash"}}' % hour)
    assert store.counters_24h(now=NOW) == {} and store.exit_facts(now=NOW) == (0, "crash")


def test_a_count_stops_at_the_cap(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    exc = _ours()
    store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW)
    store.save_if_dirty(force=True)
    document = json.loads(_file_text())
    document["errors"][0]["entry"]["count"] = store.COUNT_MAX
    config.REPORTS_FILE.write_text(json.dumps(document))
    store.reset()
    store.record_exception(exc, where="daemon", trigger="log_exception", now=NOW + 700)
    [record] = store.records()
    assert record["entry"]["count"] == store.COUNT_MAX
    assert (record["last_ts"], record["occasions"]) == (NOW + 700, 2)   # still updated


def test_a_file_that_breaks_half_way_through_leaves_nothing(monkeypatch):
    _fill()
    store.save_if_dirty(force=True)
    store.reset()
    real = store._adopt

    def _half(document):
        real(document)
        raise RuntimeError("half way")

    monkeypatch.setattr(store, "_adopt", _half)
    assert store.records() == [] and store.exit_facts(now=NOW) == (0, "unknown")


def test_the_store_file_is_private():
    _fill()
    store.save_if_dirty(force=True)
    assert config.REPORTS_FILE.stat().st_mode & 0o777 == 0o600


def test_a_file_over_the_size_cap_is_ignored():
    config.REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "errors": [], "pad": "x" * store.MAX_FILE_BYTES,
                "exits": {"unclean": [], "last": "crash"}}
    config.REPORTS_FILE.write_text(json.dumps(document))
    assert store.exit_facts(now=NOW) == (0, "unknown")


def test_a_hostile_file_keeps_only_what_the_schema_allows(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    _fill()
    store.save_if_dirty(force=True)
    document = json.loads(_file_text())
    good = document["errors"][0]
    hostile = [
        dict(good, entry=dict(good["entry"], fingerprint="ap1-000000000001", logger=LABEL)),
        dict(good, entry=dict(good["entry"], fingerprint="ap1-000000000002",
                              frames=[{"file": f"{CWD}/x.py", "line": 1, "fn": "f"}])),
        dict(good, entry=dict(good["entry"], fingerprint="ap1-000000000003"), note=PROMPT),
        dict(good, entry=dict(good["entry"], fingerprint="ap1-000000000004"), first_ts=CHAT),
        dict(good, entry=dict(good["entry"], fingerprint="ap1-000000000005"), occasions=0),
        dict(good, entry={k: v for k, v in good["entry"].items() if k != "tier"}
                    | {"fingerprint": "ap1-000000000006"}),
        {"entry": TOKEN}, USERNAME, None]
    document["errors"] = hostile + [good]
    document["counters"][LABEL] = {"tg_5xx": 1}      # sorts after every real hour
    next(iter(document["counters"].values()))[USERNAME] = 3
    document["digest"]["2026-10-07T05"] = [{"site": f"{CWD}/x.py:1", "level": "ERROR", "n": 1},
                                           {"site": "aipager/state.py:3", "level": "ERROR",
                                            "n": 2, "msg": PROMPT}]
    document["exits"] = {"unclean": [CHAT, NOW, "x"], "last": LABEL, "who": USERNAME}
    document[USERNAME] = TOKEN
    config.REPORTS_FILE.write_text(json.dumps(document))
    store.reset()
    assert [r["entry"]["fingerprint"] for r in store.records()] == [good["entry"]["fingerprint"]]
    store.record_counter("tg_5xx", now=NOW)   # dirty, so the clean copy is written
    store.save_if_dirty(force=True)
    assert_no_leak(_file_without_timestamps())
    assert sc.INVALID not in _file_text()
    assert store.exit_facts(now=NOW) == (1, "unknown")   # the bogus last exit was dropped


def test_saving_is_debounced_unless_forced(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])
    assert store.save_if_dirty() is False            # nothing to write
    store.record_counter("tg_5xx", now=NOW)
    assert store.save_if_dirty() is True
    store.record_counter("tg_5xx", now=NOW)
    clock[0] += store.MIN_SAVE_INTERVAL - 1
    assert store.save_if_dirty() is False
    assert store.save_if_dirty(force=True) is True
    store.record_counter("tg_5xx", now=NOW)
    clock[0] += store.MIN_SAVE_INTERVAL
    assert store.save_if_dirty() is True


def test_a_failed_write_keeps_the_old_file_and_never_raises(monkeypatch):
    _fill()
    store.save_if_dirty(force=True)
    before = _file_text()
    store.record_counter("tg_5xx", now=NOW)

    real_replace, failures = os.replace, []

    def _fails_once(*a, **k):
        if not failures:
            failures.append(1)
            raise OSError(errno.ENOSPC, "full")
        return real_replace(*a, **k)

    # Never monkeypatch.undo() here: it would also undo conftest's redirect
    # of the store's path into tmp.
    monkeypatch.setattr(os, "replace", _fails_once)
    assert store.save_if_dirty(force=True) is False
    assert _file_text() == before
    assert store.save_if_dirty(force=True) is True     # still dirty, written next time


def test_every_write_is_checked_against_the_real_home(monkeypatch):
    checked = []
    monkeypatch.setattr(store, "check_write", lambda target: checked.append(target))
    _fill()
    store.save_if_dirty(force=True)
    assert checked == [config.REPORTS_FILE]


# ---- the log handler -----------------------------------------------------------

@pytest.fixture
def handler():
    capture.install()
    yield
    capture.uninstall()


# Log lines are emitted from a frame compiled as aipager/state.py, so the
# record's call site is aipager's (logging takes it from the caller frame).
_EMIT = {"__name__": "aipager.state"}
exec(compile("def emit_line(logger, level, msg, args, exc, extra):\n"
             "    logger.log(level, msg, *args, exc_info=exc, extra=extra)\n",
             str(fp.PACKAGE_DIR / "state.py"), "exec"), _EMIT)


def _log(name, level, msg, *args, exc=None, extra=None):
    _EMIT["emit_line"](logging.getLogger(name), level, msg, args, exc, extra)


def test_the_handler_records_shapes_and_never_text(handler, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    exc = _ours(lambda: RuntimeError(f"chat {CHAT} {TOKEN} {PROMPT}"))
    exc.__notes__ = [f"{USERNAME} {CWD}"]   # what add_note() sets (3.11+)
    _log("aipager.state", logging.ERROR, "failed for %s in %s (%s)", LABEL, CWD, TOKEN,
         exc=exc, extra={"label": LABEL, "prompt": PROMPT})
    _log("aipager.bot.notify", logging.WARNING, f"user {USERNAME} said {PROMPT}")
    _log("aipager.state", logging.ERROR, "chat %s", CHAT)
    line, bug = sorted(store.errors(), key=lambda e: e["trigger"])
    assert bug["type"] == "builtins.RuntimeError" and bug["trigger"] == "log_exception"
    assert line["type"] is None and line["tier"] == "anomaly" and line["trigger"] == "log_error"
    store.save_if_dirty(force=True)
    assert_no_leak(_file_without_timestamps())
    report = builder.build_report("manual", errors=store.errors(),
                                  counters=store.counters_24h(), log_digest=store.digest_24h())
    assert_no_leak(builder.render_preview(report))
    assert sc.INVALID not in builder.render_preview(report)


@pytest.mark.parametrize("seed", range(200))
def test_seeded_log_records_never_leak_to_disk_or_report(handler, monkeypatch, seed):
    monkeypatch.setattr(time, "time", lambda: NOW)
    rng = random.Random(seed)
    for _ in range(rng.randrange(1, 6)):
        canary = rng.choice(CANARIES)
        name = rng.choice(["aipager.state", "aipager.bot.notify", "asyncio",
                           "telegram.ext.Updater", "aipager"])
        level = rng.choice([logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL])
        exc = _ours(lambda: _make_exception(rng)) if rng.random() < 0.7 else None
        _log(name, level, f"{canary} %s %r", canary, {"p": PROMPT}, exc=exc,
             extra={"cnry_extra": canary})
    store.save_if_dirty(force=True)
    assert_no_leak(_file_without_timestamps())
    report = builder.build_report("auto", errors=store.errors(), counters=store.counters_24h(),
                                  log_digest=store.digest_24h())
    assert_no_leak(builder.render_preview(report))
    assert sc.INVALID not in json.dumps(report)


def test_levels_decide_what_is_recorded(handler, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    _log("aipager.state", logging.INFO, "info", exc=_ours())
    assert store.errors() == [] and store.digest_24h() == []
    _log("aipager.state", logging.WARNING, "a warning")
    assert store.errors() == [] and store.digest_24h()[0]["level"] == "WARNING"
    _log("aipager.state", logging.WARNING, "a warning with an error", exc=_ours())
    assert len(store.errors()) == 1
    _log("asyncio", logging.ERROR, "not ours, no exception")
    assert len(store.errors()) == 1
    _log("aipager.state", logging.CRITICAL, "critical")
    assert {r["level"] for r in store.digest_24h()} == {"WARNING", "CRITICAL"}
    assert store.errors()[0]["tier"] == "anomaly"


def test_capture_ignores_a_record_below_warning_even_without_the_handler_level():
    record = logging.LogRecord("aipager.state", logging.INFO, str(fp.PACKAGE_DIR / "state.py"),
                               3, "m", (), None, func="save")
    record.exc_info = (ValueError, _ours(), None)
    capture.capture(record)
    assert store.errors() == [] and store.digest_24h() == []


def test_the_trigger_names_the_capture_point(handler, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    _log("asyncio", logging.ERROR, "Task exception was never retrieved",
         exc=_ours(lambda: KeyError("a")))
    _log("aipager.state", logging.CRITICAL, "fatal", exc=_ours(lambda: TypeError("b")),
         extra={capture.TRIGGER_ATTR: "crash"})
    _log("aipager.state", logging.ERROR, "odd", exc=_ours(lambda: IndexError("c")),
         extra={capture.TRIGGER_ATTR: LABEL})
    record = logging.LogRecord("aipager.bot.lifecycle", logging.ERROR,
                               str(fp.PACKAGE_DIR / "bot" / "lifecycle.py"), 370,
                               "Unhandled Telegram error", (), None,
                               func="_on_telegram_error")
    record.exc_info = (LookupError, _ours(lambda: LookupError("d")), None)
    logging.getLogger("aipager.bot.lifecycle").handle(record)
    by_type = {e["type"]: e["trigger"] for e in store.errors()}
    assert by_type == {"builtins.KeyError": "task", "builtins.TypeError": "crash",
                       "builtins.IndexError": "log_exception",
                       "builtins.LookupError": "ptb_handler"}


def test_an_odd_logger_name_is_left_out_not_the_record(handler):
    _log("telegram.ext.cnry7770001234", logging.ERROR, "x", exc=_ours())
    _log("telegram.ext.CnryUser", logging.ERROR, "x", exc=_ours(lambda: IndexError("i")))
    _log("telegram.ext.Updater", logging.ERROR, "z", exc=_ours(lambda: TypeError("t")))
    _log("aipager.state", logging.ERROR, "y", exc=_ours(lambda: KeyError("k")))
    assert sorted((e["logger"] or "-") for e in store.errors()) == [
        "-", "-", "aipager.state", "telegram.ext.Updater"]


@pytest.mark.parametrize("change", [
    {"exc_info": "x"}, {"exc_info": (1, 2)}, {"exc_info": (1, 2, 3)}, {"levelno": "40"},
    {"name": None}, {"name": 5}, {"pathname": 5}, {"lineno": "3"}, {"funcName": None},
    {"levelno": 10**9}, {"aipager_report_trigger": ["crash"]}])
def test_a_malformed_record_never_raises(change):
    """``capture()`` itself, without the handler's backstop; whatever it
    records is the record's own call site."""
    record = logging.LogRecord("aipager.state", logging.ERROR, str(fp.PACKAGE_DIR / "state.py"),
                               3, "m", (), None, func="save")
    record.__dict__.update(change)
    capture.capture(record)
    assert all(e["frames"][0]["fn"] == "save" for e in store.errors())


def test_the_handler_does_not_recurse(handler, monkeypatch):
    calls = []
    real = store.record_exception

    def _logs_itself(*a, **k):
        calls.append(1)
        logging.getLogger("aipager.report.store").error("inside", exc_info=_ours())
        return real(*a, **k)

    monkeypatch.setattr(store, "record_exception", _logs_itself)
    _log("aipager.state", logging.ERROR, "outside", exc=_ours())
    assert calls == [1]


def test_the_handler_swallows_its_own_failures(handler, monkeypatch, capsys):
    monkeypatch.setattr(capture, "capture", lambda record: 1 / 0)
    _log("aipager.state", logging.ERROR, "x", exc=_ours())
    assert capsys.readouterr().err == ""


def test_install_attaches_one_handler_and_uninstall_removes_it():
    capture.install()
    capture.install()
    for name in capture.LOGGERS:
        assert sum(isinstance(h, capture.ReportLogHandler)
                   for h in logging.getLogger(name).handlers) == 1
    capture.uninstall()
    for name in capture.LOGGERS:
        assert not any(isinstance(h, capture.ReportLogHandler)
                       for h in logging.getLogger(name).handlers)


# ---- the markers ---------------------------------------------------------------

def test_the_first_install_time_is_written_once_and_kept():
    assert markers.first_start(now=NOW) == NOW
    written = config.REPORT_INSTALL_FILE.read_bytes()
    assert markers.first_start(now=NOW + 5 * 86400) == NOW      # a later start, an update
    assert config.REPORT_INSTALL_FILE.read_bytes() == written


@pytest.mark.parametrize("content", [
    b"garbage", b'{"first_start": "x"}', b'{"first_start": 1.5}', b"[]",
    json.dumps({"first_start": NOW + 2 * 86400}).encode()])
def test_a_broken_install_time_starts_the_clock_now(content):
    config.REPORT_INSTALL_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REPORT_INSTALL_FILE.write_bytes(content)
    assert markers.first_start(now=NOW) == NOW
    assert json.loads(config.REPORT_INSTALL_FILE.read_text()) == {"first_start": NOW}


@pytest.fixture
def boot(tmp_path, monkeypatch):
    path = tmp_path / "boot_id"
    path.write_text("2b1f8c6e-4a7d-4d1e-9c55-0a6b2f3e4d5c\n")
    monkeypatch.setattr(markers, "BOOT_ID", path)
    return path


def test_no_running_marker_means_a_clean_or_first_start(boot):
    assert markers.previous_exit(now=NOW) is None


def test_a_marker_left_on_this_boot_is_a_crash(boot):
    markers.write_running(service=True, now=NOW)
    assert markers.previous_exit(now=NOW + 60) == ("crash", True)
    markers.write_running(service=False, now=NOW)
    assert markers.previous_exit(now=NOW + 60) == ("crash", False)


def test_a_marker_from_another_boot_went_down_with_the_machine(boot):
    markers.write_running(service=True, now=NOW)
    boot.write_text("9f0e1d2c-3b4a-4596-8778-695a4b3c2d1e\n")
    assert markers.previous_exit(now=NOW + 60) == ("reboot", True)


def test_the_uptime_counts_time_asleep(monkeypatch):
    asleep_too = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)
    monkeypatch.setattr(markers.time, "clock_gettime",
                        lambda clock: 12345.0 if clock == asleep_too else 1.0)
    assert markers._uptime() == 12345.0


def test_without_a_boot_id_the_boot_time_is_estimated(boot, monkeypatch):
    boot.unlink()
    monkeypatch.setattr(markers, "_uptime", lambda: 3600.0)
    markers.write_running(service=True, now=NOW - 60)
    assert markers.previous_exit(now=NOW) == ("crash", True)
    markers.write_running(service=True, now=NOW - 7200)
    assert markers.previous_exit(now=NOW) == ("reboot", True)


def test_an_oversized_install_marker_is_not_trusted():
    config.REPORT_INSTALL_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REPORT_INSTALL_FILE.write_text(json.dumps({"first_start": NOW - 86400,
                                                      "pad": "x" * 5000}))
    assert markers.first_start(now=NOW) == NOW


def test_a_boot_id_with_a_trailing_newline_is_no_boot_id(boot, monkeypatch):
    markers.write_running(service=True, now=NOW - 60)
    document = json.loads(config.REPORT_RUNNING_FILE.read_text())
    document["boot"] = boot.read_text()          # the same id, newline kept
    config.REPORT_RUNNING_FILE.write_text(json.dumps(document))
    monkeypatch.setattr(markers, "_uptime", lambda: 3600.0)
    # Judged by the clock (a crash on this boot), never compared as an id.
    assert markers.previous_exit(now=NOW) == ("crash", True)


def test_a_marker_without_a_boot_id_is_judged_by_the_clock(boot, monkeypatch):
    """A marker written where no boot id could be read ("boot": "") is
    judged by the boot-time estimate, never compared as a boot id."""
    saved = boot.read_text()
    boot.unlink()
    markers.write_running(service=True, now=NOW - 60)
    boot.write_text(saved)
    monkeypatch.setattr(markers, "_uptime", lambda: 3600.0)
    assert markers.previous_exit(now=NOW) == ("crash", True)


def test_the_marker_files_are_private(boot):
    markers.first_start(now=NOW)
    markers.write_running(service=True, now=NOW)
    for path in (config.REPORT_INSTALL_FILE, config.REPORT_RUNNING_FILE):
        assert path.stat().st_mode & 0o777 == 0o600


def test_every_marker_write_is_checked_against_the_real_home(boot, monkeypatch):
    checked = []
    monkeypatch.setattr(markers, "check_write", lambda target: checked.append(target.name))
    markers.first_start(now=NOW)
    markers.write_running(service=True, now=NOW)
    markers.clear_running()
    assert checked == ["install.json", "running.json", "running.json"]


@pytest.mark.parametrize("content", [b"x", b"[]", b'{"started": "x"}', b"{}",
                                     json.dumps({"started": NOW, "pad": "x" * 5000}).encode()])
def test_a_marker_that_is_not_ours_is_unknown(boot, content):
    config.REPORT_RUNNING_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REPORT_RUNNING_FILE.write_bytes(content)
    assert markers.previous_exit(now=NOW) == ("unknown", False)


def test_a_marker_with_no_valid_start_is_unknown_even_on_this_boot(boot):
    markers.write_running(service=True, now=NOW)
    document = json.loads(config.REPORT_RUNNING_FILE.read_text())
    config.REPORT_RUNNING_FILE.write_text(json.dumps(dict(document, started="x")))
    assert markers.previous_exit(now=NOW) == ("unknown", False)


def test_clearing_the_marker_removes_it(boot):
    markers.write_running(service=True, now=NOW)
    markers.clear_running()
    markers.clear_running()
    assert markers.previous_exit(now=NOW) is None


# ---- the daemon's start and stop -----------------------------------------------

def test_an_unclean_exit_of_the_service_is_recorded_as_a_bug(boot, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    markers.write_running(service=True, now=NOW - 600)
    runtime.daemon_starting(service=True, now=NOW)
    [entry] = store.errors()
    assert (entry["tier"], entry["trigger"], entry["type"]) == ("bug", "crash", None)
    assert entry["frames"] == [runtime.CRASH_SITE]
    assert store.exit_facts(now=NOW) == (1, "crash")
    assert markers.previous_exit(now=NOW) == ("crash", True)    # this run's own marker
    assert markers.started_at() == NOW
    assert config.REPORT_INSTALL_FILE.exists() and config.REPORTS_FILE.exists()


def test_an_unclean_exit_in_the_foreground_is_only_an_anomaly(boot):
    markers.write_running(service=False, now=NOW - 600)
    runtime.daemon_starting(service=False, now=NOW)
    assert store.errors()[0]["tier"] == "anomaly"


def test_a_reboot_is_noted_and_never_recorded(boot):
    markers.write_running(service=True, now=NOW - 600)
    boot.write_text("9f0e1d2c-3b4a-4596-8778-695a4b3c2d1e\n")
    runtime.daemon_starting(service=True, now=NOW)
    assert store.errors() == [] and store.exit_facts(now=NOW) == (0, "reboot")


def test_a_first_start_records_nothing(boot):
    runtime.daemon_starting(service=True, now=NOW)
    assert store.errors() == [] and store.exit_facts(now=NOW) == (0, "unknown")


@pytest.mark.parametrize(("clean", "last", "unclean"), [(True, "clean", 0), (False, "crash", 1)])
def test_a_stop_removes_the_marker_and_says_how(boot, clean, last, unclean):
    runtime.daemon_starting(service=True, now=NOW)
    runtime.daemon_stopped(clean=clean, now=NOW)
    assert markers.previous_exit(now=NOW) is None
    store.reset()
    assert store.exit_facts(now=NOW) == (unclean, last)


def test_the_start_hook_never_raises(boot, monkeypatch):
    monkeypatch.setattr(markers, "previous_exit", lambda now=None: 1 / 0)
    runtime.daemon_starting(service=True, now=NOW)


# ---- wiring: aipager start and the monitor tick --------------------------------

@pytest.fixture
def fake_daemon(boot, monkeypatch, handler):
    from aipager.cli import daemon
    monkeypatch.setattr(daemon, "_running_as_service", lambda: True)
    return daemon


def test_a_fatal_error_is_logged_with_its_frames_recorded_and_raised(fake_daemon, monkeypatch,
                                                                     caplog):
    async def _fatal(_name):
        raise _ours(lambda: RuntimeError(f"boom {TOKEN}"))

    monkeypatch.setattr(fake_daemon, "_run_daemon", _fatal)
    with caplog.at_level(logging.CRITICAL, logger="aipager.cli.daemon"):
        with pytest.raises(RuntimeError):
            fake_daemon._run_daemon_reporting_crashes("bot")
    [record] = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert record.exc_info and record.exc_info[2] is not None   # the traceback, not one line
    [entry] = store.errors()
    assert entry["trigger"] == "crash" and entry["type"] == "builtins.RuntimeError"
    assert markers.previous_exit() is None
    store.reset()
    assert store.exit_facts()[1] == "crash"


@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit, None])
def test_a_stop_by_signal_exit_or_return_is_clean(fake_daemon, monkeypatch, stop):
    async def _run(_name):
        if stop is not None:
            raise stop()

    monkeypatch.setattr(fake_daemon, "_run_daemon", _run)
    if stop is None:
        fake_daemon._run_daemon_reporting_crashes("bot")
    else:
        with pytest.raises(stop):
            fake_daemon._run_daemon_reporting_crashes("bot")
    assert markers.previous_exit() is None and store.errors() == []
    store.reset()
    assert store.exit_facts()[1] == "clean"


def test_launchd_is_a_service_only_under_aipager_s_own_label(monkeypatch):
    from aipager import service
    from aipager.cli import daemon
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setenv("XPC_SERVICE_NAME", service.MACOS_LABEL)
    assert daemon._running_as_service() is True
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.apple.Terminal")
    assert daemon._running_as_service() is False


def test_running_as_a_service_never_raises(monkeypatch):
    from aipager import self_update
    from aipager.cli import daemon
    def _broken():
        raise RuntimeError("no cgroup")

    monkeypatch.setattr(self_update, "_under_unit_cgroup", _broken)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert daemon._running_as_service() is False
    monkeypatch.setattr(self_update, "_under_unit_cgroup", lambda: True)
    assert daemon._running_as_service() is True


def test_aipager_start_installs_the_handler_before_anything_runs(monkeypatch):
    from aipager import instance, migrate, preflight, telegram_endpoint
    from aipager.cli import daemon
    seen = []
    monkeypatch.setattr(instance, "start_check", lambda: [])
    monkeypatch.setattr(telegram_endpoint, "check", lambda: None)
    monkeypatch.setattr(preflight, "require_config", lambda: None)
    for name in ("migrate_to_v2", "upgrade_to_v3", "retire_v1"):
        monkeypatch.setattr(migrate, name, lambda: None)
    monkeypatch.setattr(daemon, "_check_existing_daemon", lambda: None)
    monkeypatch.setattr(daemon, "_acquire_daemon_lock", lambda: None)
    monkeypatch.setattr(daemon, "_telegram_preflight", lambda: "bot")
    monkeypatch.setattr(daemon, "_run_daemon_reporting_crashes",
                        lambda name: seen.append(bool(capture._installed)))
    assert daemon._cmd_start(None) == 0
    assert seen == [True]


def test_a_second_start_records_in_memory_and_never_writes(monkeypatch):
    """The handler is on before the migrations, so their errors are seen;
    a start that then finds a daemon running exits before the lock and
    writes none of the three files (the running daemon owns them)."""
    from aipager import instance, migrate, preflight, telegram_endpoint
    from aipager.cli import daemon
    monkeypatch.setattr(instance, "start_check", lambda: [])
    monkeypatch.setattr(telegram_endpoint, "check", lambda: None)
    monkeypatch.setattr(preflight, "require_config", lambda: None)

    def _migrate():
        logging.getLogger("aipager.migrate").error("migration failed", exc_info=_ours(
            lambda: KeyError("migration")))

    monkeypatch.setattr(migrate, "migrate_to_v2", _migrate)
    for name in ("upgrade_to_v3", "retire_v1"):
        monkeypatch.setattr(migrate, name, lambda: None)

    def _running():
        raise SystemExit(1)

    monkeypatch.setattr(daemon, "_check_existing_daemon", _running)
    with pytest.raises(SystemExit):
        daemon._cmd_start(None)
    assert [e["type"] for e in store.errors()] == ["builtins.KeyError"]
    for path in (config.REPORTS_FILE, config.REPORT_INSTALL_FILE, config.REPORT_RUNNING_FILE):
        assert not path.exists()


def test_the_monitor_tick_saves_the_store(monkeypatch):
    from aipager.session_monitor import SessionMonitor
    from aipager.state import SessionRegistry

    async def _noop(*a, **kw):
        return None

    store.record_counter("tg_5xx", now=NOW)
    monitor = SessionMonitor(SessionRegistry(), _noop)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", AsyncMock(return_value=[]))
    asyncio.run(monitor.tick())
    assert json.loads(_file_text())["counters"]
