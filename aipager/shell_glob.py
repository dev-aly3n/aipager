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
#: Parens as characters (a group in a ``[[ =~ ]]`` pattern).
_LIT_PARENS = {ord("("): _LIT[ord("(")], ord(")"): _LIT[ord(")")]}


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


#: Tokens where commands are read (the top level, ``$(...)``, a backtick
#: body, an arithmetic or extended glob group). ``#`` starts a comment
#: only where a word starts; ``<<`` is a here-document, ``<<<`` is not.
#: Bash's blanks are a space and a tab only: ``\r``, ``\v``, ``\f``,
#: ``\x1c``-``\x1f``, ``\x85``, a no-break space and the other Unicode
#: spaces are word characters (``echo x\r# ; cat ...`` runs the cat).
_CMD_RE = re.compile("|".join((
    r"(?P<plain>[^\x20\t\n;&|<>()`'\"\\$#]+)",
    r"(?P<nl>\n)",
    r"(?P<hstr><<<)",
    r"(?P<heredoc><<-?)",
    r"(?P<blank>[\x20\t]+)",
    r"(?P<sep>[;&|]+)",
    r"(?P<redir>[<>])",
    r"(?P<sq>'[^']*'?)",
    r"(?P<ansi>\$'(?:[^'\\]|\\[\s\S])*'?)",
    r"(?P<dq>\$?\")",
    r"(?P<esc>\\[\s\S]?)",
    r"(?P<param>\$\{)",
    r"(?P<subst>\$\()",
    r"(?P<tick>`)",
    r"(?P<var>\$(?:[A-Za-z_]\w*|[0-9@*#?$!-]))",
    r"(?P<oldarith>\$\[)",
    r"(?P<dollar>\$)",
    r"(?P<open>\()",
    r"(?P<close>\))",
    r"(?P<hash>#)",
)))
#: Tokens inside ``"..."`` (``"`` ends it) and in an unquoted
#: here-document body (``"`` is plain there).
_DQ_RE = re.compile("|".join((
    r"(?P<run>[^\"\\$`]+)",
    r"(?P<end>\")",
    r"(?P<esc>\\[\s\S]?)",
    r"(?P<param>\$\{)",
    r"(?P<subst>\$\()",
    r"(?P<tick>`)",
    r"(?P<var>\$(?:[A-Za-z_]\w*|[0-9@*#?$!-]))",
    r"(?P<oldarith>\$\[)",
    r"(?P<dollar>\$)",
)))
_HEREDOC_RE = re.compile(_DQ_RE.pattern.replace(
    r'(?P<run>[^\"\\$`]+)', r'(?P<run>[^\\$`]+)'))
#: Tokens inside ``${...}``. Bash closes it at the first ``}`` that is
#: not quoted, escaped or inside a nested expansion: only ``${`` nests,
#: a bare ``{`` does not (``${x#{}`` ends at its first ``}``). Single
#: quotes quote here even inside double quotes; ``$'...'`` decodes only
#: outside them.
_PARAM_RE = re.compile("|".join((
    r"(?P<run>[^}\\'\"$`]+)",
    r"(?P<close>\})",
    r"(?P<esc>\\[\s\S]?)",
    r"(?P<ansi>\$'(?:[^'\\]|\\[\s\S])*'?)",
    r"(?P<sq>'[^']*'?)",
    r"(?P<dq>\$?\")",
    r"(?P<param>\$\{)",
    r"(?P<subst>\$\()",
    r"(?P<tick>`)",
    r"(?P<var>\$(?:[A-Za-z_]\w*|[0-9@*#?$!-]))",
    r"(?P<oldarith>\$\[)",
    r"(?P<dollar>\$)",
)))
#: The name of a ``${...}`` (``!`` or ``#`` first, a subscript after)
#: and the operation that follows it. A default (``${x:-word}``,
#: ``${x-word}``, ``${x:=word}``, ``${x:+word}``) or a replacement
#: (``${x/pat/rep}``, ``//``, ``/#``, ``/%``) may put its text in the
#: value; the other operations (``#``, ``%``, ``:?``, ``^``, ``,``,
#: ``@``, offsets) may not, but their text is read as words all the same.
_PARAM_HEAD_RE = re.compile(
    r"[!#]?(?:[A-Za-z_]\w*|\d+|[@*#?$!-])(?:\[[^]}]*\])?"
    r"(?:(?P<value>:?[-=+]|/[/#%]?)|##?|%%?|:\??|\?|\^\^?|,,?|@)")
#: A backtick body ends at the next backtick a backslash does not escape;
#: quotes do not count there.
_TICK_BODY_RE = re.compile(r"(?:[^`\\]|\\[\s\S])*")
_TICK_UNESCAPE = re.compile(r"\\([\\`$])")
_TICK_UNESCAPE_DQ = re.compile(r"\\([\\`$\"])")
#: What may make a body set text aside (a comment, a here-document, a
#: subscript or ``$[``, a ``${...}``, a ``=~`` group).
_SETS_ASIDE_RE = re.compile(r"[#\[]|<<|\$\{|=~")
#: After these words a command may start again (``if case ...``).
_KEEPS_COMMAND_START = frozenset(
    ("if", "then", "else", "elif", "do", "while", "until", "!", "time",
     "{", "}", "coproc"))
