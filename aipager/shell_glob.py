"""Can a shell word reach aipager's own folders once bash expands it?

The safety floor's regex rules read a command as text; a glob or a brace
can spell a path in more ways than any regex lists (``[[:a]ipager``,
``{nvim/..,x}/aipager``, ``[a,]{ipager/x,y}``). This module decides the
way bash does instead: it splits the command into words with bash's
quoting, expands each word's braces, normalises the path (``//``, ``/./``,
``name/..``) and asks, segment by segment, whether a glob could match
``.config`` then ``aipager``, or ``.local`` then ``share``/``state`` then
``aipager``. When it cannot tell (a construct it does not model, an
expansion too large to list, a step that may be ``..``) on a word that
could name one of those folders, it says so and the floor refuses.

Best effort like the rest of the floor: a word built from a variable or a
command substitution is not followed (its value is unknown here), and
neither is a ``cd`` before a relative path. The real boundary stays
having no shell.

Linear in the command's length: the lexer reads each character once, the
brace expander lists at most :data:`_MAX_RESULTS` results within a size
budget, and the matcher compares a segment of at most a few items against
fixed names of five to seven characters.

The hook process imports this module: standard library only, and never
``aipager.i18n``.
"""

from __future__ import annotations

import bisect
import re

#: One character standing for a value the floor cannot see: ``$X``,
#: ``${...}``, ``$(...)``, a backtick substitution, an integer sequence.
OPAQUE = "\uf8ff"

#: Characters that mean something to brace expansion or globbing. A quoted
#: or escaped copy of one is moved into a private plane so it never acts
#: as syntax (``"*"`` is a literal star, ``\\]`` a literal ``]``).
_SPECIAL = "{},[]*?!^-()@+:=|\\."
_LIT = {ord(c): chr(0xF0000 + ord(c)) for c in _SPECIAL}
_UNLIT = {chr(0xF0000 + ord(c)): c for c in _SPECIAL}
#: A quoted ``.`` counts as a dot in a path but never makes a brace's
#: ``..`` (``{x".."{a,b}}`` keeps its outer braces): it is put back once
#: the braces are expanded.
_QUOTED_DOT = chr(0xF0000 + ord("."))
_DOTS_BACK = {ord(_QUOTED_DOT): "."}


def _lit(text: str) -> str:
    return text.translate(_LIT)


def _plain(ch: str) -> str:
    """A quoted special character back as itself (for set membership)."""
    return _UNLIT.get(ch, ch)


class Unsure(Exception):
    """The word holds something this module does not model."""


# ---------------------------------------------------------------------------
# Words, as bash splits and quotes them
# ---------------------------------------------------------------------------

_ANSI_ESCAPE_RE = re.compile(
    r"\\(?:x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})"
    r"|([0-7]{1,3})|(.))", re.S)
_ANSI_SIMPLE = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
                "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _ansi_char(m: re.Match) -> str:
    hex2, hex4, hex8, octal, other = m.groups()
    digits, base = ((hex2, 16) if hex2 else (hex4, 16) if hex4
                    else (hex8, 16) if hex8 else (octal, 8) if octal
                    else (None, 0))
    if digits is None:
        return _ANSI_SIMPLE.get(other, other)
    try:
        return chr(int(digits, base))
    except (ValueError, OverflowError):
        return ""


