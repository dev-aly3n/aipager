"""Telegram's failure classes as fixed counter keys (problem reports, 8.112).

A send or edit Telegram refused is counted by its class only: the error
code, and for a 400 the kind of request it was (a message already gone,
markup Telegram could not parse, text too long, anything else). The
description is read to classify it, never kept: what is counted is one of
:data:`aipager.report.schema.COUNTER_KEYS`. Counters count refusals, not
messages: a send refused, retried and refused again counts twice.
"""

from __future__ import annotations

#: 400 descriptions that mean the message is already gone.
_GONE = ("message was deleted", "message to edit not found", "message to delete not found",
         "message_id_invalid", "message to be replied not found", "message to reply not found")
#: 400 descriptions that are no failure: the edit was not needed, or a newer
#: edit of the same message replaced it.
_BENIGN = ("message is not modified", "canceled by new edit message request")
#: 400 descriptions that mean aipager's markup could not be parsed.
_PARSE = ("can't parse entities", "can't find end of the entity", "unsupported start tag")


def tg_400_key(description) -> str:
    """The counter of a 400 (or other 4xx) Telegram refused, by its kind."""
    text = description.lower() if isinstance(description, str) else ""
    if any(marker in text for marker in _GONE):
        return "tg_400_deleted"
    if any(marker in text for marker in _PARSE):
        return "tg_400_parse_entities"
    if "too long" in text:
        return "tg_400_too_long"
    return "tg_400_other"


def tg_failure_key(error_code, description) -> str | None:
    """The counter of a failed Bot API response, or None when there is
    nothing to count: success, a benign "not modified", and a 429 (a small
    one is counted where the limiter takes it, a ban is flood state)."""
    if type(error_code) is not int or error_code in (0, 429):
        return None
    text = description.lower() if isinstance(description, str) else ""
    if error_code == 401:
        return "tg_invalid_token"
    if error_code == 403:
        return "tg_forbidden"
    if error_code == 404 or "method not found" in text:
        return "tg_404_rich"
    if error_code >= 500:
        return "tg_5xx"
    if any(marker in text for marker in _BENIGN):
        return None
    return tg_400_key(text)
