"""Plain text out of an incoming Telegram rich message (Bot API 10.1+).

Claude's answers go out through ``sendRichMessage`` (``rich_message.py``).
When a person replies to one, Telegram delivers the replied-to message
with a ``rich_message`` field and no ``text`` or ``caption``, and a part
they highlighted comes as ``TextQuote.rich_texts`` (Bot API 10.4) with an
empty ``text``. python-telegram-bot 22.7 (Bot API 9.5) knows neither
field and keeps both in ``api_kwargs``.

These helpers turn that into the plain text a reply pointer shows Claude.
They read only, never raise, and skip whatever shape they do not know:
an unknown block or text type gives what it can (its ``text`` or its
``blocks``) and otherwise nothing. Media blocks give only their caption.

Shapes (core.telegram.org/bots/api, "Rich messages"):

- ``RichText``: a string, an array of ``RichText``, or an object with a
  ``type``. Most types carry their visible text in ``text``;
  ``custom_emoji`` has ``alternative_text``, ``mathematical_expression``
  has ``expression``, ``button`` has ``button.text``, ``anchor`` shows
  nothing.
- Blocks with nothing to read, such as ``anchor`` and ``buttons``, go
  through the generic reader, which finds no text in them.
- ``RichMessage``: ``{"blocks": [RichBlock, ...], "is_rtl": ...}``.
- ``RichBlock``: an object with a ``type``; text blocks carry ``text``,
  containers carry ``blocks`` (``blockquote``, ``collage``,
  ``slideshow``, ``details`` with a ``summary``), ``list`` has ``items``
  (each with ``label`` and ``blocks``), ``table`` has ``cells`` (rows of
  cells with ``text``), media blocks have a ``caption``
  (``RichBlockCaption``: ``text`` and ``credit``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

log = logging.getLogger(__name__)

# The first rich message read in a run logs its block types (names only,
# never content) at debug level: the live check of the shape.
_shape_logged = False
# Same for the first highlight from a rich message (types only). Seen
# live on 2026-11-12: ``rich_texts`` holds one item per block, with a
# block's bold and italic runs nested inside its item.
_quote_shape_logged = False

# Nesting deeper than this is ignored: a guard against a pathological
# shape, far beyond anything Telegram sends.
_MAX_DEPTH = 40


def rich_text_plain(value, _depth: int = 0) -> str:
    """The visible text of a ``RichText`` value; ``""`` for anything else."""
    if _depth > _MAX_DEPTH:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(rich_text_plain(v, _depth + 1) for v in value)
    if not isinstance(value, dict):
        return ""
    kind = value.get("type")
    if kind == "custom_emoji":
        alt = value.get("alternative_text")
        return alt if isinstance(alt, str) else ""
    if kind == "mathematical_expression":
        expr = value.get("expression")
        return expr if isinstance(expr, str) else ""
    if kind == "button":
        button = value.get("button")
        if isinstance(button, dict):
            return rich_text_plain(button.get("text"), _depth + 1)
        return ""
    return rich_text_plain(value.get("text"), _depth + 1)


def _caption_plain(caption, depth: int) -> str:
    """A ``RichBlockCaption`` (``text`` plus an optional ``credit``) or,
    for a table, a plain ``RichText``."""
    if isinstance(caption, dict) and "type" not in caption and (
            "text" in caption or "credit" in caption):
        parts = [rich_text_plain(caption.get("text"), depth + 1),
                 rich_text_plain(caption.get("credit"), depth + 1)]
        return " - ".join(p for p in parts if p.strip())
    return rich_text_plain(caption, depth + 1)


def _list_lines(items, depth: int, indent: str) -> list[str]:
    lines: list[str] = []
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        label = label.strip() if isinstance(label, str) else ""
        if item.get("has_checkbox"):
            label = (("[x] " if item.get("is_checked") else "[ ] ") + label).strip()
        lead = f"{indent}{label} " if label else indent
        inner = _blocks_lines(item.get("blocks"), depth + 1, indent + "  ")
        if not inner:
            if label:
                lines.append(lead.rstrip())
            continue
        lines.append(lead + inner[0][len(indent) + 2:])
        lines.extend(inner[1:])
    return lines


def _table_lines(cells, depth: int, indent: str) -> list[str]:
    lines: list[str] = []
    if not isinstance(cells, list):
        return lines
    for row in cells:
        if not isinstance(row, list):
            continue
        shown = [" ".join(rich_text_plain(c.get("text"), depth + 1).split())
                 for c in row if isinstance(c, dict)]
        if any(shown):
            lines.append(indent + " | ".join(shown))
    return lines


def _block_lines(block, depth: int, indent: str) -> list[str]:
    """The lines one ``RichBlock`` shows; ``[]`` for nothing readable."""
    if not isinstance(block, dict):
        return []
    kind = block.get("type")
    if kind == "divider":
        return [indent + "---"]
    if kind == "mathematical_expression":
        expr = block.get("expression")
        return [indent + expr] if isinstance(expr, str) and expr.strip() else []
    if kind == "list":
        return _list_lines(block.get("items"), depth, indent)
    if kind == "table":
        lines = _table_lines(block.get("cells"), depth, indent)
        cap = _caption_plain(block.get("caption"), depth)
        return lines + ([indent + cap] if cap.strip() else [])
    if kind == "pre":
        text = rich_text_plain(block.get("text"), depth + 1)
        return [indent + line for line in text.split("\n")] if text.strip() else []
    if kind == "blockquote":
        inner = _blocks_lines(block.get("blocks"), depth + 1, "")
        credit = rich_text_plain(block.get("credit"), depth + 1)
        if credit.strip():
            inner.append(credit)
        return [f"{indent}> {line}" for line in inner]
    if kind in ("expandable_blockquote", "pullquote"):
        text = rich_text_plain(block.get("text"), depth + 1)
        credit = rich_text_plain(block.get("credit"), depth + 1)
        inner = [line for line in text.split("\n")] if text.strip() else []
        if credit.strip():
            inner.append(credit)
        return [f"{indent}> {line}" for line in inner]
    if kind == "details":
        summary = rich_text_plain(block.get("summary"), depth + 1)
        lines = [indent + summary] if summary.strip() else []
        return lines + _blocks_lines(block.get("blocks"), depth + 1, indent)

    lines: list[str] = []
    text = rich_text_plain(block.get("text"), depth + 1)
    if text.strip():
        lines.extend(indent + line for line in text.split("\n"))
    lines.extend(_blocks_lines(block.get("blocks"), depth + 1, indent))
    if "caption" in block:
        cap = _caption_plain(block.get("caption"), depth)
        if cap.strip():
            lines.append(indent + cap)
    return lines


def _blocks_lines(blocks, depth: int, indent: str) -> list[str]:
    if depth > _MAX_DEPTH or not isinstance(blocks, list):
        return []
    lines: list[str] = []
    for block in blocks:
        lines.extend(_block_lines(block, depth + 1, indent))
    return lines


def rich_message_plain(rich) -> str:
    """The visible text of a ``RichMessage`` dict, one line per text line;
    ``""`` when it has nothing readable or is not a rich message."""
    global _shape_logged
    if not isinstance(rich, dict):
        return ""
    if not _shape_logged:
        _shape_logged = True
        blocks = rich.get("blocks")
        kinds = sorted({str(b.get("type")) for b in blocks if isinstance(b, dict)}
                       ) if isinstance(blocks, list) else []
        log.debug("reply to a rich message: keys %s, block types %s",
                  sorted(map(str, rich)), kinds)
    try:
        # Only blank ends go: a code block's first-line indent stays.
        return "\n".join(_blocks_lines(rich.get("blocks"), 0, "")).strip("\n")
    except Exception:      # never let a strange shape cost the person's message
        return ""


def _extra(obj, key: str):
    """A field PTB does not know: ``api_kwargs[key]``, or a real attribute
    once a newer PTB parses it."""
    value = getattr(obj, key, None)
    if value is not None:
        return value
    extra = getattr(obj, "api_kwargs", None)
    # PTB hands api_kwargs over read-only (a mappingproxy) once frozen.
    if isinstance(extra, Mapping):
        return extra.get(key)
    return None


def message_text(msg) -> str:
    """What a replied-to message says: its text, its caption, or the plain
    text of its rich message. ``""`` for a message with none of those."""
    if msg is None:
        return ""
    try:
        for field in ("text", "caption"):
            value = getattr(msg, field, None)
            if isinstance(value, str) and value:
                return value
        return rich_message_plain(_extra(msg, "rich_message"))
    except Exception:
        return ""


def _shape(value, depth: int = 0):
    """The type tree of a ``RichText`` value with every text left out:
    ``"s"`` for a string, a list for an array, the ``type`` of an object."""
    if depth > 6:
        return "..."
    if isinstance(value, str):
        return "s"
    if isinstance(value, list):
        return [_shape(v, depth + 1) for v in value[:20]]
    if isinstance(value, dict):
        return str(value.get("type"))
    return type(value).__name__


def quote_text(quote) -> str:
    """The highlighted part of a reply: ``TextQuote.text``, or the plain
    text of its ``rich_texts`` when it was chosen from a rich message, one
    line per block it spans."""
    if quote is None:
        return ""
    value = getattr(quote, "text", None)
    if isinstance(value, str) and value:
        return value
    global _quote_shape_logged
    try:
        rich_texts = _extra(quote, "rich_texts")
        if rich_texts is not None and not _quote_shape_logged:
            _quote_shape_logged = True
            log.debug("highlight from a rich message: rich_texts shape %s",
                      _shape(rich_texts))
        if isinstance(rich_texts, list):
            # One item per paragraph or list item: a line break between
            # them, as Telegram itself shows the quote.
            return "\n".join(rich_text_plain(item, 1) for item in rich_texts).strip()
        return rich_text_plain(rich_texts).strip()
    except Exception:
        return ""
