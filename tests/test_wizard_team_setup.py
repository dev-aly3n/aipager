"""Tests for wizard.team_setup — the team-mode setup flow."""

from __future__ import annotations


import pytest

from aipager.wizard import team_setup


def _stub_ask(monkeypatch, answers):
    queue = iter(answers)
    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers")
    monkeypatch.setattr(team_setup, "_ask", _ask)


@pytest.fixture(autouse=True)
def _no_spin(monkeypatch):
    """Replace _spin with no-op so the spinner doesn't try to start."""
    import contextlib
    monkeypatch.setattr(team_setup, "_spin",
                        lambda msg: contextlib.nullcontext())


# ---- _show_team_warning_panel ------------------------------------------

def test_show_team_warning_panel(capsys):
    team_setup._show_team_warning_panel()
    out = capsys.readouterr().out
    assert "shell access" in out or "shell" in out.lower()


# ---- _finalize_user ----------------------------------------------------

def test_finalize_user_empty_input_uses_suggestion(monkeypatch):
    _stub_ask(monkeypatch, [""])
    out = team_setup._finalize_user(42, "alice", set())
    assert out == {"id": 42, "label": "alice"}


def test_finalize_user_custom_label(monkeypatch):
    _stub_ask(monkeypatch, ["custom"])
    out = team_setup._finalize_user(42, "alice", set())
    assert out == {"id": 42, "label": "custom"}


def test_finalize_user_label_clash_retries(monkeypatch):
    _stub_ask(monkeypatch, ["taken", "ok"])
    out = team_setup._finalize_user(42, "x", {"taken"})
    assert out["label"] == "ok"


# ---- _pick_role --------------------------------------------------------

def test_pick_role(monkeypatch):
    _stub_ask(monkeypatch, ["developer"])
    assert team_setup._pick_role(1) == "developer"


# ---- _collect_deny_tools -----------------------------------------------

def test_collect_deny_tools_yes(monkeypatch):
    _stub_ask(monkeypatch, [True])
    assert team_setup._collect_deny_tools() == ["Write", "Edit"]


def test_collect_deny_tools_no(monkeypatch):
    _stub_ask(monkeypatch, [False])
    assert team_setup._collect_deny_tools() == []


# ---- _capture_user_identity (manual path) -----------------------------

def test_capture_user_identity_manual_numeric(monkeypatch):
    _stub_ask(monkeypatch, [
        "manual",    # pick the manual branch
        "12345",     # the id
        "alice",     # label
    ])
    monkeypatch.setattr(team_setup, "_resolve_user", lambda t, q: None)
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out == {"id": 12345, "label": "alice"}


def test_capture_user_identity_manual_handle_resolves(monkeypatch):
    _stub_ask(monkeypatch, [
        "manual",    # pick the manual branch
        "@alice",    # the handle
        "",          # label = use suggestion
    ])
    monkeypatch.setattr(team_setup, "_resolve_user",
                        lambda t, q: (42, "alice"))
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out == {"id": 42, "label": "alice"}


def test_capture_user_identity_manual_invalid_then_cancel(monkeypatch):
    # token="" → goes straight to manual path (no method prompt).
    # Non-digit raw + no token → invalid integer → retry/cancel prompt.
    _stub_ask(monkeypatch, [
        "not_a_number_or_handle",  # invalid
        "cancel",                  # cancel the user
    ])
    monkeypatch.setattr(team_setup, "_resolve_user", lambda t, q: None)
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="")
    assert out is None


def test_capture_user_identity_manual_existing_id_retries(monkeypatch):
    # token="" → no method prompt; goes straight to manual.
    _stub_ask(monkeypatch, [
        "12345",     # already on allow-list — rejected
        "67890",     # new id
        "alice",     # label
    ])
    out = team_setup._capture_user_identity(
        1, existing_ids={12345}, existing_labels=set(), token="")
    assert out == {"id": 67890, "label": "alice"}


def test_capture_user_identity_handle_resolve_fails_cancel(monkeypatch):
    _stub_ask(monkeypatch, [
        "manual",    # pick manual
        "@unknown",  # can't resolve
        "cancel",    # cancel this user
    ])
    monkeypatch.setattr(team_setup, "_resolve_user", lambda t, q: None)
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out is None


