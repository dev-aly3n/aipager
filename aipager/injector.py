"""tmux send-keys wrapper — injects keystrokes into Claude sessions."""

import logging
import subprocess

log = logging.getLogger(__name__)


def send_keys(tmux_session: str, keys: str) -> bool:
    """Send a key sequence to a tmux session.

    Args:
        tmux_session: tmux session name (e.g., "claude-dev")
        keys: key name or text to send (e.g., "y", "n", "Enter", or free text)

    Returns:
        True if successful, False otherwise.
    """
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_session, keys],
            check=True, capture_output=True, timeout=5,
        )
        log.info("Sent keys %r to tmux session %s", keys, tmux_session)
        return True
    except subprocess.CalledProcessError as e:
        log.error("tmux send-keys failed for %s: %s", tmux_session, e.stderr.decode())
        return False
    except FileNotFoundError:
        log.error("tmux not found — install with: sudo apt install tmux")
        return False
    except subprocess.TimeoutExpired:
        log.error("tmux send-keys timed out for %s", tmux_session)
        return False


def send_text_and_enter(tmux_session: str, text: str) -> bool:
    """Type text and press Enter in a tmux session."""
    # First send the text literally
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_session, "-l", text],
            check=True, capture_output=True, timeout=5,
        )
        # Then press Enter
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_session, "Enter"],
            check=True, capture_output=True, timeout=5,
        )
        log.info("Sent text %r + Enter to tmux session %s", text, tmux_session)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("Failed to send text to %s: %s", tmux_session, e)
        return False


def is_session_alive(tmux_session: str) -> bool:
    """Check if a tmux session exists."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", tmux_session],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
