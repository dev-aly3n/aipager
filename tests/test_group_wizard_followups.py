"""Roadmap 8.94 (b, c, d): the wizard's leftovers from the group-mode work.

(b) The first run's "Add a group / Add a person" step and resuming an
unfinished group draft wrote ``aipager.yaml`` but never reloaded a running
daemon, unlike the edit menu. (c) "Add a DM scope" committed with
``commit_scope``, which replaces a scope with the same chat id: adding a DM
for someone who already had one wiped their role and deny lists. (d) "Edit
member deny_tools" was offered for owners and admins, whose role ignores
member deny lists, so the edit did nothing.

No test here reaches a real daemon: the conftest points the daemon probe at
a socket that does not exist and makes ``daemon_io.os.kill`` refuse; a test
that wants a daemon fakes ``_detect_daemon_running`` and records ``kill``.
"""

from __future__ import annotations

import signal

import pytest

from aipager import policy as policy_mod
from aipager import scope as scope_mod
from aipager.scope import Member, Scope
from aipager.wizard import daemon_io, edit_menu, first_run, scope_flows
from aipager.wizard import scope_io, team_setup
from aipager.wizard.draft import load_draft, save_draft

GROUP = -1001
NEW_GROUP = -1005
DM = 555
ALY, BOB, EVE = 1, 2, 5
TOKEN = "123456:FAKE"
PID = 4242


# ---- builders ----------------------------------------------------------------

def _dm(chat_id=DM, label="owner", role="owner", deny=()):
    return Scope(chat_id=chat_id, kind="dm", label=f"{label} DM",
                 members=(Member(id=chat_id, label=label, role=role,
                                 deny_tools=tuple(deny)),))


def _group(chat_id=GROUP, members=None):
    return Scope(chat_id=chat_id, kind="group", label="team",
                 members=members or (Member(id=ALY, label="aly", role="owner"),
                                     Member(id=BOB, label="bob", role="user")))


def _cfg():
    return scope_mod.CONFIG_PATH


def _write_config(scopes) -> bytes:
    scope_mod.dump_scopes(scopes, TOKEN, _cfg())
    return _cfg().read_bytes()


def _answers(monkeypatch, module, answers):
    queue = iter(answers)

    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers") from None
    monkeypatch.setattr(module, "_ask", _ask)


def _daemon(monkeypatch, pid=PID):
    """A daemon at *pid*; returns the signals the wizard sends it."""
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", lambda: pid)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(daemon_io.os, "kill",
                        lambda p, sig: sent.append((p, sig)))
    return sent


def _no_daemon_probe(monkeypatch):
    """No daemon (the conftest's socket does not exist), and count the
    probes: a pure first run must not even look for one."""
    probes: list[int] = []
    real = daemon_io._detect_daemon_running

    def _probe():
        probes.append(1)
        return real()
    monkeypatch.setattr(daemon_io, "_detect_daemon_running", _probe)
    return probes


def _hint_calls(monkeypatch):
    calls: list[int] = []
    real = daemon_io._apply_team_change_hint

    def _spy():
        calls.append(1)
        real()
    monkeypatch.setattr(daemon_io, "_apply_team_change_hint", _spy)
    return calls


def _printed(monkeypatch):
    """What the reload step prints (daemon_io's own console only)."""
    lines: list[str] = []

    class _Console:
        @staticmethod
        def print(*a, **k):
            lines.append(" ".join(map(str, a)))
    monkeypatch.setattr(daemon_io, "console", _Console())
    return lines


def _adds_for_real(monkeypatch):
    """Stub the two sub-flows with ones that really add a scope (so the
    config the reload checks is the one on disk)."""
    def _group_flow(token, bot_username, **k):
        return scope_io.add_new_scope(_group(chat_id=NEW_GROUP), token)

    def _dm_flow(token, bot_username):
        return scope_io.add_new_scope(_dm(chat_id=EVE, label="eve",
                                          role="user"), token)
    monkeypatch.setattr(scope_flows, "add_group_scope", _group_flow)
    monkeypatch.setattr(scope_flows, "add_dm_scope", _dm_flow)


# =============================================================================
# (b) the first run's expansion step and a resumed draft reload live
# =============================================================================

