"""``aipager config`` sets, offers and shows the members' root folder
(design.md success criteria 1-3; entrypoints.md "CLI commands").

The prompts are answered at questionary's boundary: every
``questionary.<kind>(message, ...)`` becomes a recorded fake whose
``ask()`` answers by what the prompt SAYS (its message), never by its
position in the flow. The Telegram-polling identity capture is the only
other stub (it is network). ``aipager.yaml`` lives in tmp_path (root
conftest) and HOME is a throwaway folder."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest
import questionary

from aipager import member_root
from aipager import scope as _scope
from aipager.scope import Member, Scope

kit = sys.modules["_userroot_kit"]


class Prompts:
    """Answers questionary prompts by their message. ``rules`` is a list
    of ``(kind, substring, answer)``; *answer* may be a list (consumed one
    per matching prompt) or a callable ``(question) -> value``. Anything
    unmatched gets its default (select: its default or first choice)."""

    def __init__(self):
        self.rules: list[list] = []
        self.asked: list[SimpleNamespace] = []

    def on(self, kind, substring, answer):
        self.rules.append([kind, substring.lower(), answer])
        return self

    def _answer(self, q):
        for kind, sub, ans in self.rules:
            if kind == q.kind and sub in (q.message or "").lower():
                if isinstance(ans, list):
                    if not ans:
                        raise KeyboardInterrupt(f"no more answers for {q.message!r}")
                    return ans.pop(0)
                return ans(q) if callable(ans) else ans
        if q.kind == "select":
            if q.kwargs.get("default") is not None:
                d = q.kwargs["default"]
                return getattr(d, "value", d)
            ch = q.choices[0]
            return getattr(ch, "value", ch)
        if q.kind == "checkbox":
            return []
        if q.kind == "confirm":
            return confirm_default(q)
        return q.kwargs.get("default", "")

    def factory(self, kind):
        def _make(message="", *a, **kw):
            q = SimpleNamespace(kind=kind, message=message, kwargs=kw, args=a,
                                choices=list(kw.get("choices") or (a[0] if a else []) or [])
                                if kind != "confirm" else [])
            q.ask = lambda *x, **y: self._record(q)
            q.unsafe_ask = q.ask
            return q
        return _make

    def _record(self, q):
        self.asked.append(q)
        return self._answer(q)

    def of(self, substring):
        return [q for q in self.asked if substring.lower() in (q.message or "").lower()]


def confirm_default(q):
    """What pressing Enter answers: questionary.confirm defaults to True."""
    if "default" in q.kwargs:
        return q.kwargs["default"]
    return q.args[0] if q.args else True


def pick_title(substring):
    def _pick(q):
        for c in q.choices:
            if substring.lower() in str(getattr(c, "title", c)).lower():
                return getattr(c, "value", c)
        raise AssertionError(f"no choice {substring!r} in {q.message!r}")
    return _pick


@pytest.fixture
def prompts(monkeypatch):
    p = Prompts()
    for kind in ("text", "select", "confirm", "checkbox", "password",
                 "autocomplete", "path", "rawselect"):
        monkeypatch.setattr(questionary, kind, p.factory(kind))
    return p


@pytest.fixture
def cfg(tmp_path, home):
    """A committed config: the owner's DM, Sara's DM (user, no root) and a
    group with a user, written to the (redirected) aipager.yaml."""
    p = _scope.CONFIG_PATH
    scopes = [
        Scope(chat_id=1, kind="dm", label="alice DM",
              members=(Member(id=1, label="alice", role="owner"),)),
        Scope(chat_id=3, kind="dm", label="Sara DM",
              members=(Member(id=3, label="Sara", role="user"),)),
        Scope(chat_id=-100, kind="group", label="team",
              members=(Member(id=5, label="bob", role="user"),)),
    ]
    _scope.dump_scopes(scopes, "TOK", p)
    return p


def _scopes():
    return _scope.load_scopes(_scope.CONFIG_PATH)[0]


def _get(chat):
    return next(s for s in _scopes() if s.chat_id == chat)


ROOT_Q = "root folder"


def _edit(prompts, chat, answers):
    from aipager.wizard import edit_menu
    prompts.on("select", "", lambda q: _choose_root(q))
    prompts.on("text", ROOT_Q, list(answers))
    return edit_menu._edit_scope(_get(chat), "TOK")


def _choose_root(q):
    for c in q.choices:
        if "root folder" in str(getattr(c, "title", c)).lower():
            return getattr(c, "value", c)
    raise AssertionError(f"no root choice in {q.message!r}")


# ---- criterion 1: set, show, keep, reload ---------------------------------------

def test_edit_scope_menu_offers_the_root_folder(cfg, prompts):
    _edit(prompts, -100, [str(cfg.parent / "x")])
    assert any("Set the members' root folder" == str(getattr(c, "title", c))
               for q in prompts.asked if q.kind == "select" for c in q.choices)


def test_edit_scope_sets_root(cfg, prompts, home):
    _edit(prompts, -100, [str(home / "work" / "team")])
    assert _get(-100).root == str(home / "work" / "team")


def test_edit_scope_sets_root_and_reloads(cfg, prompts, home, monkeypatch):
    from aipager.wizard import edit_menu
    hints = []
    monkeypatch.setattr(edit_menu, "_apply_team_change_hint", lambda: hints.append("live"))
    monkeypatch.setattr(edit_menu, "_restart_hint", lambda: hints.append("restart"))
    monkeypatch.setattr(edit_menu, "_show_current_config", lambda: None)
    prompts.on("select", "what would you like", ["edit_scope", "exit"])
    prompts.on("select", "edit which scope", pick_title("team"))
    prompts.on("select", "", _choose_root)
    prompts.on("text", ROOT_Q, [str(home / "work" / "team")])
    edit_menu._edit_flow()
    assert hints == ["live"]


def test_edit_scope_root_prompt_is_prefilled_with_the_default(cfg, prompts, home):
    _edit(prompts, 3, [str(home / "s")])
    assert prompts.of(ROOT_Q)[0].kwargs.get("default") in (
        str(home / "aipager-users" / "sara"), "~/aipager-users/sara")


def test_edit_scope_root_prompt_is_prefilled_with_the_current_root(cfg, prompts, home):
    _edit(prompts, 3, [str(home / "s1")])
    prompts.rules.clear()
    _edit(prompts, 3, [str(home / "s2")])
    assert prompts.of(ROOT_Q)[-1].kwargs.get("default") in (str(home / "s1"), "~/s1")


def test_tilde_answer_is_expanded(cfg, prompts, home):
    _edit(prompts, 3, ["~/work/sara"])
    assert _get(3).root == str(home / "work" / "sara")


def test_root_survives_rename(cfg, prompts, home):
    from aipager.wizard import edit_menu
    _edit(prompts, -100, [str(home / "work" / "team")])
    prompts.rules.clear()
    prompts.on("select", "", "rename")
    prompts.on("text", "", "platform")
    edit_menu._edit_scope(_get(-100), "TOK")
    assert _get(-100).root == str(home / "work" / "team")


def test_root_survives_a_member_role_edit(cfg, prompts, home, monkeypatch):
    from aipager.wizard import edit_menu
    _edit(prompts, -100, [str(home / "work" / "team")])
    prompts.rules.clear()
    monkeypatch.setattr(edit_menu, "_pick_role", lambda *a, **k: "read_only")
    prompts.on("select", "", [5, "role"])
    edit_menu._edit_member(_get(-100), "TOK")
    assert _get(-100).root == str(home / "work" / "team")


def test_other_scopes_are_untouched_by_a_root_edit(cfg, prompts, home):
    _edit(prompts, -100, [str(home / "work" / "team")])
    assert _get(3).root is None


def _config_view(capsys):
    from aipager.wizard import display
    capsys.readouterr()  # drop what the edit printed
    display._show_current_config()
    return capsys.readouterr().out


def _line_of(out, label):
    return next(line for line in out.splitlines() if label in line and "root:" in line)


def test_config_view_shows_set_root(cfg, prompts, home, capsys):
    _edit(prompts, -100, [str(home / "work" / "team")])
    assert "root: ~/work/team" in _line_of(_config_view(capsys), "team")


def test_config_view_set_root_is_not_marked_default(cfg, prompts, home, capsys):
    _edit(prompts, -100, [str(home / "work" / "team")])
    assert "(default)" not in _line_of(_config_view(capsys), "team")


def test_config_view_marks_unset_root_default(cfg, capsys):
    assert "root: ~/aipager-users/sara (default)" in _line_of(_config_view(capsys), "Sara")


def test_config_view_shows_group_default(cfg, capsys):
    assert "root: ~/aipager-users/group-team (default)" in _line_of(_config_view(capsys), "team")


# ---- criterion 2: the wizard refuses bad roots, and re-asks -------------------------

def _bad(home):
    return {
        "slash": "/",
        "home": "~",
        "above-home": str(home.parent),
        "relative": "work/sara",
        "glob": "~/w/*",
        "claude-inside": "~/.claude/x",
        "config-aipager": "~/.config/aipager",
        "config-holds-protected": "~/.config",
        "ssh-inside": "~/.ssh/x",
    }


@pytest.mark.parametrize("case", ["slash", "home", "above-home", "relative", "glob",
                                  "claude-inside", "config-aipager",
                                  "config-holds-protected", "ssh-inside"])
def test_wizard_refuses_root(cfg, prompts, home, case):
    _edit(prompts, 3, [_bad(home)[case], "~/fine"])
    assert _get(3).root == str(home / "fine")


@pytest.mark.parametrize("case", ["slash", "home", "ssh-inside"])
def test_wizard_asks_again_after_a_refusal(cfg, prompts, home, case):
    _edit(prompts, 3, [_bad(home)[case], "~/fine"])
    assert len(prompts.of(ROOT_Q)) == 2


def test_wizard_accepts_a_nonexistent_folder_under_home(cfg, prompts, home):
    _edit(prompts, 3, ["~/not/yet/there"])
    assert _get(3).root == str(home / "not" / "yet" / "there")


def test_wizard_never_creates_the_root(cfg, prompts, home):
    _edit(prompts, 3, ["~/not/yet/there"])
    assert not (home / "not").exists()


def test_wizard_asks_before_sharing_another_scopes_root(cfg, prompts, home):
    _edit(prompts, -100, ["~/shared"])
    prompts.rules.clear()
    prompts.on("confirm", "", False)
    _edit(prompts, 3, ["~/shared", "~/own"])
    assert prompts.of("") and any(q.kind == "confirm" for q in prompts.asked)


def test_declining_a_shared_root_keeps_it_unshared(cfg, prompts, home):
    _edit(prompts, -100, ["~/shared"])
    prompts.rules.clear()
    prompts.asked.clear()
    prompts.on("confirm", "", False)
    _edit(prompts, 3, ["~/shared", "~/own"])
    assert _get(3).root == str(home / "own")


def test_accepting_a_shared_root_writes_it(cfg, prompts, home):
    _edit(prompts, -100, ["~/shared"])
    prompts.rules.clear()
    prompts.on("confirm", "", True)
    _edit(prompts, 3, ["~/shared"])
    assert _get(3).root == str(home / "shared")


def test_a_nested_root_also_asks(cfg, prompts, home):
    _edit(prompts, -100, ["~/shared"])
    prompts.rules.clear()
    prompts.asked.clear()
    prompts.on("confirm", "", False)
    _edit(prompts, 3, ["~/shared/sara", "~/own"])
    assert any(q.kind == "confirm" for q in prompts.asked)


def test_a_sibling_with_a_common_prefix_does_not_ask(cfg, prompts, home):
    # ~/shared2 neither equals nor nests with ~/shared.
    _edit(prompts, -100, ["~/shared"])
    prompts.rules.clear()
    prompts.asked.clear()
    prompts.on("confirm", "", False)
    _edit(prompts, 3, ["~/shared2"])
    assert _get(3).root == str(home / "shared2")


# ---- criterion 3: adding a DM for a user / a group asks for the root -----------------

@pytest.fixture
def identity(monkeypatch):
    """The Telegram-polling identity capture, stubbed (network)."""
    from aipager.wizard import first_run, team_setup
    people = []

    def _capture(idx, *, existing_ids, existing_labels, token=""):
        return people.pop(0) if people else None
    monkeypatch.setattr(team_setup, "_capture_user_identity", _capture)
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda *a, **k: [])
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: -300)
    return people


def test_add_dm_user_asks_for_the_root(cfg, prompts, identity):
    from aipager.wizard import scope_flows
    identity.append({"id": 77, "label": "nina"})
    prompts.on("select", "role for", "user")
    scope_flows.add_dm_scope("TOK", "bot")
    assert len(prompts.of(ROOT_Q)) == 1


def test_add_dm_user_root_prompt_prefilled_with_default(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 77, "label": "nina"})
    prompts.on("select", "role for", "user")
    scope_flows.add_dm_scope("TOK", "bot")
    assert prompts.of(ROOT_Q)[0].kwargs.get("default") in (
        str(home / "aipager-users" / "nina"), "~/aipager-users/nina")


def test_add_dm_user_writes_root(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 77, "label": "nina"})
    prompts.on("select", "role for", "user")
    scope_flows.add_dm_scope("TOK", "bot")
    assert _get(77).root == str(home / "aipager-users" / "nina")


def test_add_dm_user_writes_a_chosen_root(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 77, "label": "nina"})
    prompts.on("select", "role for", "user")
    prompts.on("text", ROOT_Q, "~/people/nina")
    scope_flows.add_dm_scope("TOK", "bot")
    assert _get(77).root == str(home / "people" / "nina")


def test_add_dm_read_only_writes_root(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 78, "label": "olga"})
    prompts.on("select", "role for", "read_only")
    scope_flows.add_dm_scope("TOK", "bot")
    assert _get(78).root == str(home / "aipager-users" / "olga")


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_add_dm_for_a_manager_asks_no_root(cfg, prompts, identity, role):
    from aipager.wizard import scope_flows
    identity.append({"id": 79, "label": "pat"})
    prompts.on("select", "role for", role)
    scope_flows.add_dm_scope("TOK", "bot")
    assert prompts.of(ROOT_Q) == []


def test_add_group_writes_root(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 90, "label": "quin"})
    prompts.on("text", "label for this group", "Crew")
    prompts.on("confirm", "add yourself", False)
    prompts.on("confirm", "add another", False)
    prompts.on("select", "role for", "user")
    scope_flows.add_group_scope("TOK", "bot")
    assert _get(-300).root == str(home / "aipager-users" / "group-crew")


def test_add_group_root_prompt_prefilled_with_default(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 90, "label": "quin"})
    prompts.on("text", "label for this group", "Crew")
    prompts.on("confirm", "add yourself", False)
    prompts.on("confirm", "add another", False)
    prompts.on("select", "role for", "user")
    scope_flows.add_group_scope("TOK", "bot")
    assert prompts.of(ROOT_Q)[0].kwargs.get("default") in (
        str(home / "aipager-users" / "group-crew"), "~/aipager-users/group-crew")


def test_add_group_refused_root_is_asked_again(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 90, "label": "quin"})
    prompts.on("text", "label for this group", "Crew")
    prompts.on("confirm", "add yourself", False)
    prompts.on("confirm", "add another", False)
    prompts.on("select", "role for", "user")
    prompts.on("text", ROOT_Q, ["/", "~/crew"])
    scope_flows.add_group_scope("TOK", "bot")
    assert _get(-300).root == str(home / "crew")


def test_adding_a_scope_never_moves_an_existing_default(cfg, prompts, identity, home):
    """Sara's default (unset root) stays where it was after a second DM
    whose label sanitises the same is added."""
    from aipager.wizard import scope_flows
    before = member_root.effective_root(_get(3), _scopes())
    identity.append({"id": 77, "label": "SARA"})
    prompts.on("select", "role for", "user")
    scope_flows.add_dm_scope("TOK", "bot")
    assert member_root.effective_root(_get(3), _scopes()) == before


def test_added_dm_default_differs_from_an_existing_colliding_one(cfg, prompts, identity):
    from aipager.wizard import scope_flows
    identity.append({"id": 77, "label": "SARA"})
    prompts.on("select", "role for", "user")
    scope_flows.add_dm_scope("TOK", "bot")
    assert member_root.effective_root(_get(77), _scopes()) != \
        member_root.effective_root(_get(3), _scopes())



# ---- iteration 2: a root the system runs programs from asks first (default No) ----------

CODE_FOLDERS = {
    "bin": "~/bin",
    "bin-inside": "~/bin/tools",
    "local-bin": "~/.local/bin",
    "local-bin-inside": "~/.local/bin/x",
    "systemd": "~/.config/systemd",
    "systemd-user": "~/.config/systemd/user",
    "autostart": "~/.config/autostart",
    "autostart-inside": "~/.config/autostart/a",
    "bin-trailing-slash": "~/bin/",
}


def _confirms(prompts):
    return [q for q in prompts.asked if q.kind == "confirm"]


@pytest.mark.parametrize("case", list(CODE_FOLDERS))
def test_code_loading_root_asks_to_confirm(cfg, prompts, home, case):
    _edit(prompts, 3, [CODE_FOLDERS[case], "~/fine"])
    assert len(_confirms(prompts)) >= 1


@pytest.mark.parametrize("case", ["bin", "local-bin", "systemd", "autostart"])
def test_code_loading_root_confirm_defaults_to_no(cfg, prompts, home, case):
    _edit(prompts, 3, [CODE_FOLDERS[case], "~/fine"])
    assert confirm_default(_confirms(prompts)[0]) is False


@pytest.mark.parametrize("case", list(CODE_FOLDERS))
def test_code_loading_root_not_written_when_enter_is_pressed(cfg, prompts, home, case):
    """Enter (the default) declines; the folder is asked again."""
    _edit(prompts, 3, [CODE_FOLDERS[case], "~/fine"])
    assert _get(3).root == str(home / "fine")


@pytest.mark.parametrize("case", ["bin", "systemd-user"])
def test_code_loading_root_declined_asks_again(cfg, prompts, home, case):
    _edit(prompts, 3, [CODE_FOLDERS[case], "~/fine"])
    assert len([q for q in prompts.of(ROOT_Q) if q.kind == "text"]) == 2


@pytest.mark.parametrize("case", ["bin", "local-bin", "systemd", "autostart"])
def test_code_loading_root_written_when_confirmed(cfg, prompts, home, case):
    prompts.on("confirm", "", True)
    _edit(prompts, 3, [CODE_FOLDERS[case]])
    want = os.path.normpath(os.path.expanduser(CODE_FOLDERS[case]))
    assert _get(3).root == want


LOOKALIKES = {
    "binx": "~/binx",
    "bins": "~/bins/x",
    "local-binx": "~/.local/binx",
    "systemdx": "~/.config/systemdx",
    "autostart2": "~/.config/autostart2",
    "projects-bin": "~/projects/bin",
    "config-other": "~/.config/myapp",
}


@pytest.mark.parametrize("case", list(LOOKALIKES))
def test_lookalike_of_a_code_folder_does_not_ask(cfg, prompts, home, case):
    _edit(prompts, 3, [LOOKALIKES[case], "~/fine"])
    assert _confirms(prompts) == []


@pytest.mark.parametrize("case", ["binx", "projects-bin"])
def test_lookalike_of_a_code_folder_is_written(cfg, prompts, home, case):
    _edit(prompts, 3, [LOOKALIKES[case]])
    assert _get(3).root == os.path.expanduser(LOOKALIKES[case])


def test_absolute_spelling_of_bin_also_asks(cfg, prompts, home):
    _edit(prompts, 3, [str(home / "bin"), "~/fine"])
    assert len(_confirms(prompts)) >= 1


def test_symlink_into_bin_also_asks(cfg, prompts, home):
    (home / "bin").mkdir()
    os.symlink(home / "bin", home / "tools")
    _edit(prompts, 3, ["~/tools", "~/fine"])
    assert len(_confirms(prompts)) >= 1


def test_adding_a_dm_with_a_code_folder_root_asks(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 77, "label": "nina"})
    prompts.on("select", "role for", "user")
    prompts.on("text", ROOT_Q, ["~/.local/bin", "~/people/nina"])
    scope_flows.add_dm_scope("TOK", "bot")
    assert _get(77).root == str(home / "people" / "nina")


def test_adding_a_group_with_a_code_folder_root_asks(cfg, prompts, identity, home):
    from aipager.wizard import scope_flows
    identity.append({"id": 90, "label": "quin"})
    prompts.on("text", "label for this group", "Crew")
    prompts.on("confirm", "add yourself", False)
    prompts.on("confirm", "add another", False)
    prompts.on("select", "role for", "user")
    prompts.on("text", ROOT_Q, ["~/.config/autostart", "~/crew"])
    scope_flows.add_group_scope("TOK", "bot")
    assert _get(-300).root == str(home / "crew")


def test_parser_still_loads_a_confirmed_code_folder_root(tmp_path, home):
    """The question is the wizard's; the file loads (the parser's refusal
    list in entrypoints.md does not name these folders)."""
    p = tmp_path / "a.yaml"
    _scope.dump_scopes([Scope(chat_id=3, kind="dm", label="Sara DM",
                              members=(Member(id=3, label="Sara", role="user"),),
                              root=str(home / "bin"))], "TOK", p)
    assert _scope.load_scopes(p)[0][0].root == str(home / "bin")


# ---- iteration 2: no wizard write ever moves a default root ------------------------------

@pytest.fixture
def cfg_collide(tmp_path, home):
    """Sara (3) and sara! (4) sanitise to the same name; a group; the owner."""
    p = _scope.CONFIG_PATH
    scopes = [
        Scope(chat_id=1, kind="dm", label="alice DM",
              members=(Member(id=1, label="alice", role="owner"),)),
        Scope(chat_id=3, kind="dm", label="Sara DM",
              members=(Member(id=3, label="Sara", role="user"),)),
        Scope(chat_id=4, kind="dm", label="sara! DM",
              members=(Member(id=4, label="sara!", role="user"),)),
        Scope(chat_id=-100, kind="group", label="team",
              members=(Member(id=5, label="bob", role="user"),)),
    ]
    _scope.dump_scopes(scopes, "TOK", p)
    return p


def _roots():
    sl = _scopes()
    return {s.chat_id: member_root.effective_root(s, sl) for s in sl}


def test_collision_precondition(cfg_collide):
    r = _roots()
    assert r[3] != r[4]


def test_removing_the_colliding_scope_keeps_saras_root(cfg_collide, prompts):
    from aipager.wizard import edit_menu
    before = _roots()[3]
    prompts.on("select", "", "remove")
    prompts.on("confirm", "", True)
    edit_menu._edit_scope(_get(4), "TOK")
    assert _roots()[3] == before


def test_removing_the_colliding_scope_really_removed_it(cfg_collide, prompts):
    from aipager.wizard import edit_menu
    prompts.on("select", "", "remove")
    prompts.on("confirm", "", True)
    edit_menu._edit_scope(_get(4), "TOK")
    assert 4 not in _roots()


def test_removing_the_first_colliding_scope_keeps_the_others_root(cfg_collide, prompts):
    from aipager.wizard import edit_menu
    before = _roots()[4]
    prompts.on("select", "", "remove")
    prompts.on("confirm", "", True)
    edit_menu._edit_scope(_get(3), "TOK")
    assert _roots()[4] == before


def test_setting_the_colliding_scopes_root_keeps_saras_root(cfg_collide, prompts, home):
    before = _roots()[3]
    _edit(prompts, 4, ["~/own"])
    assert _roots()[3] == before


def test_setting_a_root_onto_saras_default_path_keeps_saras_root(cfg_collide, prompts, home):
    """The group takes Sara's current default path as its root (confirmed
    share): Sara's root does not move away from it."""
    before = _roots()[3]
    prompts.on("confirm", "", True)
    _edit(prompts, -100, [before])
    assert _roots()[3] == before