#: Unquoted text up to the next character that changes how words split
#: or quote; seps are blanks and ``; & | < > )``.
_LEX_RE = re.compile("|".join((
    r"(?P<plain>[^\s;&|<>()`'\"\\$]+)",
    r"(?P<sep>[\s;&|<>)]+)",
    r"(?P<sq>'[^']*'?)",
    r"(?P<ansi>\$'(?:[^'\\]|\\[\s\S])*'?)",
    r"(?P<dq>\$?\")",
    r"(?P<esc>\\[\s\S]?)",
    r"(?P<param>\$\{)",
    r"(?P<subst>\$\(|`)",
    r"(?P<var>\$(?:[A-Za-z_]\w*|[0-9@*#?$!-]))",
    r"(?P<dollar>\$)",
    r"(?P<open>\()",
)))
_DQ_RE = re.compile("|".join((
    r"(?P<run>[^\"\\$`]+)",
    r"(?P<end>\")",
    r"(?P<esc>\\[\s\S]?)",
    r"(?P<param>\$\{)",
    r"(?P<subst>\$\(|`)",
    r"(?P<var>\$(?:[A-Za-z_]\w*|[0-9@*#?$!-]))",
    r"(?P<dollar>\$)",
)))
_BRACES_RE = re.compile(r"[{}]")
_PARENS_RE = re.compile(r"[()]")


def _skip(text: str, i: int, pairs: re.Pattern, opener: str) -> int:
    """The index after the bracket that closes the one open before *i*
    (``${...}``, ``$(...)``, ``@(...)``), or the end of *text*."""
    depth = 1
    for m in pairs.finditer(text, i):
        depth += 1 if m.group() == opener else -1
        if depth == 0:
            return m.end()
    return len(text)


def _double_quoted(text: str, i: int, cur: list[str]) -> int:
    """Read ``"..."`` from *i* (after the quote) into *cur*; the index
    after the closing quote."""
    n = len(text)
    while i < n:
        m = _DQ_RE.match(text, i)
        kind, tok, i = m.lastgroup, m.group(), m.end()
        if kind == "run":
            cur.append(_lit(tok))
        elif kind == "end":
            return i
        elif kind == "esc":
            ch = tok[1:]
            if ch in ("$", "`", '"', "\\"):
                cur.append(_lit(ch))
            elif ch != "\n":
                cur.append(_lit(tok))
        elif kind == "param":
            i = _skip(text, i, _BRACES_RE, "{")
            cur.append(OPAQUE)
        elif kind == "subst":
            if tok == "`":
                end = text.find("`", i)
                i = n if end < 0 else end + 1
            else:
                i = _skip(text, i, _PARENS_RE, "(")
            cur.append(OPAQUE)
        elif kind == "var":
            cur.append(OPAQUE)
        else:
            cur.append("$")
    return n


#: The characters that make a word worth expanding: a glob, a brace, a
#: group, or a ``..`` step.
_WORTH_RE = re.compile("[{\\[*?(]|[.%s][.%s]" % (_QUOTED_DOT, _QUOTED_DOT))


def words(command: str) -> list[str]:
    """The words of *command* as bash splits them, quotes applied: a
    quoted or escaped special character moved out of the way (see
    :data:`_SPECIAL`), every expansion one :data:`OPAQUE` character. A
    command substitution ends the word before it and its text is read
    as more words. ``(`` glued to a word (an extended glob or another
    shell's group) stays in the word. Only words holding a glob, brace,
    group or ``..`` are returned: nothing else can be expanded here."""
    out: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        if cur:
            w = "".join(cur)
            if _WORTH_RE.search(w):
                out.append(w)
            cur.clear()

    i, n = 0, len(command)
    while i < n:
        m = _LEX_RE.match(command, i)
        kind, tok, i = m.lastgroup, m.group(), m.end()
        if kind == "plain":
            cur.append(tok)
        elif kind == "sep":
            flush()
        elif kind == "sq":
            cur.append(_lit(tok[1:-1] if len(tok) > 1 and tok.endswith("'")
                            else tok[1:]))
        elif kind == "ansi":
            body = tok[2:-1] if len(tok) > 2 and tok.endswith("'") else tok[2:]
            cur.append(_lit(_ANSI_ESCAPE_RE.sub(_ansi_char, body)))
        elif kind == "dq":
            cur.append("")
            i = _double_quoted(command, i, cur)
        elif kind == "esc":
            if len(tok) == 2 and tok[1] != "\n":
                cur.append(_lit(tok[1]))
        elif kind == "param":
            i = _skip(command, i, _BRACES_RE, "{")
            cur.append(OPAQUE)
        elif kind == "subst":
            # ``$(`` glues an unknown value to the word before it; a
            # backtick opens or closes, so it only ends the word
            if tok != "`":
                cur.append(OPAQUE)
            flush()
        elif kind == "var":
            cur.append(OPAQUE)
        elif kind == "dollar":
            cur.append("$")
        else:  # "("
            last = cur[-1][-1:] if cur and cur[-1] else ""
            if cur and last != "=":
                end = _skip(command, i, _PARENS_RE, "(")
                cur.append(command[i - 1:end])
                i = end
            else:
                flush()
    flush()
    return out


