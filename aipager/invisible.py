"""Invisible characters in a message aipager types into Claude Code.

Claude Code (2.1.277+, measured on 2.1.310) cleans a typed prompt when
Enter is pressed: it removes invisible formatting characters, and an Enter
that removed anything sends nothing ("Removed N invisible characters ·
review and press Enter to send"). A Telegram message holding one (a
zero-width space copied from a web page, a stray direction mark, a byte
order mark) stayed in Claude Code's input box, never sent (roadmap 8.124).

:func:`clean` removes what Claude Code always removes and turns its line
break variants into a plain newline, so the text aipager types (and notes
as the message's body) is the text Claude Code sends. A few characters are
kept or removed by context (a half-space inside a Persian word, a direction
mark in a right-to-left line, the joiner inside an emoji sequence): those
are kept wherever Claude Code may keep them, judged by Unicode block (kept
when unsure), and removed where they clearly do nothing, such as in Latin
text. :func:`may_hold_back` says a cleaned text still holds one, so Claude
Code might hold it back after all (the caller then presses Enter once more).

Pure, no I/O; mirrors Claude Code's cleaner (its ``fye``, flag
tengu_tranquil_cloud) as read from the 2.1.310 bundle.
"""

from __future__ import annotations

import bisect
import unicodedata

# -- the characters Claude Code looks at -------------------------------------

#: Line breaks other than LF: Claude Code turns each into LF (a CR right
#: before an LF is dropped).
_LINE_BREAKS = frozenset({0x0B, 0x0C, 0x0D, 0x85, 0x2028, 0x2029})

ZWSP, ZWNJ, ZWJ, LRM, RLM, ALM = 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x061C
CGJ, VS15, VS16, KEYCAP = 0x034F, 0xFE0E, 0xFE0F, 0x20E3
_BLACK_FLAG, _CANCEL_TAG = 0x1F3F4, 0xE007F
#: The subdivision flags Claude Code keeps (England, Scotland, Wales).
_FLAG_TAGS = frozenset({"gbeng", "gbsct", "gbwls"})


def _hidden(cp: int) -> bool:
    """Every character Claude Code's cleaner considers (its ``L``)."""
    if cp < 0xA0:
        return (cp < 0x20 and cp not in (0x09, 0x0A)) or cp >= 0x7F
    if cp < 0x2000:
        return cp in (0xAD, CGJ, ALM, 0x115F, 0x1160, 0x17B4, 0x17B5) or 0x180B <= cp <= 0x180F
    if cp < 0x10000:
        return (0x200B <= cp <= 0x200F or 0x2028 <= cp <= 0x202E or 0x2060 <= cp <= 0x206F
                or cp == 0x3164 or 0xFE00 <= cp <= 0xFE0F or cp in (0xFEFF, 0xFFA0)
                or 0xFFF0 <= cp <= 0xFFFB)
    return (cp == 0x1107F or 0x13430 <= cp <= 0x1343F or cp == 0x16FE4
            or 0x1BCA0 <= cp <= 0x1BCA3 or 0x1D173 <= cp <= 0x1D17A
            or 0xE0000 <= cp <= 0xE0FFF)


#: The hidden characters Claude Code keeps in some contexts; every other
#: hidden character it always removes.
_CONDITIONAL = frozenset({ZWSP, ZWNJ, ZWJ, LRM, RLM, ALM, CGJ, VS15, VS16,
                          0xFE00, 0xFE01, 0xFE02, 0x17B4, 0x17B5,
                          0x180B, 0x180C, 0x180D, 0x180E, 0x180F, 0x1107F,
                          0x1BCA0, 0x1BCA1, 0x1BCA2, 0x1BCA3, *range(0x13430, 0x13440)})


# -- scripts, by Unicode block ------------------------------------------------

def _in(cp: int | None, ranges) -> bool:
    return cp is not None and any(lo <= cp <= hi for lo, hi in ranges)


