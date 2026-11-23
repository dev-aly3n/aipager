"""Evidence that an agent's permission dialog is on screen after its hook's gap.

A background agent's dialog enters Claude Code's terminal only once its
PermissionRequest hook is gone, and a freshly drawn dialog ignores a key
for a moment, so aipager types nothing for such a prompt until it is out
of its gap (:func:`aipager.prompt_stack.dialog_gap`). The gap ends by
itself :data:`aipager.prompt_stack.OPEN_GRACE` after the hook gave up; it
ends sooner, :data:`aipager.prompt_stack.DIALOG_SETTLE` after Claude
Code's own session record said a permission dialog is waiting, when that
record can be tied to the prompt. This module reads the record on demand
(a refused tap, a waiting auto-deny's retry), never from the monitor's
tick, and stamps ``order["dialog_seen_at"]``.

The record names no prompt (only ``status``, ``waitingFor`` and
``statusAt``), so it is tied to one only when that prompt is the only
open prompt in a gap, and only when it changed no earlier than
:data:`GONE_SLACK` before the hook's timeout was handled. It is an
accelerator only: a Claude Code that never writes "waiting" for such a
dialog leaves the grace to end the gap.

Daemon side only: never imported by the hook or by :mod:`aipager.state`,
and it never imports ``aipager.i18n``. Never raises.
"""

from __future__ import annotations

import logging
import time

from aipager import prompt_stack, term_dialog

log = logging.getLogger(__name__)

#: How long before aipager handled the hook's timeout the record may say
#: waiting and still count, seconds: the timeout datagram can be handled a
#: little after Claude Code drew the dialog and wrote its record.
GONE_SLACK = 0.5

# -- seams (tests replace these, never ``time`` itself) ---------------------

#: The session's own Claude Code record, read now off the event loop.
_record = term_dialog.fresh_record
_now_mono = time.monotonic
_now_wall = time.time


def order_of(perm) -> dict | None:
    """Open prompt *perm*'s order dict, or None."""
    info = perm.get("tool_info") if isinstance(perm, dict) else None
    order = info.get("order") if isinstance(info, dict) else None
    return order if isinstance(order, dict) else None


async def look_for_dialog(sess, perm) -> bool:
    """Read *sess*'s own Claude Code record once and, when it proves open
    prompt *perm*'s permission dialog is on screen, stamp its order's
    ``dialog_seen_at`` (monotonic). True when it stamped.

    Only for a prompt in its gap whose hook's timeout aipager handled
    (``timed_out_at``), not stamped yet, and the only open prompt in a gap
    (the record cannot be tied to one of several). The stamp is the
    record's ``statusAt`` on the monotonic clock, never before the
    timeout. Never raises."""
    try:
        order = order_of(perm)
        if order is None:
            return False
        now = _now_mono()
        timed_out = order.get("timed_out_at")
        if (isinstance(timed_out, bool) or not isinstance(timed_out, (int, float))
                or timed_out <= 0 or order.get("dialog_seen_at")
                or prompt_stack.dialog_gap(order, now) is None):
            return False
        for other in sess.open_prompts():
            if other is not perm and prompt_stack.dialog_gap(
                    order_of(other), now) is not None:
                return False
        entry = await _record(sess)
        now_mono, now_wall = _now_mono(), _now_wall()
        since_wall = now_wall - (now_mono - float(timed_out)) - GONE_SLACK
        at = term_dialog.permission_waiting_since(entry, since_wall)
        if at is None or order.get("dialog_seen_at"):
            return False
        seen = max(float(timed_out), now_mono - (now_wall - at))
        order["dialog_seen_at"] = seen
        log.info("[%s] permission dialog on screen %.2fs after its hook's "
                 "timeout (Claude Code's record)", sess.label,
                 seen - float(timed_out))
        return True
    except Exception:  # noqa: BLE001 - evidence is optional: the grace decides
        log.debug("[%s] looking for the permission dialog failed",
                  getattr(sess, "label", "?"), exc_info=True)
        return False