def test_expansion_reloads_the_running_daemon_once_for_everything_added(
        monkeypatch):
    _write_config([_dm()])
    _adds_for_real(monkeypatch)
    sent = _daemon(monkeypatch)
    calls = _hint_calls(monkeypatch)
    _answers(monkeypatch, scope_flows, ["group", "dm", "done"])
    scope_flows.offer_expansion(TOKEN, "aipagerbot")
    assert [s.chat_id for s in scope_io.read_config()[0]] == [DM, NEW_GROUP,
                                                              EVE]
    assert calls == [1]
    assert sent == [(PID, signal.SIGUSR1)]


def test_expansion_reload_says_it_was_applied_live(monkeypatch):
    _write_config([_dm()])
    _adds_for_real(monkeypatch)
    _daemon(monkeypatch)
    lines = _printed(monkeypatch)
    _answers(monkeypatch, scope_flows, ["dm", "done"])
    scope_flows.offer_expansion(TOKEN, "aipagerbot")
    assert any("Scopes reloaded live" in line for line in lines)


def test_expansion_with_no_daemon_sends_nothing_and_says_nothing(
        monkeypatch):
    """The operator's usual first run: no daemon yet. The conftest's
    ``os.kill`` raises if anything is signalled."""
    _write_config([_dm()])
    _adds_for_real(monkeypatch)
    probes = _no_daemon_probe(monkeypatch)
    lines = _printed(monkeypatch)
    _answers(monkeypatch, scope_flows, ["group", "done"])
    scope_flows.offer_expansion(TOKEN, "aipagerbot")
    assert probes                 # looked, found none (no signal)
    assert lines == []


def test_expansion_says_not_applied_when_the_daemon_would_refuse(
        monkeypatch):
    _write_config([_dm()])
    policy_mod.POLICY_PATH.write_text("roles: [not, a, mapping]\n")
    _adds_for_real(monkeypatch)
    sent = _daemon(monkeypatch)
    lines = _printed(monkeypatch)
    _answers(monkeypatch, scope_flows, ["dm", "done"])
    scope_flows.offer_expansion(TOKEN, "aipagerbot")
    assert sent == []
    assert any("Not applied" in line for line in lines)
    assert not any("reloaded live" in line for line in lines)


def test_expansion_done_at_once_never_looks_for_a_daemon(monkeypatch):
    """A pure first run (I'm done straight away) sends nothing."""
    _write_config([_dm()])
    probes = _no_daemon_probe(monkeypatch)
    calls = _hint_calls(monkeypatch)
    _answers(monkeypatch, scope_flows, ["done"])
    scope_flows.offer_expansion(TOKEN, "aipagerbot")
    assert calls == []
    assert probes == []


def test_expansion_where_nothing_was_added_sends_nothing(monkeypatch):
    before = _write_config([_dm()])
    monkeypatch.setattr(scope_flows, "add_group_scope", lambda *a, **k: False)
    monkeypatch.setattr(scope_flows, "add_dm_scope", lambda *a, **k: False)
    sent = _daemon(monkeypatch)
    calls = _hint_calls(monkeypatch)
    _answers(monkeypatch, scope_flows, ["group", "dm", "done"])
    scope_flows.offer_expansion(TOKEN, "aipagerbot")
    assert calls == []
    assert sent == []
    assert _cfg().read_bytes() == before


def test_expansion_left_with_ctrl_c_still_applies_what_was_added(
        monkeypatch):
    _write_config([_dm()])
    _adds_for_real(monkeypatch)
    sent = _daemon(monkeypatch)
    _answers(monkeypatch, scope_flows, ["group"])    # then Ctrl-C at the menu
    with pytest.raises(KeyboardInterrupt):
        scope_flows.offer_expansion(TOKEN, "aipagerbot")
    assert sent == [(PID, signal.SIGUSR1)]


def test_expansion_cancelled_action_after_an_add_still_reloads_once(
        monkeypatch):
    _write_config([_dm()])
    _adds_for_real(monkeypatch)

    def _cancelled(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(scope_flows, "add_dm_scope", _cancelled)
    monkeypatch.setattr(scope_flows, "friendly_warn", lambda *a: None)
    sent = _daemon(monkeypatch)
    _answers(monkeypatch, scope_flows, ["group", "dm", "done"])
    scope_flows.offer_expansion(TOKEN, "aipagerbot")
    assert sent == [(PID, signal.SIGUSR1)]


def _stub_first_run(monkeypatch):
    monkeypatch.setattr(first_run, "_step_token",
                        lambda step_label: (TOKEN, "aipagerbot"))
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: DM)
    monkeypatch.setattr(first_run, "_grant_owner_step",
                        lambda *a, **k: "owner")
    monkeypatch.setattr(first_run, "_step_deps", lambda step_label: True)
    monkeypatch.setattr(first_run, "_step_settings", lambda step_label: None)
    monkeypatch.setattr(first_run, "_completion_screen", lambda: None)


