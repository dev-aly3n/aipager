"""Black-box gap-fillers for the per-session preference override feature
(design.md batch 4), written independently of and to avoid duplicating
the developer's own tests/test_miniapp_session_preferences_api.py and
tests/test_preferences.py.

Areas covered that the developer's ~91 tests appear to leave open:
  - the boolean-field variant of the "explicit choice == scope default"
    mechanic case (developer only exercised it for a string field)
  - the string "none" override followed by an actual DELETE (developer
    tests PUT "none" and DELETE-of-a-different-value separately, never
    the PUT-"none"-then-DELETE sequence)
  - adversarial values that are legal for a DIFFERENT field, wrong JSON
    top-level shapes, and oversized strings
  - the rate limiter's "without writing" clause taken literally, and
    that DELETE shares (not merely PUT) the same budget
  - concurrent writes: same session/different fields, and same
    session/same field
  - the documented on-disk side effect (aipager-sessions.json) actually
    reflects a PUT, verified via a completely fresh SessionRegistry.load()
  - resolve_preferences(scope, {}) / (scope, None) equality and
    invalid-value-degrades-per-field-only, exercised directly as pure
    functions with a different field combination than the developer used
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager import preferences as prefs_mod
from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
SCOPE_CHAT_ID = -100

ADMIN_ID = 555


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_data(user_id, *, bot_token=BOT_TOKEN, auth_date=None):
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


def _scope(members=None):
    return Scope(
        chat_id=SCOPE_CHAT_ID, kind="group", label="team",
        members=members or (Member(id=ADMIN_ID, label="ada", role="admin"),),
    )


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    bot = mk_bot(registry, scopes=[_scope()])
    bot._app.bot.username = "aipager_test_bot"
    return MiniAppServer(bot, registry, port=8767)


def _mk_session(srv, label, scope_chat_id=SCOPE_CHAT_ID):
    sess = srv.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    return sess


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


# ===== mechanic: the boolean-field "explicit == scope default" case =======

def test_boolean_field_inheriting_vs_explicit_same_value_are_distinguishable(
    server, run_async,
):
    """The orchestrator's headline check, repeated for a BOOLEAN field
    rather than a string one: `simple_formatting=False` is both the
    scope default AND a real, explicit choice. An implementation that
    tests `override_value` for truthiness (instead of `is None` /
    key-presence) would collapse "explicitly False, matching scope" into
    "inheriting" -- exactly the confusion this whole mechanic exists to
    prevent, and boolean False is the value most likely to trip a
    truthiness bug that a string-only test suite would never catch."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "simple_formatting", False)
        client = await _client_for(server)
        try:
            inheriting = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            v_inherit = (await inheriting.json())["values"]["simple_formatting"]
            assert v_inherit == {
                "effective": False, "scope_default": False,
                "override_value": None, "overridden": False,
            }

            put_resp = await client.put(
                "/api/sessions/dev/preferences/simple_formatting",
                headers=_hdr(ADMIN_ID), json={"value": False},
            )
            assert put_resp.status == 200
            assert (await put_resp.json())["changed"] is True

            explicit = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            v_explicit = (await explicit.json())["values"]["simple_formatting"]
            assert v_explicit == {
                "effective": False, "scope_default": False,
                "override_value": False, "overridden": True,
            }
            # The two payloads must NOT be equal -- overridden differs
            # even though effective/scope_default are identical in both.
            assert v_inherit != v_explicit
        finally:
            await client.close()
    run_async(_run())


# ===== None vs "none": the missing PUT-"none"-then-DELETE sequence ========

def test_put_string_none_then_delete_becomes_genuinely_unset(server, run_async):
    """Sets the override to the real, selectable value "none", confirms
    it is `overridden: true` (not conflated with unset), THEN deletes it
    and confirms the field returns to genuinely inheriting -- not stuck
    reporting "none" forever because some code path treated the string
    "none" and Python None as the same sentinel internally."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "language_level", "advanced")
        client = await _client_for(server)
        try:
            put_resp = await client.put(
                "/api/sessions/dev/preferences/language_level",
                headers=_hdr(ADMIN_ID), json={"value": "none"},
            )
            v = (await put_resp.json())["values"]["language_level"]
            assert v["overridden"] is True
            assert v["override_value"] == "none"

            del_resp = await client.delete(
                "/api/sessions/dev/preferences/language_level",
                headers=_hdr(ADMIN_ID),
            )
            assert del_resp.status == 200
            del_body = await del_resp.json()
            assert del_body["changed"] is True
            v2 = del_body["values"]["language_level"]
            assert v2["overridden"] is False
            assert v2["override_value"] is None
            assert v2["effective"] == "advanced"  # back to scope, not "none"
        finally:
            await client.close()
    run_async(_run())


# ===== adversarial values: legal for a DIFFERENT field ======================

def test_value_valid_for_layout_rejected_for_answer_length(server, run_async):
    """"card" is a real, legal `layout` option -- sent to `answer_length`
    it must still be rejected as invalid for THAT field, not accepted
    because it happens to pass validation for some field."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "card"},
            )
            assert resp.status == 400
            check = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            assert (await check.json())["values"]["answer_length"]["overridden"] is False
        finally:
            await client.close()
    run_async(_run())


