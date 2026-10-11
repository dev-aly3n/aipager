"""The members' root folder in ``aipager.yaml`` and ``aipager config``:
parsed and refused like the wizard refuses it, re-emitted on every
rewrite, set from Edit a scope (then reloaded live), asked for when a DM
of a restricted role or a group is added, and shown in the current
config. A throwaway HOME and config file; the wizard's prompts are
stubbed."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from aipager import scope as _scope
from aipager.scope import Member, Scope, ScopeConfigError
from aipager.wizard import display, edit_menu, scope_flows, team_setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home" / "op"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def cfg(tmp_path, home, monkeypatch):
    import aipager.policy as _policy
    p = tmp_path / "aipager.yaml"
    monkeypatch.setattr(_scope, "CONFIG_PATH", p)
    monkeypatch.setattr(_policy, "POLICY_PATH", tmp_path / "policy.yaml")
    monkeypatch.setattr(_policy, "POLICY_D_DIR", tmp_path / "policy.d")
    _scope.dump_scopes([
        Scope(chat_id=1, kind="dm", label="owner DM",
              members=(Member(id=1, label="owner", role="owner"),)),
        Scope(chat_id=-100, kind="group", label="dev-team",
              members=(Member(id=11, label="ann", role="user"),)),
    ], "TOK", p)
    return p


def _stub(monkeypatch, module, answers):
    queue = iter(answers)

    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers") from None
    monkeypatch.setattr(module, "_ask", _ask)


def _scopes():
    return _scope.load_scopes(_scope.CONFIG_PATH)[0]


def _group():
    return next(s for s in _scopes() if s.chat_id == -100)


def _raw(cfg):
    return yaml.safe_load(cfg.read_text())


# ---- the file -----------------------------------------------------------------

def test_root_round_trips(cfg):
    _scope.dump_scopes([replace(s, root="/srv/team") if s.chat_id == -100 else s
                        for s in _scopes()], "TOK", cfg)
    assert _group().root == "/srv/team"
    assert [sd.get("root") for sd in _raw(cfg)["scopes"]] == [None, "/srv/team"]


def test_a_tilde_root_is_expanded(cfg, home):
    raw = _raw(cfg)
    raw["scopes"][1]["root"] = "~/teams/dev"
    cfg.write_text(yaml.safe_dump(raw))
    assert _group().root == str(home / "teams" / "dev")


@pytest.mark.parametrize("bad", ["/", "~", "rel/x", "~/x*", "~/.claude/x", "~/.config",
                                 "~/.ssh/x", 5, ["/srv"]])
def test_parser_refuses_wide_root(cfg, bad):
    raw = _raw(cfg)
    raw["scopes"][1]["root"] = bad
    cfg.write_text(yaml.safe_dump(raw))
    with pytest.raises(ScopeConfigError, match=r"scopes\[1\]\.root"):
        _scope.load_scopes(cfg)


def test_root_survives_rename(cfg, monkeypatch):
    _scope.dump_scopes([replace(s, root="/srv/team") if s.chat_id == -100 else s
                        for s in _scopes()], "TOK", cfg)
    _stub(monkeypatch, edit_menu, ["rename", "platform"])
    assert edit_menu._edit_scope(_group(), "TOK") is True
    assert (_group().label, _group().root) == ("platform", "/srv/team")


def test_a_rename_pins_the_default_root(cfg, monkeypatch, home):
    """A group's default root follows its label: renaming writes down the
    folder its members already use."""
    _stub(monkeypatch, edit_menu, ["rename", "platform"])
    assert edit_menu._edit_scope(_group(), "TOK") is True
    assert _group().root == str(home / "aipager-users" / "group-dev-team")


# ---- Edit a scope -> Set the members' root folder ---------------------------------

def test_edit_scope_sets_root_and_reloads(cfg, monkeypatch, home):
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    hints = []
    monkeypatch.setattr(edit_menu, "_apply_team_change_hint", lambda: hints.append("live"))
    monkeypatch.setattr(edit_menu, "_restart_hint", lambda: hints.append("restart"))
    monkeypatch.setattr(edit_menu, "_pick_scope", lambda scopes, prompt: next(
        s for s in scopes if s.chat_id == -100))
    _stub(monkeypatch, edit_menu, ["edit_scope", "root", "~/teams/dev", "exit"])
    edit_menu._edit_flow()
    assert _group().root == str(home / "teams" / "dev")
    assert hints == ["live"]
    assert not (home / "teams").exists()          # the wizard creates nothing


@pytest.mark.parametrize("bad", ["~", "/", "~/.claude/x", "rel", "~/a*"])
def test_wizard_asks_again_after_a_refused_root(cfg, monkeypatch, home, bad):
    warned = []
    monkeypatch.setattr(scope_flows, "friendly_warn", lambda m: warned.append(m))
    _stub(monkeypatch, edit_menu, ["root", bad, "/srv/team"])
    assert edit_menu._edit_scope(_group(), "TOK") is True
    assert _group().root == "/srv/team"
    assert len(warned) == 1 and warned[0].startswith("That folder can't be a root")


def test_wizard_confirms_a_shared_root(cfg, monkeypatch, home):
    owner_default = str(home / "aipager-users" / "owner")
    _stub(monkeypatch, edit_menu, ["root", owner_default, False, owner_default + "/x",
                                   True])
    assert edit_menu._edit_scope(_group(), "TOK") is True
    assert _group().root == owner_default + "/x"


def test_setting_the_same_root_again_changes_nothing(cfg, monkeypatch):
    _stub(monkeypatch, edit_menu, ["root", "/srv/team"])
    assert edit_menu._edit_scope(_group(), "TOK") is True
    before = cfg.read_bytes()
    _stub(monkeypatch, edit_menu, ["root", "/srv/team"])
    assert edit_menu._edit_scope(_group(), "TOK") is False
    assert cfg.read_bytes() == before


# ---- adding a scope ------------------------------------------------------------------

def test_add_dm_user_writes_root(cfg, monkeypatch, home):
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: {"id": 555, "label": "Sara"})
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    _stub(monkeypatch, scope_flows, [""])            # the default it offers
    assert scope_flows.add_dm_scope("TOK", "bot") is True
    sara = next(s for s in _scopes() if s.chat_id == 555)
    assert sara.root == str(home / "aipager-users" / "sara")


def test_add_dm_admin_is_not_asked(cfg, monkeypatch):
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: {"id": 555, "label": "Ada"})
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "admin")
    _stub(monkeypatch, scope_flows, [])
    assert scope_flows.add_dm_scope("TOK", "bot") is True
    assert next(s for s in _scopes() if s.chat_id == 555).root is None


def test_add_group_writes_root(cfg, monkeypatch, home):
    from aipager.wizard import first_run
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: -200)
    monkeypatch.setattr(scope_flows, "_operator_member", lambda: None)
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: {"id": 22, "label": "ben"})
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda: [])
    _stub(monkeypatch, scope_flows, ["ops", False, "/srv/ops"])
    assert scope_flows.add_group_scope("TOK", "bot") is True
    assert next(s for s in _scopes() if s.chat_id == -200).root == "/srv/ops"


# ---- the current config ----------------------------------------------------------------

def test_current_config_shows_the_root(cfg, monkeypatch, home):
    _scope.dump_scopes([replace(s, root="/srv/team") if s.chat_id == -100 else s
                        for s in _scopes()], "TOK", cfg)
    printed = []
    monkeypatch.setattr(display.console, "print", lambda *a, **k: printed.append(
        " ".join(str(x) for x in a)))
    monkeypatch.setattr(display, "_detect_daemon_running", lambda: None)
    display._show_current_config()
    body = "\n".join(printed)
    assert "root: [path]/srv/team[/path]" in body
    assert "root: [path]~/aipager-users/owner[/path] (default)" in body


def test_the_owner_never_gets_a_root_folder(cfg, home):
    """Nothing about a scope's root is created by loading or showing it."""
    _scopes()
    assert not Path(home / "aipager-users").exists()
