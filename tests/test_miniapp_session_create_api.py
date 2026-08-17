"""POST /api/sessions — the only route that can spawn a process.

Every test here mocks `bot.create_session`; nothing in this file may
launch a real Claude process or touch a real dtach socket.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.miniapp.server import MiniAppServer
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"

ADMIN_ID = 555      # bypass_safety + can_prompt
MEMBER_ID = 777     # can_prompt, NOT admin
READONLY_ID = 888   # neither
OUTSIDER_ID = 999


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
        "user": json.dumps({"id": user_id, "first_name": "T"}),
    }
    fields["hash"] = _sign(fields, bot_token)
    return urlencode(fields)


def _hdr(user_id):
    return {"X-Telegram-Init-Data": _init_data(user_id)}


class _Role:
    def __init__(self, name):
        self.bypass_safety = name == "admin"
        self.can_prompt = name in ("admin", "developer")


class _Policy:
    def get_role(self, name):
        return _Role(name)


@pytest.fixture
def server(mk_bot, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    # The daemon's own directory is an allowed root (allowed_roots), so it
    # must be pinned inside tmp_path or every test here would inherit the
    # real repo checkout as a launchable — and creatable-in — directory.
    monkeypatch.setattr("aipager.dtach.inject._PROJECT_DIR", str(project))
    registry = SessionRegistry()
    scope = Scope(
        chat_id=-100, kind="group", label="team",
        members=(
            Member(id=ADMIN_ID, label="ada", role="admin"),
            Member(id=MEMBER_ID, label="bob", role="developer"),
            Member(id=READONLY_ID, label="cy", role="read_only"),
        ),
    )
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = _Policy()
    # An existing session establishes the allowed root — the picker is
    # seeded from directories this scope already works in.
    seed = registry.get_or_create("claude-seed__g100")
    seed.label = "seed"
    seed.scope_chat_id = -100
    seed.status = Status.GONE
    seed.cwd = str(project)

    srv = MiniAppServer(bot, registry, port=8765)
    # THE mock that keeps every test in this file from spawning anything.
    bot.create_session = AsyncMock(return_value=("claude-dev__g100", ""))
    srv._project_dir = str(project)
    return srv


async def _client_for(srv):
    client = TestClient(TestServer(srv._build_app()))
    await client.start_server()
    return client


def _post(client, body, user_id=ADMIN_ID):
    return client.post("/api/sessions", headers=_hdr(user_id), json=body)


# ===== happy path =========================================================

def test_create_launches_and_returns_the_label(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev"})
            assert resp.status == 200
            assert (await resp.json())["label"] == "dev"
            server.bot.create_session.assert_awaited_once()
            kwargs = server.bot.create_session.await_args.kwargs
            assert kwargs["scope_chat_id"] == -100
            assert kwargs["skip_perms"] is False
        finally:
            await client.close()
    run_async(_run())


def test_create_announces_the_session_in_chat(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            await _post(client, {"name": "dev"})
            assert server.bot._app.bot.send_message.await_count == 1
            assert server.bot._app.bot.send_message.await_args.kwargs["chat_id"] == -100
        finally:
            await client.close()
    run_async(_run())


def test_a_failing_chat_mirror_does_not_fail_a_launched_session(server, run_async):
    """The process is already running by the time we announce it — a
    failed notification must not report failure the operator might retry."""
    async def _run():
        client = await _client_for(server)
        server.bot._app.bot.send_message.side_effect = RuntimeError("telegram down")
        try:
            assert (await _post(client, {"name": "dev"})).status == 200
        finally:
            await client.close()
    run_async(_run())


# ===== authorization ======================================================

def test_unauthenticated_cannot_create(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.post("/api/sessions", json={"name": "dev"})
            assert resp.status == 401
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_outsider_cannot_create(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            assert (await _post(client, {"name": "dev"}, OUTSIDER_ID)).status == 403
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_readonly_member_cannot_create(server, run_async):
    """A READ_ONLY member cannot prompt a session, so must not be able to
    spawn one."""
    async def _run():
        client = await _client_for(server)
        try:
            assert (await _post(client, {"name": "dev"}, READONLY_ID)).status == 403
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_ordinary_member_can_create_without_admin(server, run_async):
    """Creating is the same capability as driving — deliberately NOT
    gated on bypass_safety."""
    async def _run():
        client = await _client_for(server)
        try:
            assert (await _post(client, {"name": "dev"}, MEMBER_ID)).status == 200
        finally:
            await client.close()
    run_async(_run())


def test_auto_mode_requires_admin(server, run_async):
    """`/new !name` is admin-gated in chat. Without the same gate here the
    Mini App would hand a non-admin --dangerously-skip-permissions."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "skip_perms": True}, MEMBER_ID)
            assert resp.status == 403
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_admin_may_use_auto_mode(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "skip_perms": True}, ADMIN_ID)
            assert resp.status == 200
            assert server.bot.create_session.await_args.kwargs["skip_perms"] is True
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("truthy", ["true", 1, "yes", ["x"]])
def test_only_a_real_bool_enables_auto_mode(server, run_async, truthy):
    """A truthy-but-not-True value must not smuggle Auto mode past the
    admin gate for a non-admin."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "skip_perms": truthy}, MEMBER_ID)
            assert resp.status == 200, "non-bool truthy should be ignored, not gated"
            assert server.bot.create_session.await_args.kwargs["skip_perms"] is False
        finally:
            await client.close()
    run_async(_run())


# ===== validation is server-side ==========================================

@pytest.mark.parametrize("name", [
    "", "   ", "has space", "../../etc/passwd", "dev;rm -rf /",
    "dev\x00evil", "status", "a" * 200, None, 123,
])
def test_bad_names_rejected_without_launching(server, run_async, name):
    async def _run():
        client = await _client_for(server)
        try:
            assert (await _post(client, {"name": name})).status == 400
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_directory_outside_the_allowed_roots_is_rejected(server, run_async, tmp_path):
    async def _run():
        client = await _client_for(server)
        outside = tmp_path / "not-a-project"
        outside.mkdir()
        try:
            resp = await _post(client, {"name": "dev", "cwd": str(outside)})
            assert resp.status == 400
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_traversal_in_cwd_is_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "cwd": "/etc/../etc"})
            assert resp.status == 400
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_allowed_directory_is_passed_through_resolved(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "cwd": server._project_dir})
            assert resp.status == 200
            import os
            assert server.bot.create_session.await_args.kwargs["cwd"] == \
                os.path.realpath(server._project_dir)
        finally:
            await client.close()
    run_async(_run())


def test_malformed_body_rejected(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            r1 = await client.post("/api/sessions", headers=_hdr(ADMIN_ID), data="nope")
            assert r1.status == 400
            r2 = await client.post("/api/sessions", headers=_hdr(ADMIN_ID), json=[1, 2])
            assert r2.status == 400
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


# ===== collisions and failures ============================================

def test_name_collision_with_a_live_session_is_a_409_not_a_launch(server, run_async):
    async def _run():
        live = server.registry.get_or_create("claude-taken__g100")
        live.label = "taken"
        live.scope_chat_id = -100
        live.status = Status.BUSY
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "taken"})
            assert resp.status == 409
            assert "already exists" in (await resp.json())["detail"]
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_a_refused_launch_surfaces_as_an_error_not_a_success(server, run_async):
    async def _run():
        server.bot.create_session = AsyncMock(return_value=("", "dtach not installed"))
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev"})
            assert resp.status == 400
            assert "dtach" in (await resp.json())["detail"]
        finally:
            await client.close()
    run_async(_run())


def test_create_is_rate_limited(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            saw_429 = False
            for i in range(45):
                resp = await _post(client, {"name": f"dev{i}"})
                if resp.status == 429:
                    saw_429 = True
                    break
            assert saw_429, "create route accepted unbounded launches"
        finally:
            await client.close()
    run_async(_run())


# ===== the options route ==================================================

def test_options_lists_only_directories_this_scope_uses(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(ADMIN_ID))).json()
            import os
            assert body["directories"] == [os.path.realpath(server._project_dir)]
            assert body["can_create"] is True
            assert body["can_use_auto"] is True
        finally:
            await client.close()
    run_async(_run())


def test_options_tells_a_readonly_member_they_cannot_create(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(READONLY_ID))).json()
            assert body["can_create"] is False
            assert body["can_use_auto"] is False
        finally:
            await client.close()
    run_async(_run())


def test_options_tells_a_member_they_cannot_use_auto(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(MEMBER_ID))).json()
            assert body["can_create"] is True
            assert body["can_use_auto"] is False
        finally:
            await client.close()
    run_async(_run())


# ===== model selection ====================================================

def test_options_serves_the_canonical_model_list(server, run_async):
    """The client must not keep its own model list — one source of truth,
    the same one the chat keyboard offers."""
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(ADMIN_ID))).json()
            from aipager.config import MODEL_CHOICES
            assert [m["label"] for m in body["models"]] == \
                [label for label, _ in MODEL_CHOICES]
            # Each alias carries a "what is this for" hint instead of a
            # version number: an alias always resolves to the latest of its
            # family, so a baked-in version would be wrong on the next
            # release. No entry may look like one.
            import re
            for m in body["models"]:
                assert "hint" in m
                assert not re.search(r"\d+(\.\d+)?$", m["hint"] or "x"), (
                    f"model hint looks like a pinned version: {m!r}"
                )
        finally:
            await client.close()
    run_async(_run())


def test_chosen_model_is_passed_to_the_launch_not_typed_into_the_session(
    server, run_async,
):
    """The model must reach `launch_session` as `--model`, NOT be queued as
    a `/model` prompt.

    Queuing it was a real bug seen live: a queued prompt drains on the
    session's FIRST IDLE, which is after the operator's first real message
    has been answered. Choosing a model therefore produced a spurious
    second turn, a second busy card, and a duplicate of the previous
    answer (a slash command yields no new assistant text, so the idle
    notification re-sent the last one).
    """
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "model": "Opus"})
            assert resp.status == 200
            assert server.bot.create_session.await_args.kwargs["model"] == "opus"
            # and nothing was queued to be typed in
            sess = server.registry.get_or_create("claude-dev__g100")
            assert not sess.pending_queue, sess.pending_queue
        finally:
            await client.close()
    run_async(_run())


def test_no_model_choice_passes_no_model(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            await _post(client, {"name": "dev", "model": ""})
            assert server.bot.create_session.await_args.kwargs["model"] is None
        finally:
            await client.close()
    run_async(_run())


def test_no_model_key_at_all_passes_no_model(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev"})
            assert resp.status == 200
            assert server.bot.create_session.await_args.kwargs["model"] is None
        finally:
            await client.close()
    run_async(_run())


def test_a_full_model_name_is_passed_through(server, run_async):
    """`claude --model` takes "an alias for the latest model ... or a
    model's full name", so the enumeration cannot be the rule. A name the
    CLI does not know is the CLI's problem to report, not ours to guess
    at — what matters here is that it arrives unmangled."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "model": "claude-opus-5"})
            assert resp.status == 200
            assert server.bot.create_session.await_args.kwargs["model"] == "claude-opus-5"
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("model", [
    "../../etc/passwd",             # traversal
    "opus; rm -rf /",               # shell metacharacters
    "opus$(whoami)",
    "opus`id`",
    "opus\nsonnet",                 # newline
    "opus sonnet",                  # space — two argv tokens
    "opus\x00evil",                 # NUL
    "-rf",                          # reads as another FLAG, not a value
    "--dangerously-skip-permissions",   # ...and THAT one is the admin gate
    "a" * 65,                       # over the cap
    123, [], {},                    # not text
])
def test_a_hostile_model_is_refused_and_nothing_is_launched(server, run_async, model):
    """A rejected model must be an error, not a silent drop: launching
    something other than what was picked is the kind of quiet lie that
    costs an hour to notice."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "model": model})
            assert resp.status == 400, model
            server.bot.create_session.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_model_alias_comes_from_the_send_field_not_the_label(server, run_async, monkeypatch):
    """`keyboard.json` lets an operator map a label to an unrelated command.
    The alias handed to `--model` must come from that command, not from
    lowercasing the label — the default list hides the difference because
    every shipped label lowercases into its own alias.
    """
    monkeypatch.setattr(
        "aipager.config.MODEL_CHOICES",
        [("Claude 4.5 Opus", "/model claude-opus-4-5")],
    )

    async def _run():
        client = await _client_for(server)
        try:
            resp = await _post(client, {"name": "dev", "model": "Claude 4.5 Opus"})
            assert resp.status == 200
            assert server.bot.create_session.await_args.kwargs["model"] == "claude-opus-4-5"
        finally:
            await client.close()
    run_async(_run())


def test_options_serves_the_schema_and_scope_defaults_for_the_form(server, run_async):
    """The Advanced section renders reply-style settings from these, so a
    missing key silently empties that part of the form."""
    async def _run():
        client = await _client_for(server)
        try:
            body = await (await client.get(
                "/api/session-options", headers=_hdr(ADMIN_ID))).json()
            fields = {g["field"] for g in body["schema"]}
            assert fields == {
                "layout", "simple_formatting", "answer_length", "language_level",
            }
            assert set(body["scope_defaults"]) == fields
        finally:
            await client.close()
    run_async(_run())



# ===== POST /api/directories ==============================================
#
# The only route in aipager that writes to the filesystem outside its own
# config, and it is reachable over a public tunnel. Every test here keeps
# its parent inside tmp_path — nothing may appear anywhere else.

def _mkdir(client, body, user_id=ADMIN_ID):
    return client.post("/api/directories", headers=_hdr(user_id), json=body)


def test_a_folder_is_created_inside_an_allowed_root(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _mkdir(client, {
                "parent": server._project_dir, "name": "fresh",
            })
            assert resp.status == 200
            body = await resp.json()
            import os
            expected = os.path.join(os.path.realpath(server._project_dir), "fresh")
            assert body["path"] == expected
            assert body["existed"] is False
            assert os.path.isdir(expected)
        finally:
            await client.close()
    run_async(_run())


def test_a_new_folder_is_announced_in_chat(server, run_async):
    """Every other Mini App write mirrors, and this one writes to the
    operator's disk from a phone — chat is the only channel they read."""
    async def _run():
        client = await _client_for(server)
        try:
            await _mkdir(client, {"parent": server._project_dir, "name": "announced"})
            assert server.bot._app.bot.send_message.await_count == 1
            kwargs = server.bot._app.bot.send_message.await_args.kwargs
            assert kwargs["chat_id"] == -100
            assert "announced" in kwargs["text"]
        finally:
            await client.close()
    run_async(_run())


