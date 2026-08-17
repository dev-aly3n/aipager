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
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import (
    Update,
)

from aipager.dtach import inject

# Single source of truth for the poll timing (design.md ORCHESTRATOR
# OVERRIDE) — callbacks.py does not import this module, so this is not
# a cycle. Kept here as a plain import rather than redefined so a future
# tuning of the poll window only has to change one place.
from aipager.bot.callbacks import (
    _PERMS_POLL_COUNT,
    _PERMS_POLL_INTERVAL,
    _PERMS_RESTART_QUIET,
)
from aipager.state import Status, TrackedSession
from aipager.transcript import last_assistant_preview as _read_preview

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
    calling_chat_id,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


@dataclass
class StopOutcome:
    """Result of :meth:`SessionOpsMixin._stop_session_core`."""

    ok: bool
    label: str
    dropped: int = 0


@dataclass
class KillOutcome:
    """Result of :meth:`SessionOpsMixin._kill_session_core`.

    ``result`` is one of ``"killed"``, ``"still_running"``, ``"not_found"``.
    """

    result: str
    label: str
    session_name: str


@dataclass
class ResumeOutcome:
    """Result of :meth:`SessionOpsMixin._do_resume_core`.

    ``reason`` is one of ``"not_gone"``, ``"no_transcript"``,
    ``"launch_failed"``, ``"resumed"``. Never ``"not_found"`` — the
    caller resolves the session before calling the core, and handles a
    missing session itself (see the core's own docstring).
    """

    ok: bool
    reason: str
    err: str = ""


@dataclass
class RestartOutcome:
    """Result of :meth:`SessionOpsMixin._kill_and_relaunch_core` (and its
    thin callers :meth:`_perms_switch_core` / :meth:`_restart_session_core`).

    ``reason`` is one of ``"not_live"``, ``"already_restarting"``,
    ``"still_stopping"``, ``"launch_failed"``, ``"done"``. Never
    ``"not_found"`` — like the other cores in this module, the caller
    resolves the session before calling in.
    """

    ok: bool
    reason: str
    label: str
    skip_perms: bool = False
    err: str = ""


@dataclass
class ClearQueueOutcome:
    """Result of :meth:`SessionOpsMixin._clear_queue_core`."""

    ok: bool
    label: str
    dropped: int = 0


@dataclass
class CompactOutcome:
    """Result of :meth:`SessionOpsMixin._compact_session_core`.

    ``reason`` is one of ``"not_live"``, ``"queue_full"``,
    ``"send_failed"``, ``"queued"``, ``"sent"``.
    """

    ok: bool
    reason: str
    label: str


@dataclass
class RenameOutcome:
    """Result of :meth:`SessionOpsMixin._rename_session_core`.

    Has no failure path — validation (syntax + collision) happens in
    server.py, before the core is ever called.
    """

    ok: bool
    previous_label: str
    new_label: str
    changed: bool


class SessionOpsMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    def _prompt_marker(self, sess) -> str:
        """Identity/origin marker prepended to Telegram free-text prompts.

        DM scope → ``[via Telegram · @label]``; group → adds ``· role:X``.
        Empty for legacy/no-driver sessions (→ no marker).
        """
        if self.scopes is None:
            return ""
        member = self._driver_user(sess)
        if member is None:
            return ""
        if sess.scope_kind == "group":
            return f"[via Telegram · @{member.label} · role:{member.role}]"
        return f"[via Telegram · @{member.label}]"

    async def _inject_prompt(self, sess, text: str) -> bool:
        """Send a Telegram-originated prompt into the session.

        Marks the session origin = "telegram" (the safety boundary keys
        off this) and prepends the identity marker for free-text prompts.
        Slash commands are sent raw (a marker line would break them).
        """
        sess.last_prompt_origin = "telegram"
        # Write the policy snapshot for this turn. The safety-relevant
        # fields (role/scope/member, Phase E) only matter in v2 scope
        # mode — the marker line that flips a hook's origin check to
        # "telegram" is only ever prepended there (see _prompt_marker
        # above), so writing a snapshot in legacy/personal mode cannot
        # change PreToolUse enforcement. style_text is different: it's
        # keyed by the scope's real Telegram chat id, not by aipager.yaml
        # scope config, so it's meaningful in every mode. This is the one
        # place a session's UserPromptSubmit-hook style guidance gets
        # refreshed, and it must run on every Telegram-originated prompt
        # so a /settings change — OR a per-session override set from the
        # Mini App — reaches the very next reply, no restart. Goes through
        # resolve_preferences (never get_preferences directly): this is
        # THE critical path per design.md — a caller here that bypassed
        # it would make the per-session settings feature do nothing.
        # Best-effort.
        try:
            from aipager import preferences as prefs_mod
            from aipager.policy_snapshot import write_snapshot
            member = role = scope = None
            if self.scopes is not None:
                member = self._driver_user(sess)
                role = (self.policy.get_role(member.role)
                        if member is not None else None)
                scope = self._scope_for(sess.scope_chat_id)
            style = prefs_mod.style_text(
                prefs_mod.resolve_preferences(
                    sess.scope_chat_id or 0, sess.preference_overrides(),
                )
            )
            write_snapshot(sess.name, role, scope, member, style_text=style)
        except Exception:
            log.debug("policy snapshot write failed", exc_info=True)
        body = text
        if not text.lstrip().startswith("/"):
            marker = self._prompt_marker(sess)
            if marker:
                body = f"{marker}\n{text}"
        return await inject.send_text_and_enter(sess.name, body)

    async def create_session(
        self, label: str, *, scope_chat_id, skip_perms: bool = False,
        cwd: str | None = None, driver_user_id: int | None = None,
        model: str | None = None,
    ) -> tuple[str, str]:
        """Launch a session and register it. Returns ``(session_name, "")``
        or ``("", error)``.

        The single seam both entry points go through: chat's ``/new`` and
        the Mini App's create route. Everything that makes a launched
        process *a tracked aipager session* — the disambiguated name, the
        registry entry, the scope stamping, the GONE/UNKNOWN recovery,
        the bot-name and command refresh — lives here once, so the two
        surfaces cannot drift into registering sessions differently.

        Deliberately NOT included: the caller's own authorization, the
        name-conflict prompt, and the reply it sends. Those are
        surface-specific (chat asks Resume/Replace/Cancel with buttons;
        the Mini App answers inline before the request is even sent), and
        folding them in here would force one surface to fake the other's
        interaction model.
        """
        import asyncio

        from aipager.scope import disambiguated_name
        from aipager.state import Status

        if scope_chat_id is not None:
            scope_kind = "group" if scope_chat_id < 0 else "dm"
            session_name = disambiguated_name(label, scope_chat_id, scope_kind)
        else:
            scope_kind = "dm"
            session_name = f"claude-{label}"
        short_name = session_name.removeprefix("claude-")

        sys_extra = self._session_system_prompt(scope_chat_id, label)
        ok, err = await inject.launch_session(
            short_name, skip_perms=skip_perms, cwd=cwd or None,
            system_prompt_extra=sys_extra, model=model or None,
        )
        if not ok:
            return "", err

        sess = self.registry.get_or_create(session_name)
        sess.label = label
        sess.skip_perms = skip_perms
        if cwd:
            # The hook receiver stamps cwd on the first statusLine event,
            # but the operator picked this one explicitly — record it now
            # so the session page and the directory picker show the truth
            # before the first hook arrives.
            sess.cwd = cwd
        if scope_chat_id is not None:
            sess.scope_chat_id = scope_chat_id
            sess.scope_kind = scope_kind
        if sess.status in (Status.GONE, Status.UNKNOWN):
            self.registry.transition(session_name, Status.IDLE)
        if driver_user_id is not None:
            sess.created_by_user_id = sess.created_by_user_id or driver_user_id
            sess.last_driver_user_id = driver_user_id
        self.registry.last_active_session = session_name
        self.registry.mark_dirty()
        asyncio.create_task(self._maybe_update_bot_name(session_name))
        asyncio.create_task(self._update_bot_commands())
        return session_name, ""

    def _session_system_prompt(self, scope_chat_id, label: str) -> str | None:
        """Write the session folder + SESSION.md and return its body for
        ``--append-system-prompt``. None for legacy/grandfathered sessions
        (no scope) or on any failure (best-effort)."""
        if not scope_chat_id or self.scopes is None:
            return None
        scope = self._scope_for(scope_chat_id)
        if scope is None:
            return None
        try:
            from aipager.session_store import write_session_files
            return write_session_files(scope, self.policy, label)
        except Exception:
            log.debug("SESSION.md generation failed", exc_info=True)
            return None

    # ── Telegram handlers ──

    async def _stop_session_core(self, sess: TrackedSession) -> StopOutcome:
        """Interrupt a busy session: send Escape, clean up state.

        The shared seam both chat and the Mini App call — edit behaviour
        here, not in the wrapper. Refuses immediately, with no awaits and
        no side effects, when the session is not actually busy — this
        makes the refusal pytest-testable with zero dtach mocking.
        """
        if sess.status not in (Status.BUSY, Status.INTERACTIVE):
            return StopOutcome(ok=False, label=sess.label)

        # 1. Send Escape twice to Claude Code
        await inject.send_keys(sess.name, "Escape")
        await asyncio.sleep(0.15)
        await inject.send_keys(sess.name, "Escape")

        # 2. Cancel animation
        self._stop_animation(sess)

        # 3. Edit busy message to show "Stopped" (no keyboard)
        if sess.busy_msg_id and sess.busy_msg_id > 0:
            await self._edit_busy_raw(
                sess.busy_msg_id,
                f"⚠️ <b>{html_mod.escape(sess.label)}</b> · Stopped",
            )
        sess.busy_msg_id = None

        # 4. Transition to IDLE directly (skip notify — we handle UI here)
        dropped = len(sess.pending_queue)
        sess.pending_queue.clear()
        sess.pending_permission = None
        sess.status = Status.IDLE
        sess.trigger_msg_id = None
        sess.last_idle_at = time.monotonic()  # prevent debounce of next real IDLE
        self.registry.mark_dirty()

        log.info("[%s] Stopped by user (dropped %d queued)", sess.label, dropped)
        return StopOutcome(ok=True, label=sess.label, dropped=dropped)

    async def _stop_session(self, sess: TrackedSession,
                            update: Update | None = None,
                            query=None) -> StopOutcome:
        """Interrupt a busy session: send Escape, clean up state.

        Thin wrapper around :meth:`_stop_session_core` — chat's own
        acknowledgement (edit the busy message / react to the command),
        the core's behaviour lives in the core.
        """
        outcome = await self._stop_session_core(sess)
        if not outcome.ok:
            return outcome

        # Acknowledge
        ack = f"Stopped [{sess.label}]"
        if outcome.dropped:
            ack += (f" ({outcome.dropped} queued message"
                    f"{'s' if outcome.dropped > 1 else ''} discarded)")

        if query:
            await self._safe_answer(query, ack)
            # Also edit the callback query's message if it's the busy message
            try:
                await query.edit_message_text(
                    f"⚠️ <b>{html_mod.escape(sess.label)}</b> · Stopped",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        elif update:
            await self._react(update, "✅")

        return outcome

    async def _halt_for_safety(self, sess: TrackedSession, reason: str) -> None:
        """Cleanly halt a session after a safety block.

        Same teardown as :meth:`_stop_session` — interrupt Claude
        (Escape×2), **cancel the busy animation** (else the spinner shows
        "thinking" forever), surface the block in place of the busy
        message, and return to IDLE so the session stays responsive.
        Best-effort throughout; never raises into the notify path.
        """
        from aipager.bot.transport import resolve_chat_id
        # 1. Interrupt Claude's turn.
        try:
            await inject.send_keys(sess.name, "Escape")
            await asyncio.sleep(0.15)
            await inject.send_keys(sess.name, "Escape")
        except Exception:
            log.debug("[%s] safety halt interrupt failed", sess.label,
                      exc_info=True)
        # 2. Cancel the spinner (the piece whose absence wedged "thinking").
        self._stop_animation(sess)
        # 3. Replace the busy message with the block notice (or send fresh).
        notice = (f"🛑 <b>{html_mod.escape(sess.label)}</b> · Blocked by "
                  f"safety policy: {html_mod.escape(reason)} — stopped")
        try:
            if sess.busy_msg_id and sess.busy_msg_id > 0:
                await self._edit_busy_raw(
                    sess.busy_msg_id, notice, chat_id=resolve_chat_id(sess))
            elif self._app:
                await self._app.bot.send_message(
                    resolve_chat_id(sess), notice, parse_mode="HTML")
        except Exception:
            log.debug("[%s] safety halt notice failed", sess.label,
                      exc_info=True)
        # 4. Return to IDLE.
        sess.busy_msg_id = None
        sess.pending_queue.clear()
        sess.pending_permission = None
        sess.status = Status.IDLE
        sess.trigger_msg_id = None
        sess.last_idle_at = time.monotonic()
        self.registry.mark_dirty()
        log.info("[%s] halted by safety policy", sess.label)

    async def _kill_session_core(self, session_name: str, label: str) -> KillOutcome:
        """Kill a session by its (already-resolved) dtach session name.

        The shared seam both chat and the Mini App call — edit behaviour
        here, not in the wrapper.
        """
        # Stop animation if running
        sess = self.registry.get(session_name)
        if sess:
            self._stop_animation(sess)

        # Kill the dtach process
        killed = await inject.kill_session(session_name)
        if killed:
            self.registry.remove(session_name)
            self.registry.mark_dirty()
            asyncio.create_task(self._update_bot_commands())
            return KillOutcome(result="killed", label=label, session_name=session_name)
        if await inject.is_alive(session_name):
            # Socket still there: the process survived. Saying "not found"
            # here would tell the user it is gone while it keeps running.
            return KillOutcome(
                result="still_running", label=label, session_name=session_name,
            )
        return KillOutcome(result="not_found", label=label, session_name=session_name)

    async def _kill_session_by_label(self, source, target_label: str) -> None:
        """Kill a session by label. source is Update or CallbackQuery.

        Thin wrapper around :meth:`_kill_session_core` — the chat-only
        label→name resolution (with its ``claude-<label>`` fallback,
        unreachable from the Mini App) and reply phrasing live here.
        """
        async def _reply(text: str) -> None:
            if hasattr(source, 'message') and source.message:
                await source.message.reply_text(text)
            else:
                await source.edit_message_text(text)

        # Find session within the calling scope (label may repeat across scopes)
        found = self.registry.find_by_label(
            target_label, calling_chat_id(source), include_gone=True)
        session_name = found.name if found else f"claude-{target_label}"

        outcome = await self._kill_session_core(session_name, target_label)
        if outcome.result == "killed":
            await _reply(f"💀 Killed [{target_label}]")
        elif outcome.result == "still_running":
            await _reply(f"⚠️ Could not kill [{target_label}] — still running")
        else:
            await _reply(f"⚠️ Session [{target_label}] not found")

    async def _do_resume_core(
        self, sess: TrackedSession, *,
        skip_perms_override: bool | None = None,
        driver_user_id: int | None = None,
    ) -> ResumeOutcome:
        """Resume an already-resolved GONE session.

        The shared seam both chat and the Mini App call — edit behaviour
        here, not in the wrapper. Assumes ``sess`` is already resolved
        (never receives ``None`` — the caller handles "no such session"
        itself, since that reply differs by surface and needs no dtach
        work at all).

        ``driver_user_id``, when given, attributes this resume directly
        (mirroring :meth:`create_session`'s own precedent) rather than
        via :meth:`aipager.bot.auth.AuthMixin._mark_driver`, which needs
        an ``Update`` the Mini App does not have.
        """
        if sess.status != Status.GONE:
            return ResumeOutcome(ok=False, reason="not_gone")

        if not sess.claude_session_id:
            return ResumeOutcome(ok=False, reason="no_transcript")

        label = sess.label
        session_name = sess.name
        resume_id = sess.claude_session_id
        cwd = sess.cwd or None
        # Defensive: clear the id BEFORE launch so a repeat-failure doesn't
        # rope us into an infinite resume loop (claude --resume against a
        # deleted transcript dies → socket disappears → /resume retries).
        sess.claude_session_id = ""
        self.registry.mark_dirty()

        # Use the session's actual (possibly scope-disambiguated) name so
        # resume reattaches the right socket, not a flat claude-<label>.
        short_name = session_name.removeprefix("claude-")
        # Determine the effective skip_perms: explicit override wins,
        # then the persisted value (already recovered from the state file).
        effective_skip_perms = (
            skip_perms_override if skip_perms_override is not None
            else sess.skip_perms
        )
        sys_extra = self._session_system_prompt(sess.scope_chat_id, label)
        ok, err = await inject.launch_session(
            short_name, resume_id=resume_id, cwd=cwd,
            skip_perms=effective_skip_perms,
            system_prompt_extra=sys_extra,
        )
        if not ok:
            # Restore the id so the user can try again after fixing whatever
            # broke (e.g. removing a stale socket).
            sess.claude_session_id = resume_id
            self.registry.mark_dirty()
            return ResumeOutcome(ok=False, reason="launch_failed", err=err)

        # Resume succeeded — recover state.
        sess.gone_at = None
        sess.skip_perms = effective_skip_perms
        self.registry.transition(session_name, Status.IDLE)
        if driver_user_id is not None:
            sess.last_driver_user_id = driver_user_id
            sess.created_by_user_id = sess.created_by_user_id or driver_user_id
        self.registry.last_active_session = session_name
        self.registry.mark_dirty()
        asyncio.create_task(self._maybe_update_bot_name(session_name))
        asyncio.create_task(self._update_bot_commands())

        log.info("[%s] Resumed (claude_session_id=%s, cwd=%s)",
                 label, resume_id, cwd or "<daemon>")
        return ResumeOutcome(ok=True, reason="resumed")

    async def _do_resume(self, *, label: str, reply_fn,
                          update: Update | None = None,
                          query=None,
                          skip_perms_override: bool | None = None) -> None:
        """Shared resume logic for /resume <name> and picker callbacks.

        ``reply_fn`` is the async-callable used to send the result back
        (``update.message.reply_text`` for command, ``query.edit_message_text``
        for callbacks). ``update`` is used to attribute the driver in team
        mode when available.

        Thin wrapper around :meth:`_do_resume_core`: this is the only
        place that resolves the label to a session and handles "no such
        session" — the core is never called with ``sess is None``.
        """
        sess = self.registry.find_by_label(
            label, calling_chat_id(update or query), include_gone=True)

        if sess is None:
            await reply_fn(
                f"⚠️ No session named <b>{html_mod.escape(label)}</b> in history.\n"
                f"Send <code>/resume</code> with no name to see what's available.",
                parse_mode="HTML",
            )
            return

        driver_user_id = (
            update.effective_user.id
            if update is not None and update.effective_user is not None
            else None
        )
        outcome = await self._do_resume_core(
            sess, skip_perms_override=skip_perms_override,
            driver_user_id=driver_user_id,
        )

        if outcome.reason == "not_gone":
            await reply_fn(
                f"⚠️ <b>{html_mod.escape(label)}</b> is already running.\n"
                f"Tap <code>/{html_mod.escape(label)}</code> in the keyboard "
                f"to switch to it.",
                parse_mode="HTML",
            )
            return

        if outcome.reason == "no_transcript":
            await reply_fn(
                f"⚠️ Session <b>{html_mod.escape(label)}</b> has no resumable "
                f"transcript on disk.\n"
                f"Start a fresh one with <code>/new {html_mod.escape(label)}</code>.",
                parse_mode="HTML",
            )
            return

        if outcome.reason == "launch_failed":
            await reply_fn(
                f"❌ Couldn't resume <b>{html_mod.escape(label)}</b>: "
                f"{html_mod.escape(outcome.err)}",
                parse_mode="HTML",
            )
            return

        # Resumed — dashboard out the result.
        dashboard = self._build_session_dashboard(sess)
        # Always try to surface where the user left off. If the cached
        # preview is empty (e.g. SessionEnd hook was dropped at GONE
        # time) re-derive from the transcript file on disk. A longer
        # cap here gives enough context to remember the conversation.
        preview = sess.last_assistant_preview or _read_preview(
            sess.transcript_path, max_chars=500,
        )
        header = f"♻️ Resumed <b>{html_mod.escape(label)}</b>"
        if preview:
            body = (
                f"{header}\n\n"
                f"{dashboard}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>📜 Last response from this session</i>\n"
                f"<blockquote>{html_mod.escape(preview)}</blockquote>"
            )
        else:
            body = f"{header}\n\n{dashboard}"

        await reply_fn(body, parse_mode="HTML")

    async def _stop_by_label(self, update: Update, target_label: str) -> None:
        """Stop a session by its label."""
        sess = self.registry.find_by_label(target_label, calling_chat_id(update))
        if sess is not None:
            outcome = await self._stop_session(sess, update=update)
            if not outcome.ok:
                await update.message.reply_text(f"[{target_label}] is not busy.")
            return
        await update.message.reply_text(f"⚠️ Unknown session: {target_label}")

    def _guess_session_from_text(self, text: str) -> TrackedSession | None:
        """Try to recover a session by scanning bot-message text for its label.

        Used to route replies to OLD bot messages whose IDs aren't in the
        msg_map (e.g., busy / dashboard messages sent before track_message
        covered them, or after a long enough session that the map evicted
        them). Every session-specific bot message prefixes the label as
        visible text ("⚙️ jim · Thinking…", "[jim] · INTERACTIVE", etc.),
        so a simple substring search with word-boundary checks works.

        Falls back to None when no label is found or multiple labels match
        ambiguously — better to defer to last_active_session than to
        guess wrong.
        """
        if not text:
            return None
        matches: list[TrackedSession] = []
        for cand in self.registry.all_sessions().values():
            if not cand.label or cand.status == Status.GONE:
                continue
            # Word-bounded match: label preceded by start / space / bracket /
            # non-alnum, and followed by space / · / ] / dot / non-alnum.
            pattern = (
                rf"(?:^|[\s\W])\[?{re.escape(cand.label)}\]?(?:[\s·•\].:]|$)"
            )
            if re.search(pattern, text):
                matches.append(cand)
        if len(matches) == 1:
            return matches[0]
        return None

    async def _switch_session(self, update: Update, target_label: str) -> None:
        """Switch active session when bare /<label> is tapped (no prompt)."""
        # Find in registry within the calling scope
        sess = self.registry.find_by_label(target_label, calling_chat_id(update))
        if sess is not None:
            name = sess.name
            self.registry.last_active_session = name
            self.registry.mark_dirty()
            asyncio.create_task(self._maybe_update_bot_name(name))
            dashboard = self._build_session_dashboard(sess)
            await update.message.reply_text(dashboard, parse_mode="HTML")
            return

        # Try auto-discover
        session_name = f"claude-{target_label}"
        if await inject.is_alive(session_name):
            sess = self.registry.get_or_create(session_name)
            self.registry.last_active_session = session_name
            self.registry.mark_dirty()
            asyncio.create_task(self._maybe_update_bot_name(session_name))
            asyncio.create_task(self._update_bot_commands())
            dashboard = self._build_session_dashboard(sess)
            await update.message.reply_text(dashboard, parse_mode="HTML")
            return

        await update.message.reply_text(f"⚠️ Unknown session: {target_label}")

    # ── Mini App session menu actions (perms / clear-queue / compact /
    #    restart / rename) — see design.md's ORCHESTRATOR OVERRIDE ──

    async def _kill_and_relaunch_core(
        self, sess: TrackedSession, *, target_skip_perms: bool,
        interrupt_first: bool,
    ) -> RestartOutcome:
        """Kill (or interrupt) a live session, poll for its dtach socket
        to disappear, then relaunch it in place with a possibly-different
        ``skip_perms`` — preserving ``claude_session_id``/``cwd`` so the
        transcript resume continuity is maintained.

        THE single seam every kill-then-relaunch sequence in this
        codebase goes through now — chat's own ``/perms`` (both its BUSY
        Ctrl-C-first branch and its IDLE hard-kill branch, in
        callbacks.py) and the Mini App's perms-switch and restart
        routes. This sequence has the worst bug history in the codebase
        (roadmap 8.1, two separate causes) — see design.md's
        ORCHESTRATOR OVERRIDE for why it now exists in exactly one
        place rather than being duplicated a third time.

        Re-validates liveness and the ``is_restarting()`` guard itself
        rather than trusting the caller, the same belt-and-braces
        pattern every other core in this module already uses. Chat's
        own callers never trip either guard (they already filter to a
        live, not-already-restarting session before calling in), so
        this changes nothing about chat's observable behaviour — it
        only matters for the Mini App, where a stale menu, a direct API
        probe, or a race with the session monitor must never be able to
        kick off a second kill/relaunch while one is already in flight.
        """
        label = sess.label
        if sess.status not in (Status.BUSY, Status.INTERACTIVE, Status.IDLE):
            return RestartOutcome(ok=False, reason="not_live", label=label)
        if sess.is_restarting():
            return RestartOutcome(ok=False, reason="already_restarting", label=label)

        session_name = sess.name
        resume_id = sess.claude_session_id
        cwd = sess.cwd or None
        sock = f"{inject.SOCK_PREFIX}{session_name.removeprefix('claude-')}.sock"

        # Everything from here to a confirmed relaunch is an expected
        # outage. Opened before the kill so the dying session's own
        # SessionEnd hook — which can arrive before kill_session/send_keys
        # returns — is covered too.
        sess.restarting_until = time.monotonic() + _PERMS_RESTART_QUIET

        if interrupt_first:
            await inject.send_keys(session_name, "C-c")
            # Short pause to let the Ctrl-C signal be processed before
            # the poll loop starts checking for the socket.
            await asyncio.sleep(0.5)
        else:
            await inject.kill_session(session_name)

        # Poll for socket disappearance (up to 3 s).
        for _ in range(_PERMS_POLL_COUNT):
            await asyncio.sleep(_PERMS_POLL_INTERVAL)
            if not Path(sock).is_socket():
                break
        else:
            # Socket still present after 3 s — give up. Reopen the
            # alarm: no relaunch is coming, so whatever state the
            # session is in now is news the caller needs.
            sess.restarting_until = 0.0
            return RestartOutcome(ok=False, reason="still_stopping", label=label)

        short_name = session_name.removeprefix("claude-")
        sys_extra = self._session_system_prompt(sess.scope_chat_id, label)
        ok, err = await inject.launch_session(
            short_name, skip_perms=target_skip_perms,
            resume_id=resume_id or None, cwd=cwd,
            system_prompt_extra=sys_extra,
        )
        if not ok:
            # The relaunch failed, so the session really is gone — stop
            # suppressing, or the caller is left believing it survived.
            sess.restarting_until = 0.0
            return RestartOutcome(
                ok=False, reason="launch_failed", label=label, err=err,
            )

        # Relaunch succeeded — restore state.
        sess.skip_perms = target_skip_perms
        sess.claude_session_id = resume_id  # restore — launch_session may clear
        sess.cwd = cwd or sess.cwd
        sess.gone_at = None
        self.registry.transition(session_name, Status.IDLE)
        self.registry.mark_dirty()

        log.info("[%s] kill+relaunch done (skip_perms=%s, interrupt_first=%s)",
                 label, target_skip_perms, interrupt_first)
        return RestartOutcome(
            ok=True, reason="done", label=label, skip_perms=target_skip_perms,
        )

    async def _perms_switch_core(
        self, sess: TrackedSession, target_skip_perms: bool,
    ) -> RestartOutcome:
        """Toggle permission mode on an already-resolved, live session.

        ``interrupt_first`` mirrors chat's own ``/perms`` branching
        exactly (``handlers._handle_perms_cmd`` /
        ``callbacks._handle_callback``'s ``perms_stop_switch``): BUSY
        gets a courtesy Ctrl-C first — one more chance to finish
        cleanly before the hard kill; anything else — including
        INTERACTIVE, which chat's own command folds into the same IDLE
        flow — goes straight to :meth:`_kill_and_relaunch_core`'s hard
        kill. So the outcome for a given starting status is identical
        to what ``/perms`` would already produce for it.
        """
        interrupt_first = sess.status == Status.BUSY
        return await self._kill_and_relaunch_core(
            sess, target_skip_perms=target_skip_perms,
            interrupt_first=interrupt_first,
        )

    async def _restart_session_core(self, sess: TrackedSession) -> RestartOutcome:
        """Kill and relaunch a live session with its OWN current
        ``skip_perms`` unchanged — a restart is not a mode switch.

        Always a hard kill, regardless of status: deliberately does
        NOT reuse perms' busy-only Ctrl-C-first courtesy, which exists
        to give a MODE SWITCH one soft chance. A user-invoked restart
        means "stop it and bring it back" — the same hard-kill
        semantics Kill already applies.
        """
        return await self._kill_and_relaunch_core(
            sess, target_skip_perms=sess.skip_perms, interrupt_first=False,
        )

    async def _clear_queue_core(self, sess: TrackedSession) -> ClearQueueOutcome:
        """Discard every prompt queued behind the session's in-progress
        turn. The turn itself is untouched — only Stop interrupts it.

        No awaits: refuses immediately, with no side effects, when
        there is nothing to clear — the same "no-op refusal" shape
        :meth:`_stop_session_core` already uses, so the refusal is
        pytest-testable with zero dtach mocking.
        """
        if not sess.pending_queue:
            return ClearQueueOutcome(ok=False, label=sess.label, dropped=0)
        dropped = len(sess.pending_queue)
        sess.pending_queue.clear()
        self.registry.mark_dirty()
        log.info("[%s] queue cleared from Mini App (dropped %d)",
                 sess.label, dropped)
        return ClearQueueOutcome(ok=True, label=sess.label, dropped=dropped)

    async def _compact_session_core(self, sess: TrackedSession) -> CompactOutcome:
        """Trigger ``/compact``, treated exactly like a normal queued
        user prompt — NOT the unconditional inject chat's reactive
        "Compact Now" button uses.

        Deliberate departure from chat's own compact button (design.md):
        that button is only ever offered reactively, from a
        ``context_warning`` hook event, so it never had to consider an
        already-full queue. This route is offered proactively at any
        time, so it needs the same overflow protection a normal queued
        message already gets.
        """
        label = sess.label
        if sess.status not in (Status.BUSY, Status.IDLE):
            return CompactOutcome(ok=False, reason="not_live", label=label)
        if sess.status == Status.BUSY:
            if not sess.queue_prompt("/compact", None):
                return CompactOutcome(ok=False, reason="queue_full", label=label)
            self.registry.mark_dirty()
            return CompactOutcome(ok=True, reason="queued", label=label)
        ok = await self._inject_prompt(sess, "/compact")
        if not ok:
            return CompactOutcome(ok=False, reason="send_failed", label=label)
        return CompactOutcome(ok=True, reason="sent", label=label)

    async def _rename_session_core(
        self, sess: TrackedSession, new_label: str,
    ) -> RenameOutcome:
        """Pure metadata mutation — ``sess.label`` only, never the
        internal dtach session name, socket, resume id or transcript
        path. Syntax validation and the collision check both already
        happened in server.py before this is ever called, so this core
        has no ``ok=False`` path at all.

        No-op short-circuit when the new label equals the current one:
        no mutation, no ``mark_dirty``, no bot-commands refresh — an
        idempotent no-op, not a silent redundant write.
        """
        previous = sess.label
        if new_label == previous:
            return RenameOutcome(
                ok=True, previous_label=previous, new_label=new_label,
                changed=False,
            )
        sess.label = new_label
        self.registry.mark_dirty()
        # Refresh Telegram's own `/`-command autocomplete list — the
        # "command list staleness" landmine (research.md gotcha #4).
        # Without this, `/oldlabel` keeps appearing in the client's
        # autocomplete (it now resolves to nothing, since find_by_label
        # matches on the live label, which just changed) and `/newlabel`
        # never appears until something else happens to trigger a sync.
        asyncio.create_task(self._update_bot_commands())
        log.info("[%s] renamed to %s from Mini App", previous, new_label)
        return RenameOutcome(
            ok=True, previous_label=previous, new_label=new_label, changed=True,
        )
