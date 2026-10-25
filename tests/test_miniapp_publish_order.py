"""Mini App button publishes run one at a time, and an older one never
replaces a newer real URL (aipager.bot.lifecycle._publish_miniapp_button).

The startup publish (an override or Tailscale URL, probed) and the
managed tunnel's publishes (already probed by the tunnel manager) can
overlap; whichever finished last used to win the menu buttons while /app
handed out the other. Nothing here reaches Telegram: the bot is
``mk_bot()``'s double.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_manager as tm

TAILNET = "https://node.tailnet-example.ts.net/"
MANAGED = "https://managed-words-here.trycloudflare.com"


@pytest.fixture
def bot(mk_bot, monkeypatch):
    b = mk_bot()
    calls = []

    async def _set_menu(*, chat_id, menu_button):
        await asyncio.sleep(0)               # a real call yields
        calls.append((chat_id, getattr(getattr(menu_button, "web_app", None), "url", None)))
    b._app.bot.set_chat_menu_button = _set_menu
    monkeypatch.setattr(b, "_miniapp_button_chats", lambda: [1, 2, 3])
    monkeypatch.setattr(b, "pinned_app_changed", AsyncMock(), raising=False)
    monkeypatch.setattr(b, "release_deferred_keyboard", AsyncMock())
    b.menu_calls = calls
    return b


def _last_per_chat(calls):
    out = {}
    for chat, url in calls:
        out[chat] = url
    return out


def _gated_probe(monkeypatch, probes):
    gate = {}

    async def _probe(url):
        probes.append(url)
        await gate["e"].wait()
        return True
    monkeypatch.setattr(tunnel_mod, "probe_public_url", _probe)
    return gate


def test_an_older_publish_never_replaces_a_newer_url(bot, run_async, monkeypatch):
    probes = []
    gate = _gated_probe(monkeypatch, probes)

    async def scenario():
        gate["e"] = asyncio.Event()
        startup = asyncio.create_task(bot.publish_miniapp_button(TAILNET))
        while not probes:
            await asyncio.sleep(0)
        await bot.publish_managed_miniapp_button(MANAGED)   # the tunnel's, verified
        gate["e"].set()                                      # the slow probe ends
        await startup

    run_async(scenario())
    assert bot._miniapp_url == MANAGED
    assert _last_per_chat(bot.menu_calls) == {1: MANAGED, 2: MANAGED, 3: MANAGED}
    assert all(url != TAILNET for _chat, url in bot.menu_calls)


def test_an_older_publish_after_a_newer_clear_still_applies(bot, run_async, monkeypatch):
    """A managed tunnel that is not working publishes ""; a startup
    Tailscale URL that finishes after it is still the Mini App (today's
    behaviour, and what resolve_public_url hands out then)."""
    probes = []
    gate = _gated_probe(monkeypatch, probes)

    async def scenario():
        gate["e"] = asyncio.Event()
        startup = asyncio.create_task(bot.publish_miniapp_button(TAILNET))
        while not probes:
            await asyncio.sleep(0)
        await bot.publish_managed_miniapp_button("")
        gate["e"].set()
        await startup

    run_async(scenario())
    assert bot._miniapp_url == TAILNET
    assert _last_per_chat(bot.menu_calls) == {1: TAILNET, 2: TAILNET, 3: TAILNET}


def test_two_publishes_never_interleave_their_telegram_calls(bot, run_async):
    async def scenario():
        await asyncio.gather(bot.publish_managed_miniapp_button(MANAGED),
                             bot.publish_managed_miniapp_button(""))

    run_async(scenario())
    assert bot.menu_calls == [(1, MANAGED), (2, MANAGED), (3, MANAGED),
                              (1, None), (2, None), (3, None)]
    assert bot._miniapp_url == ""


def test_managed_publish_probes_once(bot, run_async, monkeypatch):
    """The tunnel manager probes a new URL while watching cloudflared;
    its callback publishes without a second probe."""
    from tests import tunnel_fakes

    table = tunnel_fakes.install(monkeypatch)
    table.hosts[40001] = '{"hostname":"managed-words-here.trycloudflare.com"}'
    monkeypatch.setattr("aipager.miniapp.cloudflared_fetch.ensure_cloudflared",
                        AsyncMock(return_value="/fake/cloudflared"))
    monkeypatch.setattr("aipager.config.TUNNEL_CHECK_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.05)

    async def scenario():
        m = tm.TunnelManager(8765, bot.publish_managed_miniapp_button)
        await m.start()
        for _ in range(400):
            if bot._miniapp_url == MANAGED and len(bot.menu_calls) == 3:
                break
            await asyncio.sleep(0.005)
        await m.stop()

    run_async(scenario())
    assert bot._miniapp_url == MANAGED
    assert table.probes == [MANAGED], "the URL was probed more than once"
    tunnel_mod.set_managed_tunnel_url("")
