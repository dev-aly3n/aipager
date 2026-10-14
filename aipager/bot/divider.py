"""The one divider aipager draws in a rich message.

Telegram's rich Markdown draws a line of its own (a ``RichBlockDivider``)
for a ``---`` paragraph. aipager puts one between an answer and the
agents block under it, above the busy card's status line, and between the
finished card and the answer in the merged layout.

Written ONLY as :data:`DIVIDER_SEP`: a blank line on both sides. Right
under a text line with no blank line between, ``---`` would turn that line
into a heading (GitHub Flavored Markdown's setext rule). It never goes
through ``tr.md``/``tr.line``: a direction mark on the line (``\\u200f---``)
would make it a paragraph of dashes, not a divider.

A plain-text copy of a rich message has no divider: :func:`without_dividers`
drops each one, or puts a text line in its place.

Pure text, standard library only.
"""

from __future__ import annotations

#: The divider paragraph itself.
DIVIDER = "---"
#: The only way aipager writes a divider: its own paragraph.
DIVIDER_SEP = f"\n\n{DIVIDER}\n\n"
#: What stands in for a divider in a plain-text copy, where ``---`` would
#: show as it is (the merged layout's line before the divider replaced it).
PLAIN_DIVIDER = "―――――――――――――"

_PARAGRAPH = "\n\n"


def without_dividers(text: str, plain: str = "") -> str:
    """*text* with every paragraph that is exactly :data:`DIVIDER` replaced
    by *plain*, or dropped when *plain* is ``""``. Anything else stays as
    written, a ``---`` that is not a paragraph of its own included
    (``"a\\n---"``, ``"- ---"``, ``"----"``)."""
    out = []
    for part in text.split(_PARAGRAPH):
        if part == DIVIDER:
            if plain:
                out.append(plain)
            continue
        out.append(part)
    return _PARAGRAPH.join(out)
