"""One verified URL for every surface (spec requirement 2; design.md
success criteria "resolve_public_url() returns ..." and "A startup
Tailscale publish whose probe ends after the manager's verified publish
does not replace it"; entrypoints.md bot ``publish_miniapp_button`` /
``publish_managed_miniapp_button``).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from telegram import MenuButtonCommands

from aipager.miniapp import tunnel as tunnel_mod

from miniapp_tunnel_state_bb import FakeProcess, wait_until  # noqa: I001

URL = "https://verified-only-words.trycloudflare.com"
TS_URL = "https://frontend.tailnet-example.ts.net/"


class GatedProbe:
    """Probe answers per URL; URLs in ``gates`` wait for their event."""

    def __init__(self):
        self.gates: dict[str, asyncio.Event] = {}
        self.entered: dict[str, asyncio.Event] = {}
        self.calls: list[str] = []

    def gate(self, url):
        self.gates[url] = asyncio.Event()
        self.entered[url] = asyncio.Event()

    async def __call__(self, url, *a, **k):
        self.calls.append(url)
        if url in self.gates:
            self.entered[url].set()
            await self.gates[url].wait()
        return True


# ---- resolve_public_url while the managed URL is probed ------------------------------

def _resolve_during_probe(monkeypatch, seam, make_manager, run_async):
    probe = GatedProbe()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", probe)
    seam.steps = [(FakeProcess(), URL)]
    out = {}

    async def scenario():
        probe.gate(URL)
        mgr = make_manager()
        await mgr.start()
        try:
            await asyncio.wait_for(probe.entered[URL].wait(), 3.0)
            out["during"] = await tunnel_mod.resolve_public_url()
            probe.gates[URL].set()
            await wait_until(lambda: mgr.current_url == URL)
            out["after"] = await tunnel_mod.resolve_public_url()
        finally:
            await mgr.stop()

    run_async(scenario())
    return out


def test_resolve_is_empty_while_probing(monkeypatch, seam, make_manager, run_async):
    assert _resolve_during_probe(monkeypatch, seam, make_manager, run_async)["during"] == ""


def test_resolve_is_the_url_once_its_probe_passed(monkeypatch, seam, make_manager, run_async):
    assert _resolve_during_probe(monkeypatch, seam, make_manager, run_async)["after"] == URL


def test_resolve_gives_tailscale_while_the_managed_url_is_probed(
    monkeypatch, seam, make_manager, run_async,
):
    monkeypatch.setattr("aipager.miniapp.tunnel.detect_public_url", lambda: TS_URL)
    assert _resolve_during_probe(monkeypatch, seam, make_manager, run_async)["during"] == TS_URL


def test_resolve_gives_the_override_while_the_managed_url_is_probed(
    monkeypatch, seam, make_manager, run_async,
):
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "https://manual.example/")
    assert _resolve_during_probe(monkeypatch, seam, make_manager,
                                 run_async)["during"] == "https://manual.example/"


def test_the_override_still_wins_after_the_managed_url_passed(
    monkeypatch, seam, make_manager, run_async,
):
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", "https://manual.example/")
    assert _resolve_during_probe(monkeypatch, seam, make_manager,
                                 run_async)["after"] == "https://manual.example/"


def test_the_managed_slot_is_empty_while_probing(monkeypatch, seam, make_manager, run_async):
    probe = GatedProbe()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", probe)
    seam.steps = [(FakeProcess(), URL)]

    async def scenario():
        probe.gate(URL)
        mgr = make_manager()
        await mgr.start()
        try:
            await asyncio.wait_for(probe.entered[URL].wait(), 3.0)
            return tunnel_mod.get_managed_tunnel_url(), mgr.current_url
        finally:
            probe.gates[URL].set()
            await mgr.stop()

    assert run_async(scenario()) == ("", "")


def test_a_failed_probe_never_fills_the_slot(monkeypatch, seam, make_manager, run_async):
    async def never(url, *a, **k):
        return False

    monkeypatch.setattr(tunnel_mod, "probe_public_url", never)
    seam.steps = [(FakeProcess(), URL)]
    seen = []

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            for _ in range(60):
                seen.append(tunnel_mod.get_managed_tunnel_url())
                await asyncio.sleep(0.01)
        finally:
            await mgr.stop()

    run_async(scenario())
    assert set(seen) == {""}


# ---- the bot's publishes --------------------------------------------------------------

@pytest.fixture
def bot(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    b = mk_bot()
    b._app.bot.set_chat_menu_button = AsyncMock()
    return b


def _menu_urls(bot):
    out = []
    for c in bot._app.bot.set_chat_menu_button.await_args_list:
        button = c.kwargs.get("menu_button")
        out.append("" if isinstance(button, MenuButtonCommands)
                   else getattr(getattr(button, "web_app", None), "url", None))
    return out


def test_the_managed_publish_makes_no_probe(bot, monkeypatch, run_async):
    probe = GatedProbe()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", probe)
    run_async(bot.publish_managed_miniapp_button(URL))
    assert probe.calls == []


def test_the_managed_publish_sets_the_button(bot, run_async):
    run_async(bot.publish_managed_miniapp_button(URL))
    assert _menu_urls(bot)[-1] == URL


def test_the_managed_publish_of_empty_clears_the_button(bot, run_async):
    async def go():
        await bot.publish_managed_miniapp_button(URL)
        await bot.publish_managed_miniapp_button("")
    run_async(go())
    assert bot._miniapp_url == ""


def test_the_probing_publish_still_probes(bot, monkeypatch, run_async):
    probe = GatedProbe()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", probe)
    run_async(bot.publish_miniapp_button(TS_URL))
    assert probe.calls == [TS_URL]


def _startup_race(bot, monkeypatch, run_async, *, managed):
    """A startup (probing) publish of the Tailscale URL is slow; the
    manager's publish of ``managed`` completes first; then the startup
    probe ends."""
    probe = GatedProbe()
    monkeypatch.setattr(tunnel_mod, "probe_public_url", probe)

    async def go():
        probe.gate(TS_URL)
        startup = asyncio.create_task(bot.publish_miniapp_button(TS_URL))
        await asyncio.wait_for(probe.entered[TS_URL].wait(), 3.0)
        await bot.publish_managed_miniapp_button(managed)
        probe.gates[TS_URL].set()
        await asyncio.wait_for(startup, 3.0)

    run_async(go())


def test_a_slow_startup_publish_never_replaces_the_verified_url(bot, monkeypatch, run_async):
    _startup_race(bot, monkeypatch, run_async, managed=URL)
    assert bot._miniapp_url == URL


def test_a_slow_startup_publish_leaves_the_verified_button_last(bot, monkeypatch, run_async):
    _startup_race(bot, monkeypatch, run_async, managed=URL)
    assert _menu_urls(bot)[-1] == URL


def test_a_slow_startup_publish_after_a_managed_clear_still_applies(
    bot, monkeypatch, run_async,
):
    """Today's Tailscale behaviour: a managed "" does not block it."""
    _startup_race(bot, monkeypatch, run_async, managed="")
    assert bot._miniapp_url == TS_URL


def test_two_publishes_never_interleave_their_telegram_calls(mk_bot, monkeypatch, run_async):
    """Error guessing (concurrency): two managed publishes at once; each
    one's menu-button calls run as a block."""
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    b = mk_bot()
    order = []

    async def set_menu(*a, **kw):
        button = kw.get("menu_button")
        url = "" if isinstance(button, MenuButtonCommands) else button.web_app.url
        order.append(("start", url))
        await asyncio.sleep(0.05)
        order.append(("end", url))

    b._app.bot.set_chat_menu_button = AsyncMock(side_effect=set_menu)
    url2 = "https://second-words-here.trycloudflare.com"

    async def go():
        await asyncio.gather(b.publish_managed_miniapp_button(URL),
                             b.publish_managed_miniapp_button(url2))

    run_async(go())
    starts_ends = [kind for kind, _ in order]
    assert all(starts_ends[i] == "start" and starts_ends[i + 1] == "end"
               for i in range(0, len(starts_ends), 2)), order
