"""Tests for wizard.edit_menu — admin re-configuration flow.

Each ``_edit_*`` function reads questionary prompts then mutates
team.yaml / config.env. We stub the prompts and the persistence
helpers so the logic can be exercised without writing real config.
"""

from __future__ import annotations


import pytest

from aipager.team import Role, Rules, Team, User as TeamUser
from aipager.wizard import edit_menu


def _stub_ask(monkeypatch, answers):
    queue = iter(answers)
    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers")
    monkeypatch.setattr(edit_menu, "_ask", _ask)


def _team(*users, deny_tools=()):
    admin = users or (TeamUser(id=1, label="admin", role=Role.ADMIN),)
    return Team(
        group_id=-100,
        users={u.id: u for u in admin},
        rules=Rules(deny_tools=tuple(deny_tools)),
    )


@pytest.fixture
def stub_dump_team(monkeypatch):
    """Capture dump_team calls without writing to disk."""
    calls = []
    monkeypatch.setattr("aipager.team.dump_team",
                        lambda t: calls.append(t))
    return calls


# ---- _edit_add_user -----------------------------------------------------

def test_edit_add_user_cancelled_when_no_entries(monkeypatch):
    team = _team()
    monkeypatch.setattr(edit_menu, "_collect_users", lambda **k: [])
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("tok", "5"))
    assert edit_menu._edit_add_user(team) is None


def test_edit_add_user_declined_at_confirm(monkeypatch):
    team = _team()
    monkeypatch.setattr(edit_menu, "_collect_users",
                        lambda **k: [{"id": 2, "label": "bob",
                                       "role": "developer"}])
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("tok", "5"))
    _stub_ask(monkeypatch, [False])  # decline confirm
    assert edit_menu._edit_add_user(team) is None


def test_edit_add_user_adds_developer(monkeypatch, stub_dump_team):
    team = _team()
    monkeypatch.setattr(edit_menu, "_collect_users",
                        lambda **k: [{"id": 2, "label": "bob",
                                       "role": "developer"}])
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("tok", "5"))
    _stub_ask(monkeypatch, [True])  # confirm
    new_team = edit_menu._edit_add_user(team)
    assert new_team is not None
    assert 2 in new_team.users
    assert len(stub_dump_team) == 1


# ---- _edit_review_pending ------------------------------------------------

def test_edit_review_pending_empty_returns_none(monkeypatch):
    team = _team()
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [])
    assert edit_menu._edit_review_pending(team) is None


def test_edit_review_pending_skip(monkeypatch):
    team = _team()
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [
        {"user_id": 99, "username": "alice", "display_name": "Alice",
         "first_seen": "2026-05-19"},
    ])
    monkeypatch.setattr("aipager.team.clear_pending_user",
                        lambda uid: None)
    _stub_ask(monkeypatch, ["skip"])
    assert edit_menu._edit_review_pending(team) is None


def test_edit_review_pending_dismiss_clears(monkeypatch):
    team = _team()
    cleared = []
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [
        {"user_id": 99, "username": "alice", "display_name": "Alice",
         "first_seen": "2026-05-19"},
    ])
    monkeypatch.setattr("aipager.team.clear_pending_user",
                        lambda uid: cleared.append(uid))
    _stub_ask(monkeypatch, ["dismiss"])
    edit_menu._edit_review_pending(team)
    assert cleared == [99]


def test_edit_review_pending_add_as_developer(monkeypatch, stub_dump_team):
    team = _team()
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [
        {"user_id": 99, "username": "alice", "display_name": "Alice",
         "first_seen": "2026-05-19"},
    ])
    monkeypatch.setattr("aipager.team.clear_pending_user", lambda uid: None)
    _stub_ask(monkeypatch, ["developer"])
    new_team = edit_menu._edit_review_pending(team)
    assert new_team is not None
    assert 99 in new_team.users
    assert new_team.users[99].role == Role.DEVELOPER


