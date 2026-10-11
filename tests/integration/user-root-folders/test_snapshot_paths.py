"""The root travels with the rules wherever they are copied (design.md
success criteria 10-12; entrypoints.md "Exported functions").

``resolve_snapshot``, ``merge_snapshots``, ``carried_snapshot``,
``snapshot_for_unattributed_prompt``, ``narrowed_snapshot``,
``safety_rules``, pins, triggers, ``loop_rights``, peer receipts
(``rules_of``/``well_formed_rules``) and the live reload's
``sender_rules``/``rules_narrowed``; the end effect is checked through
``enforce.decide`` where it matters."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from aipager import member_root, peer_auth
from aipager import policy_snapshot as ps
from aipager import safety
from aipager.bot import live_reload
from aipager.dtach import enforce, inject, notify_hook
from aipager.scope import Member, Scope
from aipager.state import Status

kit = sys.modules["_userroot_kit"]
OWNER, ADMIN, BOB, CY, HAL = kit.OWNER, kit.ADMIN, kit.BOB, kit.CY, kit.HAL
GROUP, SESSION, SID = kit.GROUP, kit.SESSION, kit.CLAUDE_SID
S = safety.ROOT_SENTINEL


def _note(root):
    """A restricted note whose root is *root* (absent when None)."""
    base = {"bypass_safety": False, "bypass_role_denies": False,
            "deny_tools": [], "allow_tools": [], "deny_paths_no_access": [],
            "deny_paths_no_write": [], "allow_paths": [], "deny_bash_patterns": []}
    if root is not None:
        base["root"] = root
    return base


@pytest.fixture
def team(team_root):
    return os.path.realpath(str(team_root))


# ---- resolve_snapshot ----------------------------------------------------------------

@pytest.mark.parametrize("who", [BOB, CY, HAL], ids=["user", "read_only", "helper"])
def test_resolve_gives_root_only_without_can_manage(snap_for, team, who):
    assert snap_for(who, GROUP)["root"] == team


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_resolve_gives_no_root_to_managers(snap_for, who):
    assert "root" not in snap_for(who, GROUP)


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_snapshot_bound_is_none_for_managers(pol, scopes, who):
    g = next(s for s in scopes if s.chat_id == GROUP)
    m = next(x for x in g.members if x.id == who)
    assert member_root.snapshot_bound(pol.get_role(m.role), g, scopes) is None


def test_resolve_without_root_bound_has_no_root(pol):
    assert "root" not in ps.resolve_snapshot(pol.get_role("user"), None, None)


def test_dm_root_is_the_dm_scopes(snap_for, hal_root):
    assert snap_for(HAL, HAL)["root"] == os.path.realpath(str(hal_root))


def test_default_root_reaches_the_snapshot(snap_for, scopes):
    sara = next(s for s in scopes if s.chat_id == kit.SARA)
    assert snap_for(kit.SARA, kit.SARA)["root"] == os.path.realpath(
        member_root.effective_root(sara, scopes))


def test_other_roots_are_no_access(snap_for, hal_root):
    assert str(hal_root) + "/**" in snap_for(BOB, GROUP)["deny_paths_no_access"]


def test_own_root_is_not_no_access(snap_for, team_root):
    assert str(team_root) + "/**" not in snap_for(BOB, GROUP)["deny_paths_no_access"]


def test_a_root_nested_in_the_members_own_is_not_denied(pol, home, team_root):
    """Only roots disjoint from the member's own are denied."""
    sl = [Scope(chat_id=GROUP, kind="group", label="team",
                members=(Member(id=BOB, label="bob", role="user"),), root=str(team_root)),
          Scope(chat_id=77, kind="dm", label="n DM",
                members=(Member(id=77, label="n", role="user"),), root=str(team_root / "n"))]
    role = pol.get_role("user")
    snap = ps.resolve_snapshot(role, sl[0], sl[0].members[0],
                               root_bound=member_root.snapshot_bound(role, sl[0], sl))
    assert str(team_root / "n") + "/**" not in snap["deny_paths_no_access"]


# ---- the note a member's message writes (the acting chat's root) --------------------------