# ---------------------------------------------------------------------------
# Brace expansion, as bash 5 does it
# ---------------------------------------------------------------------------

#: At most this many results per word and this many nested braces;
#: beyond them the word is :class:`Unsure`.
_MAX_RESULTS = 256
_MAX_DEPTH = 32
#: A word longer than this that has braces to expand is not expanded.
_MAX_BRACE_WORD = 8192
_BRACE_SYNTAX_RE = re.compile(r"[{},]|\.\.")
_QUOTED_COMMA_RE = re.compile(chr(0xF0000 + ord(",")))
_SEQ_RE = re.compile(r"([+-]?\d+|[A-Za-z])\.\.([+-]?\d+|[A-Za-z])"
                     r"(?:\.\.[+-]?\d+)?")


def _sequence(amble: str) -> list[str] | None:
    """A ``{x..y}`` / ``{x..y..step}`` amble's results, the literal text
    included (whether bash takes a quoted or odd form as a sequence is
    not modelled, so both readings are listed); None if it is not one.
    Integers become one :data:`OPAQUE` (digits never spell a target);
    letters list every letter between, whatever the step."""
    m = _SEQ_RE.fullmatch(amble)
    if not m:
        return None
    a, b = m[1], m[2]
    literal = "{" + amble + "}"
    if a.isalpha() != b.isalpha():
        return None
    if not a.isalpha():
        return [OPAQUE, literal]
    lo, hi = sorted((ord(a), ord(b)))
    letters = [chr(c) for c in range(lo, hi + 1)]
    if not all(ch.isalpha() for ch in letters):
        raise Unsure("a_letter_sequence_across_punctuation")
    return [*letters, literal]


def _closers(word: str) -> tuple[dict[int, int], list[tuple[int, str]]]:
    """Where bash's brace scan closes each ``{`` of *word*, and the brace
    events (``{``, ``}``, ``,``, ``..``) in order.

    From a ``{``, bash counts inner braces (a ``}`` only lowers a count
    above zero) and closes at the first ``}`` at its own level that comes
    after a comma or a ``..`` (not right before ``}``) at that level;
    earlier ``}`` at its level are plain (``{a}b,c}`` is ``a}b c``). A
    ``{`` with no such ``}`` is plain.

    One pass for every ``{`` at once: open ``{`` sit in groups by level,
    the newest group on top, each holding its distance in levels to the
    group above (the top: its own level). Only the top can be at level
    zero; when a ``}`` leaves its plain members there, the group below
    drops a level and may join it. A member moves from "no comma yet" to
    "comma seen" once and closes once; joins move the smaller group, so
    the pass is O(n log n)."""
    events = [(m.start(), m.group()) for m in _BRACE_SYNTAX_RE.finditer(word)]
    closer: dict[int, int] = {}
    # each group: [gap, members without a comma yet, members with one]
    groups: list[list] = []
    for p, ev in events:
        if ev == "{":
            if groups:
                groups[-1][0] += 1
            groups.append([0, [p], []])
        elif ev == "}":
            if not groups:
                continue
            top = groups[-1]
            if top[0]:
                top[0] -= 1
                continue
            for q in top[2]:
                closer[q] = p
            top[2] = []
            if not top[1]:
                groups.pop()
                if groups:
                    groups[-1][0] -= 1
                continue
            if len(groups) >= 2:
                below = groups[-2]
                below[0] -= 1
                if below[0] == 0:
                    groups.pop()
                    if len(below[1]) < len(top[1]):
                        below[1], top[1] = top[1], below[1]
                    below[1].extend(top[1])
                    below[0] = 0
        elif groups and groups[-1][0] == 0 and (
                ev == "," or word[p + 2:p + 3] != "}"):
            top = groups[-1]
            if len(top[2]) < len(top[1]):
                top[1], top[2] = top[2], top[1]
            top[2].extend(top[1])
            top[1] = []
    return closer, events


