"""Black box, iteration 2: when the "hooks newer" owner notice is held back.

The contract (fixes-2.md, tester-iter1-001): a stamped datagram that
arrives while a turn runs in the owner's private chat does not send the
notice and does not use one of its attempts; the session monitor's tick
(``TelegramBot.monitor_tick``) sends it once the turn ends, without a new
datagram. A muted owner chat, a 429 wait / warning regime / minimal mode
and a running update job hold it back the same way. A session in a group
chat does not hold it back. A real send failure still spends an attempt
(at most 3, ``HOOK_NOTICE_RETRY_SECONDS`` apart).

Boundaries faked: Telegram's ``send_message`` (BBEnv's recorder, or one
that raises the outbound gate's ``FloodSkipped``), the flood mute registry
(``MUTE.mute``, its public API) and the rate limiter's snapshot (as
tests/test_report_offer.py does). The retry waits are the module's
documented constants, shortened where a test needs a retry to come due."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import _version
from aipager.bot import update_flow
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import FloodSkipped
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

from bb_helpers import BBEnv, markup_data, owner_id, run_scenario

RS = "_:up:rs"
GROUP = -100777001
SHORT = 0.05


def _bump(v: str, minor=1) -> str:
    major, mi, _patch = (int(x) for x in v.split(".")[:3])
    return f"{major}.{mi + minor}.0"


NEWER = _bump(_version.__version__)


@pytest.fixture
def bb(monkeypatch, tmp_path, mk_bot):
    env = BBEnv(monkeypatch, tmp_path, mk_bot)
    # A spent attempt must be visible within a test: its wait stays long.
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_RETRY_SECONDS", 600.0)
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_SKIP_RETRY_SECONDS", SHORT)
    return env


def _notices(bb):
    return [m for m in bb.sent if RS in markup_data(m.get("reply_markup"))]


async def _ticks(bb, n=5, pause=0.01):
    for _ in range(n):
        await bb.bot.monitor_tick()
        await asyncio.sleep(pause)


async def _mid_turn(bb, n=4):
    """n stamped datagrams during a turn, each followed by a tick."""
    for _ in range(n):
        bb.manager.note_hook_version(NEWER)
        await bb.drain(3)
        await _ticks(bb, 1)


async def _turn(bb, sess, n=4):
    sess.status = Status.BUSY
    await _mid_turn(bb, n)
    sess.status = Status.IDLE
    await _ticks(bb, 5)


def _scope_mode(bb):
    """Scope mode with the owner's private chat and one group."""
    oid = owner_id()
    bb.bot.scopes = [
        Scope(chat_id=oid, kind="dm", label="alice DM",
              members=(Member(id=oid, label="alice", role="owner"),)),
        Scope(chat_id=GROUP, kind="group", label="team",
              members=(Member(id=oid, label="alice", role="owner"),)),
    ]
    bb.bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                Path("/nonexistent/policy.d"))


def _limiter(*, retry=0.0, warning=0.0, minimal=False):
    lim = MagicMock()
    lim.warning_remaining.return_value = warning
    lim.minimal_mode.return_value = minimal
    lim.snapshot.return_value = {"chats": [
        {"chat_id": owner_id(), "retry_until_in": retry, "waiters": 0}]}
    return lim


# ----- a DM session whose datagrams all come mid-turn ----------------------------------------

def test_dm_mid_turn_datagrams_send_nothing_during_the_turn(bb):
    bb.add_session(status=Status.BUSY)

    async def body():
        await _mid_turn(bb, 6)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE],
                         ids=["busy", "interactive"])
def test_dm_live_turn_of_each_kind_holds_the_notice(bb, status):
    bb.add_session(status=status)

    async def body():
        await _mid_turn(bb, 3)
        await _ticks(bb, 5)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


def test_dm_notice_arrives_after_the_turn_ends_without_a_new_datagram(bb):
    sess = bb.add_session(status=Status.BUSY)

    async def body():
        await _mid_turn(bb, 4)
        sess.status = Status.IDLE
        await _ticks(bb, 5)          # no note_hook_version after the turn
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_dm_notice_is_sent_while_the_session_is_idle(bb):
    """None before the turn ends: every notice was sent after it ended."""
    sess = bb.add_session(status=Status.BUSY)
    seen: list = []
    orig = bb._on_send

    async def spy(*a, **kw):
        if RS in markup_data(kw.get("reply_markup")):
            seen.append(sess.status)
        return await orig(*a, **kw)
    bb.bot._app.bot.send_message = AsyncMock(side_effect=spy)

    async def body():
        await _mid_turn(bb, 4)
        sess.status = Status.IDLE
        await _ticks(bb, 5)
    run_scenario(bb, body)
    assert seen == [Status.IDLE]


