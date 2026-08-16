"""Tests for aipager.miniapp.auth.verify_init_data — the whole security
gate for the Mini App server. See design.md's threat model: a subtle
bug here is a full compromise, so every rejection path gets its own
test, plus the valid case.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from aipager.miniapp.auth import (
    InitDataMissingError,
    InitDataSignatureError,
    InitDataStaleError,
    verify_init_data,
)

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
WRONG_TOKEN = "999999:zzz-topSecretOtherBotToken000000000"


def _sign(fields: dict, bot_token: str) -> str:
    """Compute the correct `hash` for `fields` (excluding any existing
    `hash` key) under `bot_token`, per Telegram's documented algorithm."""
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(fields.items()) if k != "hash"
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()


def _make_init_data(
    *, bot_token: str = BOT_TOKEN, sign_with: str | None = None,
    user: dict | None = None, auth_date: int | None = None,
    extra: dict | None = None, omit_hash: bool = False,
    corrupt_hash: bool = False,
) -> str:
    """Build a realistic initData query string, correctly (or
    deliberately incorrectly) signed."""
    if user is None:
        user = {"id": 555, "first_name": "Ada", "username": "ada"}
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps(user, separators=(",", ":")),
        "query_id": "AAEyy0IvAAAAADLLQi_ABC123",
    }
    if extra:
        fields.update(extra)

    signing_token = sign_with if sign_with is not None else bot_token
    computed_hash = _sign(fields, signing_token)
    if corrupt_hash:
        computed_hash = ("0" if computed_hash[0] != "0" else "1") + computed_hash[1:]

    if not omit_hash:
        fields["hash"] = computed_hash
    return urlencode(fields)


def test_valid_init_data_returns_user():
    init_data = _make_init_data(user={"id": 42, "first_name": "Grace"})
    user = verify_init_data(init_data, BOT_TOKEN)
    assert user["id"] == 42
    assert user["first_name"] == "Grace"


def test_tampered_payload_rejected():
    """A field changed after signing must fail — even though the hash
    field itself is untouched, the recomputed check-string differs."""
    init_data = _make_init_data(user={"id": 42, "first_name": "Grace"})
    tampered = init_data.replace("Grace", "Mallory")
    with pytest.raises(InitDataSignatureError):
        verify_init_data(tampered, BOT_TOKEN)


def test_corrupted_hash_rejected():
    init_data = _make_init_data(corrupt_hash=True)
    with pytest.raises(InitDataSignatureError):
        verify_init_data(init_data, BOT_TOKEN)


def test_wrong_bot_token_signature_rejected():
    """Signed with a different bot's token — must fail when verified
    against the real one, even though the payload itself is untouched."""
    init_data = _make_init_data(sign_with=WRONG_TOKEN)
    with pytest.raises(InitDataSignatureError):
        verify_init_data(init_data, BOT_TOKEN)


def test_stale_auth_date_rejected():
    old = int(time.time()) - 3600  # 1 hour ago, past the 300s default window
    init_data = _make_init_data(auth_date=old)
    with pytest.raises(InitDataStaleError):
        verify_init_data(init_data, BOT_TOKEN)


def test_future_auth_date_rejected():
    """The freshness check must be two-sided: `time.time() - auth_date >
    max_age_seconds` alone only ever catches OLD dates, so a far-future
    auth_date sailed through un-flagged (tester's finding). A forged
    future auth_date requires a correctly-signed initData — i.e. the
    real bot token — but the freshness window's whole purpose is to
    bound a captured/replayed credential to a short lifetime, and a
    one-sided check quietly drops that guarantee on the future side."""
    tomorrow = int(time.time()) + 86400
    init_data = _make_init_data(auth_date=tomorrow)
    with pytest.raises(InitDataStaleError):
        verify_init_data(init_data, BOT_TOKEN)


def test_auth_date_within_clock_skew_tolerance_accepted():
    """A small forward skew (e.g. the host's clock running a few
    seconds behind Telegram's) must not false-positive as 'future and
    therefore rejected' -- see CLOCK_SKEW_TOLERANCE_SECONDS."""
    from aipager.miniapp.auth import CLOCK_SKEW_TOLERANCE_SECONDS

    slightly_ahead = int(time.time()) + (CLOCK_SKEW_TOLERANCE_SECONDS // 2)
    init_data = _make_init_data(auth_date=slightly_ahead)
    user = verify_init_data(init_data, BOT_TOKEN)
    assert user["id"] == 555


def test_fresh_auth_date_within_custom_window_accepted():
    ten_min_ago = int(time.time()) - 600
    init_data = _make_init_data(auth_date=ten_min_ago)
    user = verify_init_data(init_data, BOT_TOKEN, max_age_seconds=900)
    assert user["id"] == 555


def test_missing_hash_rejected():
    init_data = _make_init_data(omit_hash=True)
    with pytest.raises(InitDataMissingError):
        verify_init_data(init_data, BOT_TOKEN)


def test_missing_auth_date_rejected():
    fields = {"user": json.dumps({"id": 1})}
    computed = _sign(fields, BOT_TOKEN)
    fields["hash"] = computed
    with pytest.raises(InitDataMissingError):
        verify_init_data(urlencode(fields), BOT_TOKEN)


def test_missing_user_rejected():
    fields = {"auth_date": str(int(time.time()))}
    computed = _sign(fields, BOT_TOKEN)
    fields["hash"] = computed
    with pytest.raises(InitDataMissingError):
        verify_init_data(urlencode(fields), BOT_TOKEN)


def test_empty_init_data_rejected():
    with pytest.raises(InitDataMissingError):
        verify_init_data("", BOT_TOKEN)


def test_malformed_init_data_rejected():
    with pytest.raises(InitDataMissingError):
        verify_init_data("this is not a query string", BOT_TOKEN)


def test_empty_bot_token_rejected():
    init_data = _make_init_data()
    with pytest.raises(InitDataMissingError):
        verify_init_data(init_data, "")


def test_malformed_user_json_rejected():
    fields = {
        "auth_date": str(int(time.time())),
        "user": "{not-json",
    }
    fields["hash"] = _sign(fields, BOT_TOKEN)
    with pytest.raises(InitDataMissingError):
        verify_init_data(urlencode(fields), BOT_TOKEN)


def test_user_without_id_rejected():
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"first_name": "No ID"}),
    }
    fields["hash"] = _sign(fields, BOT_TOKEN)
    with pytest.raises(InitDataMissingError):
        verify_init_data(urlencode(fields), BOT_TOKEN)


def test_malformed_auth_date_rejected():
    fields = {
        "auth_date": "not-a-number",
        "user": json.dumps({"id": 1}),
    }
    fields["hash"] = _sign(fields, BOT_TOKEN)
    with pytest.raises(InitDataMissingError):
        verify_init_data(urlencode(fields), BOT_TOKEN)


def test_uses_constant_time_comparison(monkeypatch):
    """hmac.compare_digest must be the comparison primitive — never a
    plain `==`, which would leak timing information."""
    import aipager.miniapp.auth as auth_mod

    calls = []
    real_compare = hmac.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(auth_mod.hmac, "compare_digest", _spy)
    verify_init_data(_make_init_data(), BOT_TOKEN)
    assert calls, "verify_init_data must call hmac.compare_digest"
