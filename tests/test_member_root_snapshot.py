"""Members' root folders in the policy snapshot: resolved for a role
without ``can_manage`` only, merged strictest-wins (a narrower root wins,
roots that do not nest give ``safety.ROOT_SENTINEL``, absent imposes
nothing, a malformed one fails closed), and carried by every path that
copies a snapshot's rules (carry, narrow, ``safety_rules``, receipts,
live reload)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aipager import member_root, peer_auth, policy, safety
from aipager import policy_snapshot as ps
from aipager.bot import live_reload
from aipager.scope import Member, Scope

S = safety.ROOT_SENTINEL
GROUP, BOB = -1001, 3


def _pol():
    return policy.load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))


def _role(name):
    return _pol().get_role(name)


def _bound(root="/srv/team", deny=()):
    return member_root.RootBound(root, tuple(deny))


def _note(root=None, **kw):
    n = ps.resolve_snapshot(_role("user"), None, None)
    n.pop("root", None)
    if root is not None:
        n["root"] = root
    n.update(kw)
    return n


# ---- merge_roots -------------------------------------------------------------

@pytest.mark.parametrize("values,want", [
    ([], None), ([None, None], None),
    (["/srv/team"], "/srv/team"),
    ([None, "/srv/team"], "/srv/team"),                      # absent imposes nothing
    (["/srv/team", "/srv/team/a"], "/srv/team/a"),            # the narrower wins
    (["/srv/team/a", "/srv/team"], "/srv/team/a"),
    (["/srv/team", "/srv/other"], S),                        # disjoint
    (["/srv/team", "/srv/teamx"], S),                        # not by prefix
    (["/srv/team", S], S),
    (["/srv/team", "relative"], S), (["/srv/team", 7], S),   # malformed
    (["/srv/team/", "/srv/team"], "/srv/team"),
], ids=str)
def test_merge_roots(values, want):
    assert safety.merge_roots(values) == want


def test_merge_roots_follows_links(tmp_path):
    (tmp_path / "real" / "sub").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert safety.merge_roots([str(tmp_path / "link"), str(tmp_path / "real" / "sub")]) \
        == str(tmp_path / "real" / "sub")


# ---- resolve_snapshot -----------------------------------------------------------

def test_resolve_gives_root_only_without_can_manage():
    for name in ("user", "read_only"):
        snap = ps.resolve_snapshot(_role(name), None, None,
                                   root_bound=_bound(deny=("/srv/other",)))
        assert snap["root"] == "/srv/team"
        assert "/srv/other/**" in snap["deny_paths_no_access"]
    for name in ("owner", "admin"):
        snap = ps.resolve_snapshot(_role(name), None, None,
                                   root_bound=_bound(deny=("/srv/other",)))
        assert "root" not in snap
        assert "/srv/other/**" not in snap["deny_paths_no_access"]
    assert "root" not in ps.resolve_snapshot(None, None, None, root_bound=_bound())
    assert "root" not in ps.resolve_snapshot(_role("user"), None, None)


def test_a_custom_bypass_role_without_manage_still_gets_the_root():
    role = policy.Role(name="lead", bypass_role_denies=True)
    snap = ps.resolve_snapshot(role, None, None, root_bound=_bound(deny=("/srv/x",)))
    assert snap["root"] == "/srv/team" and snap["confine_writes"] is False
    assert "/srv/x/**" in snap["deny_paths_no_access"]


def test_member_message_note_carries_the_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "notes_dir", lambda n: tmp_path / "notes")
    path = ps.write_note("claude-x", _role("user"), None, None, msg_id=1, chat_id=GROUP,
                         sender_key=(GROUP, BOB), body="b", raw_text="b",
                         root_bound=_bound())
    import json
    assert json.loads(Path(path).read_text())["root"] == "/srv/team"


# ---- merge / carry / narrow / rules ------------------------------------------------

def test_merge_narrower_wins():
    assert ps.merge_snapshots([_note("/srv/team"), _note("/srv/team/a")])["root"] \
        == "/srv/team/a"


def test_merge_disjoint_is_sentinel():
    assert ps.merge_snapshots([_note("/srv/team"), _note("/srv/other")])["root"] == S


def test_merge_absent_keeps_root():
    owner = ps.resolve_snapshot(_role("owner"), None, None)
    merged = ps.merge_snapshots([owner, _note("/srv/team")])
    assert merged["root"] == "/srv/team"
    assert "root" not in ps.merge_snapshots([owner, _note()])


def test_merge_malformed_is_sentinel():
    assert ps.merge_snapshots([_note("/srv/team"), _note(["x"])])["root"] == S


def test_unattributed_prompt_keeps_root():
    current = {**_note("/srv/team"), "note_bodies": []}
    kept = ps.snapshot_for_unattributed_prompt(current, [], "a terminal line")
    assert kept["root"] == "/srv/team"
    assert ps.carried_snapshot(current)["root"] == "/srv/team"
    assert "root" not in ps.carried_snapshot(_note())


def test_narrowing_keeps_root():
    current = _note("/srv/team")
    assert ps.narrowed_snapshot(current, [ps.floor_snapshot()])["root"] == "/srv/team"
    assert ps.narrowed_snapshot(current, [_note("/srv/other")])["root"] == S


def test_safety_rules_carries_root():
    assert ps.safety_rules(_note("/srv/team/../team"))["root"] == "/srv/team"
    assert "root" not in ps.safety_rules(_note())
    assert ps.safety_rules({"root": 3})["root"] == S
    assert "root" in ps.SAFETY_FIELDS


def test_floors_have_no_root():
    assert "root" not in ps.floor_snapshot()
    assert "root" not in ps.peer_floor_snapshot()
    assert "root" not in peer_auth.UNRESTRICTED_RULES


def test_loop_rights_keep_root():
    snap = {"loop_rules": ps.safety_rules(_note("/srv/team")),
            "loop_joins": [ps.safety_rules(_note("/srv/team/a"))]}
    assert ps.loop_rights(snap)["root"] == "/srv/team/a"


# ---- receipts --------------------------------------------------------------------

def test_receipt_carries_root():
    assert peer_auth.rules_of(_note("/srv/team"))["root"] == "/srv/team"
    assert "root" not in peer_auth.rules_of(_note())
    assert peer_auth.rules_of({"root": None, "bypass_safety": False}).get("root") is None


def test_receipt_bad_root_rejected():
    good = peer_auth.rules_of(_note("/srv/team"))
    assert peer_auth.well_formed_rules(good)
    assert peer_auth.well_formed_rules(peer_auth.rules_of(_note()))
    for bad in (None, 3, ["/srv"], {"p": 1}):
        assert not peer_auth.well_formed_rules({**good, "root": bad})


# ---- live reload ------------------------------------------------------------------

def _scopes(root):
    return [Scope(chat_id=GROUP, kind="group", label="team", root=root, members=(
        Member(id=1, label="alice", role="owner"),
        Member(id=BOB, label="bob", role="user")))]


def test_sender_rules_carry_the_root():
    rules = live_reload.sender_rules(_scopes("/srv/team"), _pol(), GROUP, BOB)
    assert rules[0]["root"] == "/srv/team"
    owner = live_reload.sender_rules(_scopes("/srv/team"), _pol(), GROUP, 1)
    assert "root" not in owner[0]


def test_root_change_narrows_running_turn():
    pol = _pol()
    assert live_reload.rules_narrowed(_scopes("/srv/team"), pol, _scopes("/srv/other"),
                                      pol, GROUP, BOB)
    assert live_reload.rules_narrowed(_scopes("/srv/team"), pol, _scopes("/srv/team/a"),
                                      pol, GROUP, BOB)


def test_root_widening_does_not_narrow():
    pol = _pol()
    assert not live_reload.rules_narrowed(_scopes("/srv/team/a"), pol,
                                          _scopes("/srv/team"), pol, GROUP, BOB)
    assert not live_reload.rules_narrowed(_scopes("/srv/team"), pol,
                                          _scopes("/srv/team"), pol, GROUP, BOB)