@pytest.fixture
def typed(monkeypatch):
    out = []

    async def _send(name, text, *a, **kw):
        out.append((name, text))
        return True
    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    return out


def test_member_message_note_carries_group_root(gbot, run_async, typed, team, scopes):
    """HAL is restricted in the group (root ~/roots/team) and in their DM
    (root ~/roots/hal); a message from the group to a session whose home
    chat is the DM carries the GROUP's root."""
    bot = gbot()
    sess = bot.registry.get_or_create("claude-dm1")
    sess.label, sess.scope_chat_id, sess.scope_kind = "dm1", HAL, "dm"
    sess.status = Status.IDLE
    run_async(bot._inject_prompt(sess, "hello", chat_id=GROUP, driver_user_id=HAL))
    notes = ps.list_outstanding_notes("claude-dm1")
    assert notes and notes[-1].get("root") == team


def test_member_message_note_in_dm_carries_dm_root(gbot, run_async, typed, hal_root):
    bot = gbot()
    sess = bot.registry.get_or_create("claude-dm1")
    sess.label, sess.scope_chat_id, sess.scope_kind = "dm1", HAL, "dm"
    sess.status = Status.IDLE
    run_async(bot._inject_prompt(sess, "hello", chat_id=HAL, driver_user_id=HAL))
    assert ps.list_outstanding_notes("claude-dm1")[-1].get("root") == os.path.realpath(str(hal_root))


def test_owner_message_note_has_no_root(gbot, run_async, typed):
    bot = gbot()
    sess = bot.registry.get_or_create("claude-g1__g100")
    sess.label, sess.scope_chat_id, sess.scope_kind = "g1", GROUP, "group"
    sess.status = Status.IDLE
    run_async(bot._inject_prompt(sess, "hello", chat_id=GROUP, driver_user_id=OWNER))
    assert "root" not in ps.list_outstanding_notes("claude-g1__g100")[-1]


# ---- merge_snapshots -----------------------------------------------------------------

def test_merge_narrower_wins(home):
    a, b = str(home / "t"), str(home / "t" / "sub")
    assert ps.merge_snapshots([_note(a), _note(b)])["root"] == b


def test_merge_narrower_wins_in_any_order(home):
    a, b = str(home / "t"), str(home / "t" / "sub")
    assert ps.merge_snapshots([_note(b), _note(a)])["root"] == b


def test_merge_disjoint_is_sentinel(home):
    assert ps.merge_snapshots([_note(str(home / "a")), _note(str(home / "b"))])["root"] == S


def test_merge_prefix_siblings_are_disjoint(home):
    assert ps.merge_snapshots([_note(str(home / "sara")),
                               _note(str(home / "sara2"))])["root"] == S


def test_merge_equal_roots_keep_it(home):
    r = str(home / "t")
    assert ps.merge_snapshots([_note(r), _note(r + "/")])["root"] == r


def test_merge_absent_keeps_root(home):
    r = str(home / "t")
    assert ps.merge_snapshots([_note(r), _note(None)])["root"] == r


def test_merge_owner_note_keeps_root(home, snap_for, team):
    assert ps.merge_snapshots([snap_for(OWNER, GROUP), snap_for(BOB, GROUP)])["root"] == team


def test_merge_with_the_floor_keeps_root(home):
    r = str(home / "t")
    assert ps.merge_snapshots([_note(r), ps.floor_snapshot()])["root"] == r


def test_merge_no_roots_has_no_root():
    assert "root" not in ps.merge_snapshots([_note(None), _note(None)])


@pytest.mark.parametrize("bad", [5, "relative/x", "", ["/a"], "/a\x00b"],
                         ids=["int", "relative", "empty", "list", "nul"])
def test_merge_malformed_is_sentinel(home, bad):
    assert ps.merge_snapshots([_note(str(home / "t")), _note(bad)])["root"] == S


def test_merge_sentinel_stays_sentinel(home):
    assert ps.merge_snapshots([_note(S), _note(str(home / "t"))])["root"] == S


