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
from aipager.wizard import display, edit_menu, root_flow, scope_flows, team_setup


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
    monkeypatch.setattr(root_flow, "friendly_warn", lambda m: warned.append(m))
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


# ---- a default root never moves (review iter 1, rev-iter1-002) ------------------------

def _sara_pair(cfg):
    """Two DMs whose labels sanitise alike: their defaults carry the chat
    id (``sara-101``, ``sara-102``)."""
    _scope.dump_scopes([
        Scope(chat_id=1, kind="dm", label="owner DM",
              members=(Member(id=1, label="owner", role="owner"),)),
        Scope(chat_id=101, kind="dm", label="Sara DM",
              members=(Member(id=101, label="Sara", role="user"),)),
        Scope(chat_id=102, kind="dm", label="sara DM",
              members=(Member(id=102, label="sara!", role="user"),)),
    ], "TOK", cfg)


def _effective(chat_id):
    from aipager import member_root
    scopes = _scopes()
    return member_root.effective_root(next(s for s in scopes if s.chat_id == chat_id),
                                      scopes)


def _root_of(chat_id):
    return next(s for s in _scopes() if s.chat_id == chat_id).root


def test_removing_a_scope_pins_the_default_it_would_move(cfg, home):
    from aipager.wizard import scope_io
    _sara_pair(cfg)
    assert scope_io.remove_scope(102) is True
    # Without the pin Sara's default would turn into ~/aipager-users/sara.
    assert _root_of(101) == str(home / "aipager-users" / "sara-101")
    assert _root_of(1) is None                      # nothing moved: no pin


def test_setting_another_scopes_root_pins_the_default_it_would_move(cfg, monkeypatch,
                                                                    home):
    _sara_pair(cfg)
    lee = next(s for s in _scopes() if s.chat_id == 102)
    _stub(monkeypatch, edit_menu, ["root", "/srv/lee"])
    assert edit_menu._edit_scope(lee, "TOK") is True
    assert _root_of(102) == "/srv/lee"
    assert _root_of(101) == str(home / "aipager-users" / "sara-101")


def test_a_root_on_another_scopes_default_pins_it(cfg, home):
    """Giving the group the folder Sara's default is pushes her default to
    ``sara-101``; the pin keeps her where she was (the operator confirmed
    sharing it in the wizard)."""
    from aipager.wizard import scope_io
    _scope.dump_scopes([*_scopes(), Scope(
        chat_id=101, kind="dm", label="Sara DM",
        members=(Member(id=101, label="Sara", role="user"),))], "TOK", cfg)
    sara = str(home / "aipager-users" / "sara")
    scope_io.commit_scope(replace(_group(), root=sara), "TOK")
    assert _root_of(101) == sara


@pytest.mark.parametrize("how", ["commit", "replace", "add"])
def test_every_wizard_write_pins_moved_defaults(cfg, home, how):
    from aipager.wizard import scope_io
    _sara_pair(cfg)
    if how == "commit":       # a DM member renamed: its default would follow the label
        lee = next(s for s in _scopes() if s.chat_id == 102)
        scope_io.commit_scope(replace(lee, members=(replace(lee.members[0],
                                                            label="lee"),)), "TOK")
    elif how == "replace":    # a whole-list write without the second Sara
        scope_io.replace_scopes([s for s in _scopes() if s.chat_id != 102], "TOK")
    else:                     # a new DM named like the owner's: only the old one is pinned
        assert scope_io.add_new_scope(Scope(
            chat_id=5, kind="dm", label="other owner DM",
            members=(Member(id=5, label="Owner", role="user"),)), "TOK") is True
        assert _root_of(1) == str(home / "aipager-users" / "owner")
        assert _root_of(5) is None
        assert _effective(5) == str(home / "aipager-users" / "owner-5")
        return
    assert _root_of(101) == str(home / "aipager-users" / "sara-101")


# ---- a root where the system runs programs (review iter 1 nit) -------------------------

@pytest.mark.parametrize("spot", ["~/bin", "~/.local/bin/tools", "~/.config/systemd/user",
                                  "~/.config/autostart"])
def test_wizard_confirms_a_code_loading_root(cfg, monkeypatch, home, spot):
    _stub(monkeypatch, edit_menu, ["root", spot, False, "/srv/team"])
    assert edit_menu._edit_scope(_group(), "TOK") is True
    assert _group().root == "/srv/team"
    _stub(monkeypatch, edit_menu, ["root", spot, True])
    assert edit_menu._edit_scope(_group(), "TOK") is True
    assert _group().root == str(home / spot[2:])


def test_a_plain_root_is_not_asked_about_code(cfg, monkeypatch, home):
    # One answer only: a confirm would run out of canned answers.
    _stub(monkeypatch, edit_menu, ["root", "~/binaries"])
    assert edit_menu._edit_scope(_group(), "TOK") is True
    assert _group().root == str(home / "binaries")
    assert root_flow.code_loading_folder(str(home / "bin" / "x")) == "~/bin"
    assert root_flow.code_loading_folder(str(home / "bins")) is None
