"""Asyncio Unix datagram socket listener — receives hook events from notify_hook.py.

Parses JSON datagrams, maps notification_type to state transitions,
and triggers Telegram notifications when state actually changes.

Handles both INTERACTIVE (permission prompts) and IDLE (task complete)
notifications. IDLE uses transcript-based rich summaries when available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path

from aipager.config import RICH_SUMMARIES, SOCKET_PATH
from aipager.md_to_tg import markdown_to_telegram_html
from aipager.state import SessionRegistry, Status
from aipager.transcript import extract_last_response, find_transcript

log = logging.getLogger(__name__)


_CTX_WINDOW_SIZE = 200_000  # all current Claude models use 200k context


def _read_statusline(session_name: str) -> dict | None:
    """Read real-time token data from the statusLine JSON file.

    Claude Code's statusLine hook pipes JSON to its command on every update.
    We modified the command to also write this JSON to a per-session file.
    This gives us accurate cumulative token counts — same source the terminal uses.
    """
    status_file = Path(f"/tmp/claude-status-{session_name}.json")
    try:
        data = json.loads(status_file.read_text())
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None

    ctx = data.get("context_window", {})
    total_in = ctx.get("total_input_tokens", 0)
    total_out = ctx.get("total_output_tokens", 0)
    pct = ctx.get("used_percentage")
    remaining = ctx.get("remaining_percentage")

    # Compute context_pct from whatever's available
    if pct is not None:
        context_pct = round(pct)
    elif remaining is not None:
        context_pct = round(100 - remaining)
    else:
        context_pct = 0

    return {
        "context_pct": context_pct,
        "total_input": total_in,
        "total_output": total_out,
        "total_tokens": total_in + total_out,
    }


def _extract_token_usage(transcript_path: str) -> dict | None:
    """Fallback: read context usage from transcript JSONL.

    Only used when statusLine file isn't available. Output tokens from
    the transcript are unreliable (placeholder values in many versions).
    """
    try:
        lines = Path(transcript_path).read_text().strip().splitlines()
    except (FileNotFoundError, PermissionError):
        return None

    for line in reversed(lines[-20:]):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        usage = entry.get("message", {}).get("usage")
        if usage:
            inp = usage.get("input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_create = usage.get("cache_creation_input_tokens", 0)
            total_ctx = inp + cache_read + cache_create
            pct = round(total_ctx / _CTX_WINDOW_SIZE * 100) if total_ctx else 0
            return {"context_pct": pct, "total_input": total_ctx,
                    "total_output": 0, "total_tokens": total_ctx}
    return None


def _extract_pending_tool(transcript_path: str) -> dict | None:
    """Read the last lines of the transcript to find the pending tool_use."""
    try:
        lines = Path(transcript_path).read_text().strip().splitlines()
    except (FileNotFoundError, PermissionError):
        return None

    for line in reversed(lines[-10:]):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content", [])
        for block in reversed(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            inp = block.get("input", {})
            return {"name": name, "input": inp, "summary": _summarize_tool(name, inp)}
    return None


def _extract_specific_tool(transcript_path: str, target_name: str) -> dict | None:
    """Search transcript for a specific tool_use by name (handles parallel tools)."""
    try:
        lines = Path(transcript_path).read_text().strip().splitlines()
    except (FileNotFoundError, PermissionError):
        return None

    for line in reversed(lines[-20:]):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content", [])
        for block in reversed(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") == target_name:
                inp = block.get("input", {})
                return {"name": target_name, "input": inp,
                        "summary": _summarize_tool(target_name, inp)}
    return None


def _summarize_tool(name: str, inp: dict) -> str:
    if name == "Bash":
        return f"Bash: {inp.get('description') or inp.get('command', '')[:80]}"
    if name == "AskUserQuestion":
        questions = inp.get("questions", [])
        return questions[0].get("question", "")[:120] if questions else "AskUserQuestion"
    if name in ("Read", "Write", "Edit"):
        return f"{name}: {inp.get('file_path', '')}"
    if name == "Task":
        return f"Task: {inp.get('description', inp.get('prompt', '')[:80])}"
    if name == "Glob":
        pattern = inp.get("pattern", "")
        path = inp.get("path", "")
        return f"Glob: {pattern}" if not path else f"Glob: {pattern} in {path}"
    if name == "Grep":
        pattern = inp.get("pattern", "")
        path = inp.get("path", "")
        return f"Grep: {pattern}" if not path else f"Grep: {pattern} in {path}"
    if name == "WebFetch":
        return f"WebFetch: {inp.get('url', '')[:80]}"
    if name == "WebSearch":
        return f"WebSearch: {inp.get('query', '')[:80]}"
    if name == "NotebookEdit":
        return f"NotebookEdit: {inp.get('notebook_path', '')}"
    return name


class HookReceiver:
    """Receives UDP datagrams from notify_hook.py and drives state transitions.

    Handles permission_prompt → INTERACTIVE and idle events → IDLE with
    transcript-based rich summaries.
    """

    def __init__(self, registry: SessionRegistry, notify_fn):
        self.registry = registry
        self.notify_fn = notify_fn

    async def start(self) -> None:
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self._on_datagram),
            local_addr=SOCKET_PATH,
            family=socket.AF_UNIX,
        )
        os.chmod(SOCKET_PATH, 0o666)
        self._transport = transport
        log.info("Hook receiver listening on %s", SOCKET_PATH)

    async def _on_datagram(self, data: bytes) -> None:
        try:
            msg = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        event = msg.get("notification_type") or msg.get("hook_event_name") or msg.get("type", "")
        session_name = msg.get("session", "")
        transcript_path = msg.get("transcript_path", "")

        if not session_name or not event:
            return

        # Update last activity timestamp (stale detection) + store transcript path
        sess_ref = self.registry.get_or_create(session_name)
        sess_ref.last_hook_at = time.monotonic()
        if transcript_path:
            sess_ref.transcript_path = transcript_path
            # The transcript filename IS Claude Code's session id —
            # exactly what `claude --resume <id>` consumes. Cheap to
            # derive, costs ~80 bytes on disk, makes /resume robust
            # against later transcript-path moves.
            stem = Path(transcript_path).stem
            if stem and stem != sess_ref.claude_session_id:
                sess_ref.claude_session_id = stem
            self.registry.mark_dirty()
            log.debug("[%s] Stored transcript_path: %s", session_name, transcript_path)
        # SessionStart payload also carries the session's cwd. Claude
        # organizes transcripts by encoded-cwd, so we need it later to
        # launch `claude --resume` from the right place.
        cwd = msg.get("cwd", "")
        if cwd and cwd != sess_ref.cwd:
            sess_ref.cwd = cwd
            self.registry.mark_dirty()

        log.debug("Hook event: %s from %s", event, session_name)

        if event == "PermissionRequest":
            # Primary path: structured tool data directly from hook payload
            tool_name = msg.get("tool_name", "")
            tool_input = msg.get("tool_input", {})
            if tool_name:
                tool_info = {"name": tool_name, "input": tool_input,
                             "summary": _summarize_tool(tool_name, tool_input)}
                context = {"tool_info": tool_info, "transcript_path": transcript_path}
                sess = self.registry.transition(session_name, Status.INTERACTIVE)
                if sess:
                    log.info("[%s] PermissionRequest: %s", sess.label, tool_info["summary"])
                    await self.notify_fn(sess, "permission_prompt", context)
            return

        elif event == "permission_prompt":
            # Fallback: Notification hook — only fires if PermissionRequest
            # didn't already transition to INTERACTIVE (state machine dedup)
            tool_info = _extract_pending_tool(transcript_path) if transcript_path else None
            hook_message = msg.get("message", "")

            hook_tool_name = ""
            _prefix = "permission to use "
            if _prefix in hook_message:
                hook_tool_name = hook_message.split(_prefix, 1)[1].strip()

            if hook_tool_name and (not tool_info or tool_info["name"] != hook_tool_name):
                log.info("[%s] Fallback tool mismatch: transcript=%s, hook=%s",
                         session_name, tool_info["name"] if tool_info else "None",
                         hook_tool_name)
                targeted = (_extract_specific_tool(transcript_path, hook_tool_name)
                            if transcript_path else None)
                if targeted:
                    tool_info = targeted
                else:
                    tool_info = {"name": hook_tool_name, "input": {},
                                 "summary": hook_tool_name}

            new_status = Status.INTERACTIVE
            context = {"tool_info": tool_info, "transcript_path": transcript_path}

            sess = self.registry.transition(session_name, new_status)
            if sess:
                await self.notify_fn(sess, event, context)

        elif event == "UserPromptSubmit":
            transitioned = self.registry.transition(session_name, Status.BUSY)
            # Origin tagging (Phase D): the daemon prefixes Telegram prompts
            # with "[via Telegram …]" on line 1. A markerless prompt was
            # typed into the terminal directly. Empty payload → leave
            # unchanged (the fail-closed "telegram" default holds).
            tag_sess = transitioned or self.registry.get(session_name)
            if tag_sess is not None:
                prompt = msg.get("prompt", "")
                first = prompt.split("\n", 1)[0] if prompt else ""
                if first.startswith("[via Telegram"):
                    tag_sess.last_prompt_origin = "telegram"
                elif prompt:
                    tag_sess.last_prompt_origin = "terminal"
            if transitioned:
                await self.notify_fn(transitioned, "user_prompt_submit", {})

        elif event == "PreToolUse":
            tool_name = msg.get("tool_name", "")
            tool_input = msg.get("tool_input", {})

            # AskUserQuestion blocks execution — handle as INTERACTIVE
            # (detected here because PreToolUse provides full tool_input
            #  with questions/options, unlike the Notification hook)
            if tool_name == "AskUserQuestion":
                tool_info = {"name": "AskUserQuestion", "input": tool_input,
                             "summary": _summarize_tool("AskUserQuestion", tool_input)}
                sess_aq = self.registry.transition(session_name, Status.INTERACTIVE)
                if sess_aq:
                    await self.notify_fn(sess_aq, "permission_prompt", {
                        "tool_info": tool_info,
                        "transcript_path": transcript_path,
                    })
                return

            if tool_name:
                summary = _summarize_tool(tool_name, tool_input)
                sess = self.registry.get_or_create(session_name)
                # Ensure we're in BUSY state
                if sess.status != Status.BUSY:
                    self.registry.transition(session_name, Status.BUSY)
                # Item 4.4: forward the raw tool_input for Write/Edit so
                # the bot can render a diff. We don't forward EVERY
                # tool_input because they can be huge (Read content,
                # WebFetch results, etc.).
                forward_input = (
                    tool_input if tool_name in ("Write", "Edit") else None
                )
                # Token data piggybacked from statusLine file (read by notify_hook.py)
                sl_tokens = msg.get("sl_tokens")
                if sl_tokens:
                    sess.last_token_pct = sl_tokens.get("context_pct", 0)
                    total_out = sl_tokens.get("total_output", 0)
                    if sess.output_baseline is None:
                        sess.output_baseline = total_out
                    elif total_out < sess.output_baseline:
                        sess.output_baseline = total_out
                    sess.last_output_tokens = max(0, total_out - sess.output_baseline)
                    # Lines baselines from PreToolUse (before edits happen)
                    if sess.lines_added_baseline is None:
                        sess.lines_added_baseline = sl_tokens.get("lines_added", 0)
                        sess.lines_removed_baseline = sl_tokens.get("lines_removed", 0)
                    log.debug("[%s] PreToolUse tokens: %d%% ctx, ↓%d",
                              sess.label, sess.last_token_pct, sess.last_output_tokens)
                await self.notify_fn(sess, "tool_use", {
                    "tool_name": tool_name,
                    "tool_summary": summary,
                    "tool_input_full": forward_input,
                })

        elif event == "PostToolUse":
            tool_name = msg.get("tool_name", "")
            if tool_name:
                summary = _summarize_tool(tool_name, msg.get("tool_input", {}))
                sess = self.registry.get_or_create(session_name)
                await self.notify_fn(sess, "tool_done", {
                    "tool_name": tool_name,
                    "tool_summary": summary,
                })

        elif event == "PostToolUseFailure":
            tool_name = msg.get("tool_name", "")
            if tool_name:
                summary = _summarize_tool(tool_name, msg.get("tool_input", {}))
                sess = self.registry.get_or_create(session_name)
                await self.notify_fn(sess, "tool_failed", {
                    "tool_name": tool_name,
                    "tool_summary": summary,
                })

        elif event == "SubagentStart":
            agent_id = msg.get("agent_id", "")
            agent_type = msg.get("agent_type", "unknown")
            if agent_id:
                sess = self.registry.get_or_create(session_name)
                sess.active_subagents[agent_id] = {
                    "type": agent_type,
                    "started_at": time.monotonic(),
                    "history_idx": None,  # set by telegram_bot notify
                }
                log.info("[%s] SubagentStart: %s (%s)", sess.label, agent_type, agent_id)
                await self.notify_fn(sess, "subagent_start", {
                    "agent_id": agent_id,
                    "agent_type": agent_type,
                })

        elif event == "SubagentStop":
            agent_id = msg.get("agent_id", "")
            agent_type = msg.get("agent_type", "unknown")
            if agent_id:
                sess = self.registry.get_or_create(session_name)
                # Compute elapsed time if we have a matching start
                elapsed = 0.0
                info = sess.active_subagents.pop(agent_id, None)
                if info:
                    elapsed = time.monotonic() - info["started_at"]
                log.info("[%s] SubagentStop: %s (%s, %.1fs)", sess.label, agent_type, agent_id, elapsed)
                await self.notify_fn(sess, "subagent_stop", {
                    "agent_id": agent_id,
                    "agent_type": agent_type,
                    "elapsed": elapsed,
                    "history_idx": info["history_idx"] if info else None,
                })

        elif event == "safety_blocked":
            # The PreToolUse hook denied a Telegram-driven tool call.
            # Surface it in the session's chat + audit it.
            sess = self.registry.get(session_name)
            if sess is not None:
                await self.notify_fn(sess, "safety_blocked", {
                    "tool": msg.get("tool", "?"),
                    "reason": msg.get("reason", ""),
                })

        elif event == "SessionEnd":
            sess = self.registry.get_or_create(session_name)
            source = msg.get("source", "unknown")
            self.registry.transition(session_name, Status.GONE)
            sess.last_prompt_origin = "telegram"  # fail-closed between turns
            await self.notify_fn(sess, "session_end", {"source": source})

        elif event == "PreCompact":
            # Compaction is about to start — save context % for delta display
            sess = self.registry.get_or_create(session_name)
            trigger = msg.get("trigger", "auto")
            # Save pre-compact context % (try cached, then sl_tokens, then file)
            pre_pct = sess.last_token_pct
            if not pre_pct:
                sl_tokens = msg.get("sl_tokens")
                if sl_tokens:
                    pre_pct = sl_tokens.get("context_pct", 0)
            if not pre_pct:
                sl = _read_statusline(session_name)
                if sl:
                    pre_pct = sl.get("context_pct", 0)
            sess.pre_compact_pct = pre_pct
            log.info("[%s] PreCompact (trigger=%s, pre_pct=%d%%)",
                     sess.label, trigger, pre_pct)
            await self.notify_fn(sess, "compacting", {"trigger": trigger})
            return

        elif event == "SessionStart":
            # SessionStart with source=compact fires after compaction completes
            source = msg.get("source", "")
            if source != "compact":
                self.registry.get_or_create(session_name)
                return
            sess = self.registry.get_or_create(session_name)
            # Read post-compact context % from piggybacked sl_tokens or file
            sl_tokens = msg.get("sl_tokens")
            post_pct = 0
            if sl_tokens:
                post_pct = sl_tokens.get("context_pct", 0)
            if not post_pct:
                sl = _read_statusline(session_name)
                if sl:
                    post_pct = sl.get("context_pct", 0)
            # Reset pre_compact_pct SYNCHRONOUSLY before await (race prevention)
            before_pct = sess.pre_compact_pct
            sess.pre_compact_pct = 0
            if before_pct > 0:
                # If post_pct is still high (stale file), defer to statusLine fallback
                if post_pct >= before_pct:
                    sess.pre_compact_pct = before_pct
                    log.info("[%s] SessionStart compact: post_pct=%d%% >= before=%d%%, deferring",
                             sess.label, post_pct, before_pct)
                else:
                    sess.compact_warned = False
                    log.info("[%s] Compacted: %d%% → %d%%", sess.label, before_pct, post_pct)
                    await self.notify_fn(sess, "compact_done", {
                        "before_pct": before_pct,
                        "after_pct": post_pct,
                    })
            return

        elif event == "statusline":
            # Real-time token data from statusLine hook (fires after each response)
            sess = self.registry.get_or_create(session_name)
            # The statusLine JSON occasionally has explicit null values for
            # context_pct / total_output during early ticks (before claude
            # has rendered tokens). ``dict.get(key, 0)`` only uses the default
            # when the key is missing, so an explicit ``null`` falls through
            # and crashes ``round(None)`` / arithmetic. Coerce here.
            ctx_pct = int(round(msg.get("context_pct") or 0))
            total_out = msg.get("total_output") or 0
            sess.last_token_pct = ctx_pct
            model = msg.get("model_name", "")
            if model and model != sess.model_name:
                sess.model_name = model
                await self.notify_fn(sess, "pinned_update", {})
            # Lazy baseline: set on first statusline event this BUSY cycle
            if sess.output_baseline is None:
                sess.output_baseline = total_out
            elif total_out < sess.output_baseline:
                sess.output_baseline = total_out  # session restarted
            sess.last_output_tokens = max(0, total_out - sess.output_baseline)
            # Cost (item 4.6): cumulative session-level cost from claude's
            # statusLine. Same null-coalesce guard as ctx_pct.
            cost_usd = float(msg.get("cost_usd") or 0)
            sess.last_cost_usd = cost_usd
            if sess.cost_baseline is None:
                sess.cost_baseline = cost_usd
            elif cost_usd < sess.cost_baseline:
                sess.cost_baseline = cost_usd  # session restarted
            # Lines changed (lazy baseline, same pattern). Same null-coalesce
            # guard as ctx_pct above.
            lines_add = msg.get("lines_added") or 0
            lines_rm = msg.get("lines_removed") or 0
            if sess.lines_added_baseline is None:
                sess.lines_added_baseline = lines_add
                sess.lines_removed_baseline = lines_rm
            sess.last_lines_added = max(0, lines_add - (sess.lines_added_baseline or 0))
            sess.last_lines_removed = max(0, lines_rm - (sess.lines_removed_baseline or 0))
            # Compact-done fallback: if SessionStart hook didn't fire (or had stale data),
            # detect compaction completion from the first low statusLine reading
            if ctx_pct < 30 and sess.pre_compact_pct > 0:
                before_pct = sess.pre_compact_pct
                sess.pre_compact_pct = 0  # reset SYNCHRONOUSLY before await
                sess.compact_warned = False
                log.info("[%s] Compacted (statusLine fallback): %d%% → %d%%",
                         sess.label, before_pct, ctx_pct)
                await self.notify_fn(sess, "compact_done", {
                    "before_pct": before_pct,
                    "after_pct": ctx_pct,
                })
            # Context warning: alert user once when approaching auto-compact
            elif ctx_pct >= 80 and not sess.compact_warned:
                sess.compact_warned = True
                await self.notify_fn(sess, "context_warning", {"context_pct": ctx_pct})
            elif ctx_pct < 30:
                # Reset after compaction (context drops to ~2-5%)
                sess.compact_warned = False
            log.debug("[%s] statusline: %d%% ctx, ↓%d out (base=%d, total=%d)",
                      sess.label, ctx_pct, sess.last_output_tokens,
                      sess.output_baseline or 0, total_out)
            return  # no further notification — animation reads cached values

        elif event.lower() in ("idle_prompt", "idle", "stop", "notification"):
            # Turn finished — reset origin fail-closed (Phase D §3.7a) so the
            # window before the next prompt is treated as restricted.
            _idle = self.registry.get(session_name)
            if _idle is not None:
                _idle.last_prompt_origin = "telegram"
            sess = self.registry.transition(session_name, Status.IDLE)
            if sess is None:
                # Force notification if there's an undelivered response
                # (user sent a prompt via Telegram but debounce suppressed IDLE)
                tracked = self.registry.get(session_name)
                if (tracked and tracked.trigger_msg_id
                        and msg.get("last_assistant_message")):
                    sess = tracked  # bypass debounce — user is waiting
                else:
                    return

            notify_ctx: dict = {"summary": ""}

            # Primary: last_assistant_message from hook JSON (always current)
            last_msg = msg.get("last_assistant_message", "")

            if last_msg and RICH_SUMMARIES and "```" in last_msg:
                # Rich HTML formatting for code-heavy responses
                try:
                    html_summary = markdown_to_telegram_html(last_msg)
                    notify_ctx = {
                        "summary": html_summary,
                        "html_summary": True,
                        "raw_md": last_msg,
                    }
                    log.info("[%s] Rich summary from hook (%d chars)", session_name, len(html_summary))
                except Exception:
                    notify_ctx = {"summary": last_msg}
            elif last_msg:
                notify_ctx = {"summary": last_msg}
            else:
                # Fallback: transcript (for hooks that don't include last_assistant_message)
                tracked = self.registry.get(session_name)
                tp = transcript_path or (tracked.transcript_path if tracked else "")
                if not tp and RICH_SUMMARIES:
                    tp = find_transcript(session_name)
                if tp:
                    try:
                        md = extract_last_response(tp)
                        if md and RICH_SUMMARIES and "```" in md:
                            html_summary = markdown_to_telegram_html(md)
                            notify_ctx = {
                                "summary": html_summary,
                                "html_summary": True,
                                "raw_md": md,
                            }
                        elif md:
                            notify_ctx = {"summary": md}
                    except Exception:
                        log.info("[%s] Transcript summary failed", session_name)

            await self.notify_fn(sess, "idle_prompt", notify_ctx)

        else:
            # auth_success, etc. — just ensure session is tracked
            self.registry.get_or_create(session_name)

    def stop(self) -> None:
        if hasattr(self, "_transport"):
            self._transport.close()
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, handler):
        self._handler = handler

    def datagram_received(self, data: bytes, addr) -> None:
        asyncio.ensure_future(self._handler(data))