#: A reserved word glued to ``(`` at a command's start ends there and the
#: ``(`` opens a subshell (``if(cat <<E ...``), as after a function's
#: ``name()`` (``f()(...)``): never an extended glob group. Decided from
#: the word's pieces (one piece in this set, or a name and ``()``),
#: never by joining the word, which may be long and meet many ``(``.
_OPENS_SUBSHELL = _KEEPS_COMMAND_START - {"}"}
#: ``function NAME``: the name stands where a command's start does, so
#: ``function f()(cat <<E`` opens a subshell after ``f()`` too.
_FUNCTION = "function"
#: An assignment word (``x=1``, ``a[i]+=y``): a command may still start
#: after it, and bash reads the next word's subscript as one word too.
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_]\w*(?:\[.*\])?\+?=", re.S)
#: A name glued to ``[`` where an assignment may stand: bash reads the
#: subscript to its matching ``]`` as part of the word, blanks, ``#`` and
#: ``<<`` included (``a[1 #$(cmd)]=1``, ``a[1<<2]=x``).
_SUBSCRIPT_HEAD_RE = re.compile(r"[A-Za-z_]\w*\[")
#: Tokens of a bracketed or parenthesised run bash reads to its matching
#: close as one piece of a word (an assignment's subscript, a group in a
#: ``[[ =~ ]]`` pattern): quotes and expansions nest, nothing else counts.
_MATCHED_RE = re.compile("|".join((
    r"(?P<run>[^][()'\"\\$`]+)",
    r"(?P<sq>'[^']*'?)",
    r"(?P<ansi>\$'(?:[^'\\]|\\[\s\S])*'?)",
    r"(?P<dq>\$?\")",
    r"(?P<esc>\\[\s\S]?)",
    r"(?P<param>\$\{)",
    r"(?P<subst>\$\()",
    r"(?P<oldarith>\$\[)",
    r"(?P<tick>`)",
    r"(?P<other>[][()$])",
)))
_BRACKET_RE = re.compile(r"[\[\]]")
#: The expansions read inside a :data:`_MATCHED_RE` run, and what each
#: stands for in the word around it: a value not known here, in a group
#: (so the segment holding it may match anything, see :func:`_classify`).
_NESTED_KINDS = frozenset(("dq", "param", "subst", "oldarith", "tick"))
_NESTED = "$(" + OPAQUE + ")"
#: A ``$[...]`` body with no quote, escape, backtick, ``$(`` or ``${``.
_ARITH_ONLY_RE = re.compile(r"(?:[^$`'\"\\]|\$(?![({]))*")
#: Nested quotes, expansions and groups deeper than this make the read
#: unsure (the hook must not run out of stack).
_MAX_NEST = 48


class _TooDeep(Exception):
    pass


#: At most this many characters of matched text are put in place of a
#: bare ``&`` in one read (:meth:`_Lexer._param`): each ``&`` adds a
#: whole copy of its pattern, so ``${x/<50,000 a>/<50,000 &>}`` would
#: spell 2.5 billion characters. Past it the read stops and the command
#: is refused (:class:`_TooBig`, :func:`read_overflowed`).
_MAX_STAND = 8192


class _TooBig(_TooDeep):
    """More matched text stood for ``&`` than :data:`_MAX_STAND`."""


class _Stand:
    """What a bare ``&`` in one replacement stands for (read off its
    pattern once, where the pattern ends) and whether one was met."""

    __slots__ = ("text", "met")

    def __init__(self) -> None:
        self.text = ""
        self.met = False


class _Spans:
    """Text one read set aside (a comment, a here-document's delimiter
    and body, a subscript, a ``=~`` group, an arithmetic-only ``$[...]``
    body, a ``${...}``'s text), as ``(start, end)`` in *text*. Read again
    as plain words once the whole command is read (:func:`_read_again`):
    only the outermost of nested spans, so each character is read again
    at most once per text."""

    __slots__ = ("text", "items")

    def __init__(self, text: str) -> None:
        self.text = text
        self.items: list[tuple[int, int]] = []


class _Brackets:
    """Which ``]`` closes each ``[`` of *text*, every bracket counted (as
    bash ends ``$[...]``), from one pass over its brackets, made the first
    time a ``$[`` asks."""

    __slots__ = ("text", "pairs")

    def __init__(self, text: str) -> None:
        self.text = text
        self.pairs: dict[int, int] | None = None

    def close(self, at: int) -> int:
        """The index of the ``]`` that closes the ``[`` at *at*; the
        text's length when none does."""
        if self.pairs is None:
            pairs: dict[int, int] = {}
            stack: list[int] = []
            for m in _BRACKET_RE.finditer(self.text):
                if m.group() == "[":
                    stack.append(m.start())
                elif stack:
                    pairs[stack.pop()] = m.start()
            self.pairs = pairs
        return self.pairs.get(at, len(self.text))


class _Word:
    """The pieces of the word being read and, when a ``${x:-word}``
    default could become its value, a second reading with every such
    default in place."""

    __slots__ = ("pieces", "alt", "spell")

    def __init__(self) -> None:
        self.pieces: list[str] = []
        self.alt: list[str] | None = None
        #: the word as the regex rules see it (:func:`spelled`): a
        #: variable or ``${...}`` as typed instead of :data:`OPAQUE`
        self.spell: list[str] | None = None

    def add(self, piece: str, alt: str | None = None,
            spell: str | None = None) -> None:
        self.pieces.append(piece)
        if self.alt is not None:
            self.alt.append(piece if alt is None else alt)
        elif alt is not None and alt != piece:
            self.alt = [*self.pieces[:-1], alt]
        if self.spell is not None:
            self.spell.append(piece if spell is None else spell)
        elif spell is not None and spell != piece:
            self.spell = [*self.pieces[:-1], spell]

    def spell_text(self) -> str:
        return "".join(self.spell if self.spell is not None else self.pieces)

    def last_char(self) -> str:
        for p in reversed(self.pieces):
            if p:
                return p[-1]
        return ""

    def text(self) -> str:
        return "".join(self.pieces)

    def alt_text(self) -> str:
        return "".join(self.alt if self.alt is not None else self.pieces)


