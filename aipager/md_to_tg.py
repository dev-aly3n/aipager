"""Convert Markdown to Telegram-compatible HTML.

Telegram's Bot API supports a limited subset of HTML:
<b>, <i>, <code>, <pre>, <a href>, <blockquote>.

Processing order is critical to avoid double-escaping code blocks:
1. Extract fenced code blocks → placeholders
2. Extract inline code → placeholders
3. html.escape() the remaining text
4. Convert markdown syntax to HTML tags
5. Re-insert code/inline placeholders (already escaped separately)
"""

from __future__ import annotations

import html
import re
import uuid

_FENCED_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_LIST_RE = re.compile(r"^[-*]\s+", re.MULTILINE)


def markdown_to_telegram_html(md: str) -> str:
    """Convert markdown string to Telegram HTML."""
    # Phase 1: Extract fenced code blocks → placeholders
    fenced: dict[str, str] = {}

    def _stash_fenced(m: re.Match) -> str:
        lang = m.group(1)
        code = html.escape(m.group(2).rstrip("\n"))
        key = f"\x00FENCED{uuid.uuid4().hex}\x00"
        if lang:
            fenced[key] = f'<pre><code class="language-{html.escape(lang)}">{code}</code></pre>'
        else:
            fenced[key] = f"<pre>{code}</pre>"
        return key

    text = _FENCED_RE.sub(_stash_fenced, md)

    # Phase 2: Extract inline code → placeholders
    inline: dict[str, str] = {}

    def _stash_inline(m: re.Match) -> str:
        code = html.escape(m.group(1))
        key = f"\x00INLINE{uuid.uuid4().hex}\x00"
        inline[key] = f"<code>{code}</code>"
        return key

    text = _INLINE_RE.sub(_stash_inline, text)

    # Phase 3: Escape remaining HTML entities
    text = html.escape(text)

    # Phase 4: Convert markdown to HTML tags
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    text = _HEADER_RE.sub(r"<b>\1</b>", text)
    text = _LIST_RE.sub("• ", text)

    # Phase 5: Re-insert placeholders
    for key, replacement in fenced.items():
        text = text.replace(key, replacement)
    for key, replacement in inline.items():
        text = text.replace(key, replacement)

    return text.strip()