def test_edit_review_pending_skips_existing_users(monkeypatch):
    """If a pending entry's id is already on the allow-list, it's silently
    cleared without prompting."""
    team = _team()
    cleared = []
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [
        # id=1 is the admin already in the team
        {"user_id": 1, "username": "admin", "display_name": "Admin",
         "first_seen": "2026-05-19"},
    ])
    monkeypatch.setattr("aipager.team.clear_pending_user",
                        lambda uid: cleared.append(uid))
    # No _ask should fire (since the only entry is auto-cleared)
    monkeypatch.setattr(edit_menu, "_ask",
                        lambda p: (_ for _ in ()).throw(AssertionError(
                            "_ask should not be called")))
    assert edit_menu._edit_review_pending(team) is None
    assert cleared == [1]


def test_edit_review_pending_label_clash_prompts_new_label(monkeypatch, stub_dump_team):
    team = _team(TeamUser(id=1, label="alice", role=Role.ADMIN))
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [
        {"user_id": 99, "username": "alice", "display_name": "",
         "first_seen": "?"},  # handle clashes with existing label
    ])
    monkeypatch.setattr("aipager.team.clear_pending_user", lambda uid: None)
    _stub_ask(monkeypatch, [
        "developer",        # role
        "alice2",           # new label
    ])
    new_team = edit_menu._edit_review_pending(team)
    assert 99 in new_team.users
    assert new_team.users[99].label == "alice2"


# ---- _edit_remove_user --------------------------------------------------

def test_edit_remove_user_empty_team(monkeypatch):
    """Empty team is unreachable through Team's API (at least one admin
    required by __post_init__). Skip but keep the placeholder so the
    test file enumerates all the documented states."""
    pytest.skip("Empty Team is rejected by Team.__post_init__")


def test_edit_remove_user_cancels(monkeypatch):
    team = _team(
        TeamUser(id=1, label="admin", role=Role.ADMIN),
        TeamUser(id=2, label="dev", role=Role.DEVELOPER),
    )
    _stub_ask(monkeypatch, [None])  # picked "Cancel"
    assert edit_menu._edit_remove_user(team) is None


def test_edit_remove_user_removes_dev(monkeypatch, stub_dump_team):
    team = _team(
        TeamUser(id=1, label="admin", role=Role.ADMIN),
        TeamUser(id=2, label="dev", role=Role.DEVELOPER),
    )
    _stub_ask(monkeypatch, [2, True])  # pick dev's id, confirm
    new_team = edit_menu._edit_remove_user(team)
    assert 2 not in new_team.users
    assert len(stub_dump_team) == 1


def test_edit_remove_user_confirms_no_keeps(monkeypatch):
    team = _team(
        TeamUser(id=1, label="admin", role=Role.ADMIN),
        TeamUser(id=2, label="dev", role=Role.DEVELOPER),
    )
    _stub_ask(monkeypatch, [2, False])
    assert edit_menu._edit_remove_user(team) is None


def test_edit_remove_user_refuses_to_remove_only_admin(monkeypatch):
    team = _team(TeamUser(id=1, label="admin", role=Role.ADMIN))
    _stub_ask(monkeypatch, [1])
    assert edit_menu._edit_remove_user(team) is None


# ---- _edit_change_role --------------------------------------------------

def test_edit_change_role_cancels(monkeypatch):
    team = _team(TeamUser(id=1, label="admin", role=Role.ADMIN))
    _stub_ask(monkeypatch, [None])
    assert edit_menu._edit_change_role(team) is None


def test_edit_change_role_same_role_warns_no_change(monkeypatch):
    team = _team(TeamUser(id=1, label="admin", role=Role.ADMIN))
    _stub_ask(monkeypatch, [1, "admin"])
    assert edit_menu._edit_change_role(team) is None


def test_edit_change_role_demote_only_admin_refused(monkeypatch):
    team = _team(TeamUser(id=1, label="admin", role=Role.ADMIN))
    _stub_ask(monkeypatch, [1, "developer"])
    assert edit_menu._edit_change_role(team) is None


def test_edit_change_role_promotes_developer(monkeypatch, stub_dump_team):
    team = _team(
        TeamUser(id=1, label="admin", role=Role.ADMIN),
        TeamUser(id=2, label="dev", role=Role.DEVELOPER),
    )
    _stub_ask(monkeypatch, [2, "admin"])
    new_team = edit_menu._edit_change_role(team)
    assert new_team.users[2].role == Role.ADMIN
    assert len(stub_dump_team) == 1


