"""Hooks newer than the daemon: the receiver hands the hook's own version
over without changing anything else, and the owner is told once per newer
version per run, through the outbound gate, with Restart now."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from aipager import _version
from aipager.bot import report_flow, report_offer, update_flow
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
    env.add_session(status=Status.BUSY)

    async def scenario():
        res = await env.start_restart()
        await env.until(lambda: env.manager.snapshot()["phase"] == "waiting_for_idle")
        env.manager.note_hook_version("1.2.0")
        await asyncio.sleep(0.05)
        deferred = (len(_notices(env)), env.manager._hook_seen["1.2.0"]["attempts"])
        env.manager.control("cancel", res.job["id"])
        await env.finish()
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


def test_a_muted_owner_is_retried_only_after_the_interval_and_at_most_three_times(
        env, run, monkeypatch):
    monkeypatch.setattr(report_offer, "dm_clear", lambda owner: False)
    _note(env, run, "1.1.0", "1.1.0", "1.1.0")
    assert env.manager._hook_seen["1.1.0"]["attempts"] == 1
    monkeypatch.setattr(update_flow, "HOOK_NOTICE_RETRY_SECONDS", 0.0)
    _note(env, run, *["1.1.0"] * 6)
    assert env.manager._hook_seen["1.1.0"]["attempts"] == 3
    monkeypatch.setattr(report_offer, "dm_clear", lambda owner: True)
    _note(env, run, "1.1.0")
    assert _notices(env) == []


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