def expand_braces(word: str) -> list[str]:
    """*word*'s brace expansion, as bash 5 does it.

    A ``{`` starts one when bash's scan closes it (:func:`_closers`);
    otherwise it is plain and the braces inside it may still expand
    (``{a{b,c}}`` is ``{ab} {ac}``). One that holds an unquoted comma at
    any depth is split at the commas at its own level, none giving one
    item (``{{a,b}c..d}`` is ``ac..d bc..d``); with no comma it is a
    sequence or stays as typed, inner braces and all. Quoted braces,
    commas and dots never count (see :func:`words`), except that bash
    splits on a quoted comma (not an escaped one) when nothing else does:
    both readings are listed.

    Raises :class:`Unsure` past :data:`_MAX_RESULTS` results, a total
    size of four times the word (plus 8 KB), :data:`_MAX_DEPTH` or a word
    longer than :data:`_MAX_BRACE_WORD`."""
    if "{" not in word or "}" not in word or (
            "," not in word and ".." not in word):
        # no ``{`` can open: it needs a ``}`` and a comma or ``..``
        return [word.translate(_DOTS_BACK)]
    if len(word) > _MAX_BRACE_WORD:
        raise Unsure("a_long_word_with_braces")
    closer, events = _closers(word)
    where = [p for p, _ in events]
    opens = [p for p, ev in events if ev == "{"]
    commas = [p for p, ev in events if ev == ","]
    quoted_commas = [m.start() for m in _QUOTED_COMMA_RE.finditer(word)]
    budget = 4 * len(word) + 8192
    ctx = (word, closer, events, where, opens, commas, quoted_commas, budget)
    found = _expand(ctx, 0, len(word), 0, None)
    return [r.translate(_DOTS_BACK) for r in found]


def _any_in(sorted_pos: list[int], lo: int, hi: int) -> bool:
    k = bisect.bisect_right(sorted_pos, lo)
    return k < len(sorted_pos) and sorted_pos[k] < hi


def _items_of(ctx: tuple, p: int, end: int) -> list[tuple[int, int]]:
    """The amble of the brace ``p..end`` split at the commas at its own
    level (bash's scan for ``,``: inner braces counted, a ``}`` lowering
    only a count above zero)."""
    _, _, events, where, *_ = ctx
    out = []
    start, level = p + 1, 0
    k = bisect.bisect_right(where, p)
    while k < len(events) and events[k][0] < end:
        q, ev = events[k]
        k += 1
        if ev == "{":
            level += 1
        elif ev == "}":
            level = max(0, level - 1)
        elif ev == "," and level == 0:
            out.append((start, q))
            start = q + 1
    out.append((start, end))
    return out


def _join(results: list[str], prefix: str, alts: list[str],
          budget: int) -> list[str]:
    count = len(results) * len(alts)
    size = (sum(map(len, results)) * len(alts)
            + len(results) * sum(len(prefix) + len(a) for a in alts))
    if count > _MAX_RESULTS or size > budget:
        raise Unsure("too_many_brace_results")
    return [r + prefix + a for r in results for a in alts]


