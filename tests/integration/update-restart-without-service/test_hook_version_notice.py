"""Hooks newer than the daemon: the receiver hands the hook's own version
over without changing anything else, and the owner is told once per newer
version per run, through the outbound gate, with Restart now."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock

import pytest

from aipager import _version
from aipager.bot import report_flow, report_offer, update_flow
from aipager.bot.held import HELD
from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry, Status


@pytest.fixture(autouse=True)
def _daemon_runs_101(monkeypatch):
    monkeypatch.setattr(_version, "__version__", "1.0.1")


# ---- the receiver --------------------------------------------------------------------------

def _receiver(callback=None):
    recv = hr.HookReceiver(SessionRegistry(), AsyncMock())
    if callback is not None:
        recv.on_hook_version = callback
    return recv


def _feed(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def test_the_receiver_hands_over_the_hook_version(run_async):
    seen = []
    recv = _receiver(seen.append)
    _feed(recv, run_async, hook_event_name="UserPromptSubmit", session="claude-a",
          aipager_version="1.1.0")
    _feed(recv, run_async, type="statusline", session="claude-a", aipager_version="1.1.1")
    assert seen == ["1.1.0", "1.1.1"]


@pytest.mark.parametrize("value", [None, "", 110, ["1.1.0"], "1" * 41])
def test_the_receiver_ignores_a_missing_or_odd_version(run_async, value):
    seen = []
    recv = _receiver(seen.append)
    fields = dict(hook_event_name="UserPromptSubmit", session="claude-a")
    if value is not None:
        fields["aipager_version"] = value
    _feed(recv, run_async, **fields)
    assert seen == []


def test_a_raising_callback_changes_nothing_in_the_handling(run_async):
    def boom(version):
        raise RuntimeError("callback broke")
    plain, stamped = _receiver(), _receiver(boom)
    for recv, extra in ((plain, {}), (stamped, {"aipager_version": "1.1.0"})):
        _feed(recv, run_async, hook_event_name="UserPromptSubmit", session="claude-a",
              **extra)
    assert stamped.registry.get("claude-a").status == Status.BUSY
    assert plain.registry.get("claude-a").status == Status.BUSY
    assert ([c.args[1] for c in stamped.notify_fn.await_args_list]
            == [c.args[1] for c in plain.notify_fn.await_args_list] == ["user_prompt_submit"])


def test_a_true_duplicate_is_still_dropped(run_async):
    seen = []
    recv = _receiver(seen.append)
    for _ in range(2):
        _feed(recv, run_async, hook_event_name="UserPromptSubmit", session="claude-a",
              aipager_version="1.1.0")
    assert recv.notify_fn.await_count == 1


# ---- the owner's notice ------------------------------------------------------------------------

async def _drain(env):
    for _ in range(5):
        tasks = [t for t in env.manager._tasks if not t.done()]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


def _note(env, run, *versions):
    async def scenario():
        for v in versions:
            env.manager.note_hook_version(v)
            await _drain(env)
    run(scenario)


def _notices(env):
    return [s for s in env.sent if s["text"] and "only half done" in s["text"]]


def test_one_notice_per_newer_version_per_run(env, run):
    _note(env, run, "1.1.0", "1.1.0", "1.1.0")
    assert len(_notices(env)) == 1
    _note(env, run, "1.2.0", "1.1.0")
    assert len(_notices(env)) == 2


def test_a_delivered_notice_is_never_sent_again_even_with_no_retry_wait(env, run,
                                                                        monkeypatch):
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_RETRY_SECONDS", 0.0)
    _note(env, run, "1.1.0", "1.1.0", "1.1.0")
    assert len(_notices(env)) == 1


def test_the_notice_goes_to_the_owner_quietly_with_restart_now(env, run):
    _note(env, run, "1.1.0")
    (sent,) = _notices(env)
    assert sent["chat_id"] == env.chat_id
    assert sent["text"] == ("⚠️ The aipager update is only half done: Claude Code's hooks "
                            "already run 1.1.0, but aipager itself still runs 1.0.1. Restart "
                            "aipager to finish it.")
    assert sent["disable_notification"] is True
    assert sent["rate_limit_args"] == rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT)
    assert [b.callback_data for row in sent["reply_markup"].inline_keyboard
            for b in row] == ["_:up:rs"]


@pytest.mark.parametrize("version", ["1.0.1", "1.0.0", "0.9.9", "1.1.0rc1", "garbage",
                                     None, 5])
def test_no_notice_for_an_equal_older_or_odd_version(env, run, version):
    _note(env, run, version)
    assert _notices(env) == [] and env.manager.waiting_info() is None


def test_no_owner_no_notice(env, run, monkeypatch):
    monkeypatch.setattr(report_flow, "resolve_owner", lambda bot: None)
    _note(env, run, "1.1.0", "1.1.0")
    assert _notices(env) == []
    # The Mini App banner still knows.
    assert env.manager.waiting_info() == {"version": "1.1.0", "running": "1.0.1",
                                          "source": "hooks"}


def test_a_busy_manager_defers_without_spending_an_attempt(env, run):
    sess = env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start_restart()
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        env.manager.note_hook_version("1.2.0")
        await asyncio.sleep(0.05)
        deferred = (len(_notices(env)), env.manager._hook_seen["1.2.0"]["attempts"])
        env.manager.control("cancel", res.job["id"])
        await env.finish()
        sess.status = Status.IDLE
        env.manager.note_hook_version("1.2.0")
        await _drain(env)
        return deferred
    assert run(scenario) == (0, 0)
    assert len(_notices(env)) == 1


def test_a_job_card_that_offers_the_restart_covers_the_notice(env, run):
    async def scenario():
        await env.start_update()
        await env.finish()
        env.manager.note_hook_version("1.1.0")
        await _drain(env)
        env.manager.note_hook_version("1.1.0")
        await _drain(env)
    run(scenario)
    assert env.manager.snapshot()["can_restart"] is True
    assert _notices(env) == []
    assert env.manager._hook_seen["1.1.0"]["delivered"] is True


def test_a_chat_that_is_not_clear_spends_no_attempt_and_is_sent_once_clear(
        env, run, monkeypatch):
    monkeypatch.setattr(report_offer, "dm_clear", lambda owner: False)
    _note(env, run, *["1.1.0"] * 6)
    rec = env.manager._hook_seen["1.1.0"]
    assert (rec["attempts"], rec["task"], _notices(env)) == (0, None, [])
    monkeypatch.setattr(report_offer, "dm_clear", lambda owner: True)
    _note(env, run, "1.1.0")              # rechecked only after the short wait
    assert _notices(env) == []
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_SKIP_RETRY_SECONDS", 0.0)

    async def ticks():
        for _ in range(3):
            env.manager.hook_notice_tick()
            await _drain(env)
    run(ticks)
    assert len(_notices(env)) == 1 and rec["attempts"] == 0 and rec["delivered"] is True


def _failing_send(env, calls):
    async def _fail(*a, **kw):
        calls.append(kw)
        raise RuntimeError("telegram down")
    env.bot._app.bot.send_message = AsyncMock(side_effect=_fail)


def test_a_failed_send_is_retried_only_after_the_interval_and_at_most_three_times(
        env, run, monkeypatch):
    calls: list = []
    _failing_send(env, calls)
    _note(env, run, "1.1.0", "1.1.0", "1.1.0")
    assert len(calls) == 1 and env.manager._hook_seen["1.1.0"]["attempts"] == 1
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_RETRY_SECONDS", 0.0)
    _note(env, run, *["1.1.0"] * 6)
    assert len(calls) == 3 and env.manager._hook_seen["1.1.0"]["attempts"] == 3
    env.bot._app.bot.send_message = AsyncMock(side_effect=env._on_send)
    _note(env, run, "1.1.0")
    assert _notices(env) == []


def test_a_gate_skip_spends_no_attempt_and_is_retried_after_a_short_wait(
        env, run, monkeypatch):
    results = [update_flow.SKIPPED]
    real = update_flow.send_text

    async def _send(*a, **kw):
        if results:
            return results.pop()
        return await real(*a, **kw)
    monkeypatch.setattr(update_flow, "send_text", _send)
    _note(env, run, "1.1.0")
    rec = env.manager._hook_seen["1.1.0"]
    assert rec["attempts"] == 0 and _notices(env) == [] and rec["last_try"] == "skipped"
    _note(env, run, "1.1.0")                  # not before the short wait...
    assert _notices(env) == []
    # ...and once it has passed, once.
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_SKIP_RETRY_SECONDS", 0.0)
    _note(env, run, "1.1.0", "1.1.0")
    assert len(_notices(env)) == 1 and rec["attempts"] == 0


def test_at_most_sixteen_versions_are_remembered(env, run, monkeypatch):
    monkeypatch.setattr(report_offer, "dm_clear", lambda owner: False)
    _note(env, run, *[f"1.1.{i}" for i in range(16)])
    monkeypatch.setattr(report_offer, "dm_clear", lambda owner: True)
    _note(env, run, "9.9.9")
    assert len(env.manager._hook_seen) == 16 and "9.9.9" not in env.manager._hook_seen
    assert _notices(env) == []


def test_note_hook_version_never_raises(env):
    # Outside a running loop the notice cannot be scheduled: still no raise.
    env.manager.note_hook_version("1.1.0")
    assert env.manager.waiting_info()["source"] == "hooks"


# ---- the owner's chat busy: held back, then sent when the turn ends -------------------------

def _quiet_monitor(env, monkeypatch):
    """The real ``TelegramBot.monitor_tick`` with only the notice's part live."""
    async def _nothing(*a, **k):
        return None
    monkeypatch.setattr(env.bot, "pinned_tick", _nothing)
    monkeypatch.setattr(report_offer, "tick", _nothing)


