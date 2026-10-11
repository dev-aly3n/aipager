"""The i18n API: ``Msg``, ``Plural``, ``Safe`` and ``Tr``.

English is written inline next to its key, so English output is exactly
the English in the code and no catalog is read for it. Any other language
reads ``aipager/i18n/locales/<code>.jsonl`` and falls back to the English
for a key it lacks (never a raw key).

A ``Tr`` renders one message for one output kind:

- ``html`` (Telegram HTML): parameters are HTML-escaped;
- ``text`` (plain text, toasts): parameters go in as they are;
- ``md`` (the busy card's Markdown): parameters get the card's escaping.

Code never pre-escapes a parameter; a :class:`Safe` value is already
escaped for its target and is never escaped again. Placeholders are
``{name}`` only (``{{`` and ``}}`` for literal braces); any other brace
form is refused, a missing parameter raises ``KeyError`` and an unused
one ``ValueError``, so a typo cannot ship silently.

Right-to-left languages (fa, ar) only: every line starts with U+200F
(right after a Markdown block marker such as ``# `` or ``- `` when the
line has one), each text parameter is wrapped in U+2068...U+2069, and
nothing invisible is ever placed inside ``<code>``, ``<pre>``, a tag, a
:class:`Span` or a :class:`Command` (a copied command must paste clean).
Numeric parameters (``int``/``float``, not ``bool``) and
``Tr.num``/``Tr.clock`` get the language's own digits there (and, through
``Tr.num``, its thousands separator and percent sign); string parameters
(session names, versions, paths) never do. English gets none of this.

Two parameter types say what a value is, so it is never broken:

- :class:`Span`: text already formatted for its target (a card's code
  span, an HTML ``<code>`` fragment built in code). Never escaped, never
  digit-converted, isolated whole in a right-to-left language.
- :class:`Command`: a whole slash command or @mention (``/x1``,
  ``@bob``). Escaped like text, never digit-converted, isolated whole
  while :data:`ISOLATE_COMMANDS` is on, so no mark ever lands between the
  ``/`` and the name.

A message that carries Claude's text takes the direction of that text,
not the chat's (multilingual design 5.7, rule 5): :meth:`Tr.directed`
gives the ``Tr`` that renders aipager's own lines of such a message in a
given direction. A right-to-left language rendered left to right puts no
U+200F at line starts; each of its right-to-left phrases is wrapped in
U+2067...U+2069 so it still reads right to left inside the line, and a
line whose first strong character outside the isolates is not left to
right gets U+200E, so it still aligns left. Parameters stay isolated and
numbers keep the language's digits. A
left-to-right language ignores the direction, so English output never
changes.

The ``Tr`` is always passed explicitly, never through a context variable,
so a task started in one chat can never carry its language to another.
"""

from __future__ import annotations

import html as _html
import json
import logging
import math
import re
import unicodedata
from pathlib import Path

from aipager.clockfmt import elapsed as _elapsed
from aipager.i18n import locales as _locales
from aipager.i18n import plurals as _plurals

log = logging.getLogger(__name__)

__all__ = ["Command", "ISOLATE_COMMANDS", "Msg", "NEVER_TRANSLATE", "Plural", "Safe",
           "Span", "Tr", "get", "has_translation", "pseudo"]

KEY_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")

#: Names that are never translated (design section 4.1): products, Claude
#: Code's tool names and the model aliases. The pseudo locale keeps them
#: verbatim and the glossary check requires them spelled exactly.
NEVER_TRANSLATE: tuple[str, ...] = (
    "Claude Code", "Claude", "Anthropic", "aipager", "Telegram", "Mini App",
    "GitHub", "PyPI",
    "Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Grep",
    "Glob", "WebFetch", "WebSearch", "Agent", "AskUserQuestion", "TodoWrite",
    "opusplan", "OpusPlan", "opus", "Opus", "sonnet", "Sonnet", "haiku",
    "Haiku", "fable", "Fable",
)

