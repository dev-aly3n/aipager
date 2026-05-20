"""F4: edit menu over the scope list (v2).

We stub questionary prompts (``_ask``) and redirect ``aipager.yaml`` /
``policy.yaml`` into tmp_path so the scope mutations exercise the real
``scope_io`` round-trip without touching the user's config.
"""

from __future__ import annotations

import pytest

from aipager import scope as _scope
from aipager.scope import Member, Scope
from aipager.wizard import edit_menu


def _stub_ask(monkeypatch, answers):
    queue = iter(answers)

    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers")
    monkeypatch.setattr(edit_menu, "_ask", _ask)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A committed two-scope config redirected into tmp_path."""
    import aipager.policy as _policy
    p = tmp_path / "aipager.yaml"
    monkeypatch.setattr(_scope, "CONFIG_PATH", p)
    monkeypatch.setattr(_policy, "POLICY_PATH", tmp_path / "policy.yaml")
    monkeypatch.setattr(_policy, "POLICY_D_DIR", tmp_path / "policy.d")
    scopes = [
        Scope(chat_id=1, kind="dm", label="owner DM",
              members=(Member(id=1, label="owner", role="owner"),)),
        Scope(chat_id=-100, kind="group", label="dev-team",
              members=(Member(id=11, label="ann", role="user"),
                       Member(id=22, label="ben", role="user"))),
    ]
    _scope.dump_scopes(scopes, "TOK", p)
    return p


def _scopes():
    return _scope.load_scopes(_scope.CONFIG_PATH)[0]


# ---- _edit_scope ---------------------------------------------------------

def test_edit_scope_rename(cfg, monkeypatch):
    grp = next(s for s in _scopes() if s.chat_id == -100)
    _stub_ask(monkeypatch, ["rename", "platform"])
    assert edit_menu._edit_scope(grp, "TOK") is True
    grp2 = next(s for s in _scopes() if s.chat_id == -100)
    assert grp2.label == "platform"


def test_edit_scope_remove(cfg, monkeypatch):
    grp = next(s for s in _scopes() if s.chat_id == -100)
    _stub_ask(monkeypatch, ["remove", True])
    assert edit_menu._edit_scope(grp, "TOK") is True
    assert [s.chat_id for s in _scopes()] == [1]


def test_edit_scope_remove_last_refused(cfg, monkeypatch):
    # Drop the group first so only the DM remains.
    from aipager.wizard.scope_io import remove_scope
    remove_scope(-100)
    dm = next(s for s in _scopes() if s.chat_id == 1)
    _stub_ask(monkeypatch, ["remove", True])
    assert edit_menu._edit_scope(dm, "TOK") is False
    assert [s.chat_id for s in _scopes()] == [1]


def test_edit_scope_deny_tools(cfg, monkeypatch):
    grp = next(s for s in _scopes() if s.chat_id == -100)
    _stub_ask(monkeypatch, ["deny", ["Bash"], ""])
    assert edit_menu._edit_scope(grp, "TOK") is True
    grp2 = next(s for s in _scopes() if s.chat_id == -100)
    assert grp2.deny_tools == ("Bash",)


# ---- _edit_member --------------------------------------------------------

def test_edit_member_set_role_offers_custom(cfg, monkeypatch):
    grp = next(s for s in _scopes() if s.chat_id == -100)
    seen = {}

    def _fake_pick(prompt, **k):
        seen["default"] = k.get("default")
        return "reviewer"
    monkeypatch.setattr(edit_menu, "_pick_role", _fake_pick)
    _stub_ask(monkeypatch, [11, "role"])  # pick ann, then "set role"
    assert edit_menu._edit_member(grp, "TOK") is True
    members = next(s for s in _scopes() if s.chat_id == -100).members
    ann = next(m for m in members if m.id == 11)
    assert ann.role == "reviewer"
    assert seen["default"] == "user"


def test_edit_member_remove(cfg, monkeypatch):
    grp = next(s for s in _scopes() if s.chat_id == -100)
    _stub_ask(monkeypatch, [11, "remove", True])
    assert edit_menu._edit_member(grp, "TOK") is True
    grp2 = next(s for s in _scopes() if s.chat_id == -100)
    assert {m.id for m in grp2.members} == {22}


def test_edit_member_remove_from_dm_refused(cfg, monkeypatch):
    dm = next(s for s in _scopes() if s.chat_id == 1)
    _stub_ask(monkeypatch, [1, "remove"])
    assert edit_menu._edit_member(dm, "TOK") is False
    assert len(next(s for s in _scopes() if s.chat_id == 1).members) == 1


def test_edit_member_remove_last_group_member_refused(cfg, monkeypatch):
    # Single-member group.
    from aipager.wizard.scope_io import commit_scope
    commit_scope(
        Scope(chat_id=-200, kind="group", label="solo",
              members=(Member(id=99, label="zoe", role="user"),)),
        "TOK",
    )
    solo = next(s for s in _scopes() if s.chat_id == -200)
    _stub_ask(monkeypatch, [99, "remove"])
    assert edit_menu._edit_member(solo, "TOK") is False


# ---- _view_policy (read-only — R15) --------------------------------------

def test_view_policy_never_writes(cfg, capsys):
    import hashlib

    import aipager.policy as _policy
    # Hand-write a policy.yaml with a custom role.
    _policy.POLICY_PATH.write_text(
        "roles:\n  reviewer:\n    allow_tools: [Read, Grep]\n",
        encoding="utf-8",
    )
    before = hashlib.sha256(_policy.POLICY_PATH.read_bytes()).hexdigest()
    edit_menu._view_policy()
    after = hashlib.sha256(_policy.POLICY_PATH.read_bytes()).hexdigest()
    assert before == after  # byte-identical — never written
    out = capsys.readouterr().out
    assert "reviewer" in out
    assert "never writes" in out


def test_view_policy_absent_shows_defaults(cfg, capsys):
    edit_menu._view_policy()
    out = capsys.readouterr().out
    assert "built-in defaults" in out
    assert "owner" in out and "read_only" in out


# ---- _refresh_token ------------------------------------------------------

def test_refresh_token_rewrites_token_keeps_scopes(cfg, monkeypatch):
    monkeypatch.setattr(edit_menu, "_verify_token",
                        lambda t: {"username": "newbot"})
    _stub_ask(monkeypatch, ["999999:newtokenABCDEFGHIJKLMNOPQRS"])
    scopes = _scopes()
    new = edit_menu._refresh_token(scopes)
    assert new == "999999:newtokenABCDEFGHIJKLMNOPQRS"
    again, token = _scope.load_scopes(_scope.CONFIG_PATH)
    assert token == new
    assert {s.chat_id for s in again} == {1, -100}


# ---- _edit_flow routing --------------------------------------------------

def test_edit_flow_exit(cfg, monkeypatch):
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    _stub_ask(monkeypatch, ["exit"])
    assert edit_menu._edit_flow() == 0


def test_edit_flow_view_policy_then_exit(cfg, monkeypatch):
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    calls = []
    monkeypatch.setattr(edit_menu, "_view_policy",
                        lambda: calls.append("viewed"))
    _stub_ask(monkeypatch, ["view_policy", "exit"])
    assert edit_menu._edit_flow() == 0
    assert calls == ["viewed"]


def test_edit_flow_add_dm_triggers_restart_hint(cfg, monkeypatch):
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    monkeypatch.setattr(edit_menu, "_bot_username", lambda t: "bot")
    monkeypatch.setattr(edit_menu, "add_dm_scope", lambda t, b: True)
    hints = []
    monkeypatch.setattr(edit_menu, "_restart_hint",
                        lambda: hints.append("restart"))
    _stub_ask(monkeypatch, ["add_dm", "exit"])
    edit_menu._edit_flow()
    assert hints == ["restart"]


def test_menu_choices_malformed_is_limited():
    assert [c.value for c in edit_menu._menu_choices(True)] == [
        "refresh_token", "exit",
    ]


def test_menu_choices_normal_full_menu():
    values = [c.value for c in edit_menu._menu_choices(False)]
    assert "add_group" in values and "view_policy" in values


def test_edit_flow_malformed_config_exits_cleanly(tmp_path, monkeypatch):
    p = tmp_path / "aipager.yaml"
    p.write_text("schema_version: 99\n", encoding="utf-8")  # malformed
    monkeypatch.setattr(_scope, "CONFIG_PATH", p)
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    _stub_ask(monkeypatch, ["exit"])
    assert edit_menu._edit_flow() == 0