# ---- _edit_deny_tools ---------------------------------------------------

def test_edit_deny_tools_no_change_returns_none(monkeypatch):
    team = _team(deny_tools=("Bash",))
    _stub_ask(monkeypatch, [["Bash"], ""])  # same picks, no extras
    assert edit_menu._edit_deny_tools(team) is None


def test_edit_deny_tools_picks_two(monkeypatch, stub_dump_team):
    team = _team()
    _stub_ask(monkeypatch, [["Bash", "Edit"], ""])
    new_team = edit_menu._edit_deny_tools(team)
    assert "Bash" in new_team.rules.deny_tools
    assert "Edit" in new_team.rules.deny_tools
    assert len(stub_dump_team) == 1


def test_edit_deny_tools_includes_extras(monkeypatch, stub_dump_team):
    team = _team()
    _stub_ask(monkeypatch, [["Bash"], "CustomTool, OtherTool"])
    new_team = edit_menu._edit_deny_tools(team)
    assert "CustomTool" in new_team.rules.deny_tools
    assert "OtherTool" in new_team.rules.deny_tools


# ---- _edit_refresh_token ------------------------------------------------

def test_edit_refresh_token_happy(monkeypatch):
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("", "12345"))
    monkeypatch.setattr(edit_menu, "_verify_token",
                        lambda t: {"username": "bot"})
    monkeypatch.setattr(edit_menu, "_write_env_file",
                        lambda t, c: None)
    _stub_ask(monkeypatch, ["123456:abc-_def-_ghijklmnopqrstuvwxyz"])
    assert edit_menu._edit_refresh_token() is True


def test_edit_refresh_token_retries_empty(monkeypatch):
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("", "12345"))
    monkeypatch.setattr(edit_menu, "_verify_token",
                        lambda t: {"username": "bot"})
    monkeypatch.setattr(edit_menu, "_write_env_file",
                        lambda t, c: None)
    _stub_ask(monkeypatch, ["", "123456:abc-_def-_ghijklmnopqrstuvwxyz"])
    assert edit_menu._edit_refresh_token() is True


def test_edit_refresh_token_retries_invalid(monkeypatch):
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("", "12345"))
    calls = []
    def _verify(t):
        calls.append(t)
        return None if len(calls) == 1 else {"username": "bot"}
    monkeypatch.setattr(edit_menu, "_verify_token", _verify)
    monkeypatch.setattr(edit_menu, "_write_env_file",
                        lambda t, c: None)
    _stub_ask(monkeypatch, [
        "1:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "2:yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy",
    ])
    assert edit_menu._edit_refresh_token() is True
    assert len(calls) == 2


# ---- _edit_switch_to_personal -------------------------------------------

def test_edit_switch_to_personal_declined(monkeypatch):
    team = _team()
    _stub_ask(monkeypatch, [False])  # decline archive
    assert edit_menu._edit_switch_to_personal(team) is False


def test_edit_switch_to_personal_no_yaml_warns(monkeypatch):
    team = _team()
    _stub_ask(monkeypatch, [True])
    monkeypatch.setattr("aipager.team.archive_team", lambda p: None)
    assert edit_menu._edit_switch_to_personal(team) is False


def test_edit_switch_to_personal_archives_and_skips_chat_update(monkeypatch, tmp_path):
    team = _team()
    backup_path = tmp_path / "team.yaml.bak"
    backup_path.touch()
    monkeypatch.setattr("aipager.team.archive_team", lambda p: backup_path)
    _stub_ask(monkeypatch, [
        True,   # archive
        False,  # don't update chat id
    ])
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("tok", "12345"))
    assert edit_menu._edit_switch_to_personal(team) is True