@pytest.mark.parametrize("chat", [3, 4, -100])
def test_setting_an_unrelated_root_moves_no_default(cfg_collide, prompts, home, chat):
    before = _roots()
    _edit(prompts, 1, ["~/owner-members"])
    assert _roots()[chat] == before[chat]


@pytest.mark.parametrize("chat", [3, 4])
def test_adding_a_third_colliding_dm_moves_neither(cfg_collide, prompts, identity, chat):
    from aipager.wizard import scope_flows
    before = _roots()
    identity.append({"id": 77, "label": "SARA"})
    prompts.on("select", "role for", "user")
    scope_flows.add_dm_scope("TOK", "bot")
    assert _roots()[chat] == before[chat]


def test_adding_a_third_colliding_dm_gets_its_own_root(cfg_collide, prompts, identity):
    from aipager.wizard import scope_flows
    identity.append({"id": 77, "label": "SARA"})
    prompts.on("select", "role for", "user")
    scope_flows.add_dm_scope("TOK", "bot")
    r = _roots()
    assert r[77] not in (r[3], r[4])


def test_adding_a_group_moves_no_dm_default(cfg_collide, prompts, identity):
    from aipager.wizard import scope_flows
    before = _roots()
    identity.append({"id": 90, "label": "quin"})
    prompts.on("text", "label for this group", "sara")
    prompts.on("confirm", "add yourself", False)
    prompts.on("confirm", "add another", False)
    prompts.on("select", "role for", "user")
    scope_flows.add_group_scope("TOK", "bot")
    assert {c: _roots()[c] for c in (3, 4, -100)} == {c: before[c] for c in (3, 4, -100)}