#: Scripts where ZWNJ/ZWJ shape letters: Arabic, Syriac, Thaana, NKo,
#: Mongolian, the Indic scripts, Sinhala, Tibetan, Myanmar, Khmer, Tifinagh.
_JOINING = ((0x0600, 0x07FF), (0x0860, 0x08FF), (0x0900, 0x0DFF), (0x0F00, 0x0FFF),
            (0x1000, 0x109F), (0x1780, 0x18AF), (0x19E0, 0x19FF), (0x1CD0, 0x1CFF),
            (0x2D30, 0x2D7F), (0xA8E0, 0xA8FF), (0xA980, 0xA9FF), (0xA9E0, 0xA9FF),
            (0xAA60, 0xAA7F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF), (0x10D00, 0x10D3F),
            (0x10EC0, 0x10EFF), (0x11000, 0x1107F), (0x116D0, 0x116FF),
            (0x1E900, 0x1E95F), (0x1EE00, 0x1EEFF))
#: Scripts written without spaces where ZWSP marks word breaks.
_SPACELESS = ((0x0E00, 0x0EFF), (0x1000, 0x109F), (0x1780, 0x17FF), (0x19E0, 0x19FF),
              (0xA9E0, 0xA9FF), (0xAA60, 0xAA7F), (0x116D0, 0x116FF))
#: Right-to-left letters (Hebrew, Arabic and the other RTL scripts).
_RTL = ((0x0590, 0x08FF), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF), (0x10800, 0x10FFF),
        (0x1E800, 0x1EFFF))