def test_edit_switch_to_personal_archives_and_updates_chat(monkeypatch, tmp_path):
    team = _team()
    backup_path = tmp_path / "team.yaml.bak"
    backup_path.touch()
    monkeypatch.setattr("aipager.team.archive_team", lambda p: backup_path)
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("tok", "12345"))
    monkeypatch.setattr(edit_menu, "_verify_token",
                        lambda t: {"username": "bot"})
    monkeypatch.setattr(edit_menu, "_step_chat_id",
                        lambda *a, **k: 42)
    monkeypatch.setattr(edit_menu, "_write_env_file",
                        lambda t, c: None)
    _stub_ask(monkeypatch, [True, True])
    assert edit_menu._edit_switch_to_personal(team) is True


# ---- _edit_switch_to_team -----------------------------------------------

def test_edit_switch_to_team_no_token(monkeypatch):
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("", ""))
    assert edit_menu._edit_switch_to_team() is False


def test_edit_switch_to_team_declined(monkeypatch):
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("tok", "12345"))
    monkeypatch.setattr(edit_menu, "_verify_token", lambda t: {"username": "bot"})
    monkeypatch.setattr(edit_menu, "_show_team_warning_panel", lambda: None)
    _stub_ask(monkeypatch, [False])
    assert edit_menu._edit_switch_to_team() is False


def test_edit_switch_to_team_happy(monkeypatch):
    monkeypatch.setattr(edit_menu, "_read_env_file", lambda: ("tok", "12345"))
    monkeypatch.setattr(edit_menu, "_verify_token", lambda t: {"username": "bot"})
    monkeypatch.setattr(edit_menu, "_show_team_warning_panel", lambda: None)
    monkeypatch.setattr(edit_menu, "_step_chat_id", lambda *a, **k: -100)
    monkeypatch.setattr(edit_menu, "_step_team_setup", lambda *a, **k: None)
    monkeypatch.setattr(edit_menu, "_write_env_file", lambda t, c: None)
    _stub_ask(monkeypatch, [True])
    assert edit_menu._edit_switch_to_team() is True


# ---- _edit_flow ---------------------------------------------------------

def test_edit_flow_exit_returns_zero(monkeypatch):
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: _team())
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [])
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    _stub_ask(monkeypatch, ["exit"])
    assert edit_menu._edit_flow() == 0


def test_edit_flow_full_delegates_to_first_run(monkeypatch):
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: _team())
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [])
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    monkeypatch.setattr(edit_menu, "_first_run_flow", lambda: 7)
    _stub_ask(monkeypatch, ["full"])
    assert edit_menu._edit_flow() == 7


def test_edit_flow_keyboard_interrupt_returns_130(monkeypatch):
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: _team())
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [])
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    def _boom(prompt):
        raise KeyboardInterrupt
    monkeypatch.setattr(edit_menu, "_ask", _boom)
    assert edit_menu._edit_flow() == 130


def test_edit_flow_no_team_offers_to_team(monkeypatch):
    """Personal-mode (no team.yaml) menu has limited choices."""
    from aipager.team import TeamConfigError
    def _raise(p=None):
        raise TeamConfigError("none")
    monkeypatch.setattr("aipager.team.load_team", _raise)
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    _stub_ask(monkeypatch, ["exit"])
    assert edit_menu._edit_flow() == 0


def test_edit_flow_value_error_continues(monkeypatch):
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: _team())
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [])
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    def _boom(t):
        raise ValueError("bad")
    monkeypatch.setattr(edit_menu, "_edit_add_user", _boom)
    _stub_ask(monkeypatch, ["add", "exit"])
    assert edit_menu._edit_flow() == 0


def test_edit_flow_team_modify_calls_apply_hint(monkeypatch):
    team = _team()
    monkeypatch.setattr("aipager.team.load_team", lambda p=None: team)
    monkeypatch.setattr("aipager.team.list_pending_users", lambda: [])
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    monkeypatch.setattr(edit_menu, "_edit_add_user",
                        lambda t: team)  # any non-None = "added"
    hint_called = []
    monkeypatch.setattr(edit_menu, "_apply_team_change_hint",
                        lambda: hint_called.append(1))
    _stub_ask(monkeypatch, ["add", "exit"])
    edit_menu._edit_flow()
    assert hint_called == [1]