def _expand(ctx: tuple, lo: int, hi: int, depth: int,
            first: str | None) -> list[str]:
    """The expansion of ``word[lo:hi]``, one piece bash expands on its own
    (the word, a brace item, the rest after a brace). Bash never opens a
    brace at a ``{}`` that starts a piece; whether a quote came first is
    not seen here, so such a ``{}`` is read both ways (*first* says which
    way for the one at *lo*)."""
    word, closer, _, _, opens, commas, quoted_commas, budget = ctx
    if depth > _MAX_DEPTH:
        raise Unsure("braces_nested_too_deep")
    results = [""]
    seg = lo
    k = bisect.bisect_left(opens, lo)
    while k < len(opens) and opens[k] < hi:
        p = opens[k]
        k += 1
        if p < seg:
            continue
        end = closer.get(p)
        if end is None or end >= hi:
            continue
        if p == seg and word[p + 1:p + 2] == "}":
            if p != lo or first is None:
                rest = (_expand(ctx, p, hi, depth + 1, "open")
                        + _expand(ctx, p, hi, depth + 1, "skip"))
                return _join(results, word[seg:p], rest, budget)
            if first == "skip":
                continue
        alts: list[str] = []
        split = _any_in(commas, p, end)
        if split or _any_in(quoted_commas, p, end):
            for a, b in _items_of(ctx, p, end):
                alts.extend(_expand(ctx, a, b, depth + 1, None))
        if not split:
            alts.extend(_sequence(word[p + 1:end]) or [word[p:end + 1]])
        results = _join(results, word[seg:p], alts, budget)
        seg = end + 1
    return _join(results, word[seg:hi], [""], budget)


# ---------------------------------------------------------------------------
# Glob matching against the fixed folder names
# ---------------------------------------------------------------------------

#: Every character of the names below; a bracket is reduced to which of
#: these it can match, in either letter case.
_ALPHABET = frozenset(".configlocalsharestateaipager")
_LETTERS = _ALPHABET - {"."}
#: POSIX classes as (may hold, surely holds) over those characters, in
#: lower case: whether bash folds case (``nocaseglob``, a macOS disk) is
#: not known here, so ``[[:upper:]]`` may match ``a`` and ``[![:upper:]]``
#: may too.
_CLASSES = {
    "alpha": (_LETTERS, _LETTERS), "alnum": (_LETTERS, _LETTERS),
    "word": (_LETTERS, _LETTERS), "lower": (_LETTERS, _LETTERS),
    "upper": (_LETTERS, frozenset()),
    "xdigit": (frozenset("acef"), frozenset("acef")),
    "print": (_ALPHABET, _ALPHABET), "graph": (_ALPHABET, _ALPHABET),
    "ascii": (_ALPHABET, _ALPHABET),
    "punct": (frozenset("."), frozenset(".")),
    "digit": (frozenset(), frozenset()), "space": (frozenset(), frozenset()),
    "blank": (frozenset(), frozenset()), "cntrl": (frozenset(), frozenset()),
}
#: Items of a parsed segment: a star, or the set of name characters one
#: character of the name may be (with whether it can be a leading dot).
_STAR = "*"
_GLOB_RE = re.compile(r"[*?\[(]")
_POSIX_OPEN_RE = re.compile(r"\[[:=.]")
#: The longest name compared here (``.config``, ``aipager``).
_LONGEST = 7


def _member(ch: str, maybe: set[str], sure: set[str]) -> None:
    """Add one bracket character: it may match either case, and surely
    matches only itself."""
    ch = _plain(ch)
    maybe.add(ch.lower())
    sure.add(ch)


def _range(a: str, b: str, maybe: set[str], sure: set[str]) -> None:
    a, b = _plain(a), _plain(b)
    if not ((a.islower() and b.islower()) or (a.isupper() and b.isupper())
            or (a.isdigit() and b.isdigit())):
        raise Unsure("a_range_bash_may_read_by_locale")
    if a > b:
        raise Unsure("a_reversed_range")
    got = {c for c in _ALPHABET if a.lower() <= c <= b.lower()}
    maybe |= got
    if a.islower():
        sure |= got