@pytest.mark.parametrize("role", ["admin", "read_only"])
def test_a_role_change_of_the_colliding_member_keeps_saras_root(cfg_collide, prompts,
                                                                monkeypatch, role):
    from aipager.wizard import edit_menu
    before = _roots()[3]
    monkeypatch.setattr(edit_menu, "_pick_role", lambda *a, **k: role)
    prompts.on("select", "", [4, "role"])
    edit_menu._edit_member(_get(4), "TOK")
    assert _roots()[3] == before


def test_renaming_the_colliding_scope_keeps_saras_root(cfg_collide, prompts):
    from aipager.wizard import edit_menu
    before = _roots()[3]
    prompts.on("select", "", "rename")
    prompts.on("text", "", "renamed DM")
    edit_menu._edit_scope(_get(4), "TOK")
    assert _roots()[3] == before


def test_removing_a_group_member_moves_no_default(cfg_collide, prompts):
    from aipager.wizard import edit_menu
    before = _roots()
    prompts.on("select", "", [5, "remove"])
    prompts.on("confirm", "", True)
    edit_menu._edit_member(_get(-100), "TOK")
    after = _roots()
    assert {c: after.get(c) for c in (3, 4)} == {c: before[c] for c in (3, 4)}


def test_a_chain_of_writes_moves_no_default(cfg_collide, prompts, identity, home):
    """Set, add, remove, one after the other: Sara's folder never moves."""
    from aipager.wizard import edit_menu, scope_flows
    before = _roots()[3]
    _edit(prompts, -100, ["~/team"])
    prompts.rules.clear()
    identity.append({"id": 77, "label": "Sara"})
    prompts.on("select", "role for", "user")
    scope_flows.add_dm_scope("TOK", "bot")
    prompts.rules.clear()
    prompts.on("select", "", "remove")
    prompts.on("confirm", "", True)
    edit_menu._edit_scope(_get(4), "TOK")
    assert _roots()[3] == before


__all__ = ["kit", "confirm_default"]
