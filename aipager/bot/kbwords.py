"""The one place a reply-keyboard text becomes an action.

The reply keyboard's text is also its input: tapping "status" sends the
word "status". Four places used to compare that text against English
constants, each with its own rules (the text router, the /new Name card,
the problem report's note capture and the group intake gate). They all
ask this module now, so a translated keyboard needs one change, and an
old keyboard still on screen after a language switch keeps working: every
enabled language's navigation buttons and keyboard words are accepted.
A default template or command label, which can run a command such as
/clear, is read by the text router in English and the chat's own
language only (a switch sends the new keyboard at once).

The rules each caller had are kept exactly:

- navigation buttons ("Templates", "« Back"...) match the whole text;
- the bare words (status, stop, kill, new) match case-insensitively, and
  in the text router only a text with no space that is not a command;
- template, command and model labels match the whole text, as written
  first and then normalized (keyboard.json's own spelling wins); a DEFAULT
  template or command label (one keyboard.json did not replace) is also
  matched in English and the chat's own language (every enabled language
  where no chat is given), normalized, and always sends its English
  payload: what Claude receives never depends on the chat's language;
- a group's marked tap may carry only the labels in :func:`group_labels`
  (status, stop and new, no kill, as before), compared normalized
  (:func:`is_group_label`).

Text is normalized on both sides before comparing (:func:`norm`): Unicode
NFKC, without emoji variation selectors and the invisible marks aipager
itself adds around text, with the Arabic forms of ye and kaf folded to
the Persian ones. Case and spaces are left alone.

The English of each label equals the constant in ``aipager.config``
(tested): config.py stays plain strings because the hook process imports
it, and that process never imports ``aipager.i18n``.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from aipager.config import APP_BUTTON
from aipager.i18n import Msg, get
from aipager.i18n.locales import enabled_codes

log = logging.getLogger(__name__)

__all__ = ["DEFAULT_LABELS", "bare_word", "classify", "custom_action", "group_labels",
           "is_group_label", "label_problems", "nav_action", "norm", "shown_label"]

KB_TEMPLATES = Msg("kb.templates", "Templates", note="Opens the templates keyboard")
KB_COMMANDS = Msg("kb.commands", "Commands", note="Opens the commands keyboard")
KB_MODELS = Msg("kb.models", "Model ›", note="Opens the model list")
KB_BACK = Msg("kb.back", "« Back", note="Back to the previous keyboard")
#: The Mini App button reads "📱 App" in every language (operator,
#: 2026-10-10: "don't translate the app"): a fixed label like a name, not a key.
KB_APP = APP_BUTTON
KB_STATUS = Msg("kb.status", "status", note="A main-keyboard word: shows the sessions")
KB_STOP = Msg("kb.stop", "stop", note="A main-keyboard word: stops the current turn")
KB_NEW = Msg("kb.new", "new", note="A main-keyboard word: starts a new session")
KB_KILL = Msg("kb.kill", "kill", note="Typed word: ends the current session")

#: The text of the message each sub-keyboard is sent with.
KBMSG_TEMPLATES = Msg("kbmsg.templates", "📋 Templates",
                      note="Message that carries the templates keyboard")
KBMSG_COMMANDS = Msg("kbmsg.commands", "🎛 Commands",
                     note="Message that carries the Claude Code commands keyboard")
KBMSG_MODELS = Msg("kbmsg.models", "🤖 Model",
                   note="Message that carries the model list keyboard")

#: The default template and command buttons, in ``config``'s order (a
#: tap sends the English payload; only the button is translated).
_DEFAULT_MSGS = (
    Msg("kbl.tpl.continue", "Continue",
        note="Template button: a tap sends Claude the English prompt 'Continue'. "
             "Translate the button only; one short word or phrase."),
    Msg("kbl.tpl.run_tests", "Run tests",
        note="Template button: a tap asks Claude to run the tests. Translate the button only."),
    Msg("kbl.tpl.write_tests", "Write tests",
        note="Template button: a tap asks Claude to write tests for its changes. 'Write' "
             "is the ordinary verb here, not the tool. Translate the button only."),
    Msg("kbl.tpl.commit", "Commit",
        note="Template button: a tap asks Claude to make a git commit. Translate the "
             "button only."),
    Msg("kbl.tpl.lgtm", "LGTM ship it",
        note="Template button: a tap tells Claude the work looks good and to release it. "
             "Translate the button only; keep it short."),
    Msg("kbl.tpl.show_diff", "Show diff",
        note="Template button: a tap asks Claude to show the git diff. Translate the "
             "button only."),
    Msg("kbl.tpl.explain_plan", "Explain plan",
        note="Template button: a tap asks Claude to explain its plan before changing "
             "anything. Translate the button only."),
    Msg("kbl.tpl.update_memory", "Update memory",
        note="Template button: a tap asks Claude to update its notes file (CLAUDE.md). "
             "Translate the button only."),
    Msg("kbl.cmd.compact", "Compact",
        note="Command button: a tap sends Claude Code's /compact (shrink the "
             "conversation). Translate the button only."),
    Msg("kbl.cmd.clear", "Clear",
        note="Command button: a tap sends Claude Code's /clear (start the conversation "
             "afresh). Translate the button only."),
    Msg("kbl.cmd.plan", "Plan mode",
        note="Command button: a tap sends Claude Code's /plan (plan before acting). "
             "Translate the button only."),
    Msg("kbl.cmd.init", "Init",
        note="Command button: a tap sends Claude Code's /init (set up CLAUDE.md for the "
             "project). Translate the button only."),
    Msg("kbl.cmd.security_review", "Security review",
        note="Command button: a tap sends Claude Code's /security-review. Translate the "
             "button only."),
)


def _default_labels() -> dict[tuple[str, str], Msg]:
    from aipager import config
    pairs = list(config._DEFAULT_TEMPLATES) + list(config._DEFAULT_COMMANDS)
    # A pair whose label no longer equals its key's English drops out
    # (shown as written); the pinning test fails first.
    return {(label, payload): msg for (label, payload), msg in zip(pairs, _DEFAULT_MSGS)
            if label == msg}


#: (English label, payload) -> its key, for the default template and
#: command buttons (``config._DEFAULT_TEMPLATES`` / ``_DEFAULT_COMMANDS``,
#: pinned by a test). Only a pair keyboard.json left exactly as shipped is
#: shown translated; the payload Claude receives is always the English.
DEFAULT_LABELS: dict[tuple[str, str], Msg] = _default_labels()

_MAPS = (("tpl", "_template_map"), ("cmd", "_command_map"), ("model", "_model_map"))

#: action -> label, for the navigation buttons.
NAV = {"templates": KB_TEMPLATES, "commands": KB_COMMANDS, "models": KB_MODELS,
       "back": KB_BACK, "app": KB_APP}
#: action -> label, for the bare words.
WORDS = {"status": KB_STATUS, "stop": KB_STOP, "kill": KB_KILL, "new": KB_NEW}
_LOOKING = ("new", "status")
_ACTING = ("stop", "kill")
#: The main keyboard's words a marked group tap may carry (no "kill":
#: the group keyboard never shows it).
_GROUP_WORDS = ("status", "stop", "new")

_INVISIBLE = re.compile("[\ufe0f\u200c-\u200f\u2066-\u2069]")
_FOLD = str.maketrans({"ي": "ی", "ك": "ک"})


def norm(text: str) -> str:
    """*text* as compared: NFKC, no variation selector or invisible mark,
    Arabic ye and kaf as Persian. Case and whitespace unchanged."""
    if not isinstance(text, str):
        return ""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text)).translate(_FOLD)


def _labels(msg: Msg | str, codes=None) -> list[str]:
    """*msg* in every enabled language (English first). A plain string (the
    App button) is the same in every language."""
    out = [str(msg)]
    if not isinstance(msg, Msg):
        return out
    for code in codes if codes is not None else enabled_codes():
        label = get(code).text(msg)
        if label not in out:
            out.append(label)
    return out


def nav_action(text: str) -> str | None:
    """``"nav:templates"``, ``"nav:commands"``, ``"nav:models"``,
    ``"nav:back"`` or ``"nav:app"`` for a navigation button's text."""
    t = norm(text)
    for action, msg in NAV.items():
        if any(t == norm(label) for label in _labels(msg)):
            return f"nav:{action}"
    return None