class _Lexer:
    """Reads a command as bash's parser would, far enough to know which
    text is a word and which is quoted, commented out or a here-document
    body. ``sure`` turns False when something is left open at the end
    (a quote, ``${``, ``$(``, a backtick, a here-document, a group) or
    nests too deep: bash would then read the command differently or not
    at all, and the words found here may hide part of it.

    A reading may add words, never take them away. Text this reader sets
    aside as not words (a comment, a here-document's delimiter and body,
    an assignment's subscript, a group in a ``[[ =~ ]]`` pattern, the
    text of a ``${...}``, open or closed) is read again as plain words
    whenever it could name ``.config`` or ``.local`` (:meth:`_set_aside`,
    :func:`_read_again`): if bash reads that text some other way, its
    words are checked all the same.

    *flat* is that plain reading: no comments, here-documents,
    subscripts or patterns, so nothing is set aside again; an arithmetic
    ``$[...]`` body is read as words there, and a ``${`` left open ends
    where it starts, its text read on as commands. *spell*, when given,
    collects every word and operator for :func:`spelled`. *spans* and
    *base*: where this reader's text sits in the text whose set-aside
    spans it records (*groups* holds every such text of one read)."""

    def __init__(self, text: str, out: list[str], level: int = 0,
                 dotglob: bool = False, flat: bool = False,
                 spell: list[str] | None = None,
                 spans: _Spans | None = None, base: int = 0,
                 groups: list[_Spans] | None = None,
                 bodies: dict[str, bool] | None = None,
                 room: list[int] | None = None) -> None:
        self.text = text
        self.n = len(text)
        self.out = out
        self.level = level
        self.dotglob = dotglob
        self.flat = flat
        self.spell = spell
        self.sure = True
        self.pending: list[tuple[str, bool, bool]] = []
        if spans is None and not flat:
            spans = _Spans(text)
            groups = [spans] if groups is None else groups + [spans]
        self.spans = spans
        self.base = base
        self.groups: list[_Spans] = groups if groups is not None else []
        # how many ``${`` are open here; a flat read ends the outermost
        # one left open where it starts, once (``params_off``)
        self.params = 0
        self.params_off = False
        # the bracket pairs of the text this one is a slice of, and where
        # it starts there (:meth:`_old_arith`)
        self.brackets: _Brackets | None = None
        self.boff = 0
        # backtick bodies read so far in this read (:meth:`_backtick`)
        self.bodies: dict[str, bool] = {} if bodies is None else bodies
        # the matched text ``&`` may still stand for in this read, shared
        # by every reader of it (:data:`_MAX_STAND`)
        self.room: list[int] = [_MAX_STAND] if room is None else room

    # -- helpers --------------------------------------------------------

    def _enter(self) -> None:
        self.level += 1
        if self.level > _MAX_NEST:
            raise _TooDeep

    def _emit(self, word: _Word, text: str) -> None:
        if _WORTH_RE.search(text):
            self.out.append(text)
        if word.alt is not None:
            alt = word.alt_text()
            # a value's text, plain or not: the regex rules may not see
            # it whole (a quote earlier, in a comment, throws their
            # quote-free copies off)
            if alt != text:
                self.out.append(alt)
        if self.spell is not None:
            self.spell.append(word.spell_text())

    def _mark(self, token: str) -> None:
        if self.spell is not None:
            self.spell.append(token)

    def _sub(self, text: str, base: int | None = None) -> _Lexer:
        """A reader of *text*, a body bash parses on its own. *base*: where
        *text* starts in this reader's text, when it is a plain slice of
        it (its set-aside spans then join this text's); otherwise (a
        backtick body with its escapes taken out) a text of its own."""
        if self.level + 1 > _MAX_NEST:
            raise _TooDeep
        if self.flat:
            sub = _Lexer(text, self.out, self.level + 1, self.dotglob, True,
                         bodies=self.bodies, room=self.room)
        elif base is None:
            spans = _Spans(text)
            self.groups.append(spans)
            sub = _Lexer(text, self.out, self.level + 1, self.dotglob, False,
                         self.spell, spans, 0, self.groups, self.bodies,
                         self.room)
        else:
            sub = _Lexer(text, self.out, self.level + 1, self.dotglob, False,
                         self.spell, self.spans, self.base + base,
                         self.groups, self.bodies, self.room)
        if base is not None:
            # a slice: its brackets pair as they do in this text
            if self.brackets is None:
                self.brackets, self.boff = _Brackets(self.text), 0
            sub.brackets, sub.boff = self.brackets, self.boff + base
        return sub

    def _body_read(self, sub: _Lexer) -> None:
        """Bash parses a backtick, ``$[...]`` or here-document body on
        its own: a body left open hides at most its own text, so the read
        stays sure unless that text could name ``.config`` or
        ``.local``."""
        if not sub.sure and _mentions(sub.text, self.dotglob):
            self.sure = False

    def _set_aside(self, begin: int, end: int) -> None:
        """``text[begin:end]``, set aside as not words, is read again as
        plain words once the command is read (:func:`_read_again`). A
        flat read is itself that second reading and sets nothing aside."""
        if self.spans is not None and begin < end:
            self.spans.items.append((self.base + begin, self.base + end))

    # -- commands -------------------------------------------------------

    def commands(self, i: int = 0, term: bool = False,
                 arith: bool = False) -> int:
        """Read commands from *i*. With *term*, stop after the ``)`` that
        closes the group (``$(...)``, an arithmetic or extended glob
        group); *arith* reads no comments, here-documents or ``case``."""
        text, n = self.text, self.n
        flat = self.flat
        cur = _Word()
        # the open groups, True for a compound assignment ``a=(...)``,
        # whose words are no commands and whose ``[i]=`` is a subscript
        groups: list[bool] = []
        case = 0
        start = True
        # inside ``[[ ... ]]``; the next word is a ``=~`` pattern
        cond = regexp = False

        def flush() -> None:
            nonlocal cur, case, start, cond, regexp
            if not cur.pieces:
                return
            w = cur.text()
            if cond and w == "]]":
                cond = False
            elif start and not arith:
                if w == "case":
                    case += 1
                elif w == "esac" and case:
                    case -= 1
                elif w == "[[" and not flat:
                    cond = True
            regexp = cond and w == "=~"
            if w in _KEEPS_COMMAND_START or (start and w == _FUNCTION):
                start = True
            elif start:
                start = "=" in w and _ASSIGNMENT_RE.match(w) is not None
            self._emit(cur, w)
            cur = _Word()

        def command_position() -> bool:
            return not (groups and groups[-1])

        while i < n:
            m = _CMD_RE.match(text, i)
            kind, tok, i = m.lastgroup, m.group(), m.end()
            if kind == "plain":
                if not cur.pieces and not flat and not arith:
                    at = 0
                    if start:
                        head = _SUBSCRIPT_HEAD_RE.match(tok)
                        at = head.end() if head else 0
                    elif groups and groups[-1] and tok[0] == "[":
                        at = 1
                    if at:
                        # ``a[...]=``: the subscript is one piece of the word
                        cur.add(tok[:at])
                        i = self._matched(i - len(tok) + at, "[", "]", cur)
                        continue
                cur.add(tok)
            elif kind == "blank":
                flush()
            elif kind == "nl":
                flush()
                start = command_position()
                self._mark("\n")
                if self.pending and not arith:
                    i = self._heredoc_bodies(i)
            elif kind == "sep":
                if regexp and not tok.strip("|"):
                    # ``|`` is a character of a ``=~`` pattern
                    cur.add(tok)
                    continue
                flush()
                start = command_position()
                self._mark(tok)
            elif kind in ("redir", "hstr"):
                flush()
                self._mark(tok)
            elif kind == "heredoc":
                flush()
                self._mark(tok)
                if not arith and not flat:
                    i = self._delimiter(i, tok == "<<-")
            elif kind == "sq":
                if len(tok) < 2 or not tok.endswith("'"):
                    self.sure = False
                    cur.add(_lit(tok[1:]))
                else:
                    cur.add(_lit(tok[1:-1]))
            elif kind == "ansi":
                closed = len(tok) > 2 and tok.endswith("'") and (
                    _ANSI_CLOSED_RE.fullmatch(tok) is not None)
                if not closed:
                    self.sure = False
                body = tok[2:-1] if closed else tok[2:]
                cur.add(_lit(_ANSI_ESCAPE_RE.sub(_ansi_char, body)))
            elif kind == "dq":
                cur.add("")
                i = self._double_quoted(i, cur)
            elif kind == "esc":
                if len(tok) == 2 and tok[1] != "\n":
                    cur.add(_lit(tok[1]))
            elif kind == "param":
                if self.params_off:
                    # a flat read past a ``${`` left open: plain text
                    cur.add("$")
                    i -= 1
                    continue
                i = self._param(i, cur, False)
            elif kind == "subst":
                i = self._comsub(i)
                cur.add(OPAQUE)
            elif kind == "tick":
                i = self._backtick(i, False)
                cur.add(OPAQUE)
            elif kind == "var":
                cur.add(OPAQUE, spell=tok)
            elif kind == "oldarith":
                i = self._old_arith(i)
                cur.add(OPAQUE)
            elif kind == "dollar":
                cur.add("$")
            elif kind == "open":
                if regexp and not flat:
                    # a group in a ``=~`` pattern: one piece of the word.
                    # Bash never globs that word; a group not after an
                    # extended glob's ``?*+@!`` would be a syntax error
                    # anywhere else, so its parens count as characters
                    glob = cur.last_char() in ("?", "*", "+", "@", "!")
                    cur.add("(" if glob else _lit("("))
                    i = self._matched(i, "(", ")", cur, not glob)
                    continue
                pieces = cur.pieces
                glued = bool(pieces)
                compound = glued and cur.last_char() == "="
                if glued and not compound and not (
                        start and not arith and not flat
                        and ((len(pieces) == 1
                              and pieces[0] in _OPENS_SUBSHELL)
                             or (len(pieces) == 2 and pieces[1] == "()"))):
                    # an extended glob (``@(a|b)``) or another shell's
                    # group, glued to the word: kept in it as typed
                    if text.startswith(")", i):
                        # ``()``, as a function's name ends: nothing to read
                        self._mark(")")
                        cur.add("()")
                        i += 1
                        continue
                    begin = i - 1
                    self._enter()
                    i = self.commands(i, True, True)
                    self.level -= 1
                    cur.add(text[begin:i])
                    continue
                flush()
                self._mark("(")
                if not compound and text.startswith("(", i) and not arith:
                    # ``((...))``: arithmetic, where ``<<`` is a shift
                    self._enter()
                    i = self.commands(i, True, True)
                    self.level -= 1
                    start = True
                    continue
                groups.append(compound)
                start = not compound
            elif kind == "close":
                flush()
                self._mark(")")
                if term and not groups and (arith or case == 0):
                    return i
                if groups:
                    groups.pop()
                start = command_position()
            else:  # "#"
                if cur.pieces or arith or flat:
                    cur.add("#")
                else:
                    end = text.find("\n", i)
                    end = n if end < 0 else end
                    self._set_aside(i, end)
                    i = end
        flush()
        if term:
            self.sure = False
        return n

    # -- quotes and expansions -----------------------------------------

    def _double_quoted(self, i: int, cur: _Word,
                       pattern: re.Pattern = _DQ_RE) -> int:
        """Read ``"..."`` from *i* (after the quote) into *cur*; the index
        after the closing quote. With :data:`_HEREDOC_RE`, a whole
        here-document body (``"`` is plain)."""
        text, n = self.text, self.n
        in_dq = pattern is _DQ_RE
        while i < n:
            m = pattern.match(text, i)
            kind, tok, i = m.lastgroup, m.group(), m.end()
            if kind == "run":
                cur.add(_lit(tok))
            elif kind == "end":
                return i
            elif kind == "esc":
                ch = tok[1:]
                if ch in ("$", "`", "\\") or (ch == '"' and in_dq):
                    cur.add(_lit(ch))
                elif ch != "\n":
                    cur.add(_lit(tok))
            elif kind == "param":
                if self.params_off:
                    cur.add("$")
                    i -= 1
                    continue
                i = self._param(i, cur, True)
            elif kind == "subst":
                i = self._comsub(i)
                cur.add(OPAQUE)
            elif kind == "tick":
                i = self._backtick(i, in_dq)
                cur.add(OPAQUE)
            elif kind == "var":
                cur.add(OPAQUE, spell=tok)
            elif kind == "oldarith":
                i = self._old_arith(i)
                cur.add(OPAQUE)
            else:
                cur.add("$")
        if in_dq:
            self.sure = False
        return n

    def _param(self, i: int, cur: _Word, quoted: bool) -> int:
        """Read ``${...}`` from *i* (after ``${``); one :data:`OPAQUE` in
        *cur*. Unquoted, a default (``${x:-word}``) or a replacement
        (``${x/pat/rep}``) may put its text in the value: the word's
        second reading takes it (see :class:`_Word`; for a replacement,
        the text after the pattern's first unquoted ``/``). Every
        operation's text is also a word of its own, and it is set aside
        to be read again as plain words (:meth:`_set_aside`), to the end
        of the command when nothing closes it. A command substitution
        inside is read as more words."""
        text, n = self.text, self.n
        begin = i - 2
        self._enter()
        self.params += 1
        head = _PARAM_HEAD_RE.match(text, i)
        value = head.group("value") if head is not None else None
        replacement = bool(value) and value[0] == "/"
        stand: _Stand | None = None
        # a replacement's value is the text after the pattern's end, the
        # first unquoted ``/`` (None until it is met)
        rep_at: int | None = None if replacement else 0
        word = _Word()
        if head is not None:
            i = head.end()
        while i < n:
            m = _PARAM_RE.match(text, i)
            kind, tok, i = m.lastgroup, m.group(), m.end()
            if kind == "close":
                self.level -= 1
                self.params -= 1
                inline = value is not None and rep_at is not None and (
                    not quoted)
                own = word.alt_text()
                if not inline and _WORTH_RE.search(own):
                    # not in the value: a word of its own (a value's text
                    # is in the word's second reading already)
                    self.out.append(own)
                if inline and rep_at:
                    pieces = word.alt if word.alt is not None else word.pieces
                    own = "".join(pieces[rep_at:])
                cur.add(OPAQUE, own if inline else None, text[begin:i])
                self._set_aside(begin + 2, i - 1)
                return i
            if kind == "run":
                if rep_at is None and "/" in tok:
                    k = tok.index("/") + 1
                    word.add(tok[:k])
                    rep_at = len(word.pieces)
                    # the pattern, read once: what a bare ``&`` stands for
                    stand = _Stand()
                    pattern = "".join(word.pieces)[:-1]
                    stand.text = _NESTED if OPAQUE in pattern else pattern
                    tok = tok[k:]
                    if not tok:
                        continue
                if stand is not None and "&" in tok:
                    # the pattern stands for the matched text (a glob one
                    # as a glob, so the word matches as bash's could), one
                    # holding a value as a value not known here
                    self._stand_in(word, tok, stand)
                    continue
                word.add(tok)
            elif kind == "esc":
                if len(tok) == 2:
                    word.add(_lit(tok[1]))
            elif kind == "ansi" and quoted:
                # inside double quotes ``$'`` is a ``$`` and a quote
                word.add("$")
                i -= len(tok) - 1
            elif kind == "ansi":
                closed = len(tok) > 2 and tok.endswith("'") and (
                    _ANSI_CLOSED_RE.fullmatch(tok) is not None)
                if not closed:
                    self.sure = False
                body = tok[2:-1] if closed else tok[2:]
                word.add(_lit(_ANSI_ESCAPE_RE.sub(_ansi_char, body)))
            elif kind == "sq":
                closed = len(tok) > 1 and tok.endswith("'")
                if not closed:
                    self.sure = False
                word.add(_lit(tok[1:-1] if closed else tok[1:]))
            elif kind == "dq":
                i = self._double_quoted(i, word)
            elif kind == "param":
                i = self._param(i, word, quoted)
            elif kind == "subst":
                i = self._comsub(i)
                word.add(OPAQUE)
            elif kind == "tick":
                i = self._backtick(i, quoted)
                word.add(OPAQUE)
            elif kind == "var":
                word.add(OPAQUE)
            elif kind == "oldarith":
                i = self._old_arith(i)
                word.add(OPAQUE)
            else:
                word.add("$")
        # left open: bash would read the command another way or not at
        # all, and what follows the ``${`` may be commands there
        self.sure = False
        self.level -= 1
        self.params -= 1
        cur.add(OPAQUE)
        if not self.flat:
            self._set_aside(begin + 2, n)
        elif not self.params and not self.params_off:
            # a flat read reads on from the outermost open ``${`` (once:
            # every later ``${`` is plain text, so this stays linear)
            self.params_off = True
            return begin + 2
        return n

    def _stand_in(self, word: _Word, tok: str, stand: _Stand) -> None:
        """Add *tok* to *word* with each ``&`` as *stand*'s text. The
        pattern was read off once (:meth:`_param`); every copy is paid
        from :attr:`room`, the ``&`` itself included."""
        for k, part in enumerate(tok.split("&")):
            if k:
                self.room[0] -= len(stand.text) + 1
                if self.room[0] < 0:
                    raise _TooBig
                word.add(stand.text)
                stand.met = True
            if part:
                word.add(part)

    def _comsub(self, i: int) -> int:
        """Read ``$(...)`` (or ``$((...))``) from *i* (after ``$(``): its
        commands are more words. The index after the closing ``)``."""
        self._enter()
        self._mark("(")
        i = self.commands(i, True, self.text.startswith("(", i))
        self.level -= 1
        return i

    def _backtick(self, i: int, quoted: bool) -> int:
        """Read a backtick substitution from *i* (after the backtick): the
        body runs to the next backtick a backslash does not escape, and,
        its escapes taken out, is read as commands."""
        end = _TICK_BODY_RE.match(self.text, i).end()
        body = self.text[i:end]
        if end >= self.n:
            self.sure = False
        unescape = _TICK_UNESCAPE_DQ if quoted else _TICK_UNESCAPE
        plain = unescape.sub(r"\1", body)
        # The same body read again adds the same words (one list for the
        # whole read) and the same sureness: read once. Not when spelled
        # text is collected (each place needs its words), nor for a body
        # that may set text aside (each place's text is read again).
        cache = self.spell is None and not _SETS_ASIDE_RE.search(plain)
        if cache and plain in self.bodies:
            if not self.bodies[plain]:
                self.sure = False
            return min(end + 1, self.n)
        sub = self._sub(plain, i if plain == body else None)
        self._mark("(")
        sub.commands()
        self._mark(")")
        self._body_read(sub)
        if cache:
            # True: this body leaves the read sure (see :meth:`_body_read`)
            self.bodies[plain] = sub.sure or not _mentions(sub.text,
                                                           self.dotglob)
        return min(end + 1, self.n)

    def _old_arith(self, i: int) -> int:
        """``$[...]``: read to the matching ``]`` as arithmetic (bash
        counts every ``[`` and ``]``, quoted or not). The pairs come from
        one table per text (:class:`_Brackets`), shared with the bodies
        read from it, so ``$[`` nested many deep costs no pass of the
        text per level."""
        text, n = self.text, self.n
        if self.brackets is None:
            self.brackets, self.boff = _Brackets(text), 0
        j = min(self.brackets.close(self.boff + i - 1) - self.boff, n)
        if j >= n:
            self.sure = False
        if not self.flat and _ARITH_ONLY_RE.fullmatch(text, i, j):
            # no quote, escape or substitution: arithmetic only, so no
            # word of it is a path (read again as plain words when it
            # could name a folder, should bash end it elsewhere)
            self._set_aside(i, j)
        else:
            # (a flat read is the second reading: its words are read here)
            sub = self._sub(text[i:j], i)
            sub.commands(0, False, True)
            self._body_read(sub)
        return min(j + 1, n)

    def _matched(self, i: int, open_: str, close: str, cur: _Word,
                 literal: bool = False) -> int:
        """Read from *i* (after *open_*) to the matching *close*, as bash
        reads an assignment's subscript or a group in a ``=~`` pattern:
        quotes and expansions nest (their commands are more words), blanks,
        ``#`` and ``<<`` are characters. The text goes into *cur* as typed
        (with *literal*, its parens as characters) and is set aside to be
        read again as plain words (:meth:`_set_aside`)."""
        text, n = self.text, self.n
        begin = seg = i
        parts: list[str] = []
        depth = 1
        self._enter()
        while i < n:
            m = _MATCHED_RE.match(text, i)
            kind, tok, i = m.lastgroup, m.group(), m.end()
            if kind in _NESTED_KINDS:
                # a nested expansion is one unknown value in this word
                # (its own words are read where it stands): its text is
                # not copied into every word around it
                parts.append(text[seg:m.start()])
                parts.append(_NESTED)
            if kind == "other":
                if tok == open_:
                    depth += 1
                elif tok == close:
                    depth -= 1
                    if depth == 0:
                        break
            elif kind == "sq":
                if len(tok) < 2 or not tok.endswith("'"):
                    self.sure = False
            elif kind == "ansi":
                if _ANSI_CLOSED_RE.fullmatch(tok) is None:
                    self.sure = False
            elif kind == "dq":
                i = self._double_quoted(i, _Word())
            elif kind == "param":
                i = self._param(i, _Word(), False)
            elif kind == "subst":
                i = self._comsub(i)
            elif kind == "oldarith":
                i = self._old_arith(i)
            elif kind == "tick":
                i = self._backtick(i, False)
            if kind in _NESTED_KINDS:
                seg = i
        else:
            self.sure = False
        self.level -= 1
        parts.append(text[seg:i])
        piece = "".join(parts)
        cur.add(piece.translate(_LIT_PARENS) if literal else piece)
        self._set_aside(begin, i)
        return i

    # -- here-documents -------------------------------------------------

    def _delimiter(self, i: int, strip: bool) -> int:
        """Read a here-document's delimiter word from *i*; its body is
        read at the next line end. The delimiter's text is read again as
        plain words (:meth:`_set_aside`)."""
        text, n = self.text, self.n
        while i < n and text[i] in " \t":
            i += 1
        begin = i
        parts: list[str] = []
        quoted = False
        while i < n and text[i] not in " \t\n;&|<>()":
            c = text[i]
            if c == "'":
                end = text.find("'", i + 1)
                if end < 0:
                    self.sure = False
                    end = n
                parts.append(text[i + 1:end])
                quoted, i = True, end + 1
            elif c == '"' or text.startswith('$"', i):
                i += 2 if c == "$" else 1
                while i < n and text[i] != '"':
                    if text[i] == "\\" and i + 1 < n:
                        i += 1
                    parts.append(text[i])
                    i += 1
                if i >= n:
                    self.sure = False
                quoted, i = True, i + 1
            elif c == "\\":
                parts.append(text[i + 1:i + 2])
                quoted, i = True, i + 2
            elif text.startswith("$'", i):
                m = _ANSI_TOKEN_RE.match(text, i)
                tok = m.group()
                if _ANSI_CLOSED_RE.fullmatch(tok) is None:
                    self.sure = False
                parts.append(_ANSI_ESCAPE_RE.sub(_ansi_char, tok[2:-1]))
                quoted, i = True, m.end()
            else:
                parts.append(c)
                i += 1
        i = min(i, n)
        if not parts and not quoted:
            # ``<<`` with no word: a syntax error (``<<''`` is a word:
            # the body ends at the first empty line)
            self.sure = False
        self._set_aside(begin, i)
        self.pending.append(("".join(parts), strip, quoted))
        return i

    def _heredoc_bodies(self, i: int) -> int:
        """Skip the bodies of the here-documents opened on the line just
        ended (*i* is after its newline). An unquoted body's expansions
        are read; one with no closing line is unsure. Every body is read
        again as plain words (:meth:`_set_aside`)."""
        text, n = self.text, self.n
        pending, self.pending = self.pending, []
        for delim, strip, quoted in pending:
            begin = i
            while True:
                if i >= n:
                    self.sure = False
                    body_end = n
                    break
                line_start = i
                logical: list[str] = []
                while True:
                    end = text.find("\n", i)
                    end = n if end < 0 else end
                    line = text[i:end]
                    i = end + 1
                    if quoted or end >= n:
                        logical.append(line)
                        break
                    tail = len(line) - len(line.rstrip("\\"))
                    if tail % 2 == 0:
                        logical.append(line)
                        break
                    logical.append(line[:-1])
                joined = "".join(logical)
                if (joined.lstrip("\t") if strip else joined) == delim:
                    body_end = line_start
                    break
            i = min(i, n)
            if not quoted:
                sub = self._sub(text[begin:body_end], begin)
                sub._double_quoted(0, _Word(), _HEREDOC_RE)
                self._body_read(sub)
            self._set_aside(begin, body_end)
        return i