def test_dm_parity_a_pure_first_run_sends_no_signal(monkeypatch):
    """The operator's own install: one DM, I'm done, no daemon. Exactly as
    before: the DM is written and the daemon is never looked for."""
    _stub_first_run(monkeypatch)
    probes = _no_daemon_probe(monkeypatch)
    calls = _hint_calls(monkeypatch)
    _answers(monkeypatch, scope_flows, ["done"])
    assert first_run._first_run_flow() == 0
    scopes, token = scope_io.read_config()
    assert token == TOKEN
    assert [(s.chat_id, s.kind, s.members[0].role) for s in scopes] == [
        (DM, "dm", "owner")]
    assert calls == []
    assert probes == []


def test_first_run_adding_a_person_reloads_a_running_daemon(monkeypatch):
    _stub_first_run(monkeypatch)
    _adds_for_real(monkeypatch)
    sent = _daemon(monkeypatch)
    _answers(monkeypatch, scope_flows, ["dm", "done"])
    assert first_run._first_run_flow() == 0
    assert [s.chat_id for s in scope_io.read_config()[0]] == [DM, EVE]
    assert sent == [(PID, signal.SIGUSR1)]


def _draft():
    save_draft({"kind": "group", "chat_id": NEW_GROUP, "label": "new team",
                "members": [{"id": EVE, "label": "eve", "role": "user"}]})


def _resume_answers(monkeypatch):
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: None)     # no more members
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda: [])
    _answers(monkeypatch, scope_flows, ["resume"])


def test_a_resumed_draft_reloads_the_running_daemon(monkeypatch):
    _write_config([_dm()])
    _draft()
    _resume_answers(monkeypatch)
    sent = _daemon(monkeypatch)
    calls = _hint_calls(monkeypatch)
    scope_flows.resume_or_discard_draft(TOKEN, "")
    assert [s.chat_id for s in scope_io.read_config()[0]] == [DM, NEW_GROUP]
    assert not load_draft()
    assert calls == [1]
    assert sent == [(PID, signal.SIGUSR1)]


def test_a_resumed_draft_with_no_daemon_sends_nothing(monkeypatch):
    _write_config([_dm()])
    _draft()
    _resume_answers(monkeypatch)
    probes = _no_daemon_probe(monkeypatch)
    lines = _printed(monkeypatch)
    scope_flows.resume_or_discard_draft(TOKEN, "")
    assert [s.chat_id for s in scope_io.read_config()[0]] == [DM, NEW_GROUP]
    assert probes                 # looked, found none (no signal)
    assert lines == []


def test_a_resumed_draft_says_not_applied_when_the_daemon_would_refuse(
        monkeypatch):
    _write_config([_dm()])
    policy_mod.POLICY_PATH.write_text("roles: [not, a, mapping]\n")
    _draft()
    _resume_answers(monkeypatch)
    sent = _daemon(monkeypatch)
    lines = _printed(monkeypatch)
    scope_flows.resume_or_discard_draft(TOKEN, "")
    assert sent == []
    assert any("Not applied" in line for line in lines)


def test_a_resumed_draft_that_was_refused_sends_nothing(monkeypatch):
    """The group was set up meanwhile: nothing written, nothing sent."""
    before = _write_config([_dm(), _group(chat_id=NEW_GROUP)])
    _draft()
    _resume_answers(monkeypatch)
    monkeypatch.setattr(scope_flows, "friendly_warn", lambda *a: None)
    sent = _daemon(monkeypatch)
    calls = _hint_calls(monkeypatch)
    scope_flows.resume_or_discard_draft(TOKEN, "")
    assert _cfg().read_bytes() == before
    assert calls == []
    assert sent == []


