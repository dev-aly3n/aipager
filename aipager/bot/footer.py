"""Telegram's footer block: the small text aipager draws under a divider.

Telegram's rich Markdown has no footer of its own, but it takes HTML, and
``<footer>`` is a block of small text (a ``RichBlockFooter``). aipager
draws the lines under a divider with it: the busy card's status line, and
the agents and shells block under an answer.

Markdown is not parsed inside a ``<footer>``: what goes in is HTML. The
lines are written in aipager's own one-line Markdown (``tr.md``: a name in
``**bold**``, the characters ``\\ * _ ` [ ]`` escaped with a backslash), so
:func:`footer` turns exactly that into HTML: an escaped character becomes
itself, a pair of ``**`` becomes ``<b>…</b>`` (an unpaired one stays as
written), and everything is HTML-escaped. Direction marks stay where the
line has them, inside the tag.

A footer is its own block: it starts a line (a paragraph, or a list item
after ``"- "``) and nothing follows it on that line. A plain-text copy of
a rich message has no footer: :func:`without_last_footer` leaves the busy
card's status line as text (the only footer in a message with Claude's
words in it; the agents block is never in a plain copy).

Pure text, standard library only.
"""

from __future__ import annotations

import html
import re

OPEN = "<footer>"
CLOSE = "</footer>"
_ITEM = "- "

# One token at a time: a backslash escape, a "**", or a run of anything
# else (a lone backslash or star included, as written).
_TOKEN = re.compile(r"\\([\\*_`\[\]])|(\*\*)|([^\\*]+|[\\*])")
_FOOTER = re.compile(re.escape(OPEN) + r"(.*?)" + re.escape(CLOSE), re.S)
_TAG = re.compile(r"</?b>")


def md_to_html(md: str) -> str:
    """*md* (one line of aipager's own Markdown) as HTML."""
    tokens: list[tuple[str, str]] = []
    for m in _TOKEN.finditer(md):
        escaped, stars, text = m.groups()
        if stars:
            tokens.append(("b", stars))
        else:
            tokens.append(("t", escaped if escaped is not None else text))
    pairs = sum(1 for kind, _ in tokens if kind == "b") // 2 * 2
    out: list[str] = []
    open_bold = False
    for kind, value in tokens:
        if kind == "b" and pairs:
            out.append("</b>" if open_bold else "<b>")
            open_bold = not open_bold
            pairs -= 1
        else:
            out.append(html.escape(value, quote=False))
    return "".join(out)


def footer(md: str) -> str:
    """*md* (one line of aipager's own Markdown) as a footer block."""
    return f"{OPEN}{md_to_html(md)}{CLOSE}"


def footer_block(md: str) -> str:
    """*md* (paragraphs separated by a blank line, and lists of ``"- "``
    items, one per line) with each paragraph, and each item's text, a
    footer block: the agents block under an answer, as drawn."""
    out = []
    for para in md.split("\n\n"):
        items = para.split("\n")
        if all(item.startswith(_ITEM) for item in items):
            out.append("\n".join(_ITEM + footer(item[len(_ITEM):]) for item in items))
        else:
            out.append(footer(para))
    return "\n\n".join(out)


def _plain(match: re.Match) -> str:
    return html.unescape(_TAG.sub("", match.group(1)))


def without_last_footer(text: str) -> str:
    """*text* with its last paragraph, when that is one footer block (the
    busy card's status line, as :func:`footer` drew it), as plain text;
    every other paragraph exactly as written, Claude's words included."""
    head, sep, last = text.rpartition("\n\n")
    m = _FOOTER.fullmatch(last)
    if not m:
        return text
    return f"{head}{sep}{_plain(m)}"
