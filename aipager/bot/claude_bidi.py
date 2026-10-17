"""Claude's words on the busy card, kept reading in their own direction.

The card takes the chat's direction (operator, 2026-10-17): an English
chat's card is a left-to-right rich message, a Persian or Arabic chat's
a right-to-left one, whatever language Claude writes in. Telegram
Desktop and Web lay out every block of a rich message in the message's
direction; Telegram for iPhone and Android take a block's direction from
its first strong letter. So a sentence of Claude's written the other way
round (Persian in an English chat's card, English in a Persian one)
needs help to read in order everywhere:

- :func:`prose`: a line of Claude's commentary written the other way
  (most of its letters outside code go against the card) is wrapped, after its Markdown block marker and
  any opening emphasis run, in its own direction's mark and isolate
  (U+200F U+2067 ... U+2069 U+200F for right to left, U+200E U+2066 ...
  U+2069 U+200E for left to right), so its words, numbers and full stop
  keep their order inside the card, and an app that aligns by the first
  letter still finds the line's own direction. Nothing goes inside code,
  a fenced or ``<pre>`` block, a table row or a line that already holds a
  direction control.
- :func:`span`: a code span holding a right-to-left letter, in a
  left-to-right rendering that does not isolate its parameters, is
  isolated whole from outside it (U+2068 ... U+2069), so a clock or a
  word after it is not pulled into Claude's phrase.
- :func:`row`: a left-to-right card's line built in code whose first
  strong letter is right to left (a row holding a Persian question, the
  status line, paused line or peer line of a session with a Persian
  name) starts with U+200E, so it is laid out left to right on every
  app.

English text with no right-to-left letter is never changed, and neither
is anything in a right-to-left card that already reads right to left.
Display only: nothing here decides anything, and nothing Claude reads
passes through it.
"""

from __future__ import annotations

import re
import unicodedata

from aipager.bidi_guard import BIDI_CONTROLS

__all__ = ["has_rtl_letter", "prose", "row", "span"]

_LRM, _RLM = "\u200e", "\u200f"
_LRI, _RLI, _FSI, _PDI = "\u2066", "\u2067", "\u2068", "\u2069"
_ISOLATE_OPEN = frozenset((_LRI, _RLI, _FSI))
_STRONG_R = frozenset(("R", "AL"))

#: A line's Markdown block markers (quote, list item, ordered item,
#: heading) and its indent: the marks go after them, or the block is lost.
_PREFIX = re.compile(r"(?:[ \t]*(?:>[ \t]?|[-*+][ \t]+|\d{1,9}[.)][ \t]+|#{1,6}[ \t]+))*[ \t]*")
#: A line indented four columns or more: indented code, or a list item's
#: continuation; never marked.
_INDENTED = re.compile(r"(?: {4}| {0,3}\t)")
#: A delimiter run (``*``, ``_`` or ``~``): emphasis or strikethrough
#: only when it pairs with a run of the same length on the line.
_RUN = re.compile(r"\*+|_+|~+")
#: A hard line break at the body's end (two spaces or a backslash): the
#: marks go before it.
_BREAK = re.compile(r"(?:[ \t]+|\\)\Z")
_FENCE = re.compile(r"[ \t]*(?:```|~~~)")
_PRE = re.compile(r"</?pre\b", re.I)
_TAG = re.compile(r"<[^>]*>")
#: A Markdown code span (one or more backticks, closed by a run of the
#: same length); one that crosses a line break makes the lines it
#: touches off limits.
_CODE_SPAN = re.compile(r"(?<![`\\])(`+)(?!`)(?:[^\n]|\n(?![ \t]*\n))*?(?<!`)\1(?!`)")


def has_rtl_letter(text: str) -> bool:
    """Whether *text* holds a right-to-left letter (Persian, Arabic,
    Hebrew), not a mark or a digit."""
    return any(unicodedata.bidirectional(ch) in _STRONG_R
               and unicodedata.category(ch).startswith("L") for ch in text)


def _first_strong(text: str, *, inside: bool = False) -> bool | None:
    """``True`` when *text*'s first strong letter outside isolates (the
    way a paragraph's direction is found; counting what they hold with
    *inside*, for an app that does not skip them) is right to left,
    ``False`` when it is left to right, ``None`` with none. Tags are not
    text."""
    depth = 0
    for ch in _TAG.sub("", text):
        if ch in _ISOLATE_OPEN:
            depth += 1
        elif ch == _PDI:
            depth = max(depth - 1, 0)
        elif inside or not depth:
            bidi = unicodedata.bidirectional(ch)
            if bidi == "L":
                return False
            if bidi in _STRONG_R:
                return True
    return None


#: What is not a sentence's own words: code, a link's address, a tag.
_NOT_WORDS = re.compile(r"(`+)[^`]*?\1|\]\([^)\s]*\)|<[^>]*>")


def _direction(text: str) -> bool | None:
    """The direction a line of Claude's is written in: ``True`` when more
    of its letters outside code, link addresses and tags are right to
    left than left to right, ``False`` when it has letters and they are
    not, ``None`` with none. A Persian sentence that opens on a file
    name is still Persian; a bare web address counts as letters, as
    the answer's own direction rule (``rich_message.detect_rtl``) does."""
    words = _NOT_WORDS.sub(" ", text)
    rtl = ltr = 0
    for ch in words:
        if not unicodedata.category(ch).startswith("L"):
            continue
        bidi = unicodedata.bidirectional(ch)
        if bidi in _STRONG_R:
            rtl += 1
        elif bidi == "L":
            ltr += 1
    if not rtl and not ltr:
        return None
    return rtl > ltr


