"""`/update` and its buttons: the admin gate, status text, menu (8.36)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import install_source
from aipager.bot import update_flow
from aipager.install_source import InstallSource
from aipager.scope import Member, Scope

GROUP = -100777
ADMIN, MEMBER, STRANGER = 555, 777, 999999


class _Role:
    def __init__(self, bypass):
        self.bypass_safety = bypass
        self.can_prompt = True
        self.bypass_role_denies = False


class _Policy:
    def get_role(self, name):
        return _Role(name == "admin")


def _scoped(env):
    env.bot.scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ADMIN, label="ada", role="admin"),
        Member(id=MEMBER, label="bob", role="developer"),
    ))]
    env.bot.policy = _Policy()
    env.chat_id = GROUP


def _cmd_update(env, user_id, chat_id):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = "/update"
    update.message.chat = MagicMock()
    update.message.chat.id = chat_id
    update.message.reply_text = AsyncMock(return_value=env.message)
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update


def _tap(env, data, user_id, chat_id):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = env._mk_message(chat_id)
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, query


def _replies(update):
    return [c.args[0] for c in update.message.reply_text.await_args_list]


async def _drain(env):
    for task in list(env.manager._tasks):
        await task


# ----- the gate on /update -------------------------------------------------------

def test_update_cmd_refuses_non_admin_member(env, run):
    _scoped(env)
    update = _cmd_update(env, MEMBER, GROUP)

    async def scenario():
        await update_flow.handle_update_cmd(env.bot, update, MagicMock())
    run(scenario)
    assert any("Only the admin can update aipager" in r for r in _replies(update))
    assert env.calls == [] and env.fetches == []


def test_update_cmd_refuses_stranger_in_personal_mode(env, run):
    update = _cmd_update(env, STRANGER, STRANGER)

    async def scenario():
        await update_flow.handle_update_cmd(env.bot, update, MagicMock())
    run(scenario)
    assert any("Only the admin can update aipager" in r for r in _replies(update))
    assert env.calls == [] and env.fetches == []


def test_update_cmd_allows_scope_admin(env, run):
    _scoped(env)
    update = _cmd_update(env, ADMIN, GROUP)

    async def scenario():
        await update_flow.handle_update_cmd(env.bot, update, MagicMock())
        await _drain(env)
    run(scenario)
    assert _replies(update) == ["🔎 Checking versions…"]
    assert "<b>aipager</b> 0.7.13" in env.last_text()


def test_is_update_admin_rejects_missing_user(env, monkeypatch):
    assert env.bot._is_update_admin(None, env.chat_id) is False
    # A bool is an int in Python: with an operator whose id is 1, `True`
    # would otherwise pass the personal-mode operator check.
    monkeypatch.setattr("aipager.config.CHAT_ID", "1")
    assert env.bot._is_update_admin(True, 1) is False
    assert env.bot._is_update_admin(1, 1) is True
    monkeypatch.setattr("aipager.config.CHAT_ID", str(env.chat_id))
    assert env.bot._is_update_admin(True, env.chat_id) is False
    assert env.bot._is_update_admin(env.chat_id, env.chat_id) is True
    _scoped(env)
    assert env.bot._is_update_admin(None, GROUP) is False
    assert env.bot._is_update_admin(ADMIN, GROUP) is True
    assert env.bot._is_update_admin(MEMBER, GROUP) is False


# ----- the gate on every button -------------------------------------------------

@pytest.mark.parametrize("verb", ["cc", "ap", "both", "now", "wait", "stop"])
def test_update_callback_refuses_non_admin(verb, env, run):
    _scoped(env)
    data = f"_:up:{verb}" + (":123" if verb in ("now", "wait", "stop") else "")
    update, query = _tap(env, data, MEMBER, GROUP)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
    run(scenario)
    assert env.manager.snapshot() is None
    assert env.calls == []
    texts = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert "🚫 Only the admin can update aipager." in texts


def test_update_callback_refuses_stranger_in_personal_mode(env, run):
    update, query = _tap(env, "_:up:ap", STRANGER, STRANGER)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
    run(scenario)
    assert env.manager.snapshot() is None
    assert env.calls == []


def test_update_callback_starts_a_job_for_the_operator(env, run):
    update, query = _tap(env, "_:up:cc", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
        await env.finish()
    run(scenario)
    assert env.manager.snapshot()["kind"] == "claude"
    assert env.manager.snapshot()["phase"] == "done"


def test_menu_cancel_changes_nothing(env, run):
    update, query = _tap(env, "_:up:x", env.chat_id, env.chat_id)

    async def scenario():
        await env.bot._handle_callback(update, MagicMock())
    run(scenario)
    query.edit_message_text.assert_awaited()
    assert "nothing changed" in query.edit_message_text.await_args.args[0]
    assert env.manager.snapshot() is None


# ----- status text ---------------------------------------------------------------

def _status_text(env, run, chat_id=None):
    chat = env.chat_id if chat_id is None else chat_id
    update = _cmd_update(env, env.chat_id, chat)

    async def scenario():
        await update_flow.handle_update_cmd(env.bot, update, MagicMock())
        await _drain(env)
    run(scenario)
    return env.last_text(), env.last_markup_data()


def test_update_status_shows_versions_source_restart_and_buttons(env, run):
    env.source = InstallSource(kind="pipx", prefix="/p", python="/p/bin/python",
                               origin="local", origin_detail="/home/op/aipager",
                               upgradable=True)
    text, buttons = _status_text(env, run)
    assert "<b>aipager</b> 0.7.13 · latest 0.7.14 — update available" in text
    assert "pipx, from local path /home/op/aipager" in text
    assert "<b>Claude Code</b> 2.1.281 · latest 2.1.290 — update available" in text
    assert "automatic (systemd)" in text
    assert buttons == ["_:up:cc", "_:up:ap", "_:up:both", "_:up:x"]


def test_update_status_renders_unknown_not_error(env, run, monkeypatch):
    env.pypi_latest = None        # PyPI unreachable
    env.claude_latest = None      # release channel unreachable
    env.claude_found = False
    env.set_under_unit(False)
    text, buttons = _status_text(env, run)
    assert "latest unknown" in text
    assert "<b>Claude Code</b> unknown · latest unknown" in text
    assert "Traceback" not in text and "Error" not in text
    assert buttons == ["_:up:ap", "_:up:x"]


def test_status_lookup_exception_still_renders_unknown(env, run, monkeypatch):
    from aipager import self_update

    def _boom():
        raise RuntimeError("network stack on fire")
    monkeypatch.setattr(self_update, "latest_aipager_version", _boom)
    text, _ = _status_text(env, run)
    assert "latest unknown" in text
    assert "on fire" not in text


def test_group_chat_hides_install_path(env, run):
    _scoped(env)
    env.source = InstallSource(kind="pipx", prefix="/p", python="/p/bin/python",
                               origin="local", origin_detail="/home/op/aipager",
                               upgradable=True)
    update = _cmd_update(env, ADMIN, GROUP)

    async def scenario():
        await update_flow.handle_update_cmd(env.bot, update, MagicMock())
        await _drain(env)
    run(scenario)
    assert "/home/op/aipager" not in env.last_text()
    assert "pipx, from a local path" in env.last_text()


def test_refused_source_hides_aipager_and_both(env, run, monkeypatch):
    env.source = InstallSource(kind="editable", prefix="/src", python="/src/python",
                               reason="this is an editable (development) install")
    text, buttons = _status_text(env, run)
    assert buttons == ["_:up:cc", "_:up:x"]
    assert "can't update from here" in text


def test_busy_update_says_already_running(env, run):
    import threading

    env.upgrade_release = threading.Event()
    update = _cmd_update(env, env.chat_id, env.chat_id)

    async def scenario():
        await env.start("aipager")
        await env.until(lambda: env.upgrade_calls())
        await update_flow.handle_update_cmd(env.bot, update, MagicMock())
        env.upgrade_release.set()
        await env.finish()
    run(scenario)
    assert any("An update is already running" in r for r in _replies(update))


def test_update_is_registered_and_in_help(mk_bot):
    from aipager.bot.lifecycle import LifecycleMixin

    names = [c.command for c in LifecycleMixin._command_list(set())]
    assert "update" in names
    import inspect
    from aipager.bot import handlers
    assert "/update — update aipager and Claude Code (admin)" in inspect.getsource(
        handlers.CommandHandlersMixin._handle_start_cmd)


def test_status_is_cached_between_mini_app_polls(env, run):
    async def scenario():
        await env.manager.status()
        await env.manager.status()
        assert env.fetches.count(__import__("aipager.self_update").self_update.PYPI_URL) == 1
        await env.manager.status(force=True)
        assert len([f for f in env.fetches if f.endswith("aipager/json")]) == 2
    run(scenario)


def test_resolve_tool_patch_point_is_used(env, run, monkeypatch):
    # The flow must build argv from install_source (the one table).
    seen = []
    monkeypatch.setattr(install_source, "resolve_tool",
                        lambda n: seen.append(n) or f"/abs/bin/{n}")

    async def scenario():
        await env.start("aipager")
        await env.finish()
    run(scenario)
    assert "pipx" in seen