_LRM_RTL = "\u200f"  # RIGHT-TO-LEFT MARK
_LRM = "\u200e"      # LEFT-TO-RIGHT MARK
#: Where a left-to-right line of a right-to-left language MAY get U+200E,
#: decided once the line is whole (:func:`_ltr_render`). A
#: noncharacter: never in a message, never sent.
_LINE_SLOT = "\ufdd0"
_FSI = "\u2068"      # FIRST STRONG ISOLATE
_PDI = "\u2069"      # POP DIRECTIONAL ISOLATE

_DIGITS = {
    "arabext": str.maketrans("0123456789", "۰۱۲۳۴"
                                           "۵۶۷۸۹"),
    "arab": str.maketrans("0123456789", "٠١٢٣٤"
                                        "٥٦٧٨٩"),
}

_LIST_SEPARATORS = {"ar": "، ", "fa": "، ", "zh-Hans": "、"}


class Msg(str):
    """A message key with its English. Equal to (and usable as) the
    English string everywhere: ``==``, f-strings, dict keys, sets."""

    def __new__(cls, key: str, english: str, *, note: str = "",
                eg: dict | None = None):
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ValueError(f"bad message key: {key!r}")
        if not isinstance(english, str):
            raise TypeError(f"{key}: the English must be a str")
        obj = super().__new__(cls, english)
        obj.key = key
        obj.note = note
        obj.eg = dict(eg) if eg else {}
        return obj


class Plural:
    """A counted message: the English ``one`` and ``other`` forms. ``{n}``
    is the count; other placeholders work as in :class:`Msg`."""

    __slots__ = ("key", "one", "other", "note", "eg")

    def __init__(self, key: str, *, one: str, other: str, note: str = "",
                 eg: dict | None = None):
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ValueError(f"bad message key: {key!r}")
        self.key = key
        self.one = one
        self.other = other
        self.note = note
        self.eg = dict(eg) if eg else {}

    def __repr__(self) -> str:
        return f"Plural({self.key!r}, one={self.one!r}, other={self.other!r})"


class Safe(str):
    """Text already escaped for its target. Never escaped or isolated
    again when used as a parameter."""


class Span(str):
    """A piece already formatted for its target and kept whole: a card's
    code span (``animation._mono(...)``) or an HTML ``<code>`` fragment
    built in code. Never escaped and never digit-converted; in a
    right-to-left language wrapped in U+2068...U+2069 as a whole when it
    sits outside a tag or code element, so nothing invisible ever lands
    inside it."""


class Command(str):
    """A whole slash command or @mention (``"/x1"``, ``"@bob"``). Escaped
    for the output like any text, never digit-converted, and in a
    right-to-left language isolated as ONE unit while
    :data:`ISOLATE_COMMANDS` is on. Patterns never glue a ``/`` or ``@``
    to a placeholder (``check`` refuses ``/{x}`` and ``@{x}``), so the
    mark can never split a command."""


#: Whether a :class:`Command` is wrapped in U+2068...U+2069 in a
#: right-to-left language. The one switch to turn off if a Telegram app
#: stops treating ``/x1`` next to an isolate as a tappable command
#: (multilingual design 5.7, rule 7).
ISOLATE_COMMANDS = True

#: A Markdown block marker at a line start. A right-to-left line that
#: starts with one gets its mark right AFTER the marker: the paragraph's
#: direction comes from its first strong character, and the marker must
#: stay first or the heading, quote or list item is lost.
_BLOCK_MARKER_AFTER_RLM = re.compile(
    "(?m)^\u200f(#{1,6} |> |[-*+] |[0-9]{1,9}\\. )")
_BLOCK_MARKER_AFTER_SLOT = re.compile(
    "(?m)^\ufdd0(#{1,6} |> |[-*+] |[0-9]{1,9}\\. )")

_ISOLATE_OPEN = frozenset("\u2066\u2067\u2068")
_STRONG_R = frozenset(("R", "AL"))
_HTML_SKIP = re.compile(r"<[^>]*>|&(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);")


