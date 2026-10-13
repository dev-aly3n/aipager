"""The served copy of the page's script and styles, without comments or
indentation (roadmap 8.117).

The source keeps every comment: the page is read and reviewed as Python
string modules, and the comments explain it. The phone gets a copy with
the comments, the leading indentation and the blank lines taken out,
built once at import, which keeps the page well under its byte budgets.

Conservative on purpose. Nothing inside a string, a template literal, a
regular expression or a CSS ``url()`` is touched; a style sheet's comment
with code after it on its line stays (``a/* x */b`` is not ``a b`` in
CSS), while a script's becomes the blank it counts as; and when
the scanner meets anything it can't place for certain (a string running
into a line end, a literal left open, a line break other than ``\n``, a
backslash in a style sheet's code), the source is served unchanged.

A few rare guesses are left: a ``/`` right after ``)``, ``}``, a
spread keyword (``...await``) or a number ending in a dot (``1.``) is read
the simple way, so a regular expression there could be misread.
tests/test_miniapp_strip.py reads the real served script with Node's own
parser on every run and fails on any difference in its tokens, so a
source edit that brings one in is caught.
"""

from __future__ import annotations

import bisect
import re


class _Unsure(Exception):
    """The scanner can't place something for certain (at the offset it
    carries): serve the source."""


# A ``/`` after one of these words starts a regular expression, never a
# division (``return /x/.test(s)``).
_REGEX_AFTER_WORD = frozenset({
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
})
_WORD_CHAR = re.compile(r"[\w$]")
_WORD_BEFORE = re.compile(r"[\w$]+$")
# A regular expression literal from its opening ``/``: classes may hold a
# ``/``, escapes anything but a line end.
_REGEX_BODY = re.compile(r"/(?:[^/\\\[\n]|\\[^\n]|\[(?:[^\]\\\n]|\\[^\n])*\])+/[A-Za-z]*")
_STRING = {"'": re.compile(r"'(?:[^'\\\n]|\\(?:\r\n|[\s\S]))*'"),
           '"': re.compile(r'"(?:[^"\\\n]|\\(?:\r\n|[\s\S]))*"')}
# The next character that can start or end something the scanner tracks.
_JS_NEXT = re.compile(r"[/'\"`{}]")
_TEMPLATE_NEXT = re.compile(r"[`\\]|\$\{")
_CSS_NEXT = re.compile(r"/\*|['\"]|url\(|\\", re.I)
_CSS_URL = re.compile(r"url\((?:[^)\\'\"\n]|\\[^\n])*\)", re.I)


