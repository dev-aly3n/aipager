"""Self-hosted Telegram Mini App server (stage 1 — plumbing + auth).

Opt-in, loopback-bind-only HTTP server embedded in the daemon, serving
one read-only page (daemon status + session list) gated by full
``initData`` HMAC verification and reuse of the bot's existing
scope/role authorization. Off by default; see ``aipager.config`` for
the three ``MINIAPP_*`` env vars and ``aipager miniapp`` for the CLI
toggle.

``aiohttp`` ships with aipager (a base dependency), so the server is
always *able* to run — the feature itself is still off by default.

Everything here is nonetheless reached through lazy imports from
``cli/daemon.py`` and ``bot/handlers.py``, and nothing outside
``aipager.miniapp`` imports it at module level. That is about **import
cost**, not availability: the hook scripts run on every Claude Code
event against a <5 ms budget, and aiohttp is the largest dependency in
the tree. See ``tests/test_no_aiohttp_in_base_modules.py``.
"""
