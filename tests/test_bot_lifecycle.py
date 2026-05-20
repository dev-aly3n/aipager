"""Tests for aipager.bot.lifecycle.LifecycleMixin.

The hard paths (start polling, recover_sessions with real Telegram I/O)
are exercised by the integration tests. This file targets:
- ``reload_team`` — the SIGUSR1-driven live-reload of team.yaml.
- ``_update_bot_commands`` — sync /command list to Telegram.
"""

from __future__ import annotations

from unittest.mock import AsyncMock


from aipager.state import Status, TrackedSession


# ===== reload_team =====================================================

def test_reload_team_invalid_config_keeps_old(mk_bot, run_async, monkeypatch):
    """If team.yaml is malformed, keep the existing in-memory team."""
    from aipager.team import Role, Rules, Team, TeamConfigError, User as TeamUser
    bot = mk_bot()
    old_team = Team(
        group_id=-100,
        users={1: TeamUser(id=1, label="admin", role=Role.ADMIN)},
        rules=Rules(deny_tools=[]),
    )
    bot.team = old_team

    def _raise(*a, **k):
        raise TeamConfigError("bad yaml")
    monkeypatch.setattr("aipager.team.load_team", _raise)

    run_async(bot.reload_team())
    # Old team retained
    assert bot.team is old_team


def test_reload_team_personal_to_team(mk_bot, run_async, monkeypatch):
    from aipager.team import Role, Rules, Team, User as TeamUser
    bot = mk_bot()
    bot.team = None  # personal mode

    new_team = Team(
        group_id=-100,
        users={1: TeamUser(id=1, label="admin", role=Role.ADMIN)},
        rules=Rules(deny_tools=["Bash"]),
    )
    monkeypatch.setattr("aipager.team.load_team", lambda *a, **k: new_team)
    run_async(bot.reload_team())
    assert bot.team is new_team


def test_reload_team_team_to_personal(mk_bot, run_async, monkeypatch):
    from aipager.team import Role, Rules, Team, User as TeamUser
    bot = mk_bot()
    bot.team = Team(
        group_id=-100,
        users={1: TeamUser(id=1, label="admin", role=Role.ADMIN)},
        rules=Rules(deny_tools=[]),
    )
    monkeypatch.setattr("aipager.team.load_team", lambda *a, **k: None)
    run_async(bot.reload_team())
    assert bot.team is None


def test_reload_team_no_change(mk_bot, run_async, monkeypatch):
    """Both old and new are None — no-op log line only."""
    bot = mk_bot()
    bot.team = None
    monkeypatch.setattr("aipager.team.load_team", lambda *a, **k: None)
    run_async(bot.reload_team())
    assert bot.team is None


def test_reload_team_team_to_team_diff(mk_bot, run_async, monkeypatch, caplog):
    from aipager.team import Role, Rules, Team, User as TeamUser
    bot = mk_bot()
    bot.team = Team(
        group_id=-100,
        users={1: TeamUser(id=1, label="admin", role=Role.ADMIN)},
        rules=Rules(deny_tools=["Bash"]),
    )
    new_team = Team(
        group_id=-100,
        users={
            1: TeamUser(id=1, label="admin", role=Role.ADMIN),
            2: TeamUser(id=2, label="dev", role=Role.DEVELOPER),
        },
        rules=Rules(deny_tools=["Bash", "Edit"]),
    )
    monkeypatch.setattr("aipager.team.load_team", lambda *a, **k: new_team)
    run_async(bot.reload_team())
    assert bot.team is new_team
    assert len(bot.team.users) == 2


# ===== _update_bot_commands ============================================

def test_update_bot_commands_swallows_api_failure(mk_bot, run_async):
    """Telegram's setMyCommands can fail (rate-limit, etc.) — must
    not crash the daemon."""
    bot = mk_bot()
    bot._app.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("flooded"))
    # MUST NOT raise
    run_async(bot._update_bot_commands())


def test_update_bot_commands_skips_when_no_app(mk_bot, run_async):
    bot = mk_bot()
    bot._app = None
    # MUST NOT raise
    run_async(bot._update_bot_commands())


def test_update_bot_commands_no_change_skips_call(mk_bot, run_async):
    """If the registered_labels cache matches the current state, no API
    call is made — saves rate-limit budget."""
    bot = mk_bot()
    bot._app.bot.set_my_commands = AsyncMock()
    # First call populates the cache
    run_async(bot._update_bot_commands())
    first_count = bot._app.bot.set_my_commands.await_count
    # Second call with the same registry state should be a no-op
    run_async(bot._update_bot_commands())
    assert bot._app.bot.set_my_commands.await_count == first_count