def _bracket(seg: str, i: int, run_on: bool = False,
             quirks: list[int] | None = None,
             ) -> tuple[frozenset[str], int] | None:
    """The bracket expression opening at ``seg[i] == "["``: the name
    characters it may match and the index after it; None when no ``]``
    closes it (bash then reads ``[`` as itself). ``!`` or ``^`` first
    negates, a ``]`` right after that is a member. A POSIX item that is
    not closed, is empty, names no class or holds more than one character
    is :class:`Unsure` (bash reads some of those as plain characters), and
    so is a plain ``[`` member.

    Bash 5.2 reads ``[[=a=]]x]`` two ways at once: a character the item
    matches ends the bracket at the first ``]``, any other runs on to the
    next ``]`` (``]`` and ``x`` match, alone). Each such place is noted
    in *quirks*; with *run_on* the bracket takes the second reading."""

    def done(end: int) -> tuple[frozenset[str], int]:
        if negate:
            return frozenset(x for x in _ALPHABET if x not in sure), end
        return frozenset(maybe & _ALPHABET), end

    n = len(seg)
    j = i + 1
    negate = j < n and seg[j] in "!^"
    if negate:
        j += 1
    maybe: set[str] = set()
    sure: set[str] = set()
    first = True
    while j < n:
        c = seg[j]
        if c == "]" and not first:
            return done(j + 1)
        first = False
        if c == "[" and j + 1 < n and seg[j + 1] in ":=.":
            kind = seg[j + 1]
            close = seg.find(kind + "]", j + 2)
            if close < 0:
                # no ``:]`` or ``=]`` anywhere after it: bash reads the
                # ``[`` as a plain member (``[[:a]`` holds ``[ : a``); an
                # unclosed ``[.``, or another item after one, it reads in
                # ways not modelled here (``[[.a]ipager`` matches nothing)
                if kind == "." or _POSIX_OPEN_RE.search(seg, j + 2):
                    raise Unsure("an_unclosed_posix_item")
                _member("[", maybe, sure)
                j += 1
                continue
            name = seg[j + 2:close]
            if "]" in name or not name:
                raise Unsure("a_posix_item_that_holds_or_meets_a")
            if kind == ":":
                # an unknown class matches nothing
                may, surely = _CLASSES.get(name, (frozenset(), frozenset()))
                maybe |= may
                sure |= surely
            elif kind == "=" and seg[close + 2:close + 3] == "]" and (
                    "]" in seg[close + 3:]):
                if len(name) != 1:
                    raise Unsure("a_collating_element_of_several_characters")
                if quirks is not None:
                    quirks.append(j)
                _member(name, maybe, sure)
                if run_on:
                    end = seg.find("]", close + 3)
                    span = seg[close + 3:end]
                    if _POSIX_OPEN_RE.search(span):
                        raise Unsure("an_item_in_a_run_on_bracket")
                    for ch in "]" + span:
                        _member(ch, maybe, sure)
                    return done(end + 1)
            elif len(name) == 1:
                _member(name, maybe, sure)
            else:
                raise Unsure("a_collating_element_of_several_characters")
            j = close + 2
            continue
        if c == "[":
            # bash reads ``[[]`` as just ``[``; the floor has refused a
            # bracket holding ``[`` since iteration 2 and keeps doing so
            raise Unsure("a_bracket_holding_an_open_bracket")
        if j + 2 < n and seg[j + 1] == "-" and seg[j + 2] != "]":
            _range(c, seg[j + 2], maybe, sure)
            j += 3
            continue
        _member(c, maybe, sure)
        j += 1
    return None


