"""The tunnel state stops saying "working" as soon as cloudflared is gone,
even while a publish of its (now dead) URL is still in flight inside
Telegram; the button is cleared after that publish (review-1
rev-iter1-004; spec requirements 1, 3, 4 and 6: /app and doctor tell
the same, honest story; design.md success criterion "A cloudflared that
exits during the publish").

Observed through the public surface only: ``tunnel_state.current_status()``
(what /app reads), ``read_status_file()`` (what doctor reads), the /app
reply to the operator, ``doctor.check_miniapp()``, and the bot's Telegram
calls. Partitions of "when it dies": during the first menu-button call
of the publish, and during a long-hung publish sampled several check
intervals in a row.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import MenuButtonCommands

from aipager.miniapp import tunnel_state

from miniapp_tunnel_state_bb import FakeProcess, wait_until  # noqa: I001

URL = "https://dies-mid-publish-words.trycloudflare.com"


@pytest.fixture
def death_mid_publish(monkeypatch, seam, make_manager, mk_bot, mk_update, run_async,
                      status_file_seams):
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.miniapp.server.miniapp_extra_available", lambda: True)
    # A long backoff: the state seen after the death is the dead tunnel's,
    # not a next attempt's.
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)
    proc = FakeProcess()
    seam.steps = [(proc, URL)]

    bot = mk_bot()
    in_publish = asyncio.Event()
    release = asyncio.Event()
    calls: list = []

    async def set_menu(*a, **kw):
        calls.append(kw)
        if getattr(kw.get("menu_button"), "web_app", None) is not None \
                and not release.is_set():
            in_publish.set()
            await release.wait()

    bot._app.bot.set_chat_menu_button = AsyncMock(side_effect=set_menu)
    obs = {"calls": calls}

    async def scenario():
        from aipager import doctor
        mgr = make_manager(on_url_change=bot.publish_managed_miniapp_button)
        await mgr.start()
        try:
            await asyncio.wait_for(in_publish.wait(), 3.0)
            obs["phase_while_alive"] = tunnel_state.current_status().phase
            proc.die(1)
            # One check interval is 0.2 s here; sample over five of them
            # while Telegram still holds the publish.
            phases, file_phases = [], []
            for _ in range(5):
                await asyncio.sleep(0.2)
                st = tunnel_state.current_status()
                phases.append(st.phase if st else None)
                fst = tunnel_state.read_status_file()
                file_phases.append(fst.phase if fst else None)
            obs["phases_after_death"] = phases
            obs["file_phases_after_death"] = file_phases[1:]   # after one interval
            obs["publish_still_held"] = not release.is_set()
            upd = mk_update("/app", user_id=555, chat_id=555)
            upd.effective_chat.type = "private"
            await bot._handle_app_cmd(upd, MagicMock())
            obs["app_reply"] = upd.message.reply_text.await_args.args[0]
            obs["doctor"] = doctor.check_miniapp()
            calls_before_release = len(calls)
            release.set()
            await wait_until(lambda: any(isinstance(c.get("menu_button"), MenuButtonCommands)
                                         for c in calls), timeout=3.0)
            await asyncio.sleep(0.3)
            obs["calls_before_release"] = calls_before_release
            obs["bot_url"] = bot._miniapp_url
            st = tunnel_state.current_status()
            obs["phase_at_end"] = st.phase if st else None
        finally:
            await mgr.stop()

    run_async(scenario())
    return obs


def test_the_state_says_working_while_the_tunnel_lives(death_mid_publish):
    """Control: before the death the state really is working."""
    assert death_mid_publish["phase_while_alive"] == "working"


def test_the_publish_was_still_in_flight_while_sampled(death_mid_publish):
    """Control: the samples below were taken inside the hung publish."""
    assert death_mid_publish["publish_still_held"] is True


def test_the_state_stops_saying_working_within_one_check_interval(death_mid_publish):
    assert "working" not in death_mid_publish["phases_after_death"], \
        death_mid_publish["phases_after_death"]


def test_the_state_after_the_death_is_retrying(death_mid_publish):
    assert set(death_mid_publish["phases_after_death"]) == {"retrying"}, \
        death_mid_publish["phases_after_death"]


def test_the_status_file_stops_saying_working_too(death_mid_publish):
    """doctor reads the file: it must not say "tunnel: working" either."""
    assert "working" not in death_mid_publish["file_phases_after_death"], \
        death_mid_publish["file_phases_after_death"]


def test_app_during_the_hung_publish_is_not_the_setting_up_text(death_mid_publish):
    """The operator asking /app in that window is told it is not working,
    not "still being set up"."""
    assert "still being set up" not in death_mid_publish["app_reply"], \
        death_mid_publish["app_reply"]


def test_app_during_the_hung_publish_says_it_cannot_connect(death_mid_publish):
    reply = death_mid_publish["app_reply"]
    assert "can't connect" in reply or "isn't working" in reply, reply


def test_doctor_during_the_hung_publish_does_not_say_working(death_mid_publish):
    detail = list(death_mid_publish["doctor"].detail)
    assert "tunnel: working" not in detail, detail


def test_no_clear_is_sent_while_the_publish_is_still_held(death_mid_publish):
    """The publish is never interleaved: its "" comes after it."""
    early = death_mid_publish["calls"][:death_mid_publish["calls_before_release"]]
    assert not any(isinstance(c.get("menu_button"), MenuButtonCommands) for c in early)


def test_the_button_is_cleared_after_the_publish(death_mid_publish):
    last = {}
    for c in death_mid_publish["calls"]:
        last[c.get("chat_id")] = c.get("menu_button")
    assert last and all(isinstance(b, MenuButtonCommands) for b in last.values()), last


def test_the_bot_holds_no_url_at_the_end(death_mid_publish):
    assert death_mid_publish["bot_url"] == ""


def test_the_state_never_returns_to_working_for_the_dead_url(death_mid_publish):
    assert death_mid_publish["phase_at_end"] != "working"