def test_reusing_an_existing_folder_is_not_announced(server, run_async):
    """Reuse is a no-op on disk; announcing it would be noise."""
    async def _run():
        client = await _client_for(server)
        try:
            await _mkdir(client, {"parent": server._project_dir, "name": "twice"})
            server.bot._app.bot.send_message.reset_mock()
            resp = await _mkdir(client, {"parent": server._project_dir, "name": "twice"})
            assert resp.status == 200
            assert (await resp.json())["existed"] is True
            server.bot._app.bot.send_message.assert_not_awaited()
        finally:
            await client.close()
    run_async(_run())


def test_a_failing_chat_mirror_does_not_fail_a_created_folder(server, run_async):
    """The folder already exists by then — a failed notification must not
    report it as an error the operator would retry."""
    async def _run():
        client = await _client_for(server)
        server.bot._app.bot.send_message.side_effect = RuntimeError("telegram down")
        try:
            resp = await _mkdir(client, {"parent": server._project_dir, "name": "quiet"})
            assert resp.status == 200
        finally:
            await client.close()
    run_async(_run())


def test_a_readonly_member_cannot_create_a_folder(server, run_async):
    """Same gate as creating a session: no prompting, no writing to disk."""
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _mkdir(
                client, {"parent": server._project_dir, "name": "nope"},
                user_id=READONLY_ID,
            )
            assert resp.status == 403
            import os
            assert not os.path.exists(
                os.path.join(server._project_dir, "nope"),
            )
        finally:
            await client.close()
    run_async(_run())