def _items(seg: str, run_on: bool = False,
           quirks: list[int] | None = None) -> list | None:
    """*seg* as glob items, or None once it holds more than
    :data:`_LONGEST` characters' worth (it can match none of the names).
    An item is :data:`_STAR` or a ``(chars, explicit_dot)`` pair."""
    items: list = []
    count = 0
    i, n = 0, len(seg)
    while i < n:
        c = seg[i]
        if c == "*":
            if not items or items[-1] is not _STAR:
                items.append(_STAR)
            i += 1
            continue
        if c in "()":
            raise Unsure("an_extended_glob_or_group")
        if c == "?":
            items.append((_ALPHABET, False))
            i += 1
        elif c == "[":
            got = _bracket(seg, i, run_on, quirks)
            if got is None:
                items.append((frozenset(), True))
                i += 1
            else:
                items.append((got[0], False))
                i = got[1]
        else:
            items.append((frozenset((_plain(c).lower(),)) & _ALPHABET, True))
            i += 1
        count += 1
        if count > _LONGEST:
            return None
    return items


def _can_match(items: list, name: str, dotglob: bool) -> bool:
    """Whether the glob *items* can match *name* (lower case). A leading
    ``.`` must be matched by a plain ``.`` item unless *dotglob*."""
    n = len(name)
    if name.startswith(".") and not dotglob:
        # bash: the first item must be a plain ``.`` (``*.config``,
        # ``?config`` and ``[.]config`` never match ``.config``)
        if not items or items[0] is _STAR or items[0] != (frozenset("."), True):
            return False
    reach = {0}
    for it in items:
        if it is _STAR:
            reach = set(range(min(reach), n + 1))
            continue
        chars = it[0]
        reach = {p + 1 for p in reach if p < n and name[p] in chars}
        if not reach:
            return False
    return n in reach


_C, _L, _S, _A, _D, _O, _UP = 1, 2, 4, 8, 16, 32, 64
_ALL = _C | _L | _S | _A | _D | _O


def _classify(seg: str, dotglob: bool) -> int:
    """Flags for one path segment: which of the names it can match
    (``.config``, ``.local``, ``share``/``state``, ``aipager``), whether it
    can be ``.`` or ``..`` (a glob only), whether it holds a value the
    floor cannot see. Unsure: every flag."""
    flags = _O if OPAQUE in seg else 0
    if not _GLOB_RE.search(seg):
        low = seg.lower()
        return flags | (_C if low == ".config" else _L if low == ".local"
                        else _S if low in ("share", "state")
                        else _A if low == "aipager" else 0)
    quirks: list[int] = []
    try:
        readings = [_items(seg, False, quirks)]
        if quirks:
            if len(quirks) > 1:
                raise Unsure("two_brackets_bash_reads_two_ways")
            readings.append(_items(seg, True))
    except Unsure:
        return _ALL
    for items in readings:
        if items is None:
            continue
        for name, bit in ((".config", _C), (".local", _L), ("share", _S),
                          ("state", _S), ("aipager", _A)):
            if not flags & bit and _can_match(items, name, dotglob):
                flags |= bit
        if _can_match(items, ".", False) or _can_match(items, "..", False):
            flags |= _D
    return flags


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

REACH, UNSURE, CLEAR = "reach", "unsure", "clear"

#: A word that could name ``.config`` or ``.local``: a ``.`` and, after
#: it, the letters of either name in order, any of them (and the rest)
#: possibly made by a glob, a group or a brace.
_W = r"[*?\[({]"
_NAME_AFTER_DOT_RE = re.compile(
    "|".join(
        "".join(rf"[^{c}{c.upper()}*?\[({{]*(?:{_W}|[{c}{c.upper()}]"
                for c in name) + ")" * len(name)
        for name in ("config", "local")),
)
_ANY_GLOB_RE = re.compile(_W)


def may_name_start(word: str, dotglob: bool) -> bool:
    """Cheap and generous: could some expansion of *word* hold a segment
    matching ``.config`` or ``.local``? Brace results keep the word's
    characters in order, so the letters must appear in order after a
    ``.``; checking from the first ``.`` covers every later one."""
    if dotglob and _ANY_GLOB_RE.search(word):
        return True
    word = word.translate(_DOTS_BACK)
    dot = word.find(".")
    return dot >= 0 and _NAME_AFTER_DOT_RE.match(word, dot + 1) is not None