def test_capture_user_identity_handle_resolve_fails_retry(monkeypatch):
    _stub_ask(monkeypatch, [
        "manual",    # method
        "@unknown",  # can't resolve first time
        "retry",     # retry
        "@alice",    # second attempt succeeds
        "",          # use suggested label
    ])
    calls = []
    def _resolve(t, q):
        calls.append(q)
        if len(calls) == 1:
            return None
        return (99, "alice")
    monkeypatch.setattr(team_setup, "_resolve_user", _resolve)
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out == {"id": 99, "label": "alice"}


# ---- _capture_user_identity (auto path) -------------------------------

def test_capture_user_identity_auto_happy(monkeypatch):
    _stub_ask(monkeypatch, [
        "auto",      # method
        True,        # "they've sent something — continue?"
        "",          # use suggested label
    ])
    monkeypatch.setattr(team_setup, "_fetch_id_from_updates",
                        lambda t, *, want: (42, "alice", None))
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out == {"id": 42, "label": "alice"}


def test_capture_user_identity_auto_no_result_retry(monkeypatch):
    _stub_ask(monkeypatch, [
        "auto",       # method
        True,         # continue
        "retry",      # what now? — retry
        True,         # continue
        "",           # label
    ])
    calls = []
    def _fetch(t, *, want):
        calls.append(1)
        if len(calls) == 1:
            return None, None, None
        return 42, "alice", None
    monkeypatch.setattr(team_setup, "_fetch_id_from_updates", _fetch)
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out == {"id": 42, "label": "alice"}


def test_capture_user_identity_auto_switch_to_manual(monkeypatch):
    _stub_ask(monkeypatch, [
        "auto",        # method
        True,          # continue
        "manual",      # switch
        "55",          # numeric id
        "bob",         # label
    ])
    monkeypatch.setattr(team_setup, "_fetch_id_from_updates",
                        lambda t, *, want: (None, None, None))
    monkeypatch.setattr(team_setup, "_resolve_user", lambda t, q: None)
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out == {"id": 55, "label": "bob"}


def test_capture_user_identity_auto_cancel(monkeypatch):
    _stub_ask(monkeypatch, [
        "auto", True,
        "cancel",
    ])
    monkeypatch.setattr(team_setup, "_fetch_id_from_updates",
                        lambda t, *, want: (None, None, None))
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out is None


def test_capture_user_identity_auto_existing_id_continues(monkeypatch):
    """If the auto-detected user is already on the allow-list, the loop
    re-prompts."""
    _stub_ask(monkeypatch, [
        "auto", True,    # first attempt — already-existing user
        True,            # continue
        "",              # label for the new user
    ])
    calls = []
    def _fetch(t, *, want):
        calls.append(1)
        if len(calls) == 1:
            return 12345, "alice", None  # already exists
        return 67890, "bob", None
    monkeypatch.setattr(team_setup, "_fetch_id_from_updates", _fetch)
    out = team_setup._capture_user_identity(
        1, existing_ids={12345}, existing_labels=set(), token="tok")
    assert out == {"id": 67890, "label": "bob"}


def test_capture_user_identity_cancel_at_method_select(monkeypatch):
    _stub_ask(monkeypatch, ["cancel"])
    out = team_setup._capture_user_identity(
        1, existing_ids=set(), existing_labels=set(), token="tok")
    assert out is None


# ---- _collect_users ----------------------------------------------------

def test_collect_users_one_user_then_stop(monkeypatch):
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda idx, **k: {"id": 1, "label": "alice"})
    monkeypatch.setattr(team_setup, "_pick_role", lambda idx: "developer")
    _stub_ask(monkeypatch, [False])  # don't add another
    out = team_setup._collect_users()
    assert out == [{"id": 1, "label": "alice", "role": "developer"}]


def test_collect_users_multiple_then_stop(monkeypatch):
    counter = {"n": 0}
    def _cap(idx, **k):
        counter["n"] += 1
        return {"id": counter["n"], "label": f"u{counter['n']}"}
    monkeypatch.setattr(team_setup, "_capture_user_identity", _cap)
    monkeypatch.setattr(team_setup, "_pick_role", lambda idx: "developer")
    _stub_ask(monkeypatch, [True, True, False])  # add 2 more, then stop
    out = team_setup._collect_users()
    assert len(out) == 3