def test_a_non_member_cannot_create_a_folder(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await _mkdir(
                client, {"parent": server._project_dir, "name": "nope"},
                user_id=OUTSIDER_ID,
            )
            assert resp.status == 403
        finally:
            await client.close()
    run_async(_run())


def test_creating_a_folder_without_init_data_is_unauthorized(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/directories",
                json={"parent": server._project_dir, "name": "nope"},
            )
            assert resp.status == 401
        finally:
            await client.close()
    run_async(_run())


def test_folder_creation_is_rate_limited(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            saw_429 = False
            for i in range(40):
                resp = await _mkdir(
                    client, {"parent": server._project_dir, "name": f"d{i}"},
                )
                if resp.status == 429:
                    saw_429 = True
                    break
            assert saw_429, "mkdir route accepted unbounded writes"
        finally:
            await client.close()
    run_async(_run())


def test_a_parent_outside_the_allowed_roots_is_refused(server, run_async, tmp_path):
    async def _run():
        client = await _client_for(server)
        outside = tmp_path / "not-a-project"
        outside.mkdir()
        try:
            resp = await _mkdir(client, {"parent": str(outside), "name": "evil"})
            assert resp.status == 400
            import os
            assert not os.path.exists(os.path.join(str(outside), "evil"))
        finally:
            await client.close()
    run_async(_run())


@pytest.mark.parametrize("name", [
    "../escaped", "a/b", "..", ".", "-rf", "", None, 123, False,
])
def test_a_hostile_folder_name_is_refused(server, run_async, name, tmp_path):
    """Refused AND inert: nothing may appear anywhere under tmp_path, not
    just at the one path the traversal case aimed for."""
    import os

    before = {
        os.path.join(dirpath, d)
        for dirpath, dirs, _files in os.walk(str(tmp_path)) for d in dirs
    }

    async def _run():
        client = await _client_for(server)
        try:
            resp = await _mkdir(
                client, {"parent": server._project_dir, "name": name},
            )
            assert resp.status == 400, name
        finally:
            await client.close()
    run_async(_run())

    after = {
        os.path.join(dirpath, d)
        for dirpath, dirs, _files in os.walk(str(tmp_path)) for d in dirs
    }
    assert after == before, f"{name!r} created {after - before}"


def test_a_malformed_folder_body_is_refused(server, run_async):
    async def _run():
        client = await _client_for(server)
        try:
            resp = await client.post(
                "/api/directories", headers=_hdr(ADMIN_ID), data="not json",
            )
            assert resp.status == 400
            resp = await client.post(
                "/api/directories", headers=_hdr(ADMIN_ID), json=["a", "b"],
            )
            assert resp.status == 400
        finally:
            await client.close()
    run_async(_run())


def test_a_created_folder_can_then_be_used_as_a_working_directory(server, run_async):
    """The point of the whole feature: what the route hands back must
    pass validate_cwd on the create request that follows."""
    async def _run():
        client = await _client_for(server)
        try:
            made = await (await _mkdir(
                client, {"parent": server._project_dir, "name": "usable"},
            )).json()
            resp = await _post(client, {"name": "dev", "cwd": made["path"]})
            assert resp.status == 200
            assert server.bot.create_session.await_args.kwargs["cwd"] == made["path"]
        finally:
            await client.close()
    run_async(_run())
