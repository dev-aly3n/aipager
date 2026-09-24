"""Every emoji aipager reacts with is one Telegram lets a bot set.

``setMessageReaction`` with an emoji outside ``ReactionTypeEmoji``'s list
raises, and every call site swallows the error — the reaction silently
never appears (roadmap 8.33: ✅ on commands and /stop, 🎙️ on voice).

The allowed list below is a constant copy of the Bot API's, taken from
https://core.telegram.org/bots/api#reactiontypeemoji on 2026-09-24, kept
here on purpose rather than imported: the module's own copy is pinned
against it, and the source sweep checks every literal passed to a
reaction call — including one a future call site adds without going
through ``aipager.bot.reactions``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import aipager

TELEGRAM_ALLOWED = frozenset((
    "\u2764", "\U0001f44d", "\U0001f44e", "\U0001f525", "\U0001f970",
    "\U0001f44f", "\U0001f601", "\U0001f914", "\U0001f92f", "\U0001f631",
    "\U0001f92c", "\U0001f622", "\U0001f389", "\U0001f929", "\U0001f92e",
    "\U0001f4a9", "\U0001f64f", "\U0001f44c", "\U0001f54a", "\U0001f921",
    "\U0001f971", "\U0001f974", "\U0001f60d", "\U0001f433",
    "\u2764\u200d\U0001f525", "\U0001f31a", "\U0001f32d", "\U0001f4af",
    "\U0001f923", "\u26a1", "\U0001f34c", "\U0001f3c6", "\U0001f494",
    "\U0001f928", "\U0001f610", "\U0001f353", "\U0001f37e", "\U0001f48b",
    "\U0001f595", "\U0001f608", "\U0001f634", "\U0001f62d", "\U0001f913",
    "\U0001f47b", "\U0001f468\u200d\U0001f4bb", "\U0001f440", "\U0001f383",
    "\U0001f648", "\U0001f607", "\U0001f628", "\U0001f91d", "\u270d",
    "\U0001f917", "\U0001fae1", "\U0001f385", "\U0001f384", "\u2603",
    "\U0001f485", "\U0001f92a", "\U0001f5ff", "\U0001f192", "\U0001f498",
    "\U0001f649", "\U0001f984", "\U0001f618", "\U0001f48a", "\U0001f64a",
    "\U0001f60e", "\U0001f47e", "\U0001f937\u200d\u2642", "\U0001f937",
    "\U0001f937\u200d\u2640", "\U0001f621",
))

#: Callables whose arguments carry a reaction emoji.
_REACTION_CALLS = {"set_message_reaction", "_react", "set_reaction"}


def _reaction_literals() -> list[tuple[str, int, str]]:
    """``(file, line, literal)`` for every non-ASCII string literal passed
    to a reaction call anywhere in the package."""
    root = Path(aipager.__file__).parent
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else "")
            if name not in _REACTION_CALLS:
                continue
            args = list(node.args) + [k.value for k in node.keywords]
            for arg in args:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and not arg.value.isascii()):
                    found.append((str(path.relative_to(root)), node.lineno,
                                  arg.value))
    return found


def test_the_allowed_copy_has_the_bot_api_size():
    assert len(TELEGRAM_ALLOWED) == 73


def test_module_allowed_set_matches_the_bot_api_copy():
    from aipager.bot import reactions
    assert reactions.TELEGRAM_ALLOWED_REACTIONS == TELEGRAM_ALLOWED


def test_every_lifecycle_emoji_is_allowed():
    from aipager.bot import reactions
    used = reactions.AIPAGER_REACTIONS
    assert {reactions.HANDED_OFF, reactions.TAKEN, reactions.NOT_DELIVERED,
            reactions.ACK} <= used
    assert used - TELEGRAM_ALLOWED == set()


def test_every_literal_passed_to_a_reaction_call_is_allowed():
    literals = _reaction_literals()
    bad = [(f, line, lit) for f, line, lit in literals
           if lit not in TELEGRAM_ALLOWED]
    assert bad == [], f"emoji Telegram refuses as a bot reaction: {bad}"
