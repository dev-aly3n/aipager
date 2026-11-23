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

Claude Code 2.1.287+ breaks that order for one prompt: its risky-rm ask in
Full access and Auto (a main-loop Bash prompt) carries a 2-minute
countdown and opens on top of prompts already open, so keys meant for an
older prompt land on it (roadmap 8.128). aipager leaves that countdown on,
so a prompt that may be such an ask (a countdown candidate) makes every
verdict ``"unknown"`` while another prompt is open.

That background dialog also opens late: it is drawn only once the hook
is gone, and a freshly drawn dialog ignores a key for a moment (Claude
Code 2.1.326, release harness: drawn 0.0-0.1 s after the hook's timeout;
an Enter in the same second went into the empty input line, one 2 s later
answered it every time). So a background agent's prompt, or one whose
launch is not known (its dialog may have waited for the hook too), is in
a *gap* from the hook's end until its dialog is on screen and settled:
:data:`DIALOG_SETTLE` after Claude Code's record says a permission dialog
is waiting (``order["dialog_seen_at"]``, stamped by
:mod:`aipager.prompt_gap`), or :data:`OPEN_GRACE` after the hook gave up
with no such evidence. While parked, the hook is there, so the dialog is
not: that is the gap too. Keys typed in the gap reach no dialog, so the
verdict is ``"opening"`` (:func:`dialog_gap`).

