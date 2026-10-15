"""Reading footer text in tests (aipager/bot/footer.py).

The busy card's status line and every line of the agents block under an
answer are footer blocks: ``<footer>`` with HTML inside. Tests written
against their Markdown keep their meaning through these two helpers:

- :func:`footered` draws an expected Markdown block the way aipager does
  (each paragraph a footer, each ``"- "`` item a footer in its item);
- :func:`status_md` takes a card's last line, fails unless it IS a footer,
  and reads it back as Markdown (``<b>`` as ``**``, entities unescaped;
  backslash escapes are not restored).

Reading back cannot tell ``<b>x</b>`` from a literal ``**x**``, nor
``&amp;`` from ``&``: the conversion itself (bold, escapes, HTML escaping)
is pinned in tests/test_footer_text.py, against literals. :func:`footered`
draws the way ``footer.footer_block`` does, for expectations only.
"""

from __future__ import annotations

import html
import re

from aipager.bot.footer import CLOSE, OPEN, footer

_ITEM = "- "


def footered(md_block: str) -> str:
    """*md_block* (paragraphs, and lists of ``"- "`` items) drawn as
    footers."""
    out = []
    for para in md_block.split("\n\n"):
        items = para.split("\n")
        if all(item.startswith(_ITEM) for item in items):
            out.append("\n".join(_ITEM + footer(item[len(_ITEM):]) for item in items))
        else:
            out.append(footer(para))
    return "\n\n".join(out)


def as_md(footer_html: str) -> str:
    """One footer block read back as Markdown; fails unless it is one."""
    assert footer_html.startswith(OPEN) and footer_html.endswith(CLOSE), footer_html
    inner = footer_html[len(OPEN):-len(CLOSE)]
    assert OPEN not in inner, footer_html
    return html.unescape(inner.replace("<b>", "**").replace("</b>", "**"))


def unfooter(text: str) -> str:
    """*text* with every footer block read back as Markdown (:func:`as_md`):
    for checks written against the lines' Markdown, whatever surrounds
    them."""
    return re.sub(re.escape(OPEN) + r".*?" + re.escape(CLOSE),
                  lambda m: as_md(m.group(0)), text, flags=re.S)


def status_md(card: str) -> str:
    """The card's status line (its last line, a footer) as Markdown."""
    return as_md(card.rstrip().rsplit("\n\n", 1)[-1])
