"""Telegram polling daemon — receives button taps and text replies, injects into tmux.

Usage:
    screen -S tgremote python3 -m aipager.daemon

The daemon long-polls Telegram for:
1. Callback queries (inline button taps) — dispatched by callback_data
2. Text replies to notification messages — looked up by reply_to_message_id
"""

import logging
import signal
import sys
import time

from aipager import telegram_api as tg
from aipager import session_mgr as sm
from aipager import injector
from aipager.config import BOT_TOKEN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("daemon")

# Action → verb mapping (keystrokes handled in handle_callback)
# Claude Code uses an arrow-key selector UI:
#   ❯ 1. Yes        ← pre-selected, Enter to allow
#     2. No         ← Down + Enter to deny
#     3. Type something.
# Idle prompts accept free text (Enter) or Escape to stop
ACTION_VERBS = {
    "allow": "Allowed",
    "deny": "Denied",
    "continue": "Continued",
    "stop": "Stopped",
}


def handle_callback(update: dict) -> None:
    """Handle an inline keyboard button tap."""
    cb = update["callback_query"]
    cb_id = cb["id"]
    cb_data = cb.get("data", "")
    message = cb.get("message", {})
    message_id = message.get("message_id")
    original_text = message.get("text", "")

    # Parse callback_data: "shortid:action"
    if ":" not in cb_data:
        tg.answer_callback_query(cb_id, "Invalid callback")
        return

    sid, action = cb_data.split(":", 1)

    # Parse action — either a known verb or "opt{N}" for option selection
    is_option = action.startswith("opt") and action[3:].isdigit()

    if action not in ACTION_VERBS and not is_option:
        tg.answer_callback_query(cb_id, f"Unknown action: {action}")
        return

    session = sm.get_session_by_short_id(sid)
    if not session:
        tg.answer_callback_query(cb_id, "Session not found")
        return

    tmux_session = session["tmux_session"]
    label = session["label"]

    # Check tmux session is alive
    if not injector.is_session_alive(tmux_session):
        tg.answer_callback_query(cb_id, f"tmux session '{tmux_session}' not found")
        return

    # Inject keystrokes based on action
    if is_option:
        # AskUserQuestion — navigate to option N (0-based) and select
        option_index = int(action[3:])
        verb = f"Selected option {option_index + 1}"
        ok = True
        # Press Down N times to reach the option (option 0 is pre-selected)
        for _ in range(option_index):
            if not injector.send_keys(tmux_session, "Down"):
                ok = False
                break
        if ok:
            import time
            time.sleep(0.1)  # small delay for UI to update
            ok = injector.send_keys(tmux_session, "Enter")
    elif action == "allow":
        verb = ACTION_VERBS[action]
        # Option 1 (Yes/Allow) is pre-selected — just press Enter
        ok = injector.send_keys(tmux_session, "Enter")
    elif action == "deny":
        verb = ACTION_VERBS[action]
        # Navigate down to option 2 (No/Deny), then press Enter
        ok = injector.send_keys(tmux_session, "Down")
        if ok:
            import time
            time.sleep(0.1)
            ok = injector.send_keys(tmux_session, "Enter")
    elif action == "continue":
        verb = ACTION_VERBS[action]
        ok = injector.send_keys(tmux_session, "Enter")
    elif action == "stop":
        verb = ACTION_VERBS[action]
        ok = injector.send_keys(tmux_session, "Escape")

    if ok:
        tg.answer_callback_query(cb_id, f"{verb} [{label}]")
        # Remove buttons and update message text
        tg.edit_message_text(
            message_id,
            f"{original_text}\n\n→ {verb}",
        )
        sm.remove_message(sid, message_id)
        log.info("[%s] %s (tmux: %s)", label, verb, tmux_session)
    else:
        tg.answer_callback_query(cb_id, f"Failed to send to {tmux_session}")


def handle_text_reply(update: dict) -> None:
    """Handle a text reply to a notification message — inject as free text input."""
    message = update["message"]
    reply_to = message.get("reply_to_message")
    text = message.get("text", "").strip()

    if not reply_to or not text:
        return

    reply_to_id = reply_to["message_id"]
    result = sm.get_session_by_message_id(reply_to_id)
    if not result:
        log.debug("Reply to unknown message %d", reply_to_id)
        return

    sid, session = result
    tmux_session = session["tmux_session"]
    label = session["label"]

    if not injector.is_session_alive(tmux_session):
        tg.send_message(f"⚠️ tmux session '{tmux_session}' not found")
        return

    ok = injector.send_text_and_enter(tmux_session, text)
    if ok:
        tg.send_message(f"⌨️ [{label}] Sent: {text[:50]}")
        log.info("[%s] Sent text: %s", label, text[:80])
    else:
        tg.send_message(f"❌ Failed to send to [{label}]")


def run() -> None:
    """Main polling loop."""
    if not BOT_TOKEN:
        log.error("CLAUDE_TG_BOT_TOKEN not set — exiting")
        sys.exit(1)

    log.info("Claude Remote daemon starting — polling Telegram...")
    offset = None

    while True:
        try:
            updates = tg.get_updates(offset=offset, timeout=30)

            for update in updates:
                offset = update["update_id"] + 1

                if "callback_query" in update:
                    handle_callback(update)
                elif "message" in update:
                    handle_text_reply(update)

        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception:
            log.exception("Error in poll loop")
            time.sleep(5)


def _sigterm_handler(signum, frame):
    log.info("SIGTERM received, shutting down")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _sigterm_handler)
    run()