_ANSI_TOKEN_RE = re.compile(r"\$'(?:[^'\\]|\\[\s\S])*'?")
_ANSI_CLOSED_RE = re.compile(r"\$'(?:[^'\\]|\\[\s\S])*'")


#: The characters that make a word worth expanding: a glob, a brace, a
#: group, or a ``..`` step.
_WORTH_RE = re.compile("[{\\[*?(]|[.%s][.%s]" % (_QUOTED_DOT, _QUOTED_DOT))


#: The words and operators of :func:`spelled`, quoted characters put back.
_UNLIT_TABLE = {ord(k): v for k, v in _UNLIT.items()}


#: ``$(``, ``${``, ``$[``: their ``(``, ``{``, ``[`` are syntax in a flat
#: read, never a character of a word.
_EXPANSION_OPEN_RE = re.compile(r"\$[({\[]")
#: Two dots, a quoted dot standing for one (``$'\x2e'``).
_DOTS_RE = re.compile("[.%s][.%s]" % (_QUOTED_DOT, _QUOTED_DOT))


def _may_give_worth(text: str) -> bool:
    """Whether a flat read of *text* can give a word :func:`command_reaches`
    checks (:data:`_WORTH_RE`): a glob, brace or group character that is
    not the opening of an expansion, or two dots, with quotes and
    backslashes taken out (``'.''.'``) or a numeric escape decoded
    (``$'\\x2e\\x2e'``; any other decoded character is quoted, so never a
    glob). Every word of a flat read is made of those characters."""
    plain = _EXPANSION_OPEN_RE.sub(" ", text).translate(_NO_QUOTES)
    if _WORTH_RE.search(plain):
        return True
    return "\\" in text and _DOTS_RE.search(
        _NUMERIC_ESCAPE_RE.sub(_numeric_char, text)) is not None


