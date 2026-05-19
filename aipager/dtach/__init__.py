"""dtach + Claude Code hook plumbing.

Submodules:

- ``inject`` — async injection into a running dtach session (Telegram-bot side).
- ``launcher`` — CLI-side wrapper that spawns ``dtach -A`` for a fresh
  Claude Code session and tees its output to a Telegram-friendly redraw.
- ``redraw`` — terminal redraw helper used by ``launcher``.
- ``hook_receiver`` — UDP socket listener that consumes events from
  ``notify_hook`` / ``statusline_notify`` running inside each Claude
  session and updates the in-process session registry.
- ``notify_hook`` — console-script (``aipager-hook``) installed as a
  Claude Code hook; emits one UDP datagram per event.
- ``statusline_notify`` — console-script (``aipager-statusline``)
  installed as Claude Code's statusLine; emits real-time token / cost
  metrics over the same UDP socket.

The two console-scripts live here (not at the package root) because
they're conceptually part of the dtach/hook plumbing — they just
happen to run as subprocesses spawned by Claude rather than imports
from the daemon.
"""

from aipager.dtach.inject import (
    SOCK_PREFIX,
    is_alive,
    kill_session,
    launch_session,
    list_sessions,
    send_keys,
    send_text_and_enter,
)

__all__ = [
    "SOCK_PREFIX",
    "is_alive",
    "kill_session",
    "launch_session",
    "list_sessions",
    "send_keys",
    "send_text_and_enter",
]