def _opens_left_to_right(line: str, kind: str) -> bool:
    """Whether *line* already starts left to right on its own: its first
    strong character is left to right both outside its isolates (the way
    a paragraph's direction is found) and counting what they hold (for an
    app that does not skip isolates). HTML tags and entities are not
    shown text and are skipped."""
    if kind == "html":
        line = _HTML_SKIP.sub("", line)
    depth = 0
    outside = inner = None
    for ch in line:
        if ch in _ISOLATE_OPEN:
            depth += 1
            continue
        if ch == _PDI:
            depth = max(depth - 1, 0)
            continue
        bidi = unicodedata.bidirectional(ch)
        strong = "L" if bidi == "L" else "R" if bidi in _STRONG_R else None
        if strong is None:
            continue
        if inner is None:
            inner = strong
        if not depth and outside is None:
            outside = strong
        if inner is not None and outside is not None:
            break
    return outside == "L" and inner == "L"


_RLI = "\u2067"  # RIGHT-TO-LEFT ISOLATE
_PHRASE_PART = frozenset(("R", "AL", "NSM"))
_OPENS = {"(": ")", "[": "]", "{": "}", "\u00ab": "\u00bb"}
#: What ends a right-to-left phrase: a line end, the " · " every line
#: uses between its parts, and a direction mark (one a piece rendered
#: before put there to start its own part).
_PHRASE_STOP = frozenset(("\n", _LINE_SLOT, "\u00b7", _LRM, "\u200f"))
_SENTENCE_END = frozenset(".!?\u061f\u2026")
_DIGIT = re.compile("[0-9\u0660-\u0669\u06f0-\u06f9]")
_NUMBER_PART = frozenset("0123456789\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667"
                         "\u0668\u0669\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7"
                         "\u06f8\u06f9\u066b\u066c.,:%\u066a\u061c")
_CODE_REGION = re.compile(r"<(code|pre)\b[^>]*>.*?</\1\s*>", re.S | re.I)


def _units(text: str, kind: str) -> list[tuple[str, str]]:
    """*text* as ``(kind, text)`` units for :func:`_isolate_rtl_phrases`:
    ``"stop"`` (a tag, a code element, a line end, a slot, the middle
    dot, a direction mark), ``"opaque"`` (an isolate with all it holds:
    one neutral unit) or ``"char"``. An HTML entity needs no unit of its
    own: it holds no right-to-left letter, so no phrase starts or ends in
    it."""
    units: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if kind == "html" and ch == "<":
            m = _CODE_REGION.match(text, i)
            end = m.end() if m else text.find(">", i) + 1
            if end <= i:
                end = n
            units.append(("stop", text[i:end]))
            i = end
            continue
        if ch in _ISOLATE_OPEN:
            depth, j = 0, i
            while j < n:
                if text[j] in _ISOLATE_OPEN:
                    depth += 1
                elif text[j] == _PDI:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            units.append(("opaque", text[i:j + 1]))
            i = j + 1
            continue
        units.append(("stop" if ch in _PHRASE_STOP else "char", ch))
        i += 1
    return units


def _isolate_rtl_phrases(text: str, kind: str) -> str:
    """Wrap every right-to-left phrase of a left-to-right rendering in
    U+2067...U+2069, so it reads right to left on its own inside a line
    that reads left to right: its words in their order, its numbers and
    brackets with them, and nothing after it (a clock, a cost) pulled
    into it. A phrase runs from its first right-to-left letter to its
    last one (or a bracket it opened and closed), across numbers,
    isolated parameters and a name left in Latin letters ("Telegram"),
    never across a tag, a line end, a " · " or a direction mark; a number
    right before it is part of it."""
    units = _units(text, kind)
    out: list[str] = []
    i, n = 0, len(units)
    done = 0
    while i < n:
        what, piece = units[i]
        if what != "char" or unicodedata.bidirectional(piece) not in _STRONG_R:
            i += 1
            continue
        start = i
        k = start
        while k > done and units[k - 1][0] == "char" and units[k - 1][1].isspace():
            k -= 1
        m = k
        while m > done and units[m - 1][0] == "char" and units[m - 1][1] in _NUMBER_PART:
            m -= 1
        if m < k and any(_DIGIT.match(units[x][1]) for x in range(m, k)):
            if m > done and units[m - 1][0] == "char" and units[m - 1][1] in "+-$":
                m -= 1
            start = m
        end = i + 1
        stack: list[str] = []
        j = i + 1
        while j < n:
            what, piece = units[j]
            if what == "stop":
                break
            if what == "char":
                bidi = unicodedata.bidirectional(piece)
                if bidi in _PHRASE_PART:
                    end = j + 1
                elif piece in _OPENS:
                    stack.append(_OPENS[piece])
                elif stack and piece == stack[-1]:
                    stack.pop()
                    if not stack:
                        end = j + 1
            j += 1
        # A sentence's own full stop ends it on its right-to-left side.
        while end < n and units[end][0] == "char" and units[end][1] in _SENTENCE_END:
            end += 1
        out.extend(p for _w, p in units[done:start])
        out.append(_RLI)
        out.extend(p for _w, p in units[start:end])
        out.append(_PDI)
        done = i = end
    out.extend(p for _w, p in units[done:])
    return "".join(out)