def bare_word(text: str) -> str | None:
    """``"status"``, ``"stop"``, ``"kill"`` or ``"new"`` for a bare
    keyboard word: no space, not a command, any case."""
    if not isinstance(text, str) or " " in text or text.startswith("/"):
        return None
    t = norm(text).lower()
    for action, msg in WORDS.items():
        if any(t == norm(label).lower() for label in _labels(msg)):
            return action
    return None


def shown_label(tr, label: str, payload: str) -> str:
    """How a template or command button reads in *tr*'s language: the
    translation of a default (label, payload) pair, else *label* as
    keyboard.json wrote it (every model label too)."""
    msg = DEFAULT_LABELS.get((label, payload))
    return tr.text(msg) if msg is not None else label


def _defaults_in_use(bot):
    """``(kind, English label, payload, Msg)`` for each default template
    and command pair the bot's keyboard still carries as shipped."""
    for kind, attr in _MAPS[:2]:
        mapping = getattr(bot, attr, None) or {}
        for (label, payload), msg in DEFAULT_LABELS.items():
            if mapping.get(label) == payload:
                yield kind, label, payload, msg


def custom_action(bot, text: str, chat_code: str | None = None) -> tuple[str, str] | None:
    """``("tpl"|"cmd"|"model", payload)`` for a template, command or model
    button's label: matched exactly, as keyboard.json wrote it, then
    normalized (:func:`norm`: a label with an invisible mark at its ends
    still matches), then a default template or command label (with the
    English payload).

    With *chat_code* (the text router: the chat's language, in which its
    keyboard is drawn), a default label matches only in English and that
    language: a translated label runs a command such as /clear, so a
    Russian label typed into a Persian or English chat stays a message
    for Claude. Without it (the capture steps, which only ask whether a
    text looks like a keyboard tap), every enabled language matches."""
    for kind, attr in _MAPS:
        mapping = getattr(bot, attr, None) or {}
        if text in mapping:
            return kind, mapping[text]
    t = norm(text)
    if not t:
        return None
    # The same label with an invisible mark at its ends (a right-to-left
    # keyboard row) or in another normal form still matches.
    for kind, attr in _MAPS:
        mapping = getattr(bot, attr, None) or {}
        for label, payload in mapping.items():
            if norm(label) == t:
                return kind, payload
    codes = None if chat_code is None else [chat_code]
    for kind, _label, payload, msg in _defaults_in_use(bot):
        if any(t == norm(shown) for shown in _labels(msg, codes)):
            return kind, payload
    return None


