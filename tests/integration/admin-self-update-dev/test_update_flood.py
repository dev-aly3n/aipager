"""Flood discipline of the one status message (8.36)."""

from __future__ import annotations

import asyncio

from aipager import self_update
from aipager.bot import update_flow
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args


def test_progress_edits_at_most_every_five_seconds(env, run, monkeypatch):
    monkeypatch.setattr(self_update, "PROGRESS_EDIT_MIN_SECONDS", 5)
    rep = update_flow._Reporter(env.bot, env.chat_id, env.message)

    async def scenario():
        await rep.show("outcome A")                       # essential: goes
        await rep.show("heartbeat 1", ornament=True)      # < 5 s: skipped
        await rep.show("heartbeat 2", ornament=True)      # < 5 s: skipped
        rep._last_at -= 5.1
        await rep.show("heartbeat 3", ornament=True)      # 5 s later: goes
        await rep.show("outcome B")                       # essential: always
        await rep.show("outcome B")                       # unchanged: nothing
    run(scenario)
    assert [e["text"] for e in env.edits] == ["outcome A", "heartbeat 3", "outcome B"]


def test_heartbeat_edits_are_skippable_ornament(env, run, monkeypatch):
    import threading

    monkeypatch.setattr(self_update, "PROGRESS_EDIT_MIN_SECONDS", 0.02)
    monkeypatch.setattr(self_update, "HEARTBEAT_GRANULARITY_SECONDS", 0.02)
    env.upgrade_release = threading.Event()

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: any("still working" in e["text"] for e in env.edits))
        env.upgrade_release.set()
        await env.finish()
    run(scenario)
    ornament = rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT)
    beats = [e for e in env.edits if "still working" in e["text"]]
    assert beats and all(e.get("rate_limit_args") == ornament for e in beats)
    outcome = env.edits[-1]
    assert "Restarting in 5 s" in outcome["text"]
    assert "rate_limit_args" not in outcome   # ESSENTIAL: the default class


def test_update_sends_nothing_into_muted_chat(env, run):
    MUTE.mute(env.chat_id, 600, source="test")

    async def scenario():
        await env.start("claude")
        await env.finish()
    run(scenario)
    env.message.edit_text.assert_not_awaited()
    env.bot._app.bot.send_message.assert_not_awaited()
    # The work itself still happened.
    assert env.manager.snapshot()["phase"] == "done"


def test_mini_app_job_sends_exactly_one_message(env, run):
    async def scenario():
        await env.manager.start("claude", chat_id=env.chat_id, user_id=env.chat_id,
                                origin="miniapp")
        await env.finish()
        await asyncio.sleep(0)
    run(scenario)
    assert len(env.sent) == 1
    assert env.sent[0]["chat_id"] == env.chat_id
    assert len(env.edits) >= 1   # everything after the first send is an edit
