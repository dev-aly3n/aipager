"""The Unicode Bidirectional Algorithm (UAX #9) in pure Python, for tests.

Telegram for iPhone right-aligns a laid-out line only when the glyph run
at its LEFT edge is right to left (the line's logical end, in a line that
reads right to left). :func:`sits_right_on_iphone` judges a line that
way: the whole algorithm (P2-P3, X1-X10 with isolating run sequences,
W1-W7, N0 with the paired brackets, N1-N2, I1-I2, L1, L2), then the
visually leftmost character that is not whitespace or a direction
control must be at an odd level.

Pinned against ICU in tests/golden/bidi_icu_cases.json (recorded once,
locally, with ICU 74; CI never needs ICU). Classes come from
``unicodedata.bidirectional``; a character Python does not know gets L.
One paragraph per call: a line, not a message.

Where ICU 74 departs from UAX #9 this follows UAX #9 (and the Unicode
reference implementation); found by a local random cross-check, never in
aipager's text: a nonspacing mark right after a bracket pair that N0
resolved against the embedding direction from the preceding context
(N0 c.1) takes the bracket's type here, ICU leaves it; and an unmatched
PDI inside an override takes the override's type here (X6a), ICU treats
it as whitespace. ICU also reports every level as the paragraph level
for text in one direction only ("levels don't matter"); the visual order
is the same.
"""

from __future__ import annotations

import html
import re
import unicodedata

MAX_DEPTH = 125

#: The paired brackets of BidiBrackets.txt (Unicode 15.1): opening ->
#: closing. U+2329/U+232A pair with U+3008/U+3009 too (canonically
#: equivalent), see :data:`_CANONICAL`.
BRACKETS: dict[str, str] = {chr(a): chr(b) for a, b in (
    (0x0028, 0x0029), (0x005B, 0x005D), (0x007B, 0x007D), (0x0F3A, 0x0F3B),
    (0x0F3C, 0x0F3D), (0x169B, 0x169C), (0x2045, 0x2046), (0x207D, 0x207E),
    (0x208D, 0x208E), (0x2308, 0x2309), (0x230A, 0x230B), (0x2329, 0x232A),
    (0x2768, 0x2769), (0x276A, 0x276B), (0x276C, 0x276D), (0x276E, 0x276F),
    (0x2770, 0x2771), (0x2772, 0x2773), (0x2774, 0x2775), (0x27C5, 0x27C6),
    (0x27E6, 0x27E7), (0x27E8, 0x27E9), (0x27EA, 0x27EB), (0x27EC, 0x27ED),
    (0x27EE, 0x27EF), (0x2983, 0x2984), (0x2985, 0x2986), (0x2987, 0x2988),
    (0x2989, 0x298A), (0x298B, 0x298C), (0x298D, 0x2990), (0x298F, 0x298E),
    (0x2991, 0x2992), (0x2993, 0x2994), (0x2995, 0x2996), (0x2997, 0x2998),
    (0x29D8, 0x29D9), (0x29DA, 0x29DB), (0x29FC, 0x29FD), (0x2E22, 0x2E23),
    (0x2E24, 0x2E25), (0x2E26, 0x2E27), (0x2E28, 0x2E29), (0x2E55, 0x2E56),
    (0x2E57, 0x2E58), (0x2E59, 0x2E5A), (0x2E5B, 0x2E5C), (0x3008, 0x3009),
    (0x300A, 0x300B), (0x300C, 0x300D), (0x300E, 0x300F), (0x3010, 0x3011),
    (0x3014, 0x3015), (0x3016, 0x3017), (0x3018, 0x3019), (0x301A, 0x301B),
    (0xFE59, 0xFE5A), (0xFE5B, 0xFE5C), (0xFE5D, 0xFE5E), (0xFF08, 0xFF09),
    (0xFF3B, 0xFF3D), (0xFF5B, 0xFF5D), (0xFF5F, 0xFF60), (0xFF62, 0xFF63),
)}
_CLOSERS: dict[str, str] = {b: a for a, b in BRACKETS.items()}
_CANONICAL = {"\u2329": "\u3008", "\u232a": "\u3009"}

_ISOLATE_INIT = frozenset(("LRI", "RLI", "FSI"))
_REMOVED = frozenset(("RLE", "LRE", "RLO", "LRO", "PDF", "BN"))
_NI = frozenset(("B", "S", "WS", "ON", "LRI", "RLI", "FSI", "PDI"))
_STRONG = frozenset(("L", "R", "AL"))


