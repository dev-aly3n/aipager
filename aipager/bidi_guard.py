"""Show the Unicode characters that reorder text instead of letting them act.

A permission prompt shows the real command or path a person is asked to
approve. Unicode's direction controls (the right-to-left override U+202E,
the isolates U+2066-U+2069, the embeddings, and the direction marks) change
the ORDER in which Telegram, a browser or a phone draws the characters
after them, while the shell still runs them in byte order. A command that
holds one can look like a different command from the one that runs
(Trojan Source, CVE-2021-42574).

So every such character in text a person approves or answers is replaced
by a visible marker, ``⟨U+202E⟩``. Only the controls that reorder are
touched: other invisible characters are normal text (the zero-width
non-joiner inside ordinary Persian words, the zero-width joiner inside
emoji) and stay as they are, so normal input is unchanged byte for byte.
"""

from __future__ import annotations

import re

#: The characters that change the display order of the text around them.
BIDI_CONTROLS: frozenset[str] = frozenset(
    "\u202a\u202b\u202c\u202d\u202e"  # LRE RLE PDF LRO RLO
    "\u2066\u2067\u2068\u2069"        # LRI RLI FSI PDI
    "\u200e\u200f\u061c"              # LRM RLM ALM
)

_TABLE = {ord(c): f"⟨U+{ord(c):04X}⟩" for c in BIDI_CONTROLS}

#: A marker :func:`reveal` wrote, and nothing else.
_MARKER_RE = re.compile(
    "|".join(re.escape(f"⟨U+{ord(c):04X}⟩") for c in sorted(BIDI_CONTROLS)))

#: The line a permission prompt adds when what it shows held one.
WARNING = ("⚠️ This contains hidden direction characters, shown as ⟨U+...⟩. "
           "It may not run what it looks like.")


def reveal(text):
    """*text* with every direction control replaced by its visible marker.
    Anything that is not a string is returned as it is."""
    return text.translate(_TABLE) if isinstance(text, str) else text


def has_controls(text) -> bool:
    """Whether *text* (a string) holds a direction control."""
    return isinstance(text, str) and any(c in BIDI_CONTROLS for c in text)


def revealed(*texts) -> bool:
    """Whether any of *texts* carries a marker :func:`reveal` wrote."""
    return any(isinstance(t, str) and _MARKER_RE.search(t) for t in texts)


def reveal_questions(tool_input):
    """A display copy of an AskUserQuestion input: each question, header
    and option label and description revealed. Answers go by an option's
    position, never its text, so the copy changes nothing but what is
    shown. Anything not shaped like the input is returned as it is."""
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("questions"), list):
        return tool_input
    questions = []
    for q in tool_input["questions"]:
        if not isinstance(q, dict):
            questions.append(q)
            continue
        q = {k: reveal(v) if k in ("question", "header") else v for k, v in q.items()}
        if isinstance(q.get("options"), list):
            q["options"] = [
                {k: reveal(v) if k in ("label", "description") else v for k, v in o.items()}
                if isinstance(o, dict) else o
                for o in q["options"]]
        questions.append(q)
    return {**tool_input, "questions": questions}