def test_merge_three_nested_takes_the_innermost(home):
    rs = [str(home / "a"), str(home / "a" / "b" / "c"), str(home / "a" / "b")]
    assert ps.merge_snapshots([_note(r) for r in rs])["root"] == rs[1]


def test_merge_nested_through_a_symlink(home):
    real = home / "real" / "t"
    (real / "sub").mkdir(parents=True)
    os.symlink(home / "real", home / "link")
    got = ps.merge_snapshots([_note(str(home / "link" / "t")), _note(str(real / "sub"))])["root"]
    assert got != S


# ---- carried / unattributed / narrowed / safety_rules --------------------------------------

def test_carried_snapshot_keeps_root(snap_for, team):
    assert ps.carried_snapshot(snap_for(BOB, GROUP))["root"] == team


def test_carried_snapshot_malformed_root_is_sentinel(snap_for):
    assert ps.carried_snapshot({**snap_for(BOB, GROUP), "root": 7})["root"] == S


def test_carried_owner_snapshot_has_no_root(snap_for):
    assert "root" not in ps.carried_snapshot(snap_for(OWNER, GROUP))


def test_unattributed_prompt_keeps_root(snap_for, team):
    current = {**snap_for(BOB, GROUP), "turn_origin": "telegram"}
    out = ps.snapshot_for_unattributed_prompt(current, [], "a follow-up")
    final = out if out is not None else ps.merge_snapshots([])
    assert final.get("root") == team


def test_narrowing_keeps_root(snap_for, team):
    assert ps.narrowed_snapshot(snap_for(BOB, GROUP), [ps.floor_snapshot()])["root"] == team


def test_narrowing_an_owner_turn_by_a_member_gains_the_root(snap_for, team):
    assert ps.narrowed_snapshot(snap_for(OWNER, GROUP), [snap_for(BOB, GROUP)])["root"] == team


def test_narrowing_to_a_disjoint_root_is_sentinel(snap_for):
    assert ps.narrowed_snapshot(snap_for(BOB, GROUP), [snap_for(HAL, HAL)])["root"] == S


def test_safety_rules_carries_root(snap_for, team):
    assert ps.safety_rules(snap_for(BOB, GROUP))["root"] == team


def test_safety_rules_has_no_root_for_owner(snap_for):
    assert "root" not in ps.safety_rules(snap_for(OWNER, GROUP))


def test_rules_of_carries_root(snap_for, team):
    assert peer_auth.rules_of(snap_for(BOB, GROUP))["root"] == team


@pytest.mark.parametrize("bad", [5, {}, ["/x"]], ids=["int", "dict", "list"])
def test_well_formed_rules_refuses_a_non_string_root(snap_for, bad):
    rules = {**peer_auth.rules_of(snap_for(BOB, GROUP)), "root": bad}
    assert peer_auth.well_formed_rules(rules) is False


def test_well_formed_rules_accepts_a_string_root(snap_for):
    assert peer_auth.well_formed_rules(peer_auth.rules_of(snap_for(BOB, GROUP))) is True


# ---- the hook end of it: _decide reads the merged root -------------------------------------

def test_hook_denies_home_read_outside_root(hook, use, team_root, home):
    (team_root / "p").mkdir(parents=True)
    use(BOB, GROUP)
    assert hook("Read", {"file_path": str(home / "x.txt")}, team_root / "p") is not None


# ---- pins (background agents) -----------------------------------------------------------

def test_background_agent_pinned_to_root(hook, snap_for, team_root, home, pol):
    """An agent launched by a member's turn keeps the root even when a
    later owner turn runs."""
    (team_root / "p").mkdir(parents=True)
    ps.write_pin(SESSION, "agent1", {"rules": ps.safety_rules(snap_for(BOB, GROUP))})
    ps.write_merged_snapshot(SESSION, {**snap_for(OWNER, GROUP), "turn_origin": "telegram"})
    assert hook("Read", {"file_path": str(home / "x.txt")}, team_root / "p",
                agent_id="agent1") is not None