def bidi_class(ch: str) -> str:
    """The Bidi_Class of *ch*; L for a character this Python does not
    know (unassigned)."""
    return unicodedata.bidirectional(ch) or "L"


def _matching_pdi(classes: list[str]) -> dict[int, int]:
    """BD9: each isolate initiator's matching PDI (by index)."""
    match: dict[int, int] = {}
    stack: list[int] = []
    for i, c in enumerate(classes):
        if c in _ISOLATE_INIT:
            stack.append(i)
        elif c == "PDI" and stack:
            match[stack.pop()] = i
        elif c == "B":
            stack.clear()
    return match


def _first_strong_level(classes: list[str], start: int, end: int,
                        match: dict[int, int]) -> int | None:
    """P2-P3 over ``classes[start:end]``: 1 or 0 from the first strong
    character outside isolates, None when there is none."""
    i = start
    while i < end:
        c = classes[i]
        if c in _ISOLATE_INIT:
            j = match.get(i)
            if j is None:
                return None
            i = j + 1
            continue
        if c == "L":
            return 0
        if c in ("R", "AL"):
            return 1
        i += 1
    return None


def paragraph_level(text: str) -> int:
    """P2-P3: 1 when the first strong character outside isolates is right
    to left, else 0."""
    classes = [bidi_class(ch) for ch in text]
    return _first_strong_level(classes, 0, len(classes), _matching_pdi(classes)) or 0


def _explicit(classes: list[str], para: int, match: dict[int, int]) -> list[int]:
    """X1-X8: the embedding level of every character; *classes* is
    updated for overrides (X6, X5a-c)."""
    n = len(classes)
    levels = [para] * n
    # (level, override class or None, isolate)
    stack: list[tuple[int, str | None, bool]] = [(para, None, False)]
    over_isolate = over_embed = valid_isolate = 0
    original = list(classes)
    for i in range(n):
        c = original[i]
        level, override, _iso = stack[-1]
        if c in ("RLE", "LRE", "RLO", "LRO"):
            new = (level + 1) | 1 if c in ("RLE", "RLO") else (level + 2) & ~1
            levels[i] = level
            if new <= MAX_DEPTH and not over_isolate and not over_embed:
                ov = "R" if c == "RLO" else "L" if c == "LRO" else None
                stack.append((new, ov, False))
            elif not over_isolate:
                over_embed += 1
        elif c in _ISOLATE_INIT:
            levels[i] = level
            if override:
                classes[i] = override
            rtl = c == "RLI"
            if c == "FSI":
                end = match.get(i, n)
                rtl = _first_strong_level(original, i + 1, end, match) == 1
            new = (level + 1) | 1 if rtl else (level + 2) & ~1
            if new <= MAX_DEPTH and not over_isolate and not over_embed:
                valid_isolate += 1
                stack.append((new, None, True))
            else:
                over_isolate += 1
        elif c == "PDI":
            if over_isolate:
                over_isolate -= 1
            elif valid_isolate:
                over_embed = 0
                while not stack[-1][2]:
                    stack.pop()
                stack.pop()
                valid_isolate -= 1
            level, override, _iso = stack[-1]
            levels[i] = level
            if override:
                classes[i] = override
        elif c == "PDF":
            if over_isolate:
                pass
            elif over_embed:
                over_embed -= 1
            elif not stack[-1][2] and len(stack) >= 2:
                stack.pop()
            levels[i] = stack[-1][0]
        elif c == "B":
            levels[i] = para
        elif c == "BN":
            levels[i] = level
        else:
            levels[i] = level
            if override:
                classes[i] = override
    return levels


def _sequences(classes: list[str], levels: list[int], match: dict[int, int],
               kept: list[int]) -> list[list[int]]:
    """X10 / BD13: the isolating run sequences, as lists of indices of
    the characters X9 keeps (*classes*: the original ones)."""
    runs: list[list[int]] = []
    for i in kept:
        if runs and levels[runs[-1][-1]] == levels[i]:
            runs[-1].append(i)
        else:
            runs.append([i])
    run_of = {run[0]: run for run in runs}
    pdi_of = {v: k for k, v in match.items()}
    seqs: list[list[int]] = []
    for run in runs:
        first = run[0]
        if classes[first] == "PDI" and first in pdi_of:
            continue  # it continues the sequence of its initiator
        seq = list(run)
        while classes[seq[-1]] in _ISOLATE_INIT and seq[-1] in match:
            nxt = run_of.get(match[seq[-1]])
            if nxt is None:
                break
            seq.extend(nxt)
        seqs.append(seq)
    return seqs