def test_collect_users_cancelled_first_user(monkeypatch):
    """If admin cancels the first add, return empty list."""
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda idx, **k: None)
    assert team_setup._collect_users() == []


def test_collect_users_dedups_against_existing(monkeypatch):
    counter = {"n": 0}
    captured_args = []
    def _cap(idx, *, existing_ids, existing_labels, token):
        captured_args.append((set(existing_ids), set(existing_labels)))
        counter["n"] += 1
        return {"id": 99, "label": "new"}
    monkeypatch.setattr(team_setup, "_capture_user_identity", _cap)
    monkeypatch.setattr(team_setup, "_pick_role", lambda idx: "developer")
    _stub_ask(monkeypatch, [False])  # stop after first
    team_setup._collect_users(
        existing_ids={1, 2}, existing_labels={"a", "b"}, token="tok")
    assert captured_args[0][0] == {1, 2}
    assert captured_args[0][1] == {"a", "b"}


# ---- _step_team_setup --------------------------------------------------

def test_step_team_setup_adds_one_admin_with_rules(monkeypatch):
    """Full team setup writes team.yaml with users and rules."""
    # _step_team_setup drives its own add-user loop; mock the per-user
    # helpers and the "Add another?" confirm.
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda idx, **k: {"id": 1, "label": "admin"})
    monkeypatch.setattr(team_setup, "_pick_role", lambda idx: "admin")
    monkeypatch.setattr(team_setup, "_collect_deny_tools",
                        lambda: ["Bash"])
    _stub_ask(monkeypatch, [False])  # "Add another?" no
    saved = []
    monkeypatch.setattr("aipager.team.dump_team",
                        lambda t, path=None: saved.append(t))
    team_setup._step_team_setup(group_id=-100, step_label="[5/7]", token="tok")
    assert len(saved) >= 1
    last = saved[-1]
    assert 1 in last.users
    assert "Bash" in last.rules.deny_tools


def test_step_team_setup_no_users_skips_team_yaml(monkeypatch):
    """If admin cancels at the first user, no team.yaml is written."""
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda idx, **k: None)
    saved = []
    monkeypatch.setattr("aipager.team.dump_team",
                        lambda t, path=None: saved.append(t))
    team_setup._step_team_setup(group_id=-100, step_label="[5/7]", token="tok")
    assert saved == []  # nothing written


def test_step_team_setup_warns_when_no_admin(monkeypatch, capsys):
    """If only developers added, warn but still save."""
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda idx, **k: {"id": 1, "label": "dev"})
    monkeypatch.setattr(team_setup, "_pick_role", lambda idx: "developer")
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda: [])
    _stub_ask(monkeypatch, [False])
    saved = []
    monkeypatch.setattr("aipager.team.dump_team",
                        lambda t, path=None: saved.append(t))
    team_setup._step_team_setup(group_id=-100, step_label="[5/7]", token="tok")
    assert len(saved) >= 1


# ---- _step_team_config (backwards-compat wrapper) --------------------

def test_step_team_config_personal_returns_false(monkeypatch):
    monkeypatch.setattr(team_setup, "_step_team_setup", lambda *a, **k: None)
    from aipager.wizard import first_run
    monkeypatch.setattr(first_run, "_step_pick_mode",
                        lambda *a, **k: "personal")
    assert team_setup._step_team_config() is False


def test_step_team_config_team_collects_group_id(monkeypatch):
    monkeypatch.setattr(team_setup, "_step_team_setup", lambda *a, **k: None)
    from aipager.wizard import first_run
    monkeypatch.setattr(first_run, "_step_pick_mode",
                        lambda *a, **k: "team")
    _stub_ask(monkeypatch, ["-100"])
    assert team_setup._step_team_config() is True


def test_step_team_config_team_invalid_then_valid(monkeypatch):
    monkeypatch.setattr(team_setup, "_step_team_setup", lambda *a, **k: None)
    from aipager.wizard import first_run
    monkeypatch.setattr(first_run, "_step_pick_mode",
                        lambda *a, **k: "team")
    _stub_ask(monkeypatch, ["notanumber", "5", "-100"])
    # "notanumber" → not int; "5" → positive (rejected); "-100" → ok
    assert team_setup._step_team_config() is True
