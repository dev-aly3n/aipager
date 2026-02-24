#!/usr/bin/env python3
"""Notification hook for Claude Code — sends Telegram messages with inline keyboards.

Replaces .claude/hooks/notify_telegram.sh
Reads JSON from stdin (Claude Code hook protocol), sends appropriate message.

Hook JSON format:
{
    "session_id": "uuid",
    "transcript_path": "/path/to/transcript.jsonl",
    "cwd": "/path/to/project",
    "hook_event_name": "Notification",
    "message": "Claude Code needs your approval...",
    "notification_type": "permission_prompt"
}
"""

import json
import os
import sys
from pathlib import Path

# Add parent dir to path so we can import aipager
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aipager import telegram_api as tg
from aipager import session_mgr as sm


def _extract_pending_tool(transcript_path: str) -> dict | None:
    """Read the last lines of the transcript to find the pending tool_use.

    Returns dict with:
        name: tool name (e.g., "Bash", "AskUserQuestion")
        input: tool input dict
        summary: human-readable one-liner
    """
    try:
        lines = Path(transcript_path).read_text().strip().splitlines()
    except (FileNotFoundError, PermissionError):
        return None

    # Scan from the end for the last assistant message with a tool_use
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
            return {
                "name": name,
                "input": inp,
                "summary": _summarize_tool(name, inp),
            }
    return None


def _summarize_tool(name: str, inp: dict) -> str:
    """One-line summary of a tool call."""
    if name == "Bash":
        cmd = inp.get("command", "")
        desc = inp.get("description", "")
        return f"Bash: {desc or cmd[:80]}"
    elif name == "WebSearch":
        return f"WebSearch: {inp.get('query', '')[:80]}"
    elif name == "WebFetch":
        return f"WebFetch: {inp.get('url', '')[:80]}"
    elif name in ("Read", "Write", "Edit"):
        return f"{name}: {inp.get('file_path', '')}"
    elif name == "AskUserQuestion":
        questions = inp.get("questions", [])
        if questions:
            return questions[0].get("question", "")[:120]
        return "AskUserQuestion"
    elif name == "Task":
        return f"Task: {inp.get('description', inp.get('prompt', '')[:80])}"
    else:
        return name


def _build_ask_keyboard(sid: str, tool_input: dict) -> tuple[str, dict | None]:
    """Build message text and keyboard for AskUserQuestion prompts.

    Returns (text, keyboard_dict).
    Buttons use callback_data: "{sid}:opt{index}" where index is 0-based.
    """
    questions = tool_input.get("questions", [])
    if not questions:
        return "AskUserQuestion (no questions)", None

    q = questions[0]
    question = q.get("question", "?")
    options = q.get("options", [])

    text = f"❓ {question}"

    if not options:
        return text, None

    # Show option descriptions in the message
    for i, opt in enumerate(options):
        label = opt.get("label", f"Option {i+1}")
        desc = opt.get("description", "")
        text += f"\n  {i+1}. {label}"
        if desc:
            text += f" — {desc[:60]}"

    # Build inline buttons (one per option, max 4)
    buttons = []
    for i, opt in enumerate(options[:4]):
        label = opt.get("label", f"Option {i+1}")
        buttons.append({
            "text": label,
            "callback_data": f"{sid}:opt{i}",
        })

    keyboard = {"inline_keyboard": [buttons]}
    return text, keyboard


def _detect_tmux_session() -> str | None:
    """Try to find which tmux session this Claude process is running in."""
    tmux_env = os.environ.get("TMUX", "")
    if tmux_env:
        import subprocess
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#{session_name}"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
    return None


def _label_from_tmux(tmux_session: str | None) -> str:
    """Extract label from tmux session name. 'claude-dev' → 'dev'."""
    if not tmux_session:
        return "claude"
    if tmux_session.startswith("claude-"):
        return tmux_session[7:]
    return tmux_session


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    event = data.get("notification_type", data.get("type", "unknown"))
    session_id = data.get("session_id", "unknown")
    transcript_path = data.get("transcript_path", "")

    # Detect tmux session (if running inside one)
    tmux_session = _detect_tmux_session()
    label = _label_from_tmux(tmux_session)

    # Extract what Claude is actually trying to do
    tool_info = _extract_pending_tool(transcript_path) if transcript_path else None
    tool_name = tool_info["name"] if tool_info else ""
    tool_summary = tool_info["summary"] if tool_info else ""

    # Register session
    sid = sm.register_session(
        session_id=session_id,
        tmux_session=tmux_session or "unknown",
        label=label,
    )

    # Build message and keyboard based on event type + pending tool
    if event == "permission_prompt":
        if tool_name == "AskUserQuestion" and tmux_session:
            # Custom question — show actual options as buttons
            text, keyboard = _build_ask_keyboard(sid, tool_info["input"])
            text = f"[{label}] {text}"
        else:
            # Regular tool permission — Allow/Deny
            text = f"🔐 [{label}] Permission needed"
            if tool_summary:
                text += f"\n{tool_summary}"
            if tmux_session:
                keyboard = {
                    "inline_keyboard": [[
                        {"text": "✅ Allow", "callback_data": f"{sid}:allow"},
                        {"text": "❌ Deny", "callback_data": f"{sid}:deny"},
                    ]]
                }
            else:
                keyboard = None
                text += "\n\n⚠️ Not in tmux — use terminal to respond"

    elif event in ("idle_prompt", "idle"):
        text = f"✅ [{label}] Finished — waiting for input"
        if tmux_session:
            keyboard = {
                "inline_keyboard": [[
                    {"text": "▶️ Continue", "callback_data": f"{sid}:continue"},
                    {"text": "⏹ Stop", "callback_data": f"{sid}:stop"},
                ]]
            }
        else:
            keyboard = None
            text += "\n\n⚠️ Not in tmux — use terminal to respond"

    elif event == "auth_success":
        text = f"🔑 [{label}] Authenticated"
        keyboard = None

    else:
        sys.exit(0)

    # Send message
    msg_id = tg.send_message(text, reply_markup=keyboard)
    if msg_id:
        sm.record_message(sid, msg_id, event)


if __name__ == "__main__":
    main()