def _resolve_sequence(seq: list[int], types: list[str], original: list[str],
                      levels: list[int], sos: str, eos: str, text: str) -> None:
    """W1-W7, N0-N2 over one isolating run sequence (*types* updated)."""
    t = [types[i] for i in seq]
    n = len(t)
    # W1
    for k in range(n):
        if t[k] == "NSM":
            if k == 0:
                t[k] = sos
            elif t[k - 1] in _ISOLATE_INIT or t[k - 1] == "PDI":
                t[k] = "ON"
            else:
                t[k] = t[k - 1]
    # W2, W3
    last = sos
    for k in range(n):
        if t[k] in _STRONG:
            last = t[k]
        elif t[k] == "EN" and last == "AL":
            t[k] = "AN"
    t = ["R" if x == "AL" else x for x in t]
    # W4
    for k in range(1, n - 1):
        if t[k] == "ES" and t[k - 1] == "EN" and t[k + 1] == "EN":
            t[k] = "EN"
        elif t[k] == "CS" and t[k - 1] == t[k + 1] and t[k - 1] in ("EN", "AN"):
            t[k] = t[k - 1]
    # W5
    k = 0
    while k < n:
        if t[k] != "ET":
            k += 1
            continue
        j = k
        while j < n and t[j] == "ET":
            j += 1
        if (k > 0 and t[k - 1] == "EN") or (j < n and t[j] == "EN"):
            for x in range(k, j):
                t[x] = "EN"
        k = j
    # W6
    t = ["ON" if x in ("ES", "ET", "CS") else x for x in t]
    # W7
    last = sos
    for k in range(n):
        if t[k] in ("L", "R"):
            last = t[k]
        elif t[k] == "EN" and last == "L":
            t[k] = "L"
    # N0
    level = levels[seq[0]]
    embed = "R" if level & 1 else "L"
    chars = [text[i] for i in seq]

    def strong(x: str) -> str | None:
        return "L" if x == "L" else "R" if x in ("R", "EN", "AN") else None

    pairs: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []
    for k in range(n):
        if t[k] != "ON":
            continue
        ch = _CANONICAL.get(chars[k], chars[k])
        if chars[k] in BRACKETS:
            if len(stack) == 63:
                break
            close = BRACKETS[chars[k]]
            stack.append((_CANONICAL.get(close, close), k))
        elif chars[k] in _CLOSERS:
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][0] == ch:
                    pairs.append((stack[depth][1], k))
                    del stack[depth:]
                    break
    for o, c in sorted(pairs):
        inside = {strong(t[x]) for x in range(o + 1, c)} - {None}
        if not inside:
            continue
        if embed in inside:
            new = embed
        else:
            before = sos
            for x in range(o - 1, -1, -1):
                s = strong(t[x])
                if s:
                    before = s
                    break
            new = before if before != embed else embed
        t[o] = t[c] = new
        for b in (o, c):
            x = b + 1
            while x < n and original[seq[x]] == "NSM":
                t[x] = new
                x += 1
    # N1, N2
    k = 0
    while k < n:
        if t[k] not in _NI:
            k += 1
            continue
        j = k
        while j < n and t[j] in _NI:
            j += 1
        before = sos if k == 0 else strong(t[k - 1])
        after = eos if j == n else strong(t[j])
        new = before if before is not None and before == after else embed
        for x in range(k, j):
            t[x] = new
        k = j
    for k, i in enumerate(seq):
        types[i] = t[k]


