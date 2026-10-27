"""Invisible characters in a typed prompt (roadmap 8.124, aipager/invisible.py).

Claude Code's cleaner removes them when Enter is pressed, and an Enter that
removed one sends nothing. aipager removes what Claude Code always removes
and keeps the context-dependent ones only where Claude Code may keep them.
"""

from __future__ import annotations

import pytest

from aipager import invisible

ALWAYS = {
    "nul": "\x00", "bell": "\x07", "esc": "\x1b", "us": "\x1f", "del": "\x7f",
    "c1": "\x9b", "shy": "\u00ad", "hangul-filler": "\u115f", "lre": "\u202a",
    "rlo": "\u202e", "word-joiner": "\u2060", "lri": "\u2066", "rli": "\u2067",
    "pdi": "\u2069", "invisible-times": "\u2062", "hangul-3164": "\u3164",
    "vs4": "\ufe03", "vs14": "\ufe0d", "bom": "\ufeff", "ffa0": "\uffa0",
    "fff9": "\ufff9", "16fe4": "\U00016fe4", "1d173": "\U0001d173",
    "stray-tag": "\U000e0041", "vs17": "\U000e0100",
}


@pytest.mark.parametrize("name", list(ALWAYS))
def test_an_always_removed_character_is_removed(name):
    assert invisible.clean(f"reply{ALWAYS[name]} with OK") == "reply with OK"


@pytest.mark.parametrize("brk", ["\r", "\x0b", "\x0c", "\x85", "\u2028", "\u2029"],
                         ids=["cr", "vt", "ff", "nel", "ls", "ps"])
def test_a_line_break_variant_becomes_a_newline(brk):
    assert invisible.clean(f"one{brk}two") == "one\ntwo"


def test_crlf_is_one_newline():
    assert invisible.clean("one\r\ntwo\r\n") == "one\ntwo\n"


def test_tab_and_newline_stay():
    assert invisible.clean("a\tb\nc") == "a\tb\nc"


def test_plain_and_ordinary_unicode_text_is_unchanged():
    for text in ("reply with exactly OK", "café naïve — “quotes”", "سلام دنیا", "日本語",
                 "\U0001f600 ok", ""):
        assert invisible.clean(text) == text


ZWNJ, ZWJ, ZWSP, LRM, RLM, ALM = "\u200c", "\u200d", "\u200b", "\u200e", "\u200f", "\u061c"


@pytest.mark.parametrize("text", [
    f"می{ZWNJ}خواهم",                          # Persian half-space inside a word
    f"सत्{ZWNJ}य",                               # Devanagari
    f"\U0001f468{ZWJ}\U0001f469{ZWJ}\U0001f467",  # family emoji
    f"ক্{ZWJ}ষ",                                 # Bengali conjunct
    f"สวัสดี{ZWSP}ครับ",                         # Thai word break
    f"سلام{RLM} hello",                          # RLM in a line holding RTL letters
    f"hello {LRM}שלום",                          # LRM in a line holding Hebrew
    f"عدد{ALM} 12",                              # ALM in an Arabic line
    "❤\ufe0f",                              # VS16 after an emoji
    "❤\ufe0e",                              # VS15 after a symbol
    "1\ufe0f⃣",                             # keycap
    "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",  # England
    "\u07ca\u20f0\u200c ok",                     # NKo letter, a mark, ZWNJ, Latin
], ids=["fa-zwnj", "devanagari-zwnj", "family-zwj", "bengali-zwj", "thai-zwsp", "rlm-rtl",
        "lrm-hebrew", "alm-arabic", "vs16", "vs15", "keycap", "flag", "nko-mark-zwnj"])
def test_a_context_dependent_character_is_kept_in_its_context(text):
    assert invisible.clean(text) == text


@pytest.mark.parametrize("text, cleaned", [
    (f"reply{ZWNJ} with", "reply with"),
    (f"re{ZWNJ}ply", "reply"),
    (f"reply{ZWJ} with", "reply with"),
    (f"reply{ZWSP} with", "reply with"),
    (f"reply{RLM} with", "reply with"),
    (f"reply{LRM} with", "reply with"),
    (f"reply{ALM} with", "reply with"),
    ("a\ufe0fb", "ab"),
    ("\U0001f3f4\U000e0078\U000e0079\U000e007f", "\U0001f3f4"),     # not a kept flag
    (f"سلام\nreply{RLM} with", "سلام\nreply with"),                  # RTL only on another line
], ids=["zwnj-latin", "zwnj-in-word", "zwj-latin", "zwsp-latin", "rlm-latin", "lrm-latin",
        "alm-latin", "vs16-ascii", "bad-flag", "rlm-other-line"])
def test_a_context_dependent_character_is_removed_where_it_does_nothing(text, cleaned):
    assert invisible.clean(text) == cleaned


def test_may_hold_back_only_with_a_context_dependent_character():
    assert invisible.may_hold_back(invisible.clean(f"می{ZWNJ}خواهم"))
    assert invisible.may_hold_back("❤\ufe0f")
    assert not invisible.may_hold_back(invisible.clean(f"reply{ZWSP}\ufeff with"))
    assert not invisible.may_hold_back("plain text")
    # A kept flag's tags are not held back.
    assert not invisible.may_hold_back(
        "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f")


def test_a_long_persian_message_is_cleaned_quickly():
    import time
    text = (f"این یک پیام{ZWNJ}ها است{RLM} " * 400)
    t0 = time.monotonic()
    assert invisible.clean(text) == text
    assert time.monotonic() - t0 < 1.0


@pytest.mark.parametrize("text", [
    ("a" + RLM) * 2048,                    # no RTL letter at all
    ("a" + ALM) * 2048,
    ("a" + RLM) * 2047 + "س",              # the only RTL letter at the very end
    ("\u0301" + "\u034f") * 2048,          # marks and CGJs, alternating
    ("\u0301" + ZWNJ) * 2048,
], ids=["rlm-latin", "alm-latin", "rtl-at-the-end", "marks-cgj", "marks-zwnj"])
def test_a_full_message_of_direction_marks_is_cleaned_quickly(text):
    """One pass per line, not one per mark (it runs on the daemon's loop:
    a 4,096-character message once took 7 s)."""
    import time
    t0 = time.monotonic()
    invisible.clean(text)
    assert time.monotonic() - t0 < 0.5