def _js_spans(src: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """``(literals, comments)``: the ``[start, end)`` of every string,
    template and regular expression literal, and of every comment."""
    if any(c in src for c in "\r\u2028\u2029"):
        # Line breaks a line comment ends at too: serve such a script as
        # written rather than scan lines by "\n" alone.
        raise _Unsure(0)
    literals: list[tuple[int, int]] = []
    comments: list[tuple[int, int]] = []
    # Open template literals: the brace depth of the ``${`` code each one
    # is waiting to close.
    templates: list[int] = []
    depth = 0
    i, n = 0, len(src)
    while i < n:
        m = _JS_NEXT.search(src, i)
        if m is None:
            break
        j = m.start()
        c = src[j]
        if c in "'\"":
            s = _STRING[c].match(src, j)
            if s is None:
                raise _Unsure(j)
            literals.append((j, s.end()))
            i = s.end()
        elif c == "`":
            i = _template(src, j + 1, literals, templates, depth)
            if templates and templates[-1] == depth:
                depth += 1  # the template stopped at a ``${``
        elif c == "{":
            depth += 1
            i = j + 1
        elif c == "}":
            if templates and templates[-1] == depth - 1:
                templates.pop()
                depth -= 1
                i = _template(src, j + 1, literals, templates, depth)
                if templates and templates[-1] == depth:
                    depth += 1
            else:
                depth -= 1
                if depth < 0:
                    raise _Unsure(j)
                i = j + 1
        elif src.startswith("//", j):
            end = src.find("\n", j)
            end = n if end == -1 else end
            comments.append((j, end))
            i = end
        elif src.startswith("/*", j):
            end = src.find("*/", j + 2)
            if end == -1:
                raise _Unsure(j)
            comments.append((j, end + 2))
            i = end + 2
        else:  # a lone "/": a regular expression or a division
            if _starts_regex(src, literals, comments, j):
                r = _REGEX_BODY.match(src, j)
                if r is None:
                    raise _Unsure(j)
                literals.append((j, r.end()))
                i = r.end()
            else:
                i = j + 1
    if templates or depth:
        raise _Unsure(len(src))
    return literals, comments


def _template(src: str, i: int, literals, templates, depth) -> int:
    """Scan template text from *i* (just past a backtick or a ``}``) to
    its closing backtick or its next ``${``; return where code resumes."""
    start = i
    while True:
        m = _TEMPLATE_NEXT.search(src, i)
        if m is None:
            raise _Unsure(start)
        if m.group() == "\\":
            i = m.end() + 1
            continue
        literals.append((start, m.end()))
        if m.group() == "${":
            templates.append(depth)
        return m.end()


def _starts_regex(src: str, literals, comments, slash: int) -> bool:
    """Whether the ``/`` at *slash* opens a regular expression: true after
    an operator, an opening bracket or a keyword, false after a name, a
    number, a closing bracket, a literal, ``++``/``--`` or a property
    named like a keyword (``x.in``). Blanks and *comments* before it
    don't count."""
    k = slash - 1
    c = len(comments) - 1
    while k >= 0:
        if src[k] in " \t\r\n":
            k -= 1
        elif c >= 0 and comments[c][0] <= k < comments[c][1]:
            k = comments[c][0] - 1
            c -= 1
        else:
            break
    if k < 0:
        return True
    ch = src[k]
    if ch in ")]}":
        return False
    if ch in "'\"`/" and literals and literals[-1][1] == k + 1:
        return False                    # right after a string, template or regex
    if ch in "+-" and k > 0 and src[k - 1] == ch:
        return False                    # i++ / 2 (a regex can't follow ++)
    if _WORD_CHAR.match(ch):
        # The longest keyword has 10 letters, so 12 are enough to look back
        # (a longer name, cut to 12, is no keyword either).
        word = _WORD_BEFORE.search(src, max(0, k - 11), k + 1)
        if word is None or word.group() not in _REGEX_AFTER_WORD:
            return False
        b = word.start() - 1
        while b >= 0 and src[b] in " \t":
            b -= 1
        return not (b >= 0 and src[b] == ".")  # x.in / 2 is a property
    return True


def _css_spans(src: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """``(literals, comments)`` of a style sheet: strings and unquoted
    ``url()`` values, and comments."""
    literals: list[tuple[int, int]] = []
    comments: list[tuple[int, int]] = []
    i = 0
    while True:
        m = _CSS_NEXT.search(src, i)
        if m is None:
            break
        j = m.start()
        if m.group() == "\\":
            raise _Unsure(j)            # an escape in a name: serve it as written
        if m.group() == "/*":
            end = src.find("*/", j + 2)
            if end == -1:
                raise _Unsure(j)
            comments.append((j, end + 2))
            i = end + 2
        elif m.group() in "'\"":
            s = _STRING[m.group()].match(src, j)
            if s is None:
                raise _Unsure(j)
            literals.append((j, s.end()))
            i = s.end()
        else:
            u = _CSS_URL.match(src, j)
            # A quoted url("...") is a string the next search finds.
            i = j + 4 if u is None else u.end()
            if u is not None:
                literals.append((j, u.end()))
    return literals, comments


def _rebuild(src: str, literals, comments, *, inline_is_blank: bool) -> str:
    """*src* without the comments that stand alone or end a line, without
    leading and trailing blanks on lines that start and end in code, and
    without the lines that leaves empty. *inline_is_blank* (a script): a
    one-line comment with code after it becomes the blank it counts as,
    a space only where nothing else keeps its neighbours apart."""
    lines = src.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    # (start, end, what takes its place) for each comment that goes.
    drop = []
    for s, e in comments:
        nl = src.find("\n", e)
        tail = src[e:len(src) if nl == -1 else nl]
        if tail.strip(" \t\r") == "":
            drop.append((s, e, ""))
        elif inline_is_blank and "\n" not in src[s:e]:
            apart = s > 0 and src[s - 1] in " \t" or src[e] in " \t"
            drop.append((s, e, "" if apart else " "))
    lit_starts = [s for s, _ in literals]

    def inside_literal(pos: int) -> bool:
        k = bisect.bisect_right(lit_starts, pos) - 1
        return k >= 0 and literals[k][0] < pos < literals[k][1]

    drop_starts = [s for s, _, _ in drop]
    out: list[str] = []
    for line, ls in zip(lines, starts):
        le = ls + len(line)
        # The kept text of this line: the dropped comments cut out.
        pieces, pos = [], ls
        k = bisect.bisect_right(drop_starts, ls) - 1
        k = max(k, 0)
        while k < len(drop) and drop[k][0] < le:
            s, e, blank = drop[k]
            if e > ls:
                pieces.append(src[pos:max(pos, s)])
                pieces.append(blank)
                pos = max(pos, min(e, le))
            k += 1
        pieces.append(src[pos:le])
        text = "".join(pieces)
        if not inside_literal(ls):
            text = text.lstrip(" \t")
        if not inside_literal(le):
            text = text.rstrip(" \t\r")
        if text == "" and not inside_literal(ls) and not inside_literal(le):
            continue
        out.append(text)
    return "\n".join(out)


def strip_js(src: str) -> str:
    """The script the phone gets: *src* without its comments, indentation
    and blank lines, or *src* itself when anything is in doubt."""
    try:
        literals, comments = _js_spans(src)
    except _Unsure:
        return src
    return _rebuild(src, literals, comments, inline_is_blank=True)


def strip_css(src: str) -> str:
    """The style sheet the phone gets, like :func:`strip_js`."""
    try:
        literals, comments = _css_spans(src)
    except _Unsure:
        return src
    return _rebuild(src, literals, comments, inline_is_blank=False)