def test_a_resumed_draft_paused_again_sends_nothing(monkeypatch):
    before = _write_config([_dm()])
    _draft()

    def _interrupt(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(team_setup, "_capture_user_identity", _interrupt)
    _answers(monkeypatch, scope_flows, ["resume"])
    monkeypatch.setattr(scope_flows, "friendly_warn", lambda *a: None)
    sent = _daemon(monkeypatch)
    calls = _hint_calls(monkeypatch)
    scope_flows.resume_or_discard_draft(TOKEN, "")
    assert _cfg().read_bytes() == before
    assert load_draft()
    assert calls == []
    assert sent == []


def test_a_discarded_draft_sends_nothing(monkeypatch):
    _write_config([_dm()])
    _draft()
    _answers(monkeypatch, scope_flows, ["discard"])
    sent = _daemon(monkeypatch)
    calls = _hint_calls(monkeypatch)
    scope_flows.resume_or_discard_draft(TOKEN, "")
    assert calls == []
    assert sent == []


# =============================================================================
# (c) Add a DM scope never replaces a scope
# =============================================================================

def _capture(monkeypatch, uid, label="bob"):
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: {"id": uid, "label": label})


def _warned(monkeypatch):
    out: list[tuple] = []
    monkeypatch.setattr(scope_flows, "friendly_warn",
                        lambda *a: out.append(a))
    return out


def test_adding_a_dm_for_a_person_with_a_dm_refuses(monkeypatch):
    before = _write_config([_dm(), _dm(chat_id=BOB, label="bob",
                                       role="read_only", deny=("WebFetch",))])
    _capture(monkeypatch, BOB)
    monkeypatch.setattr(scope_flows, "_pick_role",
                        lambda *a, **k: pytest.fail("must not ask a role"))
    warned = _warned(monkeypatch)
    assert scope_flows.add_dm_scope(TOKEN, "aipagerbot") is False
    assert warned == [(scope_flows.DM_ALREADY_SET_UP,)]
    assert scope_flows.DM_ALREADY_SET_UP == (
        "This person already has a DM set up. Use Edit a member to change "
        "their role.")
    assert _cfg().read_bytes() == before


def test_adding_a_dm_with_a_groups_id_refuses(monkeypatch):
    before = _write_config([_dm(), _group()])
    _capture(monkeypatch, GROUP, label="team")
    monkeypatch.setattr(scope_flows, "_pick_role",
                        lambda *a, **k: pytest.fail("must not ask a role"))
    warned = _warned(monkeypatch)
    assert scope_flows.add_dm_scope(TOKEN, "aipagerbot") is False
    assert warned == [(scope_flows.DM_ID_IS_A_GROUP,)]
    assert _cfg().read_bytes() == before


def test_a_dm_set_up_meanwhile_is_not_replaced_at_commit(monkeypatch):
    """The check after capture is not enough: the commit itself never
    replaces a scope."""
    _write_config([_dm()])
    _capture(monkeypatch, BOB)

    def _pick(*a, **k):
        # Meanwhile, someone sets bob's DM up.
        scope_mod.dump_scopes(
            [_dm(), _dm(chat_id=BOB, label="bob", role="read_only")],
            TOKEN, _cfg())
        return "owner"
    monkeypatch.setattr(scope_flows, "_pick_role", _pick)
    warned = _warned(monkeypatch)
    assert scope_flows.add_dm_scope(TOKEN, "aipagerbot") is False
    assert warned == [(scope_flows.DM_ALREADY_SET_UP,)]
    bob = next(s for s in scope_io.read_config()[0] if s.chat_id == BOB)
    assert bob.members[0].role == "read_only"


def test_adding_a_new_dm_still_adds_it_and_keeps_the_rest(monkeypatch):
    _write_config([_dm(), _group()])
    _capture(monkeypatch, EVE, label="eve")
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    warned = _warned(monkeypatch)
    assert scope_flows.add_dm_scope(TOKEN, "aipagerbot") is True
    assert warned == []
    scopes, token = scope_io.read_config()
    assert token == TOKEN
    assert scopes[:2] == [_dm(), _group()]
    assert scopes[2] == Scope(chat_id=EVE, kind="dm", label="eve DM",
                              members=(Member(id=EVE, label="eve",
                                              role="user"),))


def test_a_group_member_can_still_get_their_own_dm(monkeypatch):
    """bob is in the group, not a DM of his own: that is a new DM."""
    _write_config([_dm(), _group()])
    _capture(monkeypatch, BOB)
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    assert scope_flows.add_dm_scope(TOKEN, "aipagerbot") is True
    assert [s.chat_id for s in scope_io.read_config()[0]] == [DM, GROUP, BOB]