Pure: no aipager imports, no I/O, never raises.
"""

from __future__ import annotations

#: The PermissionRequest hook's reply deadline (notify_hook.py), seconds.
REPLY_DEADLINE = 20.0
#: Grace for a lost ``permission_reply_timeout`` datagram, seconds.
GRACE = 10.0

#: How long after Claude Code's record says a permission dialog waits
#: before keys go into it, seconds: the record lags the draw by 0.1-0.5 s,
#: so the dialog has been on screen for at least 2.1 s, never less than
#: the 2 s proven to answer it.
DIALOG_SETTLE = 2.0
#: How long after the hook gave up keys go in with no evidence of the
#: dialog, seconds: the worst draw delay seen (0.1 s), the proven 2 s
#: settle and about 2 s of margin for a loaded machine.
OPEN_GRACE = 4.0

#: Launch kinds of a prompt's owner.
OWN, FOREGROUND, BACKGROUND, UNKNOWN = "own", "foreground", "background", "unknown"

#: Permission modes in which Claude Code's risky-rm ask carries a countdown.
CANDIDATE_MODES = frozenset({"bypassPermissions", "auto"})
#: The ``mode`` of a prompt whose permission mode is not known (Claude Code
#: never sends it): counted as a countdown candidate.
MODE_UNKNOWN = "?"


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


def dialog_gap(order: object, now: float, *,
               reply_deadline: float = REPLY_DEADLINE, grace: float = GRACE,
               settle: float = DIALOG_SETTLE,
               open_grace: float = OPEN_GRACE) -> float | None:
    """The monotonic instant at which *order*'s prompt leaves its gap (see
    the module note), or None when it is not in one now.

    Only an agent's prompt (owner set) whose launch is background or not
    known, with an ``arrived`` stamp aipager wrote, is ever in a gap: an
    order aipager did not write is never held back, so nobody is locked
    out. The hook is gone from ``timed_out_at``; with no reply channel
    (no ``request_id``) from arrival; with a reply channel, while parked
    (``now`` before ``arrived + reply_deadline + grace``) it may still be
    there or gone unnoticed (its socket removed before aipager read the
    timeout), and after that from ``arrived + reply_deadline`` (the
    timeout datagram was lost). From the hook's end the gap lasts
    *open_grace*, cut to *settle* after ``dialog_seen_at`` when that
    evidence is no older than the end. Half-open: at the end instant the
    prompt is out of its gap."""
    if not isinstance(order, dict):
        return None
    if not order.get("owner") or order.get("launch") not in (BACKGROUND, UNKNOWN):
        return None
    raw = order.get("arrived")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    arrived = float(raw)
    timed_out = _num(order.get("timed_out_at"))
    request_id = order.get("request_id")
    has_channel = isinstance(request_id, str) and bool(request_id)
    gone: float | None
    if timed_out > 0:
        gone = timed_out
    elif not has_channel:
        gone = arrived
    elif now < arrived + reply_deadline + grace:
        gone = None
    else:
        gone = arrived + reply_deadline
    if gone is None:
        end = arrived + reply_deadline + grace
    else:
        end = gone + open_grace
        seen = _num(order.get("dialog_seen_at"))
        if seen > 0 and seen >= gone:
            end = min(end, seen + settle)
    return end if now < end else None


def gap_next_look(order: object, now: float, *,
                  reply_deadline: float = REPLY_DEADLINE, grace: float = GRACE,
                  settle: float = DIALOG_SETTLE,
                  open_grace: float = OPEN_GRACE) -> float | None:
    """When a refusal waiting on *order*'s gap should be looked at again:
    ``timed_out_at + settle`` while the hook's timeout is known, no
    evidence of the dialog is stamped and that instant is still ahead (a
    probe for Claude Code's record), else the gap's end. None when the
    prompt is not in a gap."""
    end = dialog_gap(order, now, reply_deadline=reply_deadline, grace=grace,
                     settle=settle, open_grace=open_grace)
    if end is None or not isinstance(order, dict):
        return None
    timed_out = _num(order.get("timed_out_at"))
    if (timed_out > 0 and not _num(order.get("dialog_seen_at")) > 0
            and now < timed_out + settle):
        return min(end, timed_out + settle)
    return end


def countdown_candidate(order: object) -> bool:
    """Whether a prompt may be Claude Code's risky-rm ask with a countdown:
    Claude's own (no agent owner: an agent's has no countdown) Bash prompt
    in Full access or Auto, or in a mode that is not known. Anything that
    is not an order dict counts (nothing about it is known). Never raises:
    an order that cannot be read counts too."""
    try:
        if not isinstance(order, dict):
            return True
        if order.get("owner"):
            return False
        mode = order.get("mode")
        return bool(order.get("tool") == "Bash"
                    and (mode in CANDIDATE_MODES or mode == MODE_UNKNOWN))
    except Exception:  # noqa: BLE001 - fail closed: a candidate
        return True


def keys_verdict(target: dict, others: list[dict], now: float, *,
                 reply_deadline: float = REPLY_DEADLINE,
                 grace: float = GRACE, settle: float = DIALOG_SETTLE,
                 open_grace: float = OPEN_GRACE) -> str:
    """Whether keystrokes typed now reach *target*'s dialog: ``"yes"``,
    ``"behind"`` when another open prompt is certainly shown before it,
    ``"opening"`` when the target's dialog may not be on screen and
    settled yet (:func:`dialog_gap`), or ``"unknown"``. Each argument is a
    prompt's ``order`` dict (see :func:`place`). With no other prompt
    open it is ``"yes"``, or ``"opening"`` in the target's gap.

    A countdown candidate among the open prompts (the target or any other,
    even one certainly not in the terminal yet) makes the verdict
    ``"unknown"``: that prompt may be on top of all of them. ``"behind"``
    and ``"unknown"`` say more than ``"opening"`` (another prompt is on
    top), so the gap only replaces a would-be ``"yes"``."""
    try:
        def yes() -> str:
            gap = dialog_gap(target, now, reply_deadline=reply_deadline,
                             grace=grace, settle=settle, open_grace=open_grace)
            return "yes" if gap is None else "opening"

        if not others:
            return yes()
        if (countdown_candidate(target)
                or any(countdown_candidate(other) for other in others)):
            return "unknown"
        kw = {"reply_deadline": reply_deadline, "grace": grace}
        t_absent, t_lo, t_hi = place(target, now, **kw)
        rest = []
        for other in others:
            q = place(other, now, **kw)
            if q[1] is None:
                continue  # certainly not in the terminal yet
            rest.append(q)
        if not rest:
            return yes()
        t_start = float("inf") if t_lo is None else t_lo
        for q_absent, _q_lo, q_hi in rest:
            if not q_absent and q_hi is not None and q_hi < t_start:
                return "behind"
        if t_absent or t_hi is None:
            return "unknown"
        for _q_absent, q_lo, _q_hi in rest:
            if not (q_lo is not None and t_hi < q_lo):
                return "unknown"
        return yes()
    except Exception:  # noqa: BLE001 - a verdict never raises: fail closed
        return "unknown"