def test_dm_notice_arrives_on_the_first_ticks_after_the_turn(bb):
    """Not 600 s later: a held-back datagram spent no attempt (an attempt
    would make the next try wait HOOK_NOTICE_RETRY_SECONDS)."""
    sess = bb.add_session(status=Status.BUSY)

    async def body():
        await _mid_turn(bb, 10)       # more hold-backs than the 3 attempts
        sess.status = Status.IDLE
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        while not _notices(bb) and loop.time() - t0 < 2.0:
            await _ticks(bb, 1)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_dm_no_duplicate_after_later_turns(bb):
    sess = bb.add_session(status=Status.IDLE)

    async def body():
        for _ in range(3):
            await _turn(bb, sess)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_dm_no_duplicate_when_idle_datagrams_follow(bb):
    sess = bb.add_session(status=Status.BUSY)

    async def body():
        await _mid_turn(bb, 2)
        sess.status = Status.IDLE
        for _ in range(5):
            bb.manager.note_hook_version(NEWER)
            await bb.drain(3)
            await _ticks(bb, 2)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_dm_hold_back_is_logged_as_deferred(bb, caplog):
    bb.add_session(status=Status.BUSY)

    async def body():
        with caplog.at_level(logging.DEBUG):
            await _mid_turn(bb, 2)
    run_scenario(bb, body)
    assert any("update.hooks_newer.notice" in r.getMessage()
               and "result=deferred" in r.getMessage() for r in caplog.records)


def test_dm_hold_back_logs_one_line_for_many_datagrams(bb, caplog):
    """Never spam: 10 datagrams and ticks behind one live turn, one line."""
    bb.add_session(status=Status.BUSY)

    async def body():
        with caplog.at_level(logging.DEBUG):
            await _mid_turn(bb, 10)
    run_scenario(bb, body)
    lines = [r for r in caplog.records if "update.hooks_newer.notice" in r.getMessage()
             and "result=deferred" in r.getMessage()]
    assert len(lines) == 1


def test_dm_hold_back_never_logs_skipped(bb, caplog):
    """The iteration-1 failure mode: a mid-turn attempt refused as skipped."""
    bb.add_session(status=Status.BUSY)

    async def body():
        with caplog.at_level(logging.DEBUG):
            await _mid_turn(bb, 4)
    run_scenario(bb, body)
    assert not any("result=skipped" in r.getMessage() for r in caplog.records)


def test_owner_dm_idle_sends_at_once_on_the_datagram(bb):
    bb.add_session(status=Status.IDLE)

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


# ----- group sessions: unchanged (sent at once) -------------------------------------------------

def test_busy_group_session_does_not_hold_the_notice(bb):
    _scope_mode(bb)
    bb.add_session(status=Status.BUSY, scope_chat_id=GROUP)

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


def test_busy_group_session_notice_goes_to_the_owner_dm(bb):
    _scope_mode(bb)
    bb.add_session(status=Status.BUSY, scope_chat_id=GROUP)

    async def body():
        bb.manager.note_hook_version(NEWER)
        await bb.drain()
    run_scenario(bb, body)
    assert _notices(bb)[0]["chat_id"] == owner_id()


def test_busy_session_in_the_owner_dm_holds_it_in_scope_mode(bb):
    _scope_mode(bb)
    bb.add_session(status=Status.BUSY, scope_chat_id=owner_id())

    async def body():
        await _mid_turn(bb, 3)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


def test_busy_group_session_one_notice_across_turns(bb):
    _scope_mode(bb)
    sess = bb.add_session(status=Status.IDLE, scope_chat_id=GROUP)

    async def body():
        for _ in range(3):
            await _turn(bb, sess)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


# ----- muted / 429 / warning / minimal: held back, no attempt used ----------------------------