def _read_again(groups: list[_Spans], out: list[str], dotglob: bool,
                room: list[int] | None = None) -> bool:
    """Read the set-aside spans of one read again as plain words (a flat
    :class:`_Lexer`, from a fresh state, so a quote inside one cannot
    swallow text outside it), adding their words to *out*. Only spans
    that could name ``.config`` or ``.local`` (no other word can reach),
    and of nested spans only the outermost: its flat read covers every
    character of the ones inside, so each character is read again at
    most once per text. False when a read nested too deep."""
    deep = False
    for group in groups:
        end = -1
        for a, b in sorted(group.items, key=lambda s: (s[0], -s[1])):
            if b <= end:
                continue
            end = max(end, b)
            part = group.text[a:b]
            if not _may_give_worth(part) or not _mentions(part, dotglob):
                continue
            try:
                _Lexer(part, out, 0, dotglob, True, room=room).commands()
            except _TooDeep:
                deep = True
    return not deep


def _read(command: str, dotglob: bool, cache: dict | None = None,
          spell_too: bool = False,
          ) -> tuple[list[str], bool, str | None, bool]:
    """The words of *command*, whether the read is sure, (with
    *spell_too*) its :func:`spelled` text, and whether it nested too
    deep (then words past that point are missing). *cache* (one dict
    for one check of one command) lets :func:`command_reaches` and
    :func:`spelled` share a read."""
    key = (command, dotglob)
    if cache is not None and key in cache and (
            cache[key][2] is not None or not spell_too):
        return cache[key]
    out: list[str] = []
    spell: list[str] | None = [] if spell_too else None
    lexer = _Lexer(command, out, 0, dotglob, False, spell)
    deep = False
    try:
        lexer.commands()
    except _TooDeep:
        deep = True
    if not _read_again(lexer.groups, out, dotglob, lexer.room):
        deep = True
    found = (out, lexer.sure and not deep,
             None if spell is None
             else " ".join(spell).translate(_UNLIT_TABLE), deep)
    if cache is not None:
        cache[key] = found
        if lexer.room[0] < 0:
            cache[_OVERFLOW] = True
    return found