def test_value_valid_for_answer_length_rejected_for_layout(server, run_async):
    """The reverse pairing: "short" is real for `answer_length`, invalid
    for `layout`."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/layout",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


def test_boolean_true_rejected_for_layout(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/layout",
                headers=_hdr(ADMIN_ID), json={"value": True},
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("bad_value", ["true", "false", "True", "1", "0"])
def test_stringly_typed_booleans_rejected_for_simple_formatting(
    server, run_async, bad_value,
):
    """A classic JS-client bug: sending the STRING "true"/"false" instead
    of a JSON boolean. Must be rejected, not coerced by truthiness."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/simple_formatting",
                headers=_hdr(ADMIN_ID), json={"value": bad_value},
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


def test_very_long_string_value_rejected_cleanly(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "x" * 200_000},
            )
            assert resp.status == 400
            check = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            assert (await check.json())["values"]["answer_length"]["overridden"] is False
        finally:
            await client.close()
    run_async(_run())


def test_array_value_rejected(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": ["short"]},
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("top_level_body", [True, 42, "just-a-string", ["a", "b"], None])
def test_non_object_top_level_json_body_rejected_not_500(
    server, run_async, top_level_body,
):
    """The body is not even a `{"value": ...}` object at all -- a scalar,
    an array, or JSON null at the top level. Must be a clean 400, never
    a 500 from an unguarded `body["value"]` lookup."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json=top_level_body,
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


def test_unknown_field_on_get_all_values_still_only_lists_real_fields(server, run_async):
    """GET never lets a client discover or probe unknown fields -- the
    `values` map is always exactly the four real ones, regardless of
    anything the client might try elsewhere."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            values = (await resp.json())["values"]
            assert set(values) == {
                "layout", "simple_formatting", "answer_length", "language_level",
            }
        finally:
            await client.close()
    run_async(_run())


# ===== rate limiting: "without writing", and DELETE shares the budget ====

def test_rate_limited_request_does_not_write_the_new_value(server, run_async):
    """entrypoints.md, verbatim: 'exceeding it returns 429 without
    writing.' Drive the shared budget to its limit, then confirm the
    LAST accepted value is still what's in effect -- not the value from
    whichever request got 429'd."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            last_good_value = None
            tripped = False
            for i in range(45):
                value = "short" if i % 2 == 0 else "long"
                resp = await client.put(
                    "/api/sessions/dev/preferences/answer_length",
                    headers=_hdr(ADMIN_ID), json={"value": value},
                )
                if resp.status == 429:
                    tripped = True
                    rejected_value = "medium"  # never sent as an accepted value
                    break
                last_good_value = value
            assert tripped, "never observed a 429 within the budget window"
            check = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            effective = (await check.json())["values"]["answer_length"]["effective"]
            assert effective == last_good_value
            assert effective != rejected_value
        finally:
            await client.close()
    run_async(_run())


def test_delete_participates_in_the_same_shared_write_budget(server, run_async):
    """Alternating PUT (to re-arm an override) and DELETE (to clear it)
    must trip the SAME budget PUT-alone does -- proving DELETE is not a
    free, unlimited operation sitting outside the shared counter."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(60):
                if i % 2 == 0:
                    resp = await client.put(
                        "/api/sessions/dev/preferences/answer_length",
                        headers=_hdr(ADMIN_ID), json={"value": "short"},
                    )
                else:
                    resp = await client.delete(
                        "/api/sessions/dev/preferences/answer_length",
                        headers=_hdr(ADMIN_ID),
                    )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429
        finally:
            await client.close()
    run_async(_run())


# ===== concurrency (error-guessing: partial/interleaved writes) ===========

def test_concurrent_puts_to_different_fields_both_apply(server, run_async):
    """Two different fields on the SAME session, written concurrently.
    A read-modify-write implementation that snapshots the whole override
    set before mutating one field could let the slower request clobber
    the faster one's change if the two aren't independently applied."""
    import asyncio

    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            results = await asyncio.gather(
                client.put(
                    "/api/sessions/dev/preferences/layout",
                    headers=_hdr(ADMIN_ID), json={"value": "merged"},
                ),
                client.put(
                    "/api/sessions/dev/preferences/language_level",
                    headers=_hdr(ADMIN_ID), json={"value": "simple"},
                ),
            )
            for r in results:
                assert r.status == 200

            check = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            values = (await check.json())["values"]
            assert values["layout"]["override_value"] == "merged"
            assert values["language_level"]["override_value"] == "simple"
        finally:
            await client.close()
    run_async(_run())


def test_concurrent_puts_to_the_same_field_land_on_one_clean_value(server, run_async):
    """Two different values for the SAME field, concurrently. No crash,
    no partial/corrupted string -- the final state is exactly one of the
    two values sent, never e.g. a mangled concatenation."""
    import asyncio

    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            results = await asyncio.gather(
                client.put(
                    "/api/sessions/dev/preferences/answer_length",
                    headers=_hdr(ADMIN_ID), json={"value": "short"},
                ),
                client.put(
                    "/api/sessions/dev/preferences/answer_length",
                    headers=_hdr(ADMIN_ID), json={"value": "long"},
                ),
            )
            for r in results:
                assert r.status == 200

            check = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            final = (await check.json())["values"]["answer_length"]["override_value"]
            assert final in ("short", "long")
        finally:
            await client.close()
    run_async(_run())


# ===== documented on-disk side effect ======================================

def test_put_persists_to_the_session_state_file_and_survives_a_reload(
    server, run_async, tmp_state_file,
):
    """entrypoints.md's Side effects section: a successful PUT/DELETE
    writes `override_*` into the persisted session-state file
    (aipager-sessions.json), scoped to one TrackedSession entry.

    The write is ASYNCHRONOUS, which an earlier version of this test got
    wrong and reported as "overrides never persist". Route handlers call
    `registry.mark_dirty()`; `SessionMonitor._loop` drains that with
    `save_if_dirty()` on its ~2s tick. That is the established pattern
    for every per-session field — chat's own `skip_perms` writes
    (`callbacks.py:194, 581`) persist exactly the same way and never call
    `save()` directly. No monitor runs in this harness, so the test
    drives the same `save_if_dirty()` the monitor would, then loads a
    BRAND NEW SessionRegistry from the file (no shared in-memory state)
    and confirms the override survived.
    """
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "xshort"},
            )
            assert resp.status == 200
        finally:
            await client.close()

        # Exactly what SessionMonitor._loop does on its next tick.
        server.registry.save_if_dirty()

        assert tmp_state_file.exists(), (
            "no session-state file was written after a successful PUT"
        )
        raw = json.loads(tmp_state_file.read_text())
        entry = raw.get("claude-dev") or raw.get("sessions", {}).get("claude-dev")
        assert entry is not None, (
            f"claude-dev entry not found in persisted state file: {raw!r}"
        )
        assert entry.get("override_answer_length") == "xshort"

        fresh_registry = SessionRegistry()
        fresh_registry.load()
        assert fresh_registry.get("claude-dev").override_answer_length == "xshort"
    run_async(_run())