def test_update_bot_commands_includes_session_labels(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot._app.bot.set_my_commands = AsyncMock()
    run_async(bot._update_bot_commands())
    bot._app.bot.set_my_commands.assert_awaited_once()
    cmds = bot._app.bot.set_my_commands.await_args.args[0]
    cmd_names = {c.command for c in cmds}
    assert "jim" in cmd_names


def test_update_bot_commands_excludes_gone_sessions(mk_bot, run_async):
    bot = mk_bot()
    sess = TrackedSession(name="claude-gone", label="gone", status=Status.GONE)
    bot.registry._sessions["claude-gone"] = sess
    bot._app.bot.set_my_commands = AsyncMock()
    run_async(bot._update_bot_commands())
    cmds = bot._app.bot.set_my_commands.await_args.args[0]
    cmd_names = {c.command for c in cmds}
    assert "gone" not in cmd_names


# ===== _update_bot_commands — per-scope (Phase G) ======================

def _scope(chat_id, label):
    from aipager.scope import Member, Scope
    return Scope(chat_id=chat_id, kind="dm" if chat_id > 0 else "group",
                 label=label,
                 members=(Member(id=abs(chat_id), label="u", role="user"),))


def _stamped(name, label, scope_chat_id):
    s = TrackedSession(name=name, label=label, status=Status.IDLE)
    s.scope_chat_id = scope_chat_id
    return s


def test_update_bot_commands_per_scope_registers_each_chat(mk_bot, run_async):
    from telegram import BotCommandScopeChat
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(-200, "grp")])
    bot.registry._sessions["claude-jim__d100"] = _stamped(
        "claude-jim__d100", "jim", 100)
    bot.registry._sessions["claude-bob__g200"] = _stamped(
        "claude-bob__g200", "bob", -200)
    bot._app.bot.set_my_commands = AsyncMock()
    run_async(bot._update_bot_commands())

    per_chat = {}
    for call in bot._app.bot.set_my_commands.await_args_list:
        scope_obj = call.kwargs["scope"]
        assert isinstance(scope_obj, BotCommandScopeChat)
        names = {c.command for c in call.args[0]}
        per_chat[scope_obj.chat_id] = names
    assert per_chat.keys() == {100, -200}
    assert "jim" in per_chat[100] and "bob" not in per_chat[100]
    assert "bob" in per_chat[-200] and "jim" not in per_chat[-200]


def test_update_bot_commands_per_scope_skips_unchanged(mk_bot, run_async):
    bot = mk_bot(scopes=[_scope(100, "ana")])
    bot.registry._sessions["claude-jim__d100"] = _stamped(
        "claude-jim__d100", "jim", 100)
    bot._app.bot.set_my_commands = AsyncMock()
    run_async(bot._update_bot_commands())
    n = bot._app.bot.set_my_commands.await_count
    run_async(bot._update_bot_commands())  # no change → no new calls
    assert bot._app.bot.set_my_commands.await_count == n


# ===== _send_keyboard — scope-filtered labels (Phase G) ================

def test_send_keyboard_scopes_labels_to_chat(mk_bot, run_async):
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(200, "ben")])
    bot.registry._sessions["claude-jim__d100"] = _stamped(
        "claude-jim__d100", "jim", 100)
    bot.registry._sessions["claude-bob__d200"] = _stamped(
        "claude-bob__d200", "bob", 200)
    run_async(bot._send_keyboard(level="main", chat_id=100))
    call = bot._app.bot.send_message.await_args
    assert call.args[0] == 100  # routed to the calling chat
    kb = call.kwargs["reply_markup"]
    btns = {b.text for row in kb.keyboard for b in row}
    assert "jim" in btns
    assert "bob" not in btns


def test_send_keyboard_multiscope_no_chat_hides_session_labels(mk_bot, run_async):
    bot = mk_bot(scopes=[_scope(100, "ana")])
    bot.registry._sessions["claude-jim__d100"] = _stamped(
        "claude-jim__d100", "jim", 100)
    run_async(bot._send_keyboard(level="main", chat_id=None))
    kb = bot._app.bot.send_message.await_args.kwargs["reply_markup"]
    btns = {b.text for row in kb.keyboard for b in row}
    assert "jim" not in btns  # no leak on an unaddressed broadcast
    assert "status" in btns   # static rows still present
