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


# Write-rate ceiling. A human tapping settings buttons never approaches
# this; a loop hammering the route over the tunnel does.
_WRITE_WINDOW_SECONDS = 60.0
_WRITE_MAX_PER_WINDOW = 30


class MiniAppServer:
    """``GET /`` (static shell) + read-only authenticated JSON routes.

    Stage 1 shipped ``/api/status``; stage 2 adds ``/api/sessions``,
    ``/api/sessions/{label}`` and ``/api/sessions/{label}/diff``. Every
    route stays strictly GET-only — none of them accept anything else.
    """

    def __init__(self, bot: "TelegramBot", registry: "SessionRegistry", port: int):
        self.bot = bot
        self.registry = registry
        self.port = port
        self._runner = None  # aiohttp.web.AppRunner | None, set in start()
        # monotonic — only used to compute an uptime delta, never
        # persisted or compared across process restarts.
        self._started_at = time.monotonic()
        # user_id -> recent write timestamps, for _allow_write.
        self._write_hits: dict = {}

    def _build_app(self) -> "web.Application":
        """Construct the aiohttp Application. Split out from start() so
        tests can exercise routes via aiohttp's test client without a
        real TCP bind."""
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/status", self._handle_status)
        app.router.add_get("/api/sessions", self._handle_sessions)
        app.router.add_get("/api/sessions/{label}", self._handle_session_detail)
        app.router.add_get("/api/sessions/{label}/diff", self._handle_session_diff)
        app.router.add_get("/api/preferences", self._handle_preferences_get)
        # The Mini App's first mutating route. PUT (not POST) because
        # setting a field to a value is idempotent by construction — the
        # same request twice leaves the same state. The field lives in the
        # path so batch 4's per-session variant can slot in beside it as
        # /api/sessions/{label}/preferences/{field} without reshaping this.
        app.router.add_put("/api/preferences/{field}", self._handle_preferences_put)
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

    async def _authenticate(self, request, route_name: str):
        """Shared auth gate for every JSON route: header read → initData
        verify → scope resolve. Returns the caller's ``scope_chat_id``
        (an ``int``) on success, or a ready-to-return ``web.Response``
        on failure — callers do ``result = await self._authenticate(...);
        if isinstance(result, web.Response): return result``.

        Not a new authorization system: still calls ``verify_init_data``
        and ``_resolve_scope_chat_id`` unmodified, just from one place
        instead of once per handler (design.md Decision 5). Every
        rejection path returns the same fixed, generic body per error
        class so a prober can't distinguish "bad signature" from
        "unknown user" from the outside — the same rule stage 1 applied
        to ``/api/status`` alone, now shared by every route.
        """
        result = await self._authenticate_user(request, route_name)
        if isinstance(result, tuple):
            return result[0]
        return result

    async def _authenticate_user(self, request, route_name: str):
        """The single implementation of the gate. Returns
        ``(scope_chat_id, user_id)`` or a ready-to-return ``web.Response``.

        Write routes need the user id twice over: to apply the same admin
        rule chat applies (``AuthMixin._is_admin_user``) and to attribute
        the change in the chat mirror. :meth:`_authenticate` is the
        narrower read-route view onto this same function — deliberately a
        delegation rather than a second copy, because two hand-maintained
        copies of an auth gate drift, and every later batch copies
        whichever one it happens to read first.
        """
        from aiohttp import web

        from aipager.config import BOT_TOKEN
        from aipager.miniapp.auth import (
            InitDataMissingError,
            InitDataSignatureError,
            InitDataStaleError,
            verify_init_data,
        )

        # Check the header's presence and validity BEFORE touching the
        # registry (design.md's non-negotiable).
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        try:
            user = verify_init_data(init_data, BOT_TOKEN)
        except InitDataMissingError:
            log.info("miniapp: %s rejected (401) — missing/malformed initData", route_name)
            return web.json_response({"error": "unauthorized"}, status=401)
        except InitDataSignatureError:
            log.info("miniapp: %s rejected (401) — bad signature", route_name)
            return web.json_response({"error": "unauthorized"}, status=401)
        except InitDataStaleError:
            log.info("miniapp: %s rejected (401) — stale auth_date", route_name)
            return web.json_response({"error": "unauthorized"}, status=401)

        user_id = user.get("id")
        scope_chat_id = self._resolve_scope_chat_id(user_id)
        if scope_chat_id is None:
            log.info("miniapp: %s rejected (403) — user not a scope member", route_name)
            return web.json_response({"error": "forbidden"}, status=403)

        log.debug("miniapp: %s authorized (scope_chat_id=%s)", route_name, scope_chat_id)
        return scope_chat_id, user_id

    async def _handle_status(self, request):
        from aiohttp import web

        result = await self._authenticate(request, "/api/status")
        if isinstance(result, web.Response):
            return result
        return web.json_response(self._build_status_payload(result))

    async def _handle_sessions(self, request):
        from aiohttp import web

        result = await self._authenticate(request, "/api/sessions")
        if isinstance(result, web.Response):
            return result
        return web.json_response(self._build_sessions_payload(result))

    async def _handle_preferences_get(self, request):
        from aiohttp import web

        from aipager.bot.settings_menu import settings_schema
        from aipager.preferences import get_preferences

        result = await self._authenticate_user(request, "/api/preferences")
        if isinstance(result, web.Response):
            return result
        scope_chat_id, user_id = result

        prefs = get_preferences(scope_chat_id)
        return web.json_response({
            # Schema and values travel together so the client never keeps
            # its own copy of the option list — one source of truth, shared
            # with the /settings inline keyboard.
            "schema": settings_schema(),
            "values": {
                "layout": prefs.layout,
                "simple_formatting": prefs.simple_formatting,
                "answer_length": prefs.answer_length,
                "language_level": prefs.language_level,
            },
            # Lets the UI disable controls it knows will be refused rather
            # than offering a button that always 403s. The server still
            # enforces it — this is a hint, not the gate.
            "can_edit": bool(self.bot._is_admin_user(user_id, scope_chat_id)),
        })

    async def _handle_preferences_put(self, request):
        from aiohttp import web

        from aipager.preferences import get_preferences, set_preference

        result = await self._authenticate_user(request, "/api/preferences/{field}")
        if isinstance(result, web.Response):
            return result
        scope_chat_id, user_id = result

        # Authentication proves *a* scope member; changing a setting that
        # affects everyone in the scope is admin-gated, exactly as the
        # `_:set:<section>:<value>` callback is in chat. Same helper, so
        # the two surfaces cannot drift apart.
        if not self.bot._is_admin_user(user_id, scope_chat_id):
            log.info("miniapp: preferences write rejected (403) — not an admin")
            return web.json_response({"error": "forbidden"}, status=403)

        if not self._allow_write(user_id):
            log.info("miniapp: preferences write rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        field = request.match_info["field"]
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(body, dict) or "value" not in body:
            return web.json_response({"error": "bad_request"}, status=400)
        value = body["value"]

        # `field` is validated by set_preference below; an unknown one never
        # reaches the comparison, so a plain getattr is enough here.
        before = getattr(get_preferences(scope_chat_id), field, None)
        try:
            # set_preference is the allow-list: it validates field name and
            # value BEFORE touching cache or disk and raises ValueError
            # otherwise. Reusing it means the Mini App cannot accept a value
            # chat would reject.
            prefs = set_preference(scope_chat_id, field, value)
        except ValueError:
            log.info("miniapp: preferences write rejected (400) — invalid field/value")
            return web.json_response({"error": "bad_request"}, status=400)

        after = getattr(prefs, field)
        if after != before:
            # Mirror to chat so the chat log stays the single audit trail —
            # only on a real change, so re-tapping the active option does
            # not spam the scope.
            await self._mirror_preference_change(scope_chat_id, user_id, field, after)

        return web.json_response({
            "values": {
                "layout": prefs.layout,
                "simple_formatting": prefs.simple_formatting,
                "answer_length": prefs.answer_length,
                "language_level": prefs.language_level,
            },
            "changed": after != before,
        })

    def _allow_write(self, user_id) -> bool:
        """Crude per-user write budget. The page is reachable over a public
        tunnel, so an unbounded write route plus any future bug is a bad
        combination; a human tapping settings buttons never approaches this
        ceiling."""
        now = time.monotonic()
        hits = [t for t in self._write_hits.get(user_id, ()) if now - t < _WRITE_WINDOW_SECONDS]
        if len(hits) >= _WRITE_MAX_PER_WINDOW:
            self._write_hits[user_id] = hits
            return False
        hits.append(now)
        self._write_hits[user_id] = hits
        return True

    async def _mirror_preference_change(self, scope_chat_id, user_id, field, value) -> None:
        """Post a one-line note to the originating chat. Best-effort: a
        failed mirror must never fail the write that already happened."""
        from aipager.bot.settings_menu import settings_schema

        label = field
        shown = value
        for section in settings_schema():
            if section["field"] != field:
                continue
            label = section["title"]
            for option in section["options"]:
                if option["value"] == value:
                    shown = option["label"]
                    break
            break
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id,
                text=f"⚙️ {label} → {shown} (changed from the Mini App)",
            )
        except Exception:
            log.debug("miniapp: preference mirror to chat failed", exc_info=True)

    async def _handle_session_detail(self, request):
        from aiohttp import web

        from aipager.miniapp.sessions import session_detail

        result = await self._authenticate(request, "/api/sessions/{label}")
        if isinstance(result, web.Response):
            return result
        scope_chat_id = result

        label = request.match_info["label"]
        # include_gone=True: viewing a finished session's final
        # timeline/diff is a legitimate, safe read (design.md Decision
        # 5). Resolved only within the caller's own scope — a label
        # belonging to a different scope must 404 identically to one
        # that doesn't exist anywhere (the headline requirement this
        # stage introduces).
        sess = self.registry.find_by_label(
            label, scope_chat_id=scope_chat_id, include_gone=True,
        )
        if sess is None:
            log.info("miniapp: /api/sessions/{label} rejected (404) — not found in scope")
            return web.json_response({"error": "not_found"}, status=404)

        return web.json_response(session_detail(sess, time.monotonic()))

    async def _handle_session_diff(self, request):
        from aiohttp import web

        from aipager.miniapp.diff import collect_diff

        result = await self._authenticate(request, "/api/sessions/{label}/diff")
        if isinstance(result, web.Response):
            return result
        scope_chat_id = result

        label = request.match_info["label"]
        sess = self.registry.find_by_label(
            label, scope_chat_id=scope_chat_id, include_gone=True,
        )
        if sess is None:
            log.info("miniapp: /api/sessions/{label}/diff rejected (404) — not found in scope")
            return web.json_response({"error": "not_found"}, status=404)

        # sess.cwd is the ONLY source of the path handed to git — it is
        # stamped server-side from the SessionStart hook payload
        # (dtach/hook_receiver.py:269-271) and never comes from this
        # request. There is no code path from an HTTP parameter to a
        # `cwd` argument passed to git (design.md Decision 2).
        return web.json_response(await collect_diff(sess.cwd))

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

    def _build_sessions_payload(self, scope_chat_id: int) -> dict:
        """Grid payload for ``GET /api/sessions``. Deliberately never
        invokes ``git`` — the polled endpoint stays fast regardless of
        pending diffs (design.md Decision 5). Applies the same
        ``hidden_from_status`` filter chat's ``/status`` uses
        (``bot/handlers.py:454-460``) to GONE sessions — stage 1's
        ``_build_status_payload`` never applied it; this closes that
        gap without touching stage 1's builder.
        """
        from aipager import __version__
        from aipager.miniapp.sessions import (
            grid_totals,
            session_summary,
            sort_for_display,
        )
        from aipager.state import Status

        now = time.monotonic()
        sessions = [
            session_summary(sess, now)
            for sess in self.registry.all_sessions(scope_chat_id).values()
            if not (sess.status == Status.GONE and sess.hidden_from_status)
        ]
        # Ordered server-side so the rule (waiting first, gone last, then
        # most-recently-active) is one pure function pytest can pin, rather
        # than a comparator buried in the page's JavaScript.
        sessions = sort_for_display(sessions)

        bot_user = getattr(self.bot._app, "bot", None) if self.bot._app else None
        bot_username = getattr(bot_user, "username", "") or ""
        return {
            "daemon": {
                "version": __version__,
                "bot_username": bot_username,
                "uptime_seconds": round(now - self._started_at),
            },
            "totals": grid_totals(sessions),
            "sessions": sessions,
        }


__all__ = ["MiniAppServer", "MiniAppUnavailable"]
