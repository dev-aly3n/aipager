"""The one place a reply-keyboard text becomes an action.

The reply keyboard's text is also its input: tapping "status" sends the
word "status". Four places used to compare that text against English
constants, each with its own rules (the text router, the /new Name card,
the problem report's note capture and the group intake gate). They all
ask this module now, so a translated keyboard needs one change, and an
old keyboard still on screen after a language switch keeps working: every
enabled language's labels are accepted.

The rules each caller had are kept exactly:

- navigation buttons ("Templates", "« Back"...) match the whole text;
- the bare words (status, stop, kill, new) match case-insensitively, and
  in the text router only a text with no space that is not a command;
- template, command and model labels match the whole text, unnormalized
  (they come from keyboard.json and are matched as written);
- a group's marked tap may carry only the labels in :func:`group_labels`
  (status, stop and new as written, no kill, as before).

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

from aipager.i18n import Msg, get
from aipager.i18n.locales import enabled_codes

log = logging.getLogger(__name__)

__all__ = ["bare_word", "classify", "custom_action", "group_labels", "label_problems",
           "nav_action", "norm"]

KB_TEMPLATES = Msg("kb.templates", "Templates", note="Opens the templates keyboard")
KB_COMMANDS = Msg("kb.commands", "Commands", note="Opens the commands keyboard")
KB_MODELS = Msg("kb.models", "Model ›", note="Opens the model list")
KB_BACK = Msg("kb.back", "« Back", note="Back to the previous keyboard")
KB_APP = Msg("kb.app", "📱 App", note="Opens the Mini App")
KB_STATUS = Msg("kb.status", "status", note="A main-keyboard word: shows the sessions")
KB_STOP = Msg("kb.stop", "stop", note="A main-keyboard word: stops the current turn")
KB_NEW = Msg("kb.new", "new", note="A main-keyboard word: starts a new session")
KB_KILL = Msg("kb.kill", "kill", note="Typed word: ends the current session")

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


def _labels(msg: Msg, codes=None) -> list[str]:
    """*msg* in every enabled language (English first)."""
    out = [str(msg)]
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


def custom_action(bot, text: str) -> tuple[str, str] | None:
    """``("tpl"|"cmd"|"model", payload)`` for a template, command or model
    button's label (matched exactly, as keyboard.json wrote it)."""
    for kind, attr in (("tpl", "_template_map"), ("cmd", "_command_map"),
                       ("model", "_model_map")):
        mapping = getattr(bot, attr, None) or {}
        if text in mapping:
            return kind, mapping[text]
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
    for attr in ("_template_map", "_command_map", "_model_map"):
        labels.update(getattr(bot, attr, None) or {})
    try:
        labels.update(bot.registry.live_labels(chat_id))
    except Exception:
        log.debug("group intake: live labels unavailable", exc_info=True)
    return labels


# ---- check 10 -----------------------------------------------------------------

#: Latin labels that are valid session names and not reserved, matched
#: before a session name since long before this check. Reserving them
#: would block those session names, so they are listed here instead.
GRANDFATHERED = frozenset({"Templates", "Commands"})


def label_problems(codes) -> list[str]:
    """Check 10: across *codes*, no two keyboard actions share a label,
    and no Latin label could be a session name unless it is reserved or
    grandfathered."""
    from aipager.dtach.inject import _RESERVED, _VALID_NAME
    problems: list[str] = []
    owner: dict[str, str] = {}
    actions = {**{f"nav:{a}": m for a, m in NAV.items()}, **WORDS}
    for action, msg in actions.items():
        for label in _labels(msg, codes):
            key = norm(label).lower()
            other = owner.setdefault(key, action)
            if other != action:
                problems.append(f"keyboard label {label!r} means both {other} and {action}")
            if (_VALID_NAME.match(label) and label.lower() not in _RESERVED
                    and label not in GRANDFATHERED):
                problems.append(f"keyboard label {label!r} could be a session name")
    return problems