#: Arabic-script letters, for the Arabic letter mark.
_ARABIC = ((0x0600, 0x07BF), (0x0860, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
           (0x10D00, 0x10D3F), (0x10EC0, 0x10EFF), (0x1EE00, 0x1EEFF))


def _emoji_like(cp: int | None) -> bool:
    return cp is not None and (cp >= 0x1F000 or 0x2000 <= cp <= 0x33FF
                               or cp in (0xA9, 0xAE, VS16))


def _mark(cp: int | None) -> bool:
    return cp is not None and unicodedata.category(chr(cp)).startswith("M")


# -- the cleaner ----------------------------------------------------------------

def _keeps(cp: int, prev: int | None, nxt: int | None, line: tuple[bool, bool],
           base: int | None = None) -> bool:
    """Whether Claude Code may keep conditional character *cp* between
    *prev* and *nxt* (the visible characters around it; *base*: the last
    one before it that is not a combining mark) in a line that holds an
    RTL letter / an Arabic-script letter (*line*). A superset of Claude
    Code's rules: kept when unsure."""
    if cp == ZWNJ:
        return _in(prev, _JOINING) or _in(base, _JOINING) or _in(nxt, _JOINING)
    if cp == ZWJ:
        return (_in(prev, _JOINING) or _in(base, _JOINING) or _in(nxt, _JOINING)
                or _emoji_like(prev) or _emoji_like(nxt))
    if cp == ZWSP:
        return _in(prev, _SPACELESS) or _in(nxt, _SPACELESS)
    if cp in (LRM, RLM):
        return line[0]
    if cp == ALM:
        return line[1]
    if cp in (VS15, VS16):
        return (prev is not None and prev >= 0xA9) or (
            prev is not None and chr(prev) in "0123456789#*" and nxt == KEYCAP)
    if cp == CGJ:
        return _mark(prev) or _mark(nxt)
    if cp in (0x17B4, 0x17B5):
        return _in(prev, ((0x1780, 0x17FF),)) or _in(nxt, ((0x1780, 0x17FF),))
    if 0x180B <= cp <= 0x180F:
        return _in(prev, ((0x1800, 0x18AF),)) or _in(nxt, ((0x1800, 0x18AF),))
    if cp == 0x1107F:
        return _in(prev, ((0x11000, 0x1107F),)) and _in(nxt, ((0x11000, 0x1107F),))
    if 0x13430 <= cp <= 0x1343F:
        return _in(prev, ((0x13000, 0x1345F),)) or _in(nxt, ((0x13000, 0x1345F),))
    if 0x1BCA0 <= cp <= 0x1BCA3:
        return _in(prev, ((0x1BC00, 0x1BCAF),)) or _in(nxt, ((0x1BC00, 0x1BCAF),))
    return True                                      # VS1-VS3: rare, kept


def _flag_end(cps: list[int], i: int) -> int:
    """After a black flag at ``cps[i]``: the index of the cancel tag that
    ends one of the subdivision flags Claude Code keeps, else -1."""
    letters = ""
    for j in range(i + 1, min(len(cps), i + 7)):
        cp = cps[j]
        if cp == _CANCEL_TAG:
            return j if letters in _FLAG_TAGS else -1
        if not 0xE0061 <= cp <= 0xE007A:
            return -1
        letters += chr(cp - 0xE0000)
    return -1


def _letter_in(cp: int, ranges) -> bool:
    return _in(cp, ranges) and unicodedata.category(chr(cp)).startswith("L")


def _line_flags(cps: list[int]) -> list[tuple[bool, bool]]:
    """For each character: whether its line holds an RTL letter and an
    Arabic-script letter (for the direction marks; once per line, as
    Claude Code caches it)."""
    out: list[tuple[bool, bool]] = []
    start = 0
    for i in range(len(cps) + 1):
        if i == len(cps) or cps[i] == 0x0A:
            line = cps[start:i]
            flags = (any(_letter_in(c, _RTL) for c in line),
                     any(_letter_in(c, _ARABIC) for c in line))
            out.extend([flags] * (i - start + (i < len(cps))))
            start = i + 1
    return out


def clean(text: str) -> str:
    """*text* as Claude Code would send it: its line breaks as LF, every
    always-removed invisible character gone, and the context-dependent ones
    kept only where Claude Code may keep them (see the module note)."""
    if all(c in "\t\n" or " " <= c <= "~" for c in text):
        return text
    cps: list[int] = []
    for i, ch in enumerate(text):
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:          # a lone surrogate: as Claude Code does
            cp = 0xFFFD
        if cp in _LINE_BREAKS:
            if cp == 0x0D and text[i + 1:i + 2] == "\n":
                continue
            cp = 0x0A
        cps.append(cp)
    lines = _line_flags(cps)
    visible = [i for i, cp in enumerate(cps) if not _hidden(cp)]
    out: list[str] = []
    base: int | None = None             # the last visible character that is not a mark
    i = 0
    while i < len(cps):
        cp = cps[i]
        if cp == _BLACK_FLAG:
            end = _flag_end(cps, i)
            if end != -1:
                out.extend(chr(c) for c in cps[i:end + 1])
                base = cp
                i = end + 1
                continue
        if not _hidden(cp):
            out.append(chr(cp))
            if not _mark(cp):
                base = cp
        elif cp in _CONDITIONAL:
            at = bisect.bisect_left(visible, i)
            prev = cps[visible[at - 1]] if at > 0 else None
            nxt = cps[visible[at]] if at < len(visible) else None
            if _keeps(cp, prev, nxt, lines[i], base):
                out.append(chr(cp))
        i += 1
    return "".join(out)


def may_hold_back(text: str) -> bool:
    """Whether Claude Code might still hold *text* (already :func:`clean`)
    back: it holds a context-dependent invisible character."""
    return any(ord(c) in _CONDITIONAL for c in text)


def without_context_dependent(text: str) -> str:
    """*text* without the context-dependent invisible characters: what a
    message aipager noted still matches once Claude Code removed one (its
    cleaner on a second Enter). Used by the note matching
    (``policy_snapshot``); everything else it removes is already gone from
    both sides."""
    if not any(ord(c) in _CONDITIONAL for c in text):
        return text
    return "".join(c for c in text if ord(c) not in _CONDITIONAL)
