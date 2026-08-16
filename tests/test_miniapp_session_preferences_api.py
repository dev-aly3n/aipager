"""Per-session preference overrides — GET/PUT/DELETE on
``/api/sessions/{label}/preferences[/{field}]`` (design.md batch 4).

Mirrors ``test_miniapp_preferences_api.py``'s adversarial style, extended
for what is unique to this route: the tri-state default-vs-override
mechanic (design §4), the `_can_prompt_user` gate instead of
`_is_admin_user`, and cross-scope label isolation on a session-scoped
route.

Test-double note (planner's own warning, design.md Risks): the existing
`_Policy`/`_Role` stand-ins in `test_miniapp_preferences_api.py` implement
only `bypass_safety`. `_can_prompt_user` also reads `Role.can_prompt`
(via `_role_can_prompt`), so the stand-ins here implement both — an
AttributeError from a half-built double would fail LOUD, not quietly
pass for the wrong reason, but building it right the first time avoids
that entirely.
"""

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
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

SCOPE_CHAT_ID = -100
FOREIGN_SCOPE_CHAT_ID = -200

ADMIN_ID = 555       # bypass_safety AND can_prompt
DEVELOPER_ID = 777    # can_prompt, NOT bypass_safety — the case this
                       # route's whole design decision exists to allow
READONLY_ID = 888     # neither bypass_safety NOR can_prompt
OUTSIDER_ID = 999     # member of no scope at all
FOREIGN_MEMBER_ID = 321  # a real member, but of the OTHER scope


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


class _Role:
    def __init__(self, *, bypass_safety=False, can_prompt=True):
        self.bypass_safety = bypass_safety
        self.can_prompt = can_prompt


class _Policy:
    """Minimal stand-in for the real policy, extended (per design.md's
    own risk note) to implement `can_prompt` alongside `bypass_safety`:

    - "admin"      -> bypass_safety=True,  can_prompt=True
    - "developer"  -> bypass_safety=False, can_prompt=True
    - "read_only"  -> bypass_safety=False, can_prompt=False
    """

    _ROLES = {
        "admin": _Role(bypass_safety=True, can_prompt=True),
        "developer": _Role(bypass_safety=False, can_prompt=True),
        "read_only": _Role(bypass_safety=False, can_prompt=False),
    }

    def get_role(self, name):
        return self._ROLES.get(name)


@pytest.fixture
def server(mk_bot):
    registry = SessionRegistry()
    scope = Scope(
        chat_id=SCOPE_CHAT_ID, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=DEVELOPER_ID, label="bob", role="developer"),
            Member(id=READONLY_ID, label="cleo", role="read_only"),
        ),
    )
    foreign_scope = Scope(
        chat_id=FOREIGN_SCOPE_CHAT_ID, kind="group", label="other-team",
        members=(Member(id=FOREIGN_MEMBER_ID, label="zed", role="admin"),),
    )
    bot = mk_bot(registry, scopes=[scope, foreign_scope])
    bot.policy = _Policy()
    bot._app.bot.username = "aipager_test_bot"
    return MiniAppServer(bot, registry, port=8766)


def _mk_session(server, label, scope_chat_id=SCOPE_CHAT_ID, status=Status.IDLE):
    sess = server.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = scope_chat_id
    sess.status = status
    return sess


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


# ===== the three presentation states (design §4) ==========================

def test_inheriting_state_is_selected_and_tagged_default(server, run_async):
    """No override at all: the scope value is both the fill AND the tag."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "medium")
        client = await _client_for(server)
        try:
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
            body = await resp.json()
            v = body["values"]["answer_length"]
            assert v == {
                "effective": "medium", "scope_default": "medium",
                "override_value": None, "overridden": False,
            }
        finally:
            await client.close()
    run_async(_run())


def test_set_same_as_scope_state_still_marked_overridden(server, run_async):
    """design §4 mechanic case 2: explicitly choosing the scope's own
    current value still counts as `overridden` — it is a recorded
    choice, not silently collapsed back into "inheriting" just because
    the numbers happen to match right now."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "medium")
        client = await _client_for(server)
        try:
            await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "medium"},
            )
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            v = (await resp.json())["values"]["answer_length"]
            assert v == {
                "effective": "medium", "scope_default": "medium",
                "override_value": "medium", "overridden": True,
            }
        finally:
            await client.close()
    run_async(_run())


