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
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from aipager.bot import TelegramBot
    from aipager.state import SessionRegistry

log = logging.getLogger(__name__)


# Short "what is this for" lines for the model picker. Keyed by the alias
# label, lowercased; an alias with no entry simply shows no hint rather
# than a wrong one. Deliberately no version numbers — see the comment at
# the /api/session-options payload.
_MODEL_HINTS = {
    "opus": "Most capable — deep reasoning, hardest problems",
    "sonnet": "Balanced — the everyday default",
    "haiku": "Fastest and cheapest — quick edits and lookups",
    "opusplan": "Opus for planning, Sonnet to execute",
    "fable": "Newest family alias",
}

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
        # The only route in aipager that can spawn a process. Gated
        # like chat's /new: can_prompt to create at all, plus admin
        # for Auto mode, and the working directory is checked against
        # an allow-list server-side (miniapp/launch.py).
        app.router.add_post("/api/sessions", self._handle_session_create)
        app.router.add_get("/api/session-options", self._handle_session_options)
        # Creating a folder is its own route rather than a field on the
        # create request: a launch that fails after the mkdir would leave
        # a stray directory behind, and the picker needs the new path
        # back so it can offer it *selected* before the operator commits.
        app.router.add_post("/api/directories", self._handle_directory_create)
        app.router.add_get("/api/sessions/{label}", self._handle_session_detail)
        app.router.add_get("/api/sessions/{label}/diff", self._handle_session_diff)
        # Session detail-page write actions (Stop/Kill/Resume/Delete) — all
        # four share their real implementation with chat's own /stop, /kill
        # and /resume via aipager.bot.session_ops's *_core methods, and are
        # gated by _can_prompt_user (not _is_admin_user), the same bar chat
        # itself applies for the identical capability (design.md
        # Authorization).
        app.router.add_post("/api/sessions/{label}/stop", self._handle_session_stop)
        app.router.add_post("/api/sessions/{label}/kill", self._handle_session_kill)
        app.router.add_post(
            "/api/sessions/{label}/resume", self._handle_session_resume,
        )
        app.router.add_delete("/api/sessions/{label}", self._handle_session_delete)
        # Five more session-menu actions (design.md: perms switch, clear
        # queue, compact, restart, rename). Same five-step shape as the
        # four routes above; perms additionally requires _is_admin_user
        # when the switch targets Auto mode.
        app.router.add_post("/api/sessions/{label}/perms", self._handle_session_perms)
        app.router.add_post(
            "/api/sessions/{label}/clearqueue", self._handle_session_clearqueue,
        )
        app.router.add_post(
            "/api/sessions/{label}/compact", self._handle_session_compact,
        )
        app.router.add_post(
            "/api/sessions/{label}/restart", self._handle_session_restart,
        )
        app.router.add_post(
            "/api/sessions/{label}/rename", self._handle_session_rename,
        )
        app.router.add_get("/api/preferences", self._handle_preferences_get)
        # The Mini App's first mutating route. PUT (not POST) because
        # setting a field to a value is idempotent by construction — the
        # same request twice leaves the same state. The field lives in the
        # path so batch 4's per-session variant can slot in beside it as
        # /api/sessions/{label}/preferences/{field} without reshaping this.
        app.router.add_put("/api/preferences/{field}", self._handle_preferences_put)
        # Per-session preference overrides (batch 4) — same shape as the
        # scope-wide route above, one path segment deeper. GET is any
        # scope member (matches _handle_session_detail); PUT/DELETE are
        # gated by _can_prompt_user, not _is_admin_user (design.md
        # Authorization).
        app.router.add_get(
            "/api/sessions/{label}/preferences", self._handle_session_preferences_get,
        )
        app.router.add_put(
            "/api/sessions/{label}/preferences/{field}",
            self._handle_session_preferences_put,
        )
        app.router.add_delete(
            "/api/sessions/{label}/preferences/{field}",
            self._handle_session_preferences_delete,
        )
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

    async def _handle_session_create(self, request):
        from aiohttp import web

        from aipager.config import MODEL_CHOICES
        from aipager.miniapp.launch import (
            allowed_roots,
            validate_cwd,
            validate_model,
            validate_session_name,
        )
        from aipager.state import Status

        result = await self._authenticate_user(request, "POST /api/sessions")
        if isinstance(result, web.Response):
            return result
        scope_chat_id, user_id = result

        # Creating a session is the same capability as driving one — the
        # same gate batch 4 established for session-scoped writes. A
        # READ_ONLY member cannot prompt, so must not be able to spawn.
        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info("miniapp: session create rejected (403) — caller cannot prompt")
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session create rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad_request"}, status=400)

        name, err = validate_session_name(body.get("name"))
        if err:
            return web.json_response({"error": "bad_request", "detail": err}, status=400)

        skip_perms = body.get("skip_perms") is True
        # Auto mode is admin-gated in chat (`/new !name`, handlers.py).
        # Without the same gate here, the Mini App would be a way to get
        # --dangerously-skip-permissions that chat deliberately denies.
        if skip_perms and not self.bot._is_admin_user(user_id, scope_chat_id):
            return web.json_response(
                {"error": "forbidden", "detail": "Auto mode requires admin."},
                status=403,
            )

        cwd, err = validate_cwd(
            body.get("cwd"), allowed_roots(self.registry, scope_chat_id),
        )
        if err:
            return web.json_response({"error": "bad_request", "detail": err}, status=400)

        # The model may be one of the chat keyboard's aliases or a full
        # name the CLI accepts ("claude-opus-5"), so it is pattern-checked
        # rather than enumerated. It is NOT queued as a `/model` prompt: a
        # queued prompt drains on the session's first IDLE, which is after
        # the operator's first real message has been answered, so it
        # produced a spurious second turn and a duplicate answer. It goes
        # to the launch as `--model`.
        chosen_model, err = validate_model(body.get("model"), MODEL_CHOICES)
        if err:
            return web.json_response({"error": "bad_request", "detail": err}, status=400)

        # Answer a name collision before spawning anything. Chat offers
        # Resume/Replace/Cancel buttons here; the Mini App says so inline
        # so the operator renames instead of getting a failed POST.
        existing = self.registry.find_by_label(
            name, scope_chat_id=scope_chat_id, include_gone=True,
        )
        if existing is not None and (
            existing.status != Status.GONE or existing.claude_session_id
        ):
            return web.json_response({
                "error": "conflict",
                "detail": f"A session named {name} already exists in this chat.",
            }, status=409)

        session_name, err = await self.bot.create_session(
            name, scope_chat_id=scope_chat_id, skip_perms=skip_perms,
            cwd=cwd or None, driver_user_id=user_id,
            model=chosen_model or None,
        )
        if not session_name:
            log.info("miniapp: session create failed — launch refused")
            return web.json_response({"error": "launch_failed", "detail": err}, status=400)

        await self._mirror_session_created(scope_chat_id, name, skip_perms, cwd)
        return web.json_response({"label": name, "session_name": session_name})

    async def _mirror_session_created(self, scope_chat_id, label, skip_perms, cwd) -> None:
        """Announce the new session in chat. Best-effort — the process is
        already running by now, so a failed notification must not turn a
        successful launch into an error the operator might retry."""
        mode = "🤖 Auto" if skip_perms else "💬 Ask"
        where = f"\n📁 {cwd}" if cwd else ""
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id,
                text=f"🚀 {label} created from the Mini App · {mode}{where}",
            )
        except Exception:
            log.debug("miniapp: session-created mirror failed", exc_info=True)

    async def _handle_directory_create(self, request):
        """Create one folder inside a directory this scope already works in.

        The only route in aipager that writes to the filesystem outside
        its own config. It is contained the same way the create route is:
        the parent goes through the unchanged `validate_cwd`, the leaf is
        a single validated path segment, and the result is re-resolved
        after the mkdir before it is handed back.
        """
        from aiohttp import web

        from aipager.miniapp.launch import allowed_roots, create_directory

        result = await self._authenticate_user(request, "POST /api/directories")
        if isinstance(result, web.Response):
            return result
        scope_chat_id, user_id = result

        # Same gate as creating a session: a READ_ONLY member cannot
        # prompt, so must not be able to write to the operator's disk.
        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info("miniapp: directory create rejected (403) — caller cannot prompt")
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: directory create rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad_request"}, status=400)

        path, existed, err = create_directory(
            body.get("parent"), body.get("name"),
            allowed_roots(self.registry, scope_chat_id),
        )
        if err:
            return web.json_response({"error": "bad_request", "detail": err}, status=400)

        log.info("miniapp: directory ready (existed=%s)", existed)
        if not existed:
            await self._mirror_directory_created(scope_chat_id, path)
        return web.json_response({"path": path, "existed": existed})

    async def _mirror_directory_created(self, scope_chat_id, path) -> None:
        """Tell the chat a folder appeared on the operator's machine.

        Every other Mini App write mirrors, and this one writes to disk
        from a phone — the chat is the only channel the operator actually
        reads. Only a genuinely new folder is announced: reporting the
        reuse of an existing one would be noise for a no-op.
        """
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id,
                text=f"📁 New folder created from the Mini App:\n{path}",
            )
        except Exception:
            log.debug("miniapp: directory-created mirror failed", exc_info=True)

    async def _handle_session_options(self, request):
        """Directory choices + the scope's defaults, for the create form.

        The picker is seeded server-side from directories this scope has
        actually used: the client never proposes a path the server has not
        already sanctioned, and the same list is what `validate_cwd`
        checks against.
        """
        from aiohttp import web

        from aipager.bot.settings_menu import settings_schema
        from aipager.miniapp.launch import allowed_roots
        from aipager.preferences import get_preferences

        result = await self._authenticate_user(request, "/api/session-options")
        if isinstance(result, web.Response):
            return result
        scope_chat_id, user_id = result

        prefs = get_preferences(scope_chat_id)
        from aipager.config import MODEL_CHOICES

        roots = allowed_roots(self.registry, scope_chat_id)
        # Which entry is the one a session lands in when nobody picks. The
        # client tags it `default` instead of offering a second "Default"
        # choice beside it — since _PROJECT_DIR became a root, the two were
        # the same directory listed twice. Reported only when it is really
        # in the list: `allowed_roots` drops it for `/` or a missing path,
        # and a tag pointing at nothing would be worse than no tag.
        from aipager.dtach import inject
        try:
            project_dir = os.path.realpath(inject._PROJECT_DIR or "")
        except OSError:
            project_dir = ""
        default_directory = project_dir if project_dir in roots else ""

        return web.json_response({
            "directories": roots,
            "default_directory": default_directory,
            # One source of truth for model names — the same list the chat
            # keyboard offers, so the two surfaces cannot drift.
            #
            # An alias means "the latest of that family" (`claude --help`:
            # "Provide an alias for the latest model ... or a model's full
            # name"), so no version is baked in here — a hardcoded "Opus 5"
            # would be wrong the day the next one ships. The running
            # session reports its actual version via the statusline, and
            # that is what the grid and session page display.
            "models": [
                {"label": label, "hint": _MODEL_HINTS.get(label.lower(), "")}
                for label, _send in MODEL_CHOICES
            ],
            "schema": settings_schema(),
            "scope_defaults": {
                "layout": prefs.layout,
                "simple_formatting": prefs.simple_formatting,
                "answer_length": prefs.answer_length,
                "language_level": prefs.language_level,
            },
            "can_create": bool(self.bot._can_prompt_user(user_id, scope_chat_id)),
            "can_use_auto": bool(self.bot._is_admin_user(user_id, scope_chat_id)),
        })

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

    # ---- per-session preference overrides (batch 4) ----------------------

    def _session_preferences_values(self, sess) -> dict:
        """The ``values`` block shared by GET/PUT/DELETE on a session's
        preferences — one entry per settable field, each carrying the four
        numbers the client needs and nothing it has to compute itself:

        - ``effective``: what this session will actually use right now —
          from :func:`resolve_preferences`, WITH the override applied.
        - ``scope_default``: the scope's own current value — from
          :func:`get_preferences`, WITHOUT any override. Deliberately a
          second, independent call rather than derived from ``effective``:
          design.md's mechanic requires the "default" tag to track the
          scope's value regardless of what this session chose, and the two
          numbers must never be allowed to contaminate each other.
        - ``override_value``: the raw stored override, or ``None`` when
          unset.
        - ``overridden``: whether an override is stored at all — independent
          of whether it happens to equal the scope default (mechanic case
          2: "explicitly set to the same value as the scope" still counts
          as overridden).

        The client then does zero resolution of its own: per option,
        ``selected = option.value == values[field].effective`` and
        ``default_tag = option.value == values[field].scope_default``.
        """
        from aipager.preferences import get_preferences, resolve_preferences
        from aipager.state import PREFERENCE_OVERRIDE_FIELDS

        overrides = sess.preference_overrides()
        effective = resolve_preferences(sess.scope_chat_id or 0, overrides)
        scope_default = get_preferences(sess.scope_chat_id or 0)
        return {
            field: {
                "effective": getattr(effective, field),
                "scope_default": getattr(scope_default, field),
                "override_value": overrides.get(field),
                "overridden": field in overrides,
            }
            for field in PREFERENCE_OVERRIDE_FIELDS
        }

    async def _resolve_own_scope_session(self, request, route_name: str):
        """Shared auth → label-resolution prelude for all three session-
        preference routes. Returns ``(sess, scope_chat_id, user_id)`` on
        success, or a ready-to-return ``web.Response``.

        Mirrors ``_handle_session_detail``'s pattern exactly:
        ``find_by_label(..., include_gone=True)`` scoped to the caller's
        own ``scope_chat_id`` so a foreign-scope label 404s byte-identically
        to one that doesn't exist anywhere — this stage adds no new way to
        probe for a session's existence across scopes. ``include_gone=True``
        because viewing (and, for a GONE-but-not-yet-evicted session,
        adjusting) its settings is as legitimate a read as the session
        detail page already treats it.
        """
        from aiohttp import web

        result = await self._authenticate_user(request, route_name)
        if isinstance(result, web.Response):
            return result
        scope_chat_id, user_id = result

        label = request.match_info["label"]
        sess = self.registry.find_by_label(
            label, scope_chat_id=scope_chat_id, include_gone=True,
        )
        if sess is None:
            log.info("miniapp: %s rejected (404) — not found in scope", route_name)
            return web.json_response({"error": "not_found"}, status=404)

        return sess, scope_chat_id, user_id

    async def _handle_session_preferences_get(self, request):
        from aiohttp import web

        from aipager.bot.settings_menu import settings_schema

        result = await self._resolve_own_scope_session(
            request, "/api/sessions/{label}/preferences",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        return web.json_response({
            "schema": settings_schema(),
            "values": self._session_preferences_values(sess),
            # can_edit here is _can_prompt_user, NOT _is_admin_user — see
            # design.md Authorization. A READ_ONLY member reads fine
            # (scope membership alone gates GET) but sees controls greyed
            # out, exactly as the scope-wide Settings tab does for a
            # non-admin.
            "can_edit": bool(self.bot._can_prompt_user(user_id, scope_chat_id)),
        })

    async def _handle_session_preferences_put(self, request):
        from aiohttp import web

        from aipager.preferences import is_valid_value
        from aipager.state import PREFERENCE_OVERRIDE_FIELDS

        result = await self._resolve_own_scope_session(
            request, "/api/sessions/{label}/preferences/{field}",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        # Session-scoped write: gated by _can_prompt_user, NOT
        # _is_admin_user. Anyone already authorized to drive this session
        # could achieve the same effect turn by turn; admin-gating a
        # durable version of that would be strictly more restrictive than
        # the capability this caller already holds (design.md
        # Authorization). READ_ONLY members fail this and get 403.
        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session preferences write rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)

        if not self._allow_write(user_id):
            log.info("miniapp: session preferences write rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        field = request.match_info["field"]
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(body, dict) or "value" not in body:
            return web.json_response({"error": "bad_request"}, status=400)
        value = body["value"]

        # Same allow-list a scope write validates through
        # (set_preference's is_valid_value) — the UI is not the gate, and
        # a session override can never accept a value chat's /settings
        # would reject.
        if field not in PREFERENCE_OVERRIDE_FIELDS or not is_valid_value(field, value):
            log.info("miniapp: session preferences write rejected (400) — invalid field/value")
            return web.json_response({"error": "bad_request"}, status=400)

        before = sess.preference_overrides().get(field)
        setattr(sess, PREFERENCE_OVERRIDE_FIELDS[field], value)
        self.registry.mark_dirty()

        # `None -> "short"` counts as changed even when "short" already
        # equals the scope default — an explicit choice was just recorded
        # (design.md mechanic case 2), so this compares the override
        # itself, never `effective`.
        changed = before != value
        if changed:
            await self._mirror_session_preference_change(
                scope_chat_id, sess.label, field, value, reset=False,
            )

        return web.json_response({
            "values": self._session_preferences_values(sess),
            "changed": changed,
        })

    async def _handle_session_preferences_delete(self, request):
        from aiohttp import web

        from aipager.preferences import get_preferences
        from aipager.state import PREFERENCE_OVERRIDE_FIELDS

        result = await self._resolve_own_scope_session(
            request, "/api/sessions/{label}/preferences/{field}",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session preferences delete rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)

        if not self._allow_write(user_id):
            log.info("miniapp: session preferences delete rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        field = request.match_info["field"]
        if field not in PREFERENCE_OVERRIDE_FIELDS:
            return web.json_response({"error": "bad_request"}, status=400)

        attr = PREFERENCE_OVERRIDE_FIELDS[field]
        # Idempotent: clearing an already-unset field is a no-op — no
        # write, no mirror, still 200 with changed: false.
        had_override = getattr(sess, attr) is not None
        if had_override:
            setattr(sess, attr, None)
            self.registry.mark_dirty()
            new_value = getattr(get_preferences(scope_chat_id or 0), field)
            await self._mirror_session_preference_change(
                scope_chat_id, sess.label, field, new_value, reset=True,
            )

        return web.json_response({
            "values": self._session_preferences_values(sess),
            "changed": had_override,
        })

    async def _mirror_session_preference_change(
        self, scope_chat_id, session_label: str, field: str, value, *, reset: bool,
    ) -> None:
        """Same purpose as :meth:`_mirror_preference_change`, extended to
        name the session — the chat log stays the audit trail for a
        per-session override too, even though `/settings` itself is not
        touched (design.md: "`/settings` in chat stays scope-level,
        unchanged"). Best-effort: a failed mirror must never fail a write
        that already happened.
        """
        from aipager.bot.settings_menu import settings_schema

        field_label = field
        shown = value
        for section in settings_schema():
            if section["field"] != field:
                continue
            field_label = section["title"]
            for option in section["options"]:
                if option["value"] == value:
                    shown = option["label"]
                    break
            break

        if reset:
            text = (
                f"⚙️ {field_label} reset to default ({shown}) for session "
                f"{session_label} (changed from the Mini App)"
            )
        else:
            text = (
                f"⚙️ {field_label} → {shown} for session {session_label} "
                f"(changed from the Mini App)"
            )
        try:
            await self.bot._app.bot.send_message(chat_id=scope_chat_id, text=text)
        except Exception:
            log.debug("miniapp: session preference mirror to chat failed", exc_info=True)

    async def _handle_session_detail(self, request):
        from aiohttp import web

        from aipager.miniapp.sessions import session_detail

        # include_gone=True: viewing a finished session's final
        # timeline/diff is a legitimate, safe read (design.md Decision
        # 5). Resolved only within the caller's own scope — a label
        # belonging to a different scope must 404 identically to one
        # that doesn't exist anywhere. _resolve_own_scope_session does
        # this identical lookup and returns the identical 404 body/log
        # line as the inline block this replaces.
        result = await self._resolve_own_scope_session(
            request, "/api/sessions/{label}",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        can_act = self.bot._can_prompt_user(user_id, scope_chat_id)
        is_admin = self.bot._is_admin_user(user_id, scope_chat_id)
        return web.json_response(
            session_detail(sess, time.monotonic(), can_act=can_act, is_admin=is_admin),
        )

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

    # ---- session detail-page write actions (Stop/Kill/Resume/Delete) -----
    #
    # Every one of the four follows the same five-step shape
    # _handle_session_preferences_put already uses:
    # _resolve_own_scope_session -> _can_prompt_user (403) ->
    # _allow_write (429) -> action-specific state guard (409/400) ->
    # the shared session_ops core, then mirror to chat on success only.

    async def _handle_session_stop(self, request):
        from aiohttp import web

        result = await self._resolve_own_scope_session(
            request, "POST /api/sessions/{label}/stop",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session stop rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session stop rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        outcome = await self.bot._stop_session_core(sess)
        if not outcome.ok:
            return web.json_response({
                "error": "not_busy",
                "detail": "This session isn't busy right now — nothing to stop.",
            }, status=409)

        await self._mirror_session_stopped(
            scope_chat_id, outcome.label, outcome.dropped,
        )
        return web.json_response({
            "status": "stopped", "label": outcome.label, "dropped": outcome.dropped,
        })

    async def _handle_session_kill(self, request):
        from aiohttp import web

        result = await self._resolve_own_scope_session(
            request, "POST /api/sessions/{label}/kill",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session kill rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session kill rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        outcome = await self.bot._kill_session_core(sess.name, sess.label)
        if outcome.result == "killed":
            await self._mirror_session_killed(scope_chat_id, outcome.label)
            return web.json_response({"status": "killed", "label": outcome.label})
        if outcome.result == "still_running":
            return web.json_response({
                "error": "still_running",
                "detail": "Could not kill — the process is still running. Try again.",
            }, status=409)
        # "not_found": the dtach socket was already gone by the time we
        # looked (a post-lookup race). Deliberately the SAME minimal body
        # as the route-level 404 above, not a distinct code — both cases
        # produce the identical, correct client reaction: treat the
        # session as gone and return to the grid (design.md Risks).
        return web.json_response({"error": "not_found"}, status=404)

    async def _handle_session_resume(self, request):
        from aiohttp import web

        from aipager.miniapp.sessions import NO_TRANSCRIPT_REASON

        result = await self._resolve_own_scope_session(
            request, "POST /api/sessions/{label}/resume",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session resume rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session resume rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        label = sess.label
        # No skip_perms_override: the Mini App always resumes with the
        # session's own persisted skip_perms, matching chat's bare
        # `/resume <name>` command (design.md Out of scope — the picker's
        # Ask/Auto override is a chat-callback-only affordance).
        outcome = await self.bot._do_resume_core(sess, driver_user_id=user_id)
        if outcome.reason == "not_gone":
            return web.json_response({
                "error": "not_gone", "detail": "This session is already running.",
            }, status=409)
        if outcome.reason == "no_transcript":
            return web.json_response({
                "error": "no_transcript", "detail": NO_TRANSCRIPT_REASON,
            }, status=409)
        if outcome.reason == "launch_failed":
            # Matches the existing _handle_session_create precedent of
            # mapping a launch failure to 400.
            return web.json_response({
                "error": "launch_failed", "detail": outcome.err,
            }, status=400)

        await self._mirror_session_resumed(scope_chat_id, label)
        return web.json_response({"status": "resumed", "label": label})

    async def _handle_session_delete(self, request):
        from aiohttp import web

        from aipager.state import Status

        result = await self._resolve_own_scope_session(
            request, "DELETE /api/sessions/{label}",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session delete rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session delete rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        if sess.status != Status.GONE:
            return web.json_response({
                "error": "conflict",
                "detail": "Session is still running. Use Kill to stop it first.",
            }, status=409)

        label = sess.label
        # registry.remove() does NOT call mark_dirty() itself (state.py)
        # — easy to forget, called out explicitly here and in design.md
        # Risks. Without this, a page refresh would still show the
        # session because the registry never persists the removal.
        self.registry.remove(sess.name)
        self.registry.mark_dirty()

        await self._mirror_session_deleted(scope_chat_id, label)
        return web.json_response({"status": "deleted", "label": label})

    # ---- session detail-page write actions: perms / clearqueue /
    #      compact / restart / rename (design.md: "Mini App session menu
    #      actions") -----------------------------------------------------
    #
    # Same five-step shape as Stop/Kill/Resume/Delete above:
    # _resolve_own_scope_session -> _can_prompt_user (403) -> [admin
    # check, perms only] -> _allow_write (429) -> action-specific state
    # guard (409/400) -> the shared session_ops core -> mirror to chat on
    # success only.

    def _restart_outcome_response(self, outcome, *, still_stopping_detail: str):
        """Map a :class:`~aipager.bot.session_ops.RestartOutcome`'s
        failure reasons to the JSON response the route should return.
        Returns ``None`` when ``outcome.ok`` — the caller then builds
        its own success body.

        Shared between perms and restart: both routes call all the way
        through to :meth:`~aipager.bot.session_ops.SessionOpsMixin.
        _kill_and_relaunch_core`, so both refuse with the identical set
        of reasons — only the still-stopping wording differs per route.
        """
        from aiohttp import web

        if outcome.reason == "not_live":
            return web.json_response({
                "error": "not_live", "detail": "Session isn't running.",
            }, status=409)
        if outcome.reason == "already_restarting":
            return web.json_response({
                "error": "already_restarting",
                "detail": "This session is already restarting — wait a moment.",
            }, status=409)
        if outcome.reason == "still_stopping":
            return web.json_response({
                "error": "still_stopping", "detail": still_stopping_detail,
            }, status=409)
        if outcome.reason == "launch_failed":
            return web.json_response({
                "error": "launch_failed", "detail": outcome.err,
            }, status=400)
        return None

    async def _handle_session_perms(self, request):
        from aiohttp import web

        result = await self._resolve_own_scope_session(
            request, "POST /api/sessions/{label}/perms",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session perms rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)

        # The client never sends a target — the server derives it by
        # toggling the session's own current mode, closing off a
        # body-tampering surface (design.md).
        target_skip_perms = not sess.skip_perms
        # Auto mode requires admin — the same gate chat's own /perms and
        # session creation apply. Switching TO Ask never needs admin.
        if target_skip_perms and not self.bot._is_admin_user(user_id, scope_chat_id):
            log.info("miniapp: session perms rejected (403) — Auto requires admin")
            return web.json_response(
                {"error": "forbidden", "detail": "Auto mode requires admin."},
                status=403,
            )

        if not self._allow_write(user_id):
            log.info("miniapp: session perms rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        outcome = await self.bot._perms_switch_core(sess, target_skip_perms)
        resp = self._restart_outcome_response(
            outcome,
            still_stopping_detail=(
                f"{outcome.label} is still stopping — mode not changed. "
                f"Try again in a moment."
            ),
        )
        if resp is not None:
            return resp

        await self._mirror_session_perms_switched(
            scope_chat_id, outcome.label, target_skip_perms,
        )
        return web.json_response({
            "status": "switched", "label": outcome.label,
            "skip_perms": target_skip_perms,
        })

    async def _handle_session_clearqueue(self, request):
        from aiohttp import web

        from aipager.miniapp.sessions import QUEUE_EMPTY_REASON

        result = await self._resolve_own_scope_session(
            request, "POST /api/sessions/{label}/clearqueue",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session clearqueue rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session clearqueue rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        outcome = await self.bot._clear_queue_core(sess)
        if not outcome.ok:
            return web.json_response({
                "error": "queue_empty", "detail": QUEUE_EMPTY_REASON,
            }, status=409)

        await self._mirror_session_queue_cleared(
            scope_chat_id, outcome.label, outcome.dropped,
        )
        return web.json_response({
            "status": "cleared", "label": outcome.label, "dropped": outcome.dropped,
        })

    async def _handle_session_compact(self, request):
        from aiohttp import web

        from aipager.miniapp.sessions import QUEUE_FULL_REASON

        result = await self._resolve_own_scope_session(
            request, "POST /api/sessions/{label}/compact",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session compact rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session compact rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        outcome = await self.bot._compact_session_core(sess)
        if outcome.reason == "not_live":
            return web.json_response({
                "error": "not_live", "detail": "Session isn't running.",
            }, status=409)
        if outcome.reason == "queue_full":
            return web.json_response({
                "error": "queue_full", "detail": QUEUE_FULL_REASON,
            }, status=409)
        if outcome.reason == "send_failed":
            return web.json_response({
                "error": "send_failed", "detail": "Couldn't send /compact.",
            }, status=400)

        await self._mirror_session_compacted(scope_chat_id, outcome.label)
        # "queued" when the session was busy, "sent" when it ran
        # immediately (IDLE) — outcome.reason already carries the exact
        # distinction, straight from _compact_session_core.
        return web.json_response({"status": outcome.reason, "label": outcome.label})

    async def _handle_session_restart(self, request):
        from aiohttp import web

        result = await self._resolve_own_scope_session(
            request, "POST /api/sessions/{label}/restart",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session restart rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session restart rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        outcome = await self.bot._restart_session_core(sess)
        resp = self._restart_outcome_response(
            outcome,
            still_stopping_detail=(
                f"{outcome.label} is still stopping. Try again in a moment."
            ),
        )
        if resp is not None:
            return resp

        await self._mirror_session_restarted(scope_chat_id, outcome.label)
        return web.json_response({"status": "restarted", "label": outcome.label})

    async def _handle_session_rename(self, request):
        from aiohttp import web

        from aipager.miniapp.launch import validate_session_name
        from aipager.state import Status

        result = await self._resolve_own_scope_session(
            request, "POST /api/sessions/{label}/rename",
        )
        if isinstance(result, web.Response):
            return result
        sess, scope_chat_id, user_id = result

        if not self.bot._can_prompt_user(user_id, scope_chat_id):
            log.info(
                "miniapp: session rename rejected (403) — "
                "caller cannot prompt this session",
            )
            return web.json_response({"error": "forbidden"}, status=403)
        if not self._allow_write(user_id):
            log.info("miniapp: session rename rejected (429) — rate limited")
            return web.json_response({"error": "too_many_requests"}, status=429)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad_request"}, status=400)

        new_label, err = validate_session_name(body.get("label"))
        if err:
            return web.json_response({"error": "bad_request", "detail": err}, status=400)

        previous_label = sess.label
        if new_label != previous_label:
            # Same collision rule POST /api/sessions applies (design.md
            # Unknown 3): a GONE session with a resumable transcript
            # still blocks — find_by_label has no tiebreaker, so two
            # entries claiming the same label the moment the GONE one
            # is resumed makes every future lookup non-deterministic.
            existing = self.registry.find_by_label(
                new_label, scope_chat_id=scope_chat_id, include_gone=True,
            )
            if existing is not None and (
                existing.status != Status.GONE or existing.claude_session_id
            ):
                return web.json_response({
                    "error": "conflict",
                    "detail": f"A session named {new_label} already exists in this chat.",
                }, status=409)

        outcome = await self.bot._rename_session_core(sess, new_label)
        if outcome.changed:
            await self._mirror_session_renamed(
                scope_chat_id, outcome.previous_label, outcome.new_label,
            )
        return web.json_response({
            "status": "renamed", "label": outcome.new_label,
            "previous_label": outcome.previous_label, "changed": outcome.changed,
        })

    async def _mirror_session_perms_switched(
        self, scope_chat_id, label, skip_perms,
    ) -> None:
        icon = "🤖" if skip_perms else "💬"
        mode_label = "Auto" if skip_perms else "Ask"
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id,
                text=f"{icon} {label} switched to {mode_label} mode from the Mini App",
            )
        except Exception:
            log.debug("miniapp: session-perms-switched mirror failed", exc_info=True)

    async def _mirror_session_queue_cleared(self, scope_chat_id, label, dropped) -> None:
        plural = "" if dropped == 1 else "s"
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id,
                text=(
                    f"🧹 Cleared {dropped} queued message{plural} for "
                    f"[{label}] from the Mini App"
                ),
            )
        except Exception:
            log.debug("miniapp: session-queue-cleared mirror failed", exc_info=True)

    async def _mirror_session_compacted(self, scope_chat_id, label) -> None:
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id,
                text=f"📦 Compact triggered for [{label}] from the Mini App",
            )
        except Exception:
            log.debug("miniapp: session-compacted mirror failed", exc_info=True)

    async def _mirror_session_restarted(self, scope_chat_id, label) -> None:
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id, text=f"🔁 Restarted [{label}] from the Mini App",
            )
        except Exception:
            log.debug("miniapp: session-restarted mirror failed", exc_info=True)

    async def _mirror_session_renamed(
        self, scope_chat_id, previous_label, new_label,
    ) -> None:
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id,
                text=f"✏️ [{previous_label}] renamed to [{new_label}] from the Mini App",
            )
        except Exception:
            log.debug("miniapp: session-renamed mirror failed", exc_info=True)

    async def _mirror_session_stopped(self, scope_chat_id, label, dropped) -> None:
        """Best-effort — the stop already happened; a failed mirror must
        never fail (or appear to undo) a write that already landed."""
        text = f"⏹️ Stopped [{label}] from the Mini App"
        if dropped:
            text += f" ({dropped} queued message{'s' if dropped > 1 else ''} discarded)"
        try:
            await self.bot._app.bot.send_message(chat_id=scope_chat_id, text=text)
        except Exception:
            log.debug("miniapp: session-stopped mirror failed", exc_info=True)

    async def _mirror_session_killed(self, scope_chat_id, label) -> None:
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id, text=f"💀 Killed [{label}] from the Mini App",
            )
        except Exception:
            log.debug("miniapp: session-killed mirror failed", exc_info=True)

    async def _mirror_session_resumed(self, scope_chat_id, label) -> None:
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id, text=f"♻️ Resumed [{label}] from the Mini App",
            )
        except Exception:
            log.debug("miniapp: session-resumed mirror failed", exc_info=True)

    async def _mirror_session_deleted(self, scope_chat_id, label) -> None:
        try:
            await self.bot._app.bot.send_message(
                chat_id=scope_chat_id, text=f"🗑️ Deleted [{label}] from the Mini App",
            )
        except Exception:
            log.debug("miniapp: session-deleted mirror failed", exc_info=True)

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
