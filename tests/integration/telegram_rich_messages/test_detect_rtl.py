"""Integration: detect_rtl — equivalence partitioning and boundary cases.

Success criteria covered:
  SC8  - detect_rtl("سلام دنیا") is True; detect_rtl("hello world") is False;
         detect_rtl("") is False

Additional equivalence classes:
  - Pure punctuation / digits only → False
  - Arabic text → True (Arabic is RTL, shares the Unicode range)
  - Mixed Persian + English identifiers where Persian dominates → True
  - Mixed Persian + English identifiers where English dominates → False
  - Long string: RTL chars only beyond position 2000 (sample is first 2000)
  - Hebrew text → True

Boundary analysis:
  - Exactly equal RTL and LTR counts → False (rtl > ltr required, not >=)
  - One RTL char more than LTR → True
"""

from __future__ import annotations

from aipager.bot.rich_message import detect_rtl


# ── SC8 spec examples ────────────────────────────────────────────────────────

def test_sc8_persian_hello_world_is_true():
    assert detect_rtl("سلام دنیا") is True


def test_sc8_english_hello_world_is_false():
    assert detect_rtl("hello world") is False


def test_sc8_empty_string_is_false():
    assert detect_rtl("") is False


# ── equivalence classes ──────────────────────────────────────────────────────

def test_detect_rtl_arabic_is_true():
    """Arabic text (also RTL) → True."""
    assert detect_rtl("مرحبا بالعالم") is True


def test_detect_rtl_hebrew_is_true():
    """Hebrew text → True."""
    assert detect_rtl("שלום עולם") is True


def test_detect_rtl_digits_only_is_false():
    """Pure digits have no letter class → False."""
    assert detect_rtl("1234567890") is False


def test_detect_rtl_punctuation_only_is_false():
    """Punctuation only → False."""
    assert detect_rtl("!@#$%^&*().,;:") is False


def test_detect_rtl_whitespace_only_is_false():
    """Whitespace only → False."""
    assert detect_rtl("   \t\n  ") is False


def test_detect_rtl_mixed_persian_dominant_is_true():
    """Persian prose with embedded English identifiers: Persian wins."""
    text = "این تابع " + "func" + " را فراخوانی می‌کند و " + "result" + " برمی‌گرداند " * 5
    assert detect_rtl(text) is True


def test_detect_rtl_mixed_english_dominant_is_false():
    """Mostly English text with a few Persian chars: English wins."""
    text = "A" * 200 + " سلام " + "B" * 200
    assert detect_rtl(text) is False


def test_detect_rtl_only_markdown_syntax_is_false():
    """Markdown syntax characters (no letters) → False."""
    assert detect_rtl("## `code` **bold** _italic_ | table |") is False


# ── boundary: rtl count vs ltr count ─────────────────────────────────────────

def test_detect_rtl_equal_rtl_and_ltr_is_false():
    """When RTL count == LTR count, result is False (need rtl > ltr strictly)."""
    # 5 Persian letters, 5 Latin letters
    text = "اbاbاbاbاb"
    rtl_chars = sum(1 for c in text if "؀" <= c <= "ۿ")
    ltr_chars = sum(1 for c in text if c.isascii() and c.isalpha())
    # Confirm our test string is balanced
    assert rtl_chars == ltr_chars

    result = detect_rtl(text)
    # With equal counts, rtl > ltr is False
    assert result is False


def test_detect_rtl_one_more_rtl_than_ltr_is_true():
    """One extra RTL letter over LTR → True."""
    # 6 Persian letters, 5 Latin letters
    text = "اbاbاbاbاbا"
    rtl_chars = sum(1 for c in text if "؀" <= c <= "ۿ")
    ltr_chars = sum(1 for c in text if c.isascii() and c.isalpha())
    assert rtl_chars == ltr_chars + 1

    assert detect_rtl(text) is True


# ── boundary: sampling at 2000 chars ─────────────────────────────────────────

def test_detect_rtl_samples_first_2000_chars():
    """If RTL chars appear only after position 2000, result must be False
    (the function samples only the first 2000 chars per the spec)."""
    # 2000 ASCII chars, then Persian text
    text = "A" * 2000 + "سلام دنیا " * 100
    # The Persian part is outside the sample window — detection uses first 2000
    # chars which are all ASCII letters → result must be False
    assert detect_rtl(text) is False


def test_detect_rtl_rtl_within_first_2000_detected():
    """RTL chars within the first 2000 chars are detected when they dominate.

    The sample is the first 2000 chars.  We need RTL to outnumber Latin inside
    that window, so we place many more Persian letters than ASCII.
    """
    # 200 Persian words (800 chars) then 500 'a' chars → RTL dominates in sample
    text = "سلام " * 200 + "a" * 500
    # Verify our construction: in the first 2000 chars, RTL >> LTR
    sample = text[:2000]
    rtl = sum(1 for c in sample if "؀" <= c <= "ۿ")
    ltr = sum(1 for c in sample if c.isascii() and c.isalpha())
    assert rtl > ltr, f"test construction error: rtl={rtl}, ltr={ltr}"

    assert detect_rtl(text) is True


# ── return type ──────────────────────────────────────────────────────────────

def test_detect_rtl_returns_bool_not_truthy():
    """Return value must be exactly bool True/False, not a truthy/falsy value."""
    assert type(detect_rtl("سلام")) is bool
    assert type(detect_rtl("hello")) is bool
    assert type(detect_rtl("")) is bool