def _word(text: str) -> str | None:
    t = norm(text).strip().lower()
    for action, msg in WORDS.items():
        if any(t == norm(label).lower() for label in _labels(msg)):
            return action
    return None


def classify(bot, text: str) -> str | None:
    """How a capture step (the /new Name card, a problem report's note)
    reads a keyboard text: ``"looking"`` (new, status or a navigation
    button: only looking around), ``"acting"`` (stop, kill, or a
    template, command or model button: acts on a session), or None (not
    a keyboard text)."""
    word = _word(text)
    if word in _LOOKING or nav_action(text):
        return "looking"
    if word in _ACTING or custom_action(bot, text):
        return "acting"
    return None


def group_labels(bot, chat_id) -> set[str]:
    """Every exact label a marked keyboard tap in group *chat_id* may
    carry: status, stop and new, the navigation buttons, the template,
    command and model labels, and the chat's live session labels."""
    labels: set[str] = set()
    for action in _GROUP_WORDS:
        labels.update(_labels(WORDS[action]))
    for msg in NAV.values():
        labels.update(_labels(msg))
    for _kind, attr in _MAPS:
        labels.update(getattr(bot, attr, None) or {})
    for _kind, _label, _payload, msg in _defaults_in_use(bot):
        labels.update(_labels(msg))
    try:
        labels.update(bot.registry.live_labels(chat_id))
    except Exception:
        log.debug("group intake: live labels unavailable", exc_info=True)
    return labels


