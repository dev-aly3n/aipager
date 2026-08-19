"""Telegram bot — python-telegram-bot v22 async Application.

Single owner of all Telegram communication. Handles:
- CallbackQuery (button taps) → dtach_inject.send_keys()
- Message replies → dtach_inject.send_text_and_enter()
- /status command → show all sessions
- /<label> <prompt> → direct send to session
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
from typing import TYPE_CHECKING

from telegram import (
    BotCommand,
    BotCommandScopeChat,
)
from telegram.ext import (
    AIORateLimiter,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, Forbidden, RetryAfter

from aipager.dtach import inject

from aipager.config import (
    APP_BUTTON, BOT_TOKEN, CHAT_ID,
)
from aipager.state import TrackedSession

# Pure-function helpers and constants live in aipager.bot.transport
# now. Re-export the names this module uses internally so the
# TelegramBot class body below (and any external consumers like the
# tests) keeps working without changes.
from aipager.bot.transport import (  # noqa: F401
    ACTION_VERBS,
    TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES,
    TELEGRAM_MAX_DOC_BYTES,
    TELEGRAM_MAX_TEXT_LEN,
    TruncationFailed,
    _build_diff_block,
    _detect_api_error,
    _DIFF_MAX_CHARS,
    _DIFF_MAX_LINES,
    _diff_view_enabled,
    _ERROR_PATTERNS,
    _extract_retry_after,
    _is_bot_blocked,
    _log_blocked_once,
    _MAX_TRUNCATIONS,
    _md_safe_boundaries,
    _PERSONAL_MODE_SENTINEL,
    _RETRY_AFTER_RE,
    _safe_truncate,
    _send_with_retry,
    _TRUNC_SUFFIX,
    _truncate_diff,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)





class LifecycleMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    async def reload_team(self) -> None:
        """Re-read ``team.yaml`` and swap ``self.team`` live.

        Triggered by the daemon's SIGUSR1 handler when the wizard
        finishes a team-config edit. On parse error, log a WARN and
        keep the previous team in memory — the admin can't lock
        themselves out by hand-editing a typo. Returning to personal
        mode (``team.yaml`` absent / archived) is a valid result:
        ``self.team`` becomes ``None`` and all handlers fall back to
        the personal-mode path.
        """
        # Reload v2 scopes/policy too (authoritative when present). A
        # broken hand-edit keeps the previous in-memory config so the
        # operator can't lock themselves out with a typo.
        try:
            from aipager.policy import PolicyError, load_policy
            from aipager.scope import ScopeConfigError, load_scopes
            _v2 = load_scopes()
            self.scopes = _v2[0] if _v2 else None
            self.policy = load_policy()
            log.info("Scope reload: %s scope(s)",
                     len(self.scopes) if self.scopes else 0)
        except (ScopeConfigError, PolicyError) as e:
            log.warning("Scope/policy reload failed — keeping previous: %s", e)

        from aipager.team import (
            TEAM_CONFIG_PATH, TeamConfigError, load_team,
        )
        try:
            new_team = load_team(TEAM_CONFIG_PATH)
        except TeamConfigError as e:
            log.warning(
                "Team config reload failed — keeping previous in-memory "
                "team. Fix and re-signal: %s", e,
            )
            return

        old = self.team
        self.team = new_team

        if old is None and new_team is None:
            log.info("Team reload: no change (still personal mode)")
        elif old is None and new_team is not None:
            log.info(
                "Team reload: personal → team (%d users, %d admin, "
                "deny=%s)",
                len(new_team.users), new_team.admin_count(),
                list(new_team.rules.deny_tools),
            )
        elif new_team is None:
            log.info("Team reload: team → personal (allow-list disabled)")
        else:
            log.info(
                "Team reload: %d → %d users · %d → %d admin · "
                "deny %s → %s",
                len(old.users), len(new_team.users),
                old.admin_count(), new_team.admin_count(),
                list(old.rules.deny_tools),
                list(new_team.rules.deny_tools),
            )

    async def _on_telegram_error(self, update: object, context) -> None:
        """Log Telegram-side failures at one line, never as a traceback.

        Two failure modes have actually cost this daemon uptime:

        * ``RetryAfter`` — flood control. With the rate limiter above this
          should now be absorbed before it ever reaches here; if one still
          arrives, say so in one line rather than dumping a stack per
          blocked send (that noise is itself a load on the single event
          loop shared with polling and the session monitor).
        * ``NetworkError`` — transient Telegram-side 5xx. A ``Bad Gateway``
          took the daemon down overnight with nothing to restart it. These
          are expected weather, not bugs.

        Anything else keeps its traceback: an unexpected error should stay
        loud.
        """
        from telegram.error import NetworkError, RetryAfter

        err = context.error
        if isinstance(err, RetryAfter):
            log.warning("Telegram flood control — retry in %ss", err.retry_after)
        elif isinstance(err, NetworkError):
            log.warning("Telegram network error (transient): %s", err)
        else:
            log.error("Unhandled Telegram error", exc_info=err)

    def _make_builder(self):
        """Build and configure the PTB ApplicationBuilder.

        Split out of :meth:`start` purely so the configuration is
        assertable: a test that constructs its own ApplicationBuilder
        proves the library works, not that THIS daemon is configured —
        exactly the shape that let a missing rate limiter pass a test
        named after it.
        """
        builder = ApplicationBuilder().token(BOT_TOKEN)

        # Long-poll config: timeout=30 means Telegram holds the connection
        # for up to 30s waiting for updates → instant response to taps.
        # Connect timeouts are 30s rather than 10s so the daemon stays
        # robust on networks where the initial TLS handshake is slow.
        builder = (
            builder
            .get_updates_read_timeout(30)
            .get_updates_write_timeout(15)
            .get_updates_connect_timeout(30)
            .read_timeout(20)
            .write_timeout(15)
            .connect_timeout(30)
        )

        # Throttle every outbound call and honour Telegram's own
        # RetryAfter, in the request layer rather than at ~64 individual
        # send/edit sites.
        #
        # Not theoretical: a runaway animation edited one message once a
        # second for hours, Telegram imposed flood control with
        # retry_after=34712 (9.6 HOURS), and because nothing honoured it,
        # every subsequent send raised telegram.error.RetryAfter as an
        # unhandled traceback. The bot went mute for most of a day —
        # prompts still reached Claude, but no reply could ever be sent
        # back. AIORateLimiter queues and retries after the stated
        # interval instead of hammering and failing.
        return builder.rate_limiter(AIORateLimiter())

    async def start(self) -> None:
        builder = self._make_builder()

        self._app = builder.build()
        self._app.add_error_handler(self._on_telegram_error)

        # Register handlers
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))
        self._app.add_handler(CommandHandler("start", self._handle_start_cmd))
        self._app.add_handler(CommandHandler("help", self._handle_start_cmd))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(CommandHandler("stop", self._handle_stop_cmd))
        self._app.add_handler(CommandHandler("kill", self._handle_kill_cmd))
        self._app.add_handler(CommandHandler("new", self._handle_new_cmd))
        self._app.add_handler(CommandHandler("resume", self._handle_resume_cmd))
        self._app.add_handler(CommandHandler("clearqueue", self._handle_clearqueue_cmd))
        self._app.add_handler(CommandHandler("whoami", self._handle_whoami))
        self._app.add_handler(CommandHandler("perms", self._handle_perms_cmd))
        self._app.add_handler(CommandHandler("settings", self._handle_settings_cmd))
        self._app.add_handler(CommandHandler("app", self._handle_app_cmd))
        # Chat gate for message handlers. Multi-scope: accept every
        # configured scope's chat. Legacy: the single CHAT_ID. (Command
        # handlers above are not chat-filtered; _authorize gates them.)
        if self.scopes:
            chat_gate = filters.Chat({s.chat_id for s in self.scopes})
        else:
            chat_gate = filters.Chat(int(CHAT_ID))
        # Media handler: photos and documents → save file, inject prompt
        self._app.add_handler(MessageHandler(
            (filters.PHOTO | filters.Document.ALL) & chat_gate,
            self._handle_file,
        ))
        # Voice messages → faster-whisper transcribe → inject as prompt.
        # Item 5.3. Only fires when the `voice` extra is installed; the
        # handler itself surfaces a friendly error otherwise.
        self._app.add_handler(MessageHandler(
            filters.VOICE & chat_gate,
            self._handle_voice,
        ))
        # Catch-all for text messages (replies and /<label> commands)
        self._app.add_handler(MessageHandler(
            filters.TEXT & chat_gate,
            self._handle_message,
        ))

        await self._app.initialize()
        await self._app.start()
        # Start polling with 30s long-poll timeout
        await self._app.updater.start_polling(
            poll_interval=0,          # no sleep between polls
            timeout=30,               # Telegram server holds connection 30s
            drop_pending_updates=True, # ignore old updates on restart
        )
        log.info("Telegram bot polling started")
        await self._update_bot_commands()

    async def stop(self) -> None:
        # Cancel every running per-session animation task FIRST and wait
        # for them to settle. Otherwise asyncio.run() at the outer layer
        # force-kills them mid-edit, which can leave orphan tasks logging
        # spurious "task was destroyed but is pending" warnings.
        to_cancel = [s.animate_task for s in self.registry.all_sessions().values()
                     if s.animate_task and not s.animate_task.done()]
        for t in to_cancel:
            t.cancel()
        for t in to_cancel:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # Close the rich-message httpx client before PTB shuts down so we
        # don't leave open connections dangling after the event loop ends.
        from aipager.bot.rich_message import close_client
        await close_client()
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def _recover_busy_message(
        self, bot, name: str, sess: TrackedSession, live_names: set[str]
    ) -> str:
        """Edit one session's orphaned busy message to a final state.

        Returns one of: ``"edited"`` / ``"vanished"`` (user deleted the
        message) / ``"too_old"`` (>48 h, Telegram refuses edits) /
        ``"blocked"`` (bot blocked by user) / ``"flooded"`` (RetryAfter)
        / ``"error:<short>"`` (unexpected). Always clears
        ``sess.busy_msg_id`` synchronously before any await so a hook
        firing mid-recovery can't race on it.
        """
        orphaned_id = sess.busy_msg_id
        sess.busy_msg_id = None  # clear before any await
        is_alive = name in live_names
        label = html_mod.escape(sess.label)
        text = (f"🔄 <b>{label}</b> · Daemon restarted" if is_alive
                else f"🔴 <b>{label}</b> · Session ended")
        try:
            await bot.edit_message_text(
                text, chat_id=CHAT_ID, message_id=orphaned_id,
                parse_mode="HTML",
            )
        except BadRequest as e:
            msg = str(e).lower()
            if "message to edit not found" in msg or "message not found" in msg:
                log.info("[%s] orphan msg %d already gone (user deleted)",
                         sess.label, orphaned_id)
                return "vanished"
            if "can't be edited" in msg or "message can't be edited" in msg:
                log.warning(
                    "[%s] orphan msg %d is too old to edit (>48h); user will "
                    "still see the stuck 'Working…' until they delete it",
                    sess.label, orphaned_id,
                )
                return "too_old"
            log.warning("[%s] orphan msg %d edit failed: %s",
                        sess.label, orphaned_id, e)
            return f"error:{str(e)[:80]}"
        except Forbidden as e:
            _log_blocked_once(e)
            return "blocked"
        except RetryAfter as e:
            wait = getattr(e, "retry_after", None) or "?"
            log.info("[%s] orphan msg %d skipped — Telegram flood control "
                     "(retry_after=%s)", sess.label, orphaned_id, wait)
            return "flooded"
        except Exception as e:  # noqa: BLE001 - true last-resort log
            log.warning("[%s] orphan msg %d recovery failed: %r",
                        sess.label, orphaned_id, e, exc_info=True)
            return f"error:{type(e).__name__}"
        return "edited"

    async def recover_sessions(self) -> None:
        """Clean up orphaned busy messages from a previous daemon lifecycle.

        For every session with a persisted ``busy_msg_id`` left over from
        the previous daemon run, try to edit the message to a terminal
        state ("Daemon restarted" if the dtach session is still alive,
        "Session ended" otherwise). Returns a summary log line so the
        outcome of each daemon startup is visible in `aipager logs`.
        """
        if not self._app:
            return
        bot = self._app.bot
        live_names = set(await inject.list_sessions())

        targets = [(name, sess) for name, sess in self.registry.all_sessions().items()
                   if sess.busy_msg_id and sess.busy_msg_id > 0]
        if not targets:
            return

        outcomes: dict[str, int] = {}
        stop_early = False
        for name, sess in targets:
            if stop_early:
                # Bot is blocked — clear remaining busy_msg_ids without trying
                # to edit (which would just generate more Forbidden noise).
                sess.busy_msg_id = None
                outcomes["skipped_blocked"] = outcomes.get("skipped_blocked", 0) + 1
                continue
            outcome = await self._recover_busy_message(bot, name, sess, live_names)
            key = outcome.split(":", 1)[0]  # "error:foo" → "error"
            outcomes[key] = outcomes.get(key, 0) + 1
            if outcome == "blocked":
                stop_early = True

        # One summary line, easy to grep in `aipager logs`.
        summary = ", ".join(f"{n} {k}" for k, n in sorted(outcomes.items()))
        suffix = ""
        if stop_early:
            suffix = "  (bot blocked — stopping retries)"
        log.info("recovered %d sessions: %s%s", len(targets), summary, suffix)
        self.registry.mark_dirty()

    async def _update_bot_commands(self) -> None:
        """Register bot commands (/ menu) and update persistent keyboard.

        Always runs on the first call after daemon startup, even when
        there are no sessions, so Telegram's server-side command cache
        from a previous run (stale ``/jim`` / ``/john`` entries) is
        cleared and the persistent keyboard appears immediately. On
        subsequent calls it short-circuits when nothing changed.
        """
        if not self._app:
            return

        if self.scopes is None:
            await self._update_bot_commands_global()
        else:
            await self._update_bot_commands_per_scope()

        # Send/update persistent keyboard (always main — first run or
        # session list changed). In multi-scope there's no single chat to
        # target here, so the keyboard renders per-chat on interaction
        # (handlers pass chat_id); a global broadcast would leak labels.
        if self.scopes is None:
            await self._send_keyboard(level="main")

    @staticmethod
    def _command_list(labels: set[str]) -> list[BotCommand]:
        """Static commands + one `/label` per live session label."""
        commands = [
            BotCommand("status", "Show all sessions"),
            BotCommand("stop", "Stop active session"),
            BotCommand("kill", "Kill a session (destroy)"),
            BotCommand("new", "Launch new session"),
            BotCommand("resume", "Resume a past session"),
            BotCommand("perms", "Switch permission mode for active session"),
            BotCommand("settings", "Configure message layout, formatting, and language"),
            BotCommand("clearqueue", "Drop pending queued prompts"),
            BotCommand("whoami", "Show your role + permissions"),
        ]
        # Read late (not module-level) so a live `aipager miniapp enable`
        # + restart is reflected without re-importing this module.
        from aipager.config import MINIAPP_ENABLED
        if MINIAPP_ENABLED:
            commands.append(BotCommand("app", "Open the Mini App dashboard"))
        for label in sorted(labels):
            commands.append(BotCommand(label, f"Send to [{label}]"))
        return commands

    async def _update_bot_commands_global(self) -> None:
        """Single global `/menu` (legacy / single-scope mode)."""
        labels = self.registry.live_labels()
        first_run = self._registered_labels is None
        if not first_run and labels == self._registered_labels:
            return  # no change
        try:
            await self._app.bot.set_my_commands(self._command_list(labels))
            self._registered_labels = labels
            log.info("Bot commands updated: status, stop + %s",
                     ", ".join(sorted(labels)) or "(none)")
        except Exception:
            log.warning("Failed to set bot commands", exc_info=True)
            if first_run:
                self._registered_labels = labels

    # ---- Mini App launch button ----------------------------------------

    def _miniapp_button_chats(self) -> list[int]:
        """Private chats that may be handed the Mini App URL.

        Deliberately an explicit list rather than "all private chats".
        ``set_chat_menu_button`` with no ``chat_id`` sets the bot's
        *default* menu button, which would show the Mini App — and so
        disclose the tunnel hostname — to anyone who so much as opens a
        chat with the bot. A Mini App URL is usable over the raw
        internet once obtained (see
        :meth:`AuthMixin._is_personal_mode_operator`), and this exact
        class of leak was already caught once, on ``/app``.

        Group scopes are skipped because they cannot have one: every
        Mini App launch surface is private-chat-only in the Bot API, and
        a group ``chat_id`` has no menu button to set.
        """
        if self.scopes is not None:
            return [s.chat_id for s in self.scopes
                    if s.kind == "dm" and s.chat_id > 0]
        # Personal and team mode share one configured chat. In practice
        # that means only personal mode gets a button: `doctor.check_team`
        # requires a team install's CHAT_ID to equal team.yaml's
        # `group_id`, which is a group — so the `> 0` test below returns
        # nothing for it. That is the correct outcome (a group has no
        # menu button), reached by the same rule rather than a special
        # case, and it is why the rule is a sign test and not a mode test.
        try:
            chat_id = int(CHAT_ID)
        except (TypeError, ValueError):
            return []
        # A negative id is a group: no menu button exists there.
        return [chat_id] if chat_id > 0 else []

    def prime_miniapp_url(self, url: str) -> None:
        """Record the launch URL before the first keyboard goes out.

        :meth:`start` ends by calling ``_update_bot_commands``, which in
        personal mode sends the persistent keyboard straight away — long
        before the Mini App server has started and
        :meth:`publish_miniapp_button` has run. Without this the very
        first keyboard after every restart is missing its App button,
        and nothing re-sends it until some unrelated event (a session
        created, a Templates→Back tap) happens to refresh it.

        Makes no Telegram call and sets no menu button: that still waits
        until the server is confirmed listening. If the server then
        fails to start, ``publish_miniapp_button("")`` clears this, so
        only the single keyboard already sent can carry a button whose
        target is not up — a URL the operator could have got from
        ``/app`` anyway.
        """
        self._miniapp_url = url

    async def publish_miniapp_button(self, url: str) -> None:
        """Show (or remove) the Mini App button in each authorized chat.

        ``url`` empty means "there is no Mini App right now" — the Mini
        App is disabled, its server failed to start, or no public HTTPS
        URL resolves. That case restores the ``/`` commands menu rather
        than leaving last run's button pointing at a port nothing is
        listening on; a button that opens a broken page is worse than no
        button, and it would otherwise survive every restart.

        Failures are logged per chat and never raised: this runs during
        startup, and one blocked or unknown chat must not cost the other
        scopes their button, let alone take the daemon down.
        """
        from telegram import MenuButtonCommands, MenuButtonWebApp, WebAppInfo

        # Verify before advertising. The daemon used to publish whatever
        # URL config held, unchecked — so when an ephemeral tunnel died the
        # button spun forever on the phone, looking exactly like a working
        # one. A URL nobody has confirmed is worse than no button: no button
        # is an honest absence, a dead button is a broken promise.
        #
        # Runs here rather than in resolve_public_url() because the URL is
        # resolved before the Mini App server is listening; only by publish
        # time is a positive result meaningful.
        if url:
            from aipager.miniapp.tunnel import probe_public_url
            if not await probe_public_url(url):
                log.warning(
                    "Mini App URL %s did not answer — publishing no button. "
                    "The Mini App will be unavailable until it is reachable.",
                    url,
                )
                url = ""

        self._miniapp_url = url
        if not self._app:
            return          # same guard every sibling that touches _app.bot uses

        try:
            chats = self._miniapp_button_chats()
            button = (
                MenuButtonWebApp(text=APP_BUTTON, web_app=WebAppInfo(url=url))
                if url else MenuButtonCommands()
            )
        except Exception:
            # The docstring promises this never raises, so the work
            # BEFORE the API call has to be covered too — otherwise the
            # guarantee holds only for the one line that happens to sit
            # inside the loop's try.
            #
            # Fail closed: if the URL could not even be made into a
            # button, the keyboard must not go on offering it either, or
            # the two surfaces would disagree about whether there is a
            # Mini App at all.
            self._miniapp_url = ""
            log.warning("Could not build the Mini App menu button", exc_info=True)
            return

        for chat_id in chats:
            try:
                # chat_id is passed ALWAYS and BY KEYWORD — see
                # _miniapp_button_chats for why the no-chat_id form is
                # not an acceptable shortcut here.
                await self._app.bot.set_chat_menu_button(
                    chat_id=chat_id, menu_button=button,
                )
            except Exception:
                log.warning(
                    "Failed to set the Mini App menu button for chat %s",
                    chat_id, exc_info=True,
                )
        log.info(
            "Mini App menu button %s for %d chat(s)",
            "published" if url else "cleared", len(chats),
        )

    def _all_notify_chat_ids(self) -> list[int]:
        """Every chat that gets the one-time startup provenance notice.

        Multi-scope: every configured scope's chat_id. Legacy: the
        single CHAT_ID, parsed the same defensively as
        :meth:`_miniapp_button_chats` does at its personal-mode branch.
        Empty (never raises) when nothing is configured.
        """
        if self.scopes is not None:
            return [s.chat_id for s in self.scopes]
        try:
            return [int(CHAT_ID)]
        except (TypeError, ValueError):
            return []

    async def send_startup_notice(self, text: str) -> None:
        """Send the one-time claude-provenance notice to every chat.

        Fire-and-forget from ``_run_daemon()`` — called once, after
        :meth:`start` has already begun polling, so a slow or failing
        send can never delay ``recover_sessions()`` or the session
        monitor. Sent via ``transport._send_with_retry`` (not a bare
        ``send_message``) so it honours the same flood-control backoff
        every other outbound message does. Failures are logged per chat
        and never raised — same discipline as
        :meth:`publish_miniapp_button`.
        """
        if not self._app:
            return
        for chat_id in self._all_notify_chat_ids():
            try:
                await _send_with_retry(self._app.bot, chat_id=chat_id, text=text)
            except Exception:
                log.warning(
                    "Failed to send startup notice to chat %s", chat_id,
                    exc_info=True,
                )

    async def _update_bot_commands_per_scope(self) -> None:
        """Per-chat `/menu` via ``BotCommandScopeChat`` so each scope's
        autocomplete lists only its own session labels (Phase G)."""
        for scope in self.scopes:
            labels = self.registry.live_labels(scope.chat_id)
            prev = self._registered_scope_labels.get(scope.chat_id)
            if prev is not None and labels == prev:
                continue
            try:
                await self._app.bot.set_my_commands(
                    self._command_list(labels),
                    scope=BotCommandScopeChat(chat_id=scope.chat_id),
                )
                self._registered_scope_labels[scope.chat_id] = labels
                log.info("Bot commands for %s: status, stop + %s",
                         scope.chat_id, ", ".join(sorted(labels)) or "(none)")
            except Exception:
                # E.g. a group the bot isn't a member of — log + skip.
                log.warning("Failed to set bot commands for scope %s",
                            scope.chat_id, exc_info=True)
                if prev is None:
                    self._registered_scope_labels[scope.chat_id] = labels
