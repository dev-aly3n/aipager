"""design.md success criterion 6 (partial -- see test-report for the
documented gap): "With MINIAPP_PUBLIC_URL set, spawn_and_discover_url is
never called and no TunnelManager is constructed; resolve_public_url()
returns the override unchanged and the
unreachable-override-clears-the-button behaviour is intact."

entrypoints.md exposes the resolver's precedence contract directly:
"explicit MINIAPP_PUBLIC_URL > the managed tunnel's current URL (if any)
> Tailscale auto-detect > \"\"" -- both halves tested below are provable
purely from that documented surface, without needing the daemon-level
construction gate itself (see test-report-1.json's issues for why that
half is out of reach from entrypoints.md alone).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.miniapp.tunnel import (
    get_managed_tunnel_url,
    resolve_public_url,
    set_managed_tunnel_url,
)

OVERRIDE_URL = "https://reverse-proxy.example.com"
MANAGED_URL = "https://some-managed-tunnel.trycloudflare.com"


def test_override_wins_even_when_a_managed_tunnel_url_is_also_set(
    monkeypatch, run_async,
):
    """Precedence: explicit override beats the managed slot outright --
    proven by setting BOTH and confirming only the override comes back,
    which a test that only ever sets one or the other cannot show.
    """
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", OVERRIDE_URL)
    set_managed_tunnel_url(MANAGED_URL)
    try:
        result = run_async(resolve_public_url())
    finally:
        set_managed_tunnel_url("")

    assert result == OVERRIDE_URL
    assert result != MANAGED_URL


def test_managed_slot_is_unaffected_by_the_override_being_set(monkeypatch, run_async):
    """The override winning at resolution time must not itself clear or
    otherwise mutate the managed slot -- `get_managed_tunnel_url()` (the
    documented read-back seam) should still report what was last set.
    """
    monkeypatch.setattr("aipager.config.MINIAPP_PUBLIC_URL", OVERRIDE_URL)
    set_managed_tunnel_url(MANAGED_URL)
    try:
        run_async(resolve_public_url())
        assert get_managed_tunnel_url() == MANAGED_URL
    finally:
        set_managed_tunnel_url("")


def _web_app_urls(bot):
    """Every URL actually advertised as a `web_app` menu button -- as
    opposed to any `set_chat_menu_button` call at all, since a "no
    button" outcome is itself implemented as a call that clears to a
    non-web_app `MenuButtonCommands` (discovered empirically: a first
    draft of this test asserted zero `set_chat_menu_button` calls and
    failed against that legitimate clearing call, which is not a leak).
    """
    urls = []
    for call in bot._app.bot.set_chat_menu_button.await_args_list:
        button = call.kwargs.get("menu_button")
        if button is not None and getattr(button, "type", None) == "web_app":
            urls.append(button.web_app.url)
    return urls


def test_unreachable_override_still_yields_no_button(monkeypatch, run_async, mk_bot):
    """"the managed path must not fight" the shipped probe guard: an
    override that does not answer the probe must still publish no
    *web_app* button, exactly as the pre-existing guard
    (tests/test_miniapp_hardening.py) establishes for any URL, managed
    or not.
    """
    monkeypatch.setattr("aipager.miniapp.tunnel.probe_public_url",
                        AsyncMock(return_value=False))
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    bot = mk_bot()
    bot._app.bot.set_chat_menu_button = AsyncMock()

    run_async(bot.publish_miniapp_button(OVERRIDE_URL))

    assert _web_app_urls(bot) == [], (
        "an unreachable override URL still resulted in a web_app button being published"
    )


def test_reachable_override_publishes_normally(monkeypatch, run_async, mk_bot):
    """Control case for the previous test: prove the probe-mock itself is
    load-bearing by flipping it to reachable and confirming a web_app
    button IS published, carrying the override URL, for the very same
    override URL.
    """
    monkeypatch.setattr("aipager.miniapp.tunnel.probe_public_url",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")

    bot = mk_bot()
    bot._app.bot.set_chat_menu_button = AsyncMock()

    run_async(bot.publish_miniapp_button(OVERRIDE_URL))

    assert _web_app_urls(bot) == [OVERRIDE_URL]
