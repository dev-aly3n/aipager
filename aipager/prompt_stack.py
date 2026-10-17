"""Which of several open prompts Claude Code shows first (roadmap 8.120).

Claude Code shows one dialog at a time. Its dialog store (2.1.302) keeps
the dialog that entered the terminal first on screen and queues later ones
underneath: the main loop's and a foreground agent's ask in arrival order,
and a background agent's goes under everything already open. Answering the
dialog on screen uncovers the next. A background agent's dialog enters the
terminal only once its PermissionRequest hook has given up
(``awaitAutomatedChecksBeforeDialog``): while aipager's hook is parked, it
is not on screen at all.

Keystrokes always reach the dialog on screen, so a tap may type keys only
when its own prompt is certainly the one shown first. This module turns
what aipager knows about each open prompt into that verdict, failing
closed: ``"yes"`` only when the order is certain.

Pure: no aipager imports, no I/O, never raises.
"""

from __future__ import annotations

#: The PermissionRequest hook's reply deadline (notify_hook.py), seconds.
REPLY_DEADLINE = 20.0
#: Grace for a lost ``permission_reply_timeout`` datagram, seconds.
GRACE = 10.0

#: Launch kinds of a prompt's owner.
OWN, FOREGROUND, BACKGROUND, UNKNOWN = "own", "foreground", "background", "unknown"


def _num(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def prompt_key(owner: object, tool_name: object, own_tool_use_id: object,
               digest: object, summary: object) -> str:
    """The identity of a prompt, to tell one dialog drawn twice from two
    dialogs: the asking owner, the tool, and the payload's own tool call id
    when it carries one, else the whole input's digest, else the summary.
    Never a borrowed id: two parallel calls of one tool still differ by
    their input."""
    def s(value: object) -> str:
        return value if isinstance(value, str) else ""
    detail = (("id:" + s(own_tool_use_id)) if s(own_tool_use_id)
              else ("in:" + s(digest)) if s(digest)
              else ("sum:" + s(summary)))
    return f"{s(owner)}|{s(tool_name)}|{detail}"


def place(order: object, now: float, *, reply_deadline: float = REPLY_DEADLINE,
          grace: float = GRACE) -> tuple[bool, float | None, float | None]:
    """``(may_be_absent, lo, hi)`` for one prompt: whether its dialog may
    not be in the terminal yet, and the window in which it entered the
    terminal's stack (``(True, None, None)``: certainly not there yet).

    *order* is the prompt's ``tool_info["order"]``; anything that is not a
    dict is Claude's own prompt, present from when it arrived."""
    if not isinstance(order, dict):
        return False, 0.0, 0.0
    raw = order.get("arrived")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        # Not a stamp aipager wrote: when it entered is not known.
        return True, 0.0, now
    arrived = float(raw)
    owner = order.get("owner")
    launch = order.get("launch")
    if not owner or launch in (OWN, FOREGROUND):
        return False, arrived, arrived
    timed_out = _num(order.get("timed_out_at"))
    request_id = order.get("request_id")
    parked = (isinstance(request_id, str) and bool(request_id)
              and now < arrived + reply_deadline + grace)
    if launch == BACKGROUND:
        if timed_out > 0:
            return False, timed_out, timed_out
        if parked:
            return True, None, None
        return True, arrived, now
    # Unknown: its dialog raced the hook (present at arrival) or waited
    # for it (present only once the hook gave up).
    if timed_out > 0:
        return False, arrived, timed_out
    if parked:
        return True, arrived, arrived
    return True, arrived, now


def keys_verdict(target: dict, others: list[dict], now: float, *,
                 reply_deadline: float = REPLY_DEADLINE,
                 grace: float = GRACE) -> str:
    """Whether keystrokes typed now reach *target*'s dialog: ``"yes"``,
    ``"behind"`` when another open prompt is certainly shown before it, or
    ``"unknown"``. Each argument is a prompt's ``order`` dict (see
    :func:`place`). With no other prompt open it is always ``"yes"``: a
    single prompt is answered exactly as before 8.120."""
    try:
        if not others:
            return "yes"
        kw = {"reply_deadline": reply_deadline, "grace": grace}
        t_absent, t_lo, t_hi = place(target, now, **kw)
        rest = []
        for other in others:
            q = place(other, now, **kw)
            if q[1] is None:
                continue  # certainly not in the terminal yet
            rest.append(q)
        if not rest:
            return "yes"
        t_start = float("inf") if t_lo is None else t_lo
        for q_absent, _q_lo, q_hi in rest:
            if not q_absent and q_hi is not None and q_hi < t_start:
                return "behind"
        if t_absent or t_hi is None:
            return "unknown"
        for _q_absent, q_lo, _q_hi in rest:
            if not (q_lo is not None and t_hi < q_lo):
                return "unknown"
        return "yes"
    except Exception:  # noqa: BLE001 - a verdict never raises: fail closed
        return "unknown"
