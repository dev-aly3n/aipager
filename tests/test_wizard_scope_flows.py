"""F3: additive scope sub-flows (add group / add DM, draft resilience)."""

from __future__ import annotations

import pytest

from aipager import scope as _scope
from aipager.policy import Policy, Role
from aipager.wizard import draft as draft_mod
from aipager.wizard import first_run, scope_flows
from aipager.wizard import team_setup


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect aipager.yaml + the draft into tmp_path."""
    cfg = tmp_path / "aipager.yaml"
    drf = tmp_path / ".wizard-draft.json"
    monkeypatch.setattr(_scope, "CONFIG_PATH", cfg)
    monkeypatch.setattr(draft_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(draft_mod, "DRAFT_PATH", drf)
    return tmp_path


def _stub_ask(monkeypatch, answers):
    queue = iter(answers)

    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers")
    monkeypatch.setattr(scope_flows, "_ask", _ask)


def _stub_captures(monkeypatch, seq):
    """team_setup._capture_user_identity pops from seq (dict or None)."""
    it = iter(seq)
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: next(it))


# ---- _role_choices -------------------------------------------------------

def test_role_choices_includes_custom(monkeypatch):
    pol = Policy(roles={
        "owner": Role(name="owner"), "admin": Role(name="admin"),
        "user": Role(name="user"), "read_only": Role(name="read_only"),
        "reviewer": Role(name="reviewer"),
    })
    monkeypatch.setattr("aipager.policy.load_policy", lambda *a, **k: pol)
    values = [c.value for c in scope_flows._role_choices()]
    assert "reviewer" in values
    assert values[:4] == ["owner", "admin", "user", "read_only"]


def test_role_choices_excludes_owner_when_asked(monkeypatch):
    pol = Policy(roles={"owner": Role(name="owner"), "user": Role(name="user")})
    monkeypatch.setattr("aipager.policy.load_policy", lambda *a, **k: pol)
    values = [c.value for c in scope_flows._role_choices(include_owner=False)]
    assert "owner" not in values


# ---- add_dm_scope --------------------------------------------------------

def test_add_dm_scope_writes(env, monkeypatch):
    # Seed an existing config so there's a token to keep.
    from aipager.scope import Member, Scope
    _scope.dump_scopes(
        [Scope(chat_id=1, kind="dm", label="owner DM",
               members=(Member(id=1, label="owner", role="owner"),))],
        "TOK", _scope.CONFIG_PATH,
    )
    _stub_captures(monkeypatch, [{"id": 555, "label": "bob"}])
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    assert scope_flows.add_dm_scope("TOK", "bot") is True
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    bob = next(s for s in scopes if s.chat_id == 555)
    assert bob.kind == "dm"
    assert bob.members[0].role == "user"


def test_add_dm_scope_cancel(env, monkeypatch):
    from aipager.scope import Member, Scope
    _scope.dump_scopes(
        [Scope(chat_id=1, kind="dm", label="owner DM",
               members=(Member(id=1, label="owner", role="owner"),))],
        "TOK", _scope.CONFIG_PATH,
    )
    _stub_captures(monkeypatch, [None])
    assert scope_flows.add_dm_scope("TOK", "bot") is False
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    assert [s.chat_id for s in scopes] == [1]


# ---- add_group_scope -----------------------------------------------------

def test_add_group_commits_and_clears_draft(env, monkeypatch):
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: -100)
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda: [])
    _stub_captures(monkeypatch, [
        {"id": 11, "label": "ann"}, {"id": 22, "label": "ben"},
    ])
    # label text, "add another?" True (after ann), False (after ben)
    _stub_ask(monkeypatch, ["dev-team", True, False])
    assert scope_flows.add_group_scope("TOK", "bot") is True
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    grp = next(s for s in scopes if s.chat_id == -100)
    assert grp.label == "dev-team"
    assert {m.label for m in grp.members} == {"ann", "ben"}
    assert draft_mod.load_draft() is None  # cleared


def test_add_group_no_members_discards(env, monkeypatch):
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: -100)
    _stub_captures(monkeypatch, [None])  # cancel before any member
    _stub_ask(monkeypatch, ["dev-team"])
    assert scope_flows.add_group_scope("TOK", "bot") is False
    assert draft_mod.load_draft() is None
    assert _scope.load_scopes(_scope.CONFIG_PATH) is None


def test_kill_mid_group_keeps_draft_and_prior_scopes(env, monkeypatch):
    from aipager.scope import Member, Scope
    # A DM scope already committed (the solo bootstrap).
    _scope.dump_scopes(
        [Scope(chat_id=1, kind="dm", label="owner DM",
               members=(Member(id=1, label="owner", role="owner"),))],
        "TOK", _scope.CONFIG_PATH,
    )
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: -100)
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    _stub_captures(monkeypatch, [
        {"id": 11, "label": "ann"}, {"id": 22, "label": "ben"},
    ])
    # label, "add another?"→True (after ann), then KeyboardInterrupt
    # (queue exhausted) on the second "add another?" → simulates Ctrl-C.
    _stub_ask(monkeypatch, ["dev-team", True])
    with pytest.raises(KeyboardInterrupt):
        scope_flows.add_group_scope("TOK", "bot")
    # Draft survives with both members; prior DM scope intact.
    d = draft_mod.load_draft()
    assert d["chat_id"] == -100
    assert len(d["members"]) == 2
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    assert [s.chat_id for s in scopes] == [1]


def test_resume_completes_group(env, monkeypatch):
    from aipager.scope import Member, Scope
    _scope.dump_scopes(
        [Scope(chat_id=1, kind="dm", label="owner DM",
               members=(Member(id=1, label="owner", role="owner"),))],
        "TOK", _scope.CONFIG_PATH,
    )
    draft_mod.save_draft({
        "kind": "group", "chat_id": -100, "label": "dev-team",
        "members": [
            {"id": 11, "label": "ann", "role": "user"},
            {"id": 22, "label": "ben", "role": "user"},
        ],
    })
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda: [])
    _stub_captures(monkeypatch, [None])  # no new members on resume
    _stub_ask(monkeypatch, ["resume"])
    scope_flows.resume_or_discard_draft("TOK", "bot")
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    grp = next(s for s in scopes if s.chat_id == -100)
    assert {m.label for m in grp.members} == {"ann", "ben"}
    assert draft_mod.load_draft() is None


def test_resume_discard(env, monkeypatch):
    draft_mod.save_draft({"kind": "group", "chat_id": -100,
                          "label": "x", "members": []})
    _stub_ask(monkeypatch, ["discard"])
    scope_flows.resume_or_discard_draft("TOK", "bot")
    assert draft_mod.load_draft() is None


def test_resume_no_draft_is_noop(env, monkeypatch):
    # Should not call _ask at all.
    monkeypatch.setattr(scope_flows, "_ask",
                        lambda p: (_ for _ in ()).throw(AssertionError("asked")))
    scope_flows.resume_or_discard_draft("TOK", "bot")


# ---- offer_expansion -----------------------------------------------------

def test_offer_expansion_done_immediately(env, monkeypatch):
    _stub_ask(monkeypatch, ["done"])
    scope_flows.offer_expansion("TOK", "bot")  # returns, writes nothing
    assert _scope.load_scopes(_scope.CONFIG_PATH) is None


def test_offer_expansion_add_dm_then_done(env, monkeypatch):
    calls = []
    monkeypatch.setattr(scope_flows, "add_dm_scope",
                        lambda t, b: calls.append("dm"))
    _stub_ask(monkeypatch, ["dm", "done"])
    scope_flows.offer_expansion("TOK", "bot")
    assert calls == ["dm"]