def _ltr_render(text: str, kind: str) -> str:
    """Finish a left-to-right rendering of a right-to-left language: its
    right-to-left phrases isolated (:func:`_isolate_rtl_phrases`), then
    every :data:`_LINE_SLOT` resolved, a U+200E where the rest of the
    line would not start left to right on its own, nothing where it
    already does or the line is blank."""
    text = _BLOCK_MARKER_AFTER_SLOT.sub("\\1" + _LINE_SLOT, text)
    text = _isolate_rtl_phrases(text, kind)
    if _LINE_SLOT not in text:
        return text
    pieces = text.split(_LINE_SLOT)
    out = [pieces[0]]
    for piece in pieces[1:]:
        line = piece.split("\n", 1)[0]
        if line.strip() and not _opens_left_to_right(line, kind):
            out.append(_LRM)
        out.append(piece)
    return "".join(out)


# ---- placeholders -------------------------------------------------------

_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}|[{}]")


def parse(pattern: str) -> list:
    """``pattern`` as a list of literal strings and ``(name,)`` tuples.
    Raises ``ValueError`` for any brace that is not ``{name}``, ``{{`` or
    ``}}`` (``{0}``, ``{x.y}``, ``{x!r}``, ``{x:>4}``, a lone brace)."""
    pieces: list = []
    buf: list[str] = []
    pos = 0
    for m in _TOKEN.finditer(pattern):
        buf.append(pattern[pos:m.start()])
        tok = m.group(0)
        if tok == "{{":
            buf.append("{")
        elif tok == "}}":
            buf.append("}")
        elif m.group(1):
            pieces.append("".join(buf))
            buf = []
            pieces.append((m.group(1),))
        else:
            raise ValueError(f"bad placeholder near {pattern[m.start():m.start() + 12]!r} "
                             f"in {pattern!r}")
        pos = m.end()
    buf.append(pattern[pos:])
    pieces.append("".join(buf))
    return pieces


def placeholders(pattern: str) -> list[str]:
    """The placeholder names of *pattern*, in order, without repeats."""
    seen: list[str] = []
    for piece in parse(pattern):
        if isinstance(piece, tuple) and piece[0] not in seen:
            seen.append(piece[0])
    return seen


def _md_escape(text: str) -> str:
    # The card's own escaper, from where it lives (never a copy).
    from aipager.bot.animation import _md_escape as card_escape
    return card_escape(text)


def _escape(kind: str, text: str) -> str:
    if kind == "html":
        return _html.escape(text)
    if kind == "md":
        return _md_escape(text)
    if kind == "text":
        return text
    raise ValueError(f"unknown output kind: {kind!r}")


_OPEN_CODE_AT_END = re.compile(r"<(code|pre)\b[^>]*>\Z")
_CLOSE_CODE_AT_START = re.compile(r"\A</(code|pre)\s*>")
_TAG_NAME = re.compile(r"\s*(/?)\s*([A-Za-z][A-Za-z0-9-]*)")