#: The key :func:`_read` sets in a check's cache when a read stopped at
#: :data:`_MAX_STAND` (see :func:`read_overflowed`).
_OVERFLOW = ("overflow",)


def read_overflowed(cache: dict) -> bool:
    """Whether a read shared through *cache* (:func:`spelled`,
    :func:`command_reaches`) stopped because ``&`` would have stood for
    more matched text than :data:`_MAX_STAND`: its words and spelling
    miss the rest of the command, so the floor refuses it."""
    return bool(cache.get(_OVERFLOW))


def lex(command: str, dotglob: bool = False) -> tuple[list[str], bool]:
    """The words of *command* as bash splits them (see :func:`words`),
    and whether the read is sure: False when a quote, expansion, group or
    here-document is left open at the end, or nesting runs too deep (the
    words may then miss part of the command)."""
    out, sure, _, _ = _read(command, dotglob)
    return list(out), sure


def words(command: str) -> list[str]:
    """The words of *command* as bash splits them, quotes applied: a
    quoted or escaped special character moved out of the way (see
    :data:`_SPECIAL`), every expansion one :data:`OPAQUE` character.
    Commands in a substitution (``$(...)``, a backtick body, an unquoted
    here-document's expansions) are read as more words. Comments and
    here-document text are not words, but are read again as plain words
    when they could name ``.config`` or ``.local`` (see :class:`_Lexer`).
    ``(`` glued to a word (an extended glob or another shell's group)
    stays in the word. An unquoted ``${x:-word}`` gives the word a second
    reading with the default in place. Only words holding a glob, brace,
    group or ``..`` are returned: nothing else can be expanded here."""
    return lex(command)[0]


