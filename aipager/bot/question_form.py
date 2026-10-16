"""Claude's question (AskUserQuestion) as shown in Telegram, whole.

The question and every option's description are shown as Claude wrote
them, as the terminal shows them. Only when the message would pass
Telegram's limit on one message's text is anything shortened: the
longest of the question and the descriptions first, each at a word
boundary and ending in "…", so a reader can see that text is missing.

Lengths are what Telegram counts: the text a reader sees (no tags,
entities decoded), in UTF-16 code units."""

from __future__ import annotations

import html as html_mod
import re

from aipager.bot.transport import TELEGRAM_MAX_TEXT_LEN

ELLIPSIS = "…"
#: A budget nothing reaches: the form as it is, never shortened.
UNLIMITED = 1 << 30
#: A cut goes back to the last space only when that keeps at least this
#: share of the room; a single long word is cut where it is.
_WORD_BACKOFF = 0.6
_TAG = re.compile(r"<[^>]+>")


def units(text: str) -> int:
    """*text*'s length in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def shown_len(html_text: str) -> int:
    """The length Telegram counts for *html_text*: its tags dropped and
    its entities decoded, in UTF-16 code units."""
    return units(html_mod.unescape(_TAG.sub("", html_text)))


def shorten(text: str, limit: int) -> str:
    """*text* when it fits *limit* UTF-16 units, else cut to fit, at a
    word boundary when one is near, ending in "…" (just "…" when only
    that fits). Empty when not even the "…" fits."""
    if units(text) <= limit:
        return text
    if limit < units(ELLIPSIS):
        return ""
    room = limit - units(ELLIPSIS)
    used, end = 0, 0
    for ch in text:
        step = units(ch)
        if used + step > room:
            break
        used += step
        end += 1
    head = text[:end]
    space = max(head.rfind(" "), head.rfind("\n"))
    if space > 0 and units(head[:space]) >= room * _WORD_BACKOFF:
        head = head[:space]
    return head.rstrip() + ELLIPSIS


def _description(opt) -> str:
    desc = opt.get("description", "") if isinstance(opt, dict) else ""
    return desc if isinstance(desc, str) else ""


def _cap_for(lengths: list[int], over: int) -> int | None:
    """The largest cap (at least one unit, the "…") that, applied to
    every length above it, saves at least *over* units; None when not
    even cutting them all to "…" does."""
    def savings(cap: int) -> int:
        return sum(n - cap for n in lengths if n > cap)
    if not lengths or savings(1) < over:
        return None
    lo, hi = 1, max(lengths)
    while lo < hi:                      # the largest cap with savings(cap) >= over
        mid = (lo + hi + 1) // 2
        if savings(mid) >= over:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _fit(texts: list[str], over: int) -> list[str]:
    """*texts* with the longest shortened first, down to one common
    length, to save at least *over* units. A text that is cut always
    ends in "…", so none vanishes unseen (each is cut to just "…" when
    even that does not save enough)."""
    cap = _cap_for([units(t) for t in texts], over)
    return [shorten(t, 1 if cap is None else cap) for t in texts]


def render(tr, question: str, options: list, *, lead: str,
           budget: int = TELEGRAM_MAX_TEXT_LEN) -> str:
    """*lead* (HTML), Claude's *question* and one row per option, as
    HTML whose shown length is at most *budget* (Telegram's per-message
    limit by default). Past the budget the longest of the question and
    the descriptions give first, down to one common length (a question
    shorter than that stays whole); then (only when they alone pass it)
    the labels in the rows; the buttons keep theirs. A
    text that is cut always ends in "…". The lead and the row
    numbers are never cut. A row whose label is ours (Claude gave none)
    reads right to left at both ends; Claude's own rows get no mark."""
    from aipager.bot.keyboards import OPTION

    ours = [not (isinstance(opt, dict) and "label" in opt) for opt in options]
    labels = [tr.text(OPTION, n=i + 1) if ours[i] else str(opt["label"])
              for i, opt in enumerate(options)]

    def build(q: str, descs: list[str], labels: list[str]) -> str:
        text = lead + html_mod.escape(q)
        for i in range(len(options)):
            row = f"{tr.num(i + 1)}. {html_mod.escape(labels[i])}"
            if descs[i]:
                row += f" - {html_mod.escape(descs[i])}"
            text += "\n  " + (tr.line(row, kind="html") if ours[i] else row)
        return text

    descs = [_description(opt) for opt in options]
    text = build(question, descs, labels)
    over = shown_len(text) - budget
    if over <= 0:
        return text
    texts = [question, *descs]
    if _cap_for([units(t) for t in texts], over) is not None:
        question, *descs = _fit(texts, over)
    else:
        # Only the labels can make the room: they give, the rest stays.
        labels = _fit(labels, over)
    text = build(question, descs, labels)
    over = shown_len(text) - budget
    if over > 0:
        question, *descs = _fit([question, *descs], over)
        text = build(question, descs, labels)
    return text
