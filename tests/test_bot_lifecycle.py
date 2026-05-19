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