def test_overridden_divergent_state_fill_and_tag_split(server, run_async):
    """The fill (effective) and the tag (scope_default) must independently
    disagree — this is the state the whole feature exists to show."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "medium")
        client = await _client_for(server)
        try:
            await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            v = (await resp.json())["values"]["answer_length"]
            assert v == {
                "effective": "short", "scope_default": "medium",
                "override_value": "short", "overridden": True,
            }
        finally:
            await client.close()
    run_async(_run())


def test_put_equal_to_scope_default_still_reports_changed_true(server, run_async):
    """design.md's `changed` semantics for PUT, verbatim: `None -> "short"`
    counts as changed even if "short" equals the scope default, because an
    explicit choice was just recorded (mechanic case 2). This compares the
    OVERRIDE before/after, never `effective` — a buggy implementation that
    compared `effective` instead would see no change here (medium ->
    medium) and wrongly report `changed: false`, silently dropping the
    mirror-to-chat notification too."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "medium")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "medium"},
            )
            assert resp.status == 200
            assert (await resp.json())["changed"] is True
            assert send.await_count == 1  # the mirror fires too
        finally:
            await client.close()
    run_async(_run())


def test_scope_default_moves_after_override_set_but_override_stays(server, run_async):
    """design.md success criterion, verbatim: changing the scope default
    after an override is set moves `scope_default` in the session's next
    GET while `override_value`/`effective` are untouched. The two numbers
    come from independent calls (get_preferences vs resolve_preferences)
    and must never contaminate each other."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "medium")
        client = await _client_for(server)
        try:
            await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            # The scope default changes underneath the override (e.g. an
            # admin edits /settings, or the Mini App's scope-wide route).
            prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "long")

            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            v = (await resp.json())["values"]["answer_length"]
            assert v["scope_default"] == "long"     # moved
            assert v["override_value"] == "short"   # untouched
            assert v["effective"] == "short"         # still the override
        finally:
            await client.close()
    run_async(_run())


# ===== DELETE returns to inheriting ========================================

def test_delete_returns_session_to_inheriting_and_effective_tracks_scope(
    server, run_async,
):
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "medium")
        client = await _client_for(server)
        try:
            await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            resp = await client.delete(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["changed"] is True
            v = body["values"]["answer_length"]
            assert v["overridden"] is False
            assert v["override_value"] is None
            assert v["effective"] == "medium"

            # And the scope itself still tracks any later scope change —
            # the session is genuinely inheriting again, not frozen at
            # whatever "medium" happened to be at delete time.
            prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "long")
            again = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            v2 = (await again.json())["values"]["answer_length"]
            assert v2["effective"] == "long"
            assert v2["scope_default"] == "long"
        finally:
            await client.close()
    run_async(_run())


def test_delete_of_an_already_unset_field_is_idempotent_no_op(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.delete(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert (await resp.json())["changed"] is False
            send.assert_not_awaited()   # no-op → no mirror
        finally:
            await client.close()
    run_async(_run())


# ===== None vs the string "none" ===========================================

def test_string_none_is_a_real_override_value_not_unset(server, run_async):
    """The operator added "none" ("don't apply any rule") deliberately in
    v0.6.0 for answer_length/language_level. Setting the override to the
    STRING "none" must show up as `overridden: true`, exactly like any
    other real value — never conflated with the field being unset."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "medium")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "none"},
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["changed"] is True
            v = body["values"]["answer_length"]
            assert v["overridden"] is True
            assert v["override_value"] == "none"
            assert v["effective"] == "none"
            assert v["scope_default"] == "medium"   # unaffected
        finally:
            await client.close()
    run_async(_run())


def test_simple_formatting_false_override_is_marked_overridden_not_unset(
    server, run_async,
):
    """`False` is a real, legal value for simple_formatting — collapsing
    "unset" and "explicitly False" into one state (e.g. testing
    `override_value` for truthiness instead of `is None`) would silently
    make this override invisible. Scope default is True so effective
    genuinely diverges too."""
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "simple_formatting", True)
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/simple_formatting",
                headers=_hdr(ADMIN_ID), json={"value": False},
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["changed"] is True
            v = body["values"]["simple_formatting"]
            assert v == {
                "effective": False, "scope_default": True,
                "override_value": False, "overridden": True,
            }
        finally:
            await client.close()
    run_async(_run())


# ===== cross-field independence ============================================