def is_group_label(bot, chat_id, label: str) -> bool:
    """Whether a marked tap's *label* is one of :func:`group_labels`,
    compared normalized: a client that drops the invisible mark a
    right-to-left label carries still matches."""
    t = norm(label)
    return bool(t) and any(t == norm(x) for x in group_labels(bot, chat_id))


# ---- check 10 -----------------------------------------------------------------

#: Latin labels that are valid session names and not reserved, matched
#: before a session name since long before this check. Reserving them
#: would block those session names, so they are listed here instead.
GRANDFATHERED = frozenset({"Templates", "Commands"})


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\ufe0f\u200d]")


def mode_word(label: str) -> str:
    """A mode word as typed: emoji and variation selectors dropped,
    normalized, stripped, lower case (``🤖 Auto`` -> ``auto``)."""
    return norm(_EMOJI.sub("", label)).strip().lower()


def label_problems(codes) -> list[str]:
    """Check 10: across *codes*, no two keyboard actions, default template
    or command labels, or mode words share a label; a translated bare
    word or mode word is one token (the text router reads only a text
    with no space); each mode label shows its typed mode word; and no
    Latin keyboard label could be a session name unless it is reserved or
    grandfathered."""
    from aipager.bot.mode_labels import (
        MODE_ASK, MODE_AUTO, MODE_FULL, WORD_ASK, WORD_AUTO, WORD_FULL)
    from aipager.dtach.inject import _RESERVED, _VALID_NAME
    problems: list[str] = []
    owner: dict[str, str] = {}

    what = "keyboard label"

    def claim(key: str, label: str, action: str) -> None:
        other = owner.setdefault(key, action)
        if other != action:
            problems.append(f"{what} {label!r} means both {other} and {action}")

    def one_word(word: str, label: str, action: str) -> None:
        if " " in word:
            problems.append(f"{label!r} ({action}) must be one word")

    from aipager.i18n.locales import PSEUDO
    real = [c for c in codes if c not in PSEUDO]
    actions = {**{f"nav:{a}": m for a, m in NAV.items()}, **WORDS}
    for action, msg in actions.items():
        for label in _labels(msg, codes):
            claim(norm(label).lower(), label, action)
        # One word in every real language (the pseudo locale's brackets
        # and padding are not words).
        for label in _labels(msg, real) if action in WORDS else ():
            one_word(norm(label).strip(), label, action)
        for label in _labels(msg, codes):
            if (_VALID_NAME.match(label) and label.lower() not in _RESERVED
                    and label not in GRANDFATHERED):
                problems.append(f"{what} {label!r} could be a session name")
    for (english, _payload), msg in DEFAULT_LABELS.items():
        kind = "cmd" if msg.key.startswith("kbl.cmd.") else "tpl"
        for label in _labels(msg, codes):
            claim(norm(label).lower(), label, f"{kind}:{english}")
    for action, msg in (("mode:ask", WORD_ASK), ("mode:auto", WORD_AUTO),
                        ("mode:full", WORD_FULL)):
        for label in _labels(msg, codes):
            claim(mode_word(label), label, action)
        for label in _labels(msg, real):
            one_word(mode_word(label), label, action)
    # The word people type is the one the label shows them ("💬 مود پرسش"
    # shows پرسش), so what they read after /mode is what /mode accepts.
    for word_msg, shown_msg in ((WORD_ASK, MODE_ASK), (WORD_AUTO, MODE_AUTO),
                                (WORD_FULL, MODE_FULL)):
        for code in dict.fromkeys(["en", *real]):
            tr = get(code)
            word, shown = tr.text(word_msg), tr.text(shown_msg)
            if mode_word(word) not in mode_word(shown):
                # "∌": the label does not contain the word (no new English
                # literal for check 11's ratchet).
                problems.append(f"{code} {shown!r} ∌ {word!r}")
    return problems