class _Scan:
    """Where we are in the HTML of a pattern: inside a tag, or inside
    ``<code>``/``<pre>``."""

    def __init__(self) -> None:
        self.tag: list[str] | None = None
        self.code = 0

    @property
    def plain(self) -> bool:
        return self.tag is None and self.code == 0

    def feed(self, text: str, *, mark: str) -> str:
        out: list[str] = []
        for ch in text:
            if self.tag is not None:
                if ch == ">":
                    m = _TAG_NAME.match("".join(self.tag))
                    if m and m.group(2).lower() in ("code", "pre"):
                        self.code += -1 if m.group(1) else 1
                        self.code = max(self.code, 0)
                    self.tag = None
                else:
                    self.tag.append(ch)
                out.append(ch)
                continue
            if ch == "<":
                self.tag = []
            out.append(ch)
            if ch == "\n" and mark and self.code == 0:
                out.append(mark)
        return "".join(out)


class Tr:
    """Renders messages in one language. Get one with :func:`get` or a
    resolver (``for_chat``, ``for_update``...), never build one."""

    def __init__(self, loc: _locales.Locale, *, ltr: bool = False):
        self._loc = loc
        self.code = loc.code
        #: The direction this ``Tr`` renders in: the language's, unless
        #: :meth:`directed` made it a left-to-right one.
        self.rtl = loc.rtl and not ltr
        self._pseudo = loc.code in _locales.PSEUDO

    def __repr__(self) -> str:
        ltr = "-ltr" if self._loc.rtl and not self.rtl else ""
        return f"<Tr {self.code}{ltr}>"

    @property
    def language_rtl(self) -> bool:
        """Whether the LANGUAGE is written right to left, whatever
        direction this ``Tr`` renders in."""
        return self._loc.rtl

    def directed(self, rtl: bool | None) -> Tr:
        """This language rendered in the direction *rtl* (multilingual
        design 5.7, rule 5): for a message that carries Claude's text,
        whose direction is that text's. ``None`` keeps the language's
        own. A left-to-right language is returned as it is: its lines
        carry no mark either way, so English never changes."""
        if not self._loc.rtl or rtl is None or rtl:
            return _base(self._loc)
        tr = _LTR_TRS.get(self._loc.code)
        if tr is None:
            tr = _LTR_TRS[self._loc.code] = Tr(self._loc, ltr=True)
        return tr

    @property
    def mark(self) -> str:
        """U+200F in a right-to-left language, else ``""``: for a line
        built in code from pieces (a /status details line) whose first
        piece is a name, so the line still reads right to left."""
        return _LRM_RTL if self.rtl else ""

    # -- lookup -----------------------------------------------------------

    def _pattern(self, key: str, english: str) -> str:
        if self.code == "en":
            return english
        if self._pseudo:
            return pseudo(english)
        value = _catalog(self.code).get(key)
        return value if isinstance(value, str) else english

    @staticmethod
    def _key_english(msg_or_key, english) -> tuple[str, str]:
        if isinstance(msg_or_key, Msg):
            if english is not None:
                raise TypeError(f"{msg_or_key.key}: a Msg carries its English")
            return msg_or_key.key, str(msg_or_key)
        if not isinstance(msg_or_key, str) or not KEY_RE.match(msg_or_key):
            raise ValueError(f"bad message key: {msg_or_key!r}")
        if not isinstance(english, str):
            raise TypeError(f"{msg_or_key}: the English is required inline")
        return msg_or_key, english

    # -- numbers ----------------------------------------------------------

    def _digits(self, text: str) -> str:
        table = _DIGITS.get(self._loc.digits)
        if table is None:
            return text
        loc = self._loc
        return (text.replace(".", loc.decimal).replace(",", loc.group)
                .replace("%", loc.percent).translate(table))

    def num(self, value) -> str:
        """A number in this language's digits (``1.5`` -> ``۱٫۵`` in fa).
        A string keeps its own shape: ``"$1,234.50"`` -> ``$۱٬۲۳۴٫۵۰``
        and ``"45%"`` -> ``۴۵٪`` in fa, unchanged in a language written
        with 0-9."""
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            text = str(int(value)) if abs(value) < 1e16 else str(value)
        else:
            text = str(value)
        return self._digits(text)

    def clock(self, seconds, unit: str = "s") -> str:
        """The elapsed clock (``4:10``, ``1:23:30``) in this language's
        digits."""
        return self._digits(_elapsed(seconds, unit))

    def join(self, items) -> str:
        return _LIST_SEPARATORS.get(self.code, ", ").join(str(i) for i in items)

    # -- rendering --------------------------------------------------------

    def _fill(self, pattern: str, params: dict, kind: str) -> str:
        pieces = parse(pattern)
        used = {p[0] for p in pieces if isinstance(p, tuple)}
        for name in used:
            if name not in params:
                raise KeyError(name)
        extra = set(params) - used
        if extra:
            raise ValueError(f"unused parameters {sorted(extra)} for {pattern!r}")
        # Isolates and digits follow the LANGUAGE; the line-start mark
        # follows the direction this Tr renders in (see `directed`).
        rtl = self._loc.rtl
        mark = _LRM_RTL if self.rtl else _LINE_SLOT if rtl else ""
        scan = _Scan()
        out: list[str] = [mark] if mark else []
        close_after_tag = False
        for i, piece in enumerate(pieces):
            if not isinstance(piece, tuple):
                text = piece
                if close_after_tag:
                    m = _CLOSE_CODE_AT_START.match(text)
                    if m:
                        out.append(scan.feed(text[:m.end()], mark=mark) + _PDI)
                        text = text[m.end():]
                    close_after_tag = False
                out.append(scan.feed(text, mark=mark))
                continue
            value = params[piece[0]]
            numeric = (isinstance(value, (int, float)) and not isinstance(value, bool))
            if isinstance(value, (Span, Command)):
                text = str(value) if isinstance(value, Span) else _escape(kind, str(value))
                whole = isinstance(value, Span) or ISOLATE_COMMANDS
                if rtl and whole and scan.plain:
                    text = _FSI + text + _PDI
                out.append(text)
                continue
            if isinstance(value, Safe):
                out.append(str(value))
                continue
            if numeric:
                raw = self.num(value) if scan.plain else str(value)
                out.append(_escape(kind, raw))
                continue
            text = _escape(kind, str(value))
            if rtl and scan.tag is None:
                if scan.code == 0:
                    text = _FSI + text + _PDI
                else:
                    prev = out[-1] if out else ""
                    nxt = pieces[i + 1] if i + 1 < len(pieces) else ""
                    m = _OPEN_CODE_AT_END.search(prev)
                    if (m and isinstance(nxt, str)
                            and _CLOSE_CODE_AT_START.match(nxt)):
                        # The whole content of a code span: isolate the
                        # span from outside its tags, never inside.
                        out[-1] = prev[:m.start()] + _FSI + prev[m.start():]
                        close_after_tag = True
            out.append(text)
        text = "".join(out)
        if self.rtl:
            text = _BLOCK_MARKER_AFTER_RLM.sub("\\1" + _LRM_RTL, text)
        elif rtl:
            text = _ltr_render(text, kind)
        return text

    def _render(self, kind: str, msg_or_key, english, params: dict):
        key, eng = self._key_english(msg_or_key, english)
        text = self._fill(self._pattern(key, eng), params, kind)
        return text if kind == "text" else Safe(text)

    def html(self, msg_or_key, english=None, /, **params) -> Safe:
        return self._render("html", msg_or_key, english, params)

    def text(self, msg_or_key, english=None, /, **params) -> str:
        return self._render("text", msg_or_key, english, params)

    def md(self, msg_or_key, english=None, /, **params) -> Safe:
        return self._render("md", msg_or_key, english, params)

    def n(self, plural: Plural, count: int, /, *, kind: str = "html", **params):
        """*plural* for *count*, in the CLDR form this language uses for
        it. ``{n}`` is the count."""
        if not isinstance(plural, Plural):
            raise TypeError("Tr.n takes a Plural")
        if "n" in params:
            raise ValueError("{n} is the count; do not pass it")
        english = plural.one if _plurals.category("en", count) == "one" else plural.other
        if self.code == "en":
            pattern = english
        elif self._pseudo:
            pattern = pseudo(english)
        else:
            forms = _catalog(self.code).get(plural.key)
            form = (forms.get(_plurals.category(self.code, count))
                    if isinstance(forms, dict) else None)
            pattern = form if isinstance(form, str) else english
        # A form may leave the count out where the language names it in a
        # word (Arabic's "one" and "two": وكيل واحد, وكيلان), as check 3
        # allows; the count is then not a parameter of that form.
        if "n" in placeholders(pattern):
            params = {**params, "n": count}
        text = self._fill(pattern, params, kind)
        return text if kind == "text" else Safe(text)