def test_muted_owner_chat_sends_nothing(bb):
    MUTE.mute(owner_id(), 60)

    async def body():
        await _mid_turn(bb, 4)
        await _ticks(bb, 5, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


def test_muted_owner_chat_gets_the_notice_once_unmuted(bb):
    """Held back through more than 3 rechecks, then sent: no attempts used."""
    MUTE.mute(owner_id(), 60)

    async def body():
        await _mid_turn(bb, 4)
        await _ticks(bb, 8, pause=SHORT)
        MUTE.forget(owner_id())
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        while not _notices(bb) and loop.time() - t0 < 2.0:
            await _ticks(bb, 1, pause=SHORT)
        await _ticks(bb, 5, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


@pytest.mark.parametrize("kw", [dict(retry=12.0), dict(warning=30.0), dict(minimal=True)],
                         ids=["429-wait", "warning", "minimal"])
def test_flood_state_holds_the_notice(bb, monkeypatch, kw):
    monkeypatch.setattr("aipager.bot.rich_message._rate_limiter", _limiter(**kw))

    async def body():
        await _mid_turn(bb, 4)
        await _ticks(bb, 5, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 0


@pytest.mark.parametrize("kw", [dict(retry=12.0), dict(warning=30.0), dict(minimal=True)],
                         ids=["429-wait", "warning", "minimal"])
def test_flood_state_cleared_sends_the_notice_once(bb, monkeypatch, kw):
    lim = _limiter(**kw)
    monkeypatch.setattr("aipager.bot.rich_message._rate_limiter", lim)

    async def body():
        await _mid_turn(bb, 4)
        await _ticks(bb, 8, pause=SHORT)
        clear = _limiter()
        lim.warning_remaining.return_value = 0.0
        lim.minimal_mode.return_value = False
        lim.snapshot.return_value = clear.snapshot.return_value
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        while not _notices(bb) and loop.time() - t0 < 2.0:
            await _ticks(bb, 1, pause=SHORT)
        await _ticks(bb, 5, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


# ----- the outbound gate skips: no attempt used --------------------------------------------------

def test_gate_skips_do_not_use_the_three_attempts(bb):
    """The gate refuses the first 5 sends (nothing reached Telegram); the
    6th goes through. Skips spend no attempt, so it is still delivered."""
    tries = []

    async def gate(*a, **kw):
        tries.append(1)
        if len(tries) <= 5:
            raise FloodSkipped(owner_id(), "sendMessage")
        return await bb._on_send(*a, **kw)
    bb.bot._app.bot.send_message = AsyncMock(side_effect=gate)

    async def body():
        bb.manager.note_hook_version(NEWER)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        while not _notices(bb) and loop.time() - t0 < 3.0:
            await _ticks(bb, 1, pause=SHORT)
        await _ticks(bb, 5, pause=SHORT)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


# ----- an update job running: held back, sent once it ends ---------------------------------------

def _job_waiting(bb):
    """A restart job into a different version, waiting on a BUSY group
    session (the owner's DM stays clear)."""
    _scope_mode(bb)
    bb.restart_probe = ("1.0.5", True)
    bb.launch_as_daemon()
    bb.attach_stop()
    return bb.add_session(status=Status.BUSY, scope_chat_id=GROUP)


def test_update_job_holds_the_notice(bb):
    _job_waiting(bb)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        await _mid_turn(bb, 4)
        await _ticks(bb, 5)
        held = len(_notices(bb))
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        return held
    assert run_scenario(bb, body) == 0


def test_update_job_end_releases_the_notice_without_a_datagram(bb):
    _job_waiting(bb)

    async def body():
        res = await bb.start_restart()
        await bb.until(lambda: bb.manager.snapshot()["phase"] == "waiting_for_idle")
        await _mid_turn(bb, 6)        # more hold-backs than the 3 attempts
        bb.manager.control("cancel", res.job["id"])
        await bb.finish()
        await _ticks(bb, 10)
        return len(_notices(bb))
    assert run_scenario(bb, body) == 1


# ----- real failures still spend attempts (the cap stays) ----------------------------------------

def test_failed_sends_stop_after_three_attempts(bb, monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_RETRY_SECONDS", SHORT)
    calls = []

    async def down(*a, **kw):
        calls.append(1)
        raise RuntimeError("telegram down")
    bb.bot._app.bot.send_message = AsyncMock(side_effect=down)

    async def body():
        bb.manager.note_hook_version(NEWER)
        for _ in range(20):
            bb.manager.note_hook_version(NEWER)
            await _ticks(bb, 1, pause=SHORT)
    run_scenario(bb, body)
    assert len(calls) == 3


def test_failed_send_waits_the_retry_interval(bb):
    """With the 600 s interval a failure is not retried within the test."""
    calls = []

    async def down(*a, **kw):
        calls.append(1)
        raise RuntimeError("telegram down")
    bb.bot._app.bot.send_message = AsyncMock(side_effect=down)

    async def body():
        for _ in range(10):
            bb.manager.note_hook_version(NEWER)
            await _ticks(bb, 1, pause=SHORT)
    run_scenario(bb, body)
    assert len(calls) == 1


def test_tick_with_nothing_noted_sends_nothing(bb):
    async def body():
        await _ticks(bb, 10)
    run_scenario(bb, body)
    assert bb.sent == []


def test_tick_with_an_equal_hook_version_sends_nothing(bb):
    async def body():
        bb.manager.note_hook_version(_version.__version__)
        await _ticks(bb, 10)
    run_scenario(bb, body)
    assert bb.sent == []


# ----- the Mini App confirm: Restart now promised only when it follows ---------------------------

def _offer_check(bb):
    """Running 1.0.1, disk 1.0.1, PyPI 1.1.0: an update is offered."""
    bb.install_probe = ("1.0.1", True)
    bb.restart_probe = ("1.0.1", True)
    bb.disk_metadata = "1.0.1"
    return run_scenario(bb, bb.manager.check)


def _payload(bb, status):
    return update_flow.check_payload(status, show_paths=True)


def test_offer_payload_in_place_in_foreground(bb):
    bb.launch_as_daemon()
    status = _offer_check(bb)
    assert _payload(bb, status).get("restart_in_place") is True


def test_offer_payload_not_in_place_under_systemd(bb):
    bb.set_under_unit(True)
    bb.launch_as_daemon()
    status = _offer_check(bb)
    assert _payload(bb, status).get("restart_in_place") is False


def test_offer_payload_not_in_place_with_unsafe_killmode(bb):
    bb.set_under_unit(True)
    bb.killmode = "control-group"
    bb.launch_as_daemon()
    status = _offer_check(bb)
    assert _payload(bb, status).get("restart_in_place") is False


def test_offer_payload_not_in_place_with_another_main_pid(bb):
    bb.set_under_unit(True)
    bb.main_pid = __import__("os").getpid() + 1
    bb.launch_as_daemon()
    status = _offer_check(bb)
    assert _payload(bb, status).get("restart_in_place") is False


def test_offer_payload_not_in_place_without_an_exec_target(bb, monkeypatch, tmp_path):
    import sys
    monkeypatch.setattr(sys, "executable", str(tmp_path / "gone" / "python3"))
    bb.launch_as_daemon()
    status = _offer_check(bb)
    assert _payload(bb, status).get("restart_in_place") is False


@pytest.mark.parametrize("unit", [False, True], ids=["foreground", "systemd"])
def test_offer_payload_really_offers_the_update(bb, unit):
    """Guard against a vacuous pass: the check really offers an update."""
    bb.set_under_unit(unit)
    bb.launch_as_daemon()
    status = _offer_check(bb)
    assert (_payload(bb, status).get("offer") or {}).get("kind") == "aipager"


# ----- the confirm the real page shows (tests/js/miniapp_smoke.js, real page JS) -----------------

_ROOT = Path(__file__).resolve().parents[3]
_HARNESS = _ROOT / "tests" / "js" / "miniapp_smoke.js"


def _english(key: str) -> str:
    import json
    for line in (_ROOT / "tools" / "i18n" / "source.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row.get("k") == key:
            return row["en"]
    raise AssertionError(f"no key {key}")


def _shown_confirm(tmp_path, scenario):
    import shutil
    import subprocess
    node = shutil.which("node") or shutil.which("nodejs")
    if node is None:
        pytest.skip("node not available")
    from aipager.miniapp.static import INDEX_HTML
    page = tmp_path / f"{scenario}.html"
    page.write_text(INDEX_HTML, encoding="utf-8")
    proc = subprocess.run([node, str(_HARNESS), str(page), scenario],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (line,) = [ln for ln in proc.stdout.splitlines() if ln.startswith("ok: confirm -> ")]
    return line[len("ok: confirm -> "):]


def test_confirm_in_place_promises_restart_now(tmp_path):
    assert _shown_confirm(tmp_path, "updates_confirm_in_place") == _english(
        "ui.update.offer_confirm_manual").format(offer="Update aipager")


@pytest.mark.parametrize("scenario", ["updates_confirm_systemd", "updates_confirm_manual_steps"],
                         ids=["systemd", "manual-steps"])
def test_confirm_without_in_place_promises_no_restart_now(tmp_path, scenario):
    assert "Restart now" not in _shown_confirm(tmp_path, scenario)


def test_confirm_with_an_automatic_restart_is_the_plain_confirm(tmp_path):
    assert _shown_confirm(tmp_path, "updates_confirm_systemd") == _english(
        "ui.update.offer_confirm").format(offer="Update aipager")


def test_confirm_without_any_restart_announces_the_steps(tmp_path):
    assert _shown_confirm(tmp_path, "updates_confirm_manual_steps") == _english(
        "ui.update.offer_confirm_steps").format(offer="Update aipager")