def spelled(command: str, *more: str, cache: dict | None = None) -> str:
    """*command* as bash reads it into words, for the floor's regex rules:
    every word with its quotes applied and ``$'...'`` decoded only where
    bash decodes it, a blank between words, operators kept, comments and
    here-document bodies left out (the regex rules see the command as
    typed too). A variable stays as typed (``$HOME``), a substitution is
    one :data:`OPAQUE`. *more* are the other copies
    :func:`command_reaches` is given (they decide dotglob); with the same
    *cache* both share one read."""
    dotglob = any(_DOTGLOB_RE.search(c) for c in (command, *more))
    return _read(command, dotglob, cache, True)[2] or ""


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
            if "[" in name:
                # ``[a[=[=]ger`` matches ``aipager`` in bash 5.2: an item
                # of ``[`` is read in ways not modelled here
                raise Unsure("a_posix_item_of_an_open_bracket")
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
                    if "-" in span:
                        # bash reads ``]h-j`` there as ``]`` and a range,
                        # ``]-z`` as one range: not modelled here
                        raise Unsure("a_range_in_a_run_on_bracket")
                    for ch in "]" + span:
                        _member(ch, maybe, sure)
                    return done(end + 1)
            elif len(name) == 1:
                _member(name, maybe, sure)
            else:
                raise Unsure("a_collating_element_of_several_characters")
            j = close + 2
            if j + 1 < n and seg[j] == "-" and seg[j + 1] != "]":
                # a range from the item (``[[.a.]-z]`` holds a to z in
                # bash): its ends are not modelled here
                raise Unsure("a_range_from_a_posix_item")
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
    if flags:
        empty = seg.replace(OPAQUE, "")
        if _GLOB_RE.search(empty):
            # each value may be empty (``[a]ipager${unset}``): the glob
            # around it as bash would then read it
            flags |= _classify(empty, dotglob)
    if not _GLOB_RE.search(seg):
        low = seg.lower()
        return flags | (_C if low == ".config" else _L if low == ".local"
                        else _S if low in ("share", "state")
                        else _A if low == "aipager" else 0)
    if "(" in seg or ")" in seg:
        # an extended glob or another shell's group, anywhere in the
        # segment (also past the longest name: ``.config?(x)``)
        return _ALL
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


#: A numeric escape (``\x2e``, ``\141``, ``\u0061``); after the extra
#: backslashes nested backticks put before one, the last still matches.
_NUMERIC_ESCAPE_RE = re.compile(
    r"\\(?:x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})|U([0-9A-Fa-f]{1,8})"
    r"|([0-7]{1,3}))")


def _numeric_char(m: re.Match) -> str:
    hex2, hex4, hex8, octal = m.groups()
    digits = hex2 or hex4 or hex8
    try:
        return chr(int(digits, 16) if digits else int(octal, 8))
    except (ValueError, OverflowError):
        return ""


