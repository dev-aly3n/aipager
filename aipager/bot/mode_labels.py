"""The display labels of a session's permission mode.

What is SHOWN (buttons, cards, /settings, the Mini App's chat lines) is
the mode's label, ``MODE_AUTO`` / ``MODE_ASK``: its emoji, its word and the
language's word for mode ("🤖 Auto mode"). What people TYPE after
``/mode`` is the mode's id (``auto``, ``ask``) or its one-word typed word,
``WORD_AUTO`` / ``WORD_ASK`` ("Auto", "خودکار"), never the two-word label.
What is STORED stays English and never passes through here: callback
data, ``new_session_mode`` values, ``skip_perms``. No code reads a shown
label back.

Sentences around a label stay literals with the label interpolated until
they are migrated themselves.
"""

from __future__ import annotations

from aipager.i18n import Msg

MODE_AUTO = Msg("mode.auto", "🤖 Auto mode",
                note="Permission mode's shown label: Claude runs tools without asking. The "
                     "emoji, the mode.word.auto word, and the word for mode")
MODE_ASK = Msg("mode.ask", "💬 Ask mode",
               note="Permission mode's shown label: Claude asks before each tool. The "
                    "emoji, the mode.word.ask word, and the word for mode")
#: The words typed after /mode: ONE word each, no emoji, the same word the
#: label above uses. Never shown on their own.
WORD_AUTO = Msg("mode.word.auto", "Auto",
                note="The word people type after /mode for Auto mode: ONE word, no emoji, "
                     "the same word as in mode.auto")
WORD_ASK = Msg("mode.word.ask", "Ask",
               note="The word people type after /mode for Ask mode: ONE word, no emoji, "
                    "the same word as in mode.ask")
MODE_ASK_INSTEAD = Msg("mode.ask_instead", "💬 Ask instead",
                       note="Button on the /new card: start the session in Ask mode")
MODE_AUTO_INSTEAD = Msg("mode.auto_instead", "🤖 Auto instead",
                        note="Button on the /new card: start the session in Auto mode")
MODE_ASK_FOR_YOU = Msg("mode.ask_for_you", "💬 Ask for you (🤖 Auto is for admins)",
                       note="The mode a member who is not an admin gets on /new")


def mode_from_word(word: str) -> str | None:
    """``"ask"`` or ``"auto"`` for a word typed after ``/mode``: the ids
    themselves (any case), or the mode's typed word (``WORD_ASK`` /
    ``WORD_AUTO``) in any enabled language, compared with its emoji and
    invisible marks dropped (``خودکار``, ``авто``, ``demander``); None for
    anything else. A translated word in
    a Latin script can also be a session name (``demander``): which one a
    word means is :func:`parse_mode_args`'s call, not this one's."""
    if not isinstance(word, str):
        return None
    if word.lower() in ("ask", "auto"):
        return word.lower()
    from aipager.bot import kbwords
    typed = kbwords.mode_word(word)
    if not typed:
        return None
    for mode, msg in (("ask", WORD_ASK), ("auto", WORD_AUTO)):
        if any(typed == kbwords.mode_word(label) for label in kbwords._labels(msg)):
            return mode
    return None


def parse_mode_args(words, session_exists) -> tuple[str | None, str | None, bool]:
    """``(mode, label, ok)`` for the words typed after ``/mode``.

    - ``ask`` / ``auto`` (any case) are the mode, exactly as always, even
      when a session has that name;
    - a translated mode word is the mode once a session name was given,
      or when no live session in the chat has exactly that name
      (*session_exists*); with one, it names that session (``/mode
      demander`` with a session ``demander``);
    - anything else is the session name;
    - a word left over (a third, or a second name) is not ok: the caller
      shows the usage.
    """
    mode = label = None
    for word in words:
        if mode is None and word.lower() in ("ask", "auto"):
            mode = word.lower()
            continue
        translated = mode_from_word(word) if mode is None else None
        if translated is not None and (
                label is not None or not session_exists(word.lstrip("/"))):
            mode = translated
        elif label is None:
            label = word.lstrip("/")
        else:
            return mode, label, False
    return mode, label, True


def mode_label(auto: bool) -> Msg:
    """``MODE_AUTO`` or ``MODE_ASK``."""
    return MODE_AUTO if auto else MODE_ASK
