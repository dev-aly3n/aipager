"""The chat's view of Claude Code's risky-removal countdown (roadmap 8.128).

In Full access and Auto, Claude Code refuses its own prompt for a risky
``rm`` / ``rmdir`` by itself when nobody answers within about 2 minutes
(:mod:`aipager.rm_countdown`). Its permission card says about when, and
once it has, the prompt message says so.
"""

from __future__ import annotations

from aipager import rm_countdown
from aipager.bot.flood import clear_time
from aipager.i18n import Msg

RM_CLOCK = Msg(
    "perm.rm_clock",
    "⏱ If nobody answers, Claude Code refuses this by itself at about {time}.",
    note="Under a permission prompt for a risky removal in Full access or Auto: Claude "
         "Code refuses it by itself about 2 minutes after it opened; {time} is a clock "
         "time (the computer's local time)",
    eg={"time": "14:05"})
RM_REFUSED = Msg(
    "perm.rm_refused",
    "⏱ Claude Code refused this by itself because nobody answered in time.",
    note="Added under a prompt message once Claude Code refused it on its own; its "
         "buttons are gone")


def clock_line_html(tr, holder) -> str:
    """The clock line under a risky-removal prompt (a leading newline and
    the line), in *tr*'s language and digits, from the ``rm_clock`` of
    *holder* (the prompt's ``tool_info`` or the prompt); ``""`` for a
    prompt with none or with one the transcript disproved (roadmap
    8.152). The time is the computer's local time of day."""
    clock = rm_countdown.shown_clock(holder)
    if clock is None:
        return ""
    try:
        shown = clear_time(clock["refuse_at"])
    except (OverflowError, OSError, ValueError):
        return ""
    return "\n" + tr.html(RM_CLOCK, time=tr.num(shown))



def take_unclocked_text(rec) -> str | None:
    """A prompt message record's HTML text without its clock line, kept
    when it was sent (``rec["text_unclocked"]``, roadmap 8.151), taken out
    of the record so it is used once; None when it has none."""
    if not isinstance(rec, dict):
        return None
    text = rec.pop("text_unclocked", None)
    return text if isinstance(text, str) and text else None