def test_owner_turn_itself_is_not_held_by_a_members_pin(hook, snap_for, team_root, home):
    (team_root / "p").mkdir(parents=True)
    ps.write_pin(SESSION, "agent1", {"rules": ps.safety_rules(snap_for(BOB, GROUP))})
    ps.write_merged_snapshot(SESSION, {**snap_for(OWNER, GROUP), "turn_origin": "telegram"})
    assert hook("Read", {"file_path": str(home / "x.txt")}, team_root / "p") is None


def test_a_pin_with_a_bad_root_fails_closed(hook, snap_for, team_root, home):
    (team_root / "p").mkdir(parents=True)
    rules = {**peer_auth.UNRESTRICTED_RULES, "root": 12}
    ps.write_pin(SESSION, "agent1", {"rules": rules})
    ps.write_merged_snapshot(SESSION, {**snap_for(OWNER, GROUP), "turn_origin": "telegram"})
    assert hook("Bash", {"command": "ls"}, team_root / "p", agent_id="agent1") is not None


# ---- triggers (scheduled prompts) and loop rights -------------------------------------------

def test_scheduled_prompt_keeps_root(snap_for, team):
    ps.write_trigger(SESSION, "check the build", peer_auth.rules_of(snap_for(BOB, GROUP)),
                     authors=(BOB,))
    assert ps.read_trigger(SESSION, "check the build")["root"] == team


def test_a_trigger_narrowed_by_a_root_change_takes_the_narrower(snap_for, team_root):
    ps.write_trigger(SESSION, "check", peer_auth.rules_of(snap_for(BOB, GROUP)), authors=(BOB,))
    narrower = {**peer_auth.rules_of(snap_for(BOB, GROUP)),
                "root": os.path.realpath(str(team_root / "sub"))}
    ps.narrow_triggers(SESSION, lambda uid: [narrower] if uid == BOB else None)
    assert ps.read_trigger(SESSION, "check")["root"] == os.path.realpath(str(team_root / "sub"))


def test_loop_rights_keep_root(snap_for, team):
    snap = {**snap_for(BOB, GROUP), "loop_rules": peer_auth.rules_of(snap_for(BOB, GROUP))}
    assert ps.loop_rights(snap)["root"] == team


# ---- criterion 10: a peer message from a member's session --------------------------------

RECEIVER = "claude-recv"
SENDER = "claude-send"
PID, START = 4150118, 264312572
BODY = "please read the operator's notes"


def _envelope(body=BODY):
    return ("Another Claude session sent a message:\n"
            f'<cross-session-message from="uds:/run/user/1001/cc-socks/{PID}.sock" '
            f'from-name="send" from-mode="bypass">\n{body}\n</cross-session-message>\n\n'
            "This came from another Claude session - not typed by your user.")


def _peer_turn(tmp_path, owner_snap):
    ps.write_merged_snapshot(RECEIVER, owner_snap)
    notify_hook._match_and_promote(RECEIVER, _envelope())
    origin = {"kind": "peer", "from": f"uds:/run/user/1001/cc-socks/{PID}.sock",
              "name": "send", "fromMode": "bypass", "verifiedPeerPid": PID,
              "verifiedPeerProcStart": str(START), "body": BODY}
    entries = [
        {"type": "user", "message": {"role": "user", "content": "[via Telegram · @aly]\nearlier"}},
        {"type": "user", "turnOrigin": "peer", "promptSource": "system", "origin": origin,
         "isMeta": True, "message": {"role": "user", "content": _envelope()}},
    ]
    t = tmp_path / "peer.jsonl"
    t.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return str(t)


def _put(rules):
    return peer_auth.write_receipt(
        sender_session=SENDER, chain=[{"pid": PID, "start": START}], message=BODY,
        rules=rules, turn_origin="telegram",
        driver={"class": "member", "labels": ["@bob"]}, via=None, sender_chat=None)


def _peer_decide(t, tool, inp, cwd):
    return enforce.decide({"hook_event_name": "PreToolUse", "session": RECEIVER,
                           "tool_name": tool, "tool_input": inp, "transcript_path": t,
                           "cwd": str(cwd), "session_id": "s1"})


@pytest.fixture
def recv_cwd(home):
    p = home / "recv"
    p.mkdir()
    return p


