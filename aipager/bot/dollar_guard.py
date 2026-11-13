"""Keep a ``$`` in the text aipager sends a plain dollar sign.

A rich message's Markdown reads ``$...$`` (and ``$$...$$``) as a LaTeX
formula (Bot API "Rich Markdown style": ``$x^2 + y^2$``, ``$$E = mc^2$$``).
Claude Code writes for a terminal, where a ``$`` is money, a shell
variable or a price, never a formula, so a sentence with two amounts
("bought $10 of Up and $10 of Down ... made +$5.64") was drawn as one
italic formula with its spaces gone and its dollar signs lost.

:func:`escape_dollars` runs on every rich payload in ``rich_message``
(after ``neutralize_media``, whose image-to-link rewrite can put an
address into a link's text) and turns each ``$`` into ``\\$``: rich
Markdown follows GitHub Flavored Markdown, where a backslash before ASCII
punctuation shows that character as itself. It leaves alone the places
where a backslash would be shown or would change an address:

- fenced code blocks, a ```` ```math ```` fence included, also when the
  fence sits in a block quote or a list (``> ```sh``, ``1. ```sh``,
  indented any depth): the busy card quotes Claude's prose line by line;
- inline code spans;
- the inside of ``<footer>`` (Markdown is not parsed there: aipager's
  footer lines are already HTML, see ``footer.py``), ``<pre>`` and
  ``<code>``;
- link addresses that are not text: a ``<scheme:...>`` autolink (no
  backslash escape works inside one) and the destination of an inline
  link ``[text](address)``. A bare address in prose is escaped like the
  rest: Telegram ends a bare link before a ``$`` (seen live on 2026-11-13:
  "https://example.com/?a=$1 costs $2." linked only "...?a=" and drew
  "1 costs" as a formula, closed even by the escaped ``\\$`` after it);
- a ``$`` already escaped (an odd number of backslashes before it), so
  escaping twice gives the same text as escaping once.

A fence opened inside a list item and never closed runs to the end of
the message here (a quoted one ends with its quote): the ``$`` after it
stay bare, as before this guard, rather than gaining a backslash.

Indented code blocks (four spaces, no fence) are not skipped: in Claude's
answers an indented line is nearly always a list item's continuation,
where an unescaped pair of amounts would turn into a formula. A real
indented code block with a ``$`` shows a backslash before it.

Each escape adds one character. A message already at Telegram's length
limit can then be refused; an answer falls back to plain text (with its
dollar signs as written), which loses nothing. ``media_guard`` makes the
same trade.
"""

from __future__ import annotations

import re

from aipager.bot.media_guard import _code_spans

# A fence line, after any container prefix: indentation, block quote
# markers, one list marker. Quoted and listed fences count too, unlike
# media_guard's _FENCE (which errs the other way on purpose). Each marker
# takes the blanks after it, so no two blank runs touch: two would split a
# long run of blanks every way before failing (seconds on a long line).
_PREFIX = re.compile(r"^[ \t]*(?:>[ \t]*)*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?")
_FENCE = re.compile(r"`{3,}|~{3,}")

# Addresses a backslash must not enter: an autolink, an inline link's
# destination. Linear: each alternative is one class repeat after a fixed
# prefix, and the destination's class stops at the next parenthesis.
_URL = re.compile(
    r"<[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\s<>]*>"
    r"|\]\([^\s()]*\)"
)

# Inline HTML whose inside is left as written, open and close on one line
# (paired by position, not one regex, so a line of unclosed tags stays
# linear). A tag's attributes are capped so no match scans a whole line.
_CODE_OPEN = re.compile(r"<code\b[^>\n]{0,200}>", re.IGNORECASE)
_CODE_CLOSE = re.compile(r"</code\s*>", re.IGNORECASE)

# Block HTML whose inside is left as written; it may span lines. A block
# opens only where a block can start: at the start of a line, after its
# quote or list prefix (where footer.py writes every footer). A tag
# named mid-sentence or in a code span ("the `<footer>` component") is
# text, and must not leave the rest of the message unescaped. Only a
# ``<pre>`` carries to later lines, and only while its quote lasts:
# aipager opens and closes every footer on one line.
_BLOCK_OPEN = re.compile(r"<(footer|pre)(?=[\s>])[^>\n]{0,200}>", re.IGNORECASE)
_BLOCK_CLOSE = {"footer": re.compile(r"</footer\s*>", re.IGNORECASE),
                "pre": re.compile(r"</pre\s*>", re.IGNORECASE)}

