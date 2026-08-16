"""Telegram Mini App ``initData`` verification.

This is the whole security gate for the Mini App server — a subtle bug
here is a full compromise (see design.md's threat model). There is no
prior HMAC code in this repo to copy from, so this is written directly
against Telegram's documented algorithm
(https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):

1. Parse ``initData`` as a URL query string.
2. Build the data-check-string: every field except ``hash``, sorted by
   key, joined as ``key=value`` pairs with ``\\n``.
3. Compute ``secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)``.
4. Compute ``HMAC_SHA256(key=secret_key, msg=data_check_string)`` (hex)
   and compare it to the ``hash`` field with a constant-time comparison.

Never log ``init_data`` or ``bot_token`` anywhere in this module, at any
level — both are time-limited (or permanent, for the token) credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

# Reject initData whose auth_date is older than this. Bounds a
# captured/replayed initData string to a short-lived credential instead
# of a permanent one (spec "Security requirements" #2).
DEFAULT_MAX_AGE_SECONDS = 300


class InitDataError(Exception):
    """Base class for initData verification failures."""


class InitDataMissingError(InitDataError):
    """``init_data`` is empty, unparsable, or missing a required field."""


class InitDataSignatureError(InitDataError):
    """The computed HMAC does not match the supplied ``hash``."""


class InitDataStaleError(InitDataError):
    """``auth_date`` is older than ``max_age_seconds``."""


def _secret_key(bot_token: str) -> bytes:
    """``HMAC_SHA256(key="WebAppData", msg=bot_token)`` — Telegram's
    documented derivation of the per-bot secret used to sign initData."""
    return hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256,
    ).digest()


def verify_init_data(
    init_data: str, bot_token: str, *, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> dict:
    """Verify a Mini App ``initData`` string; return the parsed ``user``
    object (a dict, at least containing ``id``) on success.

    Raises :class:`InitDataMissingError` for empty/unparsable input or a
    missing required field, :class:`InitDataSignatureError` for a bad
    signature, and :class:`InitDataStaleError` for an ``auth_date``
    older than ``max_age_seconds``. Checked in that order deliberately:
    nothing in ``data`` (including ``auth_date``) is trusted until the
    signature over the whole payload has verified.
    """
    if not init_data or not bot_token:
        raise InitDataMissingError("empty init_data or bot_token")

    # Telegram builds initData with URLSearchParams — a plain query
    # string. Malformed input (no '=' pairs at all) just parses to an
    # empty list here rather than raising, which the empty-pairs check
    # below turns into the same InitDataMissingError.
    pairs = parse_qsl(init_data, keep_blank_values=True)
    if not pairs:
        raise InitDataMissingError("empty or unparsable init_data")

    # Last value wins on a repeated key — initData is never expected to
    # repeat one, but this keeps behavior well-defined either way.
    data: dict[str, str] = dict(pairs)

    received_hash = data.get("hash")
    if not received_hash:
        raise InitDataMissingError("missing hash field")
    if "auth_date" not in data or "user" not in data:
        raise InitDataMissingError("missing auth_date or user field")

    check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(data.items()) if key != "hash"
    )
    computed_hash = hmac.new(
        _secret_key(bot_token), check_string.encode("utf-8"), hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison — a plain `==` would leak timing
    # information about how many leading hex digits matched.
    if not hmac.compare_digest(computed_hash, received_hash):
        raise InitDataSignatureError("signature mismatch")

    try:
        auth_date = int(data["auth_date"])
    except ValueError as e:
        raise InitDataMissingError("malformed auth_date") from e

    if time.time() - auth_date > max_age_seconds:
        raise InitDataStaleError("auth_date stale")

    try:
        user = json.loads(data["user"])
    except (json.JSONDecodeError, TypeError) as e:
        raise InitDataMissingError("malformed user field") from e
    if not isinstance(user, dict) or "id" not in user:
        raise InitDataMissingError("user field missing id")

    return user


__all__ = [
    "DEFAULT_MAX_AGE_SECONDS",
    "InitDataError",
    "InitDataMissingError",
    "InitDataSignatureError",
    "InitDataStaleError",
    "verify_init_data",
]