def test_receipt_carries_root(snap_for, team):
    assert _put(peer_auth.rules_of(snap_for(BOB, GROUP))) is True and \
        peer_auth.rules_of(snap_for(BOB, GROUP))["root"] == team


def test_peer_message_held_to_sender_root(tmp_path, snap_for, home, recv_cwd):
    t = _peer_turn(tmp_path, {**snap_for(OWNER, GROUP), "turn_origin": "telegram"})
    _put(peer_auth.rules_of(snap_for(BOB, GROUP)))
    assert _peer_decide(t, "Read", {"file_path": str(home / "secret.txt")}, recv_cwd) is not None


def test_peer_message_from_owner_reads_home(tmp_path, snap_for, home, recv_cwd):
    t = _peer_turn(tmp_path, {**snap_for(OWNER, GROUP), "turn_origin": "telegram"})
    _put(peer_auth.rules_of(snap_for(OWNER, GROUP)))
    assert _peer_decide(t, "Read", {"file_path": str(home / "secret.txt")}, recv_cwd) is None


def test_peer_message_records_the_root_on_the_receiver(tmp_path, snap_for, team, recv_cwd, home):
    t = _peer_turn(tmp_path, {**snap_for(OWNER, GROUP), "turn_origin": "telegram"})
    _put(peer_auth.rules_of(snap_for(BOB, GROUP)))
    _peer_decide(t, "Read", {"file_path": str(home / "secret.txt")}, recv_cwd)
    assert ps.read_snapshot(RECEIVER).get("root") == team


def test_receipt_bad_root_rejected(tmp_path, snap_for, recv_cwd):
    """A receipt whose root is not a string is not honoured: even owner
    rules in it fall to the peer floor (Bash denied)."""
    t = _peer_turn(tmp_path, {**snap_for(OWNER, GROUP), "turn_origin": "telegram"})
    _put({**peer_auth.UNRESTRICTED_RULES, "root": 99})
    assert _peer_decide(t, "Bash", {"command": "ls"}, recv_cwd) is not None


# ---- criterion 12: live reload -----------------------------------------------------------

def _with_root(scopes, chat, root):
    return [replace(s, root=str(root)) if s.chat_id == chat else s for s in scopes]


def test_sender_rules_carry_root(scopes, pol, team):
    notes = live_reload.sender_rules(scopes, pol, GROUP, BOB)
    assert ps.merge_snapshots(notes)["root"] == team


def test_root_change_narrows_running_turn(scopes, pol, team_root):
    new = _with_root(scopes, GROUP, team_root / "sub")
    assert live_reload.rules_narrowed(scopes, pol, new, pol, GROUP, BOB) is True


def test_root_widening_does_not_narrow(scopes, pol, team_root):
    old = _with_root(scopes, GROUP, team_root / "sub")
    assert live_reload.rules_narrowed(old, pol, scopes, pol, GROUP, BOB) is False


def test_root_moved_elsewhere_narrows(scopes, pol, home):
    new = _with_root(scopes, GROUP, home / "elsewhere")
    assert live_reload.rules_narrowed(scopes, pol, new, pol, GROUP, BOB) is True


@pytest.mark.parametrize("who", [OWNER, ADMIN], ids=["owner", "admin"])
def test_root_change_never_narrows_managers(scopes, pol, team_root, who):
    new = _with_root(scopes, GROUP, team_root / "sub")
    assert live_reload.rules_narrowed(scopes, pol, new, pol, GROUP, who) is False


def test_unchanged_root_does_not_narrow(scopes, pol):
    assert live_reload.rules_narrowed(scopes, pol, list(scopes), pol, GROUP, BOB) is False


def test_another_scopes_root_change_does_not_widen(scopes, pol, home):
    """A change elsewhere may add a deny (stricter) but never widens."""
    new = _with_root(scopes, HAL, home / "hal2")
    old_m = ps.merge_snapshots(live_reload.sender_rules(scopes, pol, GROUP, BOB))
    new_m = ps.merge_snapshots(live_reload.sender_rules(new, pol, GROUP, BOB))
    assert new_m["root"] == old_m["root"]