# ===== pure-function re-verification with a different field combination ===

def test_resolve_preferences_empty_and_none_both_match_get_preferences(server, run_async):
    prefs_mod.set_preference(SCOPE_CHAT_ID, "layout", "merged")
    prefs_mod.set_preference(SCOPE_CHAT_ID, "simple_formatting", True)
    baseline = prefs_mod.get_preferences(SCOPE_CHAT_ID)
    assert prefs_mod.resolve_preferences(SCOPE_CHAT_ID, {}) == baseline
    assert prefs_mod.resolve_preferences(SCOPE_CHAT_ID, None) == baseline


def test_invalid_override_degrades_per_field_only_layout_and_formatting(server, run_async):
    """Same success criterion the developer already pins for
    answer_length/language_level, re-verified independently with the
    OTHER two fields (layout, simple_formatting) -- a validator wired
    per-field incorrectly (e.g. sharing one validator function pointer
    across fields by mistake) would only show up on a combination the
    original test didn't try."""
    prefs_mod.set_preference(SCOPE_CHAT_ID, "layout", "card")
    prefs_mod.set_preference(SCOPE_CHAT_ID, "simple_formatting", True)
    resolved = prefs_mod.resolve_preferences(SCOPE_CHAT_ID, {
        "layout": "diagonal",          # invalid -> degrades to scope
        "simple_formatting": "nope",   # invalid (wrong type) -> degrades
        "answer_length": "short",      # valid -> applies
    })
    assert resolved.layout == "card"
    assert resolved.simple_formatting is True
    assert resolved.answer_length == "short"