def test_a_dm_session_whose_datagrams_all_come_mid_turn_gets_one_notice_after_the_turn(
        env, run, monkeypatch):
    _quiet_monitor(env, monkeypatch)
    sess = env.add_session(status=Status.IDLE)
    seen_mid_turn = []

    async def scenario():
        for _turn in range(3):
            sess.status = Status.BUSY            # UserPromptSubmit ... Stop
            for _ in range(4):
                env.manager.note_hook_version("1.1.0")
                await _drain(env)
                await env.bot.monitor_tick()
                await _drain(env)
            rec = env.manager._hook_seen["1.1.0"]
            seen_mid_turn.append((len(_notices(env)), rec["attempts"]))
            sess.status = Status.IDLE            # the turn ended
            for _ in range(3):
                await env.bot.monitor_tick()
                await _drain(env)
    run(scenario)
    # Nothing during the first turn, no attempt spent while it ran...
    assert seen_mid_turn[0] == (0, 0)
    # ...one notice once it ended, and never a second one.
    assert seen_mid_turn[1:] == [(1, 0), (1, 0)]
    assert len(_notices(env)) == 1


def _fields(**fields):
    def apply(sess):
        for key, value in fields.items():
            setattr(sess, key, value)
    return apply


def _compacting(sess):
    # A compacting card still being sent (-1 claim): only the stack top says so.
    sess.push_compacting(-1, time.monotonic(), None)