# ---- catalogs -----------------------------------------------------------

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_CATALOGS: dict[str, dict] = {}


def _catalog(code: str) -> dict:
    """The catalog of *code*, read once. A malformed line is skipped with
    a debug log (CI refuses it, check 16); a missing file is empty."""
    cat = _CATALOGS.get(code)
    if cat is not None:
        return cat
    cat = {}
    path = LOCALES_DIR / f"{code}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            key, value = entry["k"], entry["v"]
            if not isinstance(key, str) or not isinstance(value, (str, dict)):
                raise ValueError("k must be a str, v a str or an object")
        except (ValueError, KeyError, TypeError) as e:
            log.debug("i18n: %s line %d skipped (%s)", path.name, n, e)
            continue
        cat[key] = value
    _CATALOGS[code] = cat
    return cat


def has_translation(code: str, key: str) -> bool:
    """Whether *code*'s catalog has an entry for *key* (English always
    has its own text)."""
    if code == "en":
        return True
    return isinstance(code, str) and key in _catalog(code)


_TRS: dict[str, Tr] = {}
#: The left-to-right twins of the right-to-left languages (`Tr.directed`).
_LTR_TRS: dict[str, Tr] = {}


def _base(loc: _locales.Locale) -> Tr:
    tr = _TRS.get(loc.code)
    if tr is None:
        tr = _TRS[loc.code] = Tr(loc)
    return tr


