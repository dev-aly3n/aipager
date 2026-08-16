"""Loopback-only HTTP server embedding aipager's read-only Mini App.

Same start()/stop() coroutine shape as :class:`aipager.session_monitor.
SessionMonitor` and :class:`aipager.dtach.hook_receiver.HookReceiver` —
runs on the daemon's own event loop, never spawns a second one.

Security-critical: the host aiohttp binds to is hardcoded to
``127.0.0.1`` right below. Do not add a "host" option, read it from
config, or take it from args/env "for flexibility" — the tunnel
(Tailscale Funnel or similar, always operator-installed, never
bundled) is the sole intended ingress. See design.md's threat model.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from aipager.bot import TelegramBot
    from aipager.state import SessionRegistry

log = logging.getLogger(__name__)


class MiniAppUnavailable(Exception):
    """Raised from :meth:`MiniAppServer.start` when ``aiohttp`` isn't
    installed. Mirrors :class:`aipager.bot.voice.VoiceUnavailable`."""


class MiniAppServer:
    """``GET /`` (static shell) + ``GET /api/status`` (authenticated JSON).

    Stage 1 is strictly read-only — no other routes exist, and none of
    them accept anything but GET.
    """

    def __init__(self, bot: "TelegramBot", registry: "SessionRegistry", port: int):
        self.bot = bot
        self.registry = registry
        self.port = port
        self._runner = None  # aiohttp.web.AppRunner | None, set in start()
        # monotonic — only used to compute an uptime delta, never
        # persisted or compared across process restarts.
        self._started_at = time.monotonic()

    def _build_app(self) -> "web.Application":
        """Construct the aiohttp Application. Split out from start() so
        tests can exercise routes via aiohttp's test client without a
        real TCP bind."""
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/status", self._handle_status)
        return app

    async def start(self) -> None:
        try:
            from aiohttp import web
        except ImportError as e:
            raise MiniAppUnavailable(
                "aiohttp is not installed. Run:\n"
                "    uv tool install --reinstall 'aipager[miniapp]'\n"
                "or:\n"
                "    pip install 'aipager[miniapp]'"
            ) from e

        app = self._build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        # Host is hardcoded to loopback — see module docstring. Never
        # read from config, args, or env.
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()
        log.info("Mini App server listening on 127.0.0.1:%d", self.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            log.info("Mini App server stopped")

    async def _handle_index(self, request):
        from aiohttp import web

        from aipager.miniapp.static import INDEX_HTML
        return web.Response(text=INDEX_HTML, content_type="text/html")

    async def _handle_status(self, request):
        from aiohttp import web

        from aipager.config import BOT_TOKEN
        from aipager.miniapp.auth import (
            InitDataMissingError,
            InitDataSignatureError,
            InitDataStaleError,
            verify_init_data,
        )

        # Check the header's presence and validity BEFORE touching the
        # registry (design.md's non-negotiable). A fixed, generic body
        # on every rejection path so a prober can't distinguish "bad
        # signature" from "unknown user" from the outside.
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        try:
            user = verify_init_data(init_data, BOT_TOKEN)
        except InitDataMissingError:
            log.info("miniapp: /api/status rejected (401) — missing/malformed initData")
            return web.json_response({"error": "unauthorized"}, status=401)
        except InitDataSignatureError:
            log.info("miniapp: /api/status rejected (401) — bad signature")
            return web.json_response({"error": "unauthorized"}, status=401)
        except InitDataStaleError:
            log.info("miniapp: /api/status rejected (401) — stale auth_date")
            return web.json_response({"error": "unauthorized"}, status=401)

        user_id = user.get("id")
        scope_chat_id = self._resolve_scope_chat_id(user_id)
        if scope_chat_id is None:
            log.info("miniapp: /api/status rejected (403) — user not a scope member")
            return web.json_response({"error": "forbidden"}, status=403)

        return web.json_response(self._build_status_payload(scope_chat_id))

    def _resolve_scope_chat_id(self, user_id) -> int | None:
        """Authorization only — never re-derives the allow-list rules.

        A valid HMAC only proves "some Telegram user", never "an
        authorized one" (design.md threat model item 6). Reuses the
        bot's own scope/team lookup helpers exactly as every chat
        handler does, and returns the chat_id whose sessions this user
        may see — ``None`` means "not a member of any configured scope".
        """
        if not isinstance(user_id, int):
            return None
        if self.bot.scopes is not None:
            for scope in self.bot.scopes:
                if self.bot._member_in_scope(scope, user_id) is not None:
                    return scope.chat_id
            return None

        from aipager.config import CHAT_ID
        try:
            chat_id = int(CHAT_ID)
        except (TypeError, ValueError):
            return None

        if self.bot.team is not None:
            return chat_id if self.bot.team.get(user_id) is not None else None

        # Personal mode: no allow-list configured. This is NOT the same
        # trust model as a DM in personal mode today — a DM's /status
        # never leaves Telegram and is rate-limited/friction-bounded by
        # the Telegram client itself, whereas this credential (and the
        # tunnel URL that produced it) is usable over the raw internet
        # once obtained. So, unlike every other personal-mode command,
        # require the caller to actually BE the operator rather than
        # "any Telegram user with a validly-signed initData" — see
        # AuthMixin._is_personal_mode_operator.
        if not self.bot._is_personal_mode_operator(user_id):
            return None
        return chat_id

    def _build_status_payload(self, scope_chat_id: int) -> dict:
        from aipager import __version__

        now = time.monotonic()
        sessions = []
        for sess in self.registry.all_sessions(scope_chat_id).values():
            last_active = (
                round(now - sess.last_hook_at) if sess.last_hook_at else None
            )
            sessions.append({
                "label": sess.label,
                "status": sess.status.name.lower(),
                "model": sess.model_name or "",
                "context_pct": sess.last_token_pct or 0,
                "cost_usd": round(sess.last_cost_usd or 0.0, 4),
                "last_active_seconds_ago": last_active,
            })

        bot_user = getattr(self.bot._app, "bot", None) if self.bot._app else None
        bot_username = getattr(bot_user, "username", "") or ""
        return {
            "daemon": {
                "version": __version__,
                "bot_username": bot_username,
                "uptime_seconds": round(now - self._started_at),
            },
            "sessions": sessions,
        }


__all__ = ["MiniAppServer", "MiniAppUnavailable"]
