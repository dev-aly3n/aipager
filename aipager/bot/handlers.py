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
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ContextTypes,
)

from aipager.dtach import inject

from aipager.config import (
    BACK_BUTTON, COMMANDS_BUTTON,
    FILE_DOWNLOAD_DIR, KEYBOARD_PARENTS, MODELS_BUTTON,
    TEMPLATES_BUTTON,
)
from aipager.state import QUEUE_CAP, Status, TrackedSession

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





class CommandHandlersMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    async def _react(self, update: Update, emoji: str) -> None:
        """React to the user's message with an emoji."""
        try:
            await self._app.bot.set_message_reaction(
                update.effective_chat.id, update.message.message_id, emoji,
            )
        except Exception:
            pass  # reaction API may not be available in all contexts

    async def _install_voice_extra(self, query) -> None:
        """Run the install subprocess and edit the prompt message with
        progress, ending in success or failure.

        Triggered by the `__voice__:install` inline-keyboard tap when
        the user sends a voice message without the voice extra. Lets
        users on the road install the extra without SSH access.
        """
        from aipager import updater

        installer = updater._detect_installer()
        cmd = updater.install_extra_cmd(installer, "voice")
        if cmd is None:
            # Should be unreachable for "voice" today — install_extra_cmd
            # only returns None for an extra it doesn't know about. Keep
            # a defensive fallback so a future extra can't strand the user.
            await self._safe_edit_callback(
                query,
                "⚠️ This aipager build has no installer recipe for the\n"
                "voice extra. Update aipager and try again.",
            )
            return

        await self._safe_edit_callback(
            query, "📦 Installing voice extra…",
        )

        # Launch the install. Capture stdout/stderr so a failure message
        # can show the user what went wrong.
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, subprocess.SubprocessError) as e:
            await self._safe_edit_callback(
                query, f"❌ Couldn't start installer: {html_mod.escape(str(e))}",
            )
            return

        # Edit the message every few seconds so the user has a
        # heartbeat that the install is alive.
        started = time.monotonic()
        last_edit_at = started
        while True:
            try:
                # Wait briefly for the proc to finish; if it doesn't,
                # edit the message and loop.
                await asyncio.wait_for(proc.wait(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                elapsed = int(time.monotonic() - started)
                if time.monotonic() - last_edit_at >= 5.0:
                    await self._safe_edit_callback(
                        query,
                        f"📦 Installing voice extra — still working… ({elapsed}s)",
                    )
                    last_edit_at = time.monotonic()

        stdout = (await proc.stdout.read()).decode("utf-8", errors="replace")
        if proc.returncode == 0:
            # Only brew genuinely wipes the extra on upgrade (its
            # formula venv is rebuilt from scratch). Editable /
            # pip-user / venv installs keep faster-whisper indefinitely
            # — the warning would just be noise there.
            footer = ""
            if installer == "brew":
                footer = (
                    "\n\n<i>Note: a future `brew upgrade aipager` may "
                    "rebuild the formula venv and drop faster-whisper. "
                    "If voice stops working, tap Install again.</i>"
                )

            # Always offer the restart button — service units use
            # systemctl / launchctl; other installs spawn a detached
            # replacement and SIGTERM ourselves. Either way the user
            # doesn't need terminal access.
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔄 Restart daemon now",
                    callback_data="__voice__:restart",
                ),
            ]])
            await self._safe_edit_callback(
                query,
                "✅ Installed. Voice features need a daemon restart "
                "to load the new module." + footer,
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            tail = stdout.strip()[-500:]
            await self._safe_edit_callback(
                query,
                f"❌ Install failed (exit {proc.returncode}):\n"
                f"<pre>{html_mod.escape(tail)}</pre>",
                parse_mode="HTML",
            )

    async def _restart_daemon(self, query) -> None:
        """Trigger a clean restart.

        Service-managed daemons (systemd-user / launchd) go through
        their respective managers. Foreground / editable daemons spawn
        a detached replacement that waits for us to die, then we
        SIGTERM ourselves — so users on their phone don't need
        terminal access to pick up a code change.
        """
        import platform
        from aipager.service import (
            LINUX_UNIT_PATH, MACOS_LABEL, MACOS_PLIST_PATH, _run,
        )

        sysname = platform.system().lower()
        if sysname == "linux" and LINUX_UNIT_PATH.exists():
            await self._safe_edit_callback(
                query, "🔄 Restarting via systemctl --user…",
            )
            # systemctl will kill us; the unit's Restart=on-failure /
            # the service definition handles relaunch.
            _run(["systemctl", "--user", "restart", "aipager.service"])
            return
        if sysname == "darwin" and MACOS_PLIST_PATH.exists():
            await self._safe_edit_callback(
                query, "🔄 Restarting via launchctl kickstart…",
            )
            _run(["launchctl", "kickstart", "-k",
                  f"gui/{os.getuid()}/{MACOS_LABEL}"])
            return

        # No service unit — spawn a detached replacement and self-kill.
        # The shell wrapper polls our PID via `kill -0`; once we die
        # (HookReceiver.stop unlinks /tmp/aipager.sock as part of
        # cli.py's SIGTERM handler) the wrapper execs aipager start.
        parent_pid = os.getpid()
        log_path = "/tmp/aipager.log"
        try:
            log_fd = os.open(
                log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644,
            )
        except OSError as e:
            await self._safe_edit_callback(
                query,
                f"⚠️ Couldn't open {log_path}: {html_mod.escape(str(e))}\n"
                "Please restart manually.",
            )
            return

        shell_cmd = (
            f"while kill -0 {parent_pid} 2>/dev/null; do sleep 0.2; done; "
            f"exec {shlex.quote(sys.executable)} -m aipager.cli start"
        )
        try:
            proc = subprocess.Popen(
                ["sh", "-c", shell_cmd],
                stdout=log_fd, stderr=log_fd, stdin=subprocess.DEVNULL,
                start_new_session=True, close_fds=True,
            )
        except OSError as e:
            os.close(log_fd)
            await self._safe_edit_callback(
                query,
                f"⚠️ Couldn't spawn replacement: {html_mod.escape(str(e))}\n"
                "Please restart manually.",
            )
            return
        os.close(log_fd)

        # If the shell wrapper died inside 300 ms, the spawn failed —
        # don't kill ourselves and surface the failure.
        await asyncio.sleep(0.3)
        if proc.poll() is not None:
            await self._safe_edit_callback(
                query,
                "⚠️ Replacement daemon failed to start. "
                "Please restart manually.",
            )
            return

        # Tell the user before we die. The HTTP edit needs a beat to
        # flush before SIGTERM tears the event loop down.
        await self._safe_edit_callback(
            query,
            "🔄 Restarting — send your voice message again in a few seconds.",
        )
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    async def _handle_start_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start and /help — friendly welcome with current state."""
        if not await self._authorize(update, allow_read_only=True):
            return
        sessions = sorted(
            (sess.label, sess.status.name.lower())
            for sess in self.registry.all_sessions().values()
            if sess.status != Status.GONE and sess.label
        )
        if sessions:
            session_block = "\n".join(f"  • <b>{lbl}</b> · {status}"
                                     for lbl, status in sessions)
        else:
            session_block = "  <i>(no sessions yet)</i>"

        text = (
            "\U0001f44b <b>aipager</b> — Telegram remote for Claude Code\n\n"
            "Talk to your local Claude sessions from this chat. The daemon "
            "is running and mirroring sessions to you live.\n\n"
            "<b>Tracked sessions</b>\n"
            f"{session_block}\n\n"
            "<b>How to use</b>\n"
            "  • Tap a session name on the keyboard below to switch to it.\n"
            "  • Send a plain message — it goes to the active session.\n"
            "  • Reply to a session's message to pin your prompt to that session.\n\n"
            "<b>Open a new session on your computer</b>\n"
            "  <code>aipager session &lt;name&gt;</code>\n\n"
            "<b>Commands</b>\n"
            "  /status — per-session dashboard\n"
            "  /stop — interrupt the active session\n"
            "  /kill — terminate a session\n"
            "  /new — launch a new session (alias for `aipager session`)\n"
        )
        try:
            await self._app.bot.send_message(
                update.effective_chat.id, text, parse_mode="HTML",
            )
        except Exception:
            log.warning("Failed to send /start welcome", exc_info=True)
        # Make sure the persistent keyboard is showing.
        await self._send_keyboard(level="main")

    async def _handle_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command — rich per-session dashboard."""
        if not await self._authorize(update, allow_read_only=True):
            return
        sessions = self.registry.all_sessions()
        if not sessions:
            discovered = await inject.list_sessions()
            if not discovered:
                await update.message.reply_text("No sessions found.")
                return
            for name in discovered:
                self.registry.get_or_create(name)
            sessions = self.registry.all_sessions()

        blocks = []
        has_gone = False
        for name, sess in sessions.items():
            alive = await inject.is_alive(name)
            # Reconcile: socket alive but status GONE → recover to IDLE
            if alive and sess.status == Status.GONE:
                self.registry.transition(name, Status.IDLE)
            # Hidden flag is set by "Clear gone sessions". If a hidden
            # session comes back alive (e.g. via /resume), unhide so it
            # reappears in /status. /resume always shows it regardless.
            if alive and sess.hidden_from_status:
                sess.hidden_from_status = False
                self.registry.mark_dirty()
            if sess.status == Status.GONE and sess.hidden_from_status:
                continue
            icon = "🟢" if alive else "🔴"
            if not alive and not sess.hidden_from_status:
                has_gone = True
            status_str = sess.status.name.lower()
            if sess.status == Status.BUSY and sess.busy_started_at:
                elapsed_s = int(time.monotonic() - sess.busy_started_at)
                if elapsed_s >= 60:
                    status_str += f" {elapsed_s // 60}m{elapsed_s % 60}s"
                else:
                    status_str += f" {elapsed_s}s"
            # Read live data from statusLine file
            sl = self._read_status_file(name)
            # Build table rows
            rows = []
            model = (sl.get("model") if sl else None) or sess.model_name or "—"
            ctx_pct = sl["ctx_pct"] if sl else (sess.last_token_pct or 0)
            cost = f"${sl['cost']:.2f}" if sl and sl["cost"] >= 0.01 else "—"
            queue = str(len(sess.pending_queue)) if sess.pending_queue else "0"
            rows.append(f"  Model  {html_mod.escape(model)}")
            rows.append(f"  Ctx    {ctx_pct}%")
            rows.append(f"  Cost   {cost}")
            if sess.pending_queue:
                rows.append(f"  Queue  {queue}")
            # Last tool for BUSY sessions
            if sess.status == Status.BUSY and sess.tool_history:
                last_summary, last_done = sess.tool_history[-1]
                t_icon = "✅" if last_done and last_done != "failed" else ("❌" if last_done == "failed" else "⏳")
                rows.append(f"  Tool   {t_icon} {html_mod.escape(last_summary[:50])}")
            header = f"{icon} <b>{html_mod.escape(sess.label)}</b> · {status_str}"
            table = "\n".join(rows)
            blocks.append(f"{header}\n<code>{table}</code>")

        keyboard = None
        if has_gone:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Clear gone sessions", callback_data="_:clear_gone"),
            ]])
        await update.message.reply_text(
            "\n\n".join(blocks), parse_mode="HTML", reply_markup=keyboard,
        )
        asyncio.create_task(self._update_bot_commands())

    @staticmethod
    def _read_status_file(session_name: str) -> dict | None:
        """Read cost and context from the statusLine JSON file."""
        try:
            data = json.loads(Path(f"/tmp/claude-status-{session_name}.json").read_text())
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None
        ctx = data.get("context_window", {})
        pct = ctx.get("used_percentage")
        remaining = ctx.get("remaining_percentage")
        if pct is not None:
            ctx_pct = round(pct)
        elif remaining is not None:
            ctx_pct = round(100 - remaining)
        else:
            ctx_pct = 0
        return {
            "ctx_pct": ctx_pct,
            "cost": data.get("cost", {}).get("total_cost_usd", 0),
            "model": data.get("model", {}).get("display_name", ""),
            "total_output": ctx.get("total_output_tokens", 0),
        }

    async def _handle_stop_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /stop command — stop the last active session."""
        if not await self._authorize(update):
            return
        name = self.registry.last_active_session
        if not name:
            await update.message.reply_text("No active session to stop.")
            return
        sess = self.registry.get(name)
        if not sess or sess.status not in (Status.BUSY, Status.INTERACTIVE):
            label = sess.label if sess else "?"
            await update.message.reply_text(f"[{label}] is not busy.")
            return
        await self._stop_session(sess, update=update)

    async def _handle_clearqueue_cmd(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /clearqueue — drop pending queued prompts for the last
        active session without interrupting the running task.

        `/stop` already drains the queue, but at the cost of interrupting
        the current work. `/clearqueue` is the "just stop QUEUING for
        the next thing" surgical tool: the current prompt keeps running,
        the queue empties.
        """
        if not await self._authorize(update):
            return
        name = self.registry.last_active_session
        if not name:
            await update.message.reply_text(
                "No active session — switch to one with /<label> first.",
            )
            return
        sess = self.registry.get(name)
        if not sess:
            await update.message.reply_text(f"Session '{name}' not found.")
            return
        dropped = len(sess.pending_queue)
        if dropped == 0:
            await update.message.reply_text(
                f"Nothing to clear in [{html_mod.escape(sess.label)}].",
                parse_mode="HTML",
            )
            return
        sess.pending_queue.clear()
        self.registry.mark_dirty()
        plural = "s" if dropped > 1 else ""
        await update.message.reply_text(
            f"🗑️ Cleared {dropped} queued message{plural} for "
            f"[<b>{html_mod.escape(sess.label)}</b>].",
            parse_mode="HTML",
        )
        log.info("[%s] /clearqueue cleared %d entries", sess.label, dropped)

    async def _handle_kill_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /kill <label> — destroy a session entirely."""
        if not await self._authorize(update):
            return
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            # No label given — show inline keyboard with session choices
            sessions = self.registry.all_sessions()
            alive = [
                sess for sess in sessions.values()
                if sess.status != Status.GONE and sess.label
            ]
            if not alive:
                await update.message.reply_text("No sessions to kill.")
                return
            buttons = [
                [InlineKeyboardButton(f"💀 {sess.label}", callback_data=f"{sess.name}:kill")]
                for sess in alive
            ]
            await update.message.reply_text(
                "Which session to kill?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        target_label = parts[1].strip()
        # Two-tap confirmation: show inline [💀 Kill] [Cancel] instead of
        # destroying immediately. One mistype on a phone shouldn't wipe a
        # session; the user explicitly confirms here.
        sess = self.registry.find_by_label(target_label, calling_chat_id(update))
        target_name = sess.name if sess is not None else None
        if target_name is None:
            await update.message.reply_text(
                f"⚠️ Unknown or already-gone session: {target_label}",
            )
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "💀 Kill", callback_data=f"{target_name}:kill-confirm"),
            InlineKeyboardButton(
                "Cancel", callback_data=f"{target_name}:kill-cancel"),
        ]])
        await update.message.reply_text(
            f"⚠️ Kill session [<b>{html_mod.escape(target_label)}</b>]? "
            "This will terminate the running claude process.",
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    async def _handle_new_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new <name> [prompt] — launch a new Claude Code session.

        Prefix the name with ``!`` to launch with
        ``--dangerously-skip-permissions`` (e.g. ``/new !dev fix the bug``).
        Without the prefix, claude runs with its default safety checks.
        """
        if not await self._authorize(update):
            return
        text = update.message.text.strip()
        parts = text.split(maxsplit=2)  # /new <name> [prompt...]
        if len(parts) < 2:
            await update.message.reply_text(
                "Usage: /new &lt;name&gt; [initial prompt]\n"
                "Prefix the name with <code>!</code> to skip permission checks "
                "(<code>--dangerously-skip-permissions</code>).\n"
                "Example: /new dev fix the auth bug\n"
                "Example: /new !dev fix the auth bug",
                parse_mode="HTML",
            )
            return

        raw_name = parts[1].strip()
        skip_perms = raw_name.startswith("!")
        name = raw_name.lstrip("!").strip()
        prompt = parts[2].strip() if len(parts) > 2 else ""

        if not name:
            await update.message.reply_text(
                "⚠️ Session name is empty after stripping <code>!</code>.",
                parse_mode="HTML",
            )
            return

        # Name-conflict check — both alive sessions and entries in the
        # GONE history get the same Resume / Replace / Cancel prompt so
        # the user doesn't accidentally throw away a conversation by
        # typing /new with a familiar name. The button callbacks land
        # in _handle_callback under the new_resume / new_replace /
        # new_cancel actions defined below.
        # Resolve the calling scope from the message's chat. New sessions
        # get a disambiguated internal name (claude-<label>__<suffix>) so
        # two scopes can reuse the same label; the user only sees <label>.
        from aipager.scope import disambiguated_name
        chat_id = calling_chat_id(update)
        scope_kind = "group" if (chat_id is not None and chat_id < 0) else "dm"
        existing = self.registry.find_by_label(name, chat_id, include_gone=True)
        if existing is not None and (
            existing.status != Status.GONE
            or existing.claude_session_id
        ):
            await self._send_new_conflict_prompt(
                update=update,
                existing=existing,
                prompt=prompt,
                skip_perms=skip_perms,
            )
            return

        status_msg = await update.message.reply_text(
            f"🚀 Launching <b>{html_mod.escape(name)}</b>"
            + (" <code>(unsafe)</code>" if skip_perms else "") + "...",
            parse_mode="HTML",
        )

        if chat_id is not None:
            session_name = disambiguated_name(name, chat_id, scope_kind)
        else:
            session_name = f"claude-{name}"
        short_name = session_name.removeprefix("claude-")

        ok, err = await inject.launch_session(short_name, skip_perms=skip_perms)
        if not ok:
            await status_msg.edit_text(f"❌ {html_mod.escape(err)}")
            return

        # Switch active session to the new one
        sess = self.registry.get_or_create(session_name)
        sess.label = name
        if chat_id is not None:
            sess.scope_chat_id = chat_id
            sess.scope_kind = scope_kind
        # Recover from GONE/UNKNOWN — we just verified the socket is alive
        if sess.status in (Status.GONE, Status.UNKNOWN):
            self.registry.transition(session_name, Status.IDLE)
        # Team-mode attribution: record the creator (and current driver).
        self._mark_driver(sess, update)
        self.registry.last_active_session = session_name
        self.registry.mark_dirty()
        asyncio.create_task(self._maybe_update_bot_name(session_name))
        asyncio.create_task(self._update_bot_commands())

        # Queue the initial prompt if given — it'll drain on first IDLE
        if prompt:
            # Flatten newlines (lesson: newlines cause premature Enter)
            prompt = prompt.replace("\n", " — ")
            if sess.queue_prompt(prompt, update.message.message_id):
                self.registry.mark_dirty()

        await status_msg.edit_text(
            f"✅ <b>{html_mod.escape(name)}</b> launched"
            + ("\n📝 Prompt queued" if prompt else ""),
            parse_mode="HTML",
        )
        log.info("Launched session %s (prompt=%s)", name, bool(prompt))

    # ---- /new name-conflict prompt --------------------------------------

    async def _send_new_conflict_prompt(self, *, update: Update,
                                          existing: TrackedSession,
                                          prompt: str,
                                          skip_perms: bool) -> None:
        """Render the Resume / Replace / Cancel buttons when /new hits a known name.

        ``existing.status`` tells us whether the conflict is with a live
        session (`!= GONE`) or a history entry (`GONE` with a stashed
        claude_session_id). Both flows share the same buttons; the
        callback distinguishes via the session's current status.
        """
        label = existing.label
        alive = existing.status != Status.GONE

        self._new_conflict_pending[existing.name] = {
            "prompt": prompt,
            "skip_perms": skip_perms,
            "user_id": (update.effective_user.id
                        if update.effective_user else 0),
            "msg_id": update.message.message_id,
        }

        if alive:
            header = (
                f"⚠️ <b>{html_mod.escape(label)}</b> is already running.\n"
                f"What would you like to do?"
            )
            resume_label = "↩️ Switch to it"
        else:
            preview = existing.last_assistant_preview or ""
            header = (
                f"♻️ <b>{html_mod.escape(label)}</b> was previously used.\n"
            )
            if preview:
                header += (
                    f"<i>Last response:</i>\n"
                    f"<blockquote>{html_mod.escape(preview)}</blockquote>\n"
                )
            header += "What would you like to do?"
            resume_label = "♻️ Resume"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(resume_label,
                                     callback_data=f"{existing.name}:new_resume"),
                InlineKeyboardButton("🆕 Replace (fresh)",
                                     callback_data=f"{existing.name}:new_replace"),
            ],
            [
                InlineKeyboardButton("↩️ Cancel",
                                     callback_data=f"{existing.name}:new_cancel"),
            ],
        ])
        await update.message.reply_text(
            header, parse_mode="HTML", reply_markup=keyboard,
        )

    async def _handle_resume_cmd(self, update: Update,
                                  ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /resume [<name>] — resume a previous session, or open picker."""
        if not await self._authorize(update):
            return
        parts = (update.message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            text, kb = self._render_resume_picker(page=0)
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=kb,
            )
            return
        name = parts[1].strip().lstrip("@/").lower()
        # Reuse direct-resume helper so the /resume command and the picker
        # button take exactly the same code path (and produce the same
        # errors / dashboard reply).
        await self._do_resume(
            label=name,
            reply_fn=update.message.reply_text,
            update=update,
        )

    async def _handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle text messages — replies to notifications or /<label> commands."""
        if not await self._authorize(update):
            return
        text = update.message.text.strip()
        if not text:
            return

        # Keyboard navigation + button matching — MUST stay above /<label> handlers
        # because some buttons (e.g. "/compact") start with slash.
        if text == TEMPLATES_BUTTON:
            await self._send_keyboard(level="templates")
            return
        if text == COMMANDS_BUTTON:
            await self._send_keyboard(level="commands")
            return
        if text == MODELS_BUTTON:
            await self._send_keyboard(level="models")
            return
        if text == BACK_BUTTON:
            # Context-aware: go to parent of current level (models→commands, etc.)
            parent = KEYBOARD_PARENTS.get(self._keyboard_level, "main")
            await self._send_keyboard(level=parent)
            return

        # Quick template buttons — inject predefined prompt into active session
        if text in self._template_map:
            await self._send_template(update, self._template_map[text])
            return

        # Claude Code slash commands — inject instantly, no BUSY transition
        if text in self._command_map:
            await self._send_command(update, self._command_map[text])
            return

        # Model choices — instant commands
        if text in self._model_map:
            await self._send_command(update, self._model_map[text])
            return

        # /<label> <prompt> — direct send
        if text.startswith("/") and " " in text:
            parts = text.split(" ", 1)
            target_label = parts[0][1:]
            prompt_text = parts[1].strip()
            if target_label and prompt_text:
                if prompt_text.lower() == "stop":
                    await self._stop_by_label(update, target_label)
                    return
                await self._direct_send(update, target_label, prompt_text)
                return

        # Bare /<label> — switch active session (e.g. keyboard tap)
        if text.startswith("/") and " " not in text:
            target_label = text[1:]
            if target_label and target_label not in ("status", "stop", "kill"):
                await self._switch_session(update, target_label)
                return

        # Bare text matching keyboard buttons (no slash)
        text_lower = text.lower()
        if " " not in text and not text.startswith("/"):
            if text_lower == "status":
                await self._handle_status(update, ctx)
                return
            if text_lower == "stop":
                await self._handle_stop_cmd(update, ctx)
                return
            if text_lower == "kill":
                await self._handle_kill_cmd(update, ctx)
                return
            if text_lower == "new":
                await self._handle_new_cmd(update, ctx)
                return
            # Check if it matches a known session label (scoped to caller)
            if self.registry.find_by_label(text, calling_chat_id(update)):
                await self._switch_session(update, text)
                return

        # Routing precedence:
        #   1. reply_to.message_id → exact match in the msg_map
        #   2. reply_to.message_id → any session whose last_msg_id matches
        #      (catches replies to messages too old for the capped map)
        #   3. parse the reply_to text for a known session label
        #      (catches replies to OLD messages from before this daemon run,
        #       e.g. busy messages sent before track_message covered them)
        #   4. last_active_session (the session the user most recently addressed)
        #   5. Error: nothing to route to → tell the user instead of silently dropping
        reply_to = update.message.reply_to_message
        sess = None
        fallback_reason = ""
        if reply_to:
            sess = self.registry.get_session_by_msg(reply_to.message_id)
            if not sess:
                for cand in self.registry.all_sessions().values():
                    if cand.last_msg_id == reply_to.message_id:
                        sess = cand
                        break
            if not sess:
                sess = self._guess_session_from_text(
                    reply_to.text or reply_to.caption or ""
                )
                if sess:
                    fallback_reason = (
                        f"reply target msg {reply_to.message_id} not tracked — "
                        f"recovered session [{sess.label}] from message text"
                    )
            if not sess:
                fallback_reason = (
                    f"reply target msg {reply_to.message_id} unknown — "
                    "routed by last_active fallback"
                )
        if not sess:
            name = self.registry.last_active_session
            sess = self.registry.get(name) if name else None

        if not sess:
            log.warning("Dropped text %r — no session to route to", text[:80])
            await update.message.reply_text(
                "⚠️ I don't know which session this is for. Pick one with "
                "/<label> or the keyboard."
            )
            return

        if fallback_reason:
            log.info("[%s] %s", sess.label, fallback_reason)

        self.registry.last_active_session = sess.name  # user is talking to this session now
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if session is busy — inject when it goes IDLE
        if sess.status == Status.BUSY:
            if not sess.queue_prompt(text, update.message.message_id):
                await update.message.reply_text(
                    f"⚠️ Queue is full ({QUEUE_CAP} pending) for "
                    f"[{html_mod.escape(sess.label)}]. Tap stop or wait "
                    "for the current task to finish.",
                    parse_mode="HTML",
                )
                return
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] Queued (busy): %s", sess.label, text[:80])
            return

        sess.trigger_msg_id = update.message.message_id
        sess.last_prompt = text
        self._mark_driver(sess, update)
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, text)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Sent text: %s", sess.label, text[:80])
        else:
            await update.message.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _handle_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle voice messages — transcribe via local Whisper, inject as prompt.

        Requires the optional ``aipager[voice]`` extra. When unavailable
        we tell the user once and bail out. On success we send back the
        transcript (so the user can verify what we heard) and inject it
        into the active session like any other text prompt.
        """
        if not await self._authorize(update):
            return
        msg = update.message
        if not msg.voice:
            return

        # Voice extra installed?
        from aipager.bot import voice
        if not voice.is_available():
            # Offer to install it remotely — user might be away from
            # their terminal. The callback handler does the actual work.
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📦 Install voice", callback_data="__voice__:install"),
                InlineKeyboardButton(
                    "Cancel", callback_data="__voice__:cancel"),
            ]])
            await msg.reply_text(
                "⚠️ Voice messages need the optional voice extra "
                "(~200 MB install · ~74 MB model on first use).",
                reply_markup=keyboard,
            )
            return

        # Pre-size check (Telegram bot file-download limit, ~20 MB).
        size = msg.voice.file_size or 0
        if size > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES:
            mb = size / (1024 * 1024)
            limit_mb = TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES // (1024 * 1024)
            await msg.reply_text(
                f"⚠️ Voice message is {mb:.1f} MB; Telegram bots are capped at {limit_mb} MB.",
            )
            return

        # Download the .ogg file
        try:
            FILE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            save_path = FILE_DOWNLOAD_DIR / f"{ts}_voice.ogg"
            tg_file = await msg.voice.get_file()
            await tg_file.download_to_drive(custom_path=str(save_path))
        except Exception:
            log.warning("Voice download failed", exc_info=True)
            await msg.reply_text("❌ Failed to download voice message.")
            return

        # Acknowledge so the user knows we're working
        ack_msg = await msg.reply_text(
            "🎙️ <i>Transcribing…</i>", parse_mode="HTML",
        )

        try:
            text = await voice.transcribe(str(save_path))
        except voice.VoiceUnavailable as e:
            await ack_msg.edit_text(f"⚠️ {e}")
            return
        except Exception as e:
            log.warning("transcription failed", exc_info=True)
            await ack_msg.edit_text(f"❌ Transcription failed: {e}")
            return
        finally:
            # Clean up the audio file once we've transcribed it
            save_path.unlink(missing_ok=True)

        if not text:
            await ack_msg.edit_text("⚠️ Couldn't make out any speech in that.")
            return

        # Show the transcript to the user
        await ack_msg.edit_text(
            f"🎙️ <i>Heard:</i> {html_mod.escape(text)}",
            parse_mode="HTML",
        )

        # Inject the transcript as if it were a regular text reply.
        # Route to the same session that bare text would (last_active or
        # reply target), reusing _handle_message's logic. Build a
        # lightweight shim Update so we don't duplicate routing code.
        # Simpler: write the text into the existing message and dispatch.
        # Cleanest: invoke the same logic inline.
        await self._dispatch_voice_transcript(update, text)

    async def _dispatch_voice_transcript(
        self, update: Update, transcript: str,
    ) -> None:
        """Inject a voice-transcript into the active session as a prompt.

        Mirrors the routing precedence of ``_handle_message`` (reply
        target → last_active_session) so the user's voice behaves like
        their typed text would.
        """
        reply_to = update.message.reply_to_message
        sess = None
        if reply_to:
            sess = self.registry.get_session_by_msg(reply_to.message_id)
            if not sess:
                for cand in self.registry.all_sessions().values():
                    if cand.last_msg_id == reply_to.message_id:
                        sess = cand
                        break
            if not sess:
                sess = self._guess_session_from_text(
                    reply_to.text or reply_to.caption or ""
                )
        if not sess:
            name = self.registry.last_active_session
            sess = self.registry.get(name) if name else None
        if not sess:
            await update.message.reply_text(
                "⚠️ Voice transcribed but no active session to send it to. "
                "Pick one with /<label> first."
            )
            return

        self.registry.last_active_session = sess.name
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if busy
        if sess.status == Status.BUSY:
            if not sess.queue_prompt(transcript, update.message.message_id):
                await update.message.reply_text(
                    f"⚠️ Queue full for [{html_mod.escape(sess.label)}].",
                    parse_mode="HTML",
                )
                return
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] Voice queued (busy): %r", sess.label, transcript[:80])
            return

        sess.trigger_msg_id = update.message.message_id
        sess.last_prompt = transcript
        self._mark_driver(sess, update)
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, transcript)
        if ok:
            await self._react(update, "🎙️")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Voice injected: %r", sess.label, transcript[:80])
        else:
            await update.message.reply_text(
                f"❌ Failed to inject transcript into [{sess.label}]",
            )

    async def _handle_file(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photo/document messages — download file and inject prompt."""
        if not await self._authorize(update):
            return
        msg = update.message

        # Up-front size check — Telegram's bot API tops out at 20 MB on
        # download, so we'd rather reject with a clear message than try
        # and fail with a vague "Failed to download" later.
        file_size = None
        if msg.document:
            file_size = msg.document.file_size
        elif msg.photo:
            # Largest size is last; treat its file_size as the cap
            file_size = msg.photo[-1].file_size if msg.photo[-1] else None
        if file_size and file_size > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES:
            mb = file_size / (1024 * 1024)
            limit_mb = TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES // (1024 * 1024)
            await msg.reply_text(
                f"⚠️ File is {mb:.1f} MB. The Telegram bot API caps file "
                f"downloads at {limit_mb} MB. Try splitting it, or paste the "
                "content as text.",
            )
            return

        # Download the file
        try:
            FILE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())

            if msg.photo:
                # Use largest photo size
                tg_file = await msg.photo[-1].get_file()
                filename = f"{ts}_photo.jpg"
            elif msg.document:
                tg_file = await msg.document.get_file()
                raw_name = msg.document.file_name or "document"
                orig = Path(raw_name).name or "document"
                filename = f"{ts}_{orig}"
            else:
                return

            save_path = FILE_DOWNLOAD_DIR / filename
            await tg_file.download_to_drive(custom_path=str(save_path))
        except Exception:
            log.warning("File download failed", exc_info=True)
            await msg.reply_text("❌ Failed to download file")
            return

        # Construct prompt — keep it clean, let the file path do the work
        caption = msg.caption or ""
        if caption:
            prompt = f"{caption} {save_path}"
        elif msg.photo:
            prompt = f"Describe this image: {save_path}"
        else:
            prompt = f"Read and analyze this file: {save_path}"

        # Resolve target session (same routing precedence as _handle_message)
        reply_to = msg.reply_to_message
        sess = None
        if reply_to:
            sess = self.registry.get_session_by_msg(reply_to.message_id)
            if not sess:
                for cand in self.registry.all_sessions().values():
                    if cand.last_msg_id == reply_to.message_id:
                        sess = cand
                        break
            if not sess:
                sess = self._guess_session_from_text(
                    reply_to.text or reply_to.caption or ""
                )
        if not sess:
            name = self.registry.last_active_session
            sess = self.registry.get(name) if name else None

        if not sess:
            await msg.reply_text(
                "⚠️ I don't know which session this file is for. Pick one with "
                "/<label> or the keyboard."
            )
            return

        self.registry.last_active_session = sess.name
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await msg.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if busy
        if sess.status == Status.BUSY:
            if not sess.queue_prompt(prompt, msg.message_id):
                await msg.reply_text(
                    f"⚠️ Queue is full ({QUEUE_CAP} pending) for "
                    f"[{html_mod.escape(sess.label)}]. File not queued.",
                    parse_mode="HTML",
                )
                return
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] File queued (busy): %s", sess.label, filename)
            return

        sess.trigger_msg_id = msg.message_id
        sess.last_prompt = prompt
        self._mark_driver(sess, update)
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, prompt)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] File sent: %s", sess.label, filename)
        else:
            await msg.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _send_template(self, update: Update, prompt_text: str) -> None:
        """Inject a quick-template prompt into the last active session."""
        name = self.registry.last_active_session
        sess = self.registry.get(name) if name else None

        if not sess or sess.status == Status.GONE:
            await update.message.reply_text("⚠️ No active session")
            return

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # Queue if session is busy
        if sess.status == Status.BUSY:
            if not sess.queue_prompt(prompt_text, update.message.message_id):
                await update.message.reply_text(
                    f"⚠️ Queue is full ({QUEUE_CAP} pending) for "
                    f"[{html_mod.escape(sess.label)}]. Tap stop or wait "
                    "for the current task to finish.",
                    parse_mode="HTML",
                )
                return
            self.registry.mark_dirty()
            await self._react(update, "👀")
            log.info("[%s] Template queued (busy): %s", sess.label, prompt_text[:80])
            return

        sess.trigger_msg_id = update.message.message_id
        sess.last_prompt = prompt_text
        self.registry.mark_dirty()
        ok = await inject.send_text_and_enter(sess.name, prompt_text)
        if ok:
            await self._react(update, "👀")
            self.registry.transition(sess.name, Status.BUSY)
            await self._send_busy_and_animate(sess)
            log.info("[%s] Template sent: %s", sess.label, prompt_text[:80])
        else:
            await update.message.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _send_command(self, update: Update, command_text: str) -> None:
        """Inject an instant Claude Code slash command (no BUSY transition).

        Unlike _send_template(), this does NOT transition to BUSY or start
        animation. Slash commands like /model, /cost, /context complete
        instantly and produce no Claude response.
        """
        name = self.registry.last_active_session
        sess = self.registry.get(name) if name else None

        if not sess or sess.status == Status.GONE:
            await update.message.reply_text("⚠️ No active session")
            return

        if not await inject.is_alive(sess.name):
            await update.message.reply_text(f"⚠️ Session '{sess.name}' not found")
            return

        # /clear is destructive — refuse while session is actively working
        if command_text == "/clear" and sess.status == Status.BUSY:
            await update.message.reply_text("⚠️ Can't clear while session is busy")
            return

        ok = await inject.send_text_and_enter(sess.name, command_text)
        if ok:
            await self._react(update, "✅")
            # Explicit feedback for model changes
            if command_text.startswith("/model "):
                model_arg = command_text.split(" ", 1)[1]
                await update.message.reply_text(
                    f"🔄 <b>{html_mod.escape(sess.label)}</b> → {html_mod.escape(model_arg)}",
                    parse_mode="HTML",
                )
            log.info("[%s] Command sent: %s", sess.label, command_text)
        else:
            await update.message.reply_text(f"❌ Failed to send to [{sess.label}]")

    async def _direct_send(self, update: Update, target_label: str, prompt_text: str) -> None:
        """Send prompt directly to a session by label."""
        sess = self.registry.find_by_label(
            target_label, calling_chat_id(update), include_gone=True)
        if sess is not None:
            name = sess.name
            if not await inject.is_alive(name):
                await update.message.reply_text(f"⚠️ [{target_label}] session not alive")
                return
            sess.trigger_msg_id = update.message.message_id
            sess.last_prompt = prompt_text
            self.registry.mark_dirty()
            self.registry.last_active_session = name  # user explicitly targeted this session
            asyncio.create_task(self._maybe_update_bot_name(name))
            ok = await inject.send_text_and_enter(name, prompt_text)
            if ok:
                await self._react(update, "👀")
                self.registry.transition(name, Status.BUSY)
                await self._send_busy_and_animate(sess)
                log.info("[%s] Direct send: %s", target_label, prompt_text[:80])
            else:
                await update.message.reply_text(f"❌ Failed to send to [{target_label}]")
            return

        # Not found in registry — try session discovery
        session_name = f"claude-{target_label}"
        if await inject.is_alive(session_name):
            new_sess = self.registry.get_or_create(session_name)
            new_sess.trigger_msg_id = update.message.message_id
            new_sess.last_prompt = prompt_text
            self.registry.mark_dirty()
            self.registry.last_active_session = session_name
            asyncio.create_task(self._maybe_update_bot_name(session_name))
            ok = await inject.send_text_and_enter(session_name, prompt_text)
            if ok:
                await self._react(update, "👀")
                self.registry.transition(session_name, Status.BUSY)
                await self._send_busy_and_animate(new_sess)
            else:
                await update.message.reply_text(f"❌ Failed to send to [{target_label}]")
        else:
            await update.message.reply_text(f"⚠️ Unknown session: {target_label}")