def _mentions(text: str, dotglob: bool) -> bool:
    """Whether any part of *text* could name ``.config`` or ``.local``:
    :func:`may_name_start` on the whole text as typed, and again with
    every numeric escape decoded wherever it stands. No ``$'...'`` span
    is looked for: which ``$'`` bash reads as one depends on the quotes
    before it, and an earlier ``$'`` bash does not read (``grep
    'TODO$'``) would shift every later one. An escape bash leaves alone
    keeps an ``x``, ``u`` or a digit in the word, which no name holds,
    so the two readings cover every mix."""
    if may_name_start(text, dotglob):
        return True
    if "\\" not in text:
        return False
    return may_name_start(_NUMERIC_ESCAPE_RE.sub(_numeric_char, text),
                          dotglob)


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


def _up(node: tuple | None) -> list[tuple | None]:
    """The readings after a ``..`` on top of *node*. Above the start it
    stacks (``a/../..``); after a value the floor cannot see (``$X``, an
    unsure segment) it is either one segment up or still somewhere not
    seen, as if ``$X`` were there (``$X/../x`` reads like ``$X/x``)."""
    if node is None or node[0] & _UP:
        return [(_UP, node)]
    if node[0] & _O:
        return [node[1], node]
    return [node[1]]


def _walk(path: str, dotglob: bool, known: dict[str, int] | None = None,
          ) -> str:
    """Read *path*'s segments in order, keeping every way the path can
    resolve: ``//`` and ``/./`` drop out, ``name/..`` cancels (see
    :func:`_up`), and a glob that may be ``.`` or ``..`` (``.*``) is read
    all three ways. Each reading is a linked stack, so a long path costs
    one step a segment. :data:`REACH` as soon as one reading passes
    through aipager's folders. *known* caches each segment's flags (one
    dict for a whole command)."""
    segs = [s for s in path.split("/") if s and s != "."]
    last = len(segs) - 1
    readings: list[tuple | None] = [None]
    # a long path repeats its segments: classify each kind once
    if known is None:
        known = {}
    for k, seg in enumerate(segs):
        if seg == "..":
            nxt = [r for node in readings for r in _up(node)]
            if len(nxt) > _MAX_READINGS:
                return UNSURE
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
                nxt.extend(_up(node))
        if len(nxt) > _MAX_READINGS:
            return UNSURE
        readings = nxt
    return CLEAR


#: At most this many brace results are walked for one command (words of
#: one result do not count); past it the command is :data:`UNSURE`.
_MAX_WALKED = 4096


def word_reach(word: str, dotglob: bool = False,
               known: dict[str, int] | None = None,
               budget: list[int] | None = None) -> str:
    """:data:`REACH` when some expansion of *word* (from :func:`words`)
    can be a path through aipager's folders, :data:`UNSURE` when this
    module cannot tell on a word that could name ``.config`` or
    ``.local``, else :data:`CLEAR`. *known* and *budget* (a one-item
    list, the brace results left) are shared by a command's words."""
    if not may_name_start(word, dotglob):
        return CLEAR
    if known is None:
        known = {}
    try:
        results = expand_braces(word)
        if budget is not None and len(results) > 1:
            budget[0] -= len(results)
            if budget[0] < 0:
                return UNSURE
        verdicts = [_walk(r, dotglob, known) for r in results]
    except Unsure:
        return UNSURE
    if REACH in verdicts:
        return REACH
    return UNSURE if UNSURE in verdicts else CLEAR


#: Words that turn on matching a leading ``.`` with ``*`` or ``?``.
_DOTGLOB_RE = re.compile(r"dotglob|GLOBIGNORE")


#: Quotes and backslashes, taken out before asking whether a copy could
#: hold a word worth expanding (``'.''.'`` is a ``..`` step).
_NO_QUOTES = str.maketrans("", "", "'\"\\")


def command_reaches(command: str, *more: str,
                    cache: dict | None = None) -> bool:
    """Whether any word of *command* (as typed) or of another copy of it
    (*more*: with quotes taken out) can reach aipager's folders, or
    cannot be told apart from one that does (:func:`word_reach`).

    *command* is read as bash would read it; when that read is not sure
    (something left open, see :func:`lex`), its words may hide part of
    the command, so it counts as reaching whenever any copy could name
    ``.config`` or ``.local`` at all. The other copies only add words.
    *cache*: see :func:`spelled`."""
    copies = (command, *more)
    dotglob = any(_DOTGLOB_RE.search(c) for c in copies)
    # Every word of a copy is made of its text, in order (a numeric
    # escape may be any character): when no copy could name ``.config``
    # or ``.local`` anywhere, no word can reach and nothing is unsure.
    if not any(_mentions(c, dotglob) for c in copies):
        return False
    seen: set[str] = set()
    known: dict[str, int] = {}
    budget = [_MAX_WALKED]
    # a command with no quotes reads the same both ways: lex it once
    for k, copy in enumerate(dict.fromkeys(copies)):
        # Only a word holding a glob, brace, group or ``..`` is checked
        # here (``_emit``; a plain path is the regex rules' part), and
        # such a word's characters come from its copy's text with quotes
        # taken out (never the ``(``, ``{`` or ``[`` that opens ``$(``,
        # ``${`` or ``$[``), or two dots ``$'...'`` decodes
        # (:func:`_may_give_worth`). Another copy with neither holds no
        # such word, read or hidden (too deep included), and its
        # sureness never counts: it is not read. (A ``\${`` brace bash
        # does expand leaves a ``$`` glued before its items, so it can't
        # spell a folder's name alone.) The command as typed is read
        # whenever it may hold an opening (``${``, ``$(``, a quote): one
        # left open refuses the command (see :func:`lex`).
        if k == 0:
            if not _WORTH_RE.search(copy.translate(_NO_QUOTES)) and (
                    "$'" not in copy):
                continue
        elif not _may_give_worth(copy):
            continue
        found, sure, _, deep = _read(copy, dotglob, cache)
        # a copy that nested too deep misses the words past that point
        # (a quote-free copy can nest where the command as typed does
        # not): fail closed whichever copy it is
        if deep or (k == 0 and not sure):
            return True
        for w in found:
            if w in seen:
                continue
            seen.add(w)
            if word_reach(w, dotglob, known, budget) != CLEAR:
                return True
    return False
