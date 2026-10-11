"""The display labels of a session's permission mode.

What is SHOWN (buttons, cards, /settings, the Mini App's chat lines) is
the mode's label, ``MODE_ASK`` / ``MODE_AUTO`` / ``MODE_FULL``: its emoji,
its word and the language's word for mode ("🤖 Auto mode"). What people
TYPE after ``/mode`` is the mode's id (``ask``, ``auto``, ``full``) or its
one-word typed word, ``WORD_ASK`` / ``WORD_AUTO`` / ``WORD_FULL`` ("Auto",
"خودکار"), never the label. What is STORED stays the id and never passes
through here: callback data, ``new_session_perm_mode`` values,
``perm_mode`` (aipager/perm_modes.py). No code reads a shown label back.

Sentences around a label stay literals with the label interpolated until
they are migrated themselves.
"""

from __future__ import annotations

import html as _html

from aipager import perm_modes
from aipager.i18n import Msg, Span

MODE_AUTO = Msg("mode.auto", "🤖 Auto mode",
                note="Permission mode's shown label: a safety check runs safe actions and "
                     "stops risky ones for a person to decide. The label names the mode; the "
                     "emoji, the mode.word.auto word, and the word for mode")
MODE_ASK = Msg("mode.ask", "💬 Ask mode",
               note="Permission mode's shown label: Claude asks before each action. The "
                    "emoji, the mode.word.ask word, and the word for mode")
MODE_FULL = Msg("mode.full", "🔓 Full access mode",
                note="Permission mode's shown label: everything runs with no checks (only "
                     "admins may choose it). The emoji, the mode.word.full word, and the "
                     "word for mode")
#: The words typed after /mode: ONE word each, no emoji, the same word the
#: label above uses. Never shown on their own.
WORD_AUTO = Msg("mode.word.auto", "Auto",
                note="The word people type after /mode for Auto mode: ONE word, no emoji, "
                     "the same word as in mode.auto")
WORD_ASK = Msg("mode.word.ask", "Ask",
               note="The word people type after /mode for Ask mode: ONE word, no emoji, "
                    "the same word as in mode.ask")
WORD_FULL = Msg("mode.word.full", "Full",
                note="The word people type after /mode for Full access mode: ONE word, no "
                     "emoji, a word that appears in mode.full")
MODE_ASK_INSTEAD = Msg("mode.ask_instead", "💬 Ask mode instead",
                       note="Button on the /new card: start the session in Ask mode")
MODE_AUTO_INSTEAD = Msg("mode.auto_instead", "🤖 Auto mode instead",
                        note="Button on the /new card: start the session in Auto mode")
MODE_FULL_INSTEAD = Msg("mode.full_instead", "🔓 Full access mode instead",
                        note="Button on the /new card: start the session in Full access mode")
MODE_AUTO_FOR_YOU = Msg("modecard.auto_for_you",
                        "🤖 Auto mode (🔓 Full access mode is for admins)",
                        note="The mode a member who is not an admin gets on /new when the "
                             "chat's default is Full access")
#: Shown in text only (the /mode card, status lines, the Mini App's facts),
#: never on a button: message keys, with a message's length budget.
MODE_ASK_NO_AUTO = Msg("modecard.ask_no_auto", "💬 Ask mode (Auto mode isn't available here)",
                       note="The session's mode, shown in a sentence: Ask mode, because Claude "
                            "Code's Auto mode is not available for its model or plan. Starts "
                            "with the mode.ask label")
MODE_OTHER = Msg("modecard.other_mode", "⚙️ Claude Code's {name} mode",
                 note="A permission mode of Claude Code's that is none of aipager's three "
                      "(set in the terminal); {name} is Claude Code's own name for it, kept "
                      "as it is",
                 eg={"name": "plan"})

#: The id -> its typed word, in the order every surface lists the modes.
_WORDS = ((perm_modes.ASK, WORD_ASK), (perm_modes.AUTO, WORD_AUTO),
          (perm_modes.FULL, WORD_FULL))
_LABELS = {perm_modes.ASK: MODE_ASK, perm_modes.AUTO: MODE_AUTO,
           perm_modes.FULL: MODE_FULL, "ask_no_auto": MODE_ASK_NO_AUTO}


def mode_from_word(word: str) -> str | None:
    """``"ask"``, ``"auto"`` or ``"full"`` for a word typed after ``/mode``:
    the ids themselves (any case), or the mode's typed word in any
    enabled language, compared with its emoji and invisible marks dropped
    (``خودکار``, ``авто``, ``demander``); None for anything else. A
    translated word in a Latin script can also be a session name
    (``demander``): which one a word means is :func:`parse_mode_args`'s
    call, not this one's."""
    if not isinstance(word, str):
        return None
    if word.lower() in perm_modes.MODES:
        return word.lower()
    from aipager.bot import kbwords
    typed = kbwords.mode_word(word)
    if not typed:
        return None
    for mode, msg in _WORDS:
        if any(typed == kbwords.mode_word(label) for label in kbwords._labels(msg)):
            return mode
    return None


def parse_mode_args(words, session_exists) -> tuple[str | None, str | None, bool]:
    """``(mode, label, ok)`` for the words typed after ``/mode``.

    - ``ask`` / ``auto`` / ``full`` (any case) are the mode, exactly as
      always, even when a session has that name;
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
        if mode is None and word.lower() in perm_modes.MODES:
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


def mode_label(mode: str) -> Msg:
    """The shown label of mode id *mode* (``ask``, ``auto``, ``full``, or
    ``ask_no_auto``). A bool is a caller still passing the old
    ``skip_perms``: TypeError, never a guess."""
    if isinstance(mode, bool) or not isinstance(mode, str):
        raise TypeError(mode)
    return _LABELS[mode]


def shown_label(tr, sess, *, html: bool = True) -> str:
    """*sess*'s mode as shown, rendered by *tr* (HTML or plain text): its
    label, Ask with Auto not available, or Claude Code's own mode by its
    name (``sess.shown_mode()``)."""
    mode, raw = sess.shown_mode()
    render = tr.html if html else tr.text
    if mode == "other":
        name = Span(f"<code>{_html.escape(raw)}</code>") if html else Span(raw)
        return render(MODE_OTHER, name=name)
    return render(mode_label(mode))
