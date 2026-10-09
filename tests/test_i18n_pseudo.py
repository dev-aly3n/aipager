"""Check 11, the pseudo-locale half: rendered under ``qps``, every string
of a migrated renderer is marked, so English that slipped past the
catalog shows up as an unmarked ASCII word."""

from __future__ import annotations

import re
import subprocess
import sys

from aipager.i18n import core
from tests import i18n_tool

tool = i18n_tool.load()

_MARKED = re.compile(r"⟦.*?⟧", re.S)
_TOKENS = re.compile(r"/[A-Za-z0-9_]+|@\w+|<[^>]+>|&\w+;|\d+")


def unmarked_words(text: str, known=()) -> list[str]:
    """ASCII words in *text* outside the pseudo markers, after removing
    tokens that are never translated (commands, tags, numbers, names)."""
    rest = _MARKED.sub(" ", text)
    for word in sorted(set(known) | set(core.NEVER_TRANSLATE), key=len, reverse=True):
        rest = rest.replace(word, " ")
    rest = _TOKENS.sub(" ", rest)
    return re.findall(r"[A-Za-z]{2,}", rest)


def test_the_sweep_sees_unmarked_english():
    assert unmarked_words(core.pseudo("Hello /new in Telegram")) == []
    assert unmarked_words("Hello " + core.pseudo("world")) == ["Hello"]


def test_the_pseudo_command():
    tool_path = str(tool.ROOT / "tools" / "i18n.py")
    out = subprocess.run([sys.executable, tool_path, "pseudo", "Send /new"],
                         capture_output=True, text=True, check=True, cwd=tool.ROOT)
    assert out.stdout.strip() == core.pseudo("Send /new")
    out = subprocess.run([sys.executable, tool_path, "pseudo", "--rtl", "Hi"],
                         capture_output=True, text=True, check=True, cwd=tool.ROOT)
    assert out.stdout.strip() == "\u200f" + core.pseudo("Hi")