# A `$` with no backslash right before it, or with an even run of them
# (each pair is an escaped backslash, so the `$` itself is unescaped).
_BARE_DOLLAR = re.compile(r"(?<!\\)((?:\\\\)*)\$")


def _escape_plain(text: str) -> str:
    return _BARE_DOLLAR.sub(r"\1\\$", text) if "$" in text else text


def _escape_spans(line: str, kept: list[tuple[int, int]]) -> str:
    """*line* with every bare ``$`` escaped outside the *kept* spans."""
    out: list[str] = []
    pos = 0
    for start, end in sorted(kept):
        if end <= pos:
            continue
        start = max(start, pos)
        out.append(_escape_plain(line[pos:start]))
        out.append(line[start:end])
        pos = end
    out.append(_escape_plain(line[pos:]))
    return "".join(out)


def _inline_raw(line: str) -> list[tuple[int, int]]:
    """``<code>...</code>`` spans of *line*, each open tag paired with the
    first close tag after it."""
    if "<" not in line:
        return []
    closes = [(m.start(), m.end()) for m in _CODE_CLOSE.finditer(line)]
    spans: list[tuple[int, int]] = []
    ci = 0
    for m in _CODE_OPEN.finditer(line):
        if spans and m.start() < spans[-1][1]:
            continue
        while ci < len(closes) and closes[ci][0] < m.end():
            ci += 1
        if ci == len(closes):
            break
        spans.append((m.start(), closes[ci][1]))
        ci += 1
    return spans


def _escape_line(line: str, block: str | None) -> tuple[str, str | None]:
    """One non-fenced line, and the raw HTML block still open after it.

    *block* is the raw block (``pre``) open when the line starts: its
    inside is kept up to its closing tag. A ``footer`` left open keeps
    the rest of its line and ends there."""
    kept: list[tuple[int, int]] = []
    if block is None:
        opened = _BLOCK_OPEN.match(line, _PREFIX.match(line).end())
        if opened is not None:
            kept.append((opened.start(), opened.end()))
            block = opened.group(1).lower()
            pos = opened.end()
    else:
        pos = 0
    if block is not None:
        close = _BLOCK_CLOSE[block].search(line, pos)
        if close is None:
            kept.append((pos, len(line)))
            return _escape_spans(line, kept), block if block == "pre" else None
        kept.append((pos, close.end()))
        block = None
    kept.extend(_code_spans(line))
    kept.extend(_inline_raw(line))
    kept.extend((m.start(), m.end()) for m in _URL.finditer(line))
    return _escape_spans(line, kept), None


def escape_dollars(markdown: str) -> str:
    """*markdown* with every ``$`` escaped as ``\\$``, outside code, raw
    HTML blocks and link addresses.

    Idempotent, and a no-op on text without ``$``.
    """
    if not markdown or "$" not in markdown:
        return markdown
    lines = markdown.split("\n")
    # The open fence's marker and the quote depth it opened at: a fence in
    # a quote ends with the quote, and a quoted fence line inside a
    # top-level fence is that fence's text, not its end.
    fence: tuple[str, int] | None = None
    block: str | None = None
    block_depth = 0
    for i, line in enumerate(lines):
        prefix = _PREFIX.match(line)
        depth = prefix.group(0).count(">")
        m = _FENCE.match(line, prefix.end())
        if m and m.group(0)[0] == "`" and "`" in line[m.end():]:
            m = None                  # an inline code span, not a fence
        if block is not None and depth < block_depth:
            block = None              # the quote holding the <pre> ended
        if fence is not None:
            marker, fence_depth = fence
            if depth < fence_depth:
                fence = None          # the quote holding it ended
            else:
                if m and depth == fence_depth and m.group(0)[0] == marker[0] \
                        and len(m.group(0)) >= len(marker) \
                        and not line[m.end():].strip():
                    fence = None
                continue
        if m and block is None:
            fence = (m.group(0), depth)
            continue
        if "$" in line or "<" in line or block is not None:
            was_open = block is not None
            lines[i], block = _escape_line(line, block)
            if block is not None and not was_open:
                block_depth = depth
    return "\n".join(lines)