def get(code: str | None) -> Tr:
    """The ``Tr`` for *code*. An unknown code gives English (never
    raises); ``"qps"``/``"qps-rtl"`` give the pseudo locales."""
    loc = _locales.lookup(code) if isinstance(code, str) else None
    if loc is None:
        loc = _locales.LOCALES["en"]
    return _base(loc)


def reset_caches() -> None:
    """Forget every loaded catalog (tests)."""
    _CATALOGS.clear()


# ---- the pseudo locale --------------------------------------------------

_PSEUDO_TABLE = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "àƀçđéƒĝĥîĵķĺ"
    "ɱñöþǫŕšţûṽŵẋ"
    "ýž"
    "ÀƁÇĐÉƑĜĤÎĴĶĹ"
    "ṀÑÖÞǪŔŠŢÛṼŴẊ"
    "ÝŽ",
)

PSEUDO_OPEN, PSEUDO_CLOSE = "⟦", "⟧"


def _protected() -> re.Pattern:
    words = sorted(NEVER_TRANSLATE, key=len, reverse=True)
    return re.compile(
        r"\{\{|\}\}|\{[A-Za-z_][A-Za-z0-9_]*\}"
        r"|<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>"
        r"|<[^>]+>|&(?:[A-Za-z]+|#\d+);"
        r"|(?<![\w/])/[A-Za-z0-9_]+"
        r"|\b(?:" + "|".join(re.escape(w) for w in words) + r")\b",
        re.S,
    )


_PROTECTED = _protected()


def pseudo(english: str) -> str:
    """The pseudo-localized form of *english*: letters become accented
    look-alikes, the whole is wrapped in ``⟦ ⟧`` and padded about 35%.
    Placeholders, tags, entities, slash commands, code and every
    ``NEVER_TRANSLATE`` word stay verbatim."""
    out: list[str] = []
    pos = 0
    for m in _PROTECTED.finditer(english):
        out.append(english[pos:m.start()].translate(_PSEUDO_TABLE))
        out.append(m.group(0))
        pos = m.end()
    out.append(english[pos:].translate(_PSEUDO_TABLE))
    pad = "~" * math.ceil(len(english) * 0.35)
    return PSEUDO_OPEN + "".join(out) + pad + PSEUDO_CLOSE
