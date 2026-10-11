"""The bot's description in each language (multilingual design 5.8).

Telegram shows a bot's description on its empty chat screen and its short
description on its profile, per ``language_code`` (two letters: ``zh``,
``pt``). At start, in the background and at the lowest priority, aipager
sets them for each language it speaks other than English, but never over
the owner's own text from BotFather:

- the text aipager would set is hashed; when that hash is the one it
  recorded last time for every language of a kind, nothing is asked at all
  for that kind;
- else, before any per-language read, it reads the default (no
  ``language_code``) of that kind, once. Telegram shows a per-language
  text INSTEAD of the default to readers of that language, and BotFather
  writes the default, so a non-empty default is the owner's: then no
  language of that kind is read or set (outcome "owner"), nothing is
  recorded, and the owner's text reaches every reader. Such an install
  costs this one read per kind at every start;
- with no default, it reads what Telegram has for each language whose
  hash differs, and sets its own text only when that is empty or is (by
  hash) what aipager set before; text it does not recognise is the
  owner's and is left alone, with nothing recorded.

The default and ``en`` are never written: English readers see whatever the
owner has. The hashes live in the language state
(``aipager.i18n.persist``). Never blocks start, never raises.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from aipager.i18n import Msg, get, persist
from aipager.i18n.core import has_translation
from aipager.i18n.locales import LOCALES, enabled_codes

log = logging.getLogger(__name__)

__all__ = ["DESCRIPTION", "SHORT", "START_DELAY", "run_at_start", "sync_descriptions"]

DESCRIPTION = Msg(
    "botdesc.description",
    "aipager connects this bot to Claude Code sessions on its owner's computer. From here "
    "you can follow what Claude is doing, answer its questions and permission prompts, and "
    "send it new prompts. Only people the owner has added can use it.",
    note="The bot's description in Telegram (shown on the empty chat screen before the "
         "first message). Plain text, at most 512 characters.")
SHORT = Msg(
    "botdesc.short", "Control Claude Code sessions from Telegram.",
    note="The bot's short description in Telegram (its profile and share card). Plain text, "
         "at most 120 characters.")

#: Seconds after polling starts before the descriptions are looked at.
START_DELAY = 30.0
#: Seam: tests replace THIS, never ``asyncio.sleep`` (CLAUDE.md).
_sleep = asyncio.sleep

# Marks Telegram may add or drop around a right-to-left text: never part of
# what is compared. Written as escapes (no raw direction control in source).
_MARKS = "\u200e\u200f\u061c"
_KINDS = (
    # (kind, key, getter, setter, field)
    ("description", DESCRIPTION, "get_my_description", "set_my_description",
     "description"),
    ("short", SHORT, "get_my_short_description", "set_my_short_description",
     "short_description"),
)


def _norm(text) -> str:
    if not isinstance(text, str):
        return ""
    return text.strip().strip(_MARKS).strip()


def _digest(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:16]


def _ornament() -> dict | None:
    from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
    return rate_limit_args(priority=PRIORITY_ORNAMENT)


async def sync_descriptions(tg_bot) -> dict[str, dict[str, str]]:
    """Set each language's (short) description where it fits. Returns, per
    Telegram code and kind: "set", "unchanged", "owner" (left alone),
    "untranslated" or "error"."""
    languages = []
    for code in enabled_codes():
        loc = LOCALES.get(code)
        if code == "en" or loc is None or not loc.telegram_code or loc.telegram_code == "en":
            continue
        languages.append((code, loc.telegram_code))
    outcomes: dict[str, dict[str, str]] = {tg: {} for _, tg in languages}
    for kind, msg, getter, setter, field in _KINDS:
        pending = []
        for code, tg in languages:
            if not has_translation(code, msg.key):
                outcomes[tg][kind] = "untranslated"
                continue
            want = get(code).text(msg)
            if persist.description_hash(tg, kind) == _digest(want):
                outcomes[tg][kind] = "unchanged"
                continue
            pending.append((code, tg, want))
        if not pending:
            continue                        # the steady state: no call at all
        # The default first: when it is the owner's, the kind is left alone.
        try:
            default = await getattr(tg_bot, getter)(rate_limit_args=_ornament())
            owner_default = _norm(getattr(default, field, "") or "") != ""
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the next start asks again
            log.info("bot %s default not read (%s)", kind, type(e).__name__)
            for _code, tg, _want in pending:
                outcomes[tg][kind] = "error"
            continue
        if owner_default:
            for _code, tg in languages:
                outcomes[tg][kind] = "owner"
            continue
        for _code, tg, want in pending:
            outcomes[tg][kind] = await _sync_one(tg_bot, tg, kind, want, getter, setter,
                                                 field)
    return outcomes


async def _sync_one(tg_bot, tg, kind, want, getter, setter, field) -> str:
    digest = _digest(want)
    ours = persist.description_hash(tg, kind)
    try:
        current = await getattr(tg_bot, getter)(language_code=tg,
                                                 rate_limit_args=_ornament())
        now = _norm(getattr(current, field, "") or "")
        if now == "" or (ours is not None and _digest(now) == ours):
            await getattr(tg_bot, setter)(**{field: want}, language_code=tg,
                                          rate_limit_args=_ornament())
            persist.record_description(tg, kind, digest)
            return "set"
        if now == _norm(want):
            persist.record_description(tg, kind, digest)
            return "unchanged"
        return "owner"
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - one language never stops the rest
        log.info("bot %s for %s not synced (%s)", kind, tg, type(e).__name__)
        return "error"


async def run_at_start(tg_bot) -> None:
    """The start-time task: wait, then sync. Never raises."""
    try:
        await _sleep(START_DELAY)
        outcomes = await sync_descriptions(tg_bot)
        changed = sorted(tg for tg, kinds in outcomes.items() if "set" in kinds.values())
        if changed:
            log.info("bot descriptions set for %s", ", ".join(changed))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - background: log and carry on
        log.info("bot descriptions not synced", exc_info=True)
