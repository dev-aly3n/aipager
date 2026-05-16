"""Tests for aipager.md_to_tg — markdown → Telegram-HTML converter."""

from aipager.md_to_tg import markdown_to_telegram_html


def test_fenced_code_block_escapes_html_chars():
    html = markdown_to_telegram_html('```python\nprint("<hi>")\n```')
    assert "<pre>" in html
    assert "&lt;hi&gt;" in html
    assert 'class="language-python"' in html


def test_fenced_code_block_without_language():
    html = markdown_to_telegram_html("```\nplain code\n```")
    assert "<pre>plain code</pre>" in html


def test_inline_code():
    html = markdown_to_telegram_html("use `printf` here")
    assert "<code>printf</code>" in html


def test_bold():
    html = markdown_to_telegram_html("this is **bold** text")
    assert "<b>bold</b>" in html


def test_italic():
    html = markdown_to_telegram_html("this is *italic* text")
    assert "<i>italic</i>" in html


def test_link():
    html = markdown_to_telegram_html("[click](https://example.com)")
    assert '<a href="https://example.com">click</a>' in html


def test_header_becomes_bold():
    html = markdown_to_telegram_html("# Heading")
    assert "<b>Heading</b>" in html


def test_list_bullet():
    html = markdown_to_telegram_html("- item")
    assert "• item" in html


def test_no_double_escape_in_code_block():
    """Special chars inside code blocks must be escaped exactly once."""
    html = markdown_to_telegram_html("```\n<a>&\n```")
    assert "&lt;a&gt;&amp;" in html
    assert "&amp;amp;" not in html