def _crossing_lines(text: str) -> set[int]:
    """The indexes of *text*'s lines a multi-line code span touches."""
    out: set[int] = set()
    for m in _CODE_SPAN.finditer(text):
        if "\n" in m.group(0):
            first = text.count("\n", 0, m.start())
            out.update(range(first, first + m.group(0).count("\n") + 1))
    return out


def _punct(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "PS"


def _can_open(text: str, start: int, end: int) -> bool:
    """Whether the delimiter run ``text[start:end]`` can open emphasis
    (CommonMark's flanking rules; ``~`` as ``*``)."""
    before = text[start - 1] if start else " "
    after = text[end] if end < len(text) else " "
    if after.isspace():
        return False
    edge = before.isspace() or _punct(before)
    if text[start] == "_":
        return edge
    return not _punct(after) or edge


def _can_close(text: str, start: int, end: int) -> bool:
    """Whether the delimiter run ``text[start:end]`` can close emphasis."""
    before = text[start - 1] if start else " "
    after = text[end] if end < len(text) else " "
    if before.isspace():
        return False
    edge = after.isspace() or _punct(after)
    if text[start] == "_":
        return edge
    return not _punct(before) or edge


def _emphasis_ends(body: str) -> tuple[int, int]:
    """How much of *body*'s start and end is an emphasis run that the
    marks must stay outside of: an opening run at the start that a run of
    the same length closes later on the line, and a closing run at the
    end (before a hard break) that one of the same length opened before
    it. A ``_``, ``*`` or ``~`` that does not pair (``__init__``-like
    names, ``*.py``, ``~/projects``, ``foo~``) is text and stays inside.
    A pairing check, not CommonMark's delimiter stack: in ``x *a* b*`` the
    last ``*`` is taken for a closer and kept outside, a literal star
    shown at the line's far end; the markup itself is never broken."""
    runs = [m for m in _RUN.finditer(body)]
    brk = _BREAK.search(body)
    stop = brk.start() if brk else len(body)
    lead = 0
    if runs and runs[0].start() == 0 and _can_open(body, 0, runs[0].end()):
        first = runs[0].group(0)
        if any(r.group(0) == first and r.start() >= runs[0].end()
               and _can_close(body, r.start(), r.end()) for r in runs[1:]):
            lead = runs[0].end()
    tail = len(body) - stop
    last = [r for r in runs if r.end() == stop]
    if last and _can_close(body, last[0].start(), last[0].end()):
        run = last[0]
        if any(r.group(0) == run.group(0) and r.end() <= run.start()
               and _can_open(body, r.start(), r.end()) for r in runs):
            tail = len(body) - run.start()
    return lead, tail


def _wrap(line: str, rtl: bool) -> str:
    """*line* (prefix, body) with its body's text between its own
    direction's mark and isolate."""
    prefix = _PREFIX.match(line).group(0)
    body = line[len(prefix):]
    start, end = _emphasis_ends(body)
    lead, core, tail = body[:start], body[start:len(body) - end], body[len(body) - end:]
    mark, iso = (_RLM, _RLI) if rtl else (_LRM, _LRI)
    return f"{prefix}{lead}{mark}{iso}{core}{_PDI}{mark}{tail}"


def prose(text: str, card_rtl: bool) -> str:
    """*text*, one block of Claude's commentary (Markdown), for a card
    laid out right to left (*card_rtl*) or left to right: each line
    written the other way (:func:`_direction`) is wrapped in its own
    direction (see the module docstring). English in a left-to-right
    card comes back as it is."""
    if not card_rtl and not has_rtl_letter(text):
        return text
    if any(ch in BIDI_CONTROLS for ch in text):
        # Claude placed direction controls itself: an isolate of ours
        # around them could be closed early by one of its own.
        return text
    crossing = _crossing_lines(text)
    lines = text.split("\n")
    fenced = False
    pre = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or i in crossing or _INDENTED.match(line):
            continue
        tags = _PRE.findall(line)
        if tags or pre:
            if tags:
                pre = not tags[-1].startswith("</")
            continue
        prefix = _PREFIX.match(line).group(0)
        if line[len(prefix):].startswith("|"):
            continue  # a table row: Telegram aligns its cells itself
        own = _direction(line[len(prefix):])
        if own is None or own == card_rtl:
            continue
        lines[i] = _wrap(line, own)
    return "\n".join(lines)


def span(tr, code: str, text: str) -> str:
    """*code* (the code span drawn for *text*) isolated whole from outside
    it when *text* holds a right-to-left letter and *tr* renders left to
    right without isolating its parameters; else *code* as it is."""
    if getattr(tr, "isolates", False):
        return code
    if not has_rtl_letter(text):
        return code
    return f"{_FSI}{code}{_PDI}"


def row(tr, line: str) -> str:
    """*line*, a whole card line built in code (a row, the status line,
    the paused or peer line), with U+200E at its start when *tr* renders
    left to right and the line's first strong letter is right to left;
    else as it is. A right-to-left rendering (the chat's own, or the
    in-RTL twin) marks its lines itself."""
    if getattr(tr, "rtl", False) or getattr(tr, "ltr_in_rtl", False):
        return line
    if not (_first_strong(line) or _first_strong(line, inside=True)):
        return line
    return _LRM + line