def test_overriding_one_field_leaves_the_other_three_tracking_scope(
    server, run_async,
):
    async def _run():
        _mk_session(server, "dev")
        prefs_mod.set_preference(SCOPE_CHAT_ID, "language_level", "advanced")
        client = await _client_for(server)
        try:
            await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            values = (await resp.json())["values"]
            assert values["answer_length"]["overridden"] is True
            assert values["language_level"]["overridden"] is False
            assert values["language_level"]["effective"] == "advanced"
            assert values["layout"]["overridden"] is False
            assert values["simple_formatting"]["overridden"] is False
        finally:
            await client.close()
    run_async(_run())


# ===== authorization: _can_prompt_user, NOT _is_admin_user ================

def test_readonly_member_gets_403_on_put(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(READONLY_ID), json={"value": "short"},
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_readonly_member_gets_403_on_delete(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(READONLY_ID),
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_readonly_member_gets_200_on_get_with_can_edit_false(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(READONLY_ID))
            assert resp.status == 200
            assert (await resp.json())["can_edit"] is False
        finally:
            await client.close()
    run_async(_run())


def test_developer_non_admin_can_put_without_bypass_safety(server, run_async):
    """The headline authorization decision (design.md Authorization):
    a session override is gated by can_prompt, NOT admin/bypass_safety.
    A developer (can_prompt=True, bypass_safety=False) must succeed here
    even though the SAME user is rejected by the scope-wide route."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(DEVELOPER_ID), json={"value": "short"},
            )
            assert resp.status == 200
            assert (await resp.json())["values"]["answer_length"]["override_value"] == "short"
        finally:
            await client.close()
    run_async(_run())


def test_developer_non_admin_can_delete_without_bypass_safety(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev")
        sess.override_answer_length = "short"
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(DEVELOPER_ID),
            )
            assert resp.status == 200
            assert (await resp.json())["changed"] is True
        finally:
            await client.close()
    run_async(_run())


def test_developer_write_here_but_rejected_on_the_scope_wide_route(
    server, run_async,
):
    """The asymmetry that motivates the whole design decision, in one
    test: the SAME user, the SAME request shape, two different routes,
    two different outcomes — because a session override is not a
    scope-wide change."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            session_resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(DEVELOPER_ID), json={"value": "short"},
            )
            assert session_resp.status == 200

            scope_resp = await client.put(
                "/api/preferences/answer_length",
                headers=_hdr(DEVELOPER_ID), json={"value": "short"},
            )
            assert scope_resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_outsider_gets_403_regardless_of_session(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(OUTSIDER_ID))
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_unauthenticated_write_is_rejected_and_changes_nothing(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                json={"value": "short"},
            )
            assert resp.status == 401
            check = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            assert (await check.json())["values"]["answer_length"]["overridden"] is False
        finally:
            await client.close()
    run_async(_run())


# ===== cross-scope isolation: byte-identical 404s ==========================

@pytest.mark.parametrize("label", ["ghost-does-not-exist", "other"])
def test_foreign_or_unknown_label_404s_identically_on_get(server, run_async, label):
    async def _run():
        # "other" genuinely exists — but in the FOREIGN scope.
        _mk_session(server, "other", scope_chat_id=FOREIGN_SCOPE_CHAT_ID)
        client = await _client_for(server)
        try:
            resp = await client.get(
                f"/api/sessions/{label}/preferences", headers=_hdr(ADMIN_ID))
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("label", ["ghost-does-not-exist", "other"])
def test_foreign_or_unknown_label_404s_identically_on_put(server, run_async, label):
    async def _run():
        _mk_session(server, "other", scope_chat_id=FOREIGN_SCOPE_CHAT_ID)
        client = await _client_for(server)
        try:
            resp = await client.put(
                f"/api/sessions/{label}/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("label", ["ghost-does-not-exist", "other"])
def test_foreign_or_unknown_label_404s_identically_on_delete(server, run_async, label):
    async def _run():
        _mk_session(server, "other", scope_chat_id=FOREIGN_SCOPE_CHAT_ID)
        client = await _client_for(server)
        try:
            resp = await client.delete(
                f"/api/sessions/{label}/preferences/answer_length",
                headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 404
            assert await resp.json() == {"error": "not_found"}
        finally:
            await client.close()
    run_async(_run())


def test_foreign_scope_admin_cannot_read_a_session_in_our_scope(server, run_async):
    """Symmetric check: an admin of the OTHER scope gets the same 404 for
    a label that exists, just not in their scope."""
    async def _run():
        _mk_session(server, "dev", scope_chat_id=SCOPE_CHAT_ID)
        client = await _client_for(server)
        try:
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(FOREIGN_MEMBER_ID))
            assert resp.status == 404
        finally:
            await client.close()
    run_async(_run())


# ===== server-side validation (the UI is not the gate) =====================

def test_unknown_field_is_rejected_on_put(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/not_a_real_field",
                headers=_hdr(ADMIN_ID), json={"value": "x"},
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


def test_unknown_field_is_rejected_on_delete(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.delete(
                "/api/sessions/dev/preferences/not_a_real_field",
                headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("field,value", [
    ("answer_length", "enormous"),
    ("layout", "sideways"),
    ("language_level", "l33t"),
    ("simple_formatting", "yes-please"),
    ("answer_length", None),
    ("answer_length", {"nested": "object"}),
])
def test_invalid_value_is_rejected(server, run_async, field, value):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                f"/api/sessions/dev/preferences/{field}",
                headers=_hdr(ADMIN_ID), json={"value": value},
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


def test_malformed_body_is_rejected(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), data="not json",
            )
            assert resp.status == 400
            resp2 = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"no_value_key": 1},
            )
            assert resp2.status == 400
        finally:
            await client.close()
    run_async(_run())


# ===== idempotency + the chat mirror =======================================

def test_repeat_put_of_the_same_value_does_not_re_mirror(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            assert send.await_count == 1
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            assert resp.status == 200
            assert (await resp.json())["changed"] is False
            assert send.await_count == 1
        finally:
            await client.close()
    run_async(_run())


def test_mirror_names_the_session_and_goes_to_its_own_scope_chat(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == SCOPE_CHAT_ID
            text = send.await_args.kwargs["text"]
            assert "dev" in text
            assert "changed from the Mini App" in text
        finally:
            await client.close()
    run_async(_run())


def test_reset_mirror_says_reset_to_default(server, run_async):
    async def _run():
        sess = _mk_session(server, "dev")
        sess.override_answer_length = "short"
        prefs_mod.set_preference(SCOPE_CHAT_ID, "answer_length", "medium")
        client = await _client_for(server)
        send = server.bot._app.bot.send_message
        try:
            resp = await client.delete(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID),
            )
            assert resp.status == 200
            assert send.await_count == 1
            text = send.await_args.kwargs["text"]
            assert "reset to default" in text
            assert "dev" in text
        finally:
            await client.close()
    run_async(_run())


def test_a_failing_chat_mirror_does_not_fail_the_write(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        server.bot._app.bot.send_message.side_effect = RuntimeError("telegram down")
        try:
            resp = await client.put(
                "/api/sessions/dev/preferences/answer_length",
                headers=_hdr(ADMIN_ID), json={"value": "short"},
            )
            assert resp.status == 200
            assert (await resp.json())["values"]["answer_length"]["override_value"] == "short"
        finally:
            await client.close()
    run_async(_run())


# ===== rate limiting is the SAME shared budget as the scope route =========

def test_write_route_is_rate_limited(server, run_async):
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(45):
                value = "short" if i % 2 else "long"
                resp = await client.put(
                    "/api/sessions/dev/preferences/answer_length",
                    headers=_hdr(ADMIN_ID), json={"value": value},
                )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429, "session preference write route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


def test_budget_is_shared_with_the_scope_wide_preference_route(server, run_async):
    """entrypoints.md: 'Shares the existing per-user write budget with the
    scope-preference route (~30 writes / 60s)'. Alternate writes across
    BOTH routes for the same user — the 429 must still land within the
    combined budget, proving it is one shared counter, not two separate
    30-write allowances (which would silently double the real ceiling)."""
    async def _run():
        _mk_session(server, "dev")
        client = await _client_for(server)
        try:
            seen_429 = False
            for i in range(45):
                if i % 2 == 0:
                    resp = await client.put(
                        "/api/sessions/dev/preferences/answer_length",
                        headers=_hdr(ADMIN_ID), json={"value": "short"},
                    )
                else:
                    resp = await client.put(
                        "/api/preferences/answer_length",
                        headers=_hdr(ADMIN_ID), json={"value": "long"},
                    )
                if resp.status == 429:
                    seen_429 = True
                    break
            assert seen_429
            # Well under 45 combined requests — if the budgets were
            # separate this would never trip inside this loop.
            assert i < 40
        finally:
            await client.close()
    run_async(_run())


# ===== GONE sessions remain reachable (mirrors _handle_session_detail) ====

def test_gone_session_preferences_still_readable(server, run_async):
    async def _run():
        _mk_session(server, "dev", status=Status.GONE)
        client = await _client_for(server)
        try:
            resp = await client.get(
                "/api/sessions/dev/preferences", headers=_hdr(ADMIN_ID))
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())
