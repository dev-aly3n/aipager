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
import time
# Path itself is no longer dereferenced in this module (the poll loop
# that used it moved to session_ops.py's _kill_and_relaunch_core — see
# design.md ORCHESTRATOR OVERRIDE) but the name is kept importable here
# on purpose: tests/test_bot_callbacks_perms.py and
# tests/integration/perms_mode_ux/test_perms_command.py patch
# "aipager.bot.callbacks.Path", and removing the import would turn a
# harmless no-op patch into an AttributeError on every one of them.
from pathlib import Path  # noqa: F401
from typing import TYPE_CHECKING

from telegram import (
    Update,
)
from telegram.ext import (
    ContextTypes,
)

from aipager.dtach import inject

from aipager import preferences
from aipager.bot.settings_menu import (
    SECTIONS as SETTINGS_SECTIONS,
    render_settings_root,
    render_settings_section,
)
from aipager.config import (
    CHAT_ID,
)
from aipager.state import Status
from aipager.team import (
    attribution_label,
)

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
    calling_chat_id,
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


# Claude Code renders its permission prompt as a cursor menu whose shape
# depends on the tool. Observed on v2.1.220 for a Bash call:
#
#     > 1. Yes
#       2. Yes, and always allow access to <dir>/ from this project
#       3. No
#
# A tool with no directory scope to widen shows only Yes/No, so the index
# of "No" is NOT fixed. The daemon cannot read the labels: the
# PermissionRequest hook payload carries only tool_name/tool_input
# (dtach/hook_receiver.py), never the rendered options. So a deny has to
# be expressed as cursor movement that is correct for every shape.
#
# The invariant that makes that possible: the menu CLAMPS at the last
# item, it does not wrap. Verified by pushing Down five times at a
# three-item prompt — the cursor stopped on "3. No" rather than cycling
# back to "Yes". Overshooting therefore always lands on the last option,
# which is the negative one in every shape observed.
#
# Getting this wrong is not symmetric. Until this constant existed, deny
# sent a single Down and selected "Yes, and always allow" — it ran the
# tool the operator had just refused AND widened permissions for the rest
# of the session, while reporting "Denied" to the user and to the audit
# log. Overshooting can only ever land on a refusal, so it fails safe.
_DENY_OVERSHOOT = 5


_PERMS_POLL_COUNT = 15
_PERMS_POLL_INTERVAL = 0.2
# How long a `/perms` restart stays "expected" for the exit-alarm paths.
# Generous on purpose: the dying session's SessionEnd hook is a separate
# subprocess and can land after the relaunch has already succeeded, and that
# late arrival is precisely what used to be announced as a crash. The window
# expires on its own, so a genuine crash a moment later is still reported.
_PERMS_RESTART_QUIET = 10.0


class CallbackDispatchMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    @staticmethod
    async def _safe_answer(query, text: str | None = None, **kwargs) -> None:
        """Call ``query.answer(text, **kwargs)`` swallowing any "query is
        too old" or "already answered" errors.

        Used everywhere we want to set a toast text after the eager
        ack at the top of ``_handle_callback`` — Telegram refuses a
        second answer for the same query, so without this wrapper the
        whole handler would crash on the second ``answer`` call.
        ``kwargs`` forwards e.g. ``show_alert=True`` for a modal toast.
        """
        try:
            await query.answer(text, **kwargs)
        except Exception:
            pass

    async def _do_perms_switch_via_fn(self, sess, target_skip_perms: bool, edit_fn) -> None:
        """Kill + poll + relaunch with toggled skip_perms.

        Thin wrapper around :meth:`SessionOpsMixin._perms_switch_core` —
        the single kill/poll/relaunch seam chat and the Mini App now
        both go through (design.md ORCHESTRATOR OVERRIDE). ``edit_fn``
        is an async callable ``(text, **kw)`` used to update the
        in-progress message (either via query.edit_message_text or via
        status_msg.edit_text / update.message.reply_text, depending on
        the call site).
        """
        label = sess.label
        outcome = await self._perms_switch_core(sess, target_skip_perms)

        if outcome.reason == "still_stopping":
            await edit_fn(
                f"⚠️ <b>{html_mod.escape(label)}</b> is still stopping — "
                f"mode not changed. Try /perms again in a moment.",
                parse_mode="HTML",
            )
            return

        if outcome.reason == "launch_failed":
            await edit_fn(
                f"❌ Couldn't switch mode: {html_mod.escape(outcome.err)}",
                parse_mode="HTML",
            )
            return

        if not outcome.ok:
            # not_live / already_restarting: chat's own callers already
            # filter to a live, not-already-restarting session before
            # reaching here, so this never trips in practice — a silent
            # no-op is still safer than crashing on an unhandled branch.
            return

        mode_icon = "🤖" if target_skip_perms else "💬"
        mode_label = "Auto" if target_skip_perms else "Ask"
        await edit_fn(
            f"{mode_icon} <b>{html_mod.escape(label)}</b> is now in "
            f"{mode_label} mode.",
            parse_mode="HTML",
        )
        log.info("[%s] perms switched to skip_perms=%s", label, target_skip_perms)

    # Callback-data value tokens → the field value `preferences.set_preference`
    # expects. Only `simple_formatting` needs translation (bool ↔ on/off);
    # every other field's values already double as their own tokens, so
    # they round-trip through this map unchanged.
    _SETTINGS_VALUE_TOKENS = {
        "formatting": {"on": True, "off": False},
    }

    async def _dispatch_settings_action(self, update: Update, query, action: str) -> None:
        """Route one `_:set...` callback (see settings_menu.py's module
        docstring for the full callback-data contract).

        Navigation (root/section/back/close) is always allowed — viewing
        current values is never gated. A value-set tap
        (`_:set:<section>:<value>`) additionally requires admin in a
        **group** scope (`chat_id < 0`); DM scopes and personal-mode
        installs skip the check entirely, matching `_is_admin`'s own
        semantics there. An unresolvable `chat_id` (`None`) is treated the
        same as a group scope — fail closed rather than silently skip
        the check.
        """
        chat_id = calling_chat_id(update)
        parts = action.split(":")[1:]  # drop the leading "set"

        if not parts or parts == ["back"]:
            text, kb = render_settings_root(chat_id or 0)
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        if parts == ["close"]:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        if len(parts) == 1:
            section = parts[0]
            rendered = render_settings_section(chat_id or 0, section)
            if rendered is None:
                await self._safe_answer(query, "Invalid callback")
                return
            text, kb = rendered
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        if len(parts) == 2:
            section, token = parts
            if section not in SETTINGS_SECTIONS:
                await self._safe_answer(query, "Invalid callback")
                return

            # Fail closed: an unresolvable chat_id (`calling_chat_id`
            # returning None) is treated the same as a group scope rather
            # than skipped like a DM. `_is_admin` still returns True for a
            # genuinely-ungated case (personal-mode installs are always
            # admin), so this only tightens the check for the team/scope
            # modes where it matters — a permission gate should never
            # degrade to "allow" just because it couldn't identify the
            # chat.
            if (chat_id is None or chat_id < 0) and not self._is_admin(update):
                await self._safe_answer(
                    query, "Only an admin can change settings in this group.",
                    show_alert=True,
                )
                return

            # Safe: `section` was checked against SETTINGS_SECTIONS above,
            # and this dict's keys are exactly SETTINGS_SECTIONS's members
            # (both come from settings_menu.SECTIONS), so the lookup below
            # can never raise KeyError.
            field = {
                "layout": "layout", "formatting": "simple_formatting",
                "length": "answer_length", "level": "language_level",
            }[section]
            value_map = self._SETTINGS_VALUE_TOKENS.get(section)
            # `.get(token, token)` — an unrecognised token (e.g.
            # "_:set:formatting:sideways") falls through as the raw string
            # rather than raising KeyError here. That lets `set_preference`
            # be the single validation authority: its allow-list check
            # rejects the bogus value with `ValueError`, caught below, the
            # same way every other section's malformed tokens are already
            # handled.
            value = value_map.get(token, token) if value_map is not None else token
            try:
                preferences.set_preference(chat_id or 0, field, value)
            except ValueError:
                await self._safe_answer(query, "Invalid value")
                return

            text, kb = render_settings_section(chat_id or 0, section)
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        await self._safe_answer(query, "Invalid callback")

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button tap.

        Acknowledges the callback query immediately (with no toast text)
        so Telegram clears the button-spinner within a few hundred
        milliseconds even when the handler does long work. Subsequent
        toast calls go through :py:meth:`_safe_answer` which swallows
        the resulting error — toasts are a nice-to-have here; the
        actual outcome (message edits, status updates) is what the
        user really sees.
        """
        query = update.callback_query
        # Team mode: reject taps from users not on the allow-list with a
        # toast. Personal mode passes through unchanged (sentinel value).
        member = await self._authorize_callback(query)
        if member is None:
            return
        cb_data = query.data or ""
        original_text = query.message.text or "" if query.message else ""

        # Eager ack — clear the spinner before any slow work.
        await self._safe_answer(query)

        if ":" not in cb_data:
            await self._safe_answer(query, "Invalid callback")
            return

        session_name, action = cb_data.split(":", 1)

        if action == "stop":
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, "Session not found")
                return
            outcome = await self._stop_session(sess, query=query)
            if not outcome.ok:
                await self._safe_answer(query, f"[{sess.label}] is not busy.")
            return

        if action == "kill":
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name
            await self._safe_answer(query, f"Killing {label}...")
            await self._kill_session_by_label(query, label)
            return

        if action == "kill-confirm":
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name.removeprefix("claude-")
            await self._safe_answer(query, f"Killing {label}...")
            await self._kill_session_by_label(query, label)
            return

        if action == "kill-cancel":
            try:
                await query.edit_message_text(
                    "↩️ Cancelled (no session killed).",
                )
            except Exception:
                pass
            return

        # ---- Voice-extra remote-install flow (item 5.3 follow-up) ----
        if session_name == "__voice__":
            if action == "cancel":
                try:
                    await query.edit_message_text(
                        "↩️ OK, voice not installed."
                    )
                except Exception:
                    pass
                return
            if action == "install":
                # Fire-and-forget — the install can take a couple
                # minutes and we want the callback handler to return
                # quickly so Telegram doesn't time out.
                asyncio.create_task(self._install_voice_extra(query))
                return
            if action == "restart":
                asyncio.create_task(self._restart_daemon(query))
                return
            return  # unknown __voice__ sub-action

        if action == "retry":
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, "Session not found")
                return
            if not sess.last_prompt:
                await self._safe_answer(query, "Nothing to retry")
                return
            if not await inject.is_alive(session_name):
                await self._safe_answer(query, f"Session '{session_name}' not alive")
                return
            # Re-inject the last prompt (last_prompt stays set for retry-of-retry)
            prompt = sess.last_prompt
            ok = await self._inject_prompt(sess, prompt)
            if ok:
                await self._safe_answer(query, f"Retrying [{sess.label}]")
                # Delete the error message — busy animation replaces it
                try:
                    await self._app.bot.delete_message(
                        chat_id=CHAT_ID,
                        message_id=query.message.message_id,
                    )
                except Exception:
                    pass
                self.registry.transition(session_name, Status.BUSY)
                await self._send_busy_and_animate(sess)
                log.info("[%s] Retry: %s", sess.label, prompt[:80])
            else:
                await self._safe_answer(query, "Failed to retry")
            return

        if action == "compact":
            sess = self.registry.get(session_name)
            if not sess:
                await self._safe_answer(query, "Session not found")
                return
            if not await inject.is_alive(session_name):
                await self._safe_answer(query, f"Session '{session_name}' not found")
                return
            ok = await self._inject_prompt(sess, "/compact")
            if ok:
                await self._safe_answer(query, f"Compacting [{sess.label}]")
                try:
                    await self._app.bot.delete_message(
                        chat_id=CHAT_ID,
                        message_id=query.message.message_id,
                    )
                except Exception:
                    pass
                log.info("[%s] Compact triggered by user", sess.label)
            else:
                await self._safe_answer(query, "Failed to send /compact")
            return

        # ---- /settings menu callbacks ----------------------------------
        # `_:set`, `_:set:<section>`, `_:set:back`, `_:set:close` (nav —
        # always allowed) and `_:set:<section>:<value>` (write — admin-
        # gated in groups). `action` already carries everything after the
        # first colon (e.g. "set:layout:merged"); the "set" prefix is a
        # decoy of the `session_name, action = cb_data.split(":", 1)`
        # split above landing on the `_` global-action sentinel.
        if session_name == "_" and (action == "set" or action.startswith("set:")):
            await self._dispatch_settings_action(update, query, action)
            return

        if action == "clear_gone":
            # Hide GONE sessions from /status. Preserves resume
            # metadata (claude_session_id, cwd) so /resume keeps
            # working — see TrackedSession.hidden_from_status.
            hidden = []
            for name, sess in list(self.registry.all_sessions().items()):
                if sess.status == Status.GONE and not sess.hidden_from_status:
                    sess.hidden_from_status = True
                    hidden.append(sess.label)
            if hidden:
                self.registry.mark_dirty()
                await self._safe_answer(query, f"Hidden {len(hidden)} session(s)")
                try:
                    await query.edit_message_text(
                        f"Hidden from /status: {', '.join(hidden)}\n"
                        f"<i>Still available in /resume.</i>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                log.info("Hid gone sessions from /status: %s", hidden)
            else:
                await self._safe_answer(query, "No gone sessions to hide")
            return

        # ---- /perms callbacks -----------------------------------------
        if action in ("perms_confirm", "perms_cancel",
                      "perms_stop_switch", "perms_wait"):
            pending = self._perms_pending.pop(session_name, None)
            target_skip_perms = (pending or {}).get("target_skip_perms", False)
            label = (pending or {}).get("label", session_name.removeprefix("claude-"))
            sess = self.registry.get(session_name)

            if action == "perms_cancel":
                try:
                    await query.edit_message_text("↩️ Cancelled.")
                except Exception:
                    pass
                return

            if action == "perms_wait":
                try:
                    await query.edit_message_text(
                        f"⏳ Cancelled — try /perms again when "
                        f"<b>{html_mod.escape(label)}</b> is idle.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                return

            if sess is None:
                try:
                    await query.edit_message_text("⚠️ Session not found.")
                except Exception:
                    pass
                return

            async def _edit(text, **kw):
                try:
                    await query.edit_message_text(text, **kw)
                except Exception:
                    await self._app.bot.send_message(
                        chat_id=CHAT_ID, text=text, **kw,
                    )

            if action == "perms_confirm":
                # IDLE Ask→Auto confirmed.
                await self._do_perms_switch_via_fn(sess, target_skip_perms, _edit)
                return

            if action == "perms_stop_switch":
                # BUSY: send Ctrl-C, then poll for socket disappearance.
                # Same deliberate outage as the IDLE path — the session is
                # meant to exit here, so its exit is not news. Thin
                # wrapper around the same shared core the IDLE path
                # (_do_perms_switch_via_fn) and the Mini App both go
                # through now (design.md ORCHESTRATOR OVERRIDE).
                outcome = await self._perms_switch_core(sess, target_skip_perms)

                if outcome.reason == "still_stopping":
                    try:
                        await query.edit_message_text(
                            f"⚠️ <b>{html_mod.escape(label)}</b> is still stopping — "
                            f"mode not changed. Try /perms again in a moment.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    return

                if outcome.reason == "launch_failed":
                    try:
                        await query.edit_message_text(
                            f"❌ Couldn't switch mode: {html_mod.escape(outcome.err)}",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    return

                if not outcome.ok:
                    return

                mode_icon = "🤖" if target_skip_perms else "💬"
                mode_label = "Auto" if target_skip_perms else "Ask"
                try:
                    await query.edit_message_text(
                        f"{mode_icon} <b>{html_mod.escape(label)}</b> is now in "
                        f"{mode_label} mode.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                log.info("[%s] perms switched (stop_switch) to skip_perms=%s",
                         label, target_skip_perms)
                return

        # ---- /resume mode-picker callbacks ----------------------------
        if action in ("resume_mode_ask", "resume_mode_auto", "resume_mode_cancel"):
            sess = self.registry.get(session_name)
            label = self._resume_mode_pending.pop(session_name, None)
            if label is None and sess is not None:
                label = sess.label
            if label is None:
                label = session_name.removeprefix("claude-")

            if action == "resume_mode_cancel":
                try:
                    await query.edit_message_text("↩️ Cancelled.")
                except Exception:
                    pass
                return

            skip_perms_override = (action == "resume_mode_auto")

            async def _reply(text, **kw):
                try:
                    await query.edit_message_text(text, **kw)
                except Exception:
                    await self._app.bot.send_message(
                        chat_id=CHAT_ID, text=text, **kw,
                    )

            await self._do_resume(
                label=label, reply_fn=_reply,
                skip_perms_override=skip_perms_override,
            )
            return

        # ---- /resume picker callbacks ---------------------------------
        if action == "resume" and session_name and session_name != "_":
            # Resolve the user-facing label from the registry: suffixed
            # names (label__d<chat_id>) don't round-trip through a plain
            # prefix strip, and _do_resume matches on sess.label.
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name.removeprefix("claude-")
            # Store pending label for the mode-picker callbacks.
            self._resume_mode_pending[session_name] = label
            # Edit the picker message to show the mode-picker keyboard.
            persisted_skip_perms = sess.skip_perms if sess else False
            kb = self._build_resume_mode_keyboard(session_name, persisted_skip_perms)
            try:
                await query.edit_message_text(
                    f"Resume <b>{html_mod.escape(label)}</b> as:",
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception:
                pass
            return

        if action.startswith("resume_page:"):
            try:
                page = int(action.split(":", 1)[1])
            except (IndexError, ValueError):
                page = 0
            text, kb = self._render_resume_picker(
                page=page, scope_chat_id=calling_chat_id(query),
            )
            try:
                await query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=kb,
                )
            except Exception:
                pass
            return

        if action == "resume_noop":
            # The page indicator is a no-op tap; just clear the spinner.
            return

        # ---- /new name-conflict callbacks -----------------------------
        if action in ("new_resume", "new_replace", "new_cancel"):
            pending = self._new_conflict_pending.pop(session_name, None)
            sess = self.registry.get(session_name)
            label = sess.label if sess else session_name.removeprefix("claude-")

            if action == "new_cancel":
                try:
                    await query.edit_message_text(
                        "↩️ Cancelled — no session changed.",
                    )
                except Exception:
                    pass
                return

            prompt = (pending or {}).get("prompt", "")
            skip_perms = (pending or {}).get("skip_perms", False)

            if action == "new_resume":
                # Live session → switch to it; GONE session → /resume flow.
                if sess and sess.status != Status.GONE:
                    self.registry.last_active_session = session_name
                    self.registry.mark_dirty()
                    asyncio.create_task(
                        self._maybe_update_bot_name(session_name)
                    )
                    if prompt and sess.queue_prompt(prompt,
                                                    pending.get("msg_id", 0)):
                        self.registry.mark_dirty()
                    try:
                        await query.edit_message_text(
                            f"↩️ Switched to <b>{html_mod.escape(label)}</b>"
                            + ("\n📝 Prompt queued" if prompt else ""),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    return

                # GONE: route through the shared _do_resume helper.
                async def _reply(text, **kw):
                    try:
                        await query.edit_message_text(text, **kw)
                    except Exception:
                        await self._app.bot.send_message(
                            chat_id=CHAT_ID, text=text, **kw,
                        )

                await self._do_resume(label=label, reply_fn=_reply)
                # Queue the prompt into the freshly-resumed session.
                if prompt:
                    resumed = self.registry.get(session_name)
                    if resumed and resumed.queue_prompt(
                        prompt, pending.get("msg_id", 0),
                    ):
                        self.registry.mark_dirty()
                return

            if action == "new_replace":
                # Kill alive socket first, then launch fresh (no resume_id).
                if sess and sess.status != Status.GONE:
                    await inject.kill_session(session_name)
                    # Wait briefly for socket to disappear so the next
                    # launch_session's "already exists" check passes.
                    sock = f"{inject.SOCK_PREFIX}{label}.sock"
                    from pathlib import Path as _Path
                    for _ in range(10):
                        await asyncio.sleep(0.2)
                        if not _Path(sock).is_socket():
                            break
                # Drop the resume metadata so the new session is truly fresh.
                if sess:
                    sess.claude_session_id = ""
                    sess.transcript_path = ""
                    sess.cwd = ""
                    sess.gone_at = None
                    self.registry.mark_dirty()

                try:
                    await query.edit_message_text(
                        f"🚀 Launching <b>{html_mod.escape(label)}</b> "
                        f"(fresh)…",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

                ok, err = await inject.launch_session(label, skip_perms=skip_perms)
                if not ok:
                    try:
                        await self._app.bot.send_message(
                            chat_id=CHAT_ID,
                            text=f"❌ {html_mod.escape(err)}",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    return

                new_sess = self.registry.get_or_create(session_name)
                if new_sess.status in (Status.GONE, Status.UNKNOWN):
                    self.registry.transition(session_name, Status.IDLE)
                self.registry.last_active_session = session_name
                self.registry.mark_dirty()
                asyncio.create_task(self._maybe_update_bot_name(session_name))
                asyncio.create_task(self._update_bot_commands())
                if prompt and new_sess.queue_prompt(
                    prompt, pending.get("msg_id", 0),
                ):
                    self.registry.mark_dirty()

                try:
                    await self._app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            f"✅ <b>{html_mod.escape(label)}</b> launched"
                            + ("\n📝 Prompt queued" if prompt else "")
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                log.info("[%s] /new conflict resolved via Replace (prompt=%s)",
                         label, bool(prompt))
                return

        is_option = action.startswith("opt") and action[3:].isdigit()

        if action not in ACTION_VERBS and not is_option and action != "submit":
            await self._safe_answer(query, f"Unknown: {action}")
            return

        sess = self.registry.get(session_name)
        if not sess:
            await self._safe_answer(query, "Session not found")
            return

        if not await inject.is_alive(session_name):
            await self._safe_answer(query, f"Session '{session_name}' not found")
            return

        # Inject keystrokes
        ok = True
        perm = sess.pending_permission or {}
        if is_option or action == "submit":
            log.info("[%s] Callback: action=%s, multi_select=%s, has_perm=%s",
                     sess.label, action, perm.get("multi_select"), bool(perm))

        if is_option and perm.get("multi_select"):
            # ── Multi-select: toggle checkbox, update keyboard, return early ──
            option_index = int(action[3:])
            cursor_pos = perm.get("cursor_pos", 0)
            selected = perm.get("selected", set())

            # Navigate from cursor_pos to option_index
            delta = option_index - cursor_pos
            key = "Down" if delta > 0 else "Up"
            for _ in range(abs(delta)):
                if not await inject.send_keys(session_name, key):
                    ok = False
                    break
            if ok:
                await asyncio.sleep(0.1)
                ok = await inject.send_keys(session_name, "Enter")  # toggle checkbox

            if ok:
                # Update selected set
                if option_index in selected:
                    selected.discard(option_index)
                else:
                    selected.add(option_index)
                perm["selected"] = selected
                perm["cursor_pos"] = option_index

                opt_label = perm["options"][option_index].get("label", f"Option {option_index+1}")
                toggled = "☑" if option_index in selected else "⬜"
                await self._safe_answer(query, f"{toggled} {opt_label}")

                # Rebuild keyboard with updated checkmarks
                keyboard = self._build_inline_ask_keyboard(
                    session_name, perm["options"],
                    multi_select=True, selected=selected)
                text = self._build_busy_text(sess.label, "Waiting", sess)
                await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                log.info("[%s] Multi-select toggle: opt%d (%s), selected=%s",
                         sess.label, option_index, toggled, selected)
            else:
                await self._safe_answer(query, "Failed to send keys")
            return

        elif action == "submit" and perm.get("multi_select"):
            # ── Multi-select: advance to next tab via Right arrow ──
            # TUI tabs: ← ☒ Q1  ☐ Q2  ...  ✔ Submit →
            # Right arrow moves one tab forward.
            selected = perm.get("selected", set())
            options = perm.get("options", [])
            questions = perm.get("questions", [])
            current_idx = perm.get("current_idx", 0)
            next_idx = current_idx + 1
            is_last = next_idx >= len(questions)

            # Build verb from selected options
            sel_labels = [options[i].get("label", f"#{i+1}")
                          for i in sorted(selected)]
            verb = "Selected: " + ", ".join(sel_labels) if sel_labels else "Submitted (none)"

            # Right to advance one tab (to next question, or to Submit)
            ok = await inject.send_keys(session_name, "Right")
            if ok and is_last:
                # Last question — landed on Submit tab, press Enter to submit
                await asyncio.sleep(0.15)
                ok = await inject.send_keys(session_name, "Enter")

            if ok:
                await self._safe_answer(query, f"✅ {verb[:180]}")

                # Collapse into tool_history
                question_text = perm.get("question", "?")
                collapsed = f"❓ {question_text[:40]} → {verb}"
                sess.record_tool(collapsed, True)

                # Audit reply (multi-select submit path)
                from aipager import audit as audit_mod
                # `member` was resolved by _authorize_callback at the top
                # of _handle_callback; in personal mode it's a sentinel
                # (id=0) — only record real allow-listed users.
                actor = member if member is not None and member.id != 0 else None
                audit_mod.append(
                    session=sess.name, label=sess.label, action="Answered",
                    tool="AskUserQuestion",
                    summary=f"{question_text[:120]} → {verb[:80]}",
                    user_id=actor.id if actor else None,
                    username=actor.label if actor else "",
                    scope_label=self._scope_label(sess.scope_chat_id),
                    scope_chat_id=sess.scope_chat_id or None,
                )
                by_attr = f" by {attribution_label(actor)}" if actor else ""
                try:
                    await self._app.bot.send_message(
                        CHAT_ID,
                        f"✓ <b>{html_mod.escape(sess.label)}</b> · "
                        f"Answered{html_mod.escape(by_attr)} · "
                        f"{html_mod.escape(question_text[:80])} → "
                        f"{html_mod.escape(verb[:80])}",
                        parse_mode="HTML",
                        reply_to_message_id=(sess.busy_msg_id
                                             if sess.busy_msg_id and sess.busy_msg_id > 0
                                             else None),
                    )
                except Exception:
                    log.debug("[%s] audit message send failed", sess.label,
                              exc_info=True)

                if not is_last:
                    # More questions — Right moved to next question tab
                    next_q = questions[next_idx]
                    next_options = next_q.get("options", [])
                    next_multi = next_q.get("multiSelect", False)
                    sess.pending_permission = {
                        "ask_question": True,
                        "question": next_q.get("question", "?"),
                        "options": next_options,
                        "questions": questions,
                        "current_idx": next_idx,
                        "multi_select": next_multi,
                        "cursor_pos": 0,
                        "selected": set(),
                        "tool_info": perm.get("tool_info"),
                        "wait_started_at": perm.get("wait_started_at"),
                    }
                    await asyncio.sleep(0.3)
                    keyboard = self._build_inline_ask_keyboard(
                        session_name, next_options,
                        multi_select=next_multi)
                    text = self._build_busy_text(sess.label, "Waiting", sess)
                    await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                    log.info("[%s] Multi-select submit, advanced to Q%d/%d",
                             sess.label, next_idx + 1, len(questions))
                else:
                    # Last question — done; discount wait time from elapsed timer
                    wait_start = perm.get("wait_started_at", 0)
                    if wait_start and sess.busy_started_at:
                        sess.busy_started_at += time.monotonic() - wait_start
                    sess.pending_permission = None
                    self.registry.transition(session_name, Status.BUSY)
                    keyboard = self._build_stop_keyboard(session_name)
                    text = self._build_busy_text(sess.label, "Working", sess)
                    await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                    self._start_animation(sess)
                    log.info("[%s] Multi-select submit complete", sess.label)
            else:
                await self._safe_answer(query, "Failed to send keys")
            return

        elif is_option:
            option_index = int(action[3:])
            verb = f"Selected option {option_index + 1}"
            for _ in range(option_index):
                if not await inject.send_keys(session_name, "Down"):
                    ok = False
                    break
            if ok:
                await asyncio.sleep(0.1)
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "allow":
            verb = ACTION_VERBS[action]
            ok = await inject.send_keys(session_name, "Enter")
        elif action == "allow_always":
            verb = ACTION_VERBS[action]
            # One Down → "Yes, and always allow …", the second item when the
            # tool has a scope to widen. When it has none the menu is only
            # Yes/No, so this lands on "No" and refuses — the harmless
            # direction to be wrong in.
            ok = await inject.send_keys(session_name, "Down")
            if ok:
                await asyncio.sleep(0.1)
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "deny":
            verb = ACTION_VERBS[action]
            # Overshoot to the last item — see _DENY_OVERSHOOT.
            for _ in range(_DENY_OVERSHOOT):
                if not await inject.send_keys(session_name, "Down"):
                    ok = False
                    break
                await asyncio.sleep(0.1)
            if ok:
                ok = await inject.send_keys(session_name, "Enter")
        elif action == "continue":
            verb = ACTION_VERBS[action]
            ok = await inject.send_keys(session_name, "Enter")
        else:
            verb = action

        if ok:
            await self._safe_answer(query, f"{verb} [{sess.label}]")

            if sess.pending_permission:
                # Collapse current question into tool_history
                perm = sess.pending_permission
                if perm.get("ask_question"):
                    audit_detail = perm["question"][:80]
                    collapsed = f"❓ {audit_detail[:40]} → {verb}"
                    audit_tool_name = "AskUserQuestion"
                else:
                    audit_detail = perm.get("tool_summary", "Permission")[:80]
                    collapsed = f"🔑 {audit_detail[:60]} → {verb}"
                    audit_tool_name = (perm.get("tool_info") or {}).get("name", "")
                sess.record_tool(collapsed, True)

                # Persistent audit trail to disk (jsonl).
                from aipager import audit as audit_mod
                actor = (
                    member if member is not None and member.id != 0 else None
                )
                audit_mod.append(
                    session=sess.name, label=sess.label, action=verb,
                    tool=audit_tool_name, summary=audit_detail,
                    user_id=actor.id if actor else None,
                    username=actor.label if actor else "",
                    scope_label=self._scope_label(sess.scope_chat_id),
                    scope_chat_id=sess.scope_chat_id or None,
                    denied=(verb == "Deny"),
                )

                # Audit reply in chat — persistent record of the decision
                # the user just made. Threaded under the busy message so
                # the scrollback reads as a conversation.
                audit_icon = {
                    "Allowed": "✅",
                    "Denied": "🚫",
                    "Continue": "▶️",
                }.get(verb, "·")
                by_attr = f" by {attribution_label(actor)}" if actor else ""
                try:
                    await self._app.bot.send_message(
                        CHAT_ID,
                        f"{audit_icon} <b>{html_mod.escape(sess.label)}</b> · "
                        f"{verb}{html_mod.escape(by_attr)} · "
                        f"{html_mod.escape(audit_detail)}",
                        parse_mode="HTML",
                        reply_to_message_id=(sess.busy_msg_id
                                             if sess.busy_msg_id and sess.busy_msg_id > 0
                                             else None),
                    )
                except Exception:
                    log.debug("[%s] audit message send failed", sess.label,
                              exc_info=True)

                # Multi-question AskUserQuestion: advance to next question
                questions = perm.get("questions", [])
                current_idx = perm.get("current_idx", 0)
                next_idx = current_idx + 1

                if perm.get("ask_question") and next_idx < len(questions):
                    # More questions — show the next one inline
                    # NOTE: No Tab needed — Claude Code TUI auto-advances
                    # to the next unanswered question tab after Enter.
                    next_q = questions[next_idx]
                    next_options = next_q.get("options", [])
                    next_multi = next_q.get("multiSelect", False)
                    sess.pending_permission = {
                        "ask_question": True,
                        "question": next_q.get("question", "?"),
                        "options": next_options,
                        "questions": questions,
                        "current_idx": next_idx,
                        "multi_select": next_multi,
                        "cursor_pos": 0,
                        "selected": set(),
                        "tool_info": perm.get("tool_info"),
                        "wait_started_at": perm.get("wait_started_at"),
                    }
                    await asyncio.sleep(0.3)  # let TUI process and auto-advance
                    keyboard = self._build_inline_ask_keyboard(
                        session_name, next_options,
                        multi_select=next_multi)
                    text = self._build_busy_text(sess.label, "Waiting", sess)
                    await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                    log.info("[%s] Multi-question: advanced to Q%d/%d",
                             sess.label, next_idx + 1, len(questions))
                else:
                    # Last question (or non-AskUserQuestion) — done
                    if perm.get("ask_question") and len(questions) > 1:
                        # Multi-question form: TUI auto-advances to Submit tab
                        # after last option selection. Send Enter to submit.
                        await asyncio.sleep(0.3)
                        await inject.send_keys(session_name, "Enter")
                    # Discount wait time from elapsed timer
                    wait_start = perm.get("wait_started_at", 0)
                    if wait_start and sess.busy_started_at:
                        sess.busy_started_at += time.monotonic() - wait_start
                    sess.pending_permission = None
                    # Transition back to BUSY and restart animation
                    self.registry.transition(session_name, Status.BUSY)
                    keyboard = self._build_stop_keyboard(session_name)
                    text = self._build_busy_text(sess.label, "Working", sess)
                    await self._edit_busy_raw(sess.busy_msg_id, text, reply_markup=keyboard)
                    self._start_animation(sess)
                    log.info("[%s] Inline permission: %s", sess.label, verb)
            else:
                # Original behavior: edit the separate permission message
                try:
                    await query.edit_message_text(f"{original_text}\n\n→ {verb}")
                except Exception:
                    pass
                self.registry.remove_message(query.message.message_id)
                # Mark session as busy after user interaction
                self.registry.transition(session_name, Status.BUSY)
                log.info("[%s] %s", sess.label, verb)
        else:
            await self._safe_answer(query, f"Failed to send to {session_name}")