#: At most this many readings of one path at once (each glob that may be
#: ``.`` or ``..`` triples them); beyond it the path is :data:`UNSURE`.
_MAX_READINGS = 27


def _reaches(node: tuple) -> bool:
    """Whether the path read so far (a linked stack of segment flags, the
    last segment first) ends in ``.config/aipager`` or
    ``.local/{share,state}/aipager``."""
    f1, below = node
    if not f1 & _A or below is None:
        return False
    f2, below2 = below
    if f2 & _C:
        return True
    return bool(f2 & _S and below2 is not None and below2[0] & _L)


def _walk(path: str, dotglob: bool) -> str:
    """Read *path*'s segments in order, keeping every way the path can
    resolve: ``//`` and ``/./`` drop out, ``name/..`` cancels (a ``..``
    after a value the floor cannot see is :data:`UNSURE`), and a glob
    that may be ``.`` or ``..`` (``.*``) is read all three ways. Each
    reading is a linked stack, so a long path costs one step a segment.
    :data:`REACH` as soon as one reading passes through aipager's
    folders."""
    segs = [s for s in path.split("/") if s and s != "."]
    last = len(segs) - 1
    readings: list[tuple | None] = [None]
    # a long path repeats its segments: classify each kind once
    known: dict[str, int] = {}
    for k, seg in enumerate(segs):
        if seg == "..":
            nxt = []
            for node in readings:
                if node is None or node[0] & _UP:
                    nxt.append((_UP, node))
                elif node[0] & _O:
                    return UNSURE
                else:
                    nxt.append(node[1])
            readings = nxt
            continue
        if seg == "**" and dotglob:
            return UNSURE
        flags = known.get(seg)
        if flags is None:
            flags = known[seg] = _classify(seg, dotglob)
        nxt = []
        for node in readings:
            pushed = (flags, node)
            if flags & _A and _reaches(pushed):
                return REACH
            # globstar: ``.local/**`` may be ``.local/share/aipager``
            if seg == "**" and node is not None and node[0] & _L:
                return REACH
            nxt.append(pushed)
            if flags & _D and k < last:
                nxt.append(node)
                if node is None or node[0] & _UP:
                    nxt.append((_UP, node))
                elif node[0] & _O:
                    return UNSURE
                else:
                    nxt.append(node[1])
        if len(nxt) > _MAX_READINGS:
            return UNSURE
        readings = nxt
    return CLEAR


def word_reach(word: str, dotglob: bool = False) -> str:
    """:data:`REACH` when some expansion of *word* (from :func:`words`)
    can be a path through aipager's folders, :data:`UNSURE` when this
    module cannot tell on a word that could name ``.config`` or
    ``.local``, else :data:`CLEAR`."""
    if not may_name_start(word, dotglob):
        return CLEAR
    try:
        results = expand_braces(word)
        verdicts = [_walk(r, dotglob) for r in results]
    except Unsure:
        return UNSURE
    if REACH in verdicts:
        return REACH
    return UNSURE if UNSURE in verdicts else CLEAR


#: Words that turn on matching a leading ``.`` with ``*`` or ``?``.
_DOTGLOB_RE = re.compile(r"dotglob|GLOBIGNORE")


def command_reaches(*copies: str) -> bool:
    """Whether any word of any copy of a command (as typed, with quotes
    taken out) can reach aipager's folders, or cannot be told apart from
    one that does (:func:`word_reach`)."""
    dotglob = any(_DOTGLOB_RE.search(c) for c in copies)
    seen: set[str] = set()
    # a command with no quotes reads the same both ways: lex it once
    for copy in dict.fromkeys(copies):
        if not _WORTH_RE.search(copy):
            continue
        for w in words(copy):
            if w in seen:
                continue
            seen.add(w)
            if word_reach(w, dotglob) != CLEAR:
                return True
    return False
