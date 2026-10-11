"""Language-rule checks for the black-box tests of multilingual phase 3b
(``tests/integration/multilingual-phase-3b/test_blackbox_*.py``). A plain
module, imported by those tests, so no conftest can shadow it.

The rules come from the phase's entrypoints contract: Persian lines start
with a right-to-left mark, nothing invisible sits inside code, commands or
names, counts use Persian digits while versions and ids stay Latin, only
the left arrow, no em dash; Russian is Cyrillic with no invisible marks
and the right plural form for every count."""

from __future__ import annotations

import re

from aipager.i18n import plurals

RLM = "\u200f"
LRM = "\u200e"
FSI, PDI = "\u2068", "\u2069"
MARKS = (RLM, LRM, "\u2066", "\u2067", FSI, PDI, "\u061c")
EM_DASH = "\u2014"
RIGHT_ARROW = "\u2192"
LEFT_ARROW = "\u2190"

PERSIAN = re.compile("[\u0600-\u06ef\u06fa-\u06ff]")
PERSIAN_DIGIT = re.compile("[\u06f0-\u06f9]")
CYRILLIC = re.compile("[\u0400-\u04ff]")
_CODE = re.compile(r"<code>(.*?)</code>|<pre>(.*?)</pre>|`([^`]*)`", re.S)

#: Counts that exercise every Russian plural form and its traps.
RU_COUNTS = (1, 2, 5, 11, 12, 21, 22, 25)


def bare(text: str) -> str:
    """*text* with every invisible direction mark removed."""
    for m in MARKS:
        text = text.replace(m, "")
    return text


def fa_digits(n: int) -> str:
    return "".join(chr(0x06F0 + int(d)) for d in str(n))


def is_fa(text: str) -> bool:
    return bool(PERSIAN.search(text)) and not CYRILLIC.search(text)


def is_ru(text: str) -> bool:
    return bool(CYRILLIC.search(text)) and not PERSIAN.search(text)


def code_spans(text: str) -> list[str]:
    return ["".join(g for g in m.groups() if g) for m in _CODE.finditer(text)]


def fa_problems(text: str) -> list[str]:
    """Every Persian rule *text* breaks (empty when it follows them all)."""
    out = []
    if not PERSIAN.search(text):
        out.append("no Persian letters")
    for line in text.split("\n"):
        if PERSIAN.search(line) and not line.startswith(RLM):
            out.append(f"line without a leading RLM: {line!r}")
    if RIGHT_ARROW in text:
        out.append("right arrow")
    if EM_DASH in text:
        out.append("em dash")
    if CYRILLIC.search(text):
        out.append("Cyrillic in Persian")
    for span in code_spans(text):
        if any(m in span for m in MARKS):
            out.append(f"invisible mark inside code: {span!r}")
    return out


def ru_problems(text: str) -> list[str]:
    out = []
    if not CYRILLIC.search(text):
        out.append("no Cyrillic letters")
    if any(m in text for m in MARKS):
        out.append("invisible mark in Russian")
    if PERSIAN.search(text) or PERSIAN_DIGIT.search(text):
        out.append("Persian in Russian")
    if EM_DASH in text:
        out.append("em dash")
    return out


def _template(text: str, n: int) -> str:
    return re.sub(rf"(?<![\d.]){n}(?![\d.])", "{n}", text, count=1)


def ru_plural_problems(render, counts=RU_COUNTS) -> list[str]:
    """*render(n)* for every count in *counts* (``RU_COUNTS``): the counts of one
    plural category read the same once the number is taken out (so the form
    follows the category, not ``n == 1``), and every text shows its number.
    Two categories may share a form ("5 раз", "21 раз")."""
    out = []
    by_cat: dict[str, set[str]] = {}
    for n in counts:
        text = render(n)
        if not re.search(rf"(?<![\d.]){n}(?![\d.])", text):
            out.append(f"{n}: number missing from {text!r}")
        by_cat.setdefault(plurals.category("ru", n), set()).add(_template(text, n))
    for cat, texts in by_cat.items():
        if len(texts) != 1:
            out.append(f"{cat}: several forms {sorted(texts)!r}")
    return out
