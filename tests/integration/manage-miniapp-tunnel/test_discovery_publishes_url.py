"""design.md success criterion 1: with no override and the Mini App
enabled, a started ``TunnelManager`` whose seam yields a URL makes that
exact URL the answer everywhere the daemon asks — the menu button
(via the pre-existing, already-probed ``bot.publish_miniapp_button``)
and the single resolver ``resolve_public_url()`` that spec.md says
also feeds the reply keyboard and ``/app``:

    "An ephemeral random hostname is fine ... all fed by the single
    resolver `tunnel.resolve_public_url()`." (spec.md)

So proving ``resolve_public_url()`` agrees with what was just discovered
is proving all three surfaces agree, without needing to read (or
reimplement) the reply-keyboard/``/app`` handlers themselves.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import aipager.miniapp.tunnel_manager as tunnel_manager_mod
from aipager.miniapp.tunnel import resolve_public_url


DISCOVERED_URL = "https://yes-consolidation-math-shorter.trycloudflare.com"


@pytest.fixture(autouse=True)
def _reachable(monkeypatch):
    # Fake trycloudflare URLs never resolve for real; treat them as
    # reachable so publish_miniapp_button's pre-existing probe guard
    # (see tests/test_miniapp_hardening.py) doesn't swallow every call
    # here and make these tests pass for the wrong reason.
    monkeypatch.setattr("aipager.miniapp.tunnel.probe_public_url",
                        AsyncMock(return_value=True))


def test_resolve_public_url_returns_the_discovered_url(
    monkeypatch, run_async, FakeProcess, wait_until, mock_cloudflared_binary,
):
    proc = FakeProcess()

    async def fake_seam(binary, port):
        return proc, DISCOVERED_URL

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        changes = []

        async def on_change(url):
            changes.append(url)

        manager = TunnelManager(port=8765, on_url_change=on_change)
        try:
            await manager.start()
            await wait_until(lambda: manager.current_url == DISCOVERED_URL,
                              timeout=5.0)
            assert await resolve_public_url() == DISCOVERED_URL
        finally:
            await manager.stop()

    run_async(scenario())


def test_publish_miniapp_button_advertises_the_discovered_url(
    monkeypatch, run_async, mk_bot, FakeProcess, wait_until, mock_cloudflared_binary,
):
    proc = FakeProcess()

    async def fake_seam(binary, port):
        return proc, DISCOVERED_URL

    monkeypatch.setattr(tunnel_manager_mod, "spawn_and_discover_url", fake_seam)

    bot = mk_bot()
    bot._app.bot.set_chat_menu_button = AsyncMock()
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    async def scenario():
        from aipager.miniapp.tunnel_manager import TunnelManager

        manager = TunnelManager(port=8765, on_url_change=bot.publish_miniapp_button)
        try:
            await manager.start()
            await wait_until(
                lambda: bot._app.bot.set_chat_menu_button.await_count > 0,
                timeout=5.0,
            )
        finally:
            await manager.stop()

    run_async(scenario())

    calls = bot._app.bot.set_chat_menu_button.await_args_list
    assert calls, "publish_miniapp_button never called set_chat_menu_button"
    urls = {c.kwargs["menu_button"].web_app.url for c in calls}
    assert urls == {DISCOVERED_URL}
