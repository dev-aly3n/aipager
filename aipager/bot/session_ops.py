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
    reason: str = ""     # "stale" when the tap came from an earlier turn


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


# ---- Reply context (design.md Part 2) --------------------------------
#
# Pure wording helpers for SessionOpsMixin._build_reply_context. Kept as
# plain module-level functions (not methods) since none of them touch
# `self` — the same separation-of-concerns split as transport.py's pure
# helpers. Internal to this module; the Tester must observe their output
# only via read_snapshot(...)["reply_context"] or pending_queue[...][3]
# (entrypoints.md), never call these directly.

def _reply_author(reply_to, bot_id: int) -> str | None:
    """``"you"`` | ``"Telegram user <id>"`` | ``None``.

    Numeric id only, never a label — labels are mutable. ``None`` when
    ``from_user`` is absent (anonymous-admin / channel-post messages),
    in which case every template below drops the attribution clause
    entirely rather than guessing.
    """
    user = getattr(reply_to, "from_user", None)
    if user is None:
        return None
    if getattr(user, "id", None) == bot_id:
        return "you"
    return f"Telegram user {user.id}"


def _reply_fmt_time(date) -> str:
    """``"21:40"`` from a Telegram ``Message.date``, or ``""`` if unusable."""
    try:
        return date.strftime("%H:%M")
    except (AttributeError, TypeError, ValueError):
        return ""


def _reply_attribution_clause(date, author: str | None) -> str:
    """``"sent 21:40, by you"`` / ``"sent 21:40, from Telegram user
    256113222"`` / ``"sent 21:40"`` (no attribution available)."""
    t = _reply_fmt_time(date)
    if not author:
        return f"sent {t}" if t else ""
    verb = "by" if author == "you" else "from"
    return f"sent {t}, {verb} {author}" if t else f"{verb} {author}"


def _whole_message_context(
    excerpt: str, date, author: str | None, *, with_file: bool, file_path,
) -> str:
    attribution = _reply_attribution_clause(date, author)
    where = f" ({attribution})" if attribution else ""
    if with_file:
        tail = f"Full text: {file_path} (read only if needed)."
    else:
        tail = "(message was queued while busy — full text was not retained)."
    return (
        "The user's message is a reply to an earlier message in this "
        f"session{where}. They are pointing at it for reference — it is "
        f"not itself a new instruction. Excerpt: \"{excerpt}\"\n{tail}"
    )


def _nontext_context(date, author: str | None) -> str:
    attribution = _reply_attribution_clause(date, author)
    where = f" ({attribution})" if attribution else ""
    return (
        "The user's message is a reply to an earlier non-text message in "
        f"this session{where} — no text content is available to show. "
        "They are pointing at it for reference; it is not itself a new "
        "instruction."
    )


def _highlight_context(
    fragment: str, date, author: str | None, *, manual: bool, file_path=None,
) -> str:
    verb = "highlighting" if manual else "quoting"
    part = "this part of" if manual else "this quoted part of"
    if author == "you":
        owner = "your earlier message"
    elif author:
        owner = f"{author}'s earlier message"
    else:
        owner = "an earlier message"
    t = _reply_fmt_time(date)
    when = f" (sent {t})" if t else ""
    tail = f"\nFull text: {file_path} (read only if needed)." if file_path else ""
    return (
        f"The user replied while {verb} {part} {owner}{when}: "
        f"\"{fragment}\". They are pointing at that specific passage — "
        f"it is not itself a new instruction.{tail}"
    )


def _external_quote_context(fragment: str) -> str:
    return (
        "The user replied while quoting this fragment from a message I "
        "can't otherwise see (from another chat, a forum topic, or a "
        "message that's no longer available): "
        f"\"{fragment}\". They are pointing at that specific passage — "
        "it is not itself a new instruction."
    )


class SessionOpsMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    def _adopt_by_typed_name(self, session_name: str,
                             target_label: str) -> TrackedSession:
        """Adopt the session `session_name`, which was rebuilt from what
        the operator typed.

        `/<label>` falls back to reconstructing `claude-{label}` and
        adopting whatever is alive under it. A session adopted that way
        should answer to the label that produced its name, rather than
        keeping whatever `get_or_create` derived from the internal name
        or revived from a recent kill.

        That only holds for a name the registry did not already know.
        A tracked session's label is authoritative: `/rename` moves the
        label while the internal name stays put, so the pre-rename name
        goes on resolving to a live socket indefinitely, and re-labelling
        an already-tracked entry here would silently undo the rename.

        `known` is not scope-aware, because `registry.get` keys on the
        internal name alone. In a multi-scope install an unscoped legacy
        name reached through this fallback is therefore not filtered by
        scope — unchanged by this method, which reproduces exactly what
        the direct `get_or_create` call here did before.
        """
        known = self.registry.get(session_name) is not None
        sess = self.registry.get_or_create(session_name)
        if not known:
            sess.label = target_label
        return sess

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

    async def _inject_prompt(self, sess, text: str, reply_context: str = "",
                             *, msg_id: int | None = None,
                             chat_id: int | None = None,
                             driver_user_id: int | None = None) -> bool:
        """Send a Telegram-originated prompt into the session.

        Marks the session origin = "telegram" (the safety boundary keys
        off this) and prepends the identity marker for free-text prompts.
        Slash commands are sent raw (a marker line would break them).

        ``reply_context`` defaults to ``""`` — deliberately, so that any
        caller (existing or future) that doesn't pass it explicitly
        clears a prior turn's reply pointer rather than leaking it into
        this one (design.md's staleness guard). Only the three
        immediate-inject reply handlers and the queue-drain site pass a
        real value.

        ``msg_id``/``chat_id`` identify the originating Telegram message
        (design.md "queue handoff") — carried on the per-message note so
        the ``UserPromptSubmit`` hook's pick-up matcher can report which
        specific message a turn consumed (``track_message`` + a 👍
        reaction), and so ``discard_queued_input`` has something to have
        cleared on Stop. ``None`` for callers with no single originating
        message (e.g. a literal ``/compact`` triggered by a hook event
        rather than operator input).

        ``driver_user_id`` is the raw Telegram user id of whoever sent
        THIS message — the note's ``sender_key`` half that
        ``transport.mixed_sender_note_outstanding`` compares against.
        Deliberately NOT read from ``sess.last_driver_user_id`` here:
        that field is only set by ``AuthMixin._mark_driver`` in
        team/scope mode, stays ``None`` for the entire lifetime of a
        personal-mode session, and — even in team mode — may not yet
        reflect THIS message at every call site. Falling back to it
        would write every personal-mode note under the same ``0``
        sender_key while the hold-check (which always reads the live
        ``update.effective_user.id``) compares against the real id,
        turning one operator's own back-to-back messages into a
        "mixed sender" false positive. Callers with a live ``Update``
        pass ``update.effective_user.id``; callers with none (the
        queue-drain site, Retry, a literal ``/compact``) fall back to
        ``sess.last_driver_user_id`` — the best available approximation
        when there is no live sender to read.

        That fallback is only good enough for bookkeeping (grouping
        "same sender" runs for the mixed-sender hold), never for
        *permission attribution*. ``sess.last_driver_user_id`` is
        mutable and can move on to a different person between a message
        failing and a later Retry, or between a message being queued and
        the daemon draining it — review rev-iter1-001/002. Role/member
        resolution below therefore reads only the id the CALLER
        explicitly supplied (before the fallback assigned below), never
        the fallback value: a caller with no live identity for this
        specific message (Retry, a literal ``/compact``, the queue-drain
        site) gets a note resolved with ``member=None``/``role=None`` —
        floor permissions, ``bypass_safety=False`` — rather than
        silently inheriting whoever ``sess.last_driver_user_id`` happens
        to name right now. Fail closed: an under-attributed note can
        only make a future merged turn *more* restrictive (design.md's
        merge invariant), never less, so this trades away Retry's
        permission elevation in the common single-user case rather than
        risk lending an unrelated sender's ``bypass_safety`` to it.
        """
        explicit_driver_user_id = driver_user_id
        if driver_user_id is None:
            driver_user_id = sess.last_driver_user_id
        sess.last_prompt_origin = "telegram"
        body = text
        if not text.lstrip().startswith("/"):
            marker = self._prompt_marker(sess)
            if marker:
                body = f"{marker}\n{text}"
        # Write a per-message policy note for this turn (design.md "queue
        # handoff") — replaces the old one-slot write_snapshot() call at
        # send time. Every message gets its own note now; the
        # UserPromptSubmit hook merges whichever ones a turn actually
        # picks up into the canonical snapshot at pick-up, not here. The
        # safety-relevant fields (role/scope/member, Phase E) only matter
        # in v2 scope mode — the marker line that flips a hook's origin
        # check to "telegram" is only ever prepended there (see
        # _prompt_marker above), so a note in legacy/personal mode cannot
        # change PreToolUse enforcement. style_text is different: it's
        # keyed by the scope's real Telegram chat id, not by aipager.yaml
        # scope config, so it's meaningful in every mode. Goes through
        # resolve_preferences (never get_preferences directly): this is
        # THE critical path per design.md — a caller here that bypassed
        # it would make the per-session settings feature do nothing.
        # Best-effort.
        try:
            from aipager import preferences as prefs_mod
            from aipager.policy_snapshot import write_note
            member = role = scope = None
            if self.scopes is not None:
                # See the docstring above: permission attribution uses
                # ONLY the explicitly-passed id, never the fallback used
                # for sender_key below. scope is unaffected — it can
                # only add restrictions, never grant them, so it is safe
                # to resolve regardless of whether the sender is known.
                member = self._driver_user_by_id(explicit_driver_user_id)
                role = (self.policy.get_role(member.role)
                        if member is not None else None)
                scope = self._scope_for(sess.scope_chat_id)
            style = prefs_mod.style_text(
                prefs_mod.resolve_preferences(
                    sess.scope_chat_id or 0, sess.preference_overrides(),
                )
            )
            sender_key = (sess.scope_chat_id or 0, driver_user_id or 0)
            write_note(
                sess.name, role, scope, member,
                msg_id=msg_id, chat_id=chat_id, sender_key=sender_key,
                body=body, raw_text=text,
                style_text=style, reply_context=reply_context,
            )
        except Exception:
            log.debug("policy note write failed", exc_info=True)
        return await inject.send_text_and_enter(sess.name, body)

    def _build_reply_context(
        self, msg, sess, *, bot_id: int, allow_file: bool,
    ) -> str:
        """The single place reply-pointer wording is built (design.md
        Part 2). Called from all three reply-bearing handlers
        (``_handle_message``, ``_dispatch_voice_transcript``,
        ``_handle_file``) — never from the Mini App or slash-command
        paths, which aren't replies.

        Returns ``""`` when the prompt isn't a reply worth flagging: no
        ``reply_to_message`` and no ``quote`` at all, or a reply to the
        session's own current latest message with no highlight attached
        (routing already lands on the right session in that case, so a
        pointer would be redundant). A highlighted fragment always
        produces context, even when the replied-to message is the
        latest — highlighting is itself the signal.

        ``allow_file`` gates writing the full-text `/tmp` fallback file:
        the caller passes ``False`` whenever the session is BUSY, so a
        queued reply's file write can never race a still-pending
        drain's file write for the same session (design.md Part 4).
        """
        reply_to = getattr(msg, "reply_to_message", None)
        quote = getattr(msg, "quote", None)
        quote_text = getattr(quote, "text", None) if quote else None

        if quote and quote_text:
            fragment = quote_text[:1000]
            if len(quote_text) > 1000:
                fragment += "…(truncated)"
            if reply_to is not None:
                author = _reply_author(reply_to, bot_id)
                file_path = None
                if allow_file and len(quote_text) > 1000:
                    from aipager.policy_snapshot import (
                        reply_context_path,
                        write_reply_context_file,
                    )
                    write_reply_context_file(
                        sess.name,
                        header=("Full text of the highlighted fragment "
                               "(truncated above to fit the prompt "
                               "context):"),
                        full_text=quote_text,
                    )
                    file_path = reply_context_path(sess.name)
                return _highlight_context(
                    fragment, getattr(reply_to, "date", None), author,
                    manual=bool(getattr(quote, "is_manual", False)),
                    file_path=file_path,
                )
            return _external_quote_context(fragment)

        if reply_to is None:
            return ""

        is_latest = reply_to.message_id in {
            v for v in (sess.last_msg_id, sess.busy_msg_id, sess.trigger_msg_id)
            if v and v > 0
        }
        if is_latest:
            return ""

        text = reply_to.text or reply_to.caption
        if not text:
            return _nontext_context(
                getattr(reply_to, "date", None), _reply_author(reply_to, bot_id),
            )

        excerpt = text[:80]
        if len(text) > 80:
            excerpt += "…"
        file_path = None
        if allow_file:
            from aipager.policy_snapshot import (
                reply_context_path,
                write_reply_context_file,
            )
            write_reply_context_file(
                sess.name,
                header="Full text of the message you're replying to:",
                full_text=text,
            )
            file_path = reply_context_path(sess.name)
        return _whole_message_context(
            excerpt, getattr(reply_to, "date", None),
            _reply_author(reply_to, bot_id),
            with_file=allow_file, file_path=file_path,
        )

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

        # 1. Send Escape twice to Claude Code — proven interrupt
        # behaviour this change does not touch.
        await inject.send_keys(sess.name, "Escape")
        await asyncio.sleep(0.15)
        await inject.send_keys(sess.name, "Escape")

        # 1b. Wipe whatever Claude's own not-yet-picked-up queue
        # surfaced into the input box (design.md "Stop and /clearqueue
        # on the same primitive") — the Escapes above can move it there
        # without cancelling the running task, and left alone it would
        # concatenate onto the next prompt. Read once, up front, so the
        # "was anything actually outstanding" check and the combined
        # drop count below agree on the same snapshot.
        from aipager.policy_snapshot import clear_notes_dir, list_outstanding_notes
        outstanding_notes = list_outstanding_notes(sess.name)
        if outstanding_notes:
            await inject.discard_queued_input(sess.name)

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
        dropped = len(sess.pending_queue) + len(outstanding_notes)
        sess.pending_queue.clear()
        clear_notes_dir(sess.name)
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
        if query is not None and not sess.tap_is_for_this_turn(
                getattr(getattr(query, "message", None), "message_id", None)):
            # Button taps are checked HERE rather than at the call site, so a
            # future caller cannot forget. `update`-driven callers (the /stop
            # command, `/<label> stop`) pass no query and are always current.
            await self._safe_answer(
                query, "That task already finished — use the current card")
            return StopOutcome(ok=False, label=sess.label, reason="stale")
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
            # The ✅ reaction stays — it's the same instant, low-effort
            # acknowledgement every other command-driven action gets, and
            # is gone as soon as the operator looks away. The count itself
            # needs a durable, chat-visible message: tester-iter1-001
            # found /stop discarding Claude's own held-but-not-picked-up
            # queue (this feature's whole point) with the count nowhere
            # the operator could see it — a reaction alone is closer to a
            # vanishing toast than the "chat acknowledgement" entrypoints.md
            # promises (see intent.md's own open question #1).
            await self._react(update, "✅")
            if getattr(update, "message", None) is not None:
                try:
                    await update.message.reply_text(ack)
                except Exception:
                    log.debug("[%s] stop acknowledgement reply failed",
                              sess.label, exc_info=True)

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
            # remember_label: the entry is about to be re-created by the
            # monitor's next scan (the socket outlives SIGTERM briefly), and
            # without this the re-creation derives the label from the
            # internal name and silently undoes a /rename.
            self.registry.remove(session_name, remember_label=True)
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
            skip_perms=effective_skip_perms, is_relaunch=True,
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

    def _guess_session_from_text(
        self, text: str, scope_chat_id: int | None = None,
    ) -> TrackedSession | None:
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

        ``scope_chat_id`` (design.md Part 0) scopes the label scan to one
        chat, same as :meth:`SessionRegistry.all_sessions` /
        :meth:`SessionRegistry.find_by_label` — a label visible in one
        chat's transcript must never resolve to a same-named session
        living in a different chat. Defaults to ``None`` (no scoping) so
        the handful of unit tests that call this directly keep working.
        """
        if not text:
            return None
        matches: list[TrackedSession] = []
        for cand in self.registry.all_sessions(scope_chat_id).values():
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

    def _resolve_reply_target(
        self, reply_to, chat_id: int | None,
    ) -> TrackedSession | None:
        """Scoped levels 1-3 of the reply-routing ladder (design.md Part 0).

        1. exact hit in the msg_map, 2. a scan for any session whose
        ``last_msg_id`` matches (catches replies too old for the capped
        map), 3. a text-guess from the replied-to message's own text
        (catches replies from before this daemon run). All three are
        scoped to ``chat_id`` — see :meth:`SessionRegistry.get_session_by_msg`
        — so a message id (or a visible label) that collides across two
        chats can never resolve into the wrong chat's session.

        Returns ``None`` to signal "fall through to last_active_session"
        (level 4) — that stays inline at each of the three call sites,
        since their "nothing found at all" wording (level 5) differs.
        """
        if not reply_to:
            return None
        scope = chat_id if chat_id is not None else 0
        sess = self.registry.get_session_by_msg(reply_to.message_id, scope)
        if sess:
            return sess
        for cand in self.registry.all_sessions(scope).values():
            if cand.last_msg_id == reply_to.message_id:
                return cand
        return self._guess_session_from_text(
            reply_to.text or reply_to.caption or "", scope,
        )

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
            sess = self._adopt_by_typed_name(session_name, target_label)
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
            resume_id=resume_id or None, cwd=cwd, is_relaunch=True,
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
        turn AND everything Claude itself is holding but hasn't
        confirmed picking up yet. The turn itself is untouched — only
        Stop interrupts it.

        Refuses immediately, with no side effects, when there is
        nothing to clear — the same "no-op refusal" shape
        :meth:`_stop_session_core` already uses, so the refusal is
        pytest-testable with zero dtach mocking. The shared seam both
        chat's ``/clearqueue`` and the Mini App's clearqueue route call
        (design.md "Stop and /clearqueue on the same primitive") — ONE
        call to :func:`inject.discard_queued_input`, never preceded by
        the interrupt pair :meth:`_stop_session_core` sends, so this can
        never cancel the running turn. Sends no keys at all when only
        ``pending_queue`` (never-injected, aipager-side-only holds) is
        being cleared — there is nothing in the pty's input box to wipe
        unless a note had actually reached it.
        """
        from aipager.policy_snapshot import clear_notes_dir, list_outstanding_notes

        outstanding_notes = list_outstanding_notes(sess.name)
        dropped = len(sess.pending_queue) + len(outstanding_notes)
        if not dropped:
            return ClearQueueOutcome(ok=False, label=sess.label, dropped=0)
        sess.pending_queue.clear()
        clear_notes_dir(sess.name)
        if outstanding_notes:
            await inject.discard_queued_input(sess.name)
        self.registry.mark_dirty()
        log.info("[%s] queue cleared (dropped %d)", sess.label, dropped)
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