def _tool_in_flight(sess):
    sess.pending_tool_started_at = time.monotonic()


def _background_agent(sess):
    # An interim Stop while a background agent the turn launched still runs.
    sess.active_subagents = {"agent-1": {"started_at": time.monotonic(),
                                         "last_seen": time.monotonic()}}


@pytest.mark.parametrize("busy", [
    _fields(status=Status.BUSY),
    _fields(status=Status.INTERACTIVE),
    _fields(busy_msg_id=77),
    _fields(busy_card_owed=True),
    _fields(notify_in_flight=1),
    _fields(pending_permission={"tool": "Bash"}),
    _compacting,
    _tool_in_flight,
    _background_agent,
], ids=["busy", "interactive", "live-card", "card-owed", "notify-in-flight",
        "permission", "compacting", "tool-in-flight", "background-agent"])
def test_any_live_turn_in_the_owner_chat_holds_the_notice_back(env, run, busy):
    sess = env.add_session(status=Status.IDLE)
    busy(sess)
    _note(env, run, "1.1.0", "1.1.0")
    rec = env.manager._hook_seen["1.1.0"]
    assert (_notices(env), rec["attempts"], rec["task"]) == ([], 0, None)


def _hold_an_answer(chat_id):
    HELD.hold(chat_id=chat_id, session="claude-dev", label="dev",
              rich_text="the answer", plain_text="the answer")


def test_a_held_answer_for_the_owner_chat_holds_the_notice_back(env, run):
    env.add_session(status=Status.IDLE)
    _hold_an_answer(env.chat_id)
    _note(env, run, "1.1.0", "1.1.0")
    rec = env.manager._hook_seen["1.1.0"]
    assert (_notices(env), rec["attempts"], rec["task"]) == ([], 0, None)
    assert rec["deferred"] == "owner-chat-held-answers"
    HELD.clear()                               # delivered: the notice follows
    _note(env, run, "1.1.0")
    assert len(_notices(env)) == 1


def test_a_held_answer_for_another_chat_does_not_hold_the_notice_back(env, run):
    _hold_an_answer(-1001234567890)
    _note(env, run, "1.1.0")
    assert len(_notices(env)) == 1


def test_a_busy_session_in_another_chat_does_not_hold_the_notice_back(env, run):
    env.add_session(status=Status.BUSY, scope_chat_id=-1001234567890)
    _note(env, run, "1.1.0")
    assert len(_notices(env)) == 1


def test_a_held_back_notice_logs_once_per_reason_and_makes_no_task(env, run, caplog):
    env.add_session(status=Status.BUSY)

    async def scenario():
        before = len(env.manager._tasks)
        for _ in range(10):
            env.manager.note_hook_version("1.1.0")
            env.manager.hook_notice_tick()
        await asyncio.sleep(0)
        return len(env.manager._tasks) - before
    with caplog.at_level(logging.INFO, logger="aipager.bot.update_flow"):
        made = run(scenario)
    held = [r.getMessage() for r in caplog.records if "result=deferred" in r.getMessage()]
    assert made == 0
    assert held == ["update.hooks_newer.notice hook=1.1.0 result=deferred "
                    "reason=owner-chat-busy"]


def test_the_tick_with_nothing_pending_does_nothing(env, run):
    async def scenario():
        env.manager.hook_notice_tick()
        await asyncio.sleep(0)
    run(scenario)
    assert env.manager._tasks == set() and _notices(env) == []