def levels(text: str, para: int | None = None) -> list[int | None]:
    """The resolved level of each character of *text* (one paragraph)
    after rule L1; None for a character X9 removes (BN and the
    embedding and override controls). *para* is the paragraph level,
    found by P2-P3 when None."""
    original = [bidi_class(ch) for ch in text]
    classes = list(original)
    match = _matching_pdi(classes)
    if para is None:
        para = _first_strong_level(classes, 0, len(classes), match) or 0
    lv = _explicit(classes, para, match)
    kept = [i for i, c in enumerate(original) if c not in _REMOVED]
    types = list(classes)
    for seq in _sequences(original, lv, match, kept):
        level = lv[seq[0]]
        pos = kept.index(seq[0])
        prev = lv[kept[pos - 1]] if pos > 0 else para
        sos = "R" if max(level, prev) & 1 else "L"
        last = seq[-1]
        pos = kept.index(last)
        if original[last] in _ISOLATE_INIT or pos + 1 == len(kept):
            nxt = para
        else:
            nxt = lv[kept[pos + 1]]
        eos = "R" if max(level, nxt) & 1 else "L"
        _resolve_sequence(seq, types, original, lv, sos, eos, text)
    out: list[int | None] = [None] * len(text)
    for i in kept:
        level, c = lv[i], types[i]
        if level & 1:
            level += 1 if c in ("L", "EN", "AN") else 0
        else:
            level += 1 if c == "R" else 2 if c in ("AN", "EN") else 0
        out[i] = level
    # L1: segment and paragraph separators, and the whitespace and
    # isolate controls before them or at the line end, go to the
    # paragraph level (on the ORIGINAL classes).
    trailing = True
    for i in range(len(text) - 1, -1, -1):
        c = original[i]
        if c in ("S", "B"):
            out[i] = para
            trailing = True
        elif c in ("WS", "LRI", "RLI", "FSI", "PDI"):
            if trailing:
                out[i] = para
        elif c in _REMOVED:
            continue
        else:
            trailing = False
    return out


def visual_order(text: str, para: int | None = None) -> list[int]:
    """L2: the indices of *text*'s characters from left to right as
    shown, without the characters X9 removes."""
    lv = levels(text, para)
    order = [i for i, level in enumerate(lv) if level is not None]
    shown = [lv[i] for i in order]
    if not shown:
        return []
    top = max(shown)
    low_odd = min((x for x in shown if x & 1), default=None)
    if low_odd is None:
        return order
    for level in range(top, low_odd - 1, -1):
        k = 0
        while k < len(order):
            if shown[k] < level:
                k += 1
                continue
            j = k
            while j < len(order) and shown[j] >= level:
                j += 1
            order[k:j] = order[k:j][::-1]
            shown[k:j] = shown[k:j][::-1]
            k = j
    return order


# ---- the shown text of a message ---------------------------------------------------

_PRE = re.compile(r"<pre\b[^>]*>.*?</pre\s*>|```.*?```", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_MD_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_MD_MARKUP = re.compile(r"\|\||[`*~]")
_PRE_SLOT = "\x00"


def shown_text(text: str) -> str:
    """*text* (HTML, the card's Markdown or plain text) as shown: each
    ``<pre>`` block (or ``` fence) on a line of its own holding only
    U+0000, tags removed (what ``<code>`` holds is kept), entities
    unescaped, and the card's Markdown markup (`` ` ``, ``*``, ``~``,
    ``||`` and the backslash of an escape) dropped. Dropping a neutral
    only makes :func:`sits_right_on_iphone` stricter."""
    text = _PRE.sub("\n" + _PRE_SLOT + "\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    out: list[str] = []
    pos = 0
    for m in _MD_ESCAPE.finditer(text):
        out.append(_MD_MARKUP.sub("", text[pos:m.start()]))
        out.append(m.group(1))
        pos = m.end()
    out.append(_MD_MARKUP.sub("", text[pos:]))
    return "".join(out)


def shown_lines(text: str) -> list[str]:
    """The lines of :func:`shown_text`, without the ``<pre>`` blocks."""
    return [line for line in shown_text(text).split("\n") if line != _PRE_SLOT]


def has_rtl_letter(line: str) -> bool:
    """Whether *line* holds a real right-to-left letter (not U+200F, not
    U+061C, not a digit or Arabic punctuation)."""
    return any(unicodedata.category(ch).startswith("L") and bidi_class(ch) in ("R", "AL")
               for ch in line)


#: What the judgement looks past at a line's left edge: whitespace and
#: the direction controls that are not strong marks. U+200E and U+200F
#: are kept: they are strong, with their own level.
_SKIPPED = frozenset(("WS", "S", "B", "LRI", "RLI", "FSI", "PDI",
                      "LRE", "RLE", "LRO", "RLO", "PDF", "BN"))


def leftmost(line: str) -> int | None:
    """The index of *line*'s visually leftmost character that is not
    whitespace or a direction control (None when there is none)."""
    for i in visual_order(line):
        ch = line[i]
        if bidi_class(ch) in _SKIPPED or ch.isspace():
            continue
        return i
    return None


def sits_right_on_iphone(line: str) -> bool:
    """Whether Telegram for iPhone right-aligns *line* (one shown line):
    its visually leftmost character, past whitespace and direction
    controls, is at an odd level. Stricter than the app may be: a
    trailing space or isolate control never counts on its own."""
    i = leftmost(line)
    if i is None:
        return False
    return bool(levels(line)[i] & 1)