# =============================================================================
# (d) Edit member deny_tools for a role that ignores it
# =============================================================================

def _edit_deny(monkeypatch, scope, member_id):
    """Run Edit a member → (member) → Edit member deny_tools. Returns
    (result, warned lines, toggle calls)."""
    _answers(monkeypatch, edit_menu, [member_id, "deny"])
    monkeypatch.setattr(edit_menu.questionary, "select",
                        lambda *a, **k: object())
    toggles: list[tuple] = []

    def _toggle(current):
        toggles.append(current)
        return ("WebFetch",)
    monkeypatch.setattr(edit_menu, "_toggle_tools", _toggle)
    warned: list[tuple] = []
    monkeypatch.setattr(edit_menu, "friendly_warn",
                        lambda *a: warned.append(a))
    return edit_menu._edit_member(scope, TOKEN), warned, toggles


def _team(role, label="carol"):
    return _group(members=(Member(id=ALY, label="aly", role="owner"),
                           Member(id=EVE, label=label, role=role)))


@pytest.mark.parametrize("role,line", [
    ("owner", "@carol is an owner: per-member blocked tools do not apply "
              "to this role."),
    ("admin", "@carol is an admin: per-member blocked tools do not apply "
              "to this role."),
])
def test_deny_edit_for_an_owner_or_admin_says_it_does_not_apply(
        monkeypatch, role, line):
    scope = _team(role)
    before = _write_config([_dm(), scope])
    result, warned, toggles = _edit_deny(monkeypatch, scope, EVE)
    assert result is False
    assert warned == [(line,)]
    assert toggles == []
    assert _cfg().read_bytes() == before


def test_deny_edit_for_a_custom_role_with_the_bypass_says_so(monkeypatch):
    policy_mod.POLICY_PATH.write_text(
        "roles:\n  lead:\n    bypass_role_denies: true\n")
    scope = _team("lead")
    before = _write_config([_dm(), scope])
    result, warned, toggles = _edit_deny(monkeypatch, scope, EVE)
    assert result is False
    assert warned == [("@carol is a lead: per-member blocked tools do not "
                       "apply to this role.",)]
    assert toggles == []
    assert _cfg().read_bytes() == before


@pytest.mark.parametrize("policy_text,role", [
    ("", "user"),
    ("", "read_only"),
    ("roles:\n  lead:\n    can_prompt: true\n", "lead"),
    # The real policy decides, not the role's name.
    ("roles:\n  admin:\n    bypass_role_denies: false\n", "admin"),
])
def test_deny_edit_is_kept_for_roles_it_applies_to(
        monkeypatch, policy_text, role):
    if policy_text:
        policy_mod.POLICY_PATH.write_text(policy_text)
    scope = _team(role)
    _write_config([_dm(), scope])
    result, warned, toggles = _edit_deny(monkeypatch, scope, EVE)
    assert result is True
    assert warned == []
    assert toggles == [()]
    carol = next(m for s in scope_io.read_config()[0] if s.chat_id == GROUP
                 for m in s.members if m.id == EVE)
    assert carol.deny_tools == ("WebFetch",)


@pytest.mark.parametrize("role", ["user", "admin", "owner"])
def test_deny_edit_is_kept_when_the_policy_cannot_be_read(monkeypatch, role):
    """A broken policy.yaml: the wizard cannot tell, so it offers the edit
    as before rather than guessing from the role's name (or falling back to
    the built-in roles, which the broken file may have changed)."""
    policy_mod.POLICY_PATH.write_text("roles: [not, a, mapping]\n")
    scope = _team(role)
    result, warned, toggles = _edit_deny(monkeypatch, scope, EVE)
    assert result is True
    assert toggles == [()]


def test_deny_edit_is_kept_for_a_role_the_policy_does_not_know(monkeypatch):
    scope = _team("ghost")
    try:
        result, warned, toggles = _edit_deny(monkeypatch, scope, EVE)
    except Exception as e:  # noqa: BLE001 — a crash is the failure here
        pytest.fail(f"the deny edit crashed on an unknown role: {e!r}")
    assert result is True
    assert warned == []
    assert toggles == [()]
