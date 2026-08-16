"""Self-hosted Telegram Mini App server (stage 1 — plumbing + auth).

Opt-in, loopback-bind-only HTTP server embedded in the daemon, serving
one read-only page (daemon status + session list) gated by full
``initData`` HMAC verification and reuse of the bot's existing
scope/role authorization. Off by default; see ``aipager.config`` for
the three ``MINIAPP_*`` env vars and ``aipager miniapp`` for the CLI
toggle.

This package (and its dependency on ``aiohttp``) is entirely optional —
nothing outside ``aipager.miniapp`` and its lazy-import call sites
(``cli/daemon.py``, ``bot/handlers.py``) imports it, so a base install
never pulls ``aiohttp`` in.
"""
